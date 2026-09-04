# -*- coding: utf-8 -*-
"""单元测试：quality 数据质量校验（空表 / 行数下限 / 正数列 / 会计勾稽）。"""
import pandas as pd
import pytest

from src.data.quality import CheckResult, check_frame, check_quote, validate_all


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


# --------------------------------------------------------------------------- #
# 行情快照校验（check_quote）
# --------------------------------------------------------------------------- #
def _quote(**kw):
    """构造单行行情快照，默认值均合理。"""
    base = {
        "name": "X",
        "price": 40.0,
        "pe": 15.0,
        "pb": 2.0,
        "market_cap": 8000.0,
        "price_52w_high": 50.0,
        "price_52w_low": 30.0,
        "symbol": "601088",
    }
    base.update(kw)
    return pd.DataFrame([base])


def test_check_quote_ok_on_normal():
    assert check_quote(_quote(), "601088").ok


def test_check_quote_empty_fails():
    assert not check_quote(pd.DataFrame(), "601088").ok


def test_check_quote_catches_price_outside_52w_range():
    r = check_quote(_quote(price=80.0), "601088")  # 超出 52 周高 50 的 ±5%
    assert not r.ok
    assert any("price_in_52w_range" in c["check"] and not c["ok"] for c in r.checks)


def test_check_quote_catches_dirty_pe():
    r = check_quote(_quote(pe=-1.0), "601088")  # 接口常见脏值
    assert not r.ok
    assert any(c["check"] == "range:pe" and not c["ok"] for c in r.checks)


def test_check_quote_skips_missing_hk_pe():
    """港股可能无 PE，缺值时跳过估值断言，不误报。"""
    q = _quote(pe=None)
    # price/pb 合理、pe 缺失 → 整体仍应 ok（pe 缺失是合法情况，非脏值）
    assert check_quote(q, "09992").ok
