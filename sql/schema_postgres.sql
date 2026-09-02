-- ============================================================================
-- fqf 基本面投研 · PostgreSQL 数仓 schema（mart 层）
-- ============================================================================
-- 定位：DuckDB 单文件数仓的「关系型数据库」等价 schema，证明
--       「schema 设计与 PG / Snowflake / BigQuery 兼容」。
--
-- 列结构与 `src/data/warehouse.py` 的 DuckDB mart 层、以及
-- `docs/cloud-deployment.md` 的 BigQuery DDL 完全一致（仅方言不同）。
-- 字段来源：`src/data/cleaner.py` 的 build_annual_financials /
--           build_quarter_financials 真实输出列。
--
-- 金额口径：统一「亿元」；比率口径：统一「%」。
-- 用法：psql -f sql/schema_postgres.sql
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- schema：raw（原始） + mart（报告指标宽表）
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS mart;

-- ============================================================================
-- mart.annual_financials —— 年度财务宽表（全历史 + 派生指标，金额亿元）
-- 主键：(symbol, report_date)
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.annual_financials (
    -- 维度
    symbol                   VARCHAR(10)  NOT NULL,   -- 股票代码
    report_date              DATE         NOT NULL,   -- 年报期末（12-31）

    -- 利润表（亿元 / %）
    operating_revenue        NUMERIC(20, 4),          -- 营业收入
    net_profit               NUMERIC(20, 4),          -- 净利润
    net_profit_parent        NUMERIC(20, 4),          -- 归母净利润
    gross_margin_pct         NUMERIC(10, 4),          -- 毛利率 %
    sell_expense             NUMERIC(20, 4),          -- 销售费用
    admin_expense            NUMERIC(20, 4),          -- 管理费用
    income_tax               NUMERIC(20, 4),          -- 所得税费用
    interest_expense         NUMERIC(20, 4),          -- 利息费用
    total_profit             NUMERIC(20, 4),          -- 利润总额

    -- 现金流（亿元）
    ocf                      NUMERIC(20, 4),          -- 经营现金流净额
    depreciation             NUMERIC(20, 4),          -- 固定资产折旧
    capital_expenditure      NUMERIC(20, 4),          -- 资本开支
    amortize_intangible      NUMERIC(20, 4),          -- 无形资产摊销
    amortize_lpe             NUMERIC(20, 4),          -- 长期待摊费用摊销
    depre_invest_realestate  NUMERIC(20, 4),          -- 投资性房地产折旧
    depre_oilgas_bio         NUMERIC(20, 4),          -- 油气/生物资产折旧
    amortize_useright        NUMERIC(20, 4),          -- 使用权资产摊销

    -- 资产负债表（亿元 / %）
    total_assets             NUMERIC(20, 4),          -- 总资产
    total_liabilities        NUMERIC(20, 4),          -- 总负债
    total_equity             NUMERIC(20, 4),          -- 归母净资产
    total_equity_all         NUMERIC(20, 4),          -- 全部股东权益
    current_assets           NUMERIC(20, 4),          -- 流动资产
    monetary_funds           NUMERIC(20, 4),          -- 货币资金
    inventory                NUMERIC(20, 4),          -- 存货
    accounts_receivable      NUMERIC(20, 4),          -- 应收账款
    interest_bearing_debt    NUMERIC(20, 4),          -- 有息负债（长借+短借）
    goodwill                 NUMERIC(20, 4),          -- 商誉
    share_capital            NUMERIC(20, 4),          -- 普通股股本（亿股）
    preferred_shares         NUMERIC(20, 4),          -- 优先股股本（亿股）
    audit_opinion            TEXT,                    -- 审计意见（东财 OPINION_TYPE）
    current_liabilities      NUMERIC(20, 4),          -- 流动负债
    accounts_payable         NUMERIC(20, 4),          -- 应付账款
    other_current_assets     NUMERIC(20, 4),          -- 其他流动资产
    other_current_liabilities NUMERIC(20, 4),         -- 其他流动负债
    noncurrent_liab_1y       NUMERIC(20, 4),          -- 一年内到期非流动负债
    retained_profit          NUMERIC(20, 4),          -- 未分配利润
    bond_payable             NUMERIC(20, 4),          -- 应付债券
    long_payable             NUMERIC(20, 4),          -- 长期应付款
    lease_liabilities        NUMERIC(20, 4),          -- 租赁负债
    short_bond_payable       NUMERIC(20, 4),          -- 应付短期债券
    noncurrent_liabilities   NUMERIC(20, 4),          -- 非流动负债
    long_term_debt           NUMERIC(20, 4),          -- 长期债务（ValueLine 口径）
    total_debt               NUMERIC(20, 4),          -- 总债务（完整有息负债）

    -- 财务指标（% / 倍）
    net_margin_pct           NUMERIC(10, 4),          -- 净利率 %
    roe_pct                  NUMERIC(10, 4),          -- ROE %
    roe_weighted_pct         NUMERIC(10, 4),          -- 加权 ROE %
    debt_ratio_pct           NUMERIC(10, 4),          -- 资产负债率 %
    revenue_yoy_pct          NUMERIC(10, 4),          -- 营收同比 %
    net_profit_yoy_pct       NUMERIC(10, 4),          -- 净利同比 %
    ocf_to_profit_pct        NUMERIC(10, 4),          -- 净现比 %
    current_ratio            NUMERIC(10, 4),          -- 流动比率
    quick_ratio              NUMERIC(10, 4),          -- 速动比率

    -- 分红（亿元 / % / 元）
    dividend_per_10          NUMERIC(10, 4),          -- 每10股派息（元）
    dividend_yield_pct       NUMERIC(10, 4),          -- 股息率 %
    dividend_total           NUMERIC(20, 4),          -- 分红总额（亿元）
    dividend_payout_pct      NUMERIC(10, 4),          -- 分红比例（股利支付率）%
    dividend_per_share       NUMERIC(10, 4),          -- 每股股息（元）

    -- ValueLine 派生指标
    working_capital          NUMERIC(20, 4),          -- 营运资本（流动资产-流动负债）
    depreciation_amortization NUMERIC(20, 4),         -- 折旧摊销总额
    income_tax_rate          NUMERIC(10, 4),          -- 所得税率 %
    retained_to_equity       NUMERIC(10, 4),          -- 留存收益占比 %
    rotc                     NUMERIC(10, 4),          -- 总资本回报率 %

    PRIMARY KEY (symbol, report_date)
);

