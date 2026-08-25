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


# 业务条线名清理（通用，不写死行业）：去噪，排除内部抵销/未分配项
def _clean_segment_name(name: str) -> str | None:
    """清理业务条线名，返回 None 表示排除（内部抵销/未分配项）。"""
    name = str(name).strip()
    if any(k in name for k in ("抵销", "未分配")):
        return None
    name = name.replace("收入", "").replace("(补充)", "").strip()
    return name or None


def _period_label(dt) -> str:
    """报告期标签（通用，覆盖季度/半年度）：03-31→Q1，06-30→中报，09-30→三季报，12-31→年报。"""
    y = str(dt.year)[2:]
    m = dt.month
    if m == 3:
        return f"{y}Q1"
    if m == 6:
        return f"{y}中报"
    if m == 9:
        return f"{y}三季报"
    return f"{y}年报"


def build_segments(seg_df: pd.DataFrame, lookback_years: int = 2):
    """分业务收入构成（通用）：近 N 年 × 业务条线（按最新期收入降序）。

    频率自适应：数据源有季度披露就用季度，只有半年度就用半年度（按时间跨度取，
    而非固定期数）。返回 (period_labels, [(业务条线, [收入(亿)], [毛利率(%)])])。
    毛利率按收入加权平均（有值的行），全缺失则为 None。
    """
    cand = seg_df[seg_df["category_type"].isin(["按产品分类", "按行业分类"])].copy()
    cand["clean"] = cand["segment_name"].map(_clean_segment_name)
    cand = cand[cand["clean"].notna()]

    # 选业务条线更丰富的分类口径（按产品 vs 按行业）
    best_type, best_n = None, 0
    for ct in ("按产品分类", "按行业分类"):
        n = cand[cand["category_type"] == ct]["clean"].nunique()
        if n > best_n:
            best_n, best_type = n, ct
    df = cand[cand["category_type"] == best_type].copy()

    # 按时间跨度取近 N 年（频率自适应：季度/半年度）
    latest = df["report_date"].max()
    cutoff = latest - pd.DateOffset(years=lookback_years)
    df = df[df["report_date"] >= cutoff]
    periods = sorted(df["report_date"].unique())

    df["rev_yi"] = df["segment_revenue"] / 1e8
    df["margin_pct"] = df["segment_margin"] * 100

    period_labels = [_period_label(p) for p in periods]

    # 业务条线按最新期收入降序
    latest_period = periods[-1]
    seg_order = (
        df[df["report_date"] == latest_period]
        .groupby("clean")["rev_yi"].sum()
        .sort_values(ascending=False).index.tolist()
    )

    result = []
    for name in seg_order:
        sub = df[df["clean"] == name]
        revs, margins = [], []
        for p in periods:
            row = sub[sub["report_date"] == p]
            rev = row["rev_yi"].sum(min_count=1) if len(row) else None
            rev = None if (rev is not None and pd.isna(rev)) else rev
            valid = row.dropna(subset=["margin_pct"])
            if len(valid) and valid["rev_yi"].sum() > 0:
                margin = (valid["margin_pct"] * valid["rev_yi"]).sum() / valid["rev_yi"].sum()
            else:
                margin = None
            revs.append(rev)
            margins.append(margin)
        result.append((name, revs, margins))

    return period_labels, result


def build_valuation(valuation: pd.DataFrame, annual: pd.DataFrame) -> dict:
    """估值面板：PE/PB/股息率/52周股价区间/估值分位。

    valuation 为长表（indicator: market_cap/pb，value 为对应值）。
    - PB / 市值：百度估值直接给（近十年）
    - PE = 最新总市值 / 最新年报归母净利
    - 52周股价 = 近一年市值 min/max ÷ 总股本
    - 分位：PB 用 PB 序列；PE 用市值分位近似（净利短期稳定）
    """
    mcap = valuation[valuation["indicator"] == "market_cap"].sort_values("report_date")
    pb = valuation[valuation["indicator"] == "pb"].sort_values("report_date")
    if mcap.empty or pb.empty:
        return None

    pb_now = pb["value"].iloc[-1]
    mcap_now = mcap["value"].iloc[-1]  # 总市值（亿元）

    net_profit = annual["net_profit_parent"].iloc[-1]  # 最新年报归母净利（亿元）
    pe = mcap_now / net_profit if net_profit and net_profit > 0 else None

    dividend_yield = None
    if "dividend_yield_pct" in annual.columns:
        dividend_yield = annual["dividend_yield_pct"].iloc[-1]

    total_shares_yi = annual["total_shares"].iloc[-1] / 1e8 if "total_shares" in annual.columns else None

    # 52周股价区间（近一年市值 ÷ 总股本）
    one_year = mcap[mcap["report_date"] >= (mcap["report_date"].max() - pd.DateOffset(years=1))]
    price_low = price_now = price_high = None
    if total_shares_yi and not one_year.empty:
        price_low = one_year["value"].min() / total_shares_yi
        price_high = one_year["value"].max() / total_shares_yi
        price_now = mcap_now / total_shares_yi

    # 分位（当前值在历史序列中的百分位）
    pb_pctile = (pb["value"] < pb_now).mean() * 100
    pe_pctile = (mcap["value"] < mcap_now).mean() * 100

    return {
        "pe": pe,
        "pb": pb_now,
        "dividend_yield": dividend_yield,
        "price_low": price_low,
        "price_now": price_now,
        "price_high": price_high,
        "pe_pctile": pe_pctile,
        "pb_pctile": pb_pctile,
    }
