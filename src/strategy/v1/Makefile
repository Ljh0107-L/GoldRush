# strategy/v1 (曾名 cpp27b) — 天梯发布防守版(3.7μs 时代收入王)
# 平台战绩: P50 ~3.7μs / 收入 ~2712; 现仍挂在天梯防守位
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. -o $@ $<
check:
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. -fsyntax-only player.cpp
