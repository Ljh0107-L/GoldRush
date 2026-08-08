# speed/v1 (曾名 v5f) — 现役总冠军
# 平台战绩: P50 250-260ns / P90 340-400 / 收入 1550-1900 / 4/4 扫荡 / 对第一名先手 249:251
# 源: commit 4115607 | flags 与首测二进制逐字一致
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. -o $@ $<
check:
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. -fsyntax-only player.cpp