-- 索引：单股按报告期倒序（最常查询模式）
CREATE INDEX IF NOT EXISTS idx_annual_symbol_date
    ON mart.annual_financials (symbol, report_date DESC);

-- ============================================================================
-- mart.quarter_financials —— 季度财务宽表（近 8 季，单季口径，金额亿元）
-- 主键：(symbol, report_date)
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.quarter_financials (
    -- 维度
    symbol                   VARCHAR(10)  NOT NULL,
    report_date              DATE         NOT NULL,   -- 季末（03-31/06-30/09-30/12-31）

    -- 利润表单季（亿元 / %）
    operating_revenue        NUMERIC(20, 4),          -- 单季营业收入
    net_profit_parent        NUMERIC(20, 4),          -- 单季归母净利润
    gross_margin_pct         NUMERIC(10, 4),          -- 单季毛利率 %（银行等无营业成本为 NULL）
    net_margin_pct           NUMERIC(10, 4),          -- 单季净利率 %

    -- 现金流单季
    ocf                      NUMERIC(20, 4),          -- 单季经营现金流

    -- 资产负债表季末（亿元）
    total_assets             NUMERIC(20, 4),
    total_liabilities        NUMERIC(20, 4),
    total_equity             NUMERIC(20, 4),          -- 归母净资产
    monetary_funds           NUMERIC(20, 4),
    inventory                NUMERIC(20, 4),
    accounts_receivable      NUMERIC(20, 4),
    interest_bearing_debt    NUMERIC(20, 4),
    goodwill                 NUMERIC(20, 4),

    -- 派生（%）
    roe_pct                  NUMERIC(10, 4),          -- 单季 ROE = 单季归母净利/季末归母净资产

    PRIMARY KEY (symbol, report_date)
);

CREATE INDEX IF NOT EXISTS idx_quarter_symbol_date
    ON mart.quarter_financials (symbol, report_date DESC);

-- ============================================================================
-- mart.segments —— 分业务收入构成（长表，近 2 年，金额亿元）
-- 主键：(symbol, report_date, segment_name)
-- ============================================================================
CREATE TABLE IF NOT EXISTS mart.segments (
    symbol                   VARCHAR(10)  NOT NULL,
    report_date              DATE         NOT NULL,
    segment_name             VARCHAR(64)  NOT NULL,   -- 业务条线名（清洗后）
    category_type            VARCHAR(16),             -- 按产品分类 / 按行业分类
    revenue_yi               NUMERIC(20, 4),          -- 条线收入（亿元）
    margin_pct               NUMERIC(10, 4),          -- 条线利润率 %

    PRIMARY KEY (symbol, report_date, segment_name)
);

CREATE INDEX IF NOT EXISTS idx_segments_symbol_date
    ON mart.segments (symbol, report_date DESC);

COMMIT;

-- ============================================================================
-- 附：raw 层说明
-- ============================================================================
-- raw 层（东财口径原始表，零加工）在 DuckDB 中为 11 张表，跨股票 UNION：
--   financial_indicator / profit_sheet / balance_sheet / cash_flow /
--   dividend / segments / valuation / quote / rating / competition / profile
--
-- 在 PG 中对应 11 张表（raw.{table}），列结构与 DuckDB 挂载完全一致。
-- 此处不逐表展开（列多且随数据源变化），关键是「raw 零加工、可追溯」的分层原则。
