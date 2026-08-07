# v5 — 200ns 信封版(可用资源勘定基线)
CXXFLAGS = -std=c++17 -O3 -fPIC -Wall -Wextra -shared -I..
player.so: player.cpp ../game_api.h
	g++ $(CXXFLAGS) -march=native -o $@ $<
local: player.cpp ../game_api.h
	c++ $(CXXFLAGS) -o player.so player.cpp
check:
	c++ $(CXXFLAGS) -fsyntax-only player.cpp
