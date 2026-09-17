#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""raw 层 schema 落盘：从 DuckDB 反向生成 raw.* 的 DDL 文件。

为什么需要这个脚本
------------------
mart 层有显式 DDL（`src/data/warehouse.py` 内联声明），raw 层则**没有**：
`src/data/warehouse.py:load_raw_layer()` 用
`pd.concat(join="outer")` 做跨标的列并集 + DuckDB 类型推断，直接
`CREATE OR REPLACE TABLE raw.{table}` 物化 —— 表结构是"跑出来的"，不是"声明出来的"。

这带来两个问题：
  1. 想知道 raw 有哪些列、什么类型、什么单位，只能连库查（或读代码猜）；
  2. 上游东财接口新增/改名一个字段，表结构会静默变化，没有 diff 可比对。

本脚本把真实表结构**反向导出**为 DDL 文件，作用等价于给推理出的 schema 补一份
"声明快照"：既可直接阅读，也可入库前 diff（重跑本脚本 → git diff 看结构漂移）。

设计原则
--------
1. 单一事实源仍是 DuckDB（脚本只读，绝不改库）——DDL 是产物，不是约束。
2. 列注释（含义 / 单位 / 口径）写在本脚本的字典里，不写进产物文件，
   这样重跑不会丢注释，注释也随代码一起被 review。
3. 单位标注是硬需求：raw 层**不是统一量纲**（多数列是元，少数是亿元），
   这是本项目最容易踩的口径坑（详见下方 UNIT 说明），必须在 DDL 里逐列写明。

口径说明（重要）
----------------
raw 层保留东财/腾讯/百度的**原始口径**，量纲不统一：
  · 绝大多数金额列 = **元**（profit_sheet.revenue / balance_sheet.total_assets / segments.segment_revenue ...）
  · `quote.market_cap`、`valuation.value`、`competition.revenue_yi|net_profit_yi` = **亿元**
  · `dividend.total_shares`、`balance_sheet.share_capital` = **股**（非亿股）
  · 所有 `*_pct` = **%**（非小数）
转「亿元」统一口径是 mart 层（cleaner.build_*）的职责，raw 层不做任何加工。

用法
----
    python scripts/gen_raw_schema.py           # 生成 / 刷新 sql/schema_raw.sql
    python scripts/gen_raw_schema.py --stdout  # 打印到终端，不写文件
    python scripts/gen_raw_schema.py --check   # 只比对现有文件是否有结构漂移（CI 友好）

方言固定为 DuckDB（唯一实际使用的引擎）。事实源与产物同引擎，`--check` 才能
拿产物直接对着 DuckDB 验证；跨方言映射属于迁移场景，本机不落地，故不再保留。

退出码：0 = 成功（--check 时表示无漂移）；1 = 有漂移 / 失败。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "warehouse" / "fqf.duckdb"
OUT_PATH = ROOT / "sql" / "schema_raw.sql"

# ---------------------------------------------------------------------------
# 类型映射：DuckDB（pandas 推断结果）→ DDL 中的类型名
#
# 事实源本身就是 DuckDB，故这里是「规范化」而非「跨方言翻译」：
# pandas 写 parquet 时可能落成 TIMESTAMP_NS，DuckDB 建表则统一 TIMESTAMP。
# ---------------------------------------------------------------------------
TYPE_MAP = {
    "TIMESTAMP_NS": "TIMESTAMP",
    "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE",
    "DOUBLE": "DOUBLE",
    "BIGINT": "BIGINT",
    "INTEGER": "INTEGER",
    "BOOLEAN": "BOOLEAN",
    "VARCHAR": "VARCHAR",
}

