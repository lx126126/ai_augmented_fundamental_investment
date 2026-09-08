# -*- coding: utf-8 -*-
"""单元测试：adapter 修正应用（raw 保持原始 + 修正进 mart 的核心逻辑）。"""
import json

import pandas as pd

from src.data.adapter import _apply_corrections


def _write_log(tmp_path, code, year, items):
    """写一个临时 reconcile.json，返回 validation 目录路径。"""
    val = tmp_path / "validation"
    val.mkdir(exist_ok=True)
    (val / f"{code}_{year}_reconcile.json").write_text(
        json.dumps({"code": code, "year": year, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return val


def test_apply_corrections_overwrites_target_year(tmp_path):
    """修正应只覆盖对应年份的年报行，其他年份不变。"""
    val = _write_log(tmp_path, "999999", 2025, [
        {"table": "balance_sheet", "field": "total_assets",
         "pdf_yi": 6277.61, "diff_pct": 43.98},
    ])
    raw = {
        "balance_sheet": pd.DataFrame({
            "report_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
            "total_assets": [6680.2e8, 9038.3e8],
        }),
    }
    out = _apply_corrections(raw, "999999", val_dir=val)
    assert round(out["balance_sheet"].iloc[1]["total_assets"] / 1e8, 1) == 6277.6
    assert round(out["balance_sheet"].iloc[0]["total_assets"] / 1e8, 1) == 6680.2


def test_apply_corrections_skips_absurd_diff(tmp_path):
    """diff_pct 异常大（单位识别错误）的修正应被跳过，不污染数据。"""
    val = _write_log(tmp_path, "999999", 2025, [
        {"table": "cash_flow", "field": "capital_expenditure",
         "pdf_yi": 0.00043197, "diff_pct": 99999900.0},
    ])
    raw = {
        "cash_flow": pd.DataFrame({
            "report_date": pd.to_datetime(["2025-12-31"]),
            "capital_expenditure": [431.97e8],
        }),
    }
    out = _apply_corrections(raw, "999999", val_dir=val)
    assert round(out["cash_flow"].iloc[0]["capital_expenditure"] / 1e8, 2) == 431.97


def test_apply_corrections_no_log_returns_unchanged(tmp_path):
    """无修正记录时，返回数据应不变（但应为副本，不修改原 dict）。"""
    raw = {
        "balance_sheet": pd.DataFrame({
            "report_date": pd.to_datetime(["2025-12-31"]),
            "total_assets": [9038.3e8],
        }),
    }
    out = _apply_corrections(raw, "999999", val_dir=tmp_path / "nonexistent")
    assert out["balance_sheet"].iloc[0]["total_assets"] == 9038.3e8
    assert out is not raw  # 返回副本，不修改原 dict
