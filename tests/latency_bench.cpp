// Direct CLOCK_MONOTONIC_RAW benchmark for the GoldRush moveDecision ABI.
// Build from repository root on the target x86 host:
//   g++ -O2 -std=c++17 -Wall -Wextra -o latency_bench tests/latency_bench.cpp -ldl
// Run:
//   ./latency_bench --mode hot --reps 9 --cpu 0 input.bin player.so samples.csv
// Modes: hot = no eviction; cold = the existing bench's 16 MiB data eviction;
// cold2 = data plus 600 generated cache-line functions to perturb I-cache/BTB.
#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dlfcn.h>
#include <fcntl.h>
#include <sched.h>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

#include "../src/game_api.h"

namespace {

constexpr size_t DATA_THRASH_BYTES = 16ULL * 1024 * 1024;
constexpr size_t CODE_SLOTS = 600;
constexpr size_t CODE_STRIDE = 64;
constexpr size_t CODE_THRASH_BYTES = CODE_SLOTS * CODE_STRIDE;
volatile uint64_t g_sink = 0;

long long rawNs() {
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) {
        std::fprintf(stderr, "clock_gettime: %s\n", std::strerror(errno));
        std::exit(2);
    }
    return static_cast<long long>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

void pinCpu(int cpu) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        std::fprintf(stderr, "sched_setaffinity(cpu=%d): %s\n", cpu, std::strerror(errno));
        std::exit(2);
    }
    std::fprintf(stderr, "pinned_cpu=%d\n", cpu);
}

std::vector<unsigned char> readFile(const char* path) {
    FILE* file = std::fopen(path, "rb");
    if (!file) {
        std::fprintf(stderr, "open %s: %s\n", path, std::strerror(errno));
        std::exit(2);
    }
    if (std::fseek(file, 0, SEEK_END) != 0) std::exit(2);
    const long size = std::ftell(file);
    if (size < 0 || std::fseek(file, 0, SEEK_SET) != 0) std::exit(2);
    std::vector<unsigned char> bytes(static_cast<size_t>(size));
    if (std::fread(bytes.data(), 1, bytes.size(), file) != bytes.size()) {
        std::fprintf(stderr, "read %s failed\n", path);
        std::exit(2);
    }
    std::fclose(file);
    return bytes;
}

