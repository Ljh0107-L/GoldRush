#!/bin/bash
# runbatch.sh — 平台跑批(从 scratchpad 转正)
# 用法: runbatch.sh a.so b.so ...          # 各打一局 vs cpp1(需 CPP1 环境变量指路)
#       VS=<对手id> runbatch.sh a.so       # 约战指定对手
# 输出: 每局一行战报; 日志落 logs/
cd "$(dirname "$0")/.." || exit 1
CPP1=${CPP1:-cpp1.so}
for so in "$@"; do
  name=$(basename "$so" .so)
  if [ -n "$VS" ]; then
    ./tools/gr.py submit --map 1 --vs "$VS" "$so:$name" >/dev/null 2>&1
  else
    ./tools/gr.py submit --map 1 "$so:$name" "$CPP1:cpp1" >/dev/null 2>&1
  fi
  sleep 3
  gid=$(./tools/gr.py games 2>&1 | sed -n 2p | awk '{print $1}')
  ./tools/gr.py watch "$gid" >/dev/null 2>&1
  mv "game_$gid.log" logs/ 2>/dev/null
  echo "== $name${VS:+ vs$VS} $gid"
  ./tools/grlog.py "logs/game_$gid.log" 2>&1 | sed -n '5,6p'
done
