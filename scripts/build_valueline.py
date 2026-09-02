#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ValueLine 一页研报。

形态：纵向长图（小程序上下滑动 / 小红书笔记），宽度固定 1080px、高度自适应。
数据来源：优先从 parquet 读取真实财报（src/data/adapter.py），无数据时降级为示例数据。
输出：templates/valueline.html（预览） + reports/{报告期}/601088.html（归档）。

用法：
    python scripts/build_valueline.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 尝试导入数据适配层（可选，无 parquet 数据时降级为示例数据）
try:
    from src.data.adapter import build_template_data
    _HAS_DATA = True
except Exception:
    _HAS_DATA = False

# 尝试导入 LLM 叙事层生成（可选，无 key 时降级为占位）
try:
    from src.report.llm import generate_narrative
    _HAS_LLM = True
except Exception:
    _HAS_LLM = False

# 示例数据（仅降级用；真实渲染用 adapter 从 parquet 读取）
from _sample_data import (
    SAMPLE_YEARS,
    SAMPLE_FINANCIALS,
    SAMPLE_QUARTER_LABELS,
    SAMPLE_QUARTERLY,
    SAMPLE_SEGMENTS,
)

# 当前渲染用的数据（默认示例，build() 时若 parquet 存在则被真实数据覆盖）
YEARS = SAMPLE_YEARS
FINANCIALS = SAMPLE_FINANCIALS
QUARTER_LABELS = SAMPLE_QUARTER_LABELS
QUARTERLY = SAMPLE_QUARTERLY
SEGMENT_LABELS = SAMPLE_QUARTER_LABELS  # 示例时分业务用季度标签；真实数据用半年度标签
SEGMENTS = SAMPLE_SEGMENTS
VALUATION = None  # 估值面板（真实数据时由 adapter 提供）
GRAHAM = None     # 格雷厄姆体检（真实数据时由 adapter 提供）
RATING = None     # 机构评级分布（真实数据时由 adapter 提供）
FRAUD = None      # 财务造假检测（真实数据时由 adapter 提供）
COMPETITION = None  # 竞争地位（行业排名/营收份额，真实数据时由 adapter 提供）
BUSINESS_MAP = None  # 业务版图（主营业务一句话 + 各业务收入占比，真实数据时由 adapter 提供）
CURRENT_POSITION = None  # 流动状况（流动资产 vs 流动负债明细，ValueLine Current Position）
ANNUAL_RATES = None      # 年增长率（销售/现金流/盈利/股息/账面价值 CAGR，ValueLine Annual Rates）
COMPANY_NAME = "中国神华"  # 公司名（真实数据时由 adapter 提供）
COMPANY_CODE = "601088"    # 股票代码
NARRATIVE = None           # LLM 叙事层（真实数据时由 generate_narrative 生成）
RECONCILE_LOG = []         # 数据交叉校验覆盖记录（官方年报 PDF 修正接口错误字段）
CURRENCY_NOTE = ""         # 货币口径说明（港股标的标注：财务已换算人民币，股价为港币）


def _is_hk(code: str) -> bool:
    """判断是否港股标的（带 .HK 后缀，或 0 开头 5 位码）。"""
    c = str(code).upper()
    if c.endswith(".HK"):
        return True
    bare = c.split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, str):
        return v  # 已是格式化字符串（来自 adapter）
    if isinstance(v, int):
        return str(v)
    return f"{v:.1f}"


def build_table() -> str:
    head = "".join(f"<th>{y}</th>" for y in YEARS)
    rows = []
    for group, name, vals in FINANCIALS:
        if group:
            rows.append(f'<tr class="group"><td colspan="{len(YEARS) + 1}">{group}</td></tr>')
            continue
        cells = [f'<td class="row-head">{name}</td>']
        cells += [f'<td class="num">{_fmt(v)}</td>' for v in vals]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="table-scroll">'
        f'<table class="dense"><thead><tr><th class="name">指标</th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        "</div>"
    )


def build_quarter_table() -> str:
    head = "".join(f"<th>{q}</th>" for q in QUARTER_LABELS)
    rows = []
    for group, name, vals in QUARTERLY:
        if group:
            rows.append(f'<tr class="group"><td colspan="{len(QUARTER_LABELS) + 1}">{group}</td></tr>')
            continue
        cells = [f'<td class="row-head">{name}</td>']
        cells += [f'<td class="num">{_fmt(v)}</td>' for v in vals]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="table-scroll">'
        f'<table class="dense"><thead><tr><th class="name">指标</th>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        "</div>"
    )


def build_segments() -> str:
    if not SEGMENTS:
        return ('<div style="font-size:11px;color:var(--faint);padding:8px 0;">'
                '分业务收入构成数据待接入（当前数据源暂未覆盖该标的）。</div>')
    n_periods = len(SEGMENTS[0][2]) if SEGMENTS else 0

    # 各期收入占比（该条线收入 / 当期总收入 × 100）
    shares = []
    for _name, _color, revs, _margins in SEGMENTS:
        s = []
        for i in range(n_periods):
            total = sum((seg[2][i] or 0) for seg in SEGMENTS)
            v = revs[i]
            s.append((v / total * 100) if (v is not None and total > 0) else None)
        shares.append(s)

    def _table(vals_list, unit: str) -> str:
        head = "".join(f"<th>{q}</th>" for q in SEGMENT_LABELS)
        rows = []
        for (name, color, _revs, _margins), vals in zip(SEGMENTS, vals_list):
            cells = [f'<td class="row-head"><span class="seg-dot" style="background:{color}"></span>{name}</td>']
            cells += [f'<td class="num">{_fmt(v)}</td>' for v in vals]
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return (
            '<div class="table-scroll">'
            f'<table class="dense"><thead><tr><th class="name">{unit}</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            "</div>"
        )

    rev_table = _table([s[2] for s in SEGMENTS], "业务条线")
    margin_table = _table([s[3] for s in SEGMENTS], "业务条线")
    share_table = _table(shares, "业务条线")

    # 最新报告期收入占比（堆叠条，None 视为 0）
    latest = [s[2][-1] if s[2][-1] is not None else 0 for s in SEGMENTS]
    total = sum(latest)
    if total > 0:
        bar = "".join(
            f'<div class="seg" style="width:{v / total * 100:.1f}%;background:{s[1]}"></div>'
            for s, v in zip(SEGMENTS, latest)
        )
        legend = "".join(
            f'<span class="seg-legend"><span class="seg-dot" style="background:{s[1]}"></span>{s[0]} {v / total * 100:.1f}%</span>'
            for s, v in zip(SEGMENTS, latest)
        )
    else:
        bar = ""
        legend = ""

    return (
        '<div class="seg-block seg-full">'
        f'<div class="seg-block-title">收入（亿元）</div>{rev_table}'
        "</div>"
        '<div class="seg-row">'
        f'<div class="seg-block"><div class="seg-block-title">利润率（%）</div>{margin_table}</div>'
        f'<div class="seg-block"><div class="seg-block-title">收入占比（%）</div>{share_table}</div>'
        "</div>"
        f'<div class="seg-bar">{bar}</div>'
        f'<div class="seg-legend-row">{legend}<span class="seg-note">（最新报告期收入占比）</span></div>'
        f'<div class="seg-note" style="font-size:10px;color:var(--faint);margin-top:5px;">利润率口径随行业而异：金融业为利差率/利润率，制造业为毛利率（数据源披露口径）。</div>'
    )


