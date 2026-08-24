"""入库层：DataFrame → parquet 本地存储。

目录约定：data/raw/{symbol}/{table}.parquet（data/raw 已 gitignore，不入库）
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"
RAW_DIR = DATA_ROOT / "raw"


def save_parquet(df: pd.DataFrame, symbol: str, table: str) -> Path:
    """存单表到 data/raw/{symbol}/{table}.parquet，返回文件路径。"""
    out_dir = RAW_DIR / symbol
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{table}.parquet"
    df.to_parquet(path, index=False)
    return path


def save_all(data: dict[str, pd.DataFrame], symbol: str) -> list[Path]:
    """存全部表，返回路径列表。"""
    return [save_parquet(df, symbol, table) for table, df in data.items()]
