-- ============================================================================
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


-- ============================================================================
-- raw.profit_sheet —— 利润表（含营业总收入 / 归母净利 / 各项费用）
-- ============================================================================
-- 数据来源：东财 · 财务报表
-- 粒度　　：(symbol, report_date)
-- 单位　　：金额列 = 元；eps = 元/股；比率列 = %
-- 更新语义：覆盖式全历史
-- 当前行数：586（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.profit_sheet (
    report_date            TIMESTAMP,  -- 报告期（期末日）
    report_type            VARCHAR,  -- 报表类型（年报 / 中报 / 季报）
    revenue                DOUBLE,  -- 营业总收入 —— 利润表第一行，对外与媒体引用口径
    revenue_yoy_pct        DOUBLE,  -- 营业总收入同比 %
    operating_revenue      DOUBLE,  -- 营业收入 —— 利润表「其中：」明细项，与营业成本配对
    operating_cost         DOUBLE,  -- 营业成本
    total_operating_cost   DOUBLE,  -- 营业总成本
    operate_tax_add        DOUBLE,  -- 税金及附加
    operating_profit       DOUBLE,  -- 营业利润
    non_operating_income   DOUBLE,  -- 营业外收入
    non_operating_expense  DOUBLE,  -- 营业外支出
    total_profit           DOUBLE,  -- 利润总额
    income_tax             DOUBLE,  -- 所得税费用
    net_profit             DOUBLE,  -- 净利润（含少数股东）
    net_profit_yoy_pct     DOUBLE,  -- 净利润同比 %
    net_profit_parent      DOUBLE,  -- 归属于母公司股东的净利润
    minority_interest      DOUBLE,  -- 少数股东损益
    deduct_net_profit      DOUBLE,  -- 扣非归母净利润
    sell_expense           DOUBLE,  -- 销售费用
    admin_expense          DOUBLE,  -- 管理费用
    research_expense       DOUBLE,  -- 研发费用
    finance_expense        DOUBLE,  -- 财务费用
    interest_expense       DOUBLE,  -- 利息费用（财务费用明细）
    invest_income          DOUBLE,  -- 投资收益
    asset_impairment_loss  DOUBLE,  -- 资产减值损失
    credit_impairment_loss DOUBLE,  -- 信用减值损失
    symbol                 VARCHAR,  -- 股票代码（A 股 6 位；港股为 5 位如 00700）
    eps                    DOUBLE  -- 每股收益（元/股）
);

