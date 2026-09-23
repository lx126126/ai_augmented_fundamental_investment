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

# 🔴 收盘前不跑（2026-09-23 实测踩到）。
#
# plist 里配了 RunAtLoad，开机登录就会拉起来一次。若登录发生在**开盘前**
# （如 09:11），腾讯实时接口返回的是**上一交易日收盘价**，但它的时间戳是「现在」
#   → 快照被写成 `report_date=今天` + `is_intraday=False`
#   → 报告页印出「收盘价 09-23」，而 09-23 这一场根本还没开市；
#   → 涨跌幅写成 0.00%（盘前天然为 0），页面显示「0.00%」。
# 更糟的是脚本 rc=0 → 上面那段会把 `.last_success` 写成今天
#   → **16:30 那次真正的收盘刷新被闸门跳过**，错误的「今天」要挂一整天。
#
# 所以：宁可让页面停在上一交易日（陈旧但正确），也不要写一个不存在的「今日收盘」。
# 家用 Mac 关机导致漏掉某天，用 `scripts/backfill_snapshot.py --auto` 按日 K 补
# （腾讯实时接口补不回历史：它只能给「现在」）。
#
# 阈值取 16:15 而不是 16:00：港股 16:00–16:10 是**收市竞价**，16:00 整点拿到的
# 还不是最终收盘价（A 股 15:00 已定盘，但港股要等竞价结束）。
# ⚠️ 必须 `10#` 强制十进制：`date +%H%M` 在开盘前给出 `0911`，而 bash 的算术运算
#    会把前导 0 当八进制 → `09` 非法，直接报 value too great for base 让整条判断失效。
HHMM=$((10#$(date +%H%M)))
if [ "$FORCE" = "0" ] && [ "$HHMM" -lt 1615 ]; then
  {
    echo "----- $(date '+%H:%M:%S') 跳过：未到收盘（A 股 15:00 / 港股 16:10 竞价结束）-----"
    echo "      此时实时接口给的是昨收，会被标成「今天的收盘价」；等到 16:30 自动跑。"
    echo "      补漏掉的交易日：${PY} scripts/backfill_snapshot.py --auto"
    echo "      确要用盘前快照：bash scripts/daily_refresh.sh --force"
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
