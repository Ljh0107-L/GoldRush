# strategy/v2a (曾名 v4) — 三阶段时间框架(A开局BFS/B事件轮/C稳态)
# 状态: 实验线。含 ctz 哨兵修复后未经平台复测(修复前收入崩盘读数作废)
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. -o $@ $<
check:
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. -fsyntax-only player.cpp