def _pct_text(pct):
    """估值分位 → (百分比文本, css类, 定性)。"""
    if pct is None:
        return "—", "", "—"
    if pct < 30:
        return f"{pct:.0f}%", "pct-low", "偏低"
    if pct > 70:
        return f"{pct:.0f}%", "pct-high", "高位"
    return f"{pct:.0f}%", "", "合理"


def build_val_grid() -> str:
    if not VALUATION:
        return '<div style="font-size:11px;color:var(--faint);padding:8px 0;">估值数据待接入（行情接口受网络限制）。</div>'
    v = VALUATION
    pe = f"{v['pe']:.1f}<small>x</small>" if v.get("pe") else "—"
    pb = f"{v['pb']:.2f}<small>x</small>" if v.get("pb") else "—"
    dy = f"{v['dividend_yield']:.1f}<small>%</small>" if v.get("dividend_yield") else "—"
    pe_pct, pe_cls, _ = _pct_text(v.get("pe_pctile"))
    pb_pct, pb_cls, _ = _pct_text(v.get("pb_pctile"))
    return (
        '<div class="val-grid">'
        f'<div class="val-item"><div class="lbl">市盈率 PE（TTM）</div><div class="v">{pe}</div><div class="pct {pe_cls}">近10年分位 {pe_pct}</div></div>'
        f'<div class="val-item"><div class="lbl">市净率 PB（MRQ）</div><div class="v">{pb}</div><div class="pct {pb_cls}">近10年分位 {pb_pct}</div></div>'
        f'<div class="val-item"><div class="lbl">股息率</div><div class="v" style="color:var(--up)">{dy}</div><div class="pct">最新报告期</div></div>'
        "</div>"
    )


def build_market_row() -> str:
    if not VALUATION:
        return ""
    v = VALUATION
    low, now, high = v.get("price_low"), v.get("price_now"), v.get("price_high")
    if low is None or high is None or now is None:
        return ""
    pos = (now - low) / (high - low) * 100 if high > low else 50
    return (
        '<div class="market-row">'
        '<div class="price-range">'
        '<div class="pr-title">52周价格区间（元）</div>'
        f'<div class="pr-bar"><div class="pr-marker" style="left:{pos:.1f}%"></div></div>'
        '<div class="pr-labels">'
        f'<span>52周最低 <b>{low:.2f}</b></span>'
        f'<span>现价 <b>{now:.2f}</b></span>'
        f'<span>52周最高 <b>{high:.2f}</b></span>'
        "</div></div>"
        + build_consensus()
        + "</div>"
    )


def build_consensus() -> str:
    """机构评级分布 + 预测每股收益（客观第三方数据）。"""
    if not RATING:
        return ('<div class="consensus">'
                '<div>机构评级（<b>数据待接入</b>）</div>'
                '<div style="font-size:10px;color:var(--faint);margin-top:4px;">机构评级与盈利预测数据源待接入。</div>'
                "</div>")
    r = RATING
    total = r.get("total") or 0
    cats = [
        ("买入", r.get("buy"), "#c0392b"),
        ("增持", r.get("overweight"), "#e67e22"),
        ("中性", r.get("neutral"), "#95a5a6"),
        ("减持", r.get("underweight"), "#2ecc71"),
        ("卖出", r.get("sell"), "#1e8e5a"),
    ]
    segs = []
    for label, val, color in cats:
        v = int(val) if val is not None else 0
        if v > 0:
            pct = v / total * 100 if total else 0
            segs.append(f'<span style="background:{color};width:{pct:.1f}%">{label} {v}</span>')
    bar = '<div class="rating-bar">' + "".join(segs) + "</div>" if segs else ""

    eps = r.get("eps_forecast", [])
    eps_txt = " / ".join(f"{e['year']} {e['eps']:.2f}" for e in eps if e.get("eps") is not None)
    eps_line = f'<div style="margin-top:5px;">预测每股收益：{eps_txt} 元</div>' if eps_txt else ""

    # 目标价（港股经济通有，A 股无）
    tp = r.get("target_price")
    tp_line = ""
    if tp is not None:
        tp_line = f'<div style="margin-top:5px;">券商目标价均值：{tp:.2f}（第三方观点）</div>'

    return (
        '<div class="consensus">'
        f'<div>机构评级（近6个月 · <b>{int(total)}家</b>）</div>'
        + bar + eps_line + tp_line
        + '<div style="font-size:10px;color:var(--faint);margin-top:5px;">第三方机构观点汇总，非本人建议。</div>'
        "</div>"
    )


def build_graham() -> str:
    if not GRAHAM:
        return ""
    g = GRAHAM
    debt = f"{g['debt_ratio']:.1f}%" if g.get("debt_ratio") is not None else "—"
    cur = f"{g['current_ratio']:.2f}" if g.get("current_ratio") is not None else "—"
    if g.get("profit_stable") is None:
        stable = "—"
    else:
        stable = "连续正盈利" if g["profit_stable"] else "存在亏损年份"
    if g.get("net_cash") is None:
        net_cash = "—"
    else:
        net_cash = "净现金" if g["net_cash"] > 0 else "有息负债＞货币资金"
    return (
        '<div class="graham">'
        '<div class="g-title">格雷厄姆质量体检</div>'
        f'<div class="g-row"><span>资产负债率</span><b>{debt}</b></div>'
        f'<div class="g-row"><span>流动比率</span><b>{cur}</b></div>'
        f'<div class="g-row"><span>盈利稳定性（5年）</span><b>{stable}</b></div>'
        f'<div class="g-row"><span>净现金 / 有息负债</span><b>{net_cash}</b></div>'
        "</div>"
    )