-- ============================================================================
-- raw.balance_sheet —— 资产负债表（资产 / 负债 / 权益，含银保专用科目）
-- ============================================================================
-- 数据来源：东财 · 财务报表
-- 粒度　　：(symbol, report_date)
-- 单位　　：金额列 = 元；股本列 = 股
-- 更新语义：覆盖式全历史
-- 当前行数：572（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.balance_sheet (
    report_date                  TIMESTAMP,  -- 报告期（期末日）
    report_type                  VARCHAR,  -- 报表类型（年报 / 中报 / 季报）
    total_assets                 DOUBLE,  -- 资产总计
    total_liabilities            DOUBLE,  -- 负债合计
    total_equity                 DOUBLE,  -- 归属于母公司股东权益合计
    total_equity_all             DOUBLE,  -- 股东权益合计（含少数股东）
    current_assets               DOUBLE,  -- 流动资产合计
    noncurrent_assets            DOUBLE,  -- 非流动资产合计
    monetary_funds               DOUBLE,  -- 货币资金
    inventory                    DOUBLE,  -- 存货
    accounts_receivable          DOUBLE,  -- 应收账款
    goodwill                     DOUBLE,  -- 商誉
    fixed_assets                 DOUBLE,  -- 固定资产
    construction_in_progress     DOUBLE,  -- 在建工程
    intangible_assets            DOUBLE,  -- 无形资产
    long_equity_invest           DOUBLE,  -- 长期股权投资
    other_noncurrent_assets      DOUBLE,  -- 其他非流动资产
    minority_equity              DOUBLE,  -- 少数股东权益
    borrowings                   DOUBLE,  -- 借款合计（短期 + 长期）
    long_term_loan               DOUBLE,  -- 长期借款
    short_term_loan              DOUBLE,  -- 短期借款
    share_capital                DOUBLE,  -- 实收资本（股本）—— 单位：股
    preferred_shares             DOUBLE,  -- 优先股
    audit_opinion                VARCHAR,  -- 审计意见类型（东财 OPINION_TYPE）
    current_liabilities          DOUBLE,  -- 流动负债合计
    accounts_payable             DOUBLE,  -- 应付账款
    other_current_assets         DOUBLE,  -- 其他流动资产
    other_current_liabilities    DOUBLE,  -- 其他流动负债
    noncurrent_liab_1y           DOUBLE,  -- 一年内到期的非流动负债
    retained_profit              DOUBLE,  -- 未分配利润
    bond_payable                 DOUBLE,  -- 应付债券
    long_payable                 DOUBLE,  -- 长期应付款
    lease_liabilities            DOUBLE,  -- 租赁负债
    short_bond_payable           DOUBLE,  -- 应付短期债券
    noncurrent_liabilities       DOUBLE,  -- 非流动负债合计
    notes_receivable             DOUBLE,  -- 应收票据
    prepayments                  DOUBLE,  -- 预付款项
    other_receivables            DOUBLE,  -- 其他应收款
    trading_financial_assets     DOUBLE,  -- 交易性金融资产
    contract_assets              DOUBLE,  -- 合同资产
    noncurrent_asset_1y          DOUBLE,  -- 一年内到期的非流动资产
    dividend_receivable          DOUBLE,  -- 应收股利
    interest_receivable          DOUBLE,  -- 应收利息
    finance_receivables          DOUBLE,  -- 应收款项融资
    invest_realestate            DOUBLE,  -- 投资性房地产
    useright_asset               DOUBLE,  -- 使用权资产
    long_prepaid_expense         DOUBLE,  -- 长期待摊费用
    defer_tax_asset              DOUBLE,  -- 递延所得税资产
    other_equity_invest          DOUBLE,  -- 其他权益工具投资
    other_noncurrent_finasset    DOUBLE,  -- 其他非流动金融资产
    other_creditor_invest        DOUBLE,  -- 其他债权投资
    hold_maturity_invest         DOUBLE,  -- 持有至到期投资
    notes_payable                DOUBLE,  -- 应付票据
    contract_liabilities         DOUBLE,  -- 合同负债
    staff_salary_payable         DOUBLE,  -- 应付职工薪酬
    tax_payable                  DOUBLE,  -- 应交税费
    advance_receivables          DOUBLE,  -- 预收款项
    other_payables               DOUBLE,  -- 其他应付款
    dividend_payable             DOUBLE,  -- 应付股利
    interest_payable             DOUBLE,  -- 应付利息
    other_noncurrent_liabilities DOUBLE,  -- 其他非流动负债
    defer_tax_liabilities        DOUBLE,  -- 递延所得税负债
    long_staff_salary_payable    DOUBLE,  -- 长期应付职工薪酬
    perpetual_bond               DOUBLE,  -- 永续债
    predict_liabilities          DOUBLE,  -- 预计负债
    lend_fund                    DOUBLE,  -- 发放贷款及垫款（银行专用）
    loan_advance                 DOUBLE,  -- 贷款及垫款（银行专用）
    buy_resale_finasset          DOUBLE,  -- 买入返售金融资产（银行专用）
    derivative_finasset          DOUBLE,  -- 衍生金融资产（银行专用）
    settle_excess_reserve        DOUBLE,  -- 结算备付金（证券专用）
    accept_deposit_interbank     DOUBLE,  -- 同业及其他金融机构存放款项（银行专用）
    derivative_finliab           DOUBLE,  -- 衍生金融负债（银行专用）
    sell_repo_finasset           DOUBLE,  -- 卖出回购金融资产款（银行专用）
    fee_commission_payable       DOUBLE,  -- 应付手续费及佣金（银行专用）
    symbol                       VARCHAR,  -- 股票代码（A 股 6 位；港股为 5 位）
    share_capital_raw            DOUBLE,  -- 股本（原始口径，备用）
    creditor_invest              DOUBLE,  -- 债权投资（银行专用）
    amortize_cost_finasset       DOUBLE,  -- 以摊余成本计量的金融资产（银行专用）
    fvtoci_finasset              DOUBLE,  -- 以公允价值计量且其变动计入其他综合收益的金融资产（银行专用）
    loan_pbc                     DOUBLE,  -- 向中央银行借款（银行专用）
    accept_deposit               DOUBLE,  -- 吸收存款（银行专用）
    iofi_deposit                 DOUBLE,  -- 同业及其他金融机构存放（银行专用）
    cash_deposit_pbc             DOUBLE,  -- 存放中央银行款项（银行专用）
    deposit_interbank            DOUBLE,  -- 存放同业款项（银行专用）
    precious_metal               DOUBLE,  -- 贵金属（银行专用）
    other_asset                  DOUBLE,  -- 其他资产
    deposit_certificate          DOUBLE  -- 存单（银行专用）
);

