# v4 — 时间框架架构: A开局(无限预算BFS) / B事件轮(视野找堆) / C稳态(210ns)
CXXFLAGS = -std=c++17 -O3 -fPIC -Wall -Wextra -shared -I..

player.so: player.cpp ../game_api.h
	g++ $(CXXFLAGS) -march=native -o $@ $<

local: player.cpp ../game_api.h
	c++ $(CXXFLAGS) -o player.so player.cpp

check:
	c++ $(CXXFLAGS) -fsyntax-only player.cpp
