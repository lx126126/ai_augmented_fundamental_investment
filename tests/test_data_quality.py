# -*- coding: utf-8 -*-
"""单元测试：quality 数据质量校验（空表 / 行数下限 / 正数列 / 会计勾稽）。"""
import pandas as pd
import pytest

from src.data.quality import CheckResult, check_frame, validate_all


def _bs(total_assets=100.0, total_liabilities=60.0, total_equity=40.0):
    """构造一张最小资产负债表（含少数股东权益列）。"""
    return pd.DataFrame(
        {
            "report_date": pd.to_datetime(["2025-12-31"] * 5),
            "total_assets": [total_assets] * 5,
            "total_liabilities": [total_liabilities] * 5,
            "total_equity": [total_equity * 0.8] * 5,  # 归母（故意小于全部）
            "total_equity_all": [total_equity] * 5,   # 含少数股东 = 全部
        }
    )


def test_empty_frame_fails():
    r = check_frame(pd.DataFrame(), "t", min_rows=1)
    assert not r.ok
    assert r.failed >= 1


def test_min_rows_fails():
    df = pd.DataFrame({"report_date": [1, 2]})
    r = check_frame(df, "t", min_rows=5)
    assert not r.ok
    assert any(c["check"] == "min_rows" and not c["ok"] for c in r.checks)


def test_positive_col_fails_on_negative():
    df = pd.DataFrame({"revenue": [100, -5, 200]})
    r = check_frame(df, "t", positive_cols=["revenue"])
    assert not r.ok
    assert any(c["check"] == "positive:revenue" and not c["ok"] for c in r.checks)


def test_balance_identity_uses_equity_all():
    """会计恒等式必须用 total_equity_all（含少数股东），归母权益会漏算。"""
    # 归母 32 + 负债 60 = 92 ≠ 资产 100（漏了少数股东 8）
    # 全部权益 40 + 负债 60 = 100 = 资产 100 ✅
    raw = {"balance_sheet": _bs(total_assets=100, total_liabilities=60, total_equity=40)}
    res = validate_all(raw)
    assert res.ok, res.summary()


def test_validate_all_catches_broken_balance():
    """会计恒等式真被破坏时（资产 ≠ 负债+权益），应判定失败。"""
    raw = {"balance_sheet": _bs(total_assets=100, total_liabilities=60, total_equity=30)}
    res = validate_all(raw)
    assert not res.ok
    assert any("balance_identity" in c["check"] and not c["ok"] for c in res.checks)


def test_bank_profit_sheet_falls_back_to_operating_revenue():
    """银行利润表无 revenue 列，应回退到 operating_revenue 且不误报。"""
    pdf = pd.DataFrame(
        {
            "report_date": pd.to_datetime(["2025-12-31"] * 5),
            "operating_revenue": [200, 210, 220, 230, 240],
            "net_profit": [50, 52, 54, 56, 58],
        }
    )
    raw = {"profit_sheet": pdf}
    res = validate_all(raw)
    assert res.ok, res.summary()


def test_check_result_summary_lists_failures():
    r = CheckResult(table="t")
    r.add("a", True, "ok")
    r.add("b", False, "坏")
    assert r.failed == 1
    assert "✗ b" in r.summary()