# ---------------------------------------------------------------------------
# 表级元信息：业务含义 / 粒度 / 更新语义
#
# 更新语义只有两种（由 src/data/market_snapshot.py + storage.py 决定）：
#   「覆盖式全历史」—— 每次重拉接口返回的**全历史**，整文件覆写 parquet
#                      → 单文件即全历史，历史不丢，但上游重述会静默改写旧值
#   「覆盖式快照」  —— 每次只落**当日一行**，整文件覆写
#                      → 覆盖即丢历史，故另有 data/market/{code}_*.parquet 按日追加归档
# ---------------------------------------------------------------------------
TABLE_META: dict[str, dict[str, str]] = {
    "financial_indicator": {
        "title": "财务指标（盈利能力 / 偿债 / 营运 / 每股）",
        "source": "东财 · 财务分析指标",
        "grain": "(symbol, report_date)",
        "unit": "比率列 = %，金额列 = 元",
        "fresh": "覆盖式全历史",
    },
    "profit_sheet": {
        "title": "利润表（含营业总收入 / 归母净利 / 各项费用）",
        "source": "东财 · 财务报表",
        "grain": "(symbol, report_date)",
        "unit": "金额列 = 元；eps = 元/股；比率列 = %",
        "fresh": "覆盖式全历史",
    },
    "balance_sheet": {
        "title": "资产负债表（资产 / 负债 / 权益，含银保专用科目）",
        "source": "东财 · 财务报表",
        "grain": "(symbol, report_date)",
        "unit": "金额列 = 元；股本列 = 股",
        "fresh": "覆盖式全历史",
    },
    "cash_flow": {
        "title": "现金流量表（经营 / 投资 / 筹资 + 折旧摊销明细）",
        "source": "东财 · 财务报表",
        "grain": "(symbol, report_date)",
        "unit": "金额列 = 元",
        "fresh": "覆盖式全历史",
    },
    "dividend": {
        "title": "分红送配（每股股息 / 股息率 / 除权日）",
        "source": "东财 · 分红送配",
        "grain": "(symbol, report_date)",
        "unit": "dividend_per_10 = 元/10股；dividend_per_share = 元/股；股息率 = %；total_shares = 股",
        "fresh": "覆盖式全历史",
    },
    "segments": {
        "title": "主营构成（分产品 / 分行业收入与毛利率）",
        "source": "东财 · 主营构成",
        "grain": "(symbol, report_date, category_type, segment_name)",
        "unit": "金额列 = 元；segment_margin = %；segment_revenue_pct = %",
        "fresh": "覆盖式全历史",
    },
    "valuation": {
        "title": "估值历史长表（百度股市通，近十年逐日）",
        "source": "百度股市通",
        "grain": "(symbol, indicator, report_date)",
        "unit": "value 列 = 亿元（market_cap）/ 倍（pb）—— 按 indicator 区分含义",
        "fresh": "覆盖式全历史",
    },
    "quote": {
        "title": "行情快照（现价 / PE / PB / 市值 / 52周，单行）",
        "source": "腾讯行情",
        "grain": "(symbol)",
        "unit": "price = 元（港股为港元）；market_cap = 亿元；比率列 = %",
        "fresh": "覆盖式快照（历史归档见 data/market/{code}_quote.parquet）",
    },
    "rating": {
        "title": "机构评级（评级分布 / 未来三年 EPS 预测 / 目标价）",
        "source": "东财 · 机构评级",
        "grain": "(symbol)",
        "unit": "eps_* = 元/股；target_price = 元；评级列 = 家数",
        "fresh": "覆盖式快照（无历史归档）",
    },
    "competition": {
        "title": "同行比较（同行业公司的营收 / 净利）",
        "source": "东财 · 同行比较",
        "grain": "(symbol, report_date)",
        "unit": "revenue_yi / net_profit_yi = 亿元（列名已带 _yi）",
        "fresh": "覆盖式全历史",
    },
    "profile": {
        "title": "公司简介（主营业务 / 经营范围）",
        "source": "东财 · 公司概况",
        "grain": "(symbol)",
        "unit": "—",
        "fresh": "覆盖式静态（低频，随财报季一并刷新）",
    },
}

