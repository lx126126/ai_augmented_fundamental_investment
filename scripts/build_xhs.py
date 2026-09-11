#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ValueLine → 小红书轮播图生成器。

一条笔记 9 张图，回答读者关于这家公司的九个问题：
    1 封面      这是谁、现在什么价、一句话定位
    2 业务产品  它是做什么的、靠哪几个产品赚钱
    3 渠道模式  酒是怎么卖出去的、卖给谁
    4 盈利能力  赚钱的效率有多高、成本花在哪
    5 成长轨迹  过去五年长了多少、最近一期怎么样
    6 估值水平  现在贵还是便宜、处在历史什么位置
    7 股东回报  分红给了多少、给的比例变了没有
    8 现金流    现金流的表观变化背后是什么
    9 风险提示  读这家公司要盯住什么

设计原则：
- 零观点：只呈现客观数据与事实陈述，无目标价 / 多空 / 买卖建议。
- 手机逻辑宽 390px，device_scale_factor=3 → 1170px 高清（小红书不压缩）。
- 红涨绿跌（中国习惯）。
- 每个数字都能在报告里对上；口径不一致处必须标注（如收入占比来自半年报、
  分切面毛利率来自上一份年报）。

用法：
    python scripts/build_xhs.py 600519 [-o reports/xhs]
"""
from __future__ import annotations

import argparse
import html as _html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.adapter import build_template_data  # noqa: E402

# 配色（对齐报告品牌色）
C = {
    "ink": "#1a2330",
    "muted": "#5a6b7c",
    "faint": "#8a97a6",
    "accent": "#0f3d6e",
    "accent2": "#14508c",
    "accent3": "#7fa8d4",
    "bg": "#f5f7fa",
    "card": "#ffffff",
    "line": "#e3e8ef",
    "up": "#c0392b",     # 红（涨 / 正向）
    "down": "#1e8e5a",   # 绿（跌 / 负向）
    "gold": "#b8860b",
    "soft": "#eef3fb",
}

TOTAL = 9

SLIDE_NAMES = [
    "封面", "业务产品", "渠道模式", "盈利能力", "成长轨迹",
    "估值水平", "股东回报", "现金流", "风险提示",
]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _n(v, digits: int = 1, suffix: str = "") -> str:
    """数值 → 展示串（None / 非数 → —）。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:,.{digits}f}{suffix}"


def _esc(s) -> str:
    return _html.escape(str(s)) if s is not None else ""


def _yoy(v, digits: int = 1, na: str = "—") -> str:
    """同比 → 带颜色与方向的 HTML。"""
    if v is None:
        return f'<span class="flat">{na}</span>'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return f'<span class="flat">{na}</span>'
    if f > 0:
        return f'<span class="upv">+{f:.{digits}f}%</span>'
    if f < 0:
        return f'<span class="dnv">{f:.{digits}f}%</span>'
    return f'<span class="flat">0.0%</span>'


def _pct_plain(v, digits: int = 1) -> str:
    """同比 → 纯文本（用在已带颜色的容器里，避免红绿嵌套两层）。"""
    try:
        return f"{float(v):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _yoy_bar(v, width: int = 100) -> str:
    """同比 → 迷你横条（红涨绿跌），用于直观比较。"""
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    ratio = min(abs(f) / 40.0, 1.0)      # 40% 封顶
    w = max(round(ratio * width), 3)
    color = C["up"] if f >= 0 else C["down"]
    return (
        f'<div class="mbar"><div class="mbar-fill" '
        f'style="width:{w}px;background:{color}"></div></div>'
    )


def _pctile_text(p) -> str:
    if p is None:
        return "分位 —"
    try:
        return f"近10年 {float(p):.1f}% 分位"
    except (TypeError, ValueError):
        return "分位 —"


def _pctile_zone(p) -> str:
    """分位 → 区间词。只说「在历史序列里的位置」，不含好坏判断。"""
    if p is None:
        return ""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return ""
    if p < 30:
        return "历史低位"
    if p > 70:
        return "历史高位"
    return "历史中位"


def _load(code: str):
    """加载报告数据 + LLM 叙事（失败降级为 None）。"""
    data = build_template_data(code)
    narrative = None
    try:
        from src.report.llm import generate_narrative
        nd = data.get("narrative_data")
        if nd:
            narrative = generate_narrative(nd)
    except Exception:
        narrative = None
    if narrative is None:                      # 兜底：直接读缓存
        try:
            cache = Path("data/cache/narrative") / f"{code}.json"
            if cache.exists():
                narrative = json.loads(cache.read_text(encoding="utf-8")).get("narrative")
        except Exception:
            narrative = None
    return data, narrative


# --------------------------------------------------------------------------- #
# 页面骨架
# --------------------------------------------------------------------------- #
def _slide(idx: int, title: str, subtitle: str, body: str, foot: str = "") -> str:
    return (
        '<section class="slide">'
        '<div class="hd">'
        f'<span class="brand">ValueLine 一页研报</span>'
        f'<span class="pager">{idx} / {TOTAL}</span>'
        "</div>"
        f'<div class="ttl">{_esc(title)}'
        + (f'<span class="sub">{_esc(subtitle)}</span>' if subtitle else "")
        + "</div>"
        f'<div class="body">{body}</div>'
        + (f'<div class="foot">{foot}</div>' if foot else "")
        + "</section>"
    )


def _kv(rows: list[tuple[str, str, str]]) -> str:
    """键值行：(标签, 值HTML, 备注HTML)。"""
    out = '<div class="kv">'
    for k, v, note in rows:
        out += (
            '<div class="kv-row">'
            f'<div class="kv-k">{k}</div>'
            f'<div class="kv-v">{v}</div>'
            + (f'<div class="kv-note">{note}</div>' if note else "")
            + "</div>"
        )
    return out + "</div>"


def _note(text: str) -> str:
    """一句话解读（帮助读者理解，仍为客观陈述）。"""
    return f'<div class="takeaway"><span class="tk-lbl">一句话</span>{text}</div>'


# --------------------------------------------------------------------------- #
# 图 1 · 封面
# --------------------------------------------------------------------------- #
def slide_cover(data, narrative) -> str:
    name = data.get("company_name") or "—"
    comp = data.get("competition") or {}
    nd = data.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    val = data.get("valuation") or {}
    period = data.get("report_period") or "—"

    industry = comp.get("industry") or "—"
    rank = comp.get("rank")
    peers = comp.get("peers_count")
    rank_txt = f"{industry} 行业第 {rank}" + (f" / {peers} 家" if peers else "")

    lynch = (narrative or {}).get("lynch_type") or "—"
    gbadge = (narrative or {}).get("graham_badge") or "—"
    gbadge_short = gbadge.split("（")[0].strip() if gbadge else "—"

    stats = [
        ("总市值", _n((val.get("market_cap") or 0) / 10000, 2, " 万亿") if val.get("market_cap") else "—"),
        ("2025 营收", _n(latest.get("revenue"), 1, " 亿")),
        ("2025 净利", _n(latest.get("net_profit"), 1, " 亿")),
        ("ROE", _n(latest.get("roe"), 2, "%")),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="stat-n">{v}</div><div class="stat-l">{k}</div></div>'
        for k, v in stats
    )

    body = f"""
    <div class="co">
      <div class="co-name">{_esc(name)}</div>
      <div class="co-meta"><span class="code">{_esc(data.get("_code") or "")}</span><span class="sep">·</span>{_esc(rank_txt)}</div>
    </div>
    <div class="price">
      <div class="price-num">¥ {_n(val.get("price_now"), 2)}</div>
      <div class="price-lbl">{_esc(val.get("quote_date") or "")} 收盘</div>
    </div>
    <div class="stat-grid">{stat_html}</div>
    <div class="badges">
      <div class="badge"><span class="bd-k">林奇分类</span><span class="bd-v">{_esc(lynch)}</span></div>
      <div class="badge"><span class="bd-k">格雷厄姆质量</span><span class="bd-v">{_esc(gbadge_short)}</span></div>
    </div>
    <div class="cover-tip">往下 8 张 · 从「它做什么」看到「该盯住什么」</div>
    """
    foot = f'更新于 {period} · 数据来源：东方财富 / 巨潮资讯年报'
    return _slide(1, "", "", body, foot)


