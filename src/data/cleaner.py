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
    "operating_revenue", "operating_cost", "net_profit_parent", "ocf",
    "total_assets", "total_liabilities", "total_equity",
    "monetary_funds", "inventory", "accounts_receivable",
    "borrowings", "goodwill", "interest_bearing_debt",
    "long_term_loan", "short_term_loan",
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


def _with_interest_debt(bs_df: pd.DataFrame) -> pd.DataFrame:
    """补算有息负债（长期借款 + 短期借款），商誉 NaN 填 0。

    东财资产负债表的 BORROW_FUND 字段对部分公司为空，而有息负债的
    核心是长期借款 + 短期借款，故补算 interest_bearing_debt 字段。
    """
    df = bs_df.copy()
    loan_cols = [c for c in ("long_term_loan", "short_term_loan") if c in df.columns]
    if loan_cols:
        df["interest_bearing_debt"] = df[loan_cols].sum(axis=1, min_count=1)
    if "goodwill" in df.columns:
        df["goodwill"] = df["goodwill"].fillna(0.0)
    return df


def _to_yi(df: pd.DataFrame) -> pd.DataFrame:
    """金额字段由元换算为亿元。"""
    df = df.copy()
    for col in _MONEY_FIELDS.intersection(df.columns):
        df[col] = df[col] / 1e8
    return df


def _annual_dividend(dv: pd.DataFrame) -> pd.DataFrame:
    """分红按年度汇总（同一年多次分红加总），report_date 归一到 12-31。"""
    df = dv.copy()
    df["year"] = df["report_date"].dt.year
    agg = df.groupby(["symbol", "year"], as_index=False).agg(
        dividend_per_10=("dividend_per_10", "sum"),          # 年内多次分红加总
        dividend_yield_pct=("dividend_yield_pct", "sum"),    # 年内累计股息率
        total_shares=("total_shares", "last"),               # 取最新股本
    )
    agg["report_date"] = pd.to_datetime(agg["year"].astype(str) + "-12-31")
    return agg


def build_annual_financials(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """合并多表，输出对齐模板的年度财务数据（宽表，金额单位亿元）。"""
    ps = _annual(_to_yi(calc_gross_margin(data["profit_sheet"])))
    cf = _annual(_to_yi(data["cash_flow"]))
    bs = _annual(_to_yi(_with_interest_debt(data["balance_sheet"])))
    fi = _annual(data["financial_indicator"])

    key = ["symbol", "report_date"]

    ps_cols = key + [c for c in ["operating_revenue", "net_profit_parent", "gross_margin_pct"] if c in ps.columns]
    cf_cols = key + [c for c in ["ocf"] if c in cf.columns]
    bs_cols = key + [c for c in ["total_assets", "total_liabilities", "total_equity",
                                 "monetary_funds", "inventory", "accounts_receivable",
                                 "interest_bearing_debt", "goodwill"] if c in bs.columns]
    fi_cols = key + [c for c in ["net_margin_pct", "roe_pct", "roe_weighted_pct",
                                 "debt_ratio_pct", "revenue_yoy_pct", "net_profit_yoy_pct",
                                 "ocf_to_profit_pct", "current_ratio", "quick_ratio"] if c in fi.columns]

    merged = ps[ps_cols]
    merged = merged.merge(cf[cf_cols], on=key, how="left")
    merged = merged.merge(bs[bs_cols], on=key, how="left")
    merged = merged.merge(fi[fi_cols], on=key, how="left")

    # 分红数据：每股派息、股息率、总股本（普通股数量）
    if "dividend" in data:
        dv = _annual_dividend(data["dividend"])
        dv_cols = key + [c for c in ["dividend_per_10", "dividend_yield_pct", "total_shares"] if c in dv.columns]
        merged = merged.merge(dv[dv_cols], on=key, how="left")

    # 分红比例（股利支付率）= 分红总额 / 归母净利润 × 100
    if "dividend_per_10" in merged.columns and "total_shares" in merged.columns:
        merged["dividend_total"] = merged["dividend_per_10"] / 10 * merged["total_shares"] / 1e8  # 分红总额(亿元)
    if "dividend_total" in merged.columns and "net_profit_parent" in merged.columns:
        merged["dividend_payout_pct"] = merged["dividend_total"] / merged["net_profit_parent"] * 100

    return merged


def _to_single(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """累计值差分得到单季度流量值；Q1（03-31）无上期累计，直接取累计值。"""
    df = df.copy()
    for c in cols:
        s = df[c].astype(float)
        single = s.diff()
        is_q1 = df["report_date"].dt.month == 3
        single[is_q1] = s[is_q1]
        df[c] = single
    return df


def build_quarter_financials(data: dict[str, pd.DataFrame], n_quarters: int = 8) -> pd.DataFrame:
    """构建季度财务数据（近 N 季度，单季流量 + 季末时点，金额亿元）。

    口径：
    - 利润表 / 现金流表为「年初累计」，差分得到单季度流量，Q1 直接取累计
    - 资产负债表为季度末时点值，直接取
    - 单季毛利率 / 净利率 / ROE 由单季值重算
    """
    ps = data["profit_sheet"].sort_values("report_date").reset_index(drop=True)
    cf = data["cash_flow"].sort_values("report_date").reset_index(drop=True)
    bs = data["balance_sheet"].sort_values("report_date").reset_index(drop=True)

    ps = _to_single(ps, ["operating_revenue", "operating_cost", "net_profit_parent"])
    cf = _to_single(cf, ["ocf"])

    # 单季比率
    ps["gross_margin_pct"] = (ps["operating_revenue"] - ps["operating_cost"]) / ps["operating_revenue"] * 100
    ps["net_margin_pct"] = ps["net_profit_parent"] / ps["operating_revenue"] * 100

    bs = _with_interest_debt(bs)

    key = ["symbol", "report_date"]
    ps_cols = key + ["operating_revenue", "net_profit_parent", "gross_margin_pct", "net_margin_pct"]
    cf_cols = key + ["ocf"]
    bs_cols = key + ["total_assets", "total_liabilities", "total_equity",
                     "monetary_funds", "inventory", "accounts_receivable",
                     "interest_bearing_debt", "goodwill"]

    merged = ps[ps_cols].merge(cf[cf_cols], on=key, how="left")
    merged = merged.merge(bs[bs_cols], on=key, how="left")

    # 单季 ROE = 单季归母净利 / 季末归母净资产
    merged["roe_pct"] = merged["net_profit_parent"] / merged["total_equity"] * 100

    # 元 → 亿元
    merged = _to_yi(merged)

    merged = merged.sort_values("report_date").tail(n_quarters).reset_index(drop=True)
    return merged