def build_current_position() -> str:
    """流动状况（ValueLine Current Position）：流动资产 vs 流动负债明细（最新年报时点）。"""
    if not CURRENT_POSITION:
        return ""
    cp = CURRENT_POSITION
    year = cp.get("year")
    assets = cp.get("assets") or []
    liabs = cp.get("liabilities") or []
    wc = cp.get("working_capital")

    def _row(label, val, bold=False):
        v = f"{val:.1f}" if val is not None else "—"
        cls = ' class="total"' if bold else ""
        return f'<div class="cp-row{cls}"><span>{label}</span><b>{v}</b></div>'

    def _col(title, items, total_label):
        rows = [_row(label, val, bold=(label == total_label)) for label, val in items]
        return f'<div class="cp-col"><div class="cp-col-title">{title}</div>{"".join(rows)}</div>'

    wc_txt = f"{wc:.1f}" if wc is not None else "—"
    wc_cls = "pos" if (wc is not None and wc > 0) else "neg"

    return (
        '<div class="current-pos">'
        f'<div class="cp-title">流动状况（Current Position）<span class="cp-note">{year} 年报 · 单位：亿元</span></div>'
        '<div class="cp-grid">'
        + _col("流动资产", assets, "流动资产合计")
        + _col("流动负债", liabs, "流动负债合计")
        + "</div>"
        f'<div class="cp-wc">营运资本（流动资产 − 流动负债）：<b class="{wc_cls}">{wc_txt}</b> 亿元</div>'
        "</div>"
    )


def build_annual_rates() -> str:
    """年增长率（ValueLine Annual Rates）：销售/现金流/盈利/股息/账面价值 CAGR。"""
    if not ANNUAL_RATES:
        return ""
    labels = [
        ("sales", "销售收入"),
        ("cash_flow", "经营现金流"),
        ("earnings", "净利润"),
        ("dividends", "每股股息"),
        ("book_value", "账面价值（净资产）"),
    ]
    rows = []
    for key, label in labels:
        r = ANNUAL_RATES.get(key)
        if not r:
            continue
        c5 = f"{r['cagr5'] * 100:+.1f}%" if r.get("cagr5") is not None else "—"
        c10 = f"{r['cagr10'] * 100:+.1f}%" if r.get("cagr10") is not None else "—"
        rows.append(f'<div class="ar-row"><span>{label}</span><b>{c5}</b><b>{c10}</b></div>')
    if not rows:
        return ""
    return (
        '<div class="annual-rates">'
        '<div class="ar-title">年增长率（Annual Rates）<span class="ar-note">复合年增长率 CAGR</span></div>'
        '<div class="ar-row ar-head"><span></span><b>近5年</b><b>近10年</b></div>'
        + "".join(rows)
        + "</div>"
    )


def _narr(path, default=""):
    """从 NARRATIVE 取嵌套字段，缺失返回 default。"""
    if not NARRATIVE:
        return default
    node = NARRATIVE
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node if node else default


def build_business_map() -> str:
    """业务版图（客观）：主营业务一句话（巨潮）+ 各业务条线收入占比条（分业务构成）。"""
    if not BUSINESS_MAP:
        return ""
    bm = BUSINESS_MAP
    main = bm.get("main_business")
    segs = bm.get("segments") or []

    main_txt = f'<div class="bizmap-main">{main}</div>' if main else ""
    bars = []
    for s in segs:
        name = s.get("name", "")
        pct = s.get("pct")
        w = pct if (pct is not None) else 0
        bars.append(
            f'<div class="bizmap-row">'
            f'<span class="bizmap-name">{name}</span>'
            f'<div class="bizmap-track"><div class="bizmap-bar" style="width:{w:.1f}%"></div></div>'
            f'<span class="bizmap-val">{pct:.1f}%</span>'
            f'</div>'
        )
    seg_block = '<div class="bizmap-list">' + "".join(bars) + "</div>" if bars else ""

    if not main_txt and not seg_block:
        return ""
    return (
        '<div class="bizmap">'
        f'<div class="bizmap-title">业务版图 <span class="bizmap-note">主营业务 · 巨潮概况 | 占比 · 分业务收入</span></div>'
        f'{main_txt}{seg_block}'
        "</div>"
    )


def build_business_model() -> str:
    bm = NARRATIVE.get("business_model", {}) if NARRATIVE else {}
    rows = []
    for key, label in [("revenue_source", "盈利来源"), ("profit_structure", "盈利结构"),
                       ("moat", "护城河")]:
        val = bm.get(key, "")
        rows.append(f'<div class="biz-row"><div class="biz-k">{label}</div><div class="biz-v">{val}</div></div>')
    if not any(bm.get(k) for k in ("revenue_source", "profit_structure", "moat")):
        return '<div class="biz"><div class="biz-v" style="color:var(--faint);">商业模式待 LLM 生成</div></div>'
    return '<div class="biz">' + "".join(rows) + "</div>"


def build_competition() -> str:
    """竞争地位（客观数据）：行业营收排名 + 份额 + 同行对比。

    港股降级：无全市场营收排名接口，仅展示行业定位 + 公司介绍。
    """
    if not COMPETITION:
        return ""
    c = COMPETITION

    # 港股简化版：行业定位 + 公司介绍（无排名/份额/同行）
    if c.get("is_hk"):
        industry = c.get("industry") or "—"
        intro = c.get("company_intro") or ""
        intro_html = f'<div class="comp-intro">{intro}</div>' if intro else ""
        return (
            '<div class="competition">'
            f'<div class="comp-title">竞争地位 · {industry}'
            '<span class="comp-note">港股行业分类（恒生），营收排名数据源缺失</span></div>'
            + intro_html
            + '<div class="comp-note" style="margin-top:6px;">港股暂无全市场营收排名接口，'
              '仅展示行业定位与公司介绍。</div>'
            "</div>"
        )

    industry = c.get("industry") or "—"
    year = c.get("report_year")
    rank = c.get("rank")
    peers = c.get("peers_count")
    share = c.get("share_pct")
    revenue = c.get("revenue_yi")

    rank_txt = f"第 {rank} / {peers}" if (rank is not None and peers) else "—"
    share_txt = f"{share:.1f}%" if share is not None else "—"
    rev_txt = f"{revenue:.0f} 亿" if revenue is not None else "—"
    title = f"竞争地位 · {industry}" + (f"（{year} 年报）" if year else "")

    top = c.get("top_peers", [])
    max_rev = max((p.get("revenue_yi") or 0) for p in top) if top else 0
    bars = []
    for p in top:
        name = p.get("name", "")
        rv = p.get("revenue_yi")
        is_self = bool(p.get("is_self"))
        w = (rv / max_rev * 100) if (rv and max_rev) else 0
        cls = " self" if is_self else ""
        bars.append(
            f'<div class="peer-row{cls}">'
            f'<span class="peer-name">{name}</span>'
            f'<div class="peer-track"><div class="peer-bar" style="width:{w:.1f}%"></div></div>'
            f'<span class="peer-val">{rv:.0f}</span>'
            f'</div>'
        )

    return (
        '<div class="competition">'
        f'<div class="comp-title">{title}<span class="comp-note">营收口径：东财业绩报表</span></div>'
        '<div class="comp-grid">'
        f'<div class="comp-item"><div class="lbl">营收排名</div><div class="v">{rank_txt}</div></div>'
        f'<div class="comp-item"><div class="lbl">营收份额</div><div class="v" style="color:var(--accent-2)">{share_txt}</div></div>'
        f'<div class="comp-item"><div class="lbl">营收</div><div class="v">{rev_txt}</div></div>'
        "</div>"
        f'<div class="peer-list">{"".join(bars)}</div>'
        "</div>"
    )


