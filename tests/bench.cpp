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
static char g_junk[16 * 1024 * 1024];
extern "C" int icacheThrash(int x) __attribute__((weak));
static void thrash(bool icache) {
    for (size_t i = 0; i < sizeof(g_junk); i += 64) g_junk[i] = (char)i;
    if (icache && icacheThrash) {
        static int seed = 1;
        seed = icacheThrash(seed);
    }
}

int main(int argc, char** argv) {
    bool cold = false, cold2 = false;
    if (argc > 1 && !strcmp(argv[1], "-cold")) { cold = true; ++argv; --argc; }
    else if (argc > 1 && !strcmp(argv[1], "-cold2")) { cold = cold2 = true; ++argv; --argc; }
    if (argc < 3) { fprintf(stderr, "用法: bench [-cold] <inputs.bin> <a.so> [b.so ...]\n"); return 1; }
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
