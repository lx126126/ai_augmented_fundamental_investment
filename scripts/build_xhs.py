#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ValueLine → 小红书长图生成器（3 张手机端竖图）。

设计原则：
- 零观点：只呈现客观数据（估值/指标/商业模式描述/风险陈述），无目标价/多空/买卖建议。
- 手机逻辑宽 390px，导出 device_scale_factor=3 → 1170px 高清（小红书不压缩）。
- 红涨绿跌（中国习惯）。

用法：
    python scripts/build_xhs.py 600519 [-o reports/xhs]

输出：
    {code}_01_封面.png / {code}_02_指标趋势.png / {code}_03_模式风险.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.adapter import build_template_data  # noqa: E402

# 配色（对齐报告品牌色）
C = {
    "ink": "#1a2330",
    "faint": "#8a97a6",
    "accent": "#0f3d6e",
    "accent2": "#14508c",
    "bg": "#f5f7fa",
    "card": "#ffffff",
    "line": "#e3e8ef",
    "up": "#c0392b",    # 红（涨）
    "down": "#1e8e5a",  # 绿（跌）
    "gold": "#b8860b",
}


def _n(v, digits=1, suffix=""):
    """数值 → 展示字符串（None → —）。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:.{digits}f}{suffix}"


def _pctile_text(p):
    """分位 → 文本。"""
    if p is None:
        return "分位 —"
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "分位 —"
    return f"近10年 {p:.1f}% 分位"


def _pctile_zone(p):
    """分位 → 区间定性（客观标注，非建议）。"""
    if p is None:
        return ""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return ""
    if p < 30:
        return "低位"
    if p > 70:
        return "高位"
    return "中位"


def _load(code: str):
    """加载报告数据 + LLM 叙事。"""
    data = build_template_data(code)
    narrative = None
    try:
        from src.report.llm import generate_narrative
        nd = data.get("narrative_data")
        if nd:
            narrative = generate_narrative(nd)  # 失败返回 None，降级
    except Exception:
        narrative = None
    return data, narrative


def _slide(inner: str, idx: int) -> str:
    """包一张竖图。"""
    return f'<section class="slide">{inner}</section>'


def build_html(data: dict, narrative: dict | None) -> str:
    """生成 3 张竖图的完整 HTML。"""
    code = data.get("code", "")
    name = data.get("company_name", "—")
    period = data.get("report_period", "—")
    val = data.get("valuation") or {}
    comp = data.get("competition") or {}
    nd = data.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    recent = nd.get("recent") or []
    graham = data.get("graham") or {}

    industry = comp.get("industry") or "—"
    rank = comp.get("rank")
    rank_txt = f"行业第 {rank}" if rank else "行业排名 —"

    price_now = val.get("price_now")
    pe = val.get("pe")
    pb = val.get("pb")
    dy = val.get("dividend_yield")
    pe_pct = val.get("pe_pctile")
    pb_pct = val.get("pb_pctile")

    roe = latest.get("roe")
    net_margin = latest.get("net_margin")
    gross_margin = latest.get("gross_margin")
    payout = nd.get("dividend_payout")

    # ---- 图 1：封面 + 估值 ----
    lynch = (narrative or {}).get("lynch_type", "待分析")
    gbadge = (narrative or {}).get("graham_badge", "待分析")

    val_cards = ""
    for label, v, pct, unit in [
        ("PE", pe, pe_pct, ""),
        ("PB", pb, pb_pct, ""),
        ("股息率", dy, None, "%"),
    ]:
        pct_txt = _pctile_text(pct)
        zone = _pctile_zone(pct)
        zone_html = f'<div class="zone">{zone}</div>' if zone else ""
        val_cards += (
            f'<div class="val-card">'
            f'<div class="val-lbl">{label}</div>'
            f'<div class="val-num">{_n(v, 2 if label != "PE" else 1, unit)}</div>'
            f'<div class="val-pct">{pct_txt}</div>'
            f'{zone_html}'
            f'</div>'
        )

    slide1 = f"""
    <div class="brand"><span class="brand-name">ValueLine 一页研报</span><span class="brand-period">{period} 更新</span></div>
    <div class="co">
      <div class="co-name">{name}</div>
      <div class="co-meta"><span class="code">{code}</span><span class="sep">·</span><span>{industry}</span><span class="sep">·</span><span>{rank_txt}</span></div>
    </div>
    <div class="price">
      <div class="price-lbl">现价</div>
      <div class="price-num">¥ {_n(price_now, 2)}</div>
    </div>
    <div class="badges">
      <span class="badge lynch">林奇分类：{lynch}</span>
      <span class="badge graham">格雷厄姆质量：{gbadge}</span>
    </div>
    <div class="val-title">估值与市场</div>
    <div class="val-row">{val_cards}</div>
    <div class="foot-note">数据来源：东方财富 / 巨潮年报 · 估值分位基于近 10 年历史序列</div>
    """

    # ---- 图 2：核心指标 + 近 5 年趋势 ----
    metric_cards = ""
    for label, v, unit in [
        ("ROE", roe, "%"),
        ("净利率", net_margin, "%"),
        ("毛利率", gross_margin, "%"),
        ("分红比例", payout, "%"),
    ]:
        metric_cards += (
            f'<div class="metric-card">'
            f'<div class="metric-lbl">{label}</div>'
            f'<div class="metric-num">{_n(v, 1, unit)}</div>'
            f'</div>'
        )

    # 近 5 年柱状（营收 + 净利 双系列）
    max_v = 0.0
    for r in recent:
        max_v = max(max_v, float(r.get("revenue") or 0), float(r.get("profit") or 0))
    bars = ""
    for r in recent:
        y = r.get("year")
        rev = float(r.get("revenue") or 0)
        prof = float(r.get("profit") or 0)
        h_rev = round(rev / max_v * 100, 1) if max_v else 0
        h_prof = round(prof / max_v * 100, 1) if max_v else 0
        bars += (
            f'<div class="bar-group">'
            f'<div class="bar-cols">'
            f'<div class="bar rev" style="height:{h_rev}%"><span class="bar-val">{_n(rev, 0)}</span></div>'
            f'<div class="bar prof" style="height:{h_prof}%"><span class="bar-val">{_n(prof, 0)}</span></div>'
            f'</div>'
            f'<div class="bar-year">{y}</div>'
            f'</div>'
        )

    slide2 = f"""
    <div class="sec-title">核心财务指标 <span class="sec-sub">最新报告期</span></div>
    <div class="metric-row">{metric_cards}</div>
    <div class="sec-title" style="margin-top:14px;">近 5 年营收 / 净利 <span class="sec-sub">亿元</span></div>
    <div class="chart">
      <div class="legend"><span class="lg-dot rev"></span>营业总收入 <span class="lg-dot prof" style="margin-left:12px;"></span>归母净利润</div>
      <div class="bars">{bars}</div>
    </div>
    <div class="foot-note">金额单位：亿元 · 归母口径</div>
    """

    # ---- 图 3：商业模式 + 风险 + 免责 ----
    bm = (narrative or {}).get("business_model") or {}
    revenue_source = bm.get("revenue_source") or nd.get("main_business") or "—"
    moat = bm.get("moat") or "—"
    risks = (narrative or {}).get("risks") or []

    risk_items = "".join(f'<div class="risk-item"><span class="risk-dot"></span>{r}</div>' for r in risks[:3])
    if not risk_items:
        risk_items = '<div class="risk-item"><span class="risk-dot"></span>数据源暂未覆盖风险字段。</div>'

    slide3 = f"""
    <div class="sec-title">商业模式 <span class="sec-sub">靠什么赚钱 · 护城河</span></div>
    <div class="bm-block">
      <div class="bm-line"><span class="bm-k">收入来源</span><span class="bm-v">{revenue_source}</span></div>
      <div class="bm-line"><span class="bm-k">护城河</span><span class="bm-v">{moat}</span></div>
    </div>
    <div class="sec-title" style="margin-top:14px;">风险提示</div>
    <div class="risk-block">{risk_items}</div>
    <div class="disclaimer">本图仅呈现客观数据与第三方/模型事实描述，不含任何价格点位、仓位或买卖建议，不构成投资建议。数据来源：东方财富、巨潮资讯年报。市场有风险，投资需谨慎。</div>
    """

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#e9edf2; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; -webkit-font-smoothing:antialiased; }}
  .slide {{ width:390px; background:{C['bg']}; color:{C['ink']}; padding:26px 24px 20px; position:relative; }}
  .slide + .slide {{ margin-top:18px; }}

  .brand {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }}
  .brand-name {{ font-size:13px; font-weight:700; color:{C['accent']}; letter-spacing:1px; }}
  .brand-period {{ font-size:11px; color:#fff; background:{C['accent']}; padding:3px 10px; border-radius:3px; font-weight:600; }}

  .co-name {{ font-size:30px; font-weight:800; letter-spacing:1px; color:{C['accent']}; line-height:1.15; }}
  .co-meta {{ margin-top:8px; font-size:14px; color:{C['faint']}; }}
  .co-meta .code {{ font-weight:700; color:{C['ink']}; }}
  .co-meta .sep {{ margin:0 6px; color:{C['line']}; }}

  .price {{ margin-top:18px; display:flex; align-items:baseline; gap:12px; }}
  .price-lbl {{ font-size:13px; color:{C['faint']}; }}
  .price-num {{ font-size:40px; font-weight:800; color:{C['up']}; line-height:1; }}

  .badges {{ margin-top:16px; display:flex; flex-direction:column; gap:8px; }}
  .badge {{ font-size:13px; padding:8px 12px; border-radius:6px; font-weight:600; line-height:1.4; }}
  .badge.lynch {{ background:#eef3fb; color:{C['accent2']}; border:1px solid #c9d8ec; }}
  .badge.graham {{ background:#eef7f1; color:{C['down']}; border:1px solid #c4e3d2; }}

  .val-title, .sec-title {{ font-size:17px; font-weight:700; color:{C['ink']}; margin-top:22px; padding-bottom:8px; border-bottom:2px solid {C['accent']}; }}
  .sec-title {{ margin-top:0; }}
  .sec-sub {{ font-size:12px; font-weight:400; color:{C['faint']}; margin-left:6px; }}

  .val-row {{ display:flex; gap:10px; margin-top:14px; }}
  .val-card {{ flex:1; background:{C['card']}; border:1px solid {C['line']}; border-radius:10px; padding:14px 12px; text-align:center; }}
  .val-lbl {{ font-size:13px; color:{C['faint']}; }}
  .val-num {{ font-size:24px; font-weight:800; color:{C['ink']}; margin:6px 0 4px; }}
  .val-pct {{ font-size:11px; color:{C['faint']}; }}
  .zone {{ margin-top:6px; font-size:11px; font-weight:600; }}

  .metric-row {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:14px; }}
  .metric-card {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px; padding:16px 14px; }}
  .metric-lbl {{ font-size:13px; color:{C['faint']}; }}
  .metric-num {{ font-size:28px; font-weight:800; color:{C['accent']}; margin-top:6px; }}

  .chart {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px; padding:14px; margin-top:14px; }}
  .legend {{ font-size:12px; color:{C['faint']}; margin-bottom:10px; }}
  .lg-dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:middle; }}
  .lg-dot.rev {{ background:{C['accent2']}; }}
  .lg-dot.prof {{ background:{C['up']}; }}
  .bars {{ display:flex; gap:10px; align-items:flex-end; height:150px; }}
  .bar-group {{ flex:1; display:flex; flex-direction:column; align-items:center; height:100%; }}
  .bar-cols {{ display:flex; gap:4px; align-items:flex-end; flex:1; width:100%; justify-content:center; }}
  .bar {{ width:14px; border-radius:3px 3px 0 0; position:relative; }}
  .bar.rev {{ background:{C['accent2']}; }}
  .bar.prof {{ background:{C['up']}; }}
  .bar-val {{ position:absolute; top:-14px; left:50%; transform:translateX(-50%); font-size:9px; color:{C['faint']}; white-space:nowrap; }}
  .bar-year {{ font-size:10px; color:{C['faint']}; margin-top:6px; }}

  .bm-block {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px; padding:14px; margin-top:14px; }}
  .bm-line {{ display:flex; gap:10px; padding:8px 0; font-size:14px; line-height:1.6; }}
  .bm-line + .bm-line {{ border-top:1px dashed {C['line']}; }}
  .bm-k {{ flex:0 0 64px; color:{C['accent']}; font-weight:700; }}
  .bm-v {{ flex:1; color:{C['ink']}; }}

  .risk-block {{ margin-top:14px; }}
  .risk-item {{ display:flex; gap:8px; font-size:14px; line-height:1.6; color:{C['ink']}; padding:8px 0; }}
  .risk-dot {{ flex:0 0 8px; height:8px; border-radius:50%; background:{C['up']}; margin-top:7px; }}

  .disclaimer {{ margin-top:22px; font-size:10px; color:{C['faint']}; line-height:1.7; border-top:1px solid {C['line']}; padding-top:12px; }}
  .foot-note {{ margin-top:16px; font-size:10px; color:{C['faint']}; }}
</style>
</head>
<body>
  {_slide(slide1, 1)}
  {_slide(slide2, 2)}
  {_slide(slide3, 3)}
</body>
</html>"""


