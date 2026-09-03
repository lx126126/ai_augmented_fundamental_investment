#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取单只股票真实财报 → 存 parquet（P1 最小闭环入口）。

用法：
    python scripts/fetch_stock.py 601088          # 拉神华（A 股）
    python scripts/fetch_stock.py 600036          # 拉招行（A 股）
    python scripts/fetch_stock.py 09992.HK        # 拉泡泡玛特（港股，财报人民币/市值港元）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.fetcher import fetch_all, fetch_all_hk, _hk_code
from src.data.storage import save_all


def _is_hk(code: str) -> bool:
    """判断是否港股标的（代码带 .HK 后缀，或 0 开头且 5 位）。"""
    c = str(code).upper()
    if c.endswith(".HK"):
        return True
    # 剥后缀后 0 开头的 5 位码（如 09992）视为港股
    bare = c.split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "601088"
    is_hk = _is_hk(code)

    # 港股剥后缀成 5 位码（09992.HK → 09992），作为 parquet 目录名
    store_code = _hk_code(code) if is_hk else code.zfill(6)

    print(f"拉取 {code} 财报数据（{'港股' if is_hk else 'A股'}）...")
    data = fetch_all_hk(code) if is_hk else fetch_all(code)
    paths = save_all(data, store_code)

    print("\n已入库文件:")
    for p in paths:
        print(f"  {p}")

    print("\n各表摘要:")
    for table, df in data.items():
        if "report_date" in df.columns:
            period = f"报告期 {df['report_date'].min().date()} ~ {df['report_date'].max().date()}"
        else:
            period = "（无报告期）"
        print(f"  {table}: {df.shape[0]} 行 × {df.shape[1]} 列, {period}")


if __name__ == "__main__":
    main()
