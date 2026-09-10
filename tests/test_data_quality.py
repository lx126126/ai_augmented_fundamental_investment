# -*- coding: utf-8 -*-
"""单元测试：quality 数据质量校验（空表 / 行数下限 / 正数列 / 会计勾稽）。"""
import pandas as pd
import pytest

from src.data.quality import (
    CheckResult,
    check_annual_sanity,
    check_frame,
    check_quote,
    validate_all,
)


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


# ---------------------------------------------------------------------------
# 业务勾稽体检（check_annual_sanity）
# ---------------------------------------------------------------------------
def _annual_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["symbol"] = "600000"
    return df


def test_sanity_flags_broken_accounting_identity():
    """资产 ≠ 负债 + 权益（用归母权益会导致虚增偏差）应被抓出。"""
    a = _annual_frame([
        {"report_date": "2024-12-31", "total_assets": 1000.0,
         "total_liabilities": 300.0, "total_equity_all": 400.0},  # 差 300 → 30%
    ])
    r = check_annual_sanity(a, "600000")
    failed = {c["check"] for c in r.checks if not c["ok"]}
    assert any("会计恒等式" in f for f in failed)


def test_sanity_passes_when_identity_holds():
    a = _annual_frame([
        {"report_date": "2024-12-31", "total_assets": 1000.0,
         "total_liabilities": 300.0, "total_equity_all": 700.0},
    ])
    r = check_annual_sanity(a, "600000")
    assert not any("会计恒等式" in c["check"] and not c["ok"] for c in r.checks)


def test_sanity_yoy_low_base_exempt():
    """小基数起飞（如泡泡玛特 2018 净利 +6243%）不应判为异常。

    若只看同比幅度，腾讯 2002、泡泡玛特 2018 这类真实高增长会被年年误报，
    检查就失去意义——故去年同期 < 序列峰值 20% 时豁免。
    """
    a = _annual_frame([
        {"report_date": "2017-12-31", "net_profit": 0.1, "net_profit_yoy_pct": None},
        {"report_date": "2018-12-31", "net_profit": 6.3, "net_profit_yoy_pct": 6243.0},  # 基数 0.1 << 峰值 6.3
    ])
    r = check_annual_sanity(a, "600000")
    assert not any("净利润同比" in c["check"] and not c["ok"] for c in r.checks)


def test_sanity_flags_yoy_spike_on_normal_base():
    """基数正常时的同比暴增应被抓出（多为单位/口径错误）。

    注意基数与峰值的关系：基数 200 / 峰值 900 = 22% > 低基数豁免线 15%，
    故不会误豁免；同比 350% > 300% 阈值 → 应判异常。
    """
    a = _annual_frame([
        {"report_date": "2023-12-31", "net_profit": 200.0, "net_profit_yoy_pct": 10.0},
        {"report_date": "2024-12-31", "net_profit": 900.0, "net_profit_yoy_pct": 350.0},
    ])
    r = check_annual_sanity(a, "600000")
    assert any("净利润同比" in c["check"] and not c["ok"] for c in r.checks)


def test_sanity_yoy_thresholds_are_self_consistent():
    """阈值自洽性：同比阈值换算出的基数占比必须 > 低基数豁免线，否则检查永远不触发。

    同比 >X% ⟹ 基数 < 峰值/(1+X/100)。若该值 ≤ _LOW_BASE_RATIO，所有超阈值同比
    都会被低基数豁免掉，检查就成了死代码（这是改阈值时最容易踩的坑）。
    """
    from src.data.quality import _LOW_BASE_RATIO, _MAX_ABS_YOY_PCT
    implied_base_ratio = 1.0 / (1.0 + _MAX_ABS_YOY_PCT / 100.0)
    assert implied_base_ratio > _LOW_BASE_RATIO, (
        f"同比阈值 {_MAX_ABS_YOY_PCT}% 对应基数占比仅 {implied_base_ratio:.1%}，"
        f"≤ 低基数豁免线 {_LOW_BASE_RATIO:.0%} → 同比检查永远不会触发"
    )