# --------------------------------------------------------------------------- #
# 图 2 · 业务与产品
# --------------------------------------------------------------------------- #
def slide_business(data, narrative) -> str:
    bm = (narrative or {}).get("business_model") or {}
    main = bm.get("revenue_source") or data.get("business_map", {}).get("main_business") or "—"
    os_ = data.get("operating_structure") or {}
    slices = (os_.get("切片") or {})
    prods = slices.get("产品") or []
    prod_period = os_.get("期间") or "—"

    rows = ""
    for p in prods:
        pct = p.get("占比_pct") or 0
        w = max(min(float(pct), 100), 2)
        rows += (
            '<div class="seg">'
            f'<div class="seg-top"><span class="seg-name">{_esc(p.get("名称"))}</span>'
            f'<span class="seg-pct">{_n(pct, 1, "%")}</span></div>'
            f'<div class="seg-bar"><div class="seg-fill" style="width:{w}%"></div></div>'
            f'<div class="seg-meta"><span>收入 {_n(p.get("收入_亿元"), 1, " 亿")}</span>'
            f'<span>同比 {_yoy(p.get("收入同比_pct"))}</span>'
            f'<span>毛利率 {_n(p.get("毛利率_pct"), 1, "%")}</span></div>'
            "</div>"
        )

    # 产销量：年报「产品情况」表有茅台酒 / 系列酒拆分，优先展示拆分。
    #   只给「酒类合计」会让人把 11.6 万吨读成「茅台酒产量」——那其实含系列酒。
    prod_row = (os_.get("产销量") or [{}])[0]
    prod_scope = os_.get("产销量口径") or "年报"
    split = prod_row.get("拆分") or []
    inv = prod_row.get("库存拆分") or {}
    pf = ""
    if prod_row and (split or prod_row.get("生产量")):
        cards = ""
        for r in split:
            cards += (
                '<div class="pline">'
                '<div class="pline-h">'
                f'<span class="pline-n">{_esc(r.get("档次"))}</span>'
                f'<span class="pline-r">收入 {_n(r.get("收入_亿元"), 0, " 亿")}'
                f'（{_yoy(r.get("收入同比_pct"))}）</span>'
                "</div>"
                '<div class="pline-grid">'
                f'<div class="pline-cell"><span class="pline-k">产量</span>'
                f'<span class="pline-v">{_n((r.get("产量") or 0) / 10000, 2, " 万吨")}'
                f'<span class="pline-y">{_yoy(r.get("产量同比_pct"))}</span></span></div>'
                f'<div class="pline-cell"><span class="pline-k">销量</span>'
                f'<span class="pline-v">{_n((r.get("销量") or 0) / 10000, 2, " 万吨")}'
                f'<span class="pline-y">{_yoy(r.get("销量同比_pct"))}</span></span></div>'
                "</div></div>"
            )
        if not cards:          # 拆分缺失时退回合计三格，口径已写在标题与脚注里
            cards = (
                '<div class="mini-grid">'
                f'<div class="mini"><div class="mini-n">'
                f'{_n((prod_row.get("生产量") or 0) / 10000, 2, " 万吨")}</div>'
                f'<div class="mini-l">产量 · 酒类合计</div>'
                f'<div class="mini-y">{_yoy(prod_row.get("生产量同比_pct"))}</div></div>'
                f'<div class="mini"><div class="mini-n">'
                f'{_n((prod_row.get("销售量") or 0) / 10000, 2, " 万吨")}</div>'
                f'<div class="mini-l">销量 · 酒类合计</div>'
                f'<div class="mini-y">{_yoy(prod_row.get("销售量同比_pct"))}</div></div>'
                f'<div class="mini"><div class="mini-n">'
                f'{_n((prod_row.get("库存量") or 0) / 10000, 2, " 万吨")}</div>'
                '<div class="mini-l">库存量</div>'
                '<div class="mini-y flat">基酒为主</div></div>'
                "</div>"
            )
        notes = ""
        if split:
            notes += (
                f'<div class="pnote">酒类合计 产量 '
                f'{_n((prod_row.get("生产量") or 0) / 10000, 2, " 万吨")}'
                f'（{_pct_plain(prod_row.get("生产量同比_pct"))}）'
                f'· 销量 {_n((prod_row.get("销售量") or 0) / 10000, 2, " 万吨")}'
                f'（{_pct_plain(prod_row.get("销售量同比_pct"))}）</div>'
            )
        if inv:
            notes += (
                f'<div class="pnote">库存 {_n((prod_row.get("库存量") or 0) / 10000, 2, " 万吨")}'
                f' ＝ 半成品基酒 {_n((inv.get("半成品酒") or 0) / 10000, 2, " 万吨")}'
                f' ＋ 成品酒 {_n((inv.get("成品酒") or 0) / 10000, 2, " 万吨")}</div>'
            )
        pf = (
            f'<div class="sec-t">产销量 <span class="sec-s">{_esc(prod_scope)} · 吨</span></div>'
            f'<div class="plist">{cards}</div>{notes}'
            + (f'<div class="pnote">口径：{_esc(prod_row.get("口径"))}</div>'
               if prod_row.get("口径") else "")
        )

    body = (
        f'<div class="lead">{_esc(main)}</div>'
        f'<div class="sec-t">产品结构 <span class="sec-s">{_esc(prod_period)}</span></div>'
        f'<div class="segs">{rows}</div>'
        + pf
        + _note("「产量」是当年酿出的基酒，不是卖出去的酒——茅台酒从生产到出厂至少五年，"
                "所以产量和库存量看的是「几年后能卖多少」。这也是为什么产量会明显大于销量，"
                "且茅台酒与系列酒的产量几乎各占一半。")
    )
    foot = (f'收入占比与同比 = {os_.get("期间") or "半年报"}「销售情况」表；'
            f'毛利率口径为 {os_.get("毛利率口径") or "上一份年报"}；'
            f'产销量口径为 {prod_scope}「产品情况」「产品期末库存量」表')
    return _slide(2, "它是做什么的", "业务与产品结构", body, foot)


