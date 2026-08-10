// tail_path_bench.cpp — per-round cycle harness for the P90−P50 tail-width question.
//
// WHY NOT `tests/latency_bench.cpp`: that tool reports wall nanoseconds. On the contest
// build machine (measured: 119 users, load average ~62) the run-to-run scatter of a wall
// P90 is ±60 ns, which is three times the effect this line is chasing, so a nanosecond
// figure here would be unquotable. Per-thread `perf` counters read through `rdpmc` are
// resolvable instead, for two reasons:
//   1. the counter is per-thread, so cycles accumulated while some other tenant owns the
//      core are simply not counted; and
//   2. any constant instrument overhead cancels exactly in P90 − P50, which is a
//      *difference* of two quantiles of the same distribution.
//
// WHAT IT MEASURES: one value per round, taken as the minimum across reps (the protocol
// `tests/bench.cpp` uses, and the one the escapeStep judgement used). Min-over-reps
// removes interference and leaves the *data-dependent* round-to-round shape, which is
// exactly the quantity "tail width" names.
//
// EVICTION: `--evict code` walks the 68 KB victim set in tail_icache_thrash.cpp before
// each timed call, reproducing the only condition under which the escapeStep knife was
// visible at all. `--evict data` is the 16 MiB write-through of the older bench;
// `--evict both` is both. The victim footprint in bytes is printed with the results, so
// the eviction condition travels with the number.
//
// PATH ATTRIBUTION: `--masks FILE` reads a per-round path label file produced by
// `--mask-only` against an instrumented build, and joins it to the timing rounds. The
// instrumented build is only ever used to produce labels, never to produce timings.
//
// Build (contest machine):
//   g++ -std=c++17 -O2 -o tail_path_bench tests/tail_path_bench.cpp \
//       tests/tail_icache_thrash.cpp -ldl
// Run:
//   ./tail_path_bench --counter cycles --evict code --reps 9 --runs 5 --cpu 3 \
//       --masks masks_175847.txt --csv out.csv logs/game_175847.bin base.so cand.so
//   ./tail_path_bench --mask-only --csv masks_175847.txt logs/game_175847.bin trace.so
#include <asm/unistd.h>
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <sched.h>
#include <string>
#include <unistd.h>
#include <vector>

extern "C" int icacheThrash(int x);
extern "C" size_t icacheThrashBytes();
extern "C" size_t icacheThrashCount();

namespace {

struct GameOutput { int actions[6]; int k; int order; int vp; };
using MoveFn = GameOutput (*)(const void*);
using TraceFn = unsigned (*)();

constexpr int ROUNDS = 500;
constexpr size_t DATA_THRASH_BYTES = 16ULL * 1024 * 1024;

volatile long long g_sink = 0;
std::vector<unsigned char>* g_junk = nullptr;

// ------------------------------------------------------------------ timers/counters
inline uint64_t rdtscSer() {
    uint32_t lo, hi, aux;
    asm volatile("rdtscp" : "=a"(lo), "=d"(hi), "=c"(aux));
    asm volatile("lfence" ::: "memory");
    return (static_cast<uint64_t>(hi) << 32) | lo;
}

inline uint64_t rdpmcRaw(uint32_t idx) {
    uint32_t lo, hi;
    asm volatile("rdpmc" : "=a"(lo), "=d"(hi) : "c"(idx));
    return (static_cast<uint64_t>(hi) << 32) | lo;
}

struct PerfCounter {
    int fd = -1;
    perf_event_mmap_page* page = nullptr;
    uint32_t idx = 0;