-- ============================================================================
-- raw.cash_flow —— 现金流量表（经营 / 投资 / 筹资 + 折旧摊销明细）
-- ============================================================================
-- 数据来源：东财 · 财务报表
-- 粒度　　：(symbol, report_date)
-- 单位　　：金额列 = 元
-- 更新语义：覆盖式全历史
-- 当前行数：563（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.cash_flow (
    report_date             TIMESTAMP,  -- 报告期（期末日）
    report_type             VARCHAR,  -- 报表类型（年报 / 中报 / 季报）
    operating_cash_inflow   DOUBLE,  -- 经营活动现金流入小计
    operating_cash_outflow  DOUBLE,  -- 经营活动现金流出小计
    ocf                     DOUBLE,  -- 经营活动产生的现金流量净额
    investing_cash_inflow   DOUBLE,  -- 投资活动现金流入小计
    investing_cash_outflow  DOUBLE,  -- 投资活动现金流出小计
    icf                     DOUBLE,  -- 投资活动产生的现金流量净额
    financing_cash_inflow   DOUBLE,  -- 筹资活动现金流入小计
    financing_cash_outflow  DOUBLE,  -- 筹资活动现金流出小计
    financing_cash_flow     DOUBLE,  -- 筹资活动产生的现金流量净额
    depreciation            DOUBLE,  -- 固定资产折旧
    capital_expenditure     DOUBLE,  -- 购建固定资产、无形资产和其他长期资产支付的现金（资本开支）
    amortize_intangible     DOUBLE,  -- 无形资产摊销
    amortize_lpe            DOUBLE,  -- 长期待摊费用摊销
    depre_invest_realestate DOUBLE,  -- 投资性房地产折旧
    depre_oilgas_bio        DOUBLE,  -- 油气资产 / 生物资产折旧
    amortize_useright       DOUBLE,  -- 使用权资产摊销
    symbol                  VARCHAR  -- 股票代码
);

-- ============================================================================
-- raw.financial_indicator —— 财务指标（盈利能力 / 偿债 / 营运 / 每股）
-- ============================================================================
-- 数据来源：东财 · 财务分析指标
-- 粒度　　：(symbol, report_date)
-- 单位　　：比率列 = %，金额列 = 元
-- 更新语义：覆盖式全历史
-- 当前行数：390（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.financial_indicator (
    report_date            TIMESTAMP,  -- 报告期（期末日）
    gross_margin_pct       DOUBLE,  -- 销售毛利率 %
    net_margin_pct         DOUBLE,  -- 销售净利率 %
    operating_margin_pct   DOUBLE,  -- 营业利润率 %
    main_biz_margin_pct    DOUBLE,  -- 主营业务利润率 %
    roe_pct                DOUBLE,  -- 净资产收益率 %（摊薄）
    roe_weighted_pct       DOUBLE,  -- 净资产收益率 %（加权）
    roa_pct                DOUBLE,  -- 总资产报酬率 %
    roa_net_pct            DOUBLE,  -- 总资产净利率 %
    cost_profit_pct        DOUBLE,  -- 成本费用利润率 %
    revenue_yoy_pct        DOUBLE,  -- 营业总收入同比增长 %
    net_profit_yoy_pct     DOUBLE,  -- 净利润同比增长 %
    net_assets_yoy_pct     DOUBLE,  -- 净资产同比增长 %
    total_assets_yoy_pct   DOUBLE,  -- 总资产同比增长 %
    debt_ratio_pct         DOUBLE,  -- 资产负债率 %
    current_ratio          DOUBLE,  -- 流动比率
    quick_ratio            DOUBLE,  -- 速动比率
    cash_ratio_pct         DOUBLE,  -- 现金比率 %
    equity_multiplier_pct  DOUBLE,  -- 权益乘数 %
    receivable_turnover    DOUBLE,  -- 应收账款周转率（次）
    inventory_turnover     DOUBLE,  -- 存货周转率（次）
    asset_turnover         DOUBLE,  -- 总资产周转率（次）
    current_asset_turnover DOUBLE,  -- 流动资产周转率（次）
    ocf_to_profit_pct      DOUBLE,  -- 净现比（经营现金流 / 净利润）%
    ocf_to_revenue_pct     DOUBLE,  -- 经营现金流 / 营业总收入 %
    cashflow_ratio_pct     DOUBLE,  -- 现金流量比率 %
    eps                    DOUBLE,  -- 每股收益（元/股）
    bps                    DOUBLE,  -- 每股净资产（元/股）
    ocf_per_share          DOUBLE,  -- 每股经营现金流（元/股）
    dividend_payout_pct    DOUBLE,  -- 股利支付率 %
    total_assets           DOUBLE,  -- 资产总计（冗余备份列，与 balance_sheet 同源）
    symbol                 VARCHAR,  -- 股票代码
    operating_revenue      DOUBLE,  -- 营业收入（冗余备份列）
    net_profit_parent      DOUBLE  -- 归母净利润（冗余备份列）
);