def build_thesis() -> str:
    items = NARRATIVE.get("thesis", []) if NARRATIVE else []
    if not items:
        return '<ul class="thesis"><li>投资逻辑待 LLM 生成</li></ul>'
    return '<ul class="thesis">' + "".join(f"<li>{t}</li>" for t in items) + "</ul>"


def build_risks() -> str:
    items = NARRATIVE.get("risks", []) if NARRATIVE else []
    if not items:
        return '<ul class="risk"><li>风险提示待 LLM 生成</li></ul>'
    return '<ul class="risk">' + "".join(f"<li>{t}</li>" for t in items) + "</ul>"


def build_fraud() -> str:
    """财务造假检测：Beneish M-Score + 现金流背离 + 应收异常（客观算法）。"""
    if not FRAUD:
        return '<div class="fraud"><div class="f-row"><span>造假检测</span><b>数据待接入</b></div></div>'
    f = FRAUD
    rows = []

    m = f.get("mscore")
    if m:
        val = m["mscore"]
        risk = m["risk"]
        cls = "bad" if risk == "high" else "ok"
        label = "高风险" if risk == "high" else "安全"
        rows.append(f'<div class="f-row"><span>M-Score（Beneish）</span><b class="{cls}">{val:.2f} · {label}</b></div>')
    else:
        rows.append('<div class="f-row"><span>M-Score（Beneish）</span><b>数据不足</b></div>')

    cf = f.get("cashflow")
    if cf:
        ratios = " / ".join(f"{x:.1f}" if x is not None else "—" for x in cf["ratios"])
        cls = "bad" if cf["warning"] else "ok"
        txt = "背离" if cf["warning"] else "健康"
        rows.append(f'<div class="f-row"><span>经营现金流 / 净利润（近3年）</span><b class="{cls}">{ratios} · {txt}</b></div>')

    rc = f.get("receivable")
    if rc:
        cls = "bad" if rc["warning"] else "ok"
        txt = "背离" if rc["warning"] else "正常"
        rows.append(f'<div class="f-row"><span>应收增速 vs 营收增速</span><b class="{cls}">应收 {rc["ar_yoy"]:.1f}% vs 营收 {rc["rev_yoy"]:.1f}% · {txt}</b></div>')

    audit_op = f.get("audit_opinion")
    audit_level = f.get("audit_level")
    if audit_op:
        cls = {"clean": "ok", "watch": "warn", "high": "bad"}.get(audit_level, "")
        rows.append(f'<div class="f-row"><span>审计意见</span><b class="{cls}">{audit_op}</b></div>')
    else:
        rows.append('<div class="f-row"><span>审计意见</span><b>数据待接入</b></div>')

    overall = f.get("overall_risk", "low")
    cls = {"low": "ok", "medium": "warn", "high": "bad"}.get(overall, "ok")
    label = {"low": "低", "medium": "中", "high": "高"}.get(overall, "低")
    flags = f.get("flags", [])
    flag_txt = "（" + "、".join(flags) + "）" if flags else ""
    rows.append(f'<div class="f-score">综合造假风险：<b class="{cls}">{label}</b>{flag_txt}</div>')

    return '<div class="fraud">' + "".join(rows) + "</div>"


def build_verify() -> str:
    """数据校验记录：运行 validate()，与巨潮官方年报 PDF 逐项对比。"""
    from datetime import date
    today = date.today().isoformat()

    year = YEARS[-1] if YEARS else None
    if not year or not _HAS_DATA:
        return ('<div class="verify">'
                '<div><b>数据来源：</b>AKShare（主）+ 东方财富（备用）</div>'
                '<div><b>校验状态：</b>待运行（需 parquet 数据 + 官方年报）</div>'
                f'<div><b>校验日期：</b>{today}</div>'
                "</div>")

    try:
        from src.validation import validate
        result = validate(COMPANY_CODE, year)
        marks = {"一致": "✓", "差异": "✗", "缺失": "—", "接口缺失": "—"}
        rows = []
        for it in result["items"]:
            mark = marks.get(it["status"], "—")
            note = "一致" if it["status"] == "一致" else it["status"]
            rows.append(f'<div><b>{mark} {it["label"]}</b>：{note}</div>')

        # 数据交叉校验覆盖记录（接口原始值 vs 官方 PDF 金标准）
        reconcile_rows = ""
        if RECONCILE_LOG:
            items = "".join(
                f'<div>· {c["label"]}：接口 {c["api_yi"]:,.2f}亿 → 官方 {c["pdf_yi"]:,.2f}亿（差 {c["diff_pct"]:.0f}%）</div>'
                for c in RECONCILE_LOG
            )
            reconcile_rows = (
                '<div class="reconcile">'
                f'<div><b>⚠ 接口数据修正：</b>第三方接口（东财/新浪同源）在「同一控制下企业合并追溯重述」'
                f'情形下抓取错误，已用官方年报 PDF 金标准覆盖 {len(RECONCILE_LOG)} 项：</div>'
                + items + "</div>"
            )

        return (
            '<div class="verify">'
            '<div><b>数据来源：</b>AKShare（主）+ 东方财富（备用）；金标准：巨潮官方年报 PDF</div>'
            f'<div><b>校验结果：</b>{result["passed"]}/{result["total"]} 项与官方年报一致（容差 &lt;0.1%）</div>'
            + "".join(rows)
            + reconcile_rows
            + f'<div><b>校验日期：</b>{today}</div>'
            '<div><b>校验人：</b>李潇</div>'
            "</div>"
        )
    except Exception as e:
        return ('<div class="verify">'
                '<div><b>数据来源：</b>AKShare（主）+ 东方财富（备用）</div>'
                f'<div><b>校验状态：</b>未运行（{type(e).__name__}）</div>'
                f'<div><b>校验日期：</b>{today}</div>'
                "</div>")


