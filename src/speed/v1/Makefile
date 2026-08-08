# speed/v1 (曾名 sv_g5nd, NS6 战役产物) — 现役总冠军(2026-08-09 加冕, 6+6 批量判决)
# 平台战绩: P50 中位 230(单窗最佳 210) / P90 340 / 收入中位 1568 / vs T-1 先手 22%
# 前任 v5f = 本文件去掉全部 -DNS6* 的默认构建(255/350, 1656), 判决见轨 CHANGELOG 8.9
# 架构: 烘焙墙(map1)+轻扫描 / pext 环级联 / 折返预折 LUT / 免拷贝出参 / 弹窗 rows3
# 警告: BAKED_W1 是 map1 常量 —— 换图必须重烘焙(源内有生成说明)
NSFLAGS = -DNS6W -DNS6K -DNS6O -DNS6X -DNS6B3 -DNS6F -DNS6C2 -DNS6C3
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. $(NSFLAGS) -o $@ $<
v5f:
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. -o player_v5f.so player.cpp
check:
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. $(NSFLAGS) -fsyntax-only player.cpp
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. -fsyntax-only player.cpp
