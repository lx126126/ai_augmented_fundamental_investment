"""字段映射：AKShare 接口字段 → 标准字段名（snake_case）。

数据层统一使用标准字段名，屏蔽上游接口的中文/英文列名差异。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. 财务指标接口 stock_financial_analysis_indicator（中文列名，比率型指标）
# ---------------------------------------------------------------------------
FINANCIAL_INDICATOR_MAP = {
    "日期": "report_date",
    # 盈利能力
    "销售毛利率(%)": "gross_margin_pct",
    "销售净利率(%)": "net_margin_pct",
    "营业利润率(%)": "operating_margin_pct",
    "主营业务利润率(%)": "main_biz_margin_pct",
    "净资产收益率(%)": "roe_pct",
    "加权净资产收益率(%)": "roe_weighted_pct",
    "总资产利润率(%)": "roa_pct",
    "总资产净利润率(%)": "roa_net_pct",
    "成本费用利润率(%)": "cost_profit_pct",
    # 成长性
    "主营业务收入增长率(%)": "revenue_yoy_pct",
    "净利润增长率(%)": "net_profit_yoy_pct",
    "净资产增长率(%)": "net_assets_yoy_pct",
    "总资产增长率(%)": "total_assets_yoy_pct",
    # 偿债能力
    "资产负债率(%)": "debt_ratio_pct",
    "流动比率": "current_ratio",
    "速动比率": "quick_ratio",
    "现金比率(%)": "cash_ratio_pct",
    "产权比率(%)": "equity_multiplier_pct",
    # 营运能力
    "应收账款周转率(次)": "receivable_turnover",
    "存货周转率(次)": "inventory_turnover",
    "总资产周转率(次)": "asset_turnover",
    "流动资产周转率(次)": "current_asset_turnover",
    # 现金流质量（造假检测核心）
    "经营现金净流量与净利润的比率(%)": "ocf_to_profit_pct",
    "经营现金净流量对销售收入比率(%)": "ocf_to_revenue_pct",
    "现金流量比率(%)": "cashflow_ratio_pct",
    # 每股指标
    "摊薄每股收益(元)": "eps",
    "每股净资产_调整前(元)": "bps",
    "每股经营性现金流(元)": "ocf_per_share",
    # 股东回报
    "股息发放率(%)": "dividend_payout_pct",
    # 资产规模
    "总资产(元)": "total_assets",
}

# ---------------------------------------------------------------------------
# 2. 东财利润表 stock_profit_sheet_by_report_em（英文列名，绝对额）
# ---------------------------------------------------------------------------
PROFIT_SHEET_MAP = {
    "REPORT_DATE": "report_date",
    "REPORT_TYPE": "report_type",
    "TOTAL_OPERATE_INCOME": "revenue",           # 营业总收入
    "OPERATE_INCOME": "operating_revenue",        # 营业收入
    "OPERATE_COST": "operating_cost",             # 营业成本
    "OPERATE_PROFIT": "operating_profit",         # 营业利润
    "TOTAL_PROFIT": "total_profit",               # 利润总额
    "NETPROFIT": "net_profit",                    # 净利润
    "PARENT_NETPROFIT": "net_profit_parent",      # 归母净利润
    "SALE_EXPENSE": "sell_expense",               # 销售费用（M-Score SGAI）
    "MANAGE_EXPENSE": "admin_expense",            # 管理费用（M-Score SGAI）
    "INCOME_TAX": "income_tax",                   # 所得税费用
    "FE_INTEREST_EXPENSE": "interest_expense",    # 财务费用-利息费用（长期利息近似）
}

# ---------------------------------------------------------------------------
# 3. 东财资产负债表 stock_balance_sheet_by_report_em（英文列名，时点值）
# ---------------------------------------------------------------------------
BALANCE_SHEET_MAP = {
    "REPORT_DATE": "report_date",
    "REPORT_TYPE": "report_type",
    "TOTAL_ASSETS": "total_assets",               # 总资产
    "TOTAL_LIABILITIES": "total_liabilities",     # 总负债
    "TOTAL_PARENT_EQUITY": "total_equity",        # 归母净资产
    "TOTAL_EQUITY": "total_equity_all",           # 全部股东权益（含少数股东，ROTC 用）
    "TOTAL_CURRENT_ASSETS": "current_assets",     # 流动资产（M-Score AQI）
    "MONETARYFUNDS": "monetary_funds",            # 货币资金
    "INVENTORY": "inventory",                     # 存货
    "ACCOUNTS_RECE": "accounts_receivable",       # 应收账款
    "GOODWILL": "goodwill",                       # 商誉
    "FIXED_ASSET": "fixed_assets",                # 固定资产
    "BORROW_FUND": "borrowings",                  # 借款（有息负债核心）
    "LONG_LOAN": "long_term_loan",                # 长期借款
    "SHORT_LOAN": "short_term_loan",              # 短期借款
    "SHARE_CAPITAL": "share_capital",             # 股本（普通股，面值1元，总股本）
    "PREFERRED_SHARES": "preferred_shares",       # 优先股
    "OPINION_TYPE": "audit_opinion",              # 审计意见（年报行有值，如 标准无保留意见）
    # —— ValueLine 补充：流动状况 + 债务结构 + 留存收益 ——
    "TOTAL_CURRENT_LIAB": "current_liabilities",  # 流动负债合计
    "ACCOUNTS_PAYABLE": "accounts_payable",       # 应付账款
    "OTHER_CURRENT_ASSET": "other_current_assets",# 其他流动资产
    "OTHER_CURRENT_LIAB": "other_current_liabilities",  # 其他流动负债
    "NONCURRENT_LIAB_1YEAR": "noncurrent_liab_1y",      # 一年内到期非流动负债
    "UNASSIGN_RPOFIT": "retained_profit",         # 未分配利润（留存收益）
    "BOND_PAYABLE": "bond_payable",               # 应付债券
    "LONG_PAYABLE": "long_payable",               # 长期应付款
    "LEASE_LIAB": "lease_liabilities",            # 租赁负债
    "SHORT_BOND_PAYABLE": "short_bond_payable",   # 应付短期债券
    "TOTAL_NONCURRENT_LIAB": "noncurrent_liabilities",  # 非流动负债合计
}

# ---------------------------------------------------------------------------
# 4. 东财现金流表 stock_cash_flow_sheet_by_report_em（英文列名）
# ---------------------------------------------------------------------------
CASH_FLOW_MAP = {
    "REPORT_DATE": "report_date",
    "REPORT_TYPE": "report_type",
    "NETCASH_OPERATE": "ocf",                     # 经营活动现金流净额
    "NETCASH_INVEST": "icf",                      # 投资活动现金流净额
    "NETCASH_FINANCE": "fcf",                     # 筹资活动现金流净额
    "FA_IR_DEPR": "depreciation",                 # 固定资产折旧（M-Score DEPI）
    # —— ValueLine 补充：资本开支 + 折旧摊销明细 ——
    "CONSTRUCT_LONG_ASSET": "capital_expenditure",      # 购建固定资产等（资本开支）
    "IA_AMORTIZE": "amortize_intangible",               # 无形资产摊销
    "LPE_AMORTIZE": "amortize_lpe",                     # 长期待摊费用摊销
    "IR_DEPR": "depre_invest_realestate",               # 投资性房地产折旧
    "OILGAS_BIOLOGY_DEPR": "depre_oilgas_bio",          # 油气/生物资产折旧
    "USERIGHT_ASSET_AMORTIZE": "amortize_useright",     # 使用权资产摊销
}

# ---------------------------------------------------------------------------
# 5. 东财分红送配 stock_fhps_detail_em（中文列名，分红/股息率/股本）
# ---------------------------------------------------------------------------
DIVIDEND_MAP = {
    "报告期": "report_date",
    "现金分红-现金分红比例": "dividend_per_10",   # 每10股派息(元)
    "现金分红-股息率": "dividend_yield",          # 股息率(小数，需×100转%)
    "总股本": "total_shares",                     # 普通股数量(股)
}

# ---------------------------------------------------------------------------
# 6. 东财主营构成 stock_zygc_em（中文列名，分业务收入/毛利率）
# ---------------------------------------------------------------------------
SEGMENT_MAP = {
    "股票代码": "symbol",
    "报告日期": "report_date",
    "分类类型": "category_type",       # 按行业分类 / 按产品分类 / 按地区分类
    "主营构成": "segment_name",        # 业务条线名
    "主营收入": "segment_revenue",     # 收入（元）
    "收入比例": "segment_revenue_pct",  # 收入占比（小数）
    "主营成本": "segment_cost",        # 成本（元）
    "主营利润": "segment_profit",      # 利润（元）
    "毛利率": "segment_margin",        # 毛利率（小数）
}

# ---------------------------------------------------------------------------
# 7. 百度估值 stock_zh_valuation_baidu（date/value 两列）
#    indicator: 总市值(亿) / 市净率 / 市现率；period: 近一年/近三年等
# ---------------------------------------------------------------------------
VALUATION_MAP = {
    "date": "date",
    "value": "value",
}
