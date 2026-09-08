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


def missing_tables(symbol: str, tables: list[str]) -> list[str]:
    """返回指定 symbol 下「缺失或为空的表」列表，供断点续传跳过已成功的表。

    幂等续传：拉取中途失败时，已成功落盘的表下次可跳过，只补拉缺失的表。
    判定标准：文件不存在，或文件存在但读出的 DataFrame 为空（0 行）。
    """
    missing = []
    for table in tables:
        p = RAW_DIR / symbol / f"{table}.parquet"
        if not p.exists():
            missing.append(table)
            continue
        try:
            if pd.read_parquet(p).empty:
                missing.append(table)
        except Exception:
            missing.append(table)
    return missing