# ============ CSS ============
CSS = """
:root {
  --ink: #1a2330;
  --muted: #5c6b7a;
  --faint: #8a97a6;
  --line: #dde3ea;
  --line-soft: #e8edf2;
  --accent: #0f3d6e;
  --accent-2: #14508c;
  --bg-soft: #f5f7fa;
  --up: #c0392b;
  --down: #1e8e5a;
  --warn: #b8860b;
  --amber-bg: #fdf6e3;
  --amber-line: #e8d5a0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: var(--ink);
  background: #e9edf1;
  -webkit-font-smoothing: antialiased;
  padding: 24px 0;
}
.page {
  width: 100%;
  max-width: 1080px;
  margin: 0 auto;
  background: #ffffff;
  box-shadow: 0 2px 16px rgba(15, 61, 110, 0.10);
  padding: 40px 48px 36px;
}
.header { display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 16px; border-bottom: 3px solid var(--accent); }
.co-name { font-size: 30px; font-weight: 700; letter-spacing: 1px; color: var(--accent); line-height: 1.15; }
.co-name .en { font-size: 14px; font-weight: 400; color: var(--faint); letter-spacing: 0.5px; margin-left: 10px; }
.co-meta { margin-top: 9px; font-size: 12px; color: var(--muted); line-height: 1.8; }
.co-meta .tag { display: inline-block; padding: 2px 9px; border-radius: 3px; font-size: 11px; margin-right: 7px; border: 1px solid var(--line); background: var(--bg-soft); color: var(--muted); }
.co-meta .code { font-weight: 600; color: var(--ink); }
.currency-note { margin-top: 4px; font-size: 11px; color: var(--accent); }
.currency-note:empty { display: none; }
.header-right { text-align: right; flex-shrink: 0; margin-left: 16px; }
.quarter { display: inline-block; background: var(--accent); color: #fff; padding: 6px 14px; border-radius: 4px; font-size: 13px; font-weight: 600; letter-spacing: 0.5px; }
.badges { margin-top: 9px; display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
.badge { font-size: 11px; padding: 3px 10px; border-radius: 3px; font-weight: 500; }
.badge.lynch { background: #eef3fb; color: var(--accent-2); border: 1px solid #c9d8ec; }
.badge.graham { background: #eef7f1; color: var(--down); border: 1px solid #c4e3d2; }
.summary { margin: 16px 0; padding: 12px 16px; background: var(--bg-soft); border-left: 3px solid var(--accent-2); font-size: 13px; line-height: 1.8; color: #33404f; }
.summary b { color: var(--ink); }

.section { margin-bottom: 20px; }
.sec-title { font-size: 15px; font-weight: 700; color: var(--accent); padding-bottom: 6px; margin-bottom: 11px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: baseline; }
.sec-title .hint { font-size: 11px; font-weight: 400; color: var(--faint); }
.sub-title { font-size: 12px; font-weight: 600; color: var(--muted); margin: 14px 0 7px; }
.disclaim { font-size: 10px; color: var(--faint); margin-bottom: 8px; }

/* 全历史宽表 */
.table-scroll { overflow-x: auto; }
table.dense { width: 100%; border-collapse: collapse; font-size: 10px; table-layout: fixed; }
table.dense th, table.dense td { padding: 4px 3px; text-align: right; border-bottom: 1px solid var(--line-soft); overflow: hidden; }
table.dense th.name, table.dense td.name { text-align: left; width: 118px; }
table.dense th { background: var(--bg-soft); color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); font-size: 9.5px; }
table.dense td.num { font-variant-numeric: tabular-nums; }
table.dense tr.group td { background: #eef3fb; color: var(--accent-2); font-weight: 600; font-size: 10px; text-align: left; border-bottom: 1px solid var(--line); }
table.dense .row-head { font-weight: 500; color: #33404f; }

/* 业务收入构成 */
.seg-row { display: flex; gap: 18px; }
.seg-block { flex: 1; }
.seg-full { margin-bottom: 14px; }
.seg-block-title { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 6px; }

/* 商业模式 */
.biz-row { display: flex; padding: 7px 0; border-bottom: 1px dashed var(--line-soft); font-size: 12px; line-height: 1.7; }
.biz-row:last-child { border-bottom: none; }
.biz-k { flex: 0 0 78px; font-weight: 700; color: var(--accent); }
.biz-v { flex: 1; color: #33404f; }

/* 业务版图（客观数据） */
.bizmap { margin-bottom: 12px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.bizmap-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; }
.bizmap-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.bizmap-main { font-size: 11.5px; color: var(--ink); line-height: 1.6; margin: 8px 0 10px; }
.bizmap-list { display: flex; flex-direction: column; gap: 5px; }
.bizmap-row { display: flex; align-items: center; gap: 8px; font-size: 10.5px; color: var(--muted); }
.bizmap-name { flex: 0 0 88px; text-align: right; overflow: hidden; white-space: nowrap; }
.bizmap-track { flex: 1; height: 9px; background: #eef1f5; border-radius: 4px; overflow: hidden; }
.bizmap-bar { height: 100%; background: var(--accent); border-radius: 4px; }
.bizmap-val { flex: 0 0 40px; text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }

/* 竞争地位（客观数据） */
.competition { margin-top: 14px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.comp-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; }
.comp-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.comp-intro { font-size: 11px; line-height: 1.6; color: var(--muted); margin-top: 8px; }
.comp-grid { display: flex; gap: 12px; margin: 10px 0; }
.comp-item { flex: 1; padding: 9px 12px; background: #fff; border: 1px solid var(--line-soft); border-radius: 6px; }
.comp-item .lbl { font-size: 10px; color: var(--muted); }
.comp-item .v { font-size: 17px; font-weight: 700; color: var(--ink); margin-top: 3px; font-variant-numeric: tabular-nums; }
.peer-list { display: flex; flex-direction: column; gap: 4px; }
.peer-row { display: flex; align-items: center; gap: 8px; font-size: 10.5px; color: var(--muted); }
.peer-row.self { color: var(--accent-2); font-weight: 600; }
.peer-name { flex: 0 0 72px; text-align: right; overflow: hidden; white-space: nowrap; }
.peer-track { flex: 1; height: 8px; background: #eef1f5; border-radius: 4px; overflow: hidden; }
.peer-bar { height: 100%; background: #aebccb; border-radius: 4px; }
.peer-row.self .peer-bar { background: var(--accent-2); }
.peer-val { flex: 0 0 56px; text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }

/* 业务收入构成 */
.seg-dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.seg-bar { display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 12px; }
.seg-bar .seg { height: 100%; }
.seg-legend-row { font-size: 10px; color: var(--faint); margin-top: 7px; display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }
.seg-legend { display: inline-flex; align-items: center; gap: 3px; color: var(--muted); }
.seg-note { color: var(--faint); }

/* 估值三件套（横排） */
.val-grid { display: flex; gap: 12px; }
.val-item { flex: 1; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: #fff; }
.val-item .lbl { font-size: 11px; color: var(--muted); }
.val-item .v { font-size: 21px; font-weight: 700; color: var(--ink); margin: 4px 0; font-variant-numeric: tabular-nums; }
.val-item .v small { font-size: 12px; font-weight: 400; color: var(--muted); }
.val-item .pct { font-size: 10px; color: var(--faint); }
.pct-low { color: var(--up) !important; font-weight: 600; }
.pct-high { color: var(--down) !important; font-weight: 600; }

/* 市场数据（52周 + 机构预期 横排） */
.market-row { display: flex; gap: 12px; margin-top: 12px; }
.market-row > div { flex: 1; }
.price-range { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.price-range .pr-title { font-size: 11px; color: var(--muted); margin-bottom: 9px; }
.pr-bar { position: relative; height: 8px; background: #eef1f5; border-radius: 4px; margin-bottom: 7px; }
.pr-marker { position: absolute; top: -3px; width: 2px; height: 14px; background: var(--accent-2); border-radius: 1px; }
.pr-labels { display: flex; justify-content: space-between; font-size: 10.5px; color: var(--muted); }
.pr-labels b { color: var(--ink); font-variant-numeric: tabular-nums; }
.consensus { padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; font-size: 11px; color: var(--muted); line-height: 1.9; }
.consensus b { color: var(--ink); }
.rating-bar { display: flex; height: 18px; border-radius: 4px; overflow: hidden; margin: 6px 0; }
.rating-bar span { display: flex; align-items: center; justify-content: center; font-size: 10px; color: #fff; white-space: nowrap; }
.tp-grid { display: flex; gap: 8px; margin: 7px 0; }
.tp-cell { flex: 1; text-align: center; padding: 7px 4px; background: #fff; border: 1px solid var(--line-soft); border-radius: 6px; }
.tp-cell .k { font-size: 9.5px; color: var(--faint); }
.tp-cell .v { font-size: 15px; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
.rating-bar { display: flex; height: 11px; border-radius: 5px; overflow: hidden; margin: 6px 0 5px; }
.rating-bar .seg { height: 100%; }
.rating-legend { font-size: 10px; color: var(--faint); display: flex; gap: 12px; }

/* 格雷厄姆体检 */
.graham { margin-top: 12px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.graham .g-title { font-size: 12px; font-weight: 700; color: var(--accent); margin-bottom: 7px; }
.graham .g-row { display: flex; justify-content: space-between; font-size: 11px; padding: 3px 0; color: var(--muted); }
.graham .g-row b { color: var(--ink); font-weight: 600; }
.graham .g-score { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11.5px; color: var(--ink); }
.graham .g-score b { color: var(--down); }

/* ValueLine 统计：流动状况 + 年增长率 */
.current-pos { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.cp-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 9px; }
.cp-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.cp-grid { display: flex; gap: 16px; }
.cp-col { flex: 1; }
.cp-col-title { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 5px; padding-bottom: 4px; border-bottom: 1px solid var(--line); }
.cp-row { display: flex; justify-content: space-between; font-size: 11px; padding: 3px 0; color: var(--muted); }
.cp-row b { color: var(--ink); font-weight: 500; font-variant-numeric: tabular-nums; }
.cp-row.total { border-top: 1px dashed var(--line); margin-top: 3px; padding-top: 5px; }
.cp-row.total span { font-weight: 600; color: var(--ink); }
.cp-row.total b { font-weight: 700; color: var(--accent-2); }
.cp-wc { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11.5px; color: var(--muted); }
.cp-wc b { font-variant-numeric: tabular-nums; }
.cp-wc b.pos { color: var(--down); }
.cp-wc b.neg { color: var(--up); }

.annual-rates { margin-top: 12px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.ar-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.ar-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.ar-row { display: grid; grid-template-columns: 1fr 80px 80px; font-size: 11px; padding: 3px 0; color: var(--muted); }
.ar-row span { color: #33404f; }
.ar-row b { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); font-weight: 600; }
.ar-row.ar-head { color: var(--faint); font-size: 10px; border-bottom: 1px solid var(--line-soft); margin-bottom: 3px; }
.ar-row.ar-head b { color: var(--faint); font-weight: 500; }

/* 财务造假检测 */
.fraud { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.fraud .f-row { display: flex; justify-content: space-between; font-size: 11px; padding: 4px 0; color: var(--muted); }
.fraud .f-row b { color: var(--ink); font-weight: 600; }
.fraud .f-row b.ok { color: var(--down); }
.fraud .f-row b.warn { color: var(--warn); }
.fraud .f-row b.bad { color: var(--up); }
.fraud .f-score { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11.5px; color: var(--ink); }
.fraud .f-score b.ok { color: var(--down); }

/* 列表 */
.thesis, .risk { list-style: none; }
.thesis li, .risk li { font-size: 12px; line-height: 1.7; color: #33404f; padding: 5px 0 5px 18px; position: relative; border-bottom: 1px dashed var(--line-soft); }
.thesis li:last-child, .risk li:last-child { border-bottom: none; }
.thesis li::before { content: ""; position: absolute; left: 3px; top: 12px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent-2); }
.risk li::before { content: "!"; position: absolute; left: 3px; top: 6px; font-size: 11px; font-weight: 700; color: var(--warn); }

/* 数据校验 */
.verify { font-size: 11px; color: var(--faint); line-height: 1.8; }
.verify b { color: var(--muted); font-weight: 600; }
.reconcile { margin-top: 8px; padding: 8px 10px; background: var(--amber-bg); border: 1px solid var(--amber-line); border-radius: 6px; color: var(--warn); line-height: 1.7; }
.reconcile b { color: #8a6d0b; }
.footer { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 10px; color: var(--faint); line-height: 1.7; }
@media print { body { background: #fff; padding: 0; } .page { box-shadow: none; margin: 0; width: 100%; } }

/* ===== 移动端响应式（手机浏览器直开，上下滑动长图体验） ===== */
@media (max-width: 768px) {
  body { padding: 0; background: #fff; }
  .page { max-width: 100%; box-shadow: none; padding: 18px 14px 22px; }
  .header { flex-direction: column; gap: 12px; }
  .header-right { text-align: left; margin-left: 0; align-self: flex-start; }
  .badges { align-items: flex-start; }
  .co-name { font-size: 22px; }
  .co-name .en { display: block; margin-left: 0; margin-top: 3px; }

  /* 横排卡片改纵向堆叠 */
  .biz-row { flex-direction: column; gap: 2px; }
  .biz-k { flex: none; }
  .val-grid, .market-row, .cp-grid, .comp-grid, .seg-row { flex-direction: column; gap: 10px; }
  .tp-grid { flex-wrap: wrap; }

  /* 业务版图 / 竞争地位 */
  .bizmap-name { flex-basis: 60px; }
  .peer-name { flex-basis: 56px; }

  /* 宽表保持横向滚动（.table-scroll 已有 overflow-x:auto） */
  table.dense { font-size: 9px; }
  table.dense th.name, table.dense td.name { width: 88px; }

  .sec-title { font-size: 14px; }
  .summary { font-size: 12px; }
  .thesis li, .risk li { font-size: 12px; }
}
"""

