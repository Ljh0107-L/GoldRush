// bench.cpp — 多 .so 交错回放基准器(共享机噪声免疫)。
//
// 用法: g++ -O2 -o bench tests/bench.cpp -ldl
//       ./bench logs/game_140521.bin src/a.so src/b.so ...
//
// 协议: 每个 .so 完整回放 R 遍(round 0 触发内部 reset, 可重复);
// 遍与遍在 .so 之间交错(抵消频率漂移); 每轮取跨遍最小值(剔除中断干扰);
// 输出每个 .so 的 P50/P90/P99(rdtsc 周期)。
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <vector>
#include <dlfcn.h>

static inline unsigned long long rdt() {
    unsigned lo, hi;
    __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi));
    return ((unsigned long long)hi << 32) | lo;
}

// 与 game_api.h 布局一致的不透明缓冲: 只需大小与首字段 round
struct GameOutput { int actions[6]; int k; int order; int vp; };

// 冷态模式: 每次调用后写穿一块大缓冲逐出数据缓存; -cold2 再跑一遍
// 600 个分散小函数(icache_thrash.cpp)逐出指令缓存/BTB, 近似平台
// "引擎在两轮之间冲掉我们代码+数据"的真实环境。
//
// ⛔ 2026-08-12 修复: 本缓冲此前是 `static char`(非 volatile)且**只写不读** ⇒ -O2 下
// 死存储消除把数组连同整个写循环一起删掉, **`-cold` 因此是个空操作、一直在测热态**。
// 铁证: 修复前 `size -A bench` 报 **`.bss = 16` 字节**(活的 16MiB 应报 ~16777216),
// 且 `-cold` 与 hot 墙钟同为 0.00s、P50 读数完全相同。`-cold2` 的数据那一半同样是死的
// (它只逐出 I-cache/BTB, 因为 icacheThrash 是外部调用、删不掉)。
// ⇒ 凡靠本文件 `-cold` 得出的"冷态"结论一律作废需重测; `tests/latency_bench.cpp` 与
//   `tail_path_bench.cpp`(已移出工作树, git 历史可取)的逐出器是 `volatile` 且有读, **不在作废范围内**。
// 修法两点: ① `volatile` 使写成为可观察副作用, 消除不掉;
//          ② 足迹用环境变量 `THRASH_KB` 可选 —— 逐出整个 32MiB L3 一次战役需 ~5e9 次
//             存储(不可行), 而 Zen4 逐出 L1d(32KiB)+L2(1MiB) 只需约 2MiB。
//             默认仍为 16MiB, 即不设 THRASH_KB 时复现原本**意图**的行为。
// 验收: 修复后 `size -A` 应报 `.bss` ≈ 33,554,464; 且 -cold 的墙钟与读数明显高于 hot。
static volatile char g_junk[32 * 1024 * 1024];
static size_t g_junk_n = 16u * 1024u * 1024u;   // 由 THRASH_KB 覆盖
extern "C" int icacheThrash(int x) __attribute__((weak));
static void thrash(bool icache) {
    for (size_t i = 0; i < g_junk_n; i += 64) g_junk[i] = (char)i;
    if (icache && icacheThrash) {
        static int seed = 1;
        seed = icacheThrash(seed);
    }
}

int main(int argc, char** argv) {
    bool cold = false, cold2 = false;
    if (const char* e = getenv("THRASH_KB")) {        // 逐出足迹(KiB); 见文件头修复说明
        size_t kb = strtoul(e, nullptr, 10);
        if (kb && kb <= 32u * 1024u) g_junk_n = kb * 1024u;
    }
    if (argc > 1 && !strcmp(argv[1], "-cold")) { cold = true; ++argv; --argc; }
    else if (argc > 1 && !strcmp(argv[1], "-cold2")) { cold = cold2 = true; ++argv; --argc; }
    if (argc < 3) { fprintf(stderr, "用法: bench [-cold] <inputs.bin> <a.so> [b.so ...]\n"); return 1; }
    if (cold) fprintf(stderr, "[逐出足迹 %zu KiB]\n", g_junk_n / 1024);
    FILE* f = fopen(argv[1], "rb");
    if (!f) { perror("bin"); return 1; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<char> buf(sz);
    if (fread(buf.data(), 1, sz, f) != (size_t)sz) { perror("read"); return 1; }
    fclose(f);

    // GameInput 大小从 python 侧打印得知; 由 bin 大小 / 500 轮推出
    const int ROUNDS = 500;
    const long isz = sz / ROUNDS;
    if (sz % ROUNDS) { fprintf(stderr, "bin 大小不是 500 的倍数\n"); return 1; }

    const int R = 7;                     // 遍数
    int nso = argc - 2;
    std::vector<void*> hs(nso);
    std::vector<GameOutput (*)(const void*)> fns(nso);
    for (int s = 0; s < nso; ++s) {
        hs[s] = dlopen(argv[2 + s], RTLD_NOW | RTLD_LOCAL);
        if (!hs[s]) { fprintf(stderr, "dlopen %s: %s\n", argv[2 + s], dlerror()); return 1; }
        fns[s] = (GameOutput (*)(const void*))dlsym(hs[s], "moveDecision");
        if (!fns[s]) { fprintf(stderr, "no moveDecision in %s\n", argv[2 + s]); return 1; }
    }

    std::vector<std::vector<unsigned long long>> mins(
        nso, std::vector<unsigned long long>(ROUNDS, ~0ull));
    for (int rep = 0; rep < R; ++rep) {
        for (int s = 0; s < nso; ++s) {           // 遍级交错
            for (int i = 0; i < ROUNDS; ++i) {
                const void* in = buf.data() + (long)i * isz;
                if (cold) thrash(cold2);
                unsigned long long t0 = rdt();
                volatile GameOutput o = fns[s](in);
                unsigned long long dt = rdt() - t0;
                (void)o;
                if (dt < mins[s][i]) mins[s][i] = dt;
            }
        }
    }
    for (int s = 0; s < nso; ++s) {
        std::vector<unsigned long long> v = mins[s];
        std::sort(v.begin(), v.end());
        printf("%-24s P50 %6llu  P90 %6llu  P99 %6llu (cycles)\n",
               argv[2 + s], v[ROUNDS / 2], v[ROUNDS * 9 / 10], v[ROUNDS * 99 / 100]);
    }
    return 0;
}