    bool open(uint64_t config) {
        perf_event_attr attr{};
        attr.type = PERF_TYPE_HARDWARE;
        attr.size = sizeof(attr);
        attr.config = config;
        attr.exclude_kernel = 1;
        attr.exclude_hv = 1;
        fd = static_cast<int>(syscall(__NR_perf_event_open, &attr, 0, -1, -1, 0));
        if (fd < 0) {
            std::fprintf(stderr, "perf_event_open: %s (need perf_event_paranoid <= 2)\n",
                         std::strerror(errno));
            return false;
        }
        page = static_cast<perf_event_mmap_page*>(
            mmap(nullptr, static_cast<size_t>(sysconf(_SC_PAGESIZE)), PROT_READ,
                 MAP_SHARED, fd, 0));
        if (page == MAP_FAILED) {
            std::fprintf(stderr, "mmap perf page: %s\n", std::strerror(errno));
            page = nullptr;
            return false;
        }
        // Warm the counter so the kernel has scheduled it onto a hardware slot.
        for (int i = 0; i < 1000; ++i) g_sink += i;
        idx = page->index;
        if (idx == 0) {
            std::fprintf(stderr,
                         "perf mmap index==0: rdpmc is not available to user space here.\n"
                         "  (/sys/devices/cpu/rdpmc must be 1 or 2). Use --counter tsc.\n");
            return false;
        }
        return true;
    }
};

// ------------------------------------------------------------------------- helpers
std::vector<unsigned char> readFile(const char* path) {
    FILE* f = std::fopen(path, "rb");
    if (!f) { std::fprintf(stderr, "open %s: %s\n", path, std::strerror(errno)); std::exit(2); }
    std::fseek(f, 0, SEEK_END);
    const long size = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (size <= 0) { std::fprintf(stderr, "%s is empty\n", path); std::exit(2); }
    std::vector<unsigned char> bytes(static_cast<size_t>(size));
    if (std::fread(bytes.data(), 1, bytes.size(), f) != bytes.size()) {
        std::fprintf(stderr, "short read %s\n", path); std::exit(2);
    }
    std::fclose(f);
    return bytes;
}

void pinCpu(int cpu) {
    if (cpu < 0) return;
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0)
        std::fprintf(stderr, "warn: sched_setaffinity(%d): %s\n", cpu, std::strerror(errno));
}

void thrashData() {
    volatile unsigned char* p = g_junk->data();
    unsigned char carry = static_cast<unsigned char>(g_sink);
    for (size_t i = 0; i < g_junk->size(); i += 64) {
        carry = static_cast<unsigned char>(carry + p[i]);
        p[i] = static_cast<unsigned char>(carry + i);
    }
    g_sink += carry;
}

uint64_t quantile(std::vector<uint64_t> v, int num, int den) {
    if (v.empty()) return 0;
    std::sort(v.begin(), v.end());
    size_t i = (v.size() * static_cast<size_t>(num)) / static_cast<size_t>(den);
    if (i >= v.size()) i = v.size() - 1;
    return v[i];
}

void usage(const char* a0) {
    std::fprintf(stderr,
        "usage: %s [--counter cycles|instructions|tsc] [--evict none|code|data|both]\n"
        "          [--reps N] [--runs M] [--cpu N] [--warm N] [--csv FILE]\n"
        "          inputs.bin so [so2 ...]\n"
        "       %s --mask-only --csv FILE inputs.bin trace.so\n", a0, a0);
    std::exit(2);
}

}  // namespace

