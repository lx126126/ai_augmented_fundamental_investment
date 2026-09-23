#!/bin/bash
# 宏观周期看板的**周更**入口（launchd 用）。
#
# 为什么宏观不并入日更（scripts/daily_refresh.sh）
# ----------------------------------------------
# ① 数据频率不匹配：本看板的 47 个指标里绝大多数是**月频/季频**（M2、CPI、社融、
#    GDP），日更只会把同一份月度数据重复渲染 22 次，徒增故障面；
# ② 失败面独立：日更跑的是「行情 + 报告重建」，一旦某天挂了要立刻修（当天收盘
#    数据过期）；宏观数据滞后一周不影响判断（多数指标本身就是月度发布）；
# ③ 产物不同：日更写 reports/（不入库）；本看板写 web/macro.html（**入库的构建
#    产物**），混在一起会让「产物是否该提交」的边界变得含混。
#
# 时间选择：**周六 10:00**。
#   - 避开交易时段（宏观指标不依赖当日盘中价，但日频行情部分要拿到周五收盘）；
#   - 宏观数据多在月中/月末发布，周更的滞后最多 7 天，对月频指标可接受。
#
# 手动运行：
#   bash scripts/macro_refresh.sh              # 本周已成功过则跳过
#   bash scripts/macro_refresh.sh --force      # 强制重跑
set -uo pipefail

ROOT="/Users/lixiao/WorkBuddy/fqf"
PY="/Users/lixiao/.workbuddy/binaries/python/envs/fqf/bin/python"
LOG_DIR="$ROOT/data/logs"
WEEK="$(date +%G-W%V)"          # ISO 周（如 2026-W39）
LOG="$LOG_DIR/macro_refresh_$WEEK.log"
LAST_SUCCESS="$LOG_DIR/.last_success_macro"

mkdir -p "$LOG_DIR" || exit 1
cd "$ROOT" || exit 1

export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$(dirname "$PY")"
export LANG="zh_CN.UTF-8"
export PYTHONIOENCODING="utf-8"
# ⚠️ 必须无缓冲：构建耗时以分钟计，有缓冲时日志会「卡在最后一行很久」，
#    从日志判断进度会一直误以为卡死（实测踩过）。
export PYTHONUNBUFFERED="1"

FORCE=0
if [ "${1:-}" = "--force" ]; then
  FORCE=1
  shift
fi

# 家用 Mac 会睡眠/关机，launchd 的定时触发会整次跳过 → plist 里配了 RunAtLoad，
# 登录时也拉起来一次；用「本周是否已成功」做闸门避免同一周重复拉数。
if [ "$FORCE" = "0" ] && [ -f "$LAST_SUCCESS" ] && [ "$(cat "$LAST_SUCCESS")" = "$WEEK" ]; then
  {
    echo "----- $(date '+%H:%M:%S') 跳过：本周（${WEEK}）已成功构建过 -----"
    echo "      强制重跑：bash scripts/macro_refresh.sh --force"
  } >> "$LOG"
  exit 0
fi

{
  echo "======================================================================"
  echo "宏观看板周更开始 · $(date '+%Y-%m-%d %H:%M:%S') · 周次 ${WEEK}"
  echo "解释器：$PY"
  echo "----------------------------------------------------------------------"
} >> "$LOG"

# 宏观构建**不读 .env**（无 LLM 环节），所以不受沙箱对密钥文件的限制，
# 任何环境都能跑 —— 这与报告链路不同（那个必须本人手动跑）。
"$PY" scripts/build_macro.py "$@" >> "$LOG" 2>&1
rc=$?

{
  echo "----------------------------------------------------------------------"
  echo "结束 rc=$rc · $(date '+%H:%M:%S')"
  echo "======================================================================"
} >> "$LOG"

if [ "$rc" != "0" ]; then
  echo "[fqf] macro_refresh 失败 rc=${rc}，日志：$LOG" >&2
  tail -n 20 "$LOG" >&2
else
  echo "$WEEK" > "$LAST_SUCCESS"
fi

exit "$rc"
