"""清洗层：统一排序、补算缺失指标、单位换算、构建对齐模板的财务数据。

职责：
- 东财三表是倒序（最新在前），统一为升序
- 财务指标接口的「毛利率」字段缺失，从利润表补算
- 金额单位由「元」统一换算为「亿元」（对齐模板口径）
"""
from __future__ import annotations

import pandas as pd

# 金额字段（元 → 亿元）
_MONEY_FIELDS = {
    "operating_revenue", "net_profit_parent", "ocf",
    "total_assets", "total_liabilities", "total_equity",
    "monetary_funds", "inventory", "accounts_receivable",
    "borrowings", "goodwill",
}


def _annual(df: pd.DataFrame) -> pd.DataFrame:
    """筛选年报（12-31），按 report_date 升序。"""
    df = df[df["report_date"].dt.month == 12]
    return df.sort_values("report_date").reset_index(drop=True)


def calc_gross_margin(profit_df: pd.DataFrame) -> pd.DataFrame:
    """从利润表补算毛利率（%）：(营业收入 - 营业成本) / 营业收入 × 100。"""
    df = profit_df.copy()
    if {"operating_revenue", "operating_cost"}.issubset(df.columns):
        df["gross_margin_pct"] = (
            (df["operating_revenue"] - df["operating_cost"])
            / df["operating_revenue"] * 100
        )
    return df


def _to_yi(df: pd.DataFrame) -> pd.DataFrame:
    """金额字段由元换算为亿元。"""
    df = df.copy()
    for col in _MONEY_FIELDS.intersection(df.columns):
        df[col] = df[col] / 1e8
    return df


def build_annual_financials(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """合并四表，输出对齐模板的年度财务数据（宽表，金额单位亿元）。"""
    ps = _annual(_to_yi(calc_gross_margin(data["profit_sheet"])))
    cf = _annual(_to_yi(data["cash_flow"]))
    bs = _annual(_to_yi(data["balance_sheet"]))
    fi = _annual(data["financial_indicator"])

    key = ["symbol", "report_date"]

    ps_cols = key + [c for c in ["operating_revenue", "net_profit_parent", "gross_margin_pct"] if c in ps.columns]
    cf_cols = key + [c for c in ["ocf"] if c in cf.columns]
    bs_cols = key + [c for c in ["total_assets", "total_liabilities", "total_equity",
                                 "monetary_funds", "inventory", "accounts_receivable",
                                 "borrowings", "goodwill"] if c in bs.columns]
    fi_cols = key + [c for c in ["net_margin_pct", "roe_pct", "roe_weighted_pct",
                                 "debt_ratio_pct", "revenue_yoy_pct", "net_profit_yoy_pct",
                                 "ocf_to_profit_pct", "current_ratio", "quick_ratio"] if c in fi.columns]

    merged = ps[ps_cols]
    merged = merged.merge(cf[cf_cols], on=key, how="left")
    merged = merged.merge(bs[bs_cols], on=key, how="left")
    merged = merged.merge(fi[fi_cols], on=key, how="left")

    return merged
