"""数据适配层：把 cleaner 输出的宽表，转成模板渲染所需的结构。

模板结构（build_valueline.py 消费）：
- YEARS / FINANCIALS：年度表，FINANCIALS 每项为 (分组 或 None, 指标名, [格式化字符串])
- QUARTER_LABELS / QUARTERLY：季度表，同上
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .cleaner import (
    build_annual_financials,
    build_quarter_financials,
    build_segments,
    build_valuation,
    forward_adjust_kline,
)
from ..analysis.fraud import fraud_check


# 业务条线调色板（按收入降序循环分配，适配任意条线数）
SEGMENT_PALETTE = ["#378ADD", "#E24B4A", "#BA7517", "#888780", "#5B8FF9", "#F6903D", "#61A0A8", "#9270CA"]


def _pct(v, total):
    """占比百分比：≥1% 保留 1 位小数，<1% 保留 2 位（避免极小占比被 round 成 0）。"""
    if v is None or not total:
        return None
    pct = v / total * 100
    return round(pct, 2) if pct < 1.0 else round(pct, 1)


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
    (None, "营业总收入（亿元）", "revenue", 0),
    (None, "营业收入（亿元）", "operating_revenue", 0),
    (None, "营业总成本（亿元）", "total_operating_cost", 0),
    (None, "营业利润（亿元）", "operating_profit", 0),
    (None, "营业外收入（亿元）", "non_operating_income", 2),
    (None, "营业外支出（亿元）", "non_operating_expense", 2),
    (None, "利润总额（亿元）", "total_profit", 0),
    (None, "所得税费用（亿元）", "income_tax", 0),
    (None, "净利润（亿元）", "net_profit", 0),
    (None, "归母净利润（亿元）", "net_profit_parent", 0),
    (None, "少数股东损益（亿元）", "minority_interest", 1),
    (None, "扣非归母净利润（亿元）", "deduct_net_profit", 0),
    (None, "自由现金流（亿元）", "free_cash_flow", 0),
    ("资产负债表", None, None, None),
    (None, "流动资产（亿元）", "current_assets", 0),
    (None, "非流动资产（亿元）", "noncurrent_assets", 0),
    (None, "资产合计（亿元）", "total_assets", 0),
    (None, "流动负债（亿元）", "current_liabilities", 0),
    (None, "非流动负债（亿元）", "noncurrent_liabilities", 0),
    (None, "负债合计（亿元）", "total_liabilities", 0),
    (None, "股本（亿股）", "share_capital", 2),
    (None, "归母所有者权益（亿元）", "total_equity", 0),
    (None, "少数股东权益（亿元）", "minority_equity", 0),
    (None, "股东权益合计（亿元）", "total_equity_all", 0),
    ("现金流量表", None, None, None),
    (None, "经营现金流入（亿元）", "operating_cash_inflow", 0),
    (None, "经营现金流出（亿元）", "operating_cash_outflow", 0),
    (None, "经营现金流净额（亿元）", "ocf", 0),
    (None, "投资现金流入（亿元）", "investing_cash_inflow", 0),
    (None, "投资现金流出（亿元）", "investing_cash_outflow", 0),
    (None, "投资现金流净额（亿元）", "icf", 0),
    (None, "筹资现金流入（亿元）", "financing_cash_inflow", 0),
    (None, "筹资现金流出（亿元）", "financing_cash_outflow", 0),
    (None, "筹资现金流净额（亿元）", "financing_cash_flow", 0),
    ("核心财务指标", None, None, None),
    (None, "净资产收益率（ROE）%", "roe_pct", 2),
    (None, "毛利率 %", "gross_margin_pct", 1),
    (None, "净利率 %", "net_margin_pct", 1),
    (None, "营业总收入同比 %", "revenue_yoy_pct", 1),
    (None, "净利润同比 %", "net_profit_yoy_pct", 1),
    (None, "股东权益同比 %", "equity_yoy_pct", 1),
    (None, "资产负债率 %", "debt_ratio_pct", 1),
    (None, "有息负债率 %", "interest_bearing_debt_ratio", 1),
    (None, "分红比例 %", "dividend_payout_pct", 1),
    (None, "经营现金流/净利润 %", "ocf_to_profit_pct", 1),
]

QUARTER_SPEC = [
    # 单季表不设「利润表（单季）/同比增长率（对比去年同期）」这类分类行：
    # 同一张表里既混了流量（单季值）又混了时点（季末余额），分类标题既没把这两类分开，
    # 还占掉一整行版面。改为科目名自带口径，表下用一行注释统一说明。
    (None, "营业总收入（亿元）", "revenue", 0),
    (None, "营业收入（亿元）", "operating_revenue", 0),
    (None, "归母净利润（亿元）", "net_profit_parent", 0),
    (None, "货币资金（季末，亿元）", "monetary_funds", 0),
    (None, "存货（季末，亿元）", "inventory", 0),
    (None, "经营现金流净额（亿元）", "ocf", 0),
    (None, "毛利率 %", "gross_margin_pct", 1),
    (None, "净利率 %", "net_margin_pct", 1),
    (None, "营业总收入同比 %", "revenue_yoy_pct", 1),
    (None, "归母净利润同比 %", "net_profit_parent_yoy_pct", 1),
    (None, "经营现金流净额同比 %", "ocf_yoy_pct", 1),
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


def _drop_empty_rows(rows: list[tuple]) -> list[tuple]:
    """剔除整行无数据的指标行；分组标题若其下指标全被剔除则一并移除。

    银行无「货币资金/存货/营业成本」，港股接口不返回「营业总成本/现金流入流出」等科目，
    这些行全历史都是「—」，占版面却零信息量（港股年度表一度有 13 行全空）。
    """
    kept = [r for r in rows
            if not (r[1] is not None and r[2] and all(v == "—" for v in r[2]))]
    out = []
    for i, row in enumerate(kept):
        if row[0] is not None:  # 分组标题：仅当紧随其后还有指标行时保留
            if i + 1 < len(kept) and kept[i + 1][1] is not None:
                out.append(row)
            continue
        out.append(row)
    return out


def _extract(df: pd.DataFrame, spec, drop_empty: bool = False) -> list[tuple]:
    """按 spec 从宽表提取模板结构（值已格式化为字符串）。

    drop_empty=True 时剔除整行无数据的科目（见 _drop_empty_rows）。
    """
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
    return _drop_empty_rows(rows) if drop_empty else rows


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


def _apply_corrections(raw: dict[str, pd.DataFrame], code: str,
                       val_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """把 PDF 金标准修正记录应用到 raw 数据（raw 层保持接口原始值，修正在此层统一应用）。

    修正记录来源：data/validation/{code}_{year}_reconcile.json（由 reconcile_all 落盘，
    每个 item 含 table / field / pdf_yi(亿元)）。把 pdf_yi 亿元还原为「元」，覆盖到
    对应表、对应年报行的字段值。这样「重拉 raw」只刷新接口原始值，修正记录独立持久化，
    两者解耦，不会再互相抹掉。
    """
    import json

    val_dir = val_dir or (Path(__file__).resolve().parent.parent.parent / "data" / "validation")
    out = {k: v.copy() for k, v in raw.items()}
    if not val_dir.exists():
        return out

    logs = sorted(val_dir.glob(f"{code}_*_reconcile.json"))
    if not logs:
        return out

    for log_path in logs:
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        year = data.get("year")
        for item in data.get("items", []):
            table = item.get("table")
            field = item.get("field")
            pdf_yi = item.get("pdf_yi")
            diff_pct = item.get("diff_pct")
            if not table or not field or pdf_yi is None or table not in out:
                continue
            # 保护：diff_pct 异常大（如 >1e6%）说明 PDF 解析单位识别错误（元/万元/亿元错位），
            # 而非真实的重述偏差，跳过该条避免把错误解析值应用进数据。
            if diff_pct is not None and abs(diff_pct) > 1e6:
                continue
            df = out[table]
            if field not in df.columns or "report_date" not in df.columns:
                continue
            d = pd.to_datetime(df["report_date"])
            mask = (d.dt.year == year) & (d.dt.month == 12)
            if not mask.any():
                continue
            # pdf_yi 是亿元，raw 是元 → 还原为元（int 消除浮点误差，金额应为整数元）
            df.loc[mask, field] = int(round(pdf_yi * 1e8))
    return out


def load_raw(code: str) -> dict[str, pd.DataFrame]:
    """读 parquet 原始数据（含分业务构成 + 估值 + 日 K，最多 12 张表），并应用 PDF 金标准修正。"""
    d = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / code
    tables = ["financial_indicator", "profit_sheet", "balance_sheet", "cash_flow", "dividend", "segments", "valuation", "quote", "kline", "rating", "competition", "profile"]
    out = {}
    for t in tables:
        p = d / f"{t}.parquet"
        if p.exists():
            out[t] = pd.read_parquet(p)
    return _apply_corrections(out, code)


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
    # 近三年：季度披露取 12 期；半年度披露（港股，只有 6/12 月）取 6 期，否则跨度会变成 6 年
    quarter_all = build_quarter_financials(raw, n_quarters=12)
    half_yearly = not (quarter_all["report_date"].dt.month.isin([3, 9]).any())
    quarter = (quarter_all.tail(6) if half_yearly else quarter_all).reset_index(drop=True)

    years = [d.year for d in annual["report_date"].tolist()]
    financials = _extract(annual, ANNUAL_SPEC, drop_empty=True)

    # 港股半年度披露（无 Q1/Q3，只有 6 月/12 月）→ 标签用 H1/H2；A 股季报用 Q1-Q4
    quarter_labels = [_period_label(d, half_yearly) for d in quarter["report_date"].tolist()]
    quarterly = _extract(quarter, QUARTER_SPEC, drop_empty=True)

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
                {"name": name, "pct": _pct(v, total)}
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
            # 总市值（腾讯行情，亿元口径；港股为港元市值）
            if q.get("market_cap"):
                valuation["market_cap"] = q["market_cap"]
            # 港股：分红接口无 dividend_total，市值口径算不出股息率，才回退腾讯行情 f[47]。
            # 不能无条件覆盖——A 股由「分红总额 ÷ 市值」算出的股息率与 PE/PB 同分母、可算分位，
            # 被行情值盖掉就退回了两套口径并存的老问题。
            if not valuation.get("dividend_yield") and q.get("dividend_yield", 0) > 0:
                valuation["dividend_yield"] = q["dividend_yield"]
            # 股价数据日期（腾讯行情快照日期，供「发布日期 = 股价日期」对齐）
            qd = q.get("report_date")
            if qd is not None and not pd.isna(qd):
                valuation["quote_date"] = pd.Timestamp(qd).date()
            # 盘中/收盘：决定估值面板该标「最新收盘」还是「盘中价」
            valuation["is_intraday"] = q.get("is_intraday")
            valuation["quote_time"] = qd if (qd is not None and not pd.isna(qd)) else None

        # 价格区间以「近一年日 K」为准（最高优先级），覆盖上面百度估值/腾讯两套近似值。
        # 原因：腾讯 q= 接口的 52 周高低与实际成交价不符（600519 实测 1413.64 vs 真实
        # 1568.00，东财/腾讯/新浪三家日 K 一致），而该数字直接决定报告里价格位置条怎么画，
        # 错一次就是对外错一次。
        #
        # 复权口径：主口径走「前复权」（腾讯/同花顺减法口径），因为读者在腾讯自选股里
        # 看到的就是它——茅台 2026-02-06 的高点，不复权 1568.00、前复权 1539.98，
        # 差的 28.02 正好是 2026-06-26 那次每股派息。报告写不复权会被当成数据错误，
        # 所以区间用前复权，同时把不复权值一并留给脚注备查。
        if "kline" in raw and valuation is not None and not raw["kline"].empty:
            kl, qfq_meta = forward_adjust_kline(raw["kline"], raw.get("dividend"))
            kl = kl.dropna(subset=["close"]).sort_values("report_date")
            last = kl.iloc[-1]
            kl_date = last["report_date"].date()
            cutoff = last["report_date"] - pd.DateOffset(years=1)
            win = kl[kl["report_date"] >= cutoff]
            use_qfq = bool(qfq_meta.get("available")) and "close_qfq" in kl.columns
            _src = last.get("src")
            _src_txt = f" · {_src}" if isinstance(_src, str) and _src else ""
            if not win.empty:
                hi_col, lo_col = ("high_qfq", "low_qfq") if use_qfq else ("high", "low")
                lo_i, hi_i = win[lo_col].idxmin(), win[hi_col].idxmax()
                valuation["price_low"] = float(win.loc[lo_i, lo_col])
                valuation["price_high"] = float(win.loc[hi_i, hi_col])
                valuation["price_low_date"] = win.loc[lo_i, "report_date"].date()
                valuation["price_high_date"] = win.loc[hi_i, "report_date"].date()
                # 不复权对照值 + 高低点日期（脚注与自查用，避免「换个口径对不上」时无处可查）
                _raw_lo_i, _raw_hi_i = win["low"].idxmin(), win["high"].idxmax()
                valuation["price_low_raw"] = float(win.loc[_raw_lo_i, "low"])
                valuation["price_high_raw"] = float(win.loc[_raw_hi_i, "high"])
                valuation["price_high_raw_date"] = win.loc[_raw_hi_i, "report_date"].date()
                valuation["price_range_src"] = (
                    f"近一年前复权日K（腾讯口径）{_src_txt}" if use_qfq
                    else f"近一年不复权日K{_src_txt}"
                )
            valuation["price_adjusted"] = use_qfq
            valuation["price_adjust_events"] = qfq_meta.get("events_in_range") or []
            # 现价取「更晚的那一个」：日 K 通常收在上一交易日，而行情快照可能是当日盘中。
            # 若快照更晚就保留快照价（更实时），只把区间换成日 K 口径；反之用日 K 收盘价。
            # 前复权序列以最新价为锚点，所以「最新那天的前复权价 = 原始价」，可直接与快照比。
            q_date = valuation.get("quote_date")
            if q_date is None or kl_date >= q_date:
                valuation["price_now"] = float(last["close_qfq"]) if use_qfq else float(last["close"])
                valuation["price_now_date"] = kl_date
                if q_date is None:
                    valuation["quote_date"] = kl_date
            else:
                valuation["price_now_date"] = q_date

    # 机构评级（东财盈利预测 + 评级分布，可选）
    rating = None
    if "rating" in raw:
        rating = _build_rating(raw["rating"], actual_year=max(years) if years else None)

    # 竞争地位（东财业绩报表 → 行业排名 + 营收份额，可选）
    competition = None
    if "competition" in raw:
        competition = _build_competition(raw["competition"], code)

    # 公司名（腾讯行情，失败时兜底港股 profile 的「公司名称」字段）
    company_name = None
    if "quote" in raw and not raw["quote"].empty:
        company_name = raw["quote"].iloc[0].get("name")
    if not company_name and "competition" in raw and not raw["competition"].empty:
        company_name = raw["competition"].iloc[0].get("company_name")

    # 格雷厄姆体检（从年度财务数据算）
    graham = _build_graham(annual)

    # 财务造假检测（Beneish M-Score + 现金流背离 + 应收异常）
    fraud = fraud_check(annual)

    # ValueLine 统计：流动状况（Current Position）+ 年增长率（Annual Rates）
    current_position = _build_current_position(annual)
    annual_rates = _build_annual_rates(annual)

    # 构成饼图（最新年报五大类子科目构成）
    pie_data = _build_pie_data(annual)

    # 季度财报解读的事实输入（分业务占比复用 business_map 的口径，避免两处算法不一致）
    quarter_review_facts = _build_quarter_review_facts(
        quarter, annual, (business_map or {}).get("segments")
    )

    # 经营结构（分产品/渠道/地区 的收入占比·毛利率·同比）+ 年度经营计划。
    # 只能回定期报告原文抽：东财主营构成接口没有「销售渠道」切面，也不给分切面同比，
    # 而渠道结构（直销 vs 批发代理）恰恰是白酒公司利润率变动的主因。
    operating = None
    try:
        from src.report.mda_extract import build_operating_structure
        operating = build_operating_structure(code)
    except Exception:
        operating = None
    if operating and quarter_review_facts:
        quarter_review_facts["经营结构"] = operating
        # 现金流同比归因单拎成顶层键：它跟「经营结构」没关系，塞在里面会让 LLM
        # 误以为是某个切面的指标。放在顶层，prompt 里指向也更清楚。
        if operating.get("现金流归因"):
            quarter_review_facts["现金流归因"] = operating["现金流归因"]

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
        "pie_data": pie_data,
        "quarter_review_facts": quarter_review_facts,
        "operating_structure": operating,
        "sanity": _sanity_summary(annual, code),
        "narrative_data": narrative_data,
    }


def _sanity_summary(annual: pd.DataFrame, code: str) -> list[dict] | None:
    """业务勾稽体检结果（供报告「数据校验」区展示）：会计恒等式/利润勾稽/比率边界/同比异常。"""
    try:
        from src.data.quality import check_annual_sanity
        r = check_annual_sanity(annual, code)
        return [{"check": c["check"], "ok": c["ok"], "detail": c["detail"]} for c in r.checks]
    except Exception:
        return None


def _build_rating(raw_rating: pd.DataFrame, actual_year: int | None = None) -> dict | None:
    """机构评级分布 + 未来几年预测每股收益 + 目标价（东财 A 股 / 经济通港股同构）。

    actual_year：已披露**实际年报**的最近年份。东财盈利预测表会同时挂着「已实现年度」那一列
    （如 2026 年 9 月仍在回传「2025 预测 EPS 65.85」），而 2025 年报早已披露，那一列不是预测
    而是事后诸葛，渲染成「预测每股收益：2025 65.85」会让读者误以为公司未来还要赚这么多。
    因此凡 year <= actual_year 的列一律剔除，只保留真正尚未落地的年度。
    """
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
            yr = str(col)[4:]
            if actual_year is not None and yr.isdigit() and int(yr) <= actual_year:
                continue  # 已披露实际年报的年度 → 不再是预测
            val = _clean(r.get(col))
            if val is not None:
                rating["eps_forecast"].append({"year": yr, "eps": val})
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


# ---------------------------------------------------------------------------
# 构成饼图配置：五大类 → 子科目清单（展示最新年报的构成占比）
# 每项 (子科目中文名, 宽表字段名)。字段可能不存在（港股/银行），按「有值才画」处理。
# ---------------------------------------------------------------------------
PIE_GROUPS = [
    ("营业总成本", "total_operating_cost", [
        ("营业成本", "operating_cost"),
        ("税金及附加", "operate_tax_add"),
        ("销售费用", "sell_expense"),
        ("管理费用", "admin_expense"),
        ("研发费用", "research_expense"),
        ("财务费用", "finance_expense"),
        ("资产减值损失", "asset_impairment_loss"),
        ("信用减值损失", "credit_impairment_loss"),
    ]),
    ("流动资产", "current_assets", [
        ("货币资金", "monetary_funds"),
        ("拆出资金", "lend_fund"),
        ("发放贷款及垫款", "loan_advance"),
        ("交易性金融资产", "trading_financial_assets"),
        ("买入返售金融资产", "buy_resale_finasset"),
        ("衍生金融资产", "derivative_finasset"),
        ("结算备付金", "settle_excess_reserve"),
        ("应收票据", "notes_receivable"),
        ("应收账款", "accounts_receivable"),
        ("应收款项融资", "finance_receivables"),
        ("预付款项", "prepayments"),
        ("其他应收款", "other_receivables"),
        ("存货", "inventory"),
        ("合同资产", "contract_assets"),
        ("一年内到期的非流动资产", "noncurrent_asset_1y"),
        ("其他流动资产", "other_current_assets"),
    ]),
    ("非流动资产", "noncurrent_assets", [
        ("债权投资", "hold_maturity_invest"),
        ("其他债权投资", "other_creditor_invest"),
        ("长期股权投资", "long_equity_invest"),
        ("其他权益工具投资", "other_equity_invest"),
        ("其他非流动金融资产", "other_noncurrent_finasset"),
        ("投资性房地产", "invest_realestate"),
        ("固定资产", "fixed_assets"),
        ("在建工程", "construction_in_progress"),
        ("使用权资产", "useright_asset"),
        ("无形资产", "intangible_assets"),
        ("商誉", "goodwill"),
        ("长期待摊费用", "long_prepaid_expense"),
        ("递延所得税资产", "defer_tax_asset"),
        ("其他非流动资产", "other_noncurrent_assets"),
    ]),
    ("流动负债", "current_liabilities", [
        ("短期借款", "short_term_loan"),
        ("同业存放款项", "accept_deposit_interbank"),
        ("衍生金融负债", "derivative_finliab"),
        ("卖出回购金融资产款", "sell_repo_finasset"),
        ("应付票据", "notes_payable"),
        ("应付账款", "accounts_payable"),
        ("合同负债", "contract_liabilities"),
        ("预收款项", "advance_receivables"),
        ("应付职工薪酬", "staff_salary_payable"),
        ("应交税费", "tax_payable"),
        ("应付手续费及佣金", "fee_commission_payable"),
        ("其他应付款", "other_payables"),
        ("应付股利", "dividend_payable"),
        ("应付利息", "interest_payable"),
        ("一年内到期的非流动负债", "noncurrent_liab_1y"),
        ("应付短期债券", "short_bond_payable"),
        ("其他流动负债", "other_current_liabilities"),
    ]),
    ("非流动负债", "noncurrent_liabilities", [
        ("长期借款", "long_term_loan"),
        ("应付债券", "bond_payable"),
        ("租赁负债", "lease_liabilities"),
        ("长期应付款", "long_payable"),
        ("长期应付职工薪酬", "long_staff_salary_payable"),
        ("预计负债", "predict_liabilities"),
        ("递延所得税负债", "defer_tax_liabilities"),
        ("永续债", "perpetual_bond"),
        ("其他非流动负债", "other_noncurrent_liabilities"),
    ]),
]

# 银行（金融）版：资产/负债按金融科目分类（银行报表无流动/非流动划分）
PIE_GROUPS_BANK = [
    ("资产", "total_assets", [
        ("发放贷款及垫款", "loan_advance"),
        ("债权投资", ["creditor_invest", "amortize_cost_finasset"]),
        ("交易性金融资产", "trading_financial_assets"),
        ("现金及存放中央银行款项", "cash_deposit_pbc"),
        ("存放同业款项", "deposit_interbank"),
        ("拆出资金", "lend_fund"),
        ("买入返售金融资产", "buy_resale_finasset"),
        ("衍生金融资产", "derivative_finasset"),
        ("贵金属", "precious_metal"),
        ("其他债权投资", ["other_creditor_invest", "fvtoci_finasset"]),
        ("其他权益工具投资", "other_equity_invest"),
        ("长期股权投资", "long_equity_invest"),
        ("固定资产", "fixed_assets"),
        ("在建工程", "construction_in_progress"),
        ("投资性房地产", "invest_realestate"),
        ("递延所得税资产", "defer_tax_asset"),
        ("无形资产", "intangible_assets"),
    ]),
    ("负债", "total_liabilities", [
        ("吸收存款", "accept_deposit"),
        ("同业及其他金融机构存放款项", "iofi_deposit"),
        ("拆入资金", "borrowings"),
        ("应付债券", "bond_payable"),
        ("卖出回购金融资产款", "sell_repo_finasset"),
        ("向中央银行借款", "loan_pbc"),
        ("衍生金融负债", "derivative_finliab"),
        ("永续债", "perpetual_bond"),
        ("同业存单", "deposit_certificate"),
        ("应付职工薪酬", "staff_salary_payable"),
        ("应交税费", "tax_payable"),
        ("递延所得税负债", "defer_tax_liabilities"),
        ("预计负债", "predict_liabilities"),
    ]),
]


def _build_pie_data(annual: pd.DataFrame) -> dict | None:
    """构成饼图：最新年报五大类（营业总成本/流动资产/非流动资产/流动负债/非流动负债）的子科目构成。

    规则：
    - 只画「有值且 >0」的子科目；负值科目（如财务费用 <0 为利息净收益）是抵减项，单独放
      deductions 不画扇区，避免饼图占比超 100%；
    - 占比 = 值 / 正值科目加总（内部归一化，保证饼图填满 360°）；
    - 大类总额 > 正值科目加总时，差额记为「其他」兜底（未单列的正项明细）。
    """
    if annual.empty:
        return None
    latest = annual.iloc[-1]

    def _v(col):
        v = latest.get(col) if col in annual.columns else None
        return None if (v is None or pd.isna(v)) else float(v)

    def _v_multi(field):
        """字段别名列表 fallback：依次取第一个「有值」的字段（银行新/旧准则科目名不同）。"""
        if isinstance(field, (list, tuple)):
            for f in field:
                v = _v(f)
                if v is not None:
                    return v
            return None
        return _v(field)

    # 银行识别：银行报表无流动/非流动划分（无 current_assets/current_liabilities 列），
    # 且含「吸收存款 / 现金及存放中央银行款项」等银行核心科目 → 改用资产/负债两大类的金融科目方案
    is_bank = ("accept_deposit" in annual.columns) or ("cash_deposit_pbc" in annual.columns)
    pie_groups = PIE_GROUPS_BANK if is_bank else PIE_GROUPS

    groups = []
    for title, total_field, items in pie_groups:
        total = _v(total_field)
        if total is None or total <= 0:
            continue
        parts = []
        deductions = []
        pos_sum = 0.0
        for name, field in items:
            v = _v_multi(field)
            if v is None:
                continue
            if v > 0:
                parts.append({"name": name, "value": round(v, 2)})
                pos_sum += v
            elif v < 0:
                deductions.append({"name": name, "value": round(v, 2)})
        if not parts:
            continue
        # 兜底「其他」：总额 > 正值科目加总时，差额为未单列的正项明细
        if total - pos_sum > 0.01:
            parts.append({"name": "其他", "value": round(total - pos_sum, 2)})
            pos_sum = total
        # 图例按金额降序阅读（「其他」为兜底项，固定排末尾不参与排序）
        parts.sort(key=lambda p: (p["name"] == "其他", -p["value"]))
        for p in parts:
            p["pct"] = round(p["value"] / pos_sum * 100, 1)
        groups.append({
            "title": title,
            "total": round(total, 1),
            "items": parts,
            "deductions": deductions,
        })

    if not groups:
        return None
    return {"year": int(latest["report_date"].year), "groups": groups}


# 流动状况（Current Position）科目清单直接复用构成饼图的子科目定义：
# 同一张资产负债表在两处（构成饼图 / 经营统计）必须用同一套科目口径，
# 否则读者会看到「流动负债构成」和「流动状况」对不上，属于自相矛盾。
_PIE_ITEMS_BY_GROUP = {title: items for title, _field, items in PIE_GROUPS}
_PIE_ITEMS_BY_GROUP_BANK = {title: items for title, _field, items in PIE_GROUPS_BANK}

# 明细行最小金额（亿元）。低于此值的零头科目（茅台应收账款 0.03 亿）单列只会占版面，
# 统一沉到「其他（未单列科目）」里，两列才看得清真正的大头。
_CP_MIN_ITEM_YI = 1.0


def _cp_line_items(spec, subtotal, value_of) -> list[tuple[str, float]]:
    """把资产负债表一个大类的明细科目整理成可直接展示的行（不含小计行）。

    规则：
    1. 取 spec 里有值且 ≥ _CP_MIN_ITEM_YI 的科目，按金额降序；
    2. 逐项累加时若加上该项会超过报表小计（容差 0.5% 且 ≥0.5 亿），跳过该项——
       说明数据源把它归到了别的大类（茅台「发放贷款及垫款」15.54 亿就不在
       东财的流动资产小计里），硬列进去这一列就加不平；
    3. 明细加总与小计的差额（≥0.1 亿）补一行「其他（未单列科目）」。

    第 3 条是这个面板存在的意义：明细能加出报表小计，读者才敢用这里的数。
    原始版本只挑了 4 个科目就收尾，茅台 2025 流动资产漏了拆出资金 990.96 亿、
    一年内到期的非流动资产 268.71 亿，列出的 1131 亿对报表 2525 亿，缺了 55%。
    """
    picked: list[tuple[str, float]] = []
    listed = 0.0
    cap = (subtotal + max(0.5, subtotal * 0.005)) if subtotal is not None else None

    def _cands(min_yi: float) -> list[tuple[str, float]]:
        out = []
        for name, field in spec:
            v = value_of(field)
            if v is None or v < min_yi:
                continue
            out.append((name, v))
        out.sort(key=lambda kv: -kv[1])  # 先按金额降序
        return out

    # 必须先按金额降序再累加：否则大科目会被前面小科目的累计额挤出预算
    # （茅台 268.71 亿的「一年内到期的非流动资产」曾被 15.54 亿的「发放贷款及垫款」
    # 顶掉，差额兜底行直接涨到 254 亿，反而把明细表变成一句「其他」）。
    cands = _cands(_CP_MIN_ITEM_YI)
    if not cands:
        # 小公司科目金额普遍低于阈值：放宽到全部正项再取前 8，
        # 否则这一列会退化成一个孤零零的「其他（未单列科目）」
        cands = [kv for kv in _cands(0.0) if kv[1] > 0][:8]
    for name, v in cands:
        if cap is not None and listed + v > cap:
            continue
        picked.append((name, v))
        listed += v
    if subtotal is not None:
        gap = subtotal - listed
        if gap >= 0.1:
            picked.append(("其他（未单列科目）", gap))
    return picked


def _build_current_position(annual: pd.DataFrame) -> dict | None:
    """经营统计左块：最新年报的资产负债结构。

    非金融企业 → 流动状况（Current Position）：流动资产 vs 流动负债明细 + 营运资本；
    银行 → 存贷结构（Loan-to-Deposit）：银行报表无流动/非流动划分，「流动状况」概念不
    成立（ValueLine 对银行也不披露 current position），改用存贷结构 + 存贷比，后者是银行
    真正的流动性指标，且报告其他地方都没有这个数。
    """
    if annual.empty:
        return None
    latest = annual.iloc[-1]

    def _v(col):
        v = latest.get(col) if col in annual.columns else None
        return None if (v is None or pd.isna(v)) else float(v)

    def _v_multi(field):
        if isinstance(field, (list, tuple)):
            for f in field:
                v = _v(f)
                if v is not None:
                    return v
            return None
        return _v(field)

    is_bank = ("accept_deposit" in annual.columns) or ("cash_deposit_pbc" in annual.columns)

    if is_bank:
        loan = _v("loan_advance")
        deposit = _v("accept_deposit")
        # 银行同样走「明细加总 = 报表总计」的兜底逻辑（银行科目清单见 PIE_GROUPS_BANK）
        total_ta = _v("total_assets")
        total_tl = _v("total_liabilities")
        assets = [
            ("发放贷款及垫款", loan),
            ("现金及存放中央银行款项", _v("cash_deposit_pbc")),
            ("存放同业款项", _v("deposit_interbank")),
            ("拆出资金", _v("lend_fund")),
            ("债权投资", _v_multi(["creditor_invest", "amortize_cost_finasset"])),
        ]
        liabs = [
            ("吸收存款", deposit),
            ("同业及其他金融机构存放款项", _v("iofi_deposit")),
            ("拆入资金", _v("borrowings")),
            ("应付债券", _v("bond_payable")),
            ("向中央银行借款", _v("loan_pbc")),
        ]
        # 明细加总与报表总计的差额（≥0.1 亿）补一行，避免银行这一列也加不平
        if total_ta is not None:
            gap_a = total_ta - sum(v for _, v in assets if v is not None)
            if gap_a >= 0.1:
                assets.append(("其他（未单列科目）", gap_a))
        if total_tl is not None:
            gap_l = total_tl - sum(v for _, v in liabs if v is not None)
            if gap_l >= 0.1:
                liabs.append(("其他（未单列科目）", gap_l))
        assets.append(("资产总计", total_ta))
        liabs.append(("负债总计", total_tl))
        if all(v is None for _, v in assets) and all(v is None for _, v in liabs):
            return None
        ldr = loan / deposit * 100 if (loan is not None and deposit) else None
        return {
            "year": int(latest["report_date"].year),
            "title": "存贷结构（Loan-to-Deposit）",
            "assets": assets,
            "liabilities": liabs,
            "working_capital": None,
            "footer": {
                "label": "存贷比（发放贷款及垫款 ÷ 吸收存款）",
                "value": ldr,
                "unit": "%",
                "digits": 1,
            },
        }

    # 明细科目与「年度全历史表 / 构成饼图」完全同源（见 _PIE_ITEMS_BY_GROUP）：
    # 金额降序 + 「其他（未单列科目）」兜底，保证明细加总 = 报表小计。
    total_ca = _v("current_assets")
    total_cl = _v("current_liabilities")
    assets = _cp_line_items(_PIE_ITEMS_BY_GROUP["流动资产"], total_ca, _v_multi)
    liabs = _cp_line_items(_PIE_ITEMS_BY_GROUP["流动负债"], total_cl, _v_multi)
    if total_ca is not None:
        assets.append(("流动资产", total_ca))
    if total_cl is not None:
        liabs.append(("流动负债", total_cl))
    wc = _v("working_capital")

    # 核心字段全缺失视为无数据
    if all(v is None for _, v in assets) and all(v is None for _, v in liabs):
        return None

    return {
        "year": int(latest["report_date"].year),
        "title": "流动状况（Current Position）",
        "assets": assets,
        "liabilities": liabs,
        "working_capital": wc,
        "footer": {
            "label": "营运资本（流动资产 − 流动负债）",
            "value": wc,
            "unit": "亿元",
            "digits": 1,
        },
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
        "sales": _series("revenue"),
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


def _build_quarter_review_facts(quarter: pd.DataFrame, annual: pd.DataFrame,
                                segments=None, competition=None) -> dict | None:
    """「季度财报解读」的事实输入（纯数字 + 报告期，不含任何结论，结论交给 LLM 翻译）。

    单季表只有单季值，但市场看中报读的是「上半年累计」——所以这里同时给单季和
    年初至今累计两套口径，以及累计同比（与去年同样季度数加总比较），
    否则 LLM 会把 Q2 单季的 -x% 说成「上半年下滑 x%」。
    """
    if quarter is None or quarter.empty:
        return None
    q = quarter.sort_values("report_date").reset_index(drop=True)
    last = q.iloc[-1]
    d = pd.Timestamp(last["report_date"])
    y = d.year

    def _r(v, n=1):
        if v is None or pd.isna(v):
            return None
        return _clean(round(float(v), n))

    def _rc(v, n=2):
        if v is None or pd.isna(v):
            return None
        return _clean(round(float(v), n))

    def _sum(s: pd.Series):
        return _r(s.sum(min_count=1))

    single = {
        "报告期": _q_label(d),
        "单季营业总收入_亿元": _r(last.get("revenue")),
        "单季营业收入_亿元": _r(last.get("operating_revenue")),
        "单季归母净利润_亿元": _r(last.get("net_profit_parent")),
        "单季毛利率_pct": _r(last.get("gross_margin_pct")),
        "单季净利率_pct": _r(last.get("net_margin_pct")),
        "单季经营现金流净额_亿元": _r(last.get("ocf")),
        "单季营收同比_pct": _r(last.get("revenue_yoy_pct")),
        "单季归母净利同比_pct": _r(last.get("net_profit_parent_yoy_pct")),
        "单季经营现金流同比_pct": _r(last.get("ocf_yoy_pct")),
    }
    # 环比：与上一季比。Q1 环比对 Q4 含明显季节性，prompt 里会提示不要把它当趋势。
    if len(q) >= 2:
        prev = q.iloc[-2]
        for col, key in (("revenue", "单季营收环比_pct"),
                         ("net_profit_parent", "单季归母净利环比_pct")):
            a, b = last.get(col), prev.get(col)
            if a is not None and b is not None and not pd.isna(a) and not pd.isna(b) and b:
                single[key] = _r((float(a) / float(b) - 1) * 100)

    # 年初至今累计（中报=H1 两个季度加总）
    cur_y = q[q["report_date"].dt.year == y]
    prev_y = q[q["report_date"].dt.year == y - 1].head(len(cur_y))
    cumulative = {
        "累计季度数": len(cur_y),
        "累计营业总收入_亿元": _sum(cur_y["revenue"]) if "revenue" in cur_y.columns else None,
        "累计归母净利润_亿元": _sum(cur_y["net_profit_parent"]) if "net_profit_parent" in cur_y.columns else None,
        "累计经营现金流净额_亿元": _sum(cur_y["ocf"]) if "ocf" in cur_y.columns else None,
    }
    if len(prev_y) == len(cur_y) and len(cur_y) > 0:
        for col, key in (("revenue", "累计营收同比_pct"),
                         ("net_profit_parent", "累计归母净利同比_pct"),
                         ("ocf", "累计经营现金流同比_pct")):
            if col not in cur_y.columns:
                continue
            prev_sum = prev_y[col].sum(min_count=1)
            cur_sum = cur_y[col].sum(min_count=1)
            if pd.notna(prev_sum) and prev_sum and pd.notna(cur_sum):
                cumulative[key] = _r((float(cur_sum) / float(prev_sum) - 1) * 100)

    # 季末资产负债
    balance = {}
    for col, key in (("monetary_funds", "货币资金_亿元"),
                     ("inventory", "存货_亿元"),
                     ("accounts_receivable", "应收账款_亿元"),
                     ("total_assets", "总资产_亿元"),
                     ("total_liabilities", "总负债_亿元"),
                     ("total_equity", "归母净资产_亿元")):
        balance[key] = _r(last.get(col))
    ta, tl = last.get("total_assets"), last.get("total_liabilities")
    if ta is not None and tl is not None and not pd.isna(ta) and not pd.isna(tl) and ta:
        balance["资产负债率_pct"] = _r(float(tl) / float(ta) * 100)

    # 单季走势序列（供 LLM 判断「是这一个季度的问题还是连续几个季度」）
    trend = []
    for _, r in q.iterrows():
        trend.append({
            "期": _q_label(pd.Timestamp(r["report_date"])),
            "营收_亿元": _r(r.get("revenue")),
            "归母净利_亿元": _r(r.get("net_profit_parent")),
            "毛利率_pct": _r(r.get("gross_margin_pct")),
            "营收同比_pct": _r(r.get("revenue_yoy_pct")),
        })

    latest_year = int(annual["report_date"].max().year) if not annual.empty else None
    return {
        "报告期": _q_label(d),
        "单季": single,
        "年初至今累计": cumulative,
        "季末资产负债": balance,
        "单季走势序列": trend,
        "最新年报对照": {
            "年度": latest_year,
            "营业总收入_亿元": _r(annual["revenue"].iloc[-1]) if "revenue" in annual.columns else None,
            "归母净利润_亿元": _r(annual["net_profit_parent"].iloc[-1]) if "net_profit_parent" in annual.columns else None,
        },
        "分业务收入占比": [
            {"业务": s.get("name"), "占比_pct": _rc(s.get("pct"))}
            for s in (segments or [])[:6]
        ] or None,
    }


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
            "revenue": _round(r.get("revenue"), 1) if "revenue" in annual.columns else None,
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
                "revenue_pct": _pct(rev, total),
                "margin": _round(margin_map.get(name), 1),
            })

    val_summary = None
    if valuation:
        val_summary = {
            "pe": _round(valuation.get("pe"), 1),
            "pb": _round(valuation.get("pb"), 2),
            "pe_pctile": _round(valuation.get("pe_pctile"), 0),
            "pb_pctile": _round(valuation.get("pb_pctile"), 0),
            "dividend_yield": _round(valuation.get("dividend_yield"), 1),
        }

    # 股息率：与估值面板保持同一口径（最近年度分红总额 ÷ 当前市值）。
    # 面板是读者直接看到的数，叙事层若改用户报里「年内各次派息股息率之和」那一列，
    # 正文会写 4.0%、面板写 4.1%，同一页自相矛盾。年报口径仅在面板取不到时兜底
    # （港股分红表无 dividend_total，面板为 None，此时用年报/行情口径）。
    div_yield = (val_summary or {}).get("dividend_yield")
    if div_yield is None:
        div_yield = _round(_g("dividend_yield_pct"), 1)

    return {
        "name": company_name,
        "code": code,
        "latest_year": latest_year,
        "latest": {
            "revenue": _round(_g("revenue"), 1),
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
        "dividend_yield": div_yield,
        "valuation": val_summary,
        "competition": competition,
    }
