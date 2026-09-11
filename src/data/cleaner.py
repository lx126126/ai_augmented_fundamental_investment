"""清洗层：统一排序、补算缺失指标、单位换算、构建对齐模板的财务数据。

职责：
- 东财三表是倒序（最新在前），统一为升序
- 财务指标接口的「毛利率」字段缺失，从利润表补算
- 金额单位由「元」统一换算为「亿元」（对齐模板口径）
"""
from __future__ import annotations

import pandas as pd

# 金额字段（元 → 亿元；股本面值 1 元，故 share_capital 转后即「亿股」）
_MONEY_FIELDS = {
    # 利润表
    "revenue", "operating_revenue", "operating_cost", "total_operating_cost",
    "operate_tax_add", "operating_profit", "non_operating_income", "non_operating_expense",
    "total_profit", "income_tax", "net_profit", "net_profit_parent", "minority_interest",
    "deduct_net_profit", "sell_expense", "admin_expense", "research_expense",
    "finance_expense", "interest_expense", "invest_income",
    "asset_impairment_loss", "credit_impairment_loss",
    # 资产负债表
    "total_assets", "total_liabilities", "total_equity", "total_equity_all",
    "current_assets", "noncurrent_assets", "monetary_funds", "inventory", "accounts_receivable",
    "fixed_assets", "construction_in_progress", "intangible_assets", "long_equity_invest",
    "other_noncurrent_assets", "minority_equity", "borrowings", "goodwill",
    "interest_bearing_debt", "long_term_loan", "short_term_loan",
    "share_capital", "preferred_shares",
    "current_liabilities", "noncurrent_liabilities", "accounts_payable",
    "other_current_assets", "other_current_liabilities", "noncurrent_liab_1y",
    "retained_profit", "bond_payable", "long_payable", "lease_liabilities",
    "short_bond_payable", "long_term_debt", "total_debt",
    # 构成饼图子科目（流动资产/非流动资产/流动负债/非流动负债）
    "notes_receivable", "prepayments", "other_receivables", "trading_financial_assets",
    "contract_assets", "noncurrent_asset_1y", "dividend_receivable", "interest_receivable",
    "finance_receivables", "invest_realestate", "useright_asset", "long_prepaid_expense",
    "defer_tax_asset", "other_equity_invest", "other_noncurrent_finasset", "other_creditor_invest",
    "hold_maturity_invest", "notes_payable", "contract_liabilities", "staff_salary_payable",
    "tax_payable", "advance_receivables", "other_payables", "dividend_payable", "interest_payable",
    "other_noncurrent_liabilities", "defer_tax_liabilities", "long_staff_salary_payable",
    "perpetual_bond", "predict_liabilities",
    "lend_fund", "loan_advance", "buy_resale_finasset", "derivative_finasset",
    "settle_excess_reserve", "accept_deposit_interbank", "derivative_finliab",
    "sell_repo_finasset", "fee_commission_payable",
    "accept_deposit", "iofi_deposit", "cash_deposit_pbc", "deposit_interbank",
    "creditor_invest", "loan_pbc", "deposit_certificate", "precious_metal",
    "amortize_cost_finasset", "fvtoci_finasset", "other_asset",
    # 现金流量表
    "ocf", "icf", "financing_cash_flow",
    "operating_cash_inflow", "operating_cash_outflow",
    "investing_cash_inflow", "investing_cash_outflow",
    "financing_cash_inflow", "financing_cash_outflow",
    "depreciation", "capital_expenditure", "amortize_intangible", "amortize_lpe",
    "depre_invest_realestate", "depre_oilgas_bio", "amortize_useright",
}


def _annual(df: pd.DataFrame) -> pd.DataFrame:
    """筛选年报（12-31），按 report_date 升序。"""
    df = df[df["report_date"].dt.month == 12]
    return df.sort_values("report_date").reset_index(drop=True)


