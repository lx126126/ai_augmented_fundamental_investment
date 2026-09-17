#!/bin/bash
# ============================================================================
# fqf 每日刷新 · launchd 一键安装
# ============================================================================
# 为什么需要这个脚本（而不是让 agent 直接装）：
#   自动化 agent 所在的进程没有向 launchd 注册作业的授权 ——
#   `launchctl bootstrap` / `load` / `submit` 一律报
#   "Bootstrap failed: 5: Input/output error"（读 domain、enable 都正常）。
#   已用最小 plist 探针验证：与配置内容无关，是调用方权限边界。
#   故必须由「你自己的终端」执行一次。
#
# 用法：双击本文件（Finder 里），或在终端执行
#   bash ~/WorkBuddy/fqf/scripts/install_launchd.command
#
# 装完效果：每交易日（周一~周五）16:30 自动跑 daily_refresh.sh；
#   Mac 睡眠/关机错过的，在下次登录时补跑一次（RunAtLoad + .last_success 闸门）。
# ============================================================================
set -uo pipefail

LABEL="com.fqf.daily-refresh"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
SRC="$ROOT/scripts/$LABEL.plist"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_N="$(id -u)"
DOMAIN="gui/$UID_N"

echo "=============================================================="
echo " fqf 每日刷新 · launchd 安装"
echo "=============================================================="
echo "项目目录：$ROOT"
echo "任务标签：$LABEL"
echo

# ---- 前置检查 ----
if [ ! -f "$SRC" ]; then
  echo "❌ 找不到源 plist：$SRC"
  read -n 1 -p "按任意键关闭..." ; echo ; exit 1
fi
if ! plutil -lint "$SRC" >/dev/null 2>&1; then
  echo "❌ plist 语法错误，已终止："
  plutil -lint "$SRC"
  read -n 1 -p "按任意键关闭..." ; echo ; exit 1
fi
echo "✅ 源 plist 语法正确"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC" "$DST" || { echo "❌ 拷贝失败"; read -n 1 -p "按任意键关闭..."; echo; exit 1; }
echo "✅ 已安装到 $DST"

# ---- 卸载旧的（幂等：重复执行不会报错）----
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "ℹ️  已卸载旧版本任务"
launchctl unload "$DST" 2>/dev/null

# ---- 加载 ----
echo
echo "正在加载..."
if launchctl bootstrap "$DOMAIN" "$DST" 2>&1; then
  :
else
  # bootstrap 失败时退回老命令（macOS 12 上两者等价）
  echo "  bootstrap 未成功，尝试 load..."
  launchctl load "$DST" 2>&1
fi

# ---- 验证 ----
echo
echo "--------------------------------------------------------------"
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "✅ 安装成功。任务当前状态："
  echo
  launchctl print "$DOMAIN/$LABEL" 2>/dev/null | sed -n '1,12p'
  echo
  echo "--------------------------------------------------------------"
  echo "⚠️  注意：本任务配了 RunAtLoad，所以**刚才已经立即触发了一次真实刷新**"
  echo "    （拉 6 只标的的行情/估值/评级 → 重刷报告 → 重生成首页）。"
  echo "    首次执行需要 1~3 分钟，可另开一个终端看进度："
  echo
  echo "      tail -f ~/WorkBuddy/fqf/data/logs/daily_refresh_\$(date +%Y-%m-%d).log"
  echo
  echo "    跑完的标志是日志末尾出现「✅ 全部完成」。"
  echo
  echo "今后："
  echo "  · 每周一~周五 16:30 自动跑（Mac 开机状态）"
  echo "  · 关机/睡眠错过的，下次登录后补跑一次（当天已成功则跳过）"
  echo "  · 查看是否已跑过：cat ~/WorkBuddy/fqf/data/logs/.last_success"
  echo "  · 手动强制重跑：bash ~/WorkBuddy/fqf/scripts/daily_refresh.sh --force"
  echo
  echo "卸载："
  echo "  launchctl bootout $DOMAIN/$LABEL"
  echo "  rm $DST"
else
  echo "❌ 安装未生效。请把上面的输出发给 AI 排查。"
  echo
  echo "手动诊断命令："
  echo "  launchctl print $DOMAIN/$LABEL"
  echo "  plutil -lint $DST"
fi
echo "--------------------------------------------------------------"
echo
read -n 1 -p "按任意键关闭此窗口..." _ ; echo
