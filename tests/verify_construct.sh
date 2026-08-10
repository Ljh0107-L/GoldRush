#!/usr/bin/env bash
# verify_construct.sh — 交付构型的一键验收护栏。
#
# 为什么存在：现役构型含一段**死垫**（`asm(".space N, 0x90")`），用来把 `moveDecision` 的入口
# 拉回模 64 的 `0x10` 档。四档扫描已证 `0x20`/`0x30` 各 +11.67ns ≈ −128 金，而埋下这段垫子的
# 那一刀只值 +23 金——**踩雷代价是收益的 5.5 倍**。垫长与 `decide` 的体积耦合：任何改动
# `decide` 的刀都会平移入口地址，若不重算垫长就白付布局税，而且 `pair_diff` 全绿、体积正常、
# 指令数正常，**没有任何常规信号会告警**。本脚本就是那个告警。
#
# **改过 `decide`（或任何进入 `moveDecision` 的代码）之后，必须在赛事机上跑本脚本。**
#
# 四道断言：
#   1. `moveDecision` 入口 mod64 == 0x10；失败时打印实际档位与建议的 `.space` 新值
#   2. AVX512-FP16 越界指令计数 == 0（8.9 曾在 Intel 机误编出 `vmovw` 致平台 SIGILL 判负）
#   3. 产物 SHA256 与 `CHANGELOG.md` 现役档案登记值一致；不一致时区分
#      「预期的改动（你改了源码，去更新登记）」与「未登记的漂移（源码没变但产物变了）」
#   4. 三图 `pair_diff` 相对基线 == 0/500（等价重构才需要；行为刀用 --skip-pair-diff 跳过）
#
# 用法（赛事机 quant-compiler）：
#   tests/verify_construct.sh --baseline-so /tmp/base.so
#   tests/verify_construct.sh --baseline-commit f18064c        # 需在 git 仓库内运行
#   tests/verify_construct.sh --baseline-so /tmp/base.so --skip-pair-diff
#
# 退出码：0 = 全过；非 0 = 失败项数。失败信息都带可执行的修复动作。
set -uo pipefail      # 故意不用 -e：要跑完所有检查再汇总，而不是首个失败就退出

readonly WANT_MOD64=$((0x10))
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly BUILD_CMD_FLAGS=(-std=c++17 -O3 -march=native -fPIC -Wall -Wextra -shared)
# AVX512-FP16 指令族：历史判例是 `vmovw`，其余 ph 后缀同属评测机不支持的越界集合
readonly FP16_RE='vmovw|vmovsh|vcvtph|vcvtsh|vadd[sp]h|vsub[sp]h|vmul[sp]h|vdiv[sp]h|vfmadd[0-9]*[sp]h|vcmp[sp]h|vmax[sp]h|vmin[sp]h|vsqrt[sp]h|vrcpph|vrsqrtph'

SRC_DIR="$ROOT/src"
LOGS_DIR="$ROOT/logs"
BASELINE_SO=""
BASELINE_COMMIT=""
SKIP_PAIR_DIFF=0
KEEP=0
LOG_LIST=()

die()  { printf '\033[31mfatal:\033[0m %s\n' "$*" >&2; exit 99; }
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILURES=$((FAILURES+1)); }
warn() { printf '  \033[33mSKIP\033[0m  %s\n' "$*"; }

usage() { sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --baseline-so)     BASELINE_SO="${2:-}"; shift 2 ;;
        --baseline-commit) BASELINE_COMMIT="${2:-}"; shift 2 ;;
        --src)             SRC_DIR="${2:-}"; shift 2 ;;
        --logs-dir)        LOGS_DIR="${2:-}"; shift 2 ;;
        --log)             LOG_LIST+=("${2:-}"); shift 2 ;;
        --skip-pair-diff)  SKIP_PAIR_DIFF=1; shift ;;
        --keep)            KEEP=1; shift ;;
        -h|--help)         usage ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

FAILURES=0
WORK="$(mktemp -d "${TMPDIR:-/tmp}/verify_construct.XXXXXX")"
cleanup() { [[ $KEEP -eq 1 ]] && printf 'kept workdir: %s\n' "$WORK" || rm -rf "$WORK"; }
trap cleanup EXIT

[[ -f "$SRC_DIR/player.cpp" ]]  || die "no player.cpp under $SRC_DIR (use --src)"
[[ -f "$SRC_DIR/game_api.h" ]]  || die "no game_api.h under $SRC_DIR"
command -v g++      >/dev/null || die "g++ not found; this script must run on the contest build machine"
command -v objdump  >/dev/null || die "objdump not found (binutils)"

printf '== verify_construct ==\n'
printf 'host=%s  arch=%s\n' "$(hostname)" "$(uname -m)"
printf 'gcc=%s\n' "$(g++ --version | head -1)"
if [[ "$(uname -m)" != "x86_64" ]]; then
    fail "arch is $(uname -m), not x86_64 — the artifact built here is NOT submittable.
        Run this on quant-compiler (ssh Ubiquant220@8.153.76.120). Historical judgement:
        building on the wrong machine emitted AVX512-FP16 and the platform returned SIGILL."