# ============ HTML 骨架（单栏纵向长图） ============
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ValueLine 一页研报 · 模板 v1.5</title>
<style>@@CSS@@</style>
</head>
<body>
<div class="page">

  <div class="header">
    <div>
      <div class="co-name">@@COMPANY_NAME@@</div>
      <div class="co-meta">
        <span class="code">@@COMPANY_CODE@@</span>
        <span class="tag">@@INDUSTRY@@</span>
        <div style="margin-top:3px;">报告期：@@REPORT_PERIOD@@ · 发布日期：@@PUBLISH_DATE@@</div>
        <div class="currency-note">@@CURRENCY_NOTE@@</div>
      </div>
    </div>
    <div class="header-right">
      <span class="quarter">@@REPORT_PERIOD@@ 更新</span>
      <div class="badges">
        <span class="badge lynch">林奇分类：@@LYNCH_TYPE@@</span>
        <span class="badge graham">格雷厄姆质量：@@GRAHAM_BADGE@@</span>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="sec-title">商业模式 <span class="hint">靠什么赚钱 · 竞争地位 · 护城河</span></div>
@@BUSINESS_MAP@@
@@BIZ@@
@@COMPETITION@@
  </div>

  <div class="section">
    <div class="sec-title">核心财务数据（上市以来全历史 @@YEAR_RANGE@@） <span class="hint">单位：亿元 / 亿股 / %</span></div>