def export_png(html: str, out_dir: Path, code: str) -> list[Path]:
    """用 Playwright 截 3 张竖图。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright，请先执行：pip install playwright && playwright install chromium")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"_xhs_{code}.html"
    tmp.write_text(html, encoding="utf-8")
    uri = tmp.resolve().as_uri()

    names = ["封面", "指标趋势", "模式风险"]
    out_paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 2000}, device_scale_factor=3)
        page.goto(uri)
        page.wait_for_timeout(300)
        slides = page.locator("section.slide")
        for i in range(slides.count()):
            fp = out_dir / f"{code}_{i+1:02d}_{names[i]}.png"
            slides.nth(i).screenshot(path=str(fp))
            out_paths.append(fp)
        browser.close()

    tmp.unlink(missing_ok=True)
    return out_paths


def main() -> None:
    ap = argparse.ArgumentParser(description="ValueLine → 小红书长图")
    ap.add_argument("code", nargs="?", default="600519", help="股票代码（默认 600519）")
    ap.add_argument("-o", "--out", default="reports/xhs", help="输出目录（默认 reports/xhs）")
    args = ap.parse_args()

    data, narrative = _load(args.code)
    html = build_html(data, narrative)
    paths = export_png(html, Path(args.out), args.code)
    for fp in paths:
        print(f"生成：{fp}")


if __name__ == "__main__":
    main()