# ---------------------------------------------------------------------------
# 列注释：{表: {列: 注释}}。注释写在这里而非产物文件，重跑不丢。
# 未收录的列由脚本自动留空（不编造含义），便于发现上游新增字段。
# ---------------------------------------------------------------------------
COL_DOC: dict[str, dict[str, str]] = {
    "profit_sheet": {
        "report_date": "报告期（期末日）",
        "report_type": "报表类型（年报 / 中报 / 季报）",
        "revenue": "营业总收入 —— 利润表第一行，对外与媒体引用口径",
        "revenue_yoy_pct": "营业总收入同比 %",
        "operating_revenue": "营业收入 —— 利润表「其中：」明细项，与营业成本配对",
        "operating_cost": "营业成本",
        "total_operating_cost": "营业总成本",
        "operate_tax_add": "税金及附加",
        "operating_profit": "营业利润",
        "non_operating_income": "营业外收入",
        "non_operating_expense": "营业外支出",
        "total_profit": "利润总额",
        "income_tax": "所得税费用",
        "net_profit": "净利润（含少数股东）",
        "net_profit_yoy_pct": "净利润同比 %",
        "net_profit_parent": "归属于母公司股东的净利润",
        "minority_interest": "少数股东损益",
        "deduct_net_profit": "扣非归母净利润",
        "sell_expense": "销售费用",
        "admin_expense": "管理费用",
        "research_expense": "研发费用",
        "finance_expense": "财务费用",
        "interest_expense": "利息费用（财务费用明细）",
        "invest_income": "投资收益",
        "asset_impairment_loss": "资产减值损失",
        "credit_impairment_loss": "信用减值损失",
        "eps": "每股收益（元/股）",
        "symbol": "股票代码（A 股 6 位；港股为 5 位如 00700）",
    },
    "balance_sheet": {
        "report_date": "报告期（期末日）",
        "report_type": "报表类型（年报 / 中报 / 季报）",
        "total_assets": "资产总计",
        "total_liabilities": "负债合计",
        "total_equity": "归属于母公司股东权益合计",
        "total_equity_all": "股东权益合计（含少数股东）",
        "current_assets": "流动资产合计",
        "noncurrent_assets": "非流动资产合计",
        "monetary_funds": "货币资金",
        "inventory": "存货",
        "accounts_receivable": "应收账款",
        "goodwill": "商誉",
        "fixed_assets": "固定资产",
        "construction_in_progress": "在建工程",
        "intangible_assets": "无形资产",
        "long_equity_invest": "长期股权投资",
        "other_noncurrent_assets": "其他非流动资产",
        "minority_equity": "少数股东权益",
        "borrowings": "借款合计（短期 + 长期）",
        "long_term_loan": "长期借款",
        "short_term_loan": "短期借款",
        "share_capital": "实收资本（股本）—— 单位：股",
        "share_capital_raw": "股本（原始口径，备用）",
        "preferred_shares": "优先股",
        "audit_opinion": "审计意见类型（东财 OPINION_TYPE）",
        "current_liabilities": "流动负债合计",
        "accounts_payable": "应付账款",
        "other_current_assets": "其他流动资产",
        "other_current_liabilities": "其他流动负债",
        "noncurrent_liab_1y": "一年内到期的非流动负债",
        "retained_profit": "未分配利润",
        "bond_payable": "应付债券",
        "long_payable": "长期应付款",
        "lease_liabilities": "租赁负债",
        "short_bond_payable": "应付短期债券",
        "noncurrent_liabilities": "非流动负债合计",
        "notes_receivable": "应收票据",
        "prepayments": "预付款项",
        "other_receivables": "其他应收款",
        "trading_financial_assets": "交易性金融资产",
        "contract_assets": "合同资产",
        "noncurrent_asset_1y": "一年内到期的非流动资产",
        "dividend_receivable": "应收股利",
        "interest_receivable": "应收利息",
        "finance_receivables": "应收款项融资",
        "invest_realestate": "投资性房地产",
        "useright_asset": "使用权资产",
        "long_prepaid_expense": "长期待摊费用",
        "defer_tax_asset": "递延所得税资产",
        "other_equity_invest": "其他权益工具投资",
        "other_noncurrent_finasset": "其他非流动金融资产",
        "other_creditor_invest": "其他债权投资",
        "hold_maturity_invest": "持有至到期投资",
        "notes_payable": "应付票据",
        "contract_liabilities": "合同负债",
        "staff_salary_payable": "应付职工薪酬",
        "tax_payable": "应交税费",
        "advance_receivables": "预收款项",
        "other_payables": "其他应付款",
        "dividend_payable": "应付股利",
        "interest_payable": "应付利息",
        "other_noncurrent_liabilities": "其他非流动负债",
        "defer_tax_liabilities": "递延所得税负债",
        "long_staff_salary_payable": "长期应付职工薪酬",
        "perpetual_bond": "永续债",
        "predict_liabilities": "预计负债",
        # —— 以下为银行 / 保险专用科目（银行标的才有值，其余为 NULL）——
        "lend_fund": "发放贷款及垫款（银行专用）",
        "loan_advance": "贷款及垫款（银行专用）",
        "buy_resale_finasset": "买入返售金融资产（银行专用）",
        "derivative_finasset": "衍生金融资产（银行专用）",
        "settle_excess_reserve": "结算备付金（证券专用）",
        "accept_deposit_interbank": "同业及其他金融机构存放款项（银行专用）",
        "derivative_finliab": "衍生金融负债（银行专用）",
        "sell_repo_finasset": "卖出回购金融资产款（银行专用）",
        "fee_commission_payable": "应付手续费及佣金（银行专用）",
        "creditor_invest": "债权投资（银行专用）",
        "amortize_cost_finasset": "以摊余成本计量的金融资产（银行专用）",
        "fvtoci_finasset": "以公允价值计量且其变动计入其他综合收益的金融资产（银行专用）",
        "loan_pbc": "向中央银行借款（银行专用）",
        "accept_deposit": "吸收存款（银行专用）",
        "iofi_deposit": "同业及其他金融机构存放（银行专用）",
        "cash_deposit_pbc": "存放中央银行款项（银行专用）",
        "deposit_interbank": "存放同业款项（银行专用）",
        "precious_metal": "贵金属（银行专用）",
        "other_asset": "其他资产",
        "deposit_certificate": "存单（银行专用）",
        "symbol": "股票代码（A 股 6 位；港股为 5 位）",
    },
    "cash_flow": {
        "report_date": "报告期（期末日）",
        "report_type": "报表类型（年报 / 中报 / 季报）",
        "operating_cash_inflow": "经营活动现金流入小计",
        "operating_cash_outflow": "经营活动现金流出小计",
        "ocf": "经营活动产生的现金流量净额",
        "investing_cash_inflow": "投资活动现金流入小计",
        "investing_cash_outflow": "投资活动现金流出小计",
        "icf": "投资活动产生的现金流量净额",
        "financing_cash_inflow": "筹资活动现金流入小计",
        "financing_cash_outflow": "筹资活动现金流出小计",
        "financing_cash_flow": "筹资活动产生的现金流量净额",
        "depreciation": "固定资产折旧",
        "capital_expenditure": "购建固定资产、无形资产和其他长期资产支付的现金（资本开支）",
        "amortize_intangible": "无形资产摊销",
        "amortize_lpe": "长期待摊费用摊销",
        "depre_invest_realestate": "投资性房地产折旧",
        "depre_oilgas_bio": "油气资产 / 生物资产折旧",
        "amortize_useright": "使用权资产摊销",
        "symbol": "股票代码",
    },
    "financial_indicator": {
        "report_date": "报告期（期末日）",
        "gross_margin_pct": "销售毛利率 %",
        "net_margin_pct": "销售净利率 %",
        "operating_margin_pct": "营业利润率 %",
        "main_biz_margin_pct": "主营业务利润率 %",
        "roe_pct": "净资产收益率 %（摊薄）",
        "roe_weighted_pct": "净资产收益率 %（加权）",
        "roa_pct": "总资产报酬率 %",
        "roa_net_pct": "总资产净利率 %",
        "cost_profit_pct": "成本费用利润率 %",
        "revenue_yoy_pct": "营业总收入同比增长 %",
        "net_profit_yoy_pct": "净利润同比增长 %",
        "net_assets_yoy_pct": "净资产同比增长 %",
        "total_assets_yoy_pct": "总资产同比增长 %",
        "debt_ratio_pct": "资产负债率 %",
        "current_ratio": "流动比率",
        "quick_ratio": "速动比率",
        "cash_ratio_pct": "现金比率 %",
        "equity_multiplier_pct": "权益乘数 %",
        "receivable_turnover": "应收账款周转率（次）",
        "inventory_turnover": "存货周转率（次）",
        "asset_turnover": "总资产周转率（次）",
        "current_asset_turnover": "流动资产周转率（次）",
        "ocf_to_profit_pct": "净现比（经营现金流 / 净利润）%",
        "ocf_to_revenue_pct": "经营现金流 / 营业总收入 %",
        "cashflow_ratio_pct": "现金流量比率 %",
        "eps": "每股收益（元/股）",
        "bps": "每股净资产（元/股）",
        "ocf_per_share": "每股经营现金流（元/股）",
        "dividend_payout_pct": "股利支付率 %",
        "total_assets": "资产总计（冗余备份列，与 balance_sheet 同源）",
        "operating_revenue": "营业收入（冗余备份列）",
        "net_profit_parent": "归母净利润（冗余备份列）",
        "symbol": "股票代码",
    },
    "dividend": {
        "report_date": "报告期（分红对应报告期）",
        "dividend_per_10": "每 10 股派息（元）",
        "dividend_yield": "股息率（原始口径，小数）",
        "dividend_yield_pct": "股息率 %（对齐口径，下游统一用这列）",
        "dividend_per_share": "每股股息（元）",
        "total_shares": "总股本（股）",
        "ex_date": "除权除息日",
        "symbol": "股票代码",
    },
    "segments": {
        "symbol": "股票代码",
        "report_date": "报告期（期末日）",
        "category_type": "分类口径（按产品分类 / 按行业分类 / 按地区分类）",
        "segment_name": "业务条线名称（东财原始值，清洗见 cleaner._clean_segment_name）",
        "segment_revenue": "条线营业收入（元）",
        "segment_revenue_pct": "条线收入占比 %",
        "segment_cost": "条线营业成本（元）",
        "segment_profit": "条线利润（元）",
        "segment_margin": "条线毛利率 %",
    },
    "valuation": {
        "report_date": "估值日期（逐日）",
        "value": "指标值 —— indicator=market_cap 时为总市值（亿元）；indicator=pb 时为市净率（倍）",
        "indicator": "指标名（market_cap / pb）",
        "symbol": "股票代码（A 股 6 位；港股 5 位）",
    },
    "quote": {
        "name": "证券简称",
        "price": "最新价（A 股 = 元；港股 = 港元）",
        "pe": "市盈率（TTM）",
        "pb": "市净率",
        "market_cap": "总市值（亿元）",
        "price_52w_high": "52 周最高价",
        "price_52w_low": "52 周最低价",
        "dividend_yield": "股息率 %",
        "change": "涨跌额",
        "change_pct": "涨跌幅 %",
        "is_intraday": "是否盘中数据（False = 已收盘）",
        "report_date": "数据日期（抓取日；早期落盘的标的缺此列，UNION 后为 NULL）",
        "symbol": "股票代码（A 股 6 位；港股 5 位如 09992）",
    },
    "rating": {
        "symbol": "股票代码（A 股 6 位；港股 5 位）",
        "name": "证券简称",
        "rating_total": "近半年评级机构家数",
        "rating_buy": "买入评级家数",
        "rating_overweight": "增持评级家数",
        "rating_neutral": "中性评级家数",
        "rating_underweight": "减持评级家数",
        "rating_sell": "卖出评级家数",
        "eps_2025": "机构预测 2025 年每股收益（元/股）",
        "eps_2026": "机构预测 2026 年每股收益（元/股）",
        "eps_2027": "机构预测 2027 年每股收益（元/股）",
        "eps_2028": "机构预测 2028 年每股收益（元/股）",
        "target_price": "机构目标价（元）",
    },
    "competition": {
        "symbol": "股票代码（同行对比标的）",
        "name": "证券简称",
        "industry": "所属行业",
        "revenue_yi": "营业总收入（亿元）",
        "net_profit_yi": "净利润（亿元）",
        "report_date": "报告期（期末日）",
        "company_name": "公司全称",
        "company_intro": "公司简介",
    },
    "profile": {
        "symbol": "股票代码",
        "main_business": "主营业务（一句式概述）",
        "business_scope": "经营范围",
    },
}

