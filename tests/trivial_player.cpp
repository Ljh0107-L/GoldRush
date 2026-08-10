// trivial_player.cpp — 指令数标定物，不是策略。
//
// 用途：`icount` 报出的 raw_per_call 含 harness 循环自身开销。本文件的 moveDecision 无分支、
// 无循环，故其 objdump 静态指令数 == 动态指令数，可用来解出 harness 开销：
//     harness = raw(trivial) − static_body(trivial)
//     net(target) = raw(target) − harness
// 输出必须合法（动作全 4 = 原地不动，k=3），以免被合法性检查拒绝。
#include "game_api.h"

extern "C" GameOutput moveDecision(const GameInput* input) {
    (void)input;
    GameOutput out;
    out.actions[0] = 4; out.actions[1] = 4; out.actions[2] = 4;
    out.actions[3] = 4; out.actions[4] = 4; out.actions[5] = 4;
    out.k = 3;
    out.order = 0;
    out.vp = 0;
    return out;
}
