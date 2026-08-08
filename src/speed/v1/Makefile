# speed/v1 (曾名 sv_g11, NS6 战役最终形态) — 现役总冠军(2026-08-09 二次加冕)
# 平台战绩: P50 中位 200(读数 170-230) / P90 中位 292 / 收入中位 1515 (6 局, 150881-93)
# vs T-1(150894): 210/290 vs 290/350, 先手 427/500(85%) —— 首次翻转第一快枪手
# 架构: 烘焙墙+轻扫描 / 装载-扫描融合 / pext环级联 / 折返预折LUT / 队友检查下岗 /
#       sanitize+SAFE_OUT+钳位军械库下岗 / 弹窗rows3 / 边缘常量表
# 指令账: 645/调用(前任 g5nd 764, 初代 v5f 1008); 模型 40壳+20载+129计算=189
# 警告: BAKED_W1 是 map1 常量 —— 换图必须重烘焙(源内有生成说明)
NSFLAGS = -DNS6W -DNS6K -DNS6O -DNS6X -DNS6B3 -DNS6F -DNS6C2 -DNS6C3 -DNS6FU -DNS6NS -DNS6NC -DNS6NT
player.so: player.cpp ../../game_api.h
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. $(NSFLAGS) -o $@ $<
v5f:
	g++ -std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared -I../.. -o player_v5f.so player.cpp
check:
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. $(NSFLAGS) -fsyntax-only player.cpp
	c++ -std=c++17 -O3 -fPIC -Wall -I../.. -fsyntax-only player.cpp
