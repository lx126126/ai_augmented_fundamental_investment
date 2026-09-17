#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据更新证据链：一条命令回答「怎么证明数据更新过」。

为什么需要这个脚本
------------------
「日更在 16:30 跑」是**看不见的**：launchd 不弹窗、不改页面标题、日志在 `data/logs/`
里躺着。而比对表此前根本不在日更链路里、手机首页卡片又只有公司名没有数字
（2026-09-17 已修），所以真实感受就是「我感知不到」。

本脚本把散落在六处的痕迹按「从软到硬」排列打印出来，**最硬的是文件 mtime 和
按日归档的价格序列** —— 这些改不了、伪造不了。

六个证据层（越往下越硬）
------------------------
  1. 调度活着吗      launchctl 作业状态 + 闸门文件
  2. 今天跑了什么    data/logs/daily_refresh_YYYY-MM-DD.log
  3. 文件被改写了吗  data/raw/{code}/quote.parquet 的 mtime 落在今天？
  4. 价格真的变了吗  data/market/{code}_quote.parquet 的逐日归档序列
  5. 报告跟着重刷了吗 报告 HTML 的 mtime 是否晚于数据
  6. 网页产物刷新了吗 web/index.html、web/watchlist.html

用法：
    python scripts/check_refresh.py              # 全池
    python scripts/check_refresh.py --codes 601088,600519
    python scripts/check_refresh.py --days 8     # 历史序列多看几天
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import watchlist_store as wl  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
MARKET_DIR = ROOT / "data" / "market"
LOGS_DIR = ROOT / "data" / "logs"
REPORTS_DIR = ROOT / "reports"
WEB_DIR = ROOT / "web"
LABEL = "com.fqf.daily-refresh"

W = 78


def _ts(p: Path) -> str:
    if not p.exists():
        return "（不存在）"
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M:%S")


def _head(n: int, title: str) -> None:
    print(f"\n{'─' * W}\n【{n}】{title}\n{'─' * W}")


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"({type(e).__name__}: {e})"


# --------------------------------------------------------------------------- #
# 1 调度
# --------------------------------------------------------------------------- #

def section_sched() -> bool:
    _head(1, "调度活着吗（软证据：可以被人为改，但能一眼看出「压根没跑」）")
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    raw = _run(["launchctl", "print", f"gui/{uid}/{LABEL}"])

    if "Could not find service" in raw or not raw.strip():
        print(f"  ❌ 作业 {LABEL} 未注册 —— 根本没有任何自动调度")
        print(f"     安装：open scripts/install_launchd.command")
        return False

    def grab(key: str) -> str:
        m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+)$", raw, re.M)
        return m.group(1).strip() if m else "?"

    runs, exit_code, state = grab("runs"), grab("last exit code"), grab("state")
    print(f"  作业 {LABEL}")
    print(f"    state = {state}   runs = {runs}   last exit code = {exit_code}")
    print(f"    plist = {grab('path')}")

    triggers = re.findall(r'"Weekday" => (\d+)', raw)
    hours = re.findall(r'"Hour" => (\d+)', raw)
    minutes = re.findall(r'"Minute" => (\d+)', raw)
    if triggers and hours and minutes:
        wd = ",".join(sorted(set(triggers)))
        print(f"    定时 = 周{wd} {hours[0]}:{minutes[0].zfill(2)}"
              f"（共 {len(triggers)} 条 calendar trigger 已装载）")

    gate = LOGS_DIR / ".last_success"
    if gate.exists():
        val = gate.read_text(encoding="utf-8").strip()
        today_ok = val == date.today().isoformat()
        mark = "✅" if today_ok else "⚠️"
        print(f"    {mark} 闸门 .last_success = {val}"
              f"{'（就是今天 → 今天确实成功跑完过）' if today_ok else '（不是今天 → 今天还没成功跑完）'}")
    else:
        print("    ⚠️ 闸门 .last_success 不存在 → 今天还没跑成功过（或从未跑成功）")
    return True


# --------------------------------------------------------------------------- #
# 2 日志
# --------------------------------------------------------------------------- #

def section_log() -> None:
    _head(2, "今天跑了什么（日志）")
    today = date.today().isoformat()
    log = LOGS_DIR / f"daily_refresh_{today}.log"
    if not log.exists():
        print(f"  今天没有日志（{log.name}）→ 今天没跑过，或还没到 16:30")
        if LOGS_DIR.exists():
            older = sorted(LOGS_DIR.glob("daily_refresh_*.log"))[-4:]
            for o in older:
                print(f"    历史日志：{o.name}  {_ts(o)}")
        return

    txt = log.read_text(encoding="utf-8", errors="replace").splitlines()
    keep = [l for l in txt if not any(k in l for k in
            ("PerformanceWarning", "frame.insert", "pymupdf", "fitz"))]
    starts = [l for l in keep if "每日行情刷新开始" in l]
    ends = [l for l in keep if "结束 rc=" in l]
    fails = [l for l in keep if "❌" in l]
    print(f"  日志 {log.name}  （{len(txt)} 行，{_ts(log)} 最后写入）")
    for s in starts[-1:]:
        print(f"    {s.strip()}")
    for e in ends[-1:]:
        print(f"    {e.strip()}")
    if starts and ends:
        try:
            t0 = datetime.strptime(re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", starts[-1]).group(1),
                                   "%Y-%m-%d %H:%M:%S")
            t1 = datetime.strptime(f"{today} {re.search(r'· (\d{2}:\d{2}:\d{2})', ends[-1]).group(1)}",
                                   "%Y-%m-%d %H:%M:%S")
            print(f"    耗时 ≈ {int((t1 - t0).total_seconds() // 60)} 分 "
                  f"{int((t1 - t0).total_seconds() % 60)} 秒")
        except Exception:
            pass
    for f in fails[-3:]:
        print(f"    {f.strip()}")
    print("    最近 3 行：")
    for l in keep[-3:]:
        print(f"      {l[:110]}")