-- ============================================================================
-- raw.dividend —— 分红送配（每股股息 / 股息率 / 除权日）
-- ============================================================================
-- 数据来源：东财 · 分红送配
-- 粒度　　：(symbol, report_date)
-- 单位　　：dividend_per_10 = 元/10股；dividend_per_share = 元/股；股息率 = %；total_shares = 股
-- 更新语义：覆盖式全历史
-- 当前行数：172（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.dividend (
    report_date        TIMESTAMP,  -- 报告期（分红对应报告期）
    dividend_per_10    DOUBLE,  -- 每 10 股派息（元）
    dividend_yield     DOUBLE,  -- 股息率（原始口径，小数）
    total_shares       DOUBLE,  -- 总股本（股）
    symbol             VARCHAR,  -- 股票代码
    dividend_yield_pct DOUBLE,  -- 股息率 %（对齐口径，下游统一用这列）
    dividend_per_share DOUBLE,  -- 每股股息（元）
    ex_date            TIMESTAMP  -- 除权除息日
);

-- ============================================================================
-- raw.segments —— 主营构成（分产品 / 分行业收入与毛利率）
-- ============================================================================
-- 数据来源：东财 · 主营构成
-- 粒度　　：(symbol, report_date, category_type, segment_name)
-- 单位　　：金额列 = 元；segment_margin = %；segment_revenue_pct = %
-- 更新语义：覆盖式全历史
-- 当前行数：926（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.segments (
    symbol              VARCHAR,  -- 股票代码
    report_date         TIMESTAMP,  -- 报告期（期末日）
    category_type       VARCHAR,  -- 分类口径（按产品分类 / 按行业分类 / 按地区分类）
    segment_name        VARCHAR,  -- 业务条线名称（东财原始值，清洗见 cleaner._clean_segment_name）
    segment_revenue     DOUBLE,  -- 条线营业收入（元）
    segment_revenue_pct DOUBLE,  -- 条线收入占比 %
    segment_cost        DOUBLE,  -- 条线营业成本（元）
    segment_profit      DOUBLE,  -- 条线利润（元）
    segment_margin      DOUBLE  -- 条线毛利率 %
);

-- ============================================================================
-- raw.valuation —— 估值历史长表（百度股市通，近十年逐日）
-- ============================================================================
-- 数据来源：百度股市通
-- 粒度　　：(symbol, indicator, report_date)
-- 单位　　：value 列 = 亿元（market_cap）/ 倍（pb）—— 按 indicator 区分含义
-- 更新语义：覆盖式全历史
-- 当前行数：14081（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.valuation (
    report_date      TIMESTAMP,  -- 估值日期（逐日）
    value            DOUBLE,  -- 指标值 —— indicator=market_cap 时为总市值（亿元）；indicator=pb 时为市净率（倍）
    indicator        VARCHAR,  -- 指标名（market_cap / pb）
    symbol           VARCHAR  -- 股票代码（A 股 6 位；港股 5 位）
);

