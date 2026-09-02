# -*- coding: utf-8 -*-
"""单元测试：cleaner 清洗层（银行股兼容 / 净利率兜底 / 单位换算 / 派生指标）。"""
import pandas as pd
import pytest

from src.data.cleaner import (
    build_annual_financials,
    build_quarter_financials,
    calc_gross_margin,
    _with_interest_debt,
    _to_yi,
)


def _mk(df: pd.DataFrame, symbol: str = "600000") -> pd.DataFrame:
    df = df.copy()
    df["symbol"] = symbol
    df["report_date"] = pd.to_datetime(df["report_date"])
    return df


# ---------------------------------------------------------------------------
# 银行股兼容：无营业成本时毛利率应为 NaN（而非误算）
# ---------------------------------------------------------------------------

def test_bank_no_operating_cost_gross_margin_nan():
    """银行股利润表无 operating_cost，毛利率应为 NaN（不适用），不抛异常。"""
    ps = _mk(pd.DataFrame({
        "report_date": ["2024-12-31", "2025-12-31"],
        "operating_revenue": [1.0e11, 1.1e11],   # 元
        "net_profit_parent": [2.0e10, 2.2e10],
        # 无 operating_cost 列
    }))
    out = calc_gross_margin(ps)
    # 无 operating_cost 时，calc_gross_margin 不新增 gross_margin_pct 列
    assert "gross_margin_pct" not in out.columns


def test_bank_quarter_gross_margin_nan():
    """季度表：银行无营业成本，单季毛利率应为 NaN。"""
    data = {
        "profit_sheet": _mk(pd.DataFrame({
            "report_date": ["2025-03-31", "2025-06-30"],
            "operating_revenue": [3.0e10, 6.2e10],
            "net_profit_parent": [8.0e9, 1.7e10],
        })),
        "cash_flow": _mk(pd.DataFrame({
            "report_date": ["2025-03-31", "2025-06-30"],
            "ocf": [1.0e10, 2.1e10],
        })),
        "balance_sheet": _mk(pd.DataFrame({
            "report_date": ["2025-03-31", "2025-06-30"],
            "total_assets": [2.0e12, 2.1e12],
            "total_liabilities": [1.8e12, 1.9e12],
            "total_equity": [2.0e11, 2.1e11],
        })),
    }
    q = build_quarter_financials(data)
    assert q["gross_margin_pct"].isna().all()


# ---------------------------------------------------------------------------
# 净利率兜底：东财 financial_indicator 对银行缺 net_margin_pct，逐行补算
# ---------------------------------------------------------------------------

def test_net_margin_fillna_for_bank():
    """financial_indicator 的 net_margin_pct 全 NaN 时，用 归母净利/营收 兜底补算。"""
    data = {
        "profit_sheet": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "operating_revenue": [1.0e11],
            "net_profit_parent": [2.0e10],
        })),
        "cash_flow": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "ocf": [5.0e10],
        })),
        "balance_sheet": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "total_assets": [2.0e12],
            "total_liabilities": [1.8e12],
            "total_equity": [2.0e11],
        })),
        "financial_indicator": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "net_margin_pct": [float("nan")],
        })),
    }
    annual = build_annual_financials(data)
    # 兜底后净利率 = 2.0e10 / 1.0e11 * 100 = 20%
    assert annual["net_margin_pct"].iloc[0] == pytest.approx(20.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 单位换算：元 → 亿元
# ---------------------------------------------------------------------------

def test_to_yi_converts_money_fields():
    df = _mk(pd.DataFrame({
        "report_date": ["2024-12-31"],
        "operating_revenue": [1.0e10],   # 100 亿元
        "total_assets": [2.0e11],        # 2000 亿元
        "share_capital": [1.0e9],        # 10 亿股
    }))
    out = _to_yi(df)
    assert out["operating_revenue"].iloc[0] == pytest.approx(100.0)
    assert out["total_assets"].iloc[0] == pytest.approx(2000.0)
    assert out["share_capital"].iloc[0] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 有息负债补算：长期借款 + 短期借款；商誉 NaN 填 0
# ---------------------------------------------------------------------------

def test_interest_bearing_debt_and_goodwill():
    bs = _mk(pd.DataFrame({
        "report_date": ["2024-12-31"],
        "long_term_loan": [3.0e10],
        "short_term_loan": [2.0e10],
        "goodwill": [float("nan")],
        "preferred_shares": [float("nan")],
    }))
    out = _with_interest_debt(bs)
    assert out["interest_bearing_debt"].iloc[0] == pytest.approx(5.0e10)
    assert out["goodwill"].iloc[0] == 0.0
    assert out["preferred_shares"].iloc[0] == 0.0


# ---------------------------------------------------------------------------
# 派生指标：营运资本 / 所得税率 / 留存收益占比
# ---------------------------------------------------------------------------

def test_derived_metrics():
    data = {
        "profit_sheet": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "operating_revenue": [1.0e11],
            "net_profit": [2.0e10],
            "net_profit_parent": [1.8e10],
            "income_tax": [2.0e9],
            "total_profit": [2.0e10],
        })),
        "cash_flow": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "ocf": [5.0e10],
        })),
        "balance_sheet": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "total_assets": [2.0e12],
            "total_liabilities": [1.8e12],
            "total_equity": [2.0e11],
            "total_equity_all": [2.2e11],
            "current_assets": [6.0e10],
            "current_liabilities": [3.0e10],
            "retained_profit": [1.2e11],
            "share_capital": [1.0e9],
        })),
        "financial_indicator": _mk(pd.DataFrame({
            "report_date": ["2024-12-31"],
            "net_margin_pct": [18.0],
        })),
    }
    annual = build_annual_financials(data)
    # 营运资本 = 600 - 300 = 300 亿元（current_assets 6.0e10=600亿，current_liabilities 3.0e10=300亿）
    assert annual["working_capital"].iloc[0] == pytest.approx(300.0)
    # 所得税率 = 2.0e9 / 2.0e10 * 100 = 10%
    assert annual["income_tax_rate"].iloc[0] == pytest.approx(10.0)
    # 留存收益/归母净资产 = 1.2e11 / 2.0e11 * 100 = 60%
    assert annual["retained_to_equity"].iloc[0] == pytest.approx(60.0)