# --------------------------------------------------------------------------- #
# 图 3 · 渠道与模式
# --------------------------------------------------------------------------- #
def slide_channel(data) -> str:
    os_ = data.get("operating_structure") or {}
    slices = os_.get("切片") or {}
    chans = slices.get("渠道") or []
    regions = slices.get("地区") or []
    imt = os_.get("i茅台_亿元")
    dealers = os_.get("经销商") or {}

    chan_html = ""
    for ch in chans:
        chan_html += (
            '<div class="ch-card">'
            f'<div class="ch-name">{_esc(ch.get("名称"))}</div>'
            f'<div class="ch-pct">{_n(ch.get("占比_pct"), 1, "%")}</div>'
            f'<div class="ch-amt">{_n(ch.get("收入_亿元"), 1, " 亿")}</div>'
            f'<div class="ch-yoy">同比 {_yoy(ch.get("收入同比_pct"))}</div>'
            f'<div class="ch-margin">毛利率 {_n(ch.get("毛利率_pct"), 1, "%")}</div>'
            "</div>"
        )

    reg_html = ""
    for r in regions:
        reg_html += (
            '<div class="reg-row">'
            f'<span class="reg-k">{_esc(r.get("名称"))}</span>'
            f'<span class="reg-w"><span class="reg-bar" style="width:{max(min(float(r.get("占比_pct") or 0),100),2)}%"></span></span>'
            f'<span class="reg-v">{_n(r.get("收入_亿元"), 1, " 亿")} · {_n(r.get("占比_pct"), 1, "%")}</span>'
            f'<span class="reg-y">{_yoy(r.get("收入同比_pct"))}</span>'
            "</div>"
        )

    d_html = ""
    if dealers:
        dn, dw = dealers.get("国内") or {}, dealers.get("国外") or {}
        d_html = (
            '<div class="deal">'
            f'<div class="deal-item"><span class="deal-n">{_n(dn.get("期末_家"), 0, " 家")}</span>'
            f'<span class="deal-l">国内经销商</span>'
            f'<span class="deal-d">增 {_n(dn.get("增加_家"), 0)} / 减 {_n(dn.get("减少_家"), 0)}</span></div>'
            f'<div class="deal-item"><span class="deal-n">{_n(dw.get("期末_家"), 0, " 家")}</span>'
            f'<span class="deal-l">国外经销商</span>'
            f'<span class="deal-d">增 {_n(dw.get("增加_家"), 0)} / 减 {_n(dw.get("减少_家"), 0)}</span></div>'
            "</div>"
        )

    body = (
        '<div class="sec-t">两条腿走路 <span class="sec-s">按渠道</span></div>'
        f'<div class="ch-grid">{chan_html}</div>'
        + (f'<div class="imt"><span class="imt-lbl">i茅台（自营电商）</span>'
           f'<span class="imt-v">{_n(imt, 1, " 亿元")}</span></div>' if imt else "")
        + '<div class="sec-t">卖到哪里 <span class="sec-s">按地区</span></div>'
        + f'<div class="regs">{reg_html}</div>'
        + (f'<div class="sec-t">经销商网络 <span class="sec-s">期末</span></div>{d_html}' if d_html else "")
    )
    take = ("渠道结构正在换挡：直销（含 i 茅台）收入同比 +29.9%、批发代理 −21.6%。"
            "直销毛利率 94.6% 也高于批发代理的 87.9%，卖同样的酒，直营更赚。")
    body += _note(take)
    foot = ('收入与同比 = 2026 半年报「销售情况」表；毛利率口径为 '
            + _esc(os_.get("毛利率口径") or "上一份年报"))
    return _slide(3, "酒是怎么卖出去的", "渠道与商业模式", body, foot)


# --------------------------------------------------------------------------- #
# 图 4 · 盈利能力
# --------------------------------------------------------------------------- #
def slide_profit(data) -> str:
    nd = data.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    graham = data.get("graham") or {}
    pie = data.get("pie_data") or {}
    fraud = data.get("fraud") or {}

    cards = [
        ("ROE（净资产收益率）", _n(latest.get("roe"), 2, "%"), "每一元净资产赚回多少"),
        ("毛利率", _n(latest.get("gross_margin"), 1, "%"), "卖酒的差价空间"),
        ("净利率", _n(latest.get("net_margin"), 1, "%"), "每一元收入最后剩下多少"),
        ("资产负债率", _n(latest.get("debt_ratio"), 1, "%"), "几乎没有靠借钱经营"),
    ]
    card_html = "".join(
        f'<div class="mcard"><div class="mcard-l">{k}</div>'
        f'<div class="mcard-n">{v}</div><div class="mcard-d">{d}</div></div>'
        for k, v, d in cards
    )

    # 成本构成
    cost_groups = [g for g in (pie.get("groups") or []) if g.get("title") == "营业总成本"]
    cost_html = ""
    if cost_groups:
        g = cost_groups[0]
        items = g.get("items") or []
        total = g.get("total")
        cost_html = (
            f'<div class="sec-t">钱花在哪 <span class="sec-s">营业总成本 {_n(total, 1, " 亿")}</span></div>'
            '<div class="costs">'
        )
        for it in items:
            pct = it.get("pct") or 0
            cost_html += (
                '<div class="cost-row">'
                f'<span class="cost-k">{_esc(it.get("name"))}</span>'
                f'<span class="cost-w"><span class="cost-bar" style="width:{max(min(float(pct),100),1)}%"></span></span>'
                f'<span class="cost-v">{_n(it.get("value"), 1, " 亿")} · {_n(pct, 1, "%")}</span>'
                "</div>"
            )
        cost_html += "</div>"
        ded = g.get("deductions") or []
        if ded:
            cost_html += (
                '<div class="ded">财务费用为负 '
                f'{_n(ded[0].get("value"), 2, " 亿")} —— 存款利息收入大于利息支出，'
                "账上钱多到「越放越赚」</div>"
            )

    mc = (fraud.get("mscore") or {})
    audit = fraud.get("audit_opinion") or "—"
    flags = fraud.get("flags") or []

    qual = (
        '<div class="qual">'
        f'<div class="qual-item"><span class="q-l">审计意见</span><span class="q-v ok">{_esc(audit)}</span></div>'
        f'<div class="qual-item"><span class="q-l">财务粉饰 M-Score</span>'
        f'<span class="q-v ok">{_n(mc.get("mscore"), 2)}（识别阈值 {_n(mc.get("threshold"), 2)}，'
        "低于阈值即无粉饰迹象）</span></div>"
        f'<div class="qual-item"><span class="q-l">流动比率</span>'
        f'<span class="q-v">{_n(graham.get("current_ratio"), 2)}'
        "（流动资产 ÷ 流动负债）</span></div>"
        f'<div class="qual-item"><span class="q-l">财务异常红旗项</span>'
        f'<span class="q-v ok">{len(flags)} 项</span></div>'
        "</div>"
    )

    body = (
        f'<div class="mgrid">{card_html}</div>'
        + cost_html
        + f'<div class="sec-t">财务质量体检 <span class="sec-s">2025 年报</span></div>'
        + qual
        + _note("高毛利、高净利、低负债是这家公司最显著的特征："
                "赚得多、几乎不借钱、账上现金还能生息。")
    )
    foot = "指标口径：2025 年报（归母）"
    return _slide(4, "它有多能赚", "盈利能力与财务质量", body, foot)