# 表输出顺序：报表类 → 指标类 → 市场类 → 简介类（阅读顺序，非字母序）
TABLE_ORDER = [
    "profit_sheet", "balance_sheet", "cash_flow", "financial_indicator",
    "dividend", "segments", "valuation", "quote", "rating",
    "competition", "profile",
]

HEADER = """-- ============================================================================
-- fqf 基本面投研 · DuckDB schema（raw 层）
-- ============================================================================
-- 生成方式：**本文件由脚本自动生成，请勿手工编辑**
--     python scripts/gen_raw_schema.py
-- 事实源：data/warehouse/fqf.duckdb 的 raw.* 表（DuckDB 类型推断的真实结果）
-- 列注释：维护在 scripts/gen_raw_schema.py 的 COL_DOC 字典里（重跑不丢注释）
--
-- 【为什么 raw 层没有手写 DDL】
-- `src/data/warehouse.py:load_raw_layer()` 用 `pd.concat(join="outer")` 做跨标的
-- 列并集 + DuckDB 自动类型推断，直接 `CREATE OR REPLACE TABLE raw.{table}` 物化。
-- 表结构是「跑出来的」而非「声明出来的」——本文件是对该结果的**反向快照**，
-- 用于阅读、review 结构漂移（重跑本脚本 → git diff），不参与建表。
--
-- 【分层与存储介质】
--   raw 层：真源是 Parquet 文件 data/raw/{code}/{table}.parquet（每标的每表一个）；
--           DuckDB 里的 raw.* 是跨标的 UNION 的物化副本（每次构建 CREATE OR REPLACE）。
--   mart 层：只存在于 DuckDB 单文件 data/warehouse/fqf.duckdb，无 Parquet 镜像。
--
-- 【更新语义（季度数据刷新时是覆盖还是追加）】
--   覆盖式全历史 —— 接口每次返回**全历史**，整文件覆写 Parquet 后重建本表。
--                  → 单文件即完整历史，历史行不丢；但上游若重述，旧值会被静默改写。
--   覆盖式快照   —— 每次只落**当日一行**，覆写即丢历史
--                  → 故 quote 另有 data/market/{code}_quote.parquet 按日追加归档。
--
-- 【单位口径（raw 层不统一，务必逐列看注释）】
--   绝大多数金额列 = 元；`*.market_cap` / `valuation.value` / `*.revenue_yi` = 亿元；
--   股本列 = 股；所有 `*_pct` = %（非小数）。统一为「亿元」是 mart 层的职责。
-- ============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;

"""

