# ns300 v2 — 300ns 预算主线
# 开发机: make        (linux .so, -march=native)
# 本机:   make local  (mac .so, 测试用)
CXXFLAGS = -std=c++17 -O3 -fPIC -Wall -Wextra -shared -I..

player.so: player.cpp ../game_api.h
	g++ $(CXXFLAGS) -march=native -o $@ $<

local: player.cpp ../game_api.h
	c++ $(CXXFLAGS) -o player.so $<

check:
	c++ $(CXXFLAGS) -fsyntax-only $<
