// Calibrate invariant x86 TSC ticks against CLOCK_MONOTONIC_RAW.
// Build: g++ -O2 -std=c++17 -o tsc_calibrate tests/tsc_calibrate.cpp
// Run:   ./tsc_calibrate [windows=20] [milliseconds=250] [cpu=-1]
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <numeric>
#include <sched.h>
#include <unistd.h>
#include <vector>
#include <x86intrin.h>

namespace {

long long monotonicRawNs() {
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) {
        std::fprintf(stderr, "clock_gettime: %s\n", std::strerror(errno));
        std::exit(2);
    }
    return static_cast<long long>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

unsigned long long readTsc(unsigned* aux) {
    _mm_lfence();
    const auto value = __rdtscp(aux);
    _mm_lfence();
    return value;
}

void pinCpu(int requested) {
    int cpu = requested >= 0 ? requested : sched_getcpu();
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        std::fprintf(stderr, "sched_setaffinity(cpu=%d): %s\n", cpu, std::strerror(errno));
        std::exit(2);
    }
    std::fprintf(stderr, "pinned_cpu=%d\n", cpu);
}

double percentile(std::vector<double> values, double fraction) {
    std::sort(values.begin(), values.end());
    const size_t index = std::min(values.size() - 1,
                                  static_cast<size_t>(values.size() * fraction));
    return values[index];
}

}  // namespace

int main(int argc, char** argv) {
    const int windows = argc > 1 ? std::atoi(argv[1]) : 20;
    const int milliseconds = argc > 2 ? std::atoi(argv[2]) : 250;
    const int cpu = argc > 3 ? std::atoi(argv[3]) : -1;
    if (windows < 3 || milliseconds < 10) {
        std::fprintf(stderr, "windows must be >=3 and milliseconds >=10\n");
        return 2;
    }
    pinCpu(cpu);

    std::vector<double> rates;
    unsigned long long totalTicks = 0;
    long long totalNs = 0;
    int migrations = 0;
    std::printf("window,elapsed_ns,tsc_ticks,ticks_per_ns,aux_start,aux_end\n");
    for (int i = 0; i < windows; ++i) {
        timespec sleepFor{milliseconds / 1000, (milliseconds % 1000) * 1000000L};
        const long long ns0 = monotonicRawNs();
        unsigned aux0 = 0;
        const auto t0 = readTsc(&aux0);
        while (nanosleep(&sleepFor, &sleepFor) != 0 && errno == EINTR) {}
        unsigned aux1 = 0;
        const auto t1 = readTsc(&aux1);
        const long long ns1 = monotonicRawNs();
        const long long elapsedNs = ns1 - ns0;
        const auto ticks = t1 - t0;
        const double rate = static_cast<double>(ticks) / elapsedNs;
        migrations += aux0 != aux1;
        rates.push_back(rate);
        totalTicks += ticks;
        totalNs += elapsedNs;
        std::printf("%d,%lld,%llu,%.12f,%u,%u\n", i, elapsedNs, ticks, rate, aux0, aux1);
    }

    const double weighted = static_cast<double>(totalTicks) / totalNs;
    const double median = percentile(rates, 0.50);
    const double p10 = percentile(rates, 0.10);
    const double p90 = percentile(rates, 0.90);
    const auto [minimum, maximum] = std::minmax_element(rates.begin(), rates.end());
    double squared = 0.0;
    for (double rate : rates) squared += (rate - weighted) * (rate - weighted);
    const double sd = std::sqrt(squared / (rates.size() - 1));
    std::fprintf(stderr,
                 "windows=%d duration_ms=%d migrations=%d weighted_ticks_per_ns=%.12f "
                 "median=%.12f p10=%.12f p90=%.12f min=%.12f max=%.12f "
                 "sd=%.12f spread_ppm=%.3f sd_ppm=%.3f\n",
                 windows, milliseconds, migrations, weighted, median, p10, p90,
                 *minimum, *maximum, sd, (*maximum - *minimum) / weighted * 1e6,
                 sd / weighted * 1e6);
    return migrations == 0 ? 0 : 1;
}
