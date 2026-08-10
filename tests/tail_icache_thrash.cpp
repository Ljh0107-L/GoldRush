// tail_icache_thrash.cpp — I-cache / BTB eviction payload for the tail-width bench.
//
// Why it exists: the `escapeStep` constant-shaping knife (CHANGELOG `f42ce09`) showed
// **nothing** in ordinary hot replay and −27 cycles only under a ~68 KB I-cache/BTB
// eviction condition. Any tail-width measurement therefore has to reproduce that
// condition, and the condition has to be *stated in bytes* rather than in function
// count, because `aligned(64)` plus a data-dependent branch makes the per-function
// stride a compiler decision (64 B or 128 B), i.e. 600 functions is 37 KB or 75 KB.
//
// `icacheThrashBytes()` reports the measured address span of the generated functions,
// so the report can quote the eviction condition as a byte figure that a reader can
// re-derive from the artifact.
//
// Build (linked into the bench, not a shared object):
//   g++ -std=c++17 -O2 -c tests/tail_icache_thrash.cpp -o thrash.o
// Optional: -DTHRASH_FNS=N to change the footprint (default 600, matching the
// historical `icache_thrash.cpp` used by the escapeStep judgement).
#include <array>
#include <cstddef>
#include <cstdint>
#include <utility>

#ifndef THRASH_FNS
#define THRASH_FNS 600
#endif

namespace {

// Each victim is 64-byte aligned and contains one data-dependent conditional branch,
// so walking them evicts L1i lines *and* consumes BTB entries.
template <int I>
__attribute__((noinline, aligned(64))) int thrashOne(int x) {
    if ((x + I) & 1) {
        asm volatile("" ::: "memory");
        return x * 33 + I;
    }
    asm volatile("" ::: "memory");
    return x * 17 - I;
}

using Fn = int (*)(int);

template <int... I>
constexpr std::array<Fn, sizeof...(I)> makeFns(std::integer_sequence<int, I...>) {
    return {{&thrashOne<I>...}};
}

constexpr auto FNS = makeFns(std::make_integer_sequence<int, THRASH_FNS>{});

}  // namespace

extern "C" int icacheThrash(int x) {
    for (Fn f : FNS) x = f(x);
    return x;
}

extern "C" size_t icacheThrashCount() { return FNS.size(); }

// Address span of the victim set, in bytes. This is the number the report must quote
// as the eviction condition; it is measured from the artifact, not assumed.
extern "C" size_t icacheThrashBytes() {
    uintptr_t lo = ~static_cast<uintptr_t>(0), hi = 0;
    for (Fn f : FNS) {
        const uintptr_t a = reinterpret_cast<uintptr_t>(f);
        if (a < lo) lo = a;
        if (a > hi) hi = a;
    }
    // The last victim occupies at least one 64-byte line beyond its entry.
    return static_cast<size_t>(hi - lo) + 64;
}