def calc_gross_margin(profit_df: pd.DataFrame) -> pd.DataFrame:
    """从利润表补算毛利率（%）：(营业收入 - 营业成本) / 营业收入 × 100。

    顺带补「营业总收入」口径：港股利润表无 revenue（营业总收入）字段，
    其「营业额」即总收入，fallback 到 operating_revenue（A 股有 revenue 列则不动）。
    """
    df = profit_df.copy()
    if "revenue" not in df.columns and "operating_revenue" in df.columns:
        df["revenue"] = df["operating_revenue"]
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

    ValueLine 口径（本函数同时补算）：
    - long_term_debt：长期借款 + 应付债券 + 长期应付款 + 租赁负债
    - total_debt：短期借款 + 一年内到期非流动负债 + 长期借款 + 应付债券
                  + 长期应付款 + 租赁负债 + 应付短期债券（完整有息负债）
    """
    df = bs_df.copy()
    loan_cols = [c for c in ("long_term_loan", "short_term_loan") if c in df.columns]
    if loan_cols:
        df["interest_bearing_debt"] = df[loan_cols].sum(axis=1, min_count=1)

    def _sum_cols(names):
        cols = [c for c in names if c in df.columns]
        return df[cols].sum(axis=1, min_count=1) if cols else None

    lt_cols = ["long_term_loan", "bond_payable", "long_payable", "lease_liabilities"]
    td_cols = ["short_term_loan", "noncurrent_liab_1y", "long_term_loan",
               "bond_payable", "long_payable", "lease_liabilities", "short_bond_payable"]
    lt = _sum_cols(lt_cols)
    if lt is not None:
        df["long_term_debt"] = lt
    td = _sum_cols(td_cols)
    if td is not None:
        df["total_debt"] = td

    # 有息负债 fallback：部分公司（如茅台）最新报告期「长期借款/短期借款」字段为 NaN
    # （东财不披露该明细科目），但实际有应付债券/长期应付款/租赁负债等。此时用完整
    # 口径 total_debt 兜底，避免有息负债误显示为空。
    if "interest_bearing_debt" in df.columns and "total_debt" in df.columns:
        df["interest_bearing_debt"] = df["interest_bearing_debt"].fillna(df["total_debt"])

    if "goodwill" in df.columns:
        df["goodwill"] = df["goodwill"].fillna(0.0)
    if "preferred_shares" in df.columns:
        df["preferred_shares"] = df["preferred_shares"].fillna(0.0)
    return df


def _to_yi(df: pd.DataFrame) -> pd.DataFrame:
    """金额字段由元换算为亿元。"""
    df = df.copy()
    for col in _MONEY_FIELDS.intersection(df.columns):
        df[col] = df[col] / 1e8
    return df


def _annual_dividend(dv: pd.DataFrame) -> pd.DataFrame:
    """分红按年度汇总（同一年多次分红加总），report_date 归一到 12-31。

    统一输出「每股股息」口径 dividend_per_share（元/股）：
    - A 股：dividend_per_10（每10股派息）÷ 10
    - 港股：dividend_per_share（每股派息，已是人民币）直接用
    """
    df = dv.copy()
    df["year"] = df["report_date"].dt.year

    if "dividend_per_share" not in df.columns and "dividend_per_10" in df.columns:
        df["dividend_per_share"] = df["dividend_per_10"] / 10

    agg_cols = {}
    if "dividend_per_share" in df.columns:
        agg_cols["dividend_per_share"] = ("dividend_per_share", "sum")  # 年内多次分红加总
    if "dividend_yield_pct" in df.columns:
        agg_cols["dividend_yield_pct"] = ("dividend_yield_pct", "sum")
    if "total_shares" in df.columns:
        agg_cols["total_shares"] = ("total_shares", "last")

    if not agg_cols:
        return pd.DataFrame()

    agg = df.groupby(["symbol", "year"], as_index=False).agg(**agg_cols)
    agg["report_date"] = pd.to_datetime(agg["year"].astype(str) + "-12-31")
    return agg


def build_annual_financials(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """合并多表，输出对齐模板的年度财务数据（宽表，金额单位亿元）。"""
    ps = _annual(_to_yi(calc_gross_margin(data["profit_sheet"])))
    cf = _annual(_to_yi(data["cash_flow"]))
    bs = _annual(_to_yi(_with_interest_debt(data["balance_sheet"])))
    fi = _annual(data["financial_indicator"])

    key = ["symbol", "report_date"]

    ps_cols = key + [c for c in [
        "revenue", "revenue_yoy_pct", "operating_revenue", "operating_cost",
        "total_operating_cost", "operate_tax_add", "operating_profit",
        "non_operating_income", "non_operating_expense", "total_profit",
        "income_tax", "net_profit", "net_profit_yoy_pct", "net_profit_parent",
        "minority_interest", "deduct_net_profit", "gross_margin_pct",
        "sell_expense", "admin_expense", "research_expense", "finance_expense",
        "interest_expense", "invest_income", "asset_impairment_loss", "credit_impairment_loss",
    ] if c in ps.columns]
    cf_cols = key + [c for c in [
        "ocf", "operating_cash_inflow", "operating_cash_outflow",
        "icf", "investing_cash_inflow", "investing_cash_outflow",
        "financing_cash_flow", "financing_cash_inflow", "financing_cash_outflow",
        "depreciation", "capital_expenditure",
        "amortize_intangible", "amortize_lpe",
        "depre_invest_realestate", "depre_oilgas_bio", "amortize_useright",
    ] if c in cf.columns]
    bs_cols = key + [c for c in [
        "total_assets", "total_liabilities", "total_equity", "total_equity_all",
        "current_assets", "noncurrent_assets", "monetary_funds", "inventory",
        "accounts_receivable", "fixed_assets", "construction_in_progress",
        "intangible_assets", "long_equity_invest", "other_noncurrent_assets",
        "minority_equity", "interest_bearing_debt", "goodwill",
        "long_term_loan", "short_term_loan",
        "share_capital", "preferred_shares", "audit_opinion",
        "current_liabilities", "noncurrent_liabilities", "accounts_payable",
        "other_current_assets", "other_current_liabilities", "noncurrent_liab_1y",
        "retained_profit", "bond_payable", "long_payable", "lease_liabilities",
        "short_bond_payable", "long_term_debt", "total_debt",
        "notes_receivable", "prepayments", "other_receivables", "trading_financial_assets",
        "contract_assets", "noncurrent_asset_1y", "dividend_receivable", "interest_receivable",
        "finance_receivables", "invest_realestate", "useright_asset", "long_prepaid_expense",
        "defer_tax_asset", "other_equity_invest", "other_noncurrent_finasset", "other_creditor_invest",
        "hold_maturity_invest", "notes_payable", "contract_liabilities", "staff_salary_payable",
        "tax_payable", "advance_receivables", "other_payables", "dividend_payable", "interest_payable",
        "other_noncurrent_liabilities", "defer_tax_liabilities", "long_staff_salary_payable",
        "perpetual_bond", "predict_liabilities",
        "lend_fund", "loan_advance", "buy_resale_finasset", "derivative_finasset",
        "settle_excess_reserve", "accept_deposit_interbank", "derivative_finliab",
        "sell_repo_finasset", "fee_commission_payable",
        "accept_deposit", "iofi_deposit", "cash_deposit_pbc", "deposit_interbank",
        "creditor_invest", "loan_pbc", "deposit_certificate", "precious_metal",
        "borrowings", "amortize_cost_finasset", "fvtoci_finasset", "other_asset",
    ] if c in bs.columns]
    fi_cols = key + [c for c in ["net_margin_pct", "roe_pct", "roe_weighted_pct",
                                 "debt_ratio_pct", "revenue_yoy_pct", "net_profit_yoy_pct",
                                 "ocf_to_profit_pct", "current_ratio", "quick_ratio"] if c in fi.columns]

    merged = ps[ps_cols]
    merged = merged.merge(cf[cf_cols], on=key, how="left")
    merged = merged.merge(bs[bs_cols], on=key, how="left")
    # financial_indicator 与利润表可能重复提供同比字段（revenue_yoy_pct/net_profit_yoy_pct）。
    # 用 suffixes 让财务指标接口的同名列带 _fi 后缀，再 fillna 兜底：
    # 利润表接口的同比与「营业总收入/净利润」金额行一一对应（口径更准），优先；
    # 银行利润表同比列存在但值全 NaN（不适用），或港股无利润表同比列时，回退财务指标接口。
    merged = merged.merge(fi[fi_cols], on=key, how="left", suffixes=("", "_fi"))
    for _col in ("revenue_yoy_pct", "net_profit_yoy_pct"):
        if _col in merged.columns and f"{_col}_fi" in merged.columns:
            merged[_col] = merged[_col].fillna(merged[f"{_col}_fi"])
            merged = merged.drop(columns=[f"{_col}_fi"])

    # 净利率统一口径：归母净利润 / 营业收入 × 100（与季度表一致）。
    # 原用东财 net_margin_pct（净利润含少数股东 / 营业收入），与报告的「归母净利润」
    # 行分子口径不一致（茅台差约 2pp），改为强制重算覆盖，保证年度/季度口径一致。
    if {"net_profit_parent", "operating_revenue"}.issubset(merged.columns):
        rev = merged["operating_revenue"].astype(float)
        merged["net_margin_pct"] = merged["net_profit_parent"] / rev.where(rev != 0) * 100

    # —— 三张报表新增派生指标（组件均已换算为亿元）——
    # 同比兜底：银行利润表无 TOTAL_OPERATE_INCOME_YOY/NETPROFIT_YOY、财务指标「增长率」
    # 也几乎全空（银行不适用），用金额自算同比填充缺失年份。revenue 对银行已 fallback
    # 到 operating_revenue（营业收入口径）；net_profit 为净利润（含少数股东）口径，与
    # NETPROFIT_YOY 一致。
    if "revenue" in merged.columns and "revenue_yoy_pct" in merged.columns:
        merged["revenue_yoy_pct"] = merged["revenue_yoy_pct"].fillna(
            merged["revenue"].astype(float).pct_change() * 100)
    if "net_profit" in merged.columns and "net_profit_yoy_pct" in merged.columns:
        merged["net_profit_yoy_pct"] = merged["net_profit_yoy_pct"].fillna(
            merged["net_profit"].astype(float).pct_change() * 100)

    # 自由现金流 FCF = 经营现金流净额 - 资本开支（购建固定资产等，接口存正数口径）
    if "ocf" in merged.columns and "capital_expenditure" in merged.columns:
        capex = merged["capital_expenditure"].fillna(0.0)
        merged["free_cash_flow"] = merged["ocf"] - capex

    # 经营现金流净额 / 净利润（净现比，统一重算：ocf 与 net_profit 均为亿元，含少数股东口径）
    if "ocf" in merged.columns and "net_profit" in merged.columns:
        np_ = merged["net_profit"].astype(float)
        merged["ocf_to_profit_pct"] = merged["ocf"] / np_.where(np_ != 0) * 100

    # 股东权益同比增长率 = 全部股东权益（含少数股东）同比
    if "total_equity_all" in merged.columns:
        eq = merged["total_equity_all"].astype(float)
        merged["equity_yoy_pct"] = eq.pct_change() * 100

    # 有息负债率 = 有息负债 / 总资产 × 100（有息负债优先完整口径 total_debt，缺失回退长短期借款）
    if "total_assets" in merged.columns:
        ta = merged["total_assets"].astype(float)
        ibd = merged["total_debt"].astype(float) if "total_debt" in merged.columns else None
        if ibd is not None and "interest_bearing_debt" in merged.columns:
            ibd = ibd.fillna(merged["interest_bearing_debt"])
        elif ibd is None and "interest_bearing_debt" in merged.columns:
            ibd = merged["interest_bearing_debt"].astype(float)
        if ibd is not None:
            merged["interest_bearing_debt_ratio"] = ibd / ta.where(ta != 0) * 100

    # 港股财务指标接口只返回最近约 2 年（ROE/资产负债率历史缺失），用三表自算兜底：
    # 资产负债率 = 总负债 / 总资产 × 100（两字段全历史均有）
    if "debt_ratio_pct" in merged.columns and {"total_liabilities", "total_assets"}.issubset(merged.columns):
        ta2 = merged["total_assets"].astype(float)
        merged["debt_ratio_pct"] = merged["debt_ratio_pct"].fillna(
            merged["total_liabilities"] / ta2.where(ta2 != 0) * 100)
    # ROE = 归母净利 / 平均归母权益 × 100（平均权益 = (期初 + 期末)/2，与港股 ROE_AVG 口径一致）
    if "roe_pct" in merged.columns and {"net_profit_parent", "total_equity"}.issubset(merged.columns):
        np_ = merged["net_profit_parent"].astype(float)
        eq = merged["total_equity"].astype(float)
        avg_eq = (eq + eq.shift(1)) / 2
        merged["roe_pct"] = merged["roe_pct"].fillna(np_ / avg_eq.where(avg_eq != 0) * 100)

    # 分红数据：每股股息（统一口径 dividend_per_share，元/股）、股息率
    if "dividend" in data:
        dv = _annual_dividend(data["dividend"])
        if not dv.empty:
            dv_cols = key + [c for c in ["dividend_per_share", "dividend_yield_pct"] if c in dv.columns]
            merged = merged.merge(dv[dv_cols], on=key, how="left")

    # 分红比例（股利支付率）= 分红总额 / 归母净利润 × 100
    # share_capital 已换算为「亿股」（面值 1 元），故分红总额 = 每股股息 × 总股数
    if "dividend_per_share" in merged.columns and "share_capital" in merged.columns:
        merged["dividend_total"] = merged["dividend_per_share"] * merged["share_capital"]  # 分红总额(亿元)
    if "dividend_total" in merged.columns and "net_profit_parent" in merged.columns:
        merged["dividend_payout_pct"] = merged["dividend_total"] / merged["net_profit_parent"] * 100

    # —— ValueLine 派生指标（组件均已换算为亿元）——
    # 营运资本 = 流动资产 - 流动负债
    if "current_assets" in merged.columns and "current_liabilities" in merged.columns:
        merged["working_capital"] = merged["current_assets"] - merged["current_liabilities"]

    # 折旧与摊销总额 = 固定资产折旧 + 投资性房地产折旧 + 油气生物折旧
    #                  + 无形资产摊销 + 长期待摊摊销 + 使用权资产摊销（缺失项按 0）
    dpr_cols = [c for c in ("depreciation", "depre_invest_realestate", "depre_oilgas_bio",
                            "amortize_intangible", "amortize_lpe", "amortize_useright") if c in merged.columns]
    if dpr_cols:
        merged["depreciation_amortization"] = merged[dpr_cols].fillna(0.0).sum(axis=1)

    # 所得税率 = 所得税费用 / 利润总额 × 100
    if "income_tax" in merged.columns and "total_profit" in merged.columns:
        tp = merged["total_profit"].astype(float)
        merged["income_tax_rate"] = merged["income_tax"] / tp.where(tp != 0) * 100

    # 留存收益 / 普通股权益 = 未分配利润 / 归母净资产 × 100
    if "retained_profit" in merged.columns and "total_equity" in merged.columns:
        te = merged["total_equity"].astype(float)
        merged["retained_to_equity"] = merged["retained_profit"] / te.where(te != 0) * 100

    # 总资本回报率 ROTC = 净利润 /（全部股东权益 + 有息负债）× 100
    if "net_profit" in merged.columns and "total_debt" in merged.columns:
        eq = merged["total_equity_all"] if "total_equity_all" in merged.columns else merged["total_equity"]
        capital = eq.astype(float) + merged["total_debt"].astype(float)
        merged["rotc"] = merged["net_profit"] / capital.where(capital != 0) * 100

    # 每股股息已由 _annual_dividend 统一为 dividend_per_share 口径

    return merged


def _to_single(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """累计值差分得到单期流量值。

    独立起点（取累计值本身，不做差分）：
    - A 股季报：Q1（03-31）是年初累计起点
    - 港股半年度：中期（06-30）是半年累计起点（港股无 Q1/Q3，披露 H1/FY 两期）
    判断依据：若数据含 03-31 则按 A 股季度（3 月为起点），否则 6 月为起点（港股半年度）。
    """
    df = df.copy()
    has_q1 = (df["report_date"].dt.month == 3).any()
    for c in cols:
        s = df[c].astype(float)
        single = s.diff()
        if has_q1:
            is_start = df["report_date"].dt.month == 3   # A 股 Q1
        else:
            is_start = df["report_date"].dt.month == 6   # 港股 H1（半年报独立累计）
        # 首行（数据最早起点）diff 为 NaN，也视为独立起点取累计值
        is_start = is_start | single.isna()
        single[is_start] = s[is_start]
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
    if "revenue" not in ps.columns and "operating_revenue" in ps.columns:
        ps = ps.copy()
        ps["revenue"] = ps["operating_revenue"]  # 港股营业额=总收入
    cf = data["cash_flow"].sort_values("report_date").reset_index(drop=True)
    bs = data["balance_sheet"].sort_values("report_date").reset_index(drop=True)

    # 累计值差分到单季度（列存在才差分，银行等无营业成本的行业缺 operating_cost）
    _diff_cols = ["revenue", "operating_revenue", "net_profit_parent"]
    if "operating_cost" in ps.columns:
        _diff_cols.append("operating_cost")
    ps = _to_single(ps, _diff_cols)
    cf = _to_single(cf, ["ocf"])

    # 单季比率
    if "operating_cost" in ps.columns:
        ps["gross_margin_pct"] = (ps["operating_revenue"] - ps["operating_cost"]) / ps["operating_revenue"] * 100
    else:
        # 银行等金融股无营业成本，毛利率不适用
        ps["gross_margin_pct"] = float("nan")
    ps["net_margin_pct"] = ps["net_profit_parent"] / ps["operating_revenue"] * 100

    bs = _with_interest_debt(bs)

    key = ["symbol", "report_date"]
    ps_cols = key + ["revenue", "operating_revenue", "net_profit_parent", "gross_margin_pct", "net_margin_pct"]
    cf_cols = key + ["ocf"]
    # 资产负债表字段按列存在性容错（银行等金融股无 monetary_funds/inventory/interest_bearing_debt）
    bs_cols = key + [c for c in ["total_assets", "total_liabilities", "total_equity",
                                 "monetary_funds", "inventory", "accounts_receivable",
                                 "interest_bearing_debt", "goodwill"] if c in bs.columns]

    merged = ps[ps_cols].merge(cf[cf_cols], on=key, how="left")
    merged = merged.merge(bs[bs_cols], on=key, how="left")

    # 单季 ROE = 单季归母净利 / 季末归母净资产
    merged["roe_pct"] = merged["net_profit_parent"] / merged["total_equity"] * 100

    # 同比增长率：本期 vs 去年同期（按 (年, 月) 精确对齐，缺期不会像 shift(4) 那样错位）。
    # 用 (year, month) 字典回溯而非固定位移，季度/半年度披露都能正确对齐（H1↔H1、Q3↔Q3）。
    # 去年同期货值为负或缺失时不计算——从亏损转为盈利之类的同比无经济含义。
    if not merged.empty:
        ym = list(zip(merged["report_date"].dt.year, merged["report_date"].dt.month))
        for _col, _out in (("revenue", "revenue_yoy_pct"),
                           ("net_profit_parent", "net_profit_parent_yoy_pct"),
                           ("ocf", "ocf_yoy_pct")):
            if _col not in merged.columns:
                continue
            cur = pd.to_numeric(merged[_col], errors="coerce")
            prev = {(y + 1, m): v for (y, m), v in zip(ym, cur)}
            base = pd.Series([prev.get(k) for k in ym], index=merged.index, dtype="float64")
            base = base.where(base > 0)  # 基数为负/0/缺失 → 同比置空
            merged[_out] = (cur / base - 1) * 100

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


def forward_adjust_kline(kline: pd.DataFrame, dividend: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """前复权（腾讯/同花顺「减法」口径）：qfq(t) = 原始价(t) − Σ 除权日晚于 t 的每股派息。

    为什么必须显式复现这个口径：同一根 K 线在不同 App 上是不同的数——茅台 2026-02-06
    的高点，不复权 1568.00、腾讯前复权 1539.98、新浪前复权 1531.75（比例法）。
    用户在腾讯自选股里看到 1539，报告里写 1568 就会被当成数据错误。
    本函数已交叉验证与腾讯一致：2025-08-01 收盘 1417.000 → 前复权 1365.019，
    差额 51.981 恰等于其后两次派息 23.957 + 28.0242 之和。

    除权日取不到时不做任何调整并置 qfq_available=False —— 调用方据此降级到不复权口径，
    好过给出一个用 report_date 当除权日算出来的错数（两者可差半年）。
    """
    out = kline.copy()
    meta: dict = {"available": False, "n_events": 0, "events": []}
    if kline is None or kline.empty or dividend is None or dividend.empty:
        return out, meta
    if "ex_date" not in dividend.columns:
        return out, meta

    dv = dividend.copy()
    if "dividend_per_share" not in dv.columns:
        if "dividend_per_10" not in dv.columns:
            return out, meta
        dv["dividend_per_share"] = dv["dividend_per_10"] / 10
    dv = dv.dropna(subset=["ex_date", "dividend_per_share"])
    dv = dv[dv["dividend_per_share"] > 0].sort_values("ex_date")
    if dv.empty:
        return out, meta

    dates = pd.to_datetime(out["report_date"])
    adj = pd.Series(0.0, index=out.index, dtype="float64")
    events: list[dict] = []
    for _, r in dv.iterrows():
        ex = pd.Timestamp(r["ex_date"])
        dps = float(r["dividend_per_share"])
        adj.loc[dates < ex] += dps  # 除权日之前的价格都要下调这一次派息
        events.append({"ex_date": ex.date(), "dps": round(dps, 4)})

    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col + "_qfq"] = out[col] - adj

    lo, hi = dates.min(), dates.max()
    meta.update({
        "available": True,
        "n_events": len(events),
        # 只保留落在 K 线区间附近的除权事件，供报告脚注说明「区间内派了几次、共多少」
        "events_in_range": [e for e in events if lo <= pd.Timestamp(e["ex_date"]) <= hi],
        "events": events,
    })
    return out, meta


def build_valuation(valuation: pd.DataFrame, annual: pd.DataFrame) -> dict:
    """估值面板：PE/PB/股息率/52周股价区间/估值分位。

    valuation 为长表（indicator: market_cap/pb，value 为对应值）。
    - PB / 市值：百度估值直接给（近十年）
    - PE = 最新总市值 / 最新年报归母净利
    - 52周股价 = 近一年市值 min/max ÷ 总股本
    - 分位：PB 用 PB 序列；PE 用历史 PE 序列（历史市值 ÷ 同期已披露净利，
      forward-fill 年报净利到每个市值采样日），避免「市值增长」被误判为「估值贵」。
    """
    mcap = valuation[valuation["indicator"] == "market_cap"].sort_values("report_date")
    pb = valuation[valuation["indicator"] == "pb"].sort_values("report_date")
    if mcap.empty or pb.empty:
        return None

    pb_now = pb["value"].iloc[-1]
    mcap_now = mcap["value"].iloc[-1]  # 总市值（亿元）

    net_profit = annual["net_profit_parent"].iloc[-1]  # 最新年报归母净利（亿元）
    pe = mcap_now / net_profit if net_profit and net_profit > 0 else None

    # 股息率（市值口径）：最近一个有分红数据的年度，分红总额 ÷ 当前市值 × 100。
    # 旧口径是「年内各次派息的股息率之和」——分母是每次派息当时的股价，与 PE/PB
    # 用的「当前市值」不是同一个分母，所以 4.0%（旧）和 4.1%（市值口径）并存，
    # 也没法算历史分位（XHS 卡片上那个空着的「分位 —」就是它）。
    #
    # 同时回传该年度，避免面板把 2025 全年股息率标成含义模糊的「最新报告期」
    # ——2026 中报分红行存在但为空，容易被误读成「2026 年股息率」。
    dividend_yield = None
    dividend_year = None
    dividend_total = None
    dividend_per_share = None
    dividend_payout_pct = None
    dividend_history: list[dict] = []
    dv_cols = [c for c in ["dividend_per_share", "dividend_total", "dividend_payout_pct"]
               if c in annual.columns]
    if "dividend_total" in dv_cols:
        dv_all = annual[["report_date"] + dv_cols].dropna(subset=["dividend_total"]).sort_values("report_date")
        dv_all = dv_all[dv_all["dividend_total"] > 0]
        if not dv_all.empty:
            last = dv_all.iloc[-1]
            dividend_year = int(pd.Timestamp(last["report_date"]).year)
            dividend_total = float(last["dividend_total"])
            if pd.notna(last.get("dividend_per_share")):
                dividend_per_share = float(last["dividend_per_share"])
            if pd.notna(last.get("dividend_payout_pct")):
                dividend_payout_pct = float(last["dividend_payout_pct"])
            if mcap_now:
                dividend_yield = dividend_total / mcap_now * 100
            # 历史每股分红金额（近 10 个分红年度，供报告展示分红连续性）
            for _, r in dv_all.tail(10).iterrows():
                dividend_history.append({
                    "year": int(pd.Timestamp(r["report_date"]).year),
                    "dps": float(r["dividend_per_share"]) if pd.notna(r.get("dividend_per_share")) else None,
                    "total": float(r["dividend_total"]),
                    "payout_pct": float(r["dividend_payout_pct"]) if pd.notna(r.get("dividend_payout_pct")) else None,
                })

    total_shares_yi = annual["share_capital"].iloc[-1] if "share_capital" in annual.columns else None  # 已是亿股

    # 52周股价区间（近一年市值 ÷ 总股本）
    one_year = mcap[mcap["report_date"] >= (mcap["report_date"].max() - pd.DateOffset(years=1))]
    price_low = price_now = price_high = None
    if total_shares_yi and not one_year.empty:
        price_low = one_year["value"].min() / total_shares_yi
        price_high = one_year["value"].max() / total_shares_yi
        price_now = mcap_now / total_shares_yi

    # 分位（当前值在历史序列中的百分位）
    pb_pctile = (pb["value"] < pb_now).mean() * 100
    pe_s = _pe_series(mcap, annual)
    pe_pctile = float((pe_s < pe_s.iloc[-1]).mean() * 100) if not pe_s.empty else None
    dy_s = _dividend_yield_series(annual, mcap)
    dividend_pctile = float((dy_s < dy_s.iloc[-1]).mean() * 100) if not dy_s.empty else None
    dividend_series_n = int(len(dy_s))

    # PE 十年走势图序列：与 PE 分位同一条序列，近 10 年降采样到 ≤180 点
    # （SVG 体积可控，且 180 个点足够画出趋势，再多也只是像素级差别）
    pe_chart: list[dict] = []
    pe_median = None
    pe_range = None
    if not pe_s.empty:
        pe_win = pe_s[pe_s.index >= (pe_s.index.max() - pd.DateOffset(years=10))]
        pe_median = float(pe_win.median())
        step = max(1, -(-len(pe_win) // 180))  # 整数向上取整
        pe_win = pe_win.iloc[::step]
        pe_chart = [{"d": d.date(), "pe": round(float(v), 2)} for d, v in pe_win.items()]
        pe_range = {
            "start": pe_win.index.min().date(),
            "end": pe_win.index.max().date(),
            "lo": float(pe_win.min()),
            "hi": float(pe_win.max()),
        }

    # 序列元信息：供报告标注分位口径（跨度/采样数），使「X% 分位」可被独立复核
    series_meta = {
        "val_series_start": pd.Timestamp(mcap["report_date"].min()).date(),
        "val_series_end": pd.Timestamp(mcap["report_date"].max()).date(),
        "val_series_n": int(len(mcap)),
    }

    return {
        "pe": pe,
        "pb": pb_now,
        "dividend_yield": dividend_yield,
        "dividend_year": dividend_year,
        "dividend_total": dividend_total,
        "dividend_per_share": dividend_per_share,
        "dividend_payout_pct": dividend_payout_pct,
        "dividend_pctile": dividend_pctile,
        "dividend_series_n": dividend_series_n,
        "dividend_history": dividend_history,
        "price_low": price_low,
        "price_now": price_now,
        "price_high": price_high,
        "pe_pctile": pe_pctile,
        "pb_pctile": pb_pctile,
        "pe_chart": pe_chart,
        "pe_median": pe_median,
        "pe_range": pe_range,
        **series_meta,
    }


def _pe_series(mcap: pd.DataFrame, annual: pd.DataFrame) -> pd.Series:
    """历史 PE 序列（index 为采样日）= 各采样日市值 ÷ 该日已披露的最近年报归母净利。

    市值是日频序列，净利是年报低频序列——把年报净利按 report_date 升序
    forward-fill 到每个市值采样日（该日市场只看得见已披露的最近年报净利），
    再算 PE = 市值/净利。PE 分位与 PE 十年走势图共用这一条序列，避免两处口径打架。

    缺陷规避：不用「市值分位」近似（市值因公司成长天然右移，会把
    「公司变大」误判成「估值变贵」，导致成熟白马股 PE 分位常年 99%+）。
    """
    if "net_profit_parent" not in annual.columns:
        return pd.Series(dtype=float)
    np_annual = annual[["report_date", "net_profit_parent"]].dropna(
        subset=["net_profit_parent"]
    ).sort_values("report_date")
    if np_annual.empty:
        return pd.Series(dtype=float)

    # 市值日频序列 → 关联「该日已披露的最近年报净利」（merge_asof 向后取 <= 日期）
    mcap_sorted = mcap[["report_date", "value"]].sort_values("report_date")
    merged = pd.merge_asof(
        mcap_sorted,
        np_annual[["report_date", "net_profit_parent"]],
        on="report_date",
        direction="backward",
    )
    keep = merged["net_profit_parent"] > 0  # 剔除净利非正的样本
    s = pd.Series(
        (merged["value"] / merged["net_profit_parent"])[keep].to_numpy(),
        index=pd.DatetimeIndex(merged["report_date"][keep]),
        name="pe",
    )
    return s.dropna()


# 分红年度 Y 的派息要到次年 4 月末（年报 + 股东大会决议）才为市场所知。
# 直接用 report_date 对齐会把「事后才知道的分红」提前到当年 12-31，系统性高估历史股息率。
_DIV_DISCLOSURE_LAG = pd.DateOffset(months=4)


def _dividend_yield_series(annual: pd.DataFrame, mcap: pd.DataFrame) -> pd.Series:
    """历史股息率序列（%）= 各采样日「市场已知的最近年度分红总额」÷ 该日市值 × 100。

    与 PE 序列同一套 merge_asof 思路，唯一差别是分红总额要按披露时点后移
    （见 _DIV_DISCLOSURE_LAG），否则每年 1-4 月这一段会用到当时还不存在的数据。
    """
    if "dividend_total" not in annual.columns:
        return pd.Series(dtype=float)
    dv = annual[["report_date", "dividend_total"]].dropna(subset=["dividend_total"])
    dv = dv[dv["dividend_total"] > 0].sort_values("report_date")
    if dv.empty:
        return pd.Series(dtype=float)
    dv = dv.assign(effective=dv["report_date"] + _DIV_DISCLOSURE_LAG)
    m = mcap[["report_date", "value"]].sort_values("report_date").rename(columns={"value": "mcap"})
    merged = pd.merge_asof(
        m,
        dv[["effective", "dividend_total"]],
        left_on="report_date",
        right_on="effective",
        direction="backward",
    )
    s = merged["dividend_total"] / merged["mcap"] * 100
    keep = merged["dividend_total"].notna()
    return pd.Series(
        s[keep].to_numpy(),
        index=pd.DatetimeIndex(merged["report_date"][keep]),
        name="dividend_yield",
    ).dropna()