fi
printf 'src=%s\n\n' "$SRC_DIR"

# ---------------------------------------------------------------- build candidate
CAND="$WORK/cand.so"
if ! g++ "${BUILD_CMD_FLAGS[@]}" -o "$CAND" "$SRC_DIR/player.cpp" -I"$SRC_DIR" 2>"$WORK/build.log"; then
    sed 's/^/        /' "$WORK/build.log" >&2
    die "candidate build failed"
fi
[[ -s "$WORK/build.log" ]] && { printf 'build warnings:\n'; sed 's/^/        /' "$WORK/build.log"; }
CAND_SHA="$(sha256sum "$CAND" | cut -d' ' -f1)"

# ------------------------------------------------------- check 1: entry mod64 bucket
addr_hex="$(nm -D --defined-only "$CAND" 2>/dev/null | awk '$3=="moveDecision"{print $1}' | head -1)"
[[ -n "$addr_hex" ]] || die "cannot locate moveDecision in the artifact"
addr=$((16#$addr_hex))
mod64=$((addr % 64))
if [[ $mod64 -eq $WANT_MOD64 ]]; then
    pass "$(printf 'entry mod64 = 0x%02x at 0x%x (the only proven-good bucket)' "$mod64" "$addr")"
else
    cur_pad="$(grep -oE '\.space[[:space:]]+[0-9]+' "$SRC_DIR/player.cpp" | grep -oE '[0-9]+' | head -1)"
    delta=$(( (WANT_MOD64 - mod64 + 64) % 64 ))
    if [[ -n "$cur_pad" ]]; then
        fail "$(printf 'entry mod64 = 0x%02x at 0x%x, want 0x%02x.
        Buckets 0x20 and 0x30 are each measured at +11.67ns (about -128 gold); 0x00 shows no gain.
        FIX: in %s change  asm(".space %s, 0x90");  to  asm(".space %s, 0x90");  then re-run.
        (pad %s + %s = %s bytes shifts the entry forward into the 0x10 bucket)' \
        "$mod64" "$addr" "$WANT_MOD64" "$SRC_DIR/player.cpp" "$cur_pad" "$((cur_pad + delta))" "$cur_pad" "$delta" "$((cur_pad + delta))")"
    else
        fail "$(printf 'entry mod64 = 0x%02x at 0x%x, want 0x%02x, and no .space pad was found in player.cpp.
        FIX: insert  asm(".space %s, 0x90");  immediately before the moveDecision definition
        (after the anonymous namespace closes), then re-run.' "$mod64" "$addr" "$WANT_MOD64" "$delta")"
    fi
fi

# ------------------------------------------------------------ check 2: FP16 out-of-range
fp16=$(objdump -d "$CAND" | grep -cE "[[:space:]](${FP16_RE})[[:space:]]" || true)
if [[ "$fp16" -eq 0 ]]; then
    pass "AVX512-FP16 instruction count = 0"
else
    fail "$(printf 'found %s AVX512-FP16 instructions — the evaluator does NOT support them and will SIGILL.
        Offenders:
%s
        FIX: you are almost certainly compiling on the wrong machine. Build on quant-compiler
        (AMD EPYC 9T25, avx512fp16 absent from flags), not on an Intel host.' \
        "$fp16" "$(objdump -d "$CAND" | grep -oE "[[:space:]](${FP16_RE})[[:space:]]" | sort -u | head -5 | sed 's/^/          /')")"
fi

# ------------------------------------- check 3: artifact SHA vs CHANGELOG registration
CHANGELOG="$SRC_DIR/CHANGELOG.md"
if [[ ! -f "$CHANGELOG" ]]; then
    warn "no CHANGELOG.md under $SRC_DIR — cannot check the SHA registration"
else
    reg_sha="$(grep -A1 -iE '产物 SHA256|artifact SHA256' "$CHANGELOG" \
               | grep -oE '[0-9a-f]{64}' | head -1)"
    if [[ -z "$reg_sha" ]]; then
        reg_sha="$(grep -oE '[0-9a-f]{64}' "$CHANGELOG" | head -1)"
    fi
    if [[ -z "$reg_sha" ]]; then
        warn "no 64-hex SHA256 found in CHANGELOG.md; register the artifact hash in the live-archive table"
    elif [[ "$reg_sha" == "$CAND_SHA" ]]; then
        pass "artifact SHA256 matches the CHANGELOG registration (${CAND_SHA:0:16}...)"
    else
        # Distinguish an intentional source change from unexplained toolchain drift.
        verdict="cannot classify (no git repo here, so I cannot tell whether player.cpp changed)"
        if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
            reg_commit="$(grep -oE '`[0-9a-f]{7,40}`' "$CHANGELOG" | tr -d '`' | head -1)"
            if [[ -n "$reg_commit" ]] && git -C "$ROOT" cat-file -e "${reg_commit}^{commit}" 2>/dev/null; then
                if git -C "$ROOT" diff --quiet "$reg_commit" -- src/player.cpp 2>/dev/null; then
                    verdict="UNREGISTERED DRIFT — player.cpp is identical to $reg_commit yet the artifact differs.
        Suspect a different build machine, compiler version or flags. Do NOT submit until explained."
                else
                    verdict="EXPECTED — player.cpp differs from $reg_commit, so you changed the source.
        FIX: update the live-archive four-tuple in CHANGELOG.md with the new SHA256 above."
                fi
            fi
        fi
        fail "$(printf 'artifact SHA256 does not match the registration.
          built:      %s
          registered: %s
        %s' "$CAND_SHA" "$reg_sha" "$verdict")"
    fi
fi

# ----------------------------------------------- check 4: three-map pair_diff vs baseline
if [[ $SKIP_PAIR_DIFF -eq 1 ]]; then
    warn "pair_diff skipped by --skip-pair-diff (only legitimate for an intentional behaviour change)"
else
    base_so=""
    if [[ -n "$BASELINE_SO" ]]; then
        [[ -f "$BASELINE_SO" ]] || die "--baseline-so $BASELINE_SO does not exist"
        base_so="$BASELINE_SO"
    elif [[ -n "$BASELINE_COMMIT" ]]; then
        if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
            mkdir -p "$WORK/base"
            git -C "$ROOT" show "$BASELINE_COMMIT:src/player.cpp"  > "$WORK/base/player.cpp" 2>/dev/null \
                || die "cannot read src/player.cpp at $BASELINE_COMMIT"
            git -C "$ROOT" show "$BASELINE_COMMIT:src/game_api.h" > "$WORK/base/game_api.h" 2>/dev/null \
                || cp "$SRC_DIR/game_api.h" "$WORK/base/game_api.h"
            g++ "${BUILD_CMD_FLAGS[@]}" -o "$WORK/base.so" "$WORK/base/player.cpp" -I"$WORK/base" \
                2>/dev/null || die "baseline build from $BASELINE_COMMIT failed"
            base_so="$WORK/base.so"
        else
            die "--baseline-commit needs a git repo at $ROOT (there is none here).
      On the contest machine pass a prebuilt baseline instead: --baseline-so /tmp/base.so"
        fi
    else
        die "need --baseline-so PATH or --baseline-commit REF (or --skip-pair-diff)"
    fi

    if [[ ${#LOG_LIST[@]} -eq 0 ]]; then
        # Pick one log per distinct map, identified by the wall-token count on line 2.
        declare -A seen=()
        for f in "$LOGS_DIR"/*.log; do
            [[ -f "$f" ]] || continue
            fp="$(sed -n '2p' "$f" | tr -cd '1' | wc -c | tr -d ' ')"
            [[ "$fp" == "0" ]] && continue
            if [[ -z "${seen[$fp]:-}" ]]; then seen[$fp]="$f"; LOG_LIST+=("$f"); fi
        done
    fi
    if [[ ${#LOG_LIST[@]} -lt 3 ]]; then
        fail "$(printf 'only %s distinct map(s) available for pair_diff under %s.
        Equivalence must be shown on all three maps — a single map cannot catch a
        map-specific divergence (the wall tables differ: 40 / 24 / 78 walls).
        FIX: copy one 500-round log per map onto this machine, or pass --log A --log B --log C.' \
        "${#LOG_LIST[@]}" "$LOGS_DIR")"
    else
        printf '  ....  pair_diff on %d maps vs %s\n' "${#LOG_LIST[@]}" "$(basename "$base_so")"
        pd_out="$("$(command -v python3)" "$ROOT/tests/pair_diff.py" "$base_so" "$CAND" "${LOG_LIST[@]}" 2>&1)"
        pd_rc=$?
        bad="$(printf '%s\n' "$pd_out" | grep -vcE '分歧 0/|diff 0/' || true)"
        if [[ $pd_rc -eq 0 ]]; then
            pass "pair_diff 0/500 on all ${#LOG_LIST[@]} maps"
            printf '%s\n' "$pd_out" | sed 's/^/          /'
        else
            fail "$(printf 'pair_diff found divergences (%s line(s) non-zero) — this is NOT an equivalence refactor.
%s
        FIX: either make the change behaviour-preserving, or if the behaviour change is
        intentional, this gate does not apply and you owe a head-to-head income batch
        instead (>=3 same-window interleaved pairs, see INFRA section 4).' \
            "$bad" "$(printf '%s\n' "$pd_out" | sed 's/^/          /')")"
        fi
    fi
fi

# ------------------------------------------------------------------------- summary
printf '\n'
if [[ $FAILURES -eq 0 ]]; then
    printf '\033[32mALL CHECKS PASSED\033[0m  sha256=%s\n' "$CAND_SHA"
    printf 'Reminder: passing these four gates proves the artifact is well-formed and behaviour-\n'
    printf 'preserving. It does NOT prove it is faster — that needs perf cycles per call, same-window\n'
    printf 'paired interleaved (gate 2-prime, INFRA section 4). Instruction count alone is not evidence.\n'
    exit 0
fi
printf '\033[31m%d CHECK(S) FAILED\033[0m — do not submit or land this construct.\n' "$FAILURES"
exit "$FAILURES"