class Evictor {
  public:
    explicit Evictor(bool withCode) : data_(DATA_THRASH_BYTES), withCode_(withCode) {
        for (size_t i = 0; i < data_.size(); i += 64) data_[i] = static_cast<unsigned char>(i);
        if (!withCode_) return;
        code_ = static_cast<unsigned char*>(mmap(nullptr, CODE_THRASH_BYTES,
                                                PROT_READ | PROT_WRITE,
                                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
        if (code_ == MAP_FAILED) {
            std::fprintf(stderr, "mmap code: %s\n", std::strerror(errno));
            std::exit(2);
        }
        // Each cache-line-sized x86-64 function computes return = argument + 1.
        for (size_t offset = 0; offset < CODE_THRASH_BYTES; offset += CODE_STRIDE) {
            std::memset(code_ + offset, 0x90, CODE_STRIDE);
            code_[offset + 0] = 0x8d;  // lea 1(%rdi), %eax
            code_[offset + 1] = 0x47;
            code_[offset + 2] = 0x01;
            code_[offset + 3] = 0xc3;  // ret
        }
        if (mprotect(code_, CODE_THRASH_BYTES, PROT_READ | PROT_EXEC) != 0) {
            std::fprintf(stderr, "mprotect code: %s\n", std::strerror(errno));
            std::exit(2);
        }
    }

    ~Evictor() {
        if (code_ && code_ != MAP_FAILED) munmap(code_, CODE_THRASH_BYTES);
    }

    void thrash() {
        // Volatile accesses keep the complete cache-line walk observable.
        volatile unsigned char* p = data_.data();
        unsigned char carry = static_cast<unsigned char>(g_sink);
        for (size_t i = 0; i < data_.size(); i += 64) {
            carry = static_cast<unsigned char>(carry + p[i]);
            p[i] = static_cast<unsigned char>(carry + i);
        }
        g_sink += carry;
        if (!withCode_) return;
        using TinyFn = int (*)(int);
        int value = static_cast<int>(g_sink);
        // A coprime stride permutes all 600 targets while touching each once.
        for (size_t i = 0, slot = 0; i < CODE_SLOTS; ++i, slot = (slot + 599) % CODE_SLOTS) {
            auto fn = reinterpret_cast<TinyFn>(code_ + slot * CODE_STRIDE);
            value = fn(value);
        }
        g_sink += static_cast<unsigned>(value);
    }

  private:
    std::vector<unsigned char> data_;
    bool withCode_ = false;
    unsigned char* code_ = nullptr;
};

bool legal(const GameOutput& out) {
    if (out.k < 0 || out.k > S || out.order < 0 || out.order > 1 ||
        out.vp < 0 || out.vp > 2) return false;
    for (int action : out.actions)
        if (action < 0 || action > 4) return false;
    return true;
}

uint64_t hashOutput(uint64_t hash, const GameOutput& out) {
    const auto* bytes = reinterpret_cast<const unsigned char*>(&out);
    for (size_t i = 0; i < sizeof(out); ++i) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

long long quantile(std::vector<long long> values, int numerator, int denominator) {
    std::sort(values.begin(), values.end());
    const size_t index = std::min(values.size() - 1,
                                  values.size() * numerator / denominator);
    return values[index];
}

void usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s [--mode hot|cold|cold2] [--reps N] [--cpu N] "
                 "input.bin player.so samples.csv\n", argv0);
    std::exit(2);
}

}  // namespace