@@TABLE@@
    <div class="sub-title">近两年季度（@@QUARTER_RANGE@@）</div>
@@QUARTER_TABLE@@
    <div style="font-size:10px;color:var(--faint);margin-top:6px;">利润表为单季度值，资产负债表 / 股本为季度末时点值。</div>

    <div class="sub-title">业务收入构成（@@SEGMENT_RANGE@@）</div>
@@SEGMENTS@@
    <div style="font-size:10px;color:var(--faint);margin-top:3px;">注：示例数据，仅演示模板版式，非实时行情，不作投资依据；正式版覆盖招股书及上市前披露数据。</div>
  </div>

  <div class="section">
    <div class="sec-title">经营统计（ValueLine 口径） <span class="hint">流动状况 · 年增长率</span></div>
@@CURRENT_POSITION@@
@@ANNUAL_RATES@@
  </div>

  <div class="section">
    <div class="sec-title">估值与市场 <span class="hint">数据来源：百度估值 + 财报计算</span></div>
    <div class="disclaim">市场数据与第三方机构观点汇总，非投资建议。</div>
@@VAL_GRID@@
@@MARKET_ROW@@
@@GRAHAM@@
  </div>

  <div class="section">
    <div class="sec-title">投资逻辑</div>
@@THESIS@@
  </div>

  <div class="section">
    <div class="sec-title">风险提示</div>
@@RISKS@@
  </div>

  <div class="section">
    <div class="sec-title">财务造假检测 <span class="hint">Beneish M-Score</span></div>
@@FRAUD@@
    <div style="font-size:10px;color:var(--faint);margin-top:5px;">M-Score 阈值 -1.78（Beneish 1999 模型），基于近两年财报计算，仅供参考，不构成投资建议。</div>
  </div>

  <div class="section">
    <div class="sec-title">数据校验记录</div>
@@VERIFY@@
  </div>

  <div class="footer">
    本页为个人投研研究记录，仅供学习交流，不构成任何投资建议或买卖依据。52周价格、机构评级与盈利预测均为公开市场数据及第三方机构观点，非本人建议。数据可能存在误差或滞后，请以公司官方披露及监管文件为准。公开版遵循「完全去操作」原则，不含本人操作建议。
  </div>