int main(int argc, char** argv) {
    std::string counter = "cycles";
    std::string evict = "code";
    int reps = 9, runs = 1, cpu = -1, warm = 200000;
    bool maskOnly = false;
    const char* csvPath = nullptr;
    int arg = 1;
    while (arg < argc && std::strncmp(argv[arg], "--", 2) == 0) {
        const std::string opt = argv[arg++];
        if (opt == "--mask-only") { maskOnly = true; continue; }
        if (arg >= argc) usage(argv[0]);
        if      (opt == "--counter") counter = argv[arg++];
        else if (opt == "--evict")   evict   = argv[arg++];
        else if (opt == "--reps")    reps    = std::atoi(argv[arg++]);
        else if (opt == "--runs")    runs    = std::atoi(argv[arg++]);
        else if (opt == "--cpu")     cpu     = std::atoi(argv[arg++]);
        else if (opt == "--warm")    warm    = std::atoi(argv[arg++]);
        else if (opt == "--csv")     csvPath = argv[arg++];
        else usage(argv[0]);
    }
    if (argc - arg < 2 || reps < 1 || runs < 1) usage(argv[0]);
    const char* binPath = argv[arg];
    const int nso = argc - arg - 1;
    char** soPaths = argv + arg + 1;

    pinCpu(cpu);
    const auto inputBytes = readFile(binPath);
    if (inputBytes.size() % ROUNDS != 0) {
        std::fprintf(stderr, "%s is not a multiple of %d rounds\n", binPath, ROUNDS);
        return 2;
    }
    const long isz = static_cast<long>(inputBytes.size()) / ROUNDS;

    std::vector<void*> handles(static_cast<size_t>(nso));
    std::vector<MoveFn> moves(static_cast<size_t>(nso));
    std::vector<TraceFn> traces(static_cast<size_t>(nso), nullptr);
    for (int s = 0; s < nso; ++s) {
        handles[static_cast<size_t>(s)] = dlopen(soPaths[s], RTLD_NOW | RTLD_LOCAL);
        if (!handles[static_cast<size_t>(s)]) {
            std::fprintf(stderr, "dlopen %s: %s\n", soPaths[s], dlerror()); return 2;
        }
        moves[static_cast<size_t>(s)] = reinterpret_cast<MoveFn>(
            dlsym(handles[static_cast<size_t>(s)], "moveDecision"));
        if (!moves[static_cast<size_t>(s)]) {
            std::fprintf(stderr, "no moveDecision in %s\n", soPaths[s]); return 2;
        }
        traces[static_cast<size_t>(s)] = reinterpret_cast<TraceFn>(
            dlsym(handles[static_cast<size_t>(s)], "tailTrace"));
    }

    // --------------------------------------------------------------- mask-only mode
    if (maskOnly) {
        if (nso != 1 || !traces[0]) {
            std::fprintf(stderr, "--mask-only needs exactly one .so exporting tailTrace()\n");
            return 2;
        }
        // Two independent passes; the labels must agree, otherwise the label stream is
        // not a deterministic function of the input stream and cannot be joined to
        // timings taken in a different pass.
        std::vector<unsigned> a(ROUNDS), b(ROUNDS);
        for (int pass = 0; pass < 2; ++pass) {
            std::vector<unsigned>& dst = pass ? b : a;
            for (int i = 0; i < ROUNDS; ++i) {
                GameOutput o = moves[0](inputBytes.data() + isz * i);
                g_sink += o.k;
                dst[static_cast<size_t>(i)] = traces[0]();
            }
        }
        int mismatch = 0;
        for (int i = 0; i < ROUNDS; ++i)
            mismatch += (a[static_cast<size_t>(i)] != b[static_cast<size_t>(i)]);
        std::fprintf(stderr, "mask_pass_mismatch=%d (must be 0)\n", mismatch);
        FILE* out = csvPath ? std::fopen(csvPath, "w") : stdout;
        if (!out) { std::perror("csv"); return 2; }
        std::fprintf(out, "round,mask\n");
        for (int i = 0; i < ROUNDS; ++i)
            std::fprintf(out, "%d,%u\n", i, a[static_cast<size_t>(i)]);
        if (csvPath) std::fclose(out);
        return mismatch == 0 ? 0 : 1;
    }

    // ------------------------------------------------------------------ timing mode
    const bool wantData = (evict == "data" || evict == "both");
    const bool wantCode = (evict == "code" || evict == "both");
    if (evict != "none" && !wantData && !wantCode) usage(argv[0]);
    if (wantData) g_junk = new std::vector<unsigned char>(DATA_THRASH_BYTES, 0u);

    PerfCounter pc;
    const bool usePerf = (counter == "cycles" || counter == "instructions");
    if (usePerf) {
        const uint64_t cfg = (counter == "cycles") ? PERF_COUNT_HW_CPU_CYCLES
                                                   : PERF_COUNT_HW_INSTRUCTIONS;
        if (!pc.open(cfg)) return 2;
    } else if (counter != "tsc") {
        usage(argv[0]);
    }

    // Warm-up: fill icache/BTB/branch history so rep 0 is not a special case, and so
    // every .so has been resident at least once before any of them is timed.
    for (int s = 0; s < nso; ++s)
        for (int i = 0; i < warm / std::max(1, nso); ++i) {
            GameOutput o = moves[static_cast<size_t>(s)](
                inputBytes.data() + isz * (i % ROUNDS));
            g_sink += o.k;
        }

    FILE* csv = csvPath ? std::fopen(csvPath, "w") : nullptr;
    if (csvPath && !csv) { std::perror("csv"); return 2; }
    if (csv) std::fprintf(csv, "run,so_index,so,round,value\n");

    std::fprintf(stderr,
        "bin=%s rounds=%d counter=%s evict=%s reps=%d runs=%d cpu=%d "
        "thrash_fns=%zu thrash_bytes=%zu\n",
        binPath, ROUNDS, counter.c_str(), evict.c_str(), reps, runs, cpu,
        icacheThrashCount(), icacheThrashBytes());

    int idxAnomalies = 0;
    for (int run = 0; run < runs; ++run) {
        std::vector<std::vector<uint64_t>> mins(
            static_cast<size_t>(nso), std::vector<uint64_t>(ROUNDS, ~0ull));
        for (int rep = 0; rep < reps; ++rep) {
            for (int s = 0; s < nso; ++s) {          // rep-level interleave
                for (int i = 0; i < ROUNDS; ++i) {
                    const void* in = inputBytes.data() + isz * i;
                    if (wantData) thrashData();
                    if (wantCode) g_sink += icacheThrash(static_cast<int>(g_sink) | 1);
                    uint64_t v0, v1;
                    asm volatile("" ::: "memory");
                    if (usePerf) {
                        v0 = rdpmcRaw(pc.idx - 1);
                        GameOutput o = moves[static_cast<size_t>(s)](in);
                        v1 = rdpmcRaw(pc.idx - 1);
                        g_sink += o.k + o.order;
                    } else {
                        v0 = rdtscSer();
                        GameOutput o = moves[static_cast<size_t>(s)](in);
                        v1 = rdtscSer();
                        g_sink += o.k + o.order;
                    }
                    asm volatile("" ::: "memory");
                    if (usePerf && pc.page->index != pc.idx) { ++idxAnomalies; continue; }
                    const uint64_t d = (v1 - v0) & 0xFFFFFFFFFFFFull;
                    uint64_t& m = mins[static_cast<size_t>(s)][static_cast<size_t>(i)];
                    if (d < m) m = d;
                }
            }
        }
        for (int s = 0; s < nso; ++s) {
            std::vector<uint64_t> steady;            // r>=20 discards opening rounds
            for (int i = 20; i < ROUNDS; ++i)
                steady.push_back(mins[static_cast<size_t>(s)][static_cast<size_t>(i)]);
            const uint64_t p50 = quantile(steady, 50, 100);
            const uint64_t p90 = quantile(steady, 90, 100);
            std::printf("run=%d so=%-14s n=%zu P50=%6llu P90=%6llu P99=%6llu "
                        "WIDTH(P90-P50)=%6lld\n",
                        run, soPaths[s], steady.size(),
                        static_cast<unsigned long long>(p50),
                        static_cast<unsigned long long>(p90),
                        static_cast<unsigned long long>(quantile(steady, 99, 100)),
                        static_cast<long long>(p90) - static_cast<long long>(p50));
            std::fflush(stdout);
            if (csv)
                for (int i = 0; i < ROUNDS; ++i)
                    std::fprintf(csv, "%d,%d,%s,%d,%llu\n", run, s, soPaths[s], i,
                        static_cast<unsigned long long>(
                            mins[static_cast<size_t>(s)][static_cast<size_t>(i)]));
        }
    }
    if (csv) std::fclose(csv);
    std::fprintf(stderr, "perf_index_anomalies=%d sink=%lld\n", idxAnomalies,
                 static_cast<long long>(g_sink));
    for (int s = 0; s < nso; ++s) dlclose(handles[static_cast<size_t>(s)]);
    delete g_junk;
    return 0;
}