# --------------------------------------------------------------------------- #
# 图 5 · 成长轨迹
# --------------------------------------------------------------------------- #
def slide_growth(data) -> str:
    nd = data.get("narrative_data") or {}
    recent = nd.get("recent") or []
    rates = (data.get("annual_rates") or {})
    qr = data.get("quarter_review_facts") or {}
    q = qr.get("单季") or {}
    ytd = qr.get("年初至今累计") or {}

    max_v = 0.0
    for r in recent:
        max_v = max(max_v, float(r.get("revenue") or 0), float(r.get("profit") or 0))

    bars = ""
    for r in recent:
        rev = float(r.get("revenue") or 0)
        prof = float(r.get("profit") or 0)
        bars += (
            '<div class="bg">'
            '<div class="bcols">'
            f'<div class="bcol rev" style="height:{round(rev/max_v*100,1) if max_v else 0}%">'
            f'<span class="bval">{_n(rev, 0)}</span></div>'
            f'<div class="bcol prof" style="height:{round(prof/max_v*100,1) if max_v else 0}%">'
            f'<span class="bval">{_n(prof, 0)}</span></div>'
            "</div>"
            f'<div class="byear">{_esc(r.get("year"))}</div>'
            "</div>"
        )

    sales5 = (rates.get("sales") or {}).get("cagr5")
    earn5 = (rates.get("earnings") or {}).get("cagr5")
    div5 = (rates.get("dividends") or {}).get("cagr5")
    cagr_html = (
        '<div class="mini-grid">'
        f'<div class="mini"><div class="mini-n">{_n(sales5*100 if sales5 else None, 1, "%")}</div>'
        '<div class="mini-l">营收 5 年</div></div>'
        f'<div class="mini"><div class="mini-n">{_n(earn5*100 if earn5 else None, 1, "%")}</div>'
        '<div class="mini-l">净利 5 年</div></div>'
        f'<div class="mini"><div class="mini-n">{_n(div5*100 if div5 else None, 1, "%")}</div>'
        '<div class="mini-l">分红 5 年</div></div>'
        "</div>"
    )

    q_html = (
        '<div class="sec-t">最近一期怎么样 <span class="sec-s">' + _esc(qr.get("报告期") or "—") + "</span></div>"
        '<div class="qgrid">'
        f'<div class="qcard"><div class="q-l">单季营业收入</div><div class="q-n">{_n(q.get("单季营业收入_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {_yoy(q.get("单季营收同比_pct"))}</div></div>'
        f'<div class="qcard"><div class="q-l">单季归母净利</div><div class="q-n">{_n(q.get("单季归母净利润_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {_yoy(q.get("单季归母净利同比_pct"))}</div></div>'
        f'<div class="qcard"><div class="q-l">上半年累计营收</div><div class="q-n">{_n(ytd.get("累计营业总收入_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {_yoy(ytd.get("累计营收同比_pct"))}</div></div>'
        f'<div class="qcard"><div class="q-l">上半年累计净利</div><div class="q-n">{_n(ytd.get("累计归母净利润_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {_yoy(ytd.get("累计归母净利同比_pct"))}</div></div>'
        "</div>"
    )

    body = (
        '<div class="sec-t">近 5 年营收 / 净利 <span class="sec-s">亿元</span></div>'
        '<div class="chart"><div class="legend">'
        f'<span class="lg" style="background:{C["accent2"]}"></span>营业总收入'
        f'<span class="lg" style="background:{C["up"]};margin-left:14px;"></span>归母净利润'
        f'</div><div class="bars">{bars}</div></div>'
        f'<div class="sec-t">年化增速 <span class="sec-s">近 5 年复合</span></div>'
        f'{cagr_html}'
        f'{q_html}'
        + _note("看这家公司要分清两件事：过去五年营收年化 +11.9% 是一条漂亮的长期曲线；"
                "但最近一期单季营收同比 −5.2%、上半年累计仅 +1.3%，"
                "长期成长与短期停滞同时存在。")
    )
    foot = "年报口径（归母）· 单季为 2026Q2，累计为 2026 上半年"
    return _slide(5, "长得有多快", "成长轨迹", body, foot)


# --------------------------------------------------------------------------- #
# 图 6 · 估值水平
# --------------------------------------------------------------------------- #
def slide_valuation(data) -> str:
    val = data.get("valuation") or {}
    pe, pb, dy = val.get("pe"), val.get("pb"), val.get("dividend_yield")
    pe_p, pb_p, dy_p = val.get("pe_pctile"), val.get("pb_pctile"), val.get("dividend_pctile")
    pe_med = val.get("pe_median")

    cards = [
        ("市盈率 PE", _n(pe, 1), pe_p, "股价 ÷ 每股收益"),
        ("市净率 PB", _n(pb, 2), pb_p, "股价 ÷ 每股净资产"),
        ("股息率", _n(dy, 2, "%"), dy_p, "每股分红 ÷ 股价"),
    ]
    card_html = ""
    for label, v, p, d in cards:
        zone = _pctile_zone(p)
        card_html += (
            '<div class="vcard">'
            f'<div class="vcard-l">{label}</div>'
            f'<div class="vcard-n">{v}</div>'
            f'<div class="vcard-d">{d}</div>'
            f'<div class="vcard-p">{_pctile_text(p)}</div>'
            + (f'<div class="vcard-z">{zone}</div>' if zone else "")
            + "</div>"
        )

    # PE 现值 vs 十年中位数：把两个数放在同一条轴上看位置关系。
    # 上一版把「现在」按 现值/中位数 的比例摆在轴上、把中位数顶到最右端，
    # 读者无法判断两者到底差多少 —— 轴必须对应真实的 PE 数值。
    cmp_html = ""
    if pe and pe_med:
        try:
            pe_f, med_f = float(pe), float(pe_med)
            top = max(pe_f, med_f) * 1.16
            pos_now = max(0.0, min(pe_f / top * 100, 100.0))
            pos_med = max(0.0, min(med_f / top * 100, 100.0))
            cmp_html = (
                '<div class="sec-t">现在的 PE 在十年区间里的位置</div>'
                '<div class="pc">'
                '<div class="pc-track">'
                f'<span class="pc-fill" style="width:{pos_now:.1f}%"></span>'
                f'<span class="pc-now" style="left:{pos_now:.1f}%"></span>'
                f'<span class="pc-med" style="left:{pos_med:.1f}%"></span>'
                "</div>"
                '<div class="pc-lbls">'
                f'<span class="pc-l now" style="left:{pos_now:.1f}%">现在 {_n(pe,1)}</span>'
                f'<span class="pc-l med" style="left:{pos_med:.1f}%">十年中位数 {_n(pe_med,1)}</span>'
                "</div></div>"
            )
        except (TypeError, ValueError, ZeroDivisionError):
            cmp_html = ""

    high = val.get("price_high")
    low = val.get("price_low")
    high_raw = val.get("price_high_raw")
    now = val.get("price_now")
    range_html = ""
    if high and low and now:
        try:
            pos = (float(now) - float(low)) / (float(high) - float(low)) * 100
            pos = max(0.0, min(pos, 100.0))
        except (TypeError, ValueError, ZeroDivisionError):
            pos = 0
        range_html = (
            '<div class="sec-t">现价在近一年区间的位置 <span class="sec-s">前复权</span></div>'
            '<div class="rng">'
            f'<div class="rng-track"><div class="rng-now" style="left:{round(pos,1)}%"></div></div>'
            f'<div class="rng-marks"><span>最低 {_n(low,2)}</span><span>现价 {_n(now,2)}</span>'
            f'<span>最高 {_n(high,2)}</span></div>'
            "</div>"
        )

    body = (
        f'<div class="vgrid">{card_html}</div>'
        + cmp_html
        + range_html
        + _note("分位数说的是「这个数字在它自己近十年的历史里排第几」，不构成好坏判断。"
                "另外股息率的分位方向与 PE/PB 相反：股息率 = 每股分红 ÷ 股价，"
                "分位越高说明按现价能拿到的股息回报越高。")
    )
    foot = (f'行情日 {_esc(val.get("quote_date") or "—")} · 分位基于近 10 年历史序列'
            + (f'（{_esc(val.get("val_series_start"))} 起）' if val.get("val_series_start") else "")
            + (f' · 未复权最高价 {_n(high_raw,2)}' if high_raw else ""))
    return _slide(6, "现在贵不贵", "估值与历史分位", body, foot)


