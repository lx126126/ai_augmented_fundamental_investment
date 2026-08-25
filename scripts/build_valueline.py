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
    def _table(metric_idx: int, unit: str) -> str:
        head = "".join(f"<th>{q}</th>" for q in SEGMENT_LABELS)
        rows = []
        for name, color, revs, margins in SEGMENTS:
            vals = revs if metric_idx == 0 else margins
            cells = [f'<td class="row-head"><span class="seg-dot" style="background:{color}"></span>{name}</td>']
            cells += [f'<td class="num">{_fmt(v)}</td>' for v in vals]
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return (
            f'<table class="dense"><thead><tr><th class="name">{unit}</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )

    rev_table = _table(0, "业务条线")
    margin_table = _table(1, "业务条线")

    # 最新报告期收入占比（None 视为 0，避免求和报错）
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
        '<div class="seg-row">'
        f'<div class="seg-block"><div class="seg-block-title">收入（亿元）</div>{rev_table}</div>'
        f'<div class="seg-block"><div class="seg-block-title">毛利率（%）</div>{margin_table}</div>'
        "</div>"
        f'<div class="seg-bar">{bar}</div>'
        f'<div class="seg-legend-row">{legend}<span class="seg-note">（最新报告期收入占比）</span></div>'
    )


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
  width: 1080px;
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

/* 业务收入构成（两张表并排） */
.seg-row { display: flex; gap: 18px; }
.seg-block { flex: 1; }
.seg-block-title { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 6px; }

