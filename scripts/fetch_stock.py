#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取单只股票真实财报 → 存 parquet（P1 最小闭环入口）。

用法：
    python scripts/fetch_stock.py 601088          # 拉神华
    python scripts/fetch_stock.py 600036          # 拉招行
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.fetcher import fetch_all
from src.data.storage import save_all


def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else "601088"
    print(f"拉取 {code} 财报数据 ...")
    data = fetch_all(code)
    paths = save_all(data, code)

    print("\n已入库文件:")
    for p in paths:
        print(f"  {p}")

    print("\n各表摘要:")
    for table, df in data.items():
        print(f"  {table}: {df.shape[0]} 行 × {df.shape[1]} 列, "
              f"报告期 {df['report_date'].min().date()} ~ {df['report_date'].max().date()}")


if __name__ == "__main__":
    main()