FOOTER = """COMMIT;
"""


def _fetch_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """读取 raw schema 下的表名，按 TABLE_ORDER 排序（未收录的排最后）。"""
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'raw' ORDER BY table_name"
    ).fetchall()
    names = [r[0] for r in rows]
    known = [t for t in TABLE_ORDER if t in names]
    unknown = [t for t in names if t not in TABLE_ORDER]
    return known + unknown


def _fetch_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    """读取单表的 (列名, DuckDB 类型)，按物理顺序。"""
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _render_table(
    table: str,
    columns: list[tuple[str, str]],
    row_count: int,
) -> str:
    """渲染单表 DDL 段。"""
    meta = TABLE_META.get(table, {})
    width = max((len(c) for c, _ in columns), default=20)
    width = max(width, 16)

    lines: list[str] = []
    lines.append("-- ============================================================================")
    lines.append(f"-- raw.{table} —— {meta.get('title', '(未收录，待补业务说明)')}")
    lines.append("-- ============================================================================")
    lines.append(f"-- 数据来源：{meta.get('source', '—')}")
    lines.append(f"-- 粒度　　：{meta.get('grain', '—')}")
    lines.append(f"-- 单位　　：{meta.get('unit', '—')}")
    lines.append(f"-- 更新语义：{meta.get('fresh', '—')}")
    lines.append(f"-- 当前行数：{row_count}（跨全部已拉取标的）")
    lines.append(f"CREATE TABLE IF NOT EXISTS raw.{table} (")

    doc = COL_DOC.get(table, {})
    rendered = []
    last = len(columns) - 1
    for i, (col, dtype) in enumerate(columns):
        target = TYPE_MAP.get(dtype)
        if target is None:  # 未预期的类型：显式报错而非静默降级
            raise ValueError(f"{table}.{col} 出现未映射的 DuckDB 类型：{dtype}")
        # 末列不加逗号，注释另行追加以免吃掉逗号
        line = f"    {col.ljust(width)} {target}" + ("" if i == last else ",")
        note = doc.get(col, "")
        if note:
            line += f"  -- {note}"
        rendered.append(line)

    lines.extend(rendered)
    lines.append(");")
    lines.append("")
    return "\n".join(lines)


