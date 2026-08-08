# speed/v2 (曾名 v5r) — 210ns 技术储备(单扫轮换+罐头折返)
# 平台战绩: P50 210 / P90 250-270; 收入仅 520(轮换收入天花板, 见轨内 CHANGELOG)
# 源: commit 4b42bf3 | flags 与首测二进制逐字一致(注意 -O2 与 falign)
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O2 -march=native -falign-functions=64 -fPIC -Wall -Wextra -shared -I../.. -DNS5ROWS3 -DNS5ALT -o $@ $<
check:
	c++ -std=c++17 -O2 -fPIC -Wall -I../.. -DNS5ROWS3 -DNS5ALT -fsyntax-only player.cpp
