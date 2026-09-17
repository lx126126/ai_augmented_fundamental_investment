#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""财报数据更新（季度频）—— 按需 / 观察池 / 全市场。

这是**季度数据更新**的入口（对应日度更新的 scripts/update_spot_all.py）。

为什么要按需而不是无脑全量
--------------------------
实测单只冷启动（2026-09-17）：

    A 股 fetch_all      63.5s / 12 张表 / 2386 行
    港股 fetch_all_hk    4.6s /  8 张表

全市场 8368 只串行 ≈ 100+ 小时。所以默认行为是**只补缺失的**，
全市场拉取是一个"跑一夜"的后台动作，而非常规命令。

🔴 与 DuckDB 数仓的关系（重要，别踩）
--------------------------------------
**本脚本只落 data/raw/*.parquet，默认不重建数仓。**

原因：`warehouse.load_raw_layer()` 把每张表**跨全部标的 pd.concat** 后再物化
（11 张表 × 8000 只），balance_sheet 一张就是 300 万行 × 87 列。本机 8GB 内存
撑不住，会 OOM。数仓定位是「**可供 SQL 分析的工作集**」，不是「全市场数据湖」。

所以分工是：
    data/raw/**          全市场原冷数据（parquet，按目录组织，报告链路直接读）
    data/warehouse/*.db  只放需要 SQL 分析/对比的标的（观察池 + 常查的）

报告生成链路（adapter → cleaner → build_valueline）**不经过 DuckDB**，
直接按目录读 parquet，因此全量数据与数仓扩容是解耦的。

用法
----
    python scripts/update_financials.py --scope missing          # 默认：只补缺表的标的
    python scripts/update_financials.py --scope watchlist        # 只更新观察池
    python scripts/update_financials.py --codes 600519,00700     # 指定标的
    python scripts/update_financials.py --scope all --workers 4  # 全市场（跑一夜）
    python scripts/update_financials.py --scope all --limit 50   # 试点
    python scripts/update_financials.py --scope missing --dry-run

退出码：0 = 成功；1 = 失败率 > 20% 或全失败。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.fetcher import fetch_all, fetch_all_hk, _hk_code  # noqa: E402
from src.data.storage import save_all, missing_tables  # noqa: E402
from src.data import market_index  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
WATCHLIST = ROOT / "watchlist" / "watchlist.json"
LOG_DIR = ROOT / "data" / "logs"


def is_hk(symbol: str) -> bool:
    """港股判定：剥后缀后 0 开头的 5 位码（与 adapter._norm_code 同口径）。"""
    bare = str(symbol).upper().split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def store_code(symbol: str) -> str:
    """目录名形态：A 股 6 位、港股 5 位。"""
    bare = str(symbol).upper().split(".")[0]
    return _hk_code(bare) if is_hk(bare) else bare.zfill(6)


def _load_watchlist() -> list[tuple[str, str]]:
    if not WATCHLIST.exists():
        return []
    obj = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    out = []
    for s in obj.get("stocks", []):
        code = str(s.get("code", "")).split(".")[0]
        if code:
            out.append((store_code(code), "HK" if is_hk(code) else "A"))
    return out


def _resolve_targets(scope: str, codes: list[str] | None) -> list[tuple[str, str]]:
    """把 scope 解析成 [(symbol, market)] 列表。"""
    if codes:
        return [(store_code(c), "HK" if is_hk(c) else "A") for c in codes]

    if scope == "watchlist":
        return _load_watchlist()

    idx = market_index.load_index()
    items = list(zip(idx["symbol"].tolist(), idx["market"].tolist()))

    if scope == "all":
        return items

    # scope == "missing"：只要「有目录但缺表」或「完全没有」的标的
    out = []
    for sym, mkt in items:
        d = RAW_DIR / sym
        if not d.exists() or not any(d.glob("*.parquet")):
            out.append((sym, mkt))
            continue
        if mkt == "HK":
            if missing_tables(sym, ["profit_sheet", "balance_sheet", "cash_flow"]):
                out.append((sym, mkt))
        else:
            if missing_tables(sym, ["profit_sheet", "balance_sheet", "cash_flow", "dividend"]):
                out.append((sym, mkt))
    return out


def update_one(symbol: str, market: str) -> dict:
    """拉取并落盘单只标的的全部表。返回结果摘要（绝不抛出，失败也要有记录）。"""
    t0 = time.time()
    try:
        if market == "HK":
            data = fetch_all_hk(symbol)
        else:
            data = fetch_all(symbol)
        if not data:
            return {"symbol": symbol, "market": market, "ok": False,
                    "err": "空返回", "elapsed": round(time.time() - t0, 1)}
        save_all(data, symbol)
        rows = sum(0 if v is None else len(v) for v in data.values())
        return {"symbol": symbol, "market": market, "ok": True, "err": "",
                "tables": len(data), "rows": rows, "elapsed": round(time.time() - t0, 1)}
    except Exception as e:
        return {"symbol": symbol, "market": market, "ok": False,
                "err": f"{type(e).__name__}: {e}"[:160], "elapsed": round(time.time() - t0, 1)}


def run(targets: list[tuple[str, str]], workers: int, skip_done: bool) -> dict:
    if skip_done:
        before = len(targets)
        keep = []
        for sym, mkt in targets:
            need = ["profit_sheet", "balance_sheet", "cash_flow"] + ([] if mkt == "HK" else ["dividend"])
            if missing_tables(sym, need):
                keep.append((sym, mkt))
        targets = keep
        print(f"[fin] --skip-done：{before} 只中 {before - len(targets)} 只已完整，剩 {len(targets)} 只")

    if not targets:
        print("[fin] 没有需要更新的标的")
        return {"total": 0, "ok": 0, "failed": 0, "fail_rate": 0.0, "elapsed_s": 0.0}

    print(f"[fin] 待更新 {len(targets)} 只（workers={workers}）…")
    started = time.time()
    results: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(update_one, s, m): s for s, m in targets}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            if done % 20 == 0 or not r["ok"]:
                ok_n = sum(1 for x in results if x["ok"])
                print(f"  [{done}/{len(targets)}] ok={ok_n} last={r['symbol']}"
                      f" {'✓' if r['ok'] else '✗ ' + r['err']}", flush=True)

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    elapsed = time.time() - started
    summary = {
        "total": len(targets),
        "ok": len(ok),
        "failed": len(bad),
        "fail_rate": round(len(bad) / len(targets), 4),
        "elapsed_s": round(elapsed, 1),
        "avg_s": round(elapsed / max(len(targets), 1), 1),
        "sample_failed": [{"symbol": r["symbol"], "err": r["err"]} for r in bad[:15]],
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(f"[fin] 完成：成功 {summary['ok']} / 失败 {summary['failed']}"
          f"（{summary['fail_rate']:.1%}），总耗时 {summary['elapsed_s']}s，均 {summary['avg_s']}s/只")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="财报数据更新（季度频）")
    ap.add_argument("--scope", default="missing", choices=["missing", "watchlist", "all"],
                    help="missing=只补缺（默认）/ watchlist=观察池 / all=全市场")
    ap.add_argument("--codes", help="指定标的，逗号分隔（如 600519,00700）")
    ap.add_argument("--workers", type=int, default=3, help="并发数（默认 3；过大易被数据源限流）")
    ap.add_argument("--limit", type=int, help="只处理前 N 只（试点）")
    ap.add_argument("--skip-done", action="store_true", help="跳过三表齐全的标的")
    ap.add_argument("--dry-run", action="store_true", help="只列计划，不拉取")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else None
    targets = _resolve_targets(args.scope, codes)
    if args.limit:
        targets = targets[:args.limit]

    if args.dry_run:
        print(f"[dry-run] scope={args.scope} 目标 {len(targets)} 只：")
        for s, m in targets[:20]:
            print(f"  {s} ({m})")
        if len(targets) > 20:
            print(f"  … 其余 {len(targets) - 20} 只")
        est = len(targets) * (4.6 if all(m == "HK" for _, m in targets) else 63.5)
        print(f"[dry-run] 串行预估耗时约 {est / 3600:.1f} 小时（workers={args.workers} 时可缩短）")
        return 0

    summary = run(targets, workers=args.workers, skip_done=args.skip_done)

    if summary["total"]:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG_DIR / f"financials_{datetime.now().strftime('%Y-%m-%d')}.json"
        log.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[fin] 摘要已写 {log}")

    return 1 if summary["fail_rate"] > 0.2 else 0


if __name__ == "__main__":
    raise SystemExit(main())
