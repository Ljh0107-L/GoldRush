# strategy/v2 (曾名 v42) — 双扫矿堆农(策略优先冠军)
# 平台战绩: P50 700-790ns / 收入 1660-2120 / 首斩力行 1958:1234
# 源: commit 3f1c106 的 src/v3/player.cpp | 三 flag 逐字一致
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. -DNS3DUAL -DNS3FILL0 -DNS3NOPLAN -o $@ $<
check:
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. -DNS3DUAL -DNS3FILL0 -DNS3NOPLAN -fsyntax-only player.cpp
