"""数据适配层：把 cleaner 输出的宽表，转成模板渲染所需的结构。

模板结构（build_valueline.py 消费）：
- YEARS / FINANCIALS：年度表，FINANCIALS 每项为 (分组 或 None, 指标名, [格式化字符串])
- QUARTER_LABELS / QUARTERLY：季度表，同上
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .cleaner import build_annual_financials, build_quarter_financials, build_segments, build_valuation
from ..analysis.fraud import fraud_check


# 业务条线调色板（按收入降序循环分配，适配任意条线数）
SEGMENT_PALETTE = ["#378ADD", "#E24B4A", "#BA7517", "#888780", "#5B8FF9", "#F6903D", "#61A0A8", "#9270CA"]


def _norm_code(code: str) -> str:
    """代码规范化：港股剥 .HK 后缀 zfill 5（如 00700.HK→00700），A 股 zfill 6（601088）。

    与 fetcher._hk_code 同构，但避免循环导入，独立实现。
    """
    c = str(code).upper().strip()
    if c.endswith(".HK"):
        return c[:-3].zfill(5)
    bare = c.split(".")[0]
    if bare.startswith("0") and len(bare) == 5:
        return bare.zfill(5)
    return bare.zfill(6)


# ---------------------------------------------------------------------------
# 字段映射：(分组, 模板指标名, 宽表字段名, 小数位)
# ---------------------------------------------------------------------------
ANNUAL_SPEC = [
    ("利润表", None, None, None),
    (None, "营业收入（亿元）", "operating_revenue", 1),
    (None, "归母净利润（亿元）", "net_profit_parent", 1),
    (None, "毛利率 %", "gross_margin_pct", 1),
    (None, "净利率 %", "net_margin_pct", 1),
    (None, "经营现金流净额（亿元）", "ocf", 1),
    (None, "折旧与摊销（亿元）", "depreciation_amortization", 1),
    (None, "资本开支（亿元）", "capital_expenditure", 1),
    (None, "所得税率 %", "income_tax_rate", 1),
    (None, "ROE（摊薄）%", "roe_pct", 1),
    (None, "ROTC（总资本回报）%", "rotc", 1),
    ("资产负债表", None, None, None),
    (None, "总资产（亿元）", "total_assets", 1),
    (None, "总负债（亿元）", "total_liabilities", 1),
    (None, "净资产（归母）（亿元）", "total_equity", 1),
    (None, "营运资本（亿元）", "working_capital", 1),
    (None, "货币资金（亿元）", "monetary_funds", 1),
    (None, "存货（亿元）", "inventory", 1),
    (None, "应收账款（亿元）", "accounts_receivable", 1),
    (None, "长期债务（亿元）", "long_term_debt", 1),
    (None, "总债务（有息）（亿元）", "total_debt", 1),
    (None, "商誉（亿元）", "goodwill", 1),
    ("股本结构", None, None, None),
    (None, "普通股数量（亿股）", "total_shares_yi", 2),
    (None, "优先股数量（亿股）", "preferred_shares_yi", 2),
    ("股东回报", None, None, None),
    (None, "每股股息（元）", "dividend_per_share", 2),
    (None, "分红比例 %", "dividend_payout_pct", 1),
    (None, "股息率 %", "dividend_yield_pct", 1),
    (None, "留存收益/普通股权益 %", "retained_to_equity", 1),
]

QUARTER_SPEC = [
    ("利润表（单季）", None, None, None),
    (None, "营业收入（亿元）", "operating_revenue", 1),
    (None, "归母净利润（亿元）", "net_profit_parent", 1),
    (None, "毛利率 %", "gross_margin_pct", 1),
    (None, "净利率 %", "net_margin_pct", 1),
    (None, "经营现金流净额（亿元）", "ocf", 1),
    (None, "ROE（单季）%", "roe_pct", 1),
    ("资产负债表（季末）", None, None, None),
    (None, "总资产（亿元）", "total_assets", 1),
    (None, "总负债（亿元）", "total_liabilities", 1),
    (None, "净资产（归母）（亿元）", "total_equity", 1),
    (None, "货币资金（亿元）", "monetary_funds", 1),
    (None, "存货（亿元）", "inventory", 1),
    (None, "应收账款（亿元）", "accounts_receivable", 1),
    (None, "有息负债（亿元）", "interest_bearing_debt", 1),
    (None, "商誉（亿元）", "goodwill", 1),
    ("股本结构（季末）", None, None, None),
    (None, "普通股数量（亿股）", "total_shares_yi", 2),
    (None, "优先股数量（亿股）", "preferred_shares_yi", 2),
]


def _clean(v):
    """NaN / inf → None。"""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _fmt(v, digits=1) -> str:
    """数值 → 展示字符串（None → —）。"""
    if v is None:
        return "—"
    return f"{float(v):.{digits}f}"


def _extract(df: pd.DataFrame, spec) -> list[tuple]:
    """按 spec 从宽表提取模板结构（值已格式化为字符串）。"""
    rows = []
    for group, name, field, digits in spec:
        if group:
            rows.append((group, None, None))
            continue
        if field in df.columns:
            vals = [_fmt(_clean(v), digits) for v in df[field].tolist()]
        else:
            vals = ["—"] * len(df)
        rows.append((None, name, vals))
    return rows


def _q_label(dt) -> str:
    """report_date → 季度标签，如 2024-09-30 → 24Q3。"""
    y = str(dt.year)[2:]
    q = (dt.month - 1) // 3 + 1
    return f"{y}Q{q}"


def _period_label(dt, half_yearly: bool = False) -> str:
    """report_date → 报告期标签；half_yearly=True 时 6 月/12 月显示 H1/H2（港股半年度）。"""
    y = str(dt.year)[2:]
    m = dt.month
    if half_yearly and m == 6:
        return f"{y}H1"
    if half_yearly and m == 12:
        return f"{y}H2"
    q = (m - 1) // 3 + 1
    return f"{y}Q{q}"


def load_raw(code: str) -> dict[str, pd.DataFrame]:
    """读 parquet 原始数据（含分业务构成 + 估值，最多 8 张表）。"""
    d = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / code
    tables = ["financial_indicator", "profit_sheet", "balance_sheet", "cash_flow", "dividend", "segments", "valuation", "quote", "rating", "competition", "profile"]
    out = {}
    for t in tables:
        p = d / f"{t}.parquet"
        if p.exists():
            out[t] = pd.read_parquet(p)
    return out


def build_template_data(code: str) -> dict:
    """读 parquet → 输出模板所需的全部数据结构。

    返回 keys:
      years, financials, quarter_labels, quarterly
    """
    code = _norm_code(code)  # 港股 00700.HK→00700，与 fetch_stock 存储目录对齐
    raw = load_raw(code)
    required = {"financial_indicator", "profit_sheet", "balance_sheet", "cash_flow"}
    if not required.issubset(raw.keys()):
        missing = required - raw.keys()
        raise FileNotFoundError(f"{code} 缺 parquet 表: {missing}，请先运行 scripts/fetch_stock.py {code}")

    annual = build_annual_financials(raw)
    quarter = build_quarter_financials(raw)

    # 股本（share_capital 已是亿股，面值 1 元）+ 优先股默认 0
    latest_shares = None
    if "share_capital" in annual.columns:
        latest_shares = annual["share_capital"].iloc[-1]  # 亿股

    annual = annual.copy()
    if "share_capital" in annual.columns:
        annual["total_shares_yi"] = annual["share_capital"]  # 已是亿股
    if "preferred_shares" in annual.columns:
        annual["preferred_shares_yi"] = annual["preferred_shares"]  # 已是亿股（面值1元）
    else:
        annual["preferred_shares_yi"] = 0.0

    quarter = quarter.copy()
    if latest_shares is not None:
        quarter["total_shares_yi"] = latest_shares  # 股本变动不频繁，用最新值近似
    quarter["preferred_shares_yi"] = 0.0

    years = [d.year for d in annual["report_date"].tolist()]
    financials = _extract(annual, ANNUAL_SPEC)

    # 港股半年度披露（无 Q1/Q3，只有 6 月/12 月）→ 标签用 H1/H2；A 股季报用 Q1-Q4
    half_yearly = not (quarter["report_date"].dt.month.isin([3, 9]).any())
    quarter_labels = [_period_label(d, half_yearly) for d in quarter["report_date"].tolist()]
    quarterly = _extract(quarter, QUARTER_SPEC)

    # 报告期 = 最新季度，如 2026Q1（用于 reports/ 归档目录）
    latest_q = quarter["report_date"].iloc[-1]
    report_period = f"{latest_q.year}Q{(latest_q.month - 1) // 3 + 1}"

    # 分业务收入构成（半年度，可选）
    segment_labels = None
    segments = None
    if "segments" in raw:
        segment_labels, seg_result = build_segments(raw["segments"])
        segments = [
            (name, SEGMENT_PALETTE[i % len(SEGMENT_PALETTE)],
             [_clean(v) for v in revs], [_clean(v) for v in margins])
            for i, (name, revs, margins) in enumerate(seg_result)
        ]

    # 业务版图（客观）：巨潮主营业务一句话 + 各业务条线最新期收入占比
    business_map = None
    if segments:
        latest_revs = [(s[0], s[2][-1]) for s in segments if s[2] and s[2][-1] is not None]
        total = sum(v for _, v in latest_revs)
        if total:
            seg_pcts = [
                {"name": name, "pct": round(v / total * 100, 1)}
                for name, v in sorted(latest_revs, key=lambda x: -x[1])
            ]
            main_business = None
            if "profile" in raw and not raw["profile"].empty:
                mb = raw["profile"].iloc[0].get("main_business")
                main_business = str(mb).strip() if mb else None
            business_map = {"main_business": main_business, "segments": seg_pcts}

    # 估值面板（百度估值算分位 + 腾讯行情精确当前值，可选）
    valuation = None
    if "valuation" in raw and "share_capital" in annual.columns:
        valuation = build_valuation(raw["valuation"], annual)
        # 腾讯行情提供精确的当前 PE/PB/52周/市值，覆盖百度估值稀疏采样
        if "quote" in raw and valuation is not None:
            q = raw["quote"].iloc[0]
            if q.get("pe") and q.get("pe") > 0:
                valuation["pe"] = q["pe"]
            if q.get("pb") and q.get("pb") > 0:
                valuation["pb"] = q["pb"]
            if q.get("price_52w_low") and q.get("price_52w_high"):
                valuation["price_low"] = q["price_52w_low"]
                valuation["price_high"] = q["price_52w_high"]
            if q.get("price"):
                valuation["price_now"] = q["price"]
            # 港股：股息率由腾讯行情 f[47] 提供（分红接口无 dividend_yield_pct）
            if q.get("dividend_yield") and q.get("dividend_yield") > 0:
                valuation["dividend_yield"] = q["dividend_yield"]

    # 机构评级（东财盈利预测 + 评级分布，可选）
    rating = None
    if "rating" in raw:
        rating = _build_rating(raw["rating"])

    # 竞争地位（东财业绩报表 → 行业排名 + 营收份额，可选）
    competition = None
    if "competition" in raw:
        competition = _build_competition(raw["competition"], code)

    # 公司名（腾讯行情）
    company_name = None
    if "quote" in raw:
        company_name = raw["quote"].iloc[0].get("name")

    # 格雷厄姆体检（从年度财务数据算）
    graham = _build_graham(annual)

    # 财务造假检测（Beneish M-Score + 现金流背离 + 应收异常）
    fraud = fraud_check(annual)

    # ValueLine 统计：流动状况（Current Position）+ 年增长率（Annual Rates）
    current_position = _build_current_position(annual)
    annual_rates = _build_annual_rates(annual)

    # LLM 叙事层的事实摘要（数据先行，LLM 只翻译不编数）
    narrative_data = _build_narrative_data(annual, segments, valuation, company_name, code, competition)
    if business_map:
        narrative_data["main_business"] = business_map["main_business"]

    return {
        "years": years,
        "financials": financials,
        "quarter_labels": quarter_labels,
        "quarterly": quarterly,
        "report_period": report_period,
        "segment_labels": segment_labels,
        "segments": segments,
        "valuation": valuation,
        "graham": graham,
        "rating": rating,
        "fraud": fraud,
        "company_name": company_name,
        "competition": competition,
        "business_map": business_map,
        "current_position": current_position,
        "annual_rates": annual_rates,
        "narrative_data": narrative_data,
    }


def _build_rating(raw_rating: pd.DataFrame) -> dict | None:
    """机构评级分布 + 未来几年预测每股收益 + 目标价（东财 A 股 / 经济通港股同构）。"""
    if raw_rating is None or raw_rating.empty:
        return None
    r = raw_rating.iloc[0]
    rating = {
        "total": _clean(r.get("rating_total")),
        "buy": _clean(r.get("rating_buy")),
        "overweight": _clean(r.get("rating_overweight")),
        "neutral": _clean(r.get("rating_neutral")),
        "underweight": _clean(r.get("rating_underweight")),
        "sell": _clean(r.get("rating_sell")),
        "eps_forecast": [],
    }
    # 目标价（港股经济通有；A 股东财盈利预测无此列，返回 None）
    if "target_price" in raw_rating.columns:
        rating["target_price"] = _clean(r.get("target_price"))
    for col in raw_rating.columns:
        if str(col).startswith("eps_"):
            val = _clean(r.get(col))
            if val is not None:
                rating["eps_forecast"].append({"year": str(col)[4:], "eps": val})
    return rating


def _build_competition(raw_comp: pd.DataFrame, code: str) -> dict | None:
    """竞争地位：行业营收排名 + 营收份额 + 同行对比（东财业绩报表口径）。

    A 股走东财业绩报表（revenue_yi/name/symbol 多行）→ 算排名/份额/同行；
    港股走公司概况（industry/company_intro 单行，无营收排名）→ 简化为行业定位。
    """
    if raw_comp is None or raw_comp.empty:
        return None

    # 港股简化路径：只有行业 + 公司介绍（无全市场营收接口）
    if "revenue_yi" not in raw_comp.columns:
        r = raw_comp.iloc[0]
        industry = r.get("industry")
        intro = r.get("company_intro")
        if not industry and not intro:
            return None
        return {
            "industry": str(industry).strip() if industry else None,
            "report_year": None,
            "rank": None,
            "peers_count": None,
            "revenue_yi": None,
            "industry_revenue": None,
            "share_pct": None,
            "top_peers": [],
            "company_intro": str(intro).strip() if intro else None,
            "is_hk": True,
        }

    # A 股路径：全行业营收排名
    df = raw_comp.dropna(subset=["revenue_yi"]).copy()
    if df.empty:
        return None
    df = df.sort_values("revenue_yi", ascending=False).reset_index(drop=True)
    industry = df["industry"].iloc[0] if "industry" in df.columns else None
    report_year = int(df["report_date"].iloc[0].year) if "report_date" in df.columns else None

    self_row = df[df["symbol"] == code.zfill(6)]
    if self_row.empty:
        return None
    rank = int(self_row.index[0]) + 1  # 营收降序后行号即名次
    peers_count = len(df)
    revenue_yi = _clean(self_row.iloc[0].get("revenue_yi"))
    industry_revenue = float(df["revenue_yi"].sum())
    share_pct = (revenue_yi / industry_revenue * 100) if (revenue_yi and industry_revenue) else None

    top_peers = [
        {"name": row["name"], "revenue_yi": _clean(row["revenue_yi"]),
         "is_self": row["symbol"] == code.zfill(6)}
        for _, row in df.head(5).iterrows()
    ]

    return {
        "industry": industry,
        "report_year": report_year,
        "rank": rank,
        "peers_count": peers_count,
        "revenue_yi": _clean(revenue_yi),
        "industry_revenue": _clean(industry_revenue),
        "share_pct": _clean(share_pct),
        "top_peers": top_peers,
    }


def _build_graham(annual: pd.DataFrame) -> dict:
    """格雷厄姆质量体检（从年度数据派生）。"""
    latest = annual.iloc[-1]

    debt_ratio = latest.get("debt_ratio_pct")
    current_ratio = latest.get("current_ratio")

    # 盈利稳定性：近 5 年归母净利是否连续为正
    profits = annual["net_profit_parent"].tail(5)
    stable = bool((profits > 0).all()) if len(profits) >= 3 else None

    # 净现金 = 货币资金 - 有息负债
    net_cash = None
    if "monetary_funds" in annual.columns and "interest_bearing_debt" in annual.columns:
        mf = latest.get("monetary_funds")
        ibd = latest.get("interest_bearing_debt")
        if mf is not None and ibd is not None:
            net_cash = mf - ibd

    return {
        "debt_ratio": _clean(debt_ratio),
        "current_ratio": _clean(current_ratio),
        "profit_stable": stable,
        "net_cash": _clean(net_cash),
    }


def _build_current_position(annual: pd.DataFrame) -> dict | None:
    """流动状况（ValueLine Current Position）：最新年报流动资产 vs 流动负债明细。"""
    if annual.empty:
        return None
    latest = annual.iloc[-1]

    def _v(col):
        v = latest.get(col) if col in annual.columns else None
        return None if (v is None or pd.isna(v)) else float(v)

    assets = [
        ("现金资产", _v("monetary_funds")),
        ("应收账款", _v("accounts_receivable")),
        ("存货", _v("inventory")),
        ("其他流动资产", _v("other_current_assets")),
        ("流动资产合计", _v("current_assets")),
    ]
    liabs = [
        ("应付账款", _v("accounts_payable")),
        ("一年内到期债务", _v("noncurrent_liab_1y")),
        ("其他流动负债", _v("other_current_liabilities")),
        ("流动负债合计", _v("current_liabilities")),
    ]
    wc = _v("working_capital")

    # 核心字段全缺失视为无数据
    if all(v is None for _, v in assets) and all(v is None for _, v in liabs):
        return None

    return {
        "year": int(latest["report_date"].year),
        "assets": assets,
        "liabilities": liabs,
        "working_capital": wc,
    }


def _cagr(vals, n_years):
    """CAGR：vals 已按时间升序，取末 n_years+1 个点，跨 n_years 年。"""
    if vals is None or len(vals) < n_years + 1:
        return None
    window = vals[-(n_years + 1):]
    first, last = window[0], window[-1]
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / n_years) - 1


def _build_annual_rates(annual: pd.DataFrame) -> dict | None:
    """年增长率（ValueLine Annual Rates）：销售/现金流/盈利/股息/账面价值 CAGR。"""
    if annual.empty:
        return None

    def _series(col):
        if col not in annual.columns:
            return None
        s = annual[col].astype(float).tolist()
        return [None if (v is None or pd.isna(v)) else v for v in s]

    series_map = {
        "sales": _series("operating_revenue"),
        "cash_flow": _series("ocf"),
        "earnings": _series("net_profit_parent"),
        "dividends": _series("dividend_per_share"),
        "book_value": _series("total_equity"),
    }

    out = {}
    for key, vals in series_map.items():
        cagr5 = _cagr(vals, 5)
        cagr10 = _cagr(vals, 10)
        if cagr5 is not None or cagr10 is not None:
            out[key] = {"cagr5": cagr5, "cagr10": cagr10}

    return out if out else None


def _build_narrative_data(annual, segments, valuation, company_name, code, competition=None) -> dict:
    """LLM 叙事层的事实摘要（纯数据，无文字）。"""
    latest = annual.iloc[-1]
    latest_year = int(latest["report_date"].year)

    def _g(col):
        v = latest.get(col) if col in annual.columns else None
        return None if (v is None or pd.isna(v)) else float(v)

    def _round(v, d=1):
        return round(v, d) if v is not None else None

    # 近 5 年营收/净利
    recent = []
    for _, r in annual.tail(5).iterrows():
        recent.append({
            "year": int(r["report_date"].year),
            "revenue": _round(r.get("operating_revenue"), 1) if "operating_revenue" in annual.columns else None,
            "profit": _round(r.get("net_profit_parent"), 1) if "net_profit_parent" in annual.columns else None,
        })

    # 分业务摘要（最新期收入占比 + 毛利率）
    seg_summary = []
    if segments:
        latest_revs = [(s[0], s[2][-1]) for s in segments if s[2] and s[2][-1] is not None]
        total = sum(v for _, v in latest_revs)
        margin_map = {s[0]: (s[3][-1] if s[3] and s[3][-1] is not None else None) for s in segments}
        for name, rev in latest_revs:
            seg_summary.append({
                "name": name,
                "revenue_pct": round(rev / total * 100, 1) if total else None,
                "margin": _round(margin_map.get(name), 1),
            })

    val_summary = None
    if valuation:
        val_summary = {
            "pe": _round(valuation.get("pe"), 1),
            "pb": _round(valuation.get("pb"), 2),
            "pe_pctile": _round(valuation.get("pe_pctile"), 0),
            "pb_pctile": _round(valuation.get("pb_pctile"), 0),
        }

    return {
        "name": company_name,
        "code": code,
        "latest_year": latest_year,
        "latest": {
            "revenue": _round(_g("operating_revenue"), 1),
            "net_profit": _round(_g("net_profit_parent"), 1),
            "gross_margin": _round(_g("gross_margin_pct"), 1),
            "net_margin": _round(_g("net_margin_pct"), 1),
            "roe": _round(_g("roe_pct"), 2),
            "debt_ratio": _round(_g("debt_ratio_pct"), 1),
            "ocf": _round(_g("ocf"), 1),
        },
        "recent": recent,
        "segments": seg_summary,
        "dividend_payout": _round(_g("dividend_payout_pct"), 1),
        "dividend_yield": _round(_g("dividend_yield_pct"), 1),
        "valuation": val_summary,
        "competition": competition,
    }
