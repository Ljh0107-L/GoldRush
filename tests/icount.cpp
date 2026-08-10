// icount.cpp — 用 perf_event_open 精确测 moveDecision 的动态指令数/调用。
//
// 为什么需要它：`0.1454ns/指令` 的成本模型只有在指令数可复现测量时才有意义。历史上
// 「659 条」锚点用了与 bench 实际调用数不符的分母，低估 36%，与系数高估 38% 恰好抵消，
// 连续两晚看似准确（军规 25）。本工具把分母写死成命令行参数并打印，杜绝该类错误。
//
// 用法：
//   icount <so> <inputs.bin> [calls_total] [reps] [instructions|cycles]
//   icount --static <so>          # 只打印 moveDecision 的静态指令数(objdump 用)
//
// 为什么要能数 cycles：指令数下降不等于变快。scan 占 43.6% 指令但 49.4% cycles，融合改写
// 可能减少指令却破坏访存并行度(ILP)，净变慢。平台 CLOCK 有 10ns 量化台阶(占被测对象
// 约 9-17%)，本地墙钟分辨不出 6ns 级差异；perf cycles 计数是精确整数，才是可判据。
// **经济意义上的验收量是 cycles，不是 instructions。**
//
// 口径：
//   * 计数器只包住调用循环，`PERF_COUNT_HW_INSTRUCTIONS` + exclude_kernel + exclude_hv。
//   * 每 rep 重新 RESET/ENABLE，报告逐 rep 的 instructions/call，供复现性核对。
//   * 打印的 `raw` 含 harness 循环自身开销。求净值须减 harness 开销：
//       harness = raw(trivial) − static_body(trivial)
//       net(target) = raw(target) − harness
//     trivial 的函数体无分支，故其静态指令数等于动态指令数，可作为标定物。
//   * 输入按 bin 顺序循环，故不同 .so 面对的输入序列逐位相同，差分才有意义。
//
// 编译（开发机）：
//   g++ -std=c++17 -O2 -o icount tests/icount.cpp -ldl
#include <asm/unistd.h>
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <vector>

#include "../src/game_api.h"

using Fn = GameOutput (*)(const GameInput*);

static long perf_open(bool cycles) {
    perf_event_attr attr{};
    attr.type = PERF_TYPE_HARDWARE;
    attr.size = sizeof(attr);
    attr.config = cycles ? PERF_COUNT_HW_CPU_CYCLES : PERF_COUNT_HW_INSTRUCTIONS;
    attr.disabled = 1;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    long fd = syscall(__NR_perf_event_open, &attr, 0, -1, -1, 0);
    if (fd < 0) {
        std::perror("perf_event_open");
        std::fprintf(stderr, "hint: need perf_event_paranoid <= 2\n");
        std::exit(2);
    }
    return fd;
}

// 累加到 volatile sink，防止调用被优化掉。
static volatile long long sink = 0;

int main(int argc, char** argv) {
    if (argc >= 3 && std::strcmp(argv[1], "--static") == 0) {
        std::printf("use: objdump -d --disassemble=moveDecision %s | grep -c '^ '\n", argv[2]);
        return 0;
    }
    if (argc < 3) {
        std::fprintf(stderr, "usage: %s <so> <inputs.bin> [calls_total] [reps] [instructions|cycles]\n",
                     argv[0]);
        return 1;
    }
    const char* so_path = argv[1];
    const char* bin_path = argv[2];
    const long long calls_total = argc > 3 ? std::atoll(argv[3]) : 500000;
    const int reps = argc > 4 ? std::atoi(argv[4]) : 3;
    const bool cycles = argc > 5 && std::strcmp(argv[5], "cycles") == 0;

    std::FILE* f = std::fopen(bin_path, "rb");
    if (!f) { std::perror("open inputs"); return 1; }
    std::fseek(f, 0, SEEK_END);
    long bytes = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (bytes <= 0 || bytes % (long)sizeof(GameInput) != 0) {
        std::fprintf(stderr, "inputs.bin size %ld not a multiple of sizeof(GameInput)=%zu\n",
                     bytes, sizeof(GameInput));
        return 1;
    }
    const size_t n = (size_t)(bytes / (long)sizeof(GameInput));
    std::vector<GameInput> inputs(n);
    if (std::fread(inputs.data(), sizeof(GameInput), n, f) != n) {
        std::fprintf(stderr, "short read\n"); return 1;
    }
    std::fclose(f);

    void* handle = dlopen(so_path, RTLD_NOW);
    if (!handle) { std::fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    Fn fn = (Fn)dlsym(handle, "moveDecision");
    if (!fn) { std::fprintf(stderr, "dlsym: %s\n", dlerror()); return 1; }

    // 预热：填满 icache/BTB/分支历史，使各 rep 处于同一稳态。
    for (long long i = 0; i < 200000; ++i) {
        GameOutput o = fn(&inputs[(size_t)(i % (long long)n)]);
        sink += o.k;
    }

    long fd = perf_open(cycles);
    std::printf("so=%s inputs=%s rounds=%zu calls_total=%lld reps=%d counter=%s\n",
                so_path, bin_path, n, calls_total, reps,
                cycles ? "cycles" : "instructions");
    // 外层重复整个 bin，内层顺序遍历：每次调用的循环开销恒定且与 .so 无关。
    const long long outer = calls_total / (long long)n;
    const long long actual_calls = outer * (long long)n;
    for (int rep = 0; rep < reps; ++rep) {
        ioctl(fd, PERF_EVENT_IOC_RESET, 0);
        ioctl(fd, PERF_EVENT_IOC_ENABLE, 0);
        for (long long o = 0; o < outer; ++o)
            for (size_t i = 0; i < n; ++i) {
                GameOutput r = fn(&inputs[i]);
                sink += r.k;
            }
        ioctl(fd, PERF_EVENT_IOC_DISABLE, 0);
        uint64_t count = 0;
        if (read(fd, &count, sizeof(count)) != (ssize_t)sizeof(count)) {
            std::perror("read counter"); return 2;
        }
        std::printf("rep%d %s=%llu calls=%lld raw_per_call=%.6f\n",
                    rep, cycles ? "cycles" : "instructions",
                    (unsigned long long)count, actual_calls,
                    (double)count / (double)actual_calls);
    }
    close(fd);
    dlclose(handle);
    std::fprintf(stderr, "sink=%lld\n", (long long)sink);  // 防优化，走 stderr 不污染结果
    return 0;
}
