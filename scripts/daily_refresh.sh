#!/bin/bash
# 每日行情刷新的 launchd 入口。
#
# 为什么用 shell 包一层：launchd 的运行环境极简（没有 PATH、没有 locale、不加载
# 你的 shell 配置），所以这里必须用解释器绝对路径，并显式给出 PATH / LANG。
#
# 日志落在 data/logs/（在 .gitignore 覆盖范围内，不会进仓库）。
#
# 手动运行：
#   bash scripts/daily_refresh.sh              # 当天已成功过则跳过
#   bash scripts/daily_refresh.sh --force      # 强制重跑
#   bash scripts/daily_refresh.sh -- --dry-run # 透传参数给 daily_refresh.py
# ⚠️ 变量引用一律用 ${VAR} 显式界定：本脚本 LANG=zh_CN.UTF-8，bash 会把紧跟在
#    变量名后的全角标点（，）」等）的 UTF-8 字节当成变量名字符，得到 `rc，`
#    这种不存在的变量名 —— 在 set -u 下直接 unbound variable 崩溃。
set -uo pipefail

ROOT="/Users/lixiao/WorkBuddy/fqf"
PY="/Users/lixiao/.workbuddy/binaries/python/envs/fqf/bin/python"
LOG_DIR="$ROOT/data/logs"
TODAY="$(date +%Y-%m-%d)"
LOG="$LOG_DIR/daily_refresh_$TODAY.log"
LAST_SUCCESS="$LOG_DIR/.last_success"

mkdir -p "$LOG_DIR" || exit 1
cd "$ROOT" || exit 1

export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$PY")"
export LANG="zh_CN.UTF-8"
export PYTHONIOENCODING="utf-8"

FORCE=0
if [ "${1:-}" = "--force" ]; then
  FORCE=1
  shift
fi

# 是否 dry-run：dry-run 成功不能写 .last_success，否则当天真正的定时任务会被跳过
DRY=0
for _a in "$@"; do
  [ "$_a" = "--dry-run" ] && DRY=1
done

# 家用 Mac 经常睡眠/关机，launchd 的定时触发会整次跳过。plist 里配了 RunAtLoad，
# 登录/唤醒时也会拉起来一次 —— 用「当天是否已成功」做闸门避免重复拉取。
if [ "$FORCE" = "0" ] && [ -f "$LAST_SUCCESS" ] && [ "$(cat "$LAST_SUCCESS")" = "$TODAY" ]; then
  {
    echo "----- $(date '+%H:%M:%S') 跳过：今天（${TODAY}）已成功刷新过 -----"
    echo "      强制重跑：bash scripts/daily_refresh.sh --force"
  } >> "$LOG"
  exit 0
fi

{
  echo "======================================================================"
  echo "每日行情刷新开始 · $(date '+%Y-%m-%d %H:%M:%S')"
  echo "解释器：$PY"
  echo "----------------------------------------------------------------------"
} >> "$LOG"

"$PY" scripts/daily_refresh.py "$@" >> "$LOG" 2>&1
rc=$?

{
  echo "----------------------------------------------------------------------"
  echo "结束 rc=$rc · $(date '+%H:%M:%S')"
  echo "======================================================================"
} >> "$LOG"

if [ "$rc" != "0" ]; then
  # 失败时把日志尾部打到 stderr，方便 launchd 的 StandardErrorPath 也留痕
  echo "[fqf] daily_refresh 失败 rc=${rc}，日志：$LOG" >&2
  tail -n 20 "$LOG" >&2
elif [ "$DRY" = "0" ]; then
  echo "$TODAY" > "$LAST_SUCCESS"
fi

exit "$rc"