def generate(db_path: Path = DB_PATH) -> str:
    """生成完整 DDL 文本（DuckDB 方言）。"""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"数仓文件不存在：{db_path}（请先跑 scripts/fetch_stock.py + warehouse）")

    parts = [HEADER]

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = _fetch_tables(con)
        if not tables:
            raise RuntimeError("raw schema 为空 —— 请先执行 load_raw_layer()")
        for t in tables:
            cols = _fetch_columns(con, t)
            n = con.execute(f'SELECT COUNT(*) FROM raw."{t}"').fetchone()[0]
            parts.append(_render_table(t, cols, n))
    finally:
        con.close()

    parts.append(FOOTER)
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="从 DuckDB 反向生成 raw 层 DDL（DuckDB 方言）")
    ap.add_argument("--out", default=str(OUT_PATH), help="输出路径")
    ap.add_argument("--stdout", action="store_true", help="打印到终端，不写文件")
    ap.add_argument("--check", action="store_true", help="只比对是否与已落盘文件一致（漂移检测）")
    args = ap.parse_args()

    try:
        sql = generate()
    except Exception as e:
        print(f"✗ 生成失败：{e}", file=sys.stderr)
        return 1

    if args.stdout:
        print(sql)
        return 0

    out = Path(args.out)
    if args.check:
        if not out.exists():
            print(f"✗ 漂移：{out} 不存在（请运行 python scripts/gen_raw_schema.py 生成）")
            return 1
        old = out.read_text(encoding="utf-8")
        # 行数会随数据刷新变化，比对时剔除，只比结构
        strip = lambda s: "\n".join(  # noqa: E731
            ln for ln in s.splitlines() if not ln.startswith("-- 当前行数")
        )
        if strip(old) != strip(sql):
            print(f"✗ 漂移：raw 层表结构已变化，请重跑 python scripts/gen_raw_schema.py 并 review diff")
            return 1
        print(f"✓ 无漂移：{out} 与 DuckDB 当前 raw schema 一致")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8")
    n_tables = sql.count("CREATE TABLE IF NOT EXISTS")
    n_cols = sum(len(v) for v in COL_DOC.values())
    print(f"✓ 已写出 {out}（{n_tables} 张表，{len(sql.splitlines())} 行，{n_cols} 条列注释）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