/* 商业模式 */
.biz-row { display: flex; padding: 7px 0; border-bottom: 1px dashed var(--line-soft); font-size: 12px; line-height: 1.7; }
.biz-row:last-child { border-bottom: none; }
.biz-k { flex: 0 0 78px; font-weight: 700; color: var(--accent); }
.biz-v { flex: 1; color: #33404f; }

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

/* 机构观点分歧（两列横排） */
.bull-bear { display: flex; gap: 12px; }
.bb-col { flex: 1; border-radius: 8px; padding: 12px 14px; border: 1px solid var(--line-soft); }
.bb-col.bull { border-left: 3px solid var(--up); background: #fdf7f6; }
.bb-col.bear { border-left: 3px solid var(--down); background: #f4faf6; }
.bb-title { font-size: 12px; font-weight: 700; margin-bottom: 6px; }
.bb-col.bull .bb-title { color: var(--up); }
.bb-col.bear .bb-title { color: var(--down); }
.bb-col ul { list-style: none; }
.bb-col li { font-size: 11px; line-height: 1.7; color: #33404f; padding: 3px 0 3px 14px; position: relative; }
.bb-col li::before { content: ""; position: absolute; left: 2px; top: 10px; width: 6px; height: 6px; border-radius: 50%; }
.bb-col.bull li::before { background: var(--up); }
.bb-col.bear li::before { background: var(--down); }

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

/* 复盘层 */
.review { background: var(--amber-bg); border: 1px solid var(--amber-line); border-radius: 8px; padding: 13px 15px; }
.review .r-title { font-size: 13px; font-weight: 700; color: #7a5c0a; margin-bottom: 8px; display: flex; align-items: center; gap: 7px; }
.review .r-title .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--warn); }
.review .r-row { font-size: 11.5px; line-height: 1.7; padding: 3px 0; }
.review .r-row .k { font-weight: 600; color: #7a5c0a; margin-right: 4px; }
.review .r-row.verified .k { color: var(--down); }
.review .r-row.refuted .k { color: var(--up); }

/* 数据校验 */
.verify { font-size: 11px; color: var(--faint); line-height: 1.8; }
.verify b { color: var(--muted); font-weight: 600; }
.footer { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 10px; color: var(--faint); line-height: 1.7; }
@media print { body { background: #fff; padding: 0; } .page { box-shadow: none; margin: 0; width: 100%; } }
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
      <div class="co-name">中国神华<span class="en">SHENHUA ENERGY</span></div>
      <div class="co-meta">
        <span class="code">601088.SH</span> · <span class="code">1088.HK</span>
        <span class="tag">煤炭开采 · 动力煤</span>
        <span class="tag">沪深300</span>
        <div style="margin-top:3px;">报告期：2026 Q2 · 发布日期：2026-08-20</div>
      </div>
    </div>
    <div class="header-right">
      <span class="quarter">2026 Q2 更新</span>
      <div class="badges">
        <span class="badge lynch">林奇分类：周期型 · 稳健收息</span>
        <span class="badge graham">格雷厄姆质量：高（低负债 · 净现金）</span>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="sec-title">商业模式 <span class="hint">靠什么赚钱 · 竞争地位 · 护城河</span></div>
    <div class="biz">
      <div class="biz-row">
        <div class="biz-k">盈利来源</div>
        <div class="biz-v">煤炭生产销售（动力煤）＋火电发电＋铁路/港口/航运＋煤化工，一体化运营；煤炭为利润核心，电力与运输为稳定补充。</div>
      </div>
      <div class="biz-row">
        <div class="biz-k">盈利结构</div>
        <div class="biz-v">煤炭业务贡献约 64% 收入与主要毛利，长协煤锁定价格；电力、运输平滑煤价周期波动。</div>
      </div>
      <div class="biz-row">
        <div class="biz-k">竞争地位</div>
        <div class="biz-v">全球最大煤炭上市公司、国内动力煤龙头，核定产能与可采储量位居行业前列。</div>
      </div>
      <div class="biz-row">
        <div class="biz-k">护城河</div>
        <div class="biz-v">资源禀赋（低成本大矿）＋一体化协同（煤电路港航）＋长协煤锁定＋规模与牌照壁垒。</div>
      </div>
    </div>
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
    <div class="sec-title">估值与市场 <span class="hint">机构一致预期</span></div>
    <div class="disclaim">市场数据与第三方机构观点汇总，非投资建议。</div>
    <div class="val-grid">
      <div class="val-item">
        <div class="lbl">市盈率 PE（TTM）</div>
        <div class="v">13.2<small>x</small></div>
        <div class="pct pct-low">近10年分位 35% · 偏低</div>
      </div>
      <div class="val-item">
        <div class="lbl">市净率 PB（MRQ）</div>
        <div class="v">1.45<small>x</small></div>
        <div class="pct">近10年分位 40% · 合理</div>
      </div>
      <div class="val-item">
        <div class="lbl">股息率</div>
        <div class="v" style="color:var(--up)">6.1<small>%</small></div>
        <div class="pct pct-low">近10年分位 85% · 高位</div>
      </div>
    </div>

    <div class="market-row">
      <div class="price-range">
        <div class="pr-title">52周价格区间（元）</div>
        <div class="pr-bar"><div class="pr-marker" style="left:43%"></div></div>
        <div class="pr-labels">
          <span>52周最低 <b>38.5</b></span>
          <span>现价 <b>41.8</b></span>
          <span>52周最高 <b>46.2</b></span>
        </div>
      </div>
      <div class="consensus">
        <div>机构目标价（<b>28</b> 家覆盖，单位：元）</div>
        <div class="tp-grid">
          <div class="tp-cell"><div class="k">最高</div><div class="v">52.0</div></div>
          <div class="tp-cell"><div class="k">最低</div><div class="v">38.0</div></div>
          <div class="tp-cell"><div class="k">平均</div><div class="v">45.0</div></div>
          <div class="tp-cell"><div class="k">中位</div><div class="v">44.5</div></div>
        </div>
        <div class="rating-bar">
          <div class="seg" style="width:43%;background:#c0392b"></div>
          <div class="seg" style="width:36%;background:#e8a09b"></div>
          <div class="seg" style="width:18%;background:#a9c2d4"></div>
          <div class="seg" style="width:3%;background:#8fbfa0"></div>
        </div>
        <div class="rating-legend"><span>买入 12</span><span>增持 10</span><span>中性 5</span><span>减持 1</span></div>
      </div>
    </div>

    <div class="graham">
      <div class="g-title">格雷厄姆质量体检</div>
      <div class="g-row"><span>资产负债率</span><b>23.5% · 优秀</b></div>
      <div class="g-row"><span>流动比率</span><b>2.1 · 稳健</b></div>
      <div class="g-row"><span>盈利稳定性（5年）</span><b>连续正盈利</b></div>
      <div class="g-row"><span>净现金 / 有息负债</span><b>净现金状态</b></div>
      <div class="g-score">综合质量评分：<b>8.2 / 10</b>（防御型及格线 6.5）</div>
    </div>
  </div>

  <div class="section">
    <div class="sec-title">投资逻辑</div>
    <ul class="thesis">
      <li>一体化「煤电路港航」协同，长协煤占比高，盈利穿越煤价周期、波动显著低于纯煤企。</li>
      <li>高分红承诺：分红率长期不低于 60%，当前股息率 6%+，具备类债券收息属性。</li>
      <li>资产负债表干净，低负债 + 净现金，符合格雷厄姆式财务稳健与安全边际要求。</li>
    </ul>
  </div>

  <div class="section">
    <div class="sec-title">风险提示</div>
    <ul class="risk">
      <li>动力煤现货价超预期下行，长协价重谈带来盈利下修压力。</li>
      <li>宏观经济复苏不及预期，压制电力需求与发电量。</li>
      <li>资本开支加大或分红率下调，削弱收息逻辑。</li>
    </ul>
  </div>

  <div class="section">
    <div class="sec-title">机构观点分歧 <span class="hint">多 vs 空</span></div>
    <div class="disclaim">以下为第三方机构代表性观点，非本人立场。</div>
    <div class="bull-bear">
      <div class="bb-col bull">
        <div class="bb-title">看多观点（买入 / 增持）</div>
        <ul>
          <li>长协煤占比超七成，盈利穿越煤价周期、波动显著小于纯煤企。</li>
          <li>高股息 + 低估值，红利资产稀缺性凸显，险资长线配置需求。</li>
          <li>煤电一体化对冲周期，充沛现金流支撑高分红可持续。</li>
        </ul>
      </div>
      <div class="bb-col bear">
        <div class="bb-title">看空观点（中性偏空 / 减持）</div>
        <ul>
          <li>动力煤需求中长期见顶，煤价中枢面临逐步下移。</li>
          <li>新能源替代加速，火电利用小时数与电价承压。</li>
          <li>分红率已处高位，进一步提升空间有限。</li>
        </ul>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="sec-title">财务造假检测 <span class="hint">算法打磨中</span></div>
    <div class="fraud">
      <div class="f-row"><span>M-Score（Beneish）</span><b class="ok">-2.8 · 安全</b></div>
      <div class="f-row"><span>经营现金流 / 净利润</span><b class="ok">1.5 · 健康</b></div>
      <div class="f-row"><span>应收增速 vs 营收增速</span><b class="ok">背离 -3% · 正常</b></div>
      <div class="f-row"><span>存货 / 营收比异常</span><b class="ok">无异常</b></div>
      <div class="f-row"><span>审计意见</span><b>标准无保留</b></div>
      <div class="f-score">综合造假风险：<b class="ok">低</b></div>
    </div>
    <div style="font-size:10px;color:var(--faint);margin-top:5px;">检测算法与阈值待打磨，当前为占位示例。</div>
  </div>

  <div class="section">
    <div class="sec-title">季度复盘 <span class="hint">上季假设 → 实际 → 验证</span></div>
    <div class="review">
      <div class="r-title"><span class="dot"></span>2026 Q2 复盘</div>
      <div class="r-row"><span class="k">上季假设：</span>Q2 煤价企稳，长协价维持，盈利环比基本持平。</div>
      <div class="r-row"><span class="k">实际：</span>现货煤价小幅反弹，长协履约稳定，归母净利略超预期。</div>
      <div class="r-row verified"><span class="k">验证 ✓：</span>「盈利穿越周期」主逻辑成立。</div>
      <div class="r-row refuted"><span class="k">打脸 ✗：</span>Q2 火电发电量低于预期，第二曲线贡献仍需观察。</div>
    </div>
  </div>

  <div class="section">
    <div class="sec-title">数据校验记录</div>
    <div class="verify">
      <div><b>数据来源：</b>AKShare（主） + 东方财富（备用）</div>
      <div><b>财务口径：</b>以公司财报 / Wind 一致预期为准</div>
      <div><b>机构数据：</b>目标价与评级截至报告日，家数可能变动</div>
      <div><b>校验日期：</b>2026-08-20</div>
      <div><b>校验人：</b>李潇</div>
      <div><b>备注：</b>本页为模板演示，数据为示例值，正式发布前需逐项核对。</div>
    </div>
  </div>

  <div class="footer">
    本页为个人投研研究记录，仅供学习交流，不构成任何投资建议或买卖依据。52周价格、机构目标价与多空观点均为公开市场数据及第三方机构观点，非本人建议。数据可能存在误差或滞后，请以公司官方披露及监管文件为准。公开版遵循「完全去操作」原则，不含本人操作建议。
  </div>

</div>
</body>
</html>
"""


def _load_real_data() -> dict | None:
    """尝试加载真实数据（神华 601088），失败返回 None。"""
    if not _HAS_DATA:
        return None
    try:
        return build_template_data("601088")
    except Exception as e:
        print(f"[build_valueline] 加载真实数据失败，降级为示例数据: {e}")
        return None


def build() -> None:
    global YEARS, FINANCIALS, QUARTER_LABELS, QUARTERLY, SEGMENT_LABELS, SEGMENTS
    real = _load_real_data()
    if real:
        YEARS = real["years"]
        FINANCIALS = real["financials"]
        QUARTER_LABELS = real["quarter_labels"]
        QUARTERLY = real["quarterly"]
        report_period = real["report_period"]
        if real["segments"]:
            SEGMENT_LABELS = real["segment_labels"]
            SEGMENTS = real["segments"]
        data_src = "真实数据 601088"
    else:
        report_period = "2026Q2"
        data_src = "示例数据"

    year_range = f"{YEARS[0]}–{YEARS[-1]}"
    quarter_range = f"{QUARTER_LABELS[0]}–{QUARTER_LABELS[-1]}"
    segment_range = f"{SEGMENT_LABELS[0]}–{SEGMENT_LABELS[-1]}" if SEGMENT_LABELS else ""

    html = (
        TEMPLATE
        .replace("@@CSS@@", CSS)
        .replace("@@TABLE@@", build_table())
        .replace("@@QUARTER_TABLE@@", build_quarter_table())
        .replace("@@SEGMENTS@@", build_segments())
        .replace("@@YEAR_RANGE@@", year_range)
        .replace("@@QUARTER_RANGE@@", quarter_range)
        .replace("@@SEGMENT_RANGE@@", segment_range)
    )

    root = Path(__file__).resolve().parent.parent

    # 1. templates/valueline.html（预览/调版式）
    tpl_out = root / "templates" / "valueline.html"
    tpl_out.write_text(html, encoding="utf-8")

    # 2. reports/{报告期}/601088.html（归档报告）
    report_out = root / "reports" / report_period / "601088.html"
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(html, encoding="utf-8")

    print(f"generated:")
    print(f"  预览: {tpl_out}")
    print(f"  归档: {report_out}")
    print(f"  数据来源: {data_src}")
    print(f"  年度: {year_range} ({len(YEARS)} 年), 季度: {quarter_range} ({len(QUARTER_LABELS)} 季)")


if __name__ == "__main__":
    build()