# --------------------------------------------------------------------------- #
# 图 7 · 股东回报
# --------------------------------------------------------------------------- #
def slide_dividend(data) -> str:
    val = data.get("valuation") or {}
    hist = [h for h in (val.get("dividend_history") or []) if h.get("dps")]
    if not hist:
        return _slide(7, "分红给了多少", "股东回报", '<div class="lead">暂无分红序列数据。</div>')

    W, H = 342, 190
    PL, PR, PT, PB = 34, 12, 16, 28
    pw, ph = W - PL - PR, H - PT - PB
    n = len(hist)

    dps_max = max(h["dps"] for h in hist) or 1
    pcts = [h["payout_pct"] for h in hist if h.get("payout_pct")]
    p_lo, p_hi = (min(pcts), max(pcts)) if pcts else (0, 100)
    p_lo = max(0.0, ((int(p_lo) - 5) // 5) * 5)
    p_hi = ((int(p_hi) + 5) // 5) * 5
    if p_hi <= p_lo:
        p_hi = p_lo + 10

    def x(i):
        return PL + (i + 0.5) / n * pw

    def y_d(v):
        return PT + ph - (v / dps_max) * ph * 0.92

    def y_p(v):
        return PT + ph - ((v - p_lo) / (p_hi - p_lo)) * ph

    bw = min(pw / n * 0.56, 22)
    bars, labels = [], []
    for i, h in enumerate(hist):
        yy = y_d(h["dps"])
        bars.append(
            f'<rect x="{x(i)-bw/2:.1f}" y="{yy:.1f}" width="{bw:.1f}" '
            f'height="{PT+ph-yy:.1f}" rx="2" fill="{C["accent2"]}" '
            f'fill-opacity="{0.95 if i==n-1 else 0.6}"/>'
        )
        if i % 2 == 0 or i == n - 1:
            labels.append(
                f'<text x="{x(i):.1f}" y="{PT+ph+14:.1f}" text-anchor="middle" '
                f'font-size="8.5" fill="{C["faint"]}">{h["year"]}</text>'
            )

    pts = [(i, h["payout_pct"]) for i, h in enumerate(hist) if h.get("payout_pct")]
    poly = " ".join(f"{x(i):.1f},{y_p(v):.1f}" for i, v in pts)
    line = (f'<polyline points="{poly}" fill="none" stroke="{C["up"]}" '
            f'stroke-width="1.8" stroke-linejoin="round"/>') if poly else ""
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y_p(v):.1f}" r="2.3" fill="{C["up"]}"/>' for i, v in pts
    )

    # 比例轴刻度（右）——只标两端，折线看的是「台阶」而不是逐点读数
    ticks = []
    for v in (p_lo, p_hi):
        yy = y_p(v)
        ticks.append(
            f'<text x="{W-PR+2:.1f}" y="{yy+3:.1f}" font-size="8" fill="{C["up"]}" '
            f'text-anchor="start">{v:.0f}%</text>'
        )

    # 每股股息轴（左）
    ticks += []
    for frac in (0.0, 1.0):
        v = dps_max * frac
        yy = y_d(v)
        ticks.append(
            f'<text x="{PL-4:.1f}" y="{yy+3:.1f}" font-size="8" fill="{C["faint"]}" '
            f'text-anchor="end">{v:.0f}</text>'
        )

    last = hist[-1]
    first = hist[0]
    svg = (
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="每股股息与分红比例历史">'
        + "".join(bars) + line + dots + "".join(ticks) + "".join(labels) + "</svg>"
    )

    body = (
        '<div class="sec-t">每股分红（柱）与分红比例（线） <span class="sec-s">元 / %</span></div>'
        f'<div class="divwrap">{svg}</div>'
        '<div class="legend2">'
        f'<span class="lg" style="background:{C["accent2"]}"></span>每股股息（左轴，元）'
        f'<span class="lg" style="background:{C["up"]};margin-left:12px;"></span>分红比例（右轴，% 占归母净利）'
        "</div>"
        '<div class="mini-grid">'
        f'<div class="mini"><div class="mini-n">{_n(last.get("dps"), 2, " 元")}</div>'
        f'<div class="mini-l">{last.get("year")} 每股分红</div></div>'
        f'<div class="mini"><div class="mini-n">{_n(last.get("total"), 0, " 亿")}</div>'
        '<div class="mini-l">分红总额</div></div>'
        f'<div class="mini"><div class="mini-n">{_n(last.get("payout_pct"), 1, "%")}</div>'
        '<div class="mini-l">分红比例</div></div>'
        "</div>"
        + _note(f'分红比例长期停在 {_n(first.get("payout_pct"),1,"%")} 附近，'
                f'{last.get("year")} 年抬到了 {_n(last.get("payout_pct"),1,"%")} —— '
                "这不是随机波动，而是公司把「赚到的钱分给股东」的比例系统性地提高了。")
    )
    foot = f'按分红实施年度统计 · 分红比例 = 现金分红 ÷ 当年归母净利 · 股息率 {_n(val.get("dividend_yield"),2,"%")}'
    return _slide(7, "分红给了多少", "股东回报", body, foot)


# --------------------------------------------------------------------------- #
# 图 8 · 现金流
# --------------------------------------------------------------------------- #
def slide_cashflow(data) -> str:
    qr = data.get("quarter_review_facts") or {}
    ocf = (data.get("operating_structure") or {}).get("现金流归因") or qr.get("现金流归因") or {}

    if not ocf:
        return _slide(8, "现金流怎么看", "现金流质量", '<div class="lead">暂无现金流归因数据。</div>')

    period = ocf.get("期间") or "本期"
    prior = ocf.get("上期") or "上期"
    net_cur = (ocf.get("经营活动产生的现金流量净额_亿元") or {}).get("本期")
    net_prev = (ocf.get("经营活动产生的现金流量净额_亿元") or {}).get("上期")
    inn = (ocf.get("经营活动现金流入小计_亿元") or {})
    out = (ocf.get("经营活动现金流出小计_亿元") or {})
    yoy = ocf.get("净额同比_pct")
    adj = ocf.get("剔除财务公司科目后") or {}
    items = ocf.get("主要变动科目") or []

    head = (
        '<div class="cf-head">'
        f'<div class="cf-h-l">{_esc(period)} 经营现金流净额</div>'
        f'<div class="cf-h-n">{_n(net_cur, 1, " 亿")}</div>'
        f'<div class="cf-h-y">同比 {_yoy(yoy)}</div>'
        "</div>"
    )

    flow = (
        '<div class="flow">'
        '<div class="flow-row"><span class="flow-k">流入</span>'
        f'<span class="flow-cur">{_n(inn.get("本期"), 1)}</span>'
        f'<span class="flow-arrow">vs</span>'
        f'<span class="flow-prev">{_n(inn.get("上期"), 1)}</span></div>'
        '<div class="flow-row"><span class="flow-k">流出</span>'
        f'<span class="flow-cur">{_n(out.get("本期"), 1)}</span>'
        f'<span class="flow-arrow">vs</span>'
        f'<span class="flow-prev">{_n(out.get("上期"), 1)}</span></div>'
        f'<div class="flow-hint">单位：亿元。右边为{_esc(prior)}。'
        "流入只增一成、流出掉一半 —— 这不是「卖酒变好了」能解释的</div>"
        "</div>"
    )

    rows = ""
    for it in items[:4]:
        is_fin = it.get("是否财务公司科目")
        tag = ('<span class="tag fin">财务公司</span>' if is_fin
               else '<span class="tag biz">主业</span>')
        rows += (
            '<div class="cft-row">'
            f'<span class="cft-k">{_esc(it.get("科目"))}</span>{tag}'
            f'<span class="cft-v">{_yoy(it.get("变动_亿元"), 1, "—")} 亿</span>'
            "</div>"
        )

    adj_html = ""
    if adj:
        adj_html = (
            '<div class="adj">'
            '<div class="adj-t">剔除财务公司科目后</div>'
            '<div class="adj-v">'
            f'<span class="adj-n">{_n(adj.get("经营性现金净额_亿元"), 1, " 亿")}</span>'
            f'<span class="adj-p">vs {_n(adj.get("上期_亿元"), 1, " 亿")}，同比 {_yoy(adj.get("同比_pct"))}</span>'
            "</div>"
            f'<div class="adj-note">{_esc(adj.get("说明"))}</div>'
            "</div>"
        )

    body = (
        head + flow
        + '<div class="sec-t">主要是哪几个科目在动 <span class="sec-s">变动额</span></div>'
        + f'<div class="cft">{rows}</div>'
        + adj_html
        + _note("表观 +438.8% 的现金流「大幅改善」并非主业回款爆发："
                "剔除财务公司（吸收存款、缴存央行、同业拆放）科目后，"
                "主业经营性现金净额同比只有 +1.9% —— 回款基本持平。")
    )
    foot = "经营活动现金流＝流入 − 流出；财务公司科目属资产负债表资金搬动，反映不了卖酒景气度"
    return _slide(8, "现金流怎么看", "一条容易被误读的数据", body, foot)


# --------------------------------------------------------------------------- #
# 图 9 · 风险与结论
# --------------------------------------------------------------------------- #
def slide_risk(data, narrative) -> str:
    risks = (narrative or {}).get("risks") or []
    comp = data.get("competition") or {}
    nd = data.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    val = data.get("valuation") or {}
    graham = data.get("graham") or {}

    risk_html = ""
    for i, r in enumerate(risks[:4], 1):
        risk_html += (
            '<div class="risk"><span class="risk-i">{}</span><span class="risk-t">{}</span></div>'
            .format(i, _esc(r))
        )
    if not risk_html:
        risk_html = '<div class="risk"><span class="risk-t">数据源未覆盖风险字段。</span></div>'

    # 关键数据速览：全部由本地事实拼装成纯数字陈述。
    # 不用 LLM 的 thesis —— 它写过「估值处于历史低位，下行风险有限」这类判断，
    # 本产品遵循完全去操作原则，结论性表述不进图文。
    facts = []
    if latest.get("roe") is not None:
        facts.append(
            f'盈利与杠杆：ROE {_n(latest.get("roe"), 2, "%")}，'
            f'资产负债率 {_n(graham.get("debt_ratio"), 1, "%")}，'
            f'流动比率 {_n(graham.get("current_ratio"), 2)}'
        )
    if val.get("pe") is not None:
        facts.append(
            f'估值位置：PE {_n(val.get("pe"), 1)}（近 10 年 {_n(val.get("pe_pctile"), 1)}% 分位），'
            f'PB {_n(val.get("pb"), 2)}（{_n(val.get("pb_pctile"), 1)}% 分位）'
        )
    if val.get("dividend_yield") is not None:
        facts.append(
            f'股东回报：股息率 {_n(val.get("dividend_yield"), 2, "%")}，'
            f'分红比例 {_n(val.get("dividend_payout_pct"), 1, "%")}'
            f'（近 10 年 {_n(val.get("dividend_pctile"), 1)}% 分位）'
        )
    if latest.get("revenue") is not None:
        facts.append(
            f'{nd.get("latest_year")} 年报：营收 {_n(latest.get("revenue"), 1, " 亿")}，'
            f'归母净利 {_n(latest.get("net_profit"), 1, " 亿")}'
        )
    thesis_html = ""
    if facts:
        thesis_html = '<div class="sec-t">关键数据速览 <span class="sec-s">纯数字陈述</span></div>'
        for f_ in facts:
            thesis_html += f'<div class="fact"><span class="fact-d"></span>{_esc(f_)}</div>'

    share = comp.get("share_pct")
    ind_rev = comp.get("industry_revenue")
    body = (
        thesis_html
        + '<div class="sec-t">读这家公司要盯住什么</div>'
        + f'<div class="risks">{risk_html}</div>'
        + (f'<div class="comp-note">行业位置：{_esc(comp.get("industry"))} '
           f'{comp.get("rank")}/{comp.get("peers_count")}，'
           f'2025 年营收 {_n(comp.get("revenue_yi"), 1, " 亿")}，'
           f'行业总额 {_n(ind_rev, 1, " 亿")}，份额 {_n(share, 1, "%")}</div>'
           if share and ind_rev else "")
        + '<div class="disclaimer">本图仅呈现客观数据与事实描述，不含任何价格点位、'
          '仓位或买卖建议，不构成投资建议。数据来源：东方财富、巨潮资讯定期报告。'
          '市场有风险，阅读者应独立判断。</div>'
    )
    foot = "全部数字可在 ValueLine 一页研报中逐项核对"
    return _slide(9, "该盯住什么", "风险与结论", body, foot)


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def build_html(data: dict, narrative: dict | None) -> str:
    slides = [
        slide_cover(data, narrative),
        slide_business(data, narrative),
        slide_channel(data),
        slide_profit(data),
        slide_growth(data),
        slide_valuation(data),
        slide_dividend(data),
        slide_cashflow(data),
        slide_risk(data, narrative),
    ]
    return _PAGE.format(css=_CSS, slides="\n".join(slides))


_CSS = f"""
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#dfe4ea; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         -webkit-font-smoothing:antialiased; }}
  .slide {{ width:390px; background:{C['bg']}; color:{C['ink']}; padding:22px 22px 18px;
            position:relative; overflow:hidden; }}
  .slide + .slide {{ margin-top:16px; }}

  /* 头部 */
  .hd {{ display:flex; justify-content:space-between; align-items:center;
         padding-bottom:12px; border-bottom:2px solid {C['accent']}; }}
  .brand {{ font-size:12px; font-weight:700; color:{C['accent']}; letter-spacing:.8px; }}
  .pager {{ font-size:11px; color:{C['faint']}; font-weight:600; }}
  .ttl {{ font-size:21px; font-weight:800; color:{C['accent']}; margin:16px 0 2px;
          line-height:1.25; }}
  .ttl .sub {{ display:block; font-size:12px; font-weight:400; color:{C['faint']};
               margin-top:5px; letter-spacing:.3px; }}
  .body {{ margin-top:14px; }}

  /* 封面 */
  .co-name {{ font-size:32px; font-weight:800; color:{C['accent']}; letter-spacing:1px;
              line-height:1.12; }}
  .co-meta {{ margin-top:9px; font-size:13px; color:{C['faint']}; }}
  .co-meta .code {{ font-weight:700; color:{C['ink']}; font-size:14px; }}
  .co-meta .sep {{ margin:0 7px; color:{C['line']}; }}
  .price {{ margin-top:20px; }}
  .price-num {{ font-size:42px; font-weight:800; color:{C['up']}; line-height:1;
                letter-spacing:-.5px; }}
  .price-lbl {{ font-size:11px; color:{C['faint']}; margin-top:7px; }}
  .stat-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:9px; margin-top:20px; }}
  .stat {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
           padding:13px 12px; }}
  .stat-n {{ font-size:19px; font-weight:800; color:{C['ink']}; }}
  .stat-l {{ font-size:11px; color:{C['faint']}; margin-top:4px; }}
  .badges {{ margin-top:16px; display:flex; flex-direction:column; gap:8px; }}
  .badge {{ background:{C['soft']}; border:1px solid #c9d8ec; border-radius:8px;
            padding:10px 12px; display:flex; gap:9px; align-items:baseline; }}
  .bd-k {{ font-size:11px; color:{C['accent2']}; font-weight:700; flex:0 0 auto; }}
  .bd-v {{ font-size:12.5px; color:{C['ink']}; font-weight:600; }}
  .cover-tip {{ margin-top:18px; font-size:11.5px; color:{C['faint']}; text-align:center;
                padding:11px 0; border-top:1px dashed {C['line']}; }}

  /* 通用块 */
  .sec-t {{ font-size:14.5px; font-weight:700; color:{C['ink']}; margin:18px 0 10px;
            display:flex; align-items:baseline; gap:7px; }}
  .sec-t:first-child {{ margin-top:0; }}
  .sec-s {{ font-size:10.5px; font-weight:400; color:{C['faint']}; }}
  .lead {{ font-size:13.5px; line-height:1.75; color:{C['ink']}; background:{C['card']};
           border:1px solid {C['line']}; border-left:3px solid {C['accent']};
           border-radius:6px; padding:13px 14px; }}

  /* 键值 / 迷你卡 */
  .mini-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; }}
  .mini {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:9px;
           padding:11px 9px; text-align:center; }}
  .mini-n {{ font-size:15px; font-weight:800; color:{C['accent']}; }}
  .mini-l {{ font-size:10px; color:{C['faint']}; margin-top:4px; }}
  .mini-y {{ font-size:10.5px; margin-top:3px; font-weight:600; }}

  /* 产销量（茅台酒 / 系列酒 拆分） */
  .plist {{ display:flex; flex-direction:column; gap:8px; }}
  .pline {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:9px;
            padding:10px 12px; }}
  .pline-h {{ display:flex; justify-content:space-between; align-items:baseline;
              margin-bottom:7px; }}
  .pline-n {{ font-size:13px; font-weight:700; color:{C['ink']}; }}
  .pline-r {{ font-size:9.5px; color:{C['faint']}; }}
  .pline-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:6px; }}
  .pline-cell {{ display:flex; justify-content:space-between; align-items:baseline;
                 background:{C['soft']}; border-radius:6px; padding:5px 8px; }}
  .pline-k {{ font-size:9.5px; color:{C['muted']}; }}
  .pline-v {{ font-size:11.5px; font-weight:700; color:{C['accent']}; white-space:nowrap; }}
  .pline-y {{ font-size:9.5px; margin-left:5px; }}
  .pnote {{ margin-top:7px; font-size:9.5px; color:{C['muted']}; line-height:1.65; }}

  /* 产品切片 */
  .segs {{ display:flex; flex-direction:column; gap:10px; }}
  .seg {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
          padding:12px 13px; }}
  .seg-top {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .seg-name {{ font-size:14.5px; font-weight:700; color:{C['ink']}; }}
  .seg-pct {{ font-size:17px; font-weight:800; color:{C['accent']}; }}
  .seg-bar {{ height:6px; background:{C['line']}; border-radius:3px; margin:9px 0 8px;
              overflow:hidden; }}
  .seg-fill {{ height:100%; background:linear-gradient(90deg,{C['accent']},{C['accent3']});
               border-radius:3px; }}
  .seg-meta {{ display:flex; justify-content:space-between; font-size:11px;
               color:{C['muted']}; }}

  /* 渠道 */
  .ch-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .ch-card {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
              padding:13px 12px; }}
  .ch-name {{ font-size:13px; font-weight:700; color:{C['ink']}; }}
  .ch-pct {{ font-size:24px; font-weight:800; color:{C['accent']}; margin:6px 0 3px; }}
  .ch-amt {{ font-size:11.5px; color:{C['muted']}; }}
  .ch-yoy {{ font-size:11.5px; margin-top:6px; }}
  .ch-margin {{ font-size:10.5px; color:{C['faint']}; margin-top:4px; }}
  .imt {{ margin-top:10px; background:{C['card']}; border:1px dashed #c9d8ec; border-radius:9px;
          padding:11px 13px; display:flex; justify-content:space-between; align-items:center; }}
  .imt-lbl {{ font-size:12px; color:{C['muted']}; }}
  .imt-v {{ font-size:17px; font-weight:800; color:{C['accent2']}; }}

  .regs {{ display:flex; flex-direction:column; gap:9px; }}
  .reg-row {{ display:grid; grid-template-columns:46px 1fr auto auto; gap:8px;
              align-items:center; font-size:11.5px; }}
  .reg-k {{ color:{C['muted']}; font-weight:600; }}
  .reg-w {{ height:8px; background:{C['line']}; border-radius:4px; overflow:hidden; }}
  .reg-bar {{ display:block; height:100%; background:{C['accent3']}; border-radius:4px; }}
  .reg-v {{ color:{C['ink']}; font-weight:600; white-space:nowrap; }}
  .reg-y {{ white-space:nowrap; font-weight:600; }}

  .deal {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .deal-item {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:9px;
                padding:12px; text-align:center; }}
  .deal-n {{ display:block; font-size:19px; font-weight:800; color:{C['accent']}; }}
  .deal-l {{ display:block; font-size:10.5px; color:{C['faint']}; margin-top:4px; }}
  .deal-d {{ display:block; font-size:10.5px; color:{C['muted']}; margin-top:3px; }}

  /* 指标卡 */
  .mgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:9px; }}
  .mcard {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
            padding:14px 13px; }}
  .mcard-l {{ font-size:11.5px; color:{C['faint']}; }}
  .mcard-n {{ font-size:26px; font-weight:800; color:{C['accent']}; margin:7px 0 5px; }}
  .mcard-d {{ font-size:10.5px; color:{C['muted']}; line-height:1.5; }}

  /* 成本构成 */
  .costs {{ display:flex; flex-direction:column; gap:8px; }}
  .cost-row {{ display:grid; grid-template-columns:74px 1fr auto; gap:9px; align-items:center;
               font-size:11.5px; }}
  .cost-k {{ color:{C['muted']}; }}
  .cost-w {{ height:9px; background:{C['line']}; border-radius:4px; overflow:hidden; }}
  .cost-bar {{ display:block; height:100%; background:{C['gold']}; opacity:.8; border-radius:4px; }}
  .cost-v {{ color:{C['ink']}; font-weight:600; white-space:nowrap; }}
  .ded {{ margin-top:9px; font-size:11px; color:{C['muted']}; background:{C['card']};
          border:1px dashed {C['line']}; border-radius:7px; padding:9px 11px; line-height:1.6; }}

  /* 质量体检 */
  .qual {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
           padding:12px 13px; }}
  .qual-item {{ display:flex; justify-content:space-between; gap:10px; font-size:11.5px;
                padding:6px 0; }}
  .qual-item + .qual-item {{ border-top:1px dashed {C['line']}; }}
  .q-l {{ color:{C['muted']}; flex:0 0 auto; }}
  .q-v {{ color:{C['ink']}; font-weight:600; text-align:right; }}
  .q-v.ok {{ color:{C['down']}; }}

  /* 柱状图 */
  .chart {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
            padding:13px 12px; }}
  .legend {{ font-size:11px; color:{C['faint']}; margin-bottom:12px; }}
  .legend .lg, .legend2 .lg {{ display:inline-block; width:10px; height:10px; border-radius:2px;
                               margin-right:5px; vertical-align:middle; }}
  .bars {{ display:flex; gap:8px; align-items:flex-end; height:138px; }}
  .bg {{ flex:1; display:flex; flex-direction:column; align-items:center; height:100%; }}
  .bcols {{ display:flex; gap:3px; align-items:flex-end; flex:1; width:100%; justify-content:center; }}
  .bcol {{ width:13px; border-radius:3px 3px 0 0; position:relative; min-height:2px; }}
  .bcol.rev {{ background:{C['accent2']}; }}
  .bcol.prof {{ background:{C['up']}; }}
  .bval {{ position:absolute; top:-13px; left:50%; transform:translateX(-50%);
           font-size:8.5px; color:{C['faint']}; white-space:nowrap; }}
  .byear {{ font-size:10px; color:{C['faint']}; margin-top:6px; }}

  /* 季度 */
  .qgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:9px; }}
  .qcard {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:9px;
            padding:12px; }}
  .q-l {{ font-size:11px; color:{C['faint']}; }}
  .q-n {{ font-size:19px; font-weight:800; color:{C['ink']}; margin:6px 0 4px; }}
  .q-y {{ font-size:11px; }}

  /* 估值卡 */
  .vgrid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; }}
  .vcard {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
            padding:12px 9px; text-align:center; }}
  .vcard-l {{ font-size:10.5px; color:{C['faint']}; }}
  .vcard-n {{ font-size:20px; font-weight:800; color:{C['ink']}; margin:6px 0 4px; }}
  .vcard-d {{ font-size:9.5px; color:{C['faint']}; line-height:1.45; }}
  .vcard-p {{ font-size:9.5px; color:{C['muted']}; margin-top:5px; }}
  .vcard-z {{ font-size:10.5px; font-weight:700; margin-top:3px; color:{C['accent2']}; }}

  /* PE 位置条 */
  .pc {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
         padding:20px 16px 12px; }}
  .pc-track {{ position:relative; height:12px; background:{C['line']}; border-radius:6px; }}
  .pc-fill {{ position:absolute; left:0; top:0; height:100%;
              background:linear-gradient(90deg,#dbe7f5,{C['accent3']}); border-radius:6px; }}
  .pc-now {{ position:absolute; top:-5px; width:3px; height:22px; background:{C['accent']};
             border-radius:2px; transform:translateX(-50%); }}
  .pc-med {{ position:absolute; top:-5px; width:2px; height:22px; background:{C['faint']};
             transform:translateX(-50%); }}
  .pc-lbls {{ position:relative; height:18px; margin-top:7px; }}
  .pc-l {{ position:absolute; transform:translateX(-50%); font-size:10px;
           white-space:nowrap; }}
  .pc-l.now {{ color:{C['accent']}; font-weight:800; }}
  .pc-l.med {{ color:{C['faint']}; }}

  /* 价格区间 */
  .rng {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
          padding:16px 14px 12px; }}
  .rng-track {{ height:9px; border-radius:5px; position:relative;
                background:linear-gradient(90deg,{C['down']},{C['line']},{C['up']}); opacity:.85; }}
  .rng-now {{ position:absolute; top:-4px; width:4px; height:17px; background:{C['ink']};
              border-radius:2px; transform:translateX(-50%); }}
  .rng-marks {{ display:flex; justify-content:space-between; font-size:10px;
                color:{C['faint']}; margin-top:8px; }}

  /* 分红图 */
  .divwrap {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
              padding:12px 10px 6px; }}
  .legend2 {{ font-size:10.5px; color:{C['faint']}; margin:9px 0 0; text-align:center; }}

  /* 现金流 */
  .cf-head {{ background:{C['accent']}; border-radius:10px; padding:15px 16px; color:#fff; }}
  .cf-h-l {{ font-size:11.5px; opacity:.82; }}
  .cf-h-n {{ font-size:30px; font-weight:800; margin:7px 0 5px; letter-spacing:-.5px; }}
  .cf-h-y {{ font-size:11.5px; opacity:.9; }}
  .cf-h-y .upv, .cf-h-y .dnv, .cf-h-y .flat {{ color:#ffd7d1 !important; opacity:1; }}
  .flow {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
           padding:12px 13px; margin-top:10px; }}
  .flow-row {{ display:grid; grid-template-columns:34px 1fr 22px 1fr; gap:5px;
               align-items:baseline; font-size:12.5px; padding:5px 0; }}
  .flow-k {{ color:{C['faint']}; font-size:11px; }}
  .flow-cur {{ font-weight:700; color:{C['ink']}; }}
  .flow-arrow {{ color:{C['faint']}; text-align:center; font-size:10px; }}
  .flow-prev {{ color:{C['faint']}; }}
  .flow-hint {{ font-size:10.5px; color:{C['muted']}; margin-top:8px; padding-top:8px;
                border-top:1px dashed {C['line']}; line-height:1.6; }}
  .cft {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
          padding:6px 13px; }}
  .cft-row {{ display:flex; align-items:center; gap:7px; font-size:11px; padding:8px 0; }}
  .cft-row + .cft-row {{ border-top:1px dashed {C['line']}; }}
  .cft-k {{ flex:1; color:{C['ink']}; line-height:1.4; }}
  .cft-v {{ font-weight:700; white-space:nowrap; }}
  .tag {{ font-size:9px; padding:2px 6px; border-radius:3px; white-space:nowrap;
          font-weight:700; }}
  .tag.fin {{ background:#fdf0ee; color:{C['up']}; }}
  .tag.biz {{ background:#eef3fb; color:{C['accent2']}; }}
  .adj {{ margin-top:10px; background:#eef7f1; border:1px solid #c4e3d2; border-radius:10px;
          padding:13px 14px; }}
  .adj-t {{ font-size:11.5px; font-weight:700; color:{C['down']}; }}
  .adj-v {{ margin:8px 0 6px; display:flex; align-items:baseline; gap:8px; }}
  .adj-n {{ font-size:22px; font-weight:800; color:{C['down']}; }}
  .adj-p {{ font-size:11px; color:{C['muted']}; }}
  .adj-note {{ font-size:10.5px; color:{C['muted']}; line-height:1.6; }}

  /* 风险 */
  .risks {{ display:flex; flex-direction:column; gap:9px; }}
  .risk {{ display:flex; gap:10px; background:{C['card']}; border:1px solid {C['line']};
           border-radius:9px; padding:12px 13px; }}
  .risk-i {{ flex:0 0 19px; height:19px; border-radius:50%; background:#fdf0ee;
             color:{C['up']}; font-size:11px; font-weight:800; text-align:center;
             line-height:19px; }}
  .risk-t {{ font-size:12px; line-height:1.7; color:{C['ink']}; }}
  .fact {{ display:flex; gap:9px; font-size:12px; line-height:1.7; color:{C['ink']};
           padding:6px 0; }}
  .fact-d {{ flex:0 0 6px; height:6px; border-radius:50%; background:{C['down']};
             margin-top:8px; }}
  .comp-note {{ margin-top:12px; font-size:11px; color:{C['muted']}; line-height:1.7;
                background:{C['card']}; border:1px solid {C['line']}; border-radius:8px;
                padding:11px 12px; }}
  .disclaimer {{ margin-top:16px; font-size:9.5px; color:{C['faint']}; line-height:1.75;
                 border-top:1px solid {C['line']}; padding-top:12px; }}

  /* 一句话 */
  .takeaway {{ margin-top:14px; background:{C['soft']}; border-left:3px solid {C['accent2']};
               border-radius:6px; padding:12px 13px; font-size:12px; line-height:1.75;
               color:{C['ink']}; }}
  .tk-lbl {{ display:inline-block; font-size:10px; font-weight:800; color:#fff;
             background:{C['accent2']}; padding:2px 7px; border-radius:3px; margin-right:7px;
             vertical-align:1px; }}

  .foot {{ margin-top:14px; font-size:9.5px; color:{C['faint']}; line-height:1.65;
           border-top:1px solid {C['line']}; padding-top:10px; }}

  .upv {{ color:{C['up']}; font-weight:700; }}
  .dnv {{ color:{C['down']}; font-weight:700; }}
  .flat {{ color:{C['faint']}; }}
"""

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>{css}</style>
</head>
<body>
{slides}
</body>
</html>"""


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def export_png(html_text: str, out_dir: Path, code: str) -> list[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright，请先执行：pip install playwright && playwright install chromium")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"_xhs_{code}.html"
    tmp.write_text(html_text, encoding="utf-8")
    uri = tmp.resolve().as_uri()

    out_paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 1400}, device_scale_factor=3)
        page.goto(uri)
        page.wait_for_timeout(400)
        slides = page.locator("section.slide")
        count = slides.count()
        for i in range(count):
            name = SLIDE_NAMES[i] if i < len(SLIDE_NAMES) else f"p{i+1}"
            fp = out_dir / f"{code}_{i+1:02d}_{name}.png"
            slides.nth(i).screenshot(path=str(fp))
            out_paths.append(fp)
        browser.close()

    tmp.unlink(missing_ok=True)
    return out_paths


def main() -> None:
    ap = argparse.ArgumentParser(description="ValueLine → 小红书轮播图")
    ap.add_argument("code", nargs="?", default="600519", help="股票代码（默认 600519）")
    ap.add_argument("-o", "--out", default="reports/xhs", help="输出目录（默认 reports/xhs）")
    ap.add_argument("--html-only", action="store_true", help="只写 HTML，不截图")
    args = ap.parse_args()

    data, narrative = _load(args.code)
    data["_code"] = args.code
    html_text = build_html(data, narrative)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.code}.html").write_text(html_text, encoding="utf-8")
    print(f"HTML：{out_dir / (args.code + '.html')}")

    if args.html_only:
        return
    for fp in export_png(html_text, out_dir, args.code):
        print(f"生成：{fp}")


if __name__ == "__main__":
    main()