int main(int argc, char** argv) {
    std::string mode = "hot";
    int reps = 9;
    int cpu = 0;
    int arg = 1;
    while (arg < argc && std::strncmp(argv[arg], "--", 2) == 0) {
        const std::string option = argv[arg++];
        if (arg >= argc) usage(argv[0]);
        if (option == "--mode") mode = argv[arg++];
        else if (option == "--reps") reps = std::atoi(argv[arg++]);
        else if (option == "--cpu") cpu = std::atoi(argv[arg++]);
        else usage(argv[0]);
    }
    if (argc - arg != 3 || reps < 1 ||
        (mode != "hot" && mode != "cold" && mode != "cold2")) usage(argv[0]);
    const char* inputPath = argv[arg];
    const char* soPath = argv[arg + 1];
    const char* csvPath = argv[arg + 2];

    static_assert(sizeof(GameInput) == 1444, "unexpected GameInput ABI size");
    static_assert(sizeof(GameOutput) == 36, "unexpected GameOutput ABI size");
    pinCpu(cpu);
    const auto inputBytes = readFile(inputPath);
    if (inputBytes.empty() || inputBytes.size() % sizeof(GameInput) != 0) {
        std::fprintf(stderr, "input size %zu is not a positive multiple of ABI size %zu\n",
                     inputBytes.size(), sizeof(GameInput));
        return 2;
    }
    const size_t rounds = inputBytes.size() / sizeof(GameInput);
    const auto* inputs = reinterpret_cast<const GameInput*>(inputBytes.data());
    for (size_t i = 0; i < rounds; ++i) {
        if (inputs[i].round != static_cast<int>(i)) {
            std::fprintf(stderr, "non-contiguous round at index %zu: %d\n", i, inputs[i].round);
            return 2;
        }
    }

    void* handle = dlopen(soPath, RTLD_NOW | RTLD_LOCAL);
    if (!handle) {
        std::fprintf(stderr, "dlopen %s: %s\n", soPath, dlerror());
        return 2;
    }
    using MoveFn = GameOutput (*)(const GameInput*);
    auto move = reinterpret_cast<MoveFn>(dlsym(handle, "moveDecision"));
    if (!move) {
        std::fprintf(stderr, "dlsym moveDecision: %s\n", dlerror());
        return 2;
    }

    FILE* csv = std::fopen(csvPath, "w");
    if (!csv) {
        std::fprintf(stderr, "open %s: %s\n", csvPath, std::strerror(errno));
        return 2;
    }
    std::fprintf(csv, "mode,rep,round,elapsed_ns,cpu\n");

    // The timer-pair control has no function call between timestamps. It is
    // reported, not silently subtracted from moveDecision elapsed time.
    std::vector<long long> timerControl;
    timerControl.reserve(10000);
    for (int i = 0; i < 10000; ++i) {
        const auto t0 = rawNs();
        asm volatile("" ::: "memory");
        const auto t1 = rawNs();
        timerControl.push_back(t1 - t0);
    }

    Evictor evictor(mode == "cold2");
    std::vector<long long> all;
    std::vector<long long> steady;
    all.reserve(rounds * reps);
    steady.reserve(rounds * reps);
    uint64_t expectedHash = 0;
    int illegal = 0;
    int hashMismatches = 0;
    for (int rep = 0; rep < reps; ++rep) {
        uint64_t outputHash = 1469598103934665603ULL;
        for (size_t round = 0; round < rounds; ++round) {
            if (mode != "hot") evictor.thrash();
            asm volatile("" ::: "memory");
            const long long start = rawNs();
            const GameOutput out = move(&inputs[round]);
            const long long finish = rawNs();
            asm volatile("" ::: "memory");
            const long long elapsed = finish - start;
            illegal += !legal(out);
            outputHash = hashOutput(outputHash, out);
            g_sink += static_cast<unsigned>(out.actions[0] + out.k + out.order + out.vp);
            const int observedCpu = sched_getcpu();
            std::fprintf(csv, "%s,%d,%zu,%lld,%d\n", mode.c_str(), rep, round,
                         elapsed, observedCpu);
            all.push_back(elapsed);
            if (round >= 20) steady.push_back(elapsed);
        }
        if (rep == 0) expectedHash = outputHash;
        else hashMismatches += outputHash != expectedHash;
        std::fprintf(stderr, "rep=%d output_hash=%016llx\n", rep,
                     static_cast<unsigned long long>(outputHash));
    }
    std::fclose(csv);

    std::fprintf(stderr,
                 "mode=%s reps=%d rounds=%zu samples=%zu abi_input=%zu abi_output=%zu "
                 "illegal=%d hash_mismatches=%d sink=%llu\n",
                 mode.c_str(), reps, rounds, all.size(), sizeof(GameInput),
                 sizeof(GameOutput), illegal, hashMismatches,
                 static_cast<unsigned long long>(g_sink));
    std::fprintf(stderr,
                 "timer_control_ns min=%lld p50=%lld p90=%lld p99=%lld max=%lld\n",
                 *std::min_element(timerControl.begin(), timerControl.end()),
                 quantile(timerControl, 50, 100), quantile(timerControl, 90, 100),
                 quantile(timerControl, 99, 100),
                 *std::max_element(timerControl.begin(), timerControl.end()));
    std::fprintf(stderr,
                 "all_ns min=%lld p50=%lld p90=%lld p99=%lld max=%lld\n",
                 *std::min_element(all.begin(), all.end()), quantile(all, 50, 100),
                 quantile(all, 90, 100), quantile(all, 99, 100),
                 *std::max_element(all.begin(), all.end()));
    std::fprintf(stderr,
                 "steady_r20_ns min=%lld p50=%lld p90=%lld p99=%lld max=%lld\n",
                 *std::min_element(steady.begin(), steady.end()),
                 quantile(steady, 50, 100), quantile(steady, 90, 100),
                 quantile(steady, 99, 100), *std::max_element(steady.begin(), steady.end()));
    dlclose(handle);
    return (illegal == 0 && hashMismatches == 0) ? 0 : 1;
}
