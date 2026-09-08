# -*- coding: utf-8 -*-
"""单元测试：storage 断点续传（missing_tables 判定缺失/空表）。"""
import pandas as pd

from src.data.storage import RAW_DIR, missing_tables, save_parquet


def test_missing_tables_all_missing(tmp_path, monkeypatch):
    """目录不存在时，所有表都应判为缺失。"""
    monkeypatch.setattr("src.data.storage.RAW_DIR", tmp_path)
    tables = ["profit_sheet", "balance_sheet", "cash_flow"]
    assert missing_tables("999999", tables) == tables


def test_missing_tables_skips_existing_nonempty(tmp_path, monkeypatch):
    """已存在且非空的表应被跳过，只返回缺失的表。"""
    monkeypatch.setattr("src.data.storage.RAW_DIR", tmp_path)
    save_parquet(pd.DataFrame({"a": [1, 2]}), "999999", "profit_sheet")
    tables = ["profit_sheet", "balance_sheet"]
    assert missing_tables("999999", tables) == ["balance_sheet"]


def test_missing_tables_empty_file_treated_as_missing(tmp_path, monkeypatch):
    """文件存在但 DataFrame 为空（0 行）应判为缺失（上次拉取失败残留）。"""
    monkeypatch.setattr("src.data.storage.RAW_DIR", tmp_path)
    save_parquet(pd.DataFrame(), "999999", "cash_flow")
    assert missing_tables("999999", ["cash_flow"]) == ["cash_flow"]