</div>
</body>
</html>
"""


def _reconcile(code: str) -> list[dict]:
    """生成报告前，用官方年报 PDF 金标准交叉校验并覆盖接口错误字段。

    背景：东财/新浪等第三方接口同源，在「同一控制下企业合并追溯重述」等特殊情形下
    会抓取错误（如神华 2025 年总资产 9038 亿 vs 官方 6278 亿）。此步骤在渲染前用官方
    年报 PDF 的三张主表（资产负债表 + 利润表 + 现金流量表）覆盖错误字段，保证报告
    数据可信。失败则降级跳过。
    """
    try:
        from src.validation import reconcile_all, load_reconcile_log
        import pandas as pd
        bs_path = Path("data/raw") / code / "balance_sheet.parquet"
        if not bs_path.exists():
            return []
        bs = pd.read_parquet(bs_path)
        d = pd.to_datetime(bs["report_date"])
        annual_dates = d[d.dt.month == 12]
        if annual_dates.empty:
            return []
        year = int(annual_dates.dt.year.max())  # 最新年报年份（12-31），非季度
        result = reconcile_all(code, year)
        n = sum(len(c) for c in result["corrections"].values())
        if n:
            print(f"[reconcile] {code} {year} 已用官方PDF金标准覆盖 {n} 个接口错误字段（三表）")
        # 读历史覆盖记录（parquet 已覆盖后本次可能返回空，但记录已落盘）
        log = load_reconcile_log(code, year)
        if log:
            print(f"[reconcile] {code} {year} 历史修正记录 {len(log)} 项（官方PDF金标准）")
        return log
    except Exception as e:
        print(f"[reconcile] 跳过（{type(e).__name__}: {e}）")
        return []


def _load_real_data(code: str) -> dict | None:
    """尝试加载真实数据，失败返回 None。"""
    if not _HAS_DATA:
        return None
    try:
        return build_template_data(code)
    except Exception as e:
        print(f"[build_valueline] 加载真实数据失败，降级为示例数据: {e}")
        return None


def build(code: str = "601088", daily: bool = False) -> None:
    global YEARS, FINANCIALS, QUARTER_LABELS, QUARTERLY, SEGMENT_LABELS, SEGMENTS, VALUATION, GRAHAM, RATING, FRAUD, COMPETITION, BUSINESS_MAP, CURRENT_POSITION, ANNUAL_RATES, COMPANY_NAME, COMPANY_CODE, NARRATIVE, RECONCILE_LOG, CURRENCY_NOTE
    # 货币口径：港股财务数据已换算人民币，但股价仍为港币，需标注避免误读
    CURRENCY_NOTE = (
        "港股标的 · 财务数据已按汇率换算为人民币，股价/市值为港币"
        if _is_hk(code) else ""
    )
    # 先做数据交叉校验（官方 PDF 金标准覆盖接口错误字段）。
    # 每日行情刷新（--daily）跳过：财务数据未变，PDF 校验/LLM 叙事无需重跑，只更新估值板块。
    RECONCILE_LOG = ([] if daily else _reconcile(code)) if _HAS_DATA else []
    real = _load_real_data(code)
    if real:
        YEARS = real["years"]
        FINANCIALS = real["financials"]
        QUARTER_LABELS = real["quarter_labels"]
        QUARTERLY = real["quarterly"]
        report_period = real["report_period"]
        if real["segments"]:
            SEGMENT_LABELS = real["segment_labels"]
            SEGMENTS = real["segments"]
        else:
            # 无分业务构成数据（如港股标的）时清空，避免 fallback 到神华示例数据
            SEGMENT_LABELS = []
            SEGMENTS = []
        VALUATION = real["valuation"]
        GRAHAM = real["graham"]
        RATING = real.get("rating")
        FRAUD = real.get("fraud")
        COMPETITION = real.get("competition")
        BUSINESS_MAP = real.get("business_map")
        CURRENT_POSITION = real.get("current_position")
        ANNUAL_RATES = real.get("annual_rates")
        if real["company_name"]:
            COMPANY_NAME = real["company_name"]
        COMPANY_CODE = code
        # LLM 生成叙事层（数据先行）。每日刷新跳过（财务数据未变，叙事不变，省 token）
        NARRATIVE = None
        if (not daily) and _HAS_LLM and real.get("narrative_data"):
            NARRATIVE = generate_narrative(real["narrative_data"])
        data_src = f"真实数据 {code}"
    else:
        report_period = "2026Q2"
        data_src = "示例数据"

    year_range = f"{YEARS[0]}–{YEARS[-1]}"
    quarter_range = f"{QUARTER_LABELS[0]}–{QUARTER_LABELS[-1]}"
    segment_range = f"{SEGMENT_LABELS[0]}–{SEGMENT_LABELS[-1]}" if SEGMENT_LABELS else ""
    publish_date = "2026-08-25"

    industry = (COMPETITION or {}).get("industry") or _narr(["industry"], "行业待接入")
    lynch_type = _narr(["lynch_type"], "待分析")
    graham_badge = _narr(["graham_badge"], "待分析")

    html = (
        TEMPLATE
        .replace("@@CSS@@", CSS)
        .replace("@@TABLE@@", build_table())
        .replace("@@QUARTER_TABLE@@", build_quarter_table())
        .replace("@@SEGMENTS@@", build_segments())
        .replace("@@YEAR_RANGE@@", year_range)
        .replace("@@QUARTER_RANGE@@", quarter_range)
        .replace("@@SEGMENT_RANGE@@", segment_range)
        .replace("@@VAL_GRID@@", build_val_grid())
        .replace("@@MARKET_ROW@@", build_market_row())
        .replace("@@GRAHAM@@", build_graham())
        .replace("@@BUSINESS_MAP@@", build_business_map())
        .replace("@@BIZ@@", build_business_model())
        .replace("@@COMPETITION@@", build_competition())
        .replace("@@CURRENT_POSITION@@", build_current_position())
        .replace("@@ANNUAL_RATES@@", build_annual_rates())
        .replace("@@THESIS@@", build_thesis())
        .replace("@@RISKS@@", build_risks())
        .replace("@@VERIFY@@", build_verify())
        .replace("@@FRAUD@@", build_fraud())
        .replace("@@COMPANY_NAME@@", COMPANY_NAME)
        .replace("@@COMPANY_CODE@@", COMPANY_CODE)
        .replace("@@INDUSTRY@@", industry)
        .replace("@@REPORT_PERIOD@@", report_period)
        .replace("@@PUBLISH_DATE@@", publish_date)
        .replace("@@CURRENCY_NOTE@@", CURRENCY_NOTE)
        .replace("@@LYNCH_TYPE@@", lynch_type)
        .replace("@@GRAHAM_BADGE@@", graham_badge)
    )

    root = Path(__file__).resolve().parent.parent

    # 1. templates/valueline.html（预览/调版式）
    tpl_out = root / "templates" / "valueline.html"
    tpl_out.write_text(html, encoding="utf-8")

    # 2. reports/{报告期}/{code}.html（归档报告）
    report_out = root / "reports" / report_period / f"{code}.html"
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(html, encoding="utf-8")

    print(f"generated:")
    print(f"  预览: {tpl_out}")
    print(f"  归档: {report_out}")
    print(f"  数据来源: {data_src}")
    print(f"  年度: {year_range} ({len(YEARS)} 年), 季度: {quarter_range} ({len(QUARTER_LABELS)} 季)")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "601088"
    daily = "--daily" in sys.argv
    build(code, daily=daily)
