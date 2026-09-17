#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场行情日更：一次刷完 A 股 + 港股全部标的的行情快照。

这是**日度数据更新**的入口（对应季度更新的 scripts/update_financials.py）。

与现有 daily_refresh.py 的分工
------------------------------
    daily_refresh.py        —— 只刷**跟踪池那几只**，并重刷它们的报告（含估值板块）
    本脚本（update_spot_all）—— 刷**全市场 8368 只**的行情，**不生成报告**

为什么要分开：行情是「全市场都需要」的公共底料（做 PE/PB 分位、横向比较、
选股扫描都要），而报告生成是「单标的、昂贵（首次约 1 分钟 + LLM）」的操作。
两者频率与成本差一个量级，混在一个任务里会让日更从 1 分钟变成几小时。

产物
----
    data/market/spot_latest.parquet          全市场最新快照（覆盖写）
    data/market/spot_history/spot_YYYY-MM-DD.parquet   当日归档（**追加**，不覆盖）

为什么同时要两份：latest 供查询/展示（永远是最新），history 供回溯
（「这只票上个月的 PE 是多少」——覆盖写会把这部分历史抹掉）。
这也是对 raw/quote 覆盖式快照缺陷的补丁。

行情源：腾讯 qt.gtimg.cn（批量 50 只/请求）。**不用东财 push2** ——
实测该域名在受限网络下会被链路重置，而腾讯这条路稳定且支持批量。

用法
----
    python scripts/update_spot_all.py                  # 全市场（约 1-3 分钟）
    python scripts/update_spot_all.py --market HK      # 只刷港股
    python scripts/update_spot_all.py --limit 200      # 试点：只刷前 200 只
    python scripts/update_spot_all.py --workers 4      # 并发度（默认 4）
    python scripts/update_spot_all.py --no-history     # 不写当日归档

退出码：0 = 成功；1 = 失败率过高（>20%，说明数据源或网络有问题）。
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.fetcher import fetch_quotes_batch  # noqa: E402
from src.data import market_index  # noqa: E402

MARKET_DIR = ROOT / "data" / "market"
LATEST_PATH = MARKET_DIR / "spot_latest.parquet"
HISTORY_DIR = MARKET_DIR / "spot_history"

#: 每个并发任务负责多少只（内部还会按 50 只再分批）
_CHUNK = 200


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def update_all(
    market: str | None = None,
    limit: int | None = None,
    workers: int = 4,
    write_history: bool = True,
) -> dict:
    """刷全市场行情快照，返回统计摘要。"""
    idx = market_index.load_index()
    if market:
        idx = idx[idx["market"].str.upper() == market.upper()]
    if limit:
        idx = idx.head(limit)

    items = list(zip(idx["symbol"].tolist(), idx["market"].tolist()))
    if not items:
        raise RuntimeError("索引为空 —— 请先运行 python scripts/build_market_index.py")

    print(f"[spot] 待刷 {len(items)} 只（workers={workers}）…")
    started = datetime.now()

    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_quotes_batch, c) for c in _chunks(items, _CHUNK)]
        done = 0
        for fut in futures:
            df, bad = fut.result()
            if not df.empty:
                frames.append(df)
            failed.extend(bad)
            done += 1
            n = sum(len(f) for f in frames)
            print(f"  进度 {done}/{len(futures)} 批，累计成功 {n} 只，失败 {len(failed)} 只", flush=True)

    if not frames:
        raise RuntimeError("全部标的都拉取失败 —— 检查网络出口（必要时设 FQF_HTTP_PROXY）")

    out = pd.concat(frames, ignore_index=True)
    out["snapshot_date"] = datetime.now().strftime("%Y-%m-%d")
    out["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 列顺序：标识在前，行情在中，时间戳收尾
    head = ["symbol", "market", "name", "price", "change", "change_pct", "pe", "pb", "market_cap"]
    cols = [c for c in head if c in out.columns] + [c for c in out.columns if c not in head]
    out = out[cols].sort_values(["market", "symbol"]).reset_index(drop=True)

    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(LATEST_PATH, index=False)
    print(f"[spot] latest 已写 {LATEST_PATH}（{len(out)} 行）")

    if write_history:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        hist_path = HISTORY_DIR / f"spot_{out['snapshot_date'].iloc[0]}.parquet"
        out.to_parquet(hist_path, index=False)
        print(f"[spot] 当日归档已写 {hist_path}")

    elapsed = (datetime.now() - started).total_seconds()
    n_total = len(items)
    summary = {
        "total": n_total,
        "ok": len(out),
        "failed": len(failed),
        "fail_rate": round(len(failed) / n_total, 4) if n_total else 0.0,
        "elapsed_s": round(elapsed, 1),
        "latest": str(LATEST_PATH),
        "sample_failed": failed[:10],
    }
    print(f"[spot] 完成：成功 {summary['ok']} / 失败 {summary['failed']}"
          f"（{summary['fail_rate']:.1%}），耗时 {summary['elapsed_s']}s")
    if failed[:5]:
        print(f"[spot] 失败样例：{', '.join(failed[:5])}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="全市场行情日更")
    ap.add_argument("--market", choices=["A", "SH", "SZ", "BJ", "HK"], help="只刷某市场")
    ap.add_argument("--limit", type=int, help="只刷前 N 只（试点/调试）")
    ap.add_argument("--workers", type=int, default=4, help="并发批次数（默认 4，过大易被限流）")
    ap.add_argument("--no-history", action="store_true", help="不写当日归档")
    args = ap.parse_args()

    mk = None if args.market in (None, "A") else args.market
    try:
        s = update_all(market=mk, limit=args.limit, workers=args.workers,
                       write_history=not args.no_history)
    except Exception as e:
        print(f"✗ 失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 1 if s["fail_rate"] > 0.2 else 0


if __name__ == "__main__":
    raise SystemExit(main())