# --------------------------------------------------------------------------- #
# 3-5 逐标的
# --------------------------------------------------------------------------- #

def _quote_row(code: str) -> dict | None:
    p = RAW_DIR / code / "quote.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    r = df.iloc[-1]
    d = r.get("report_date")
    return {
        "price": float(r["price"]) if pd.notna(r.get("price")) else None,
        "chg": float(r["change_pct"]) if pd.notna(r.get("change_pct")) else None,
        "date": pd.Timestamp(d).strftime("%Y-%m-%d") if pd.notna(d) else None,
        "mtime": p.stat().st_mtime,
    }


def _history(code: str) -> pd.DataFrame | None:
    p = MARKET_DIR / f"{code}_quote.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    df["report_date"] = pd.to_datetime(df["report_date"])
    return df.sort_values("report_date").drop_duplicates("report_date", keep="last")


def section_files(codes: list[str]) -> None:
    _head(3, "数据文件被改写了吗（硬证据：文件 mtime 不会说谎）")
    print(f"  {'代码':<8}{'现价':>9}{'涨跌':>9}  {'股价日期':<12}{'quote.parquet mtime':<22}{'归档天数':>6}")
    changed_today = 0
    for c in codes:
        q = _quote_row(c)
        if not q:
            print(f"  {c:<8}{'—':>9}{'—':>9}  {'（无 quote.parquet）'}")
            continue
        chg = f"{q['chg']:+.2f}%" if q["chg"] is not None else "—"
        px = f"{q['price']:.2f}" if q["price"] is not None else "—"
        h = _history(c)
        n = 0 if h is None else len(h)
        mt = datetime.fromtimestamp(q["mtime"])
        is_today = mt.date() == date.today()
        changed_today += is_today
        flag = "✅" if is_today else "  "
        print(f"  {c:<8}{px:>9}{chg:>9}  {str(q['date']):<12}"
              f"{mt.strftime('%m-%d %H:%M:%S'):<22}{n:>6}  {flag}")
    print(f"\n  今天被改写的行情文件：{changed_today}/{len(codes)}"
          f"　{'✅ 说明今天确实跑过数据更新' if changed_today else '⚠️ 今天没有任何行情文件被改写 → 今天还没更新过数据'}")


def section_history(codes: list[str], days: int) -> None:
    _head(4, "价格真的变了吗（最硬证据：按日追加的价格序列）")
    print("  data/market/{code}_quote.parquet 是**逐日追加**的（data/raw 那份是单行覆盖，")
    print("  只能看到「现在」，看不到「变过」）。价格一列变化即为真更新。\n")
    for c in codes:
        h = _history(c)
        if h is None or h.empty:
            print(f"  {c}  （无归档，未被 snapshot 写过）")
            continue
        tail = h.tail(days)
        parts = [f"{r['report_date'].strftime('%m-%d')} {float(r['price']):.2f}"
                 for _, r in tail.iterrows()]
        if not parts:
            print(f"  {c}  （空）")
            continue
        seq = "  |  ".join(parts[:-1]) + f"  |  {parts[-1]} ← 最新"
        print(f"  {c}  {seq}")


def section_reports(codes: list[str]) -> None:
    _head(5, "报告跟着重刷了吗")
    for c in codes:
        cands = [p for p in REPORTS_DIR.glob(f"*/{c}.html")]
        if not cands:
            print(f"  {c}  无报告")
            continue
        rep = max(cands, key=lambda p: p.stat().st_mtime)
        d = RAW_DIR / c
        newest_raw = max((f.stat().st_mtime for f in d.glob("*.parquet")), default=0.0)
        fresh = rep.stat().st_mtime >= newest_raw
        print(f"  {c}  {rep.relative_to(ROOT)}  {_ts(rep)}  "
              f"{'✅ 不旧于数据' if fresh else '⚠️ 比数据旧（下次日更会重刷）'}")


def section_web() -> None:
    _head(6, "网页产物刷新了吗")
    for name in ("index.html", "watchlist.html"):
        p = WEB_DIR / name
        print(f"  web/{name:<16} {_ts(p)}")
    print("\n  打开方式：把上面两个文件拖进浏览器，或起服务")
    print("    python -m uvicorn web.server:app --host 127.0.0.1 --port 8000")


def main() -> int:
    ap = argparse.ArgumentParser(description="数据更新证据链自检")
    ap.add_argument("--codes", help="逗号分隔；缺省=全跟踪池")
    ap.add_argument("--days", type=int, default=6, help="历史序列显示最近 N 个归档日")
    a = ap.parse_args()

    codes = ([wl.bare(x) for x in a.codes.split(",") if x.strip()]
             if a.codes else wl.codes())
    if not codes:
        print("❌ 跟踪池为空")
        return 1

    print("=" * W)
    print(f"  数据更新证据链  ·  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  跟踪池 {len(codes)} 只：{'、'.join(codes)}")
    print("=" * W)

    registered = section_sched()
    section_log()
    section_files(codes)
    section_history(codes, a.days)
    section_reports(codes)
    section_web()

    print(f"\n{'=' * W}")
    print("  一句话判据：")
    print("   · 【3】里有 ✅ = 今天的行情文件被改写过 → 数据更新**确实发生了**")
    print("   · 【4】里价格跟前一天不同 = 更新带来的是**新数据**，不是原地重写")
    print("   · 【1】闸门有今天日期 = 整批**全部成功**（任一项失败都不会写闸门）")
    if not registered:
        print("   ⚠️ 但作业未注册，以上痕迹只能来自手动运行")
    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
