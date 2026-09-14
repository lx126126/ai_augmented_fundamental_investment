#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取单只股票真实财报 → 存 parquet（P1 最小闭环入口）。

用法：
    python scripts/fetch_stock.py 601088          # 拉神华（A 股）
    python scripts/fetch_stock.py 600036          # 拉招行（A 股）
    python scripts/fetch_stock.py 09992.HK        # 拉泡泡玛特（港股，财报人民币/市值港元）
    python scripts/fetch_stock.py 601088 --incremental   # 断点续传：跳过已成功落盘的表
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.fetcher import fetch_all, fetch_all_hk, _hk_code
from src.data.storage import save_all, missing_tables


def _period_summary(df: pd.DataFrame) -> str:
    """摘要用的报告期区间。

    ⚠️ 两个坑：①quote / rating 这类表虽然有 report_date 列，但整列是 NaN，
    `.min()` 会返回 float('nan') 而不是 Timestamp，直接 .date() 抛
    AttributeError（港股抓 00883 时就是这样崩在最后一步，数据其实已落盘）；
    ②该列可能混着 str / Timestamp / NaT，必须先统一 coerce 再 dropna 判空。
    """
    if "report_date" not in df.columns:
        return "（无报告期）"
    rd = pd.to_datetime(df["report_date"], errors="coerce").dropna()
    if rd.empty:
        return "（无报告期）"
    return f"报告期 {rd.min().date()} ~ {rd.max().date()}"


def _is_hk(code: str) -> bool:
    """判断是否港股标的（代码带 .HK 后缀，或 0 开头且 5 位）。"""
    c = str(code).upper()
    if c.endswith(".HK"):
        return True
    # 剥后缀后 0 开头的 5 位码（如 09992）视为港股
    bare = c.split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def main() -> None:
    args = sys.argv[1:]
    incremental = "--incremental" in args
    args = [a for a in args if a != "--incremental"]
    code = args[0] if args else "601088"
    is_hk = _is_hk(code)

    # 港股剥后缀成 5 位码（09992.HK → 09992），作为 parquet 目录名
    store_code = _hk_code(code) if is_hk else code.zfill(6)

    print(f"拉取 {code} 财报数据（{'港股' if is_hk else 'A股'}）...")

    if is_hk:
        data = fetch_all_hk(code)
    else:
        data = fetch_all(code)

    if incremental:
        # 断点续传：只保留「缺失或为空」的表，跳过已成功落盘的表
        missing = missing_tables(store_code, list(data.keys()))
        if not missing:
            print("✅ 所有表均已成功落盘，无需重新拉取（--incremental 跳过）")
            return
        skipped = [t for t in data if t not in missing]
        if skipped:
            print(f"⏭  跳过已存在的表: {', '.join(skipped)}")
        data = {t: data[t] for t in missing}

    paths = save_all(data, store_code)

    print("\n已入库文件:")
    for p in paths:
        print(f"  {p}")

    print("\n各表摘要:")
    for table, df in data.items():
        print(f"  {table}: {df.shape[0]} 行 × {df.shape[1]} 列, {_period_summary(df)}")


if __name__ == "__main__":
    main()
