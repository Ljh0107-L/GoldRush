# v3 — 300ns 单内核(锚点驻守+轮换扫描, 无矿堆/巡逻/目标机器)
CXXFLAGS = -std=c++17 -O3 -fPIC -Wall -Wextra -shared -I..

player.so: player.cpp ../game_api.h
	g++ $(CXXFLAGS) -march=native -o $@ $<

local: player.cpp ../game_api.h
	c++ $(CXXFLAGS) -o player.so player.cpp

check:
	c++ $(CXXFLAGS) -fsyntax-only player.cpp