-- ============================================================================
-- raw.quote —— 行情快照（现价 / PE / PB / 市值 / 52周，单行）
-- ============================================================================
-- 数据来源：腾讯行情
-- 粒度　　：(symbol)
-- 单位　　：price = 元（港股为港元）；market_cap = 亿元；比率列 = %
-- 更新语义：覆盖式快照（历史归档见 data/market/{code}_quote.parquet）
-- 当前行数：8（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.quote (
    name             VARCHAR,  -- 证券简称
    price            DOUBLE,  -- 最新价（A 股 = 元；港股 = 港元）
    pe               DOUBLE,  -- 市盈率（TTM）
    pb               DOUBLE,  -- 市净率
    market_cap       DOUBLE,  -- 总市值（亿元）
    price_52w_high   DOUBLE,  -- 52 周最高价
    price_52w_low    DOUBLE,  -- 52 周最低价
    symbol           VARCHAR,  -- 股票代码（A 股 6 位；港股 5 位如 09992）
    dividend_yield   DOUBLE,  -- 股息率 %
    report_date      TIMESTAMP,  -- 数据日期（抓取日；早期落盘的标的缺此列，UNION 后为 NULL）
    change           DOUBLE,  -- 涨跌额
    change_pct       DOUBLE,  -- 涨跌幅 %
    is_intraday      BOOLEAN  -- 是否盘中数据（False = 已收盘）
);

-- ============================================================================
-- raw.rating —— 机构评级（评级分布 / 未来三年 EPS 预测 / 目标价）
-- ============================================================================
-- 数据来源：东财 · 机构评级
-- 粒度　　：(symbol)
-- 单位　　：eps_* = 元/股；target_price = 元；评级列 = 家数
-- 更新语义：覆盖式快照（无历史归档）
-- 当前行数：8（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.rating (
    symbol             VARCHAR,  -- 股票代码（A 股 6 位；港股 5 位）
    name               VARCHAR,  -- 证券简称
    rating_total       BIGINT,  -- 近半年评级机构家数
    rating_buy         DOUBLE,  -- 买入评级家数
    rating_overweight  DOUBLE,  -- 增持评级家数
    rating_neutral     DOUBLE,  -- 中性评级家数
    rating_underweight DOUBLE,  -- 减持评级家数
    rating_sell        DOUBLE,  -- 卖出评级家数
    eps_2025           DOUBLE,  -- 机构预测 2025 年每股收益（元/股）
    eps_2026           DOUBLE,  -- 机构预测 2026 年每股收益（元/股）
    eps_2027           DOUBLE,  -- 机构预测 2027 年每股收益（元/股）
    eps_2028           DOUBLE,  -- 机构预测 2028 年每股收益（元/股）
    target_price       DOUBLE  -- 机构目标价（元）
);

-- ============================================================================
-- raw.competition —— 同行比较（同行业公司的营收 / 净利）
-- ============================================================================
-- 数据来源：东财 · 同行比较
-- 粒度　　：(symbol, report_date)
-- 单位　　：revenue_yi / net_profit_yi = 亿元（列名已带 _yi）
-- 更新语义：覆盖式全历史
-- 当前行数：106（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.competition (
    symbol           VARCHAR,  -- 股票代码（同行对比标的）
    name             VARCHAR,  -- 证券简称
    industry         VARCHAR,  -- 所属行业
    revenue_yi       DOUBLE,  -- 营业总收入（亿元）
    net_profit_yi    DOUBLE,  -- 净利润（亿元）
    report_date      TIMESTAMP,  -- 报告期（期末日）
    company_name     VARCHAR,  -- 公司全称
    company_intro    VARCHAR  -- 公司简介
);

-- ============================================================================
-- raw.profile —— 公司简介（主营业务 / 经营范围）
-- ============================================================================
-- 数据来源：东财 · 公司概况
-- 粒度　　：(symbol)
-- 单位　　：—
-- 更新语义：覆盖式静态（低频，随财报季一并刷新）
-- 当前行数：5（跨全部已拉取标的）
CREATE TABLE IF NOT EXISTS raw.profile (
    symbol           VARCHAR,  -- 股票代码
    main_business    VARCHAR,  -- 主营业务（一句式概述）
    business_scope   VARCHAR  -- 经营范围
);

COMMIT;
