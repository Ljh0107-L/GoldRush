# GoldRush 2.0 C++ Makefile
#
# 提交用 .so 必须在开发机(8.153.76.120, x86_64 EPYC, 指令集≈评测机)上编译:
#   make            -> player.so (提交这个)
# 本机(macOS/arm64)开发时:
#   make check      -> 只做语法/告警检查, 不出产物
#   make local      -> 编译本机架构的 player.so, 仅供 tests/test_so.py 跑逻辑与相对延迟

CXX      = g++
CXXFLAGS = -std=c++17 -O3 -march=native -fPIC -Wall -Wextra
TARGET   = player.so
SRC      = player.cpp

all: $(TARGET)

$(TARGET): $(SRC) game_api.h
	$(CXX) $(CXXFLAGS) -shared -o $(TARGET) $(SRC)

check:
	$(CXX) -std=c++17 -Wall -Wextra -fsyntax-only $(SRC)

local: $(SRC) game_api.h
	c++ -std=c++17 -O3 -fPIC -Wall -Wextra -shared -o $(TARGET) $(SRC)

clean:
	rm -f $(TARGET)

.PHONY: all check local clean
