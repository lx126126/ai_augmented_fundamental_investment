#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ValueLine 一页研报模板 templates/valueline.html。

示例数据：中国神华（2007-2024 全历史 + 近 5 年 + 最新报告期），仅供模板演示。
未来升级：从数据层读取真实财报数据，替换 DATA 后重新生成 HTML。

用法：
    python scripts/build_valueline.py
"""
from __future__ import annotations
from pathlib import Path

# ============ 示例数据（中国神华，标注示例，非精确） ============
YEARS = list(range(2007, 2025))  # 2007 上市 - 2024

REV = [821, 1071, 1213, 1521, 2082, 2503, 2838, 2484, 1770, 1831, 2487, 2641, 2419, 2333, 3352, 3445, 3431, 3380]
PROFIT = [197, 265, 303, 372, 449, 477, 457, 368, 161, 227, 450, 439, 432, 392, 502, 696, 597, 586]
ROE = [20.1, 18.5, 17.9, 18.4, 19.2, 17.0, 14.3, 11.0, 5.9, 7.5, 13.8, 12.7, 12.1, 10.7, 13.1, 16.5, 14.9, 14.2]
DIV = [3.5, 3.8, 4.0, 4.2, 4.5, 4.6, 4.8, 5.0, 4.5, 4.2, 4.8, 5.2, 5.5, 5.8, 6.0, 5.6, 6.0, 6.3]

# ============ SVG 图表参数 ============
W, H = 228, 168
LEFT, RIGHT, TOP, BOTTOM = 30, 6, 14, 16
PLOT_W = W - LEFT - RIGHT
PLOT_H = H - TOP - BOTTOM
PLOT_BOTTOM = TOP + PLOT_H
N = len(YEARS)
GROUP_W = PLOT_W / N
BAR_W = 7.0
BAR_OFF = (GROUP_W - BAR_W) / 2

C_REV = "#85B7EB"
C_PROFIT = "#E24B4A"
C_ROE = "#E24B4A"
C_DIV = "#BA7517"


def axis_ticks(vmax, step):
    out, v = [], 0.0
    while v <= vmax + 1e-6:
        y = PLOT_BOTTOM - v / vmax * PLOT_H
        out.append((f"{v:g}", y))
        v += step
    return out


def bars(values, vmax, color):
    rows = []
    for i, v in enumerate(values):
        h = v / vmax * PLOT_H
        x = LEFT + i * GROUP_W + BAR_OFF
        y = PLOT_BOTTOM - h
        rows.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{BAR_W}" height="{h:.2f}" rx="1" fill="{color}"/>')
    return "\n".join(rows)


def polyline(values, vmax, color, width="1.6"):
    pts = []
    for i, v in enumerate(values):
        x = LEFT + i * GROUP_W + GROUP_W / 2
        y = PLOT_BOTTOM - v / vmax * PLOT_H
        pts.append(f"{x:.2f},{y:.2f}")
    return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"/>'


def dots(values, vmax, color):
    rows = []
    for i, v in enumerate(values):
        x = LEFT + i * GROUP_W + GROUP_W / 2
        y = PLOT_BOTTOM - v / vmax * PLOT_H
        rows.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.6" fill="{color}"/>')
    return "\n".join(rows)


def x_ticks():
    idxs = [0, 5, 10, 17]
    rows = []
    for i in idxs:
        x = LEFT + i * GROUP_W + GROUP_W / 2
        rows.append(f'<text x="{x:.2f}" y="{PLOT_BOTTOM + 11}" font-size="8" fill="#8a97a6" text-anchor="middle">{YEARS[i]}</text>')
    return "\n".join(rows)


def y_ticks(ticks):
    rows = []
    for label, y in ticks:
        rows.append(f'<text x="{LEFT - 4}" y="{y:.2f}" font-size="7.5" fill="#8a97a6" text-anchor="end" dominant-baseline="middle">{label}</text>')
        rows.append(f'<line x1="{LEFT}" y1="{y:.2f}" x2="{W - RIGHT}" y2="{y:.2f}" stroke="#eef1f5" stroke-width="0.5"/>')
    return "\n".join(rows)


def svg(title, body, legend, yticks):
    return f'''<svg viewBox="0 0 {W} {H}" role="img" aria-label="{title}">
<title>{title}</title>
<text x="{LEFT}" y="10" font-size="10" font-weight="500" fill="#33404f">{title}</text>
{y_ticks(yticks)}
{body}
{x_ticks()}
{legend}
</svg>'''


def build_rev():
    body = bars(REV, 3500, C_REV)
    legend = f'<text x="{LEFT}" y="{H - 4}" font-size="8" fill="#5c6b7a">单位：亿元</text>'
    return svg("营业收入（亿元）", body, legend, axis_ticks(3500, 1000))


def build_profit():
    body = bars(PROFIT, 800, C_PROFIT)
    legend = f'<text x="{LEFT}" y="{H - 4}" font-size="8" fill="#5c6b7a">单位：亿元</text>'
    return svg("归母净利润（亿元）", body, legend, axis_ticks(800, 200))


def build_roe():
    body = polyline(ROE, 25, C_ROE) + "\n" + dots(ROE, 25, C_ROE)
    body += "\n" + polyline(DIV, 25, C_DIV, width="1.3") + "\n" + dots(DIV, 25, C_DIV)
    legend = (
        '<g font-size="8">'
        f'<line x1="{LEFT}" y1="{H-8}" x2="{LEFT+12}" y2="{H-8}" stroke="{C_ROE}" stroke-width="1.6"/>'
        f'<text x="{LEFT+15}" y="{H-5}" fill="#5c6b7a">ROE</text>'
        f'<line x1="{LEFT+48}" y1="{H-8}" x2="{LEFT+60}" y2="{H-8}" stroke="{C_DIV}" stroke-width="1.3"/>'
        f'<text x="{LEFT+63}" y="{H-5}" fill="#5c6b7a">股息率</text>'
        '</g>'
    )
    return svg("ROE 与股息率（%）", body, legend, axis_ticks(25, 5))


# ============ 核心财务指标表（近 5 年 + 最新报告期） ============
# 结构：(分组标题 or None, 指标名, [5年 + 最新] 共 6 列)
FINANCIALS = [
    ("利润表", None, None),
    (None, "营业收入（亿元）", [2333, 3352, 3445, 3431, 3380, 856]),
    (None, "归母净利润（亿元）", [392, 502, 696, 597, 586, 152]),
    (None, "毛利率 %", [38.6, 40.2, 40.2, 38.5, 37.9, 38.6]),
    (None, "净利率 %", [16.8, 15.0, 20.2, 17.4, 17.3, 17.8]),
    (None, "经营现金流净额（亿元）", [813, 946, 1097, 886, 902, 248]),
    (None, "ROE（摊薄）%", [10.7, 13.1, 16.5, 14.9, 14.2, 3.6]),
    ("资产负债表", None, None),
    (None, "总资产（亿元）", [5584, 6069, 6217, 6301, 6438, 6550]),
    (None, "总负债（亿元）", [1332, 1608, 1617, 1568, 1545, 1520]),
    (None, "净资产（归母）（亿元）", [4150, 4360, 4500, 4633, 4793, 4930]),
    (None, "货币资金（亿元）", [1420, 1580, 1620, 1500, 1580, 1610]),
    (None, "存货（亿元）", [128, 135, 132, 126, 130, 142]),
    (None, "应收账款（亿元）", [189, 210, 205, 198, 210, 218]),
    (None, "有息负债（亿元）", [620, 700, 680, 650, 630, 610]),
    (None, "商誉（亿元）", [30, 30, 30, 30, 30, 30]),
    ("股东回报", None, None),
    (None, "股息率 %", [5.8, 6.0, 5.6, 6.0, 6.3, None]),
]

TABLE_COLS = ["2020", "2021", "2022", "2023", "2024", "最新"]


def build_table():
    head = "".join(f"<th>{c}</th>" for c in TABLE_COLS)
    rows = []
    for group, name, vals in FINANCIALS:
        if group:
            rows.append(f'<tr class="group"><td colspan="{len(TABLE_COLS) + 1}">{group}</td></tr>')
            continue
        cells = [f'<td class="row-head">{name}</td>']
        for v in vals:
            cells.append(f'<td class="num">{"—" if v is None else f"{v:,.0f}" if isinstance(v, int) else f"{v:.1f}"}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'''<table>
<thead><tr><th>指标</th>{head}</tr></thead>
<tbody>
{"".join(rows)}
</tbody>
</table>'''


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
  width: 794px;
  min-height: 1123px;
  margin: 0 auto;
  background: #ffffff;
  box-shadow: 0 2px 16px rgba(15, 61, 110, 0.10);
  padding: 30px 40px 26px;
  display: flex;
  flex-direction: column;
}
.header { display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 14px; border-bottom: 3px solid var(--accent); }
.co-name { font-size: 24px; font-weight: 700; letter-spacing: 1px; color: var(--accent); line-height: 1.15; }
.co-name .en { font-size: 12px; font-weight: 400; color: var(--faint); letter-spacing: 0.5px; margin-left: 8px; }
.co-meta { margin-top: 8px; font-size: 11px; color: var(--muted); line-height: 1.7; }
.co-meta .tag { display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 10px; margin-right: 6px; border: 1px solid var(--line); background: var(--bg-soft); color: var(--muted); }
.co-meta .code { font-weight: 600; color: var(--ink); }
.header-right { text-align: right; flex-shrink: 0; margin-left: 16px; }
.quarter { display: inline-block; background: var(--accent); color: #fff; padding: 5px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }
.badges { margin-top: 8px; display: flex; flex-direction: column; gap: 5px; align-items: flex-end; }
.badge { font-size: 10px; padding: 2px 8px; border-radius: 3px; font-weight: 500; }
.badge.lynch { background: #eef3fb; color: var(--accent-2); border: 1px solid #c9d8ec; }
.badge.graham { background: #eef7f1; color: var(--down); border: 1px solid #c4e3d2; }
.summary { margin: 13px 0; padding: 10px 14px; background: var(--bg-soft); border-left: 3px solid var(--accent-2); font-size: 12px; line-height: 1.7; color: #33404f; }
.summary b { color: var(--ink); }
.charts { display: flex; gap: 15px; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: #fff; margin-bottom: 16px; }
.charts svg { flex: 1; }
.body { display: flex; gap: 18px; flex: 1; }
.col-main { flex: 0 0 62%; }
.col-side { flex: 0 0 38%; }
.section { margin-bottom: 15px; }
.sec-title { font-size: 13px; font-weight: 700; color: var(--accent); padding-bottom: 5px; margin-bottom: 9px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: baseline; }
.sec-title .hint { font-size: 10px; font-weight: 400; color: var(--faint); }
table { width: 100%; border-collapse: collapse; font-size: 10px; }
th, td { padding: 4px 6px; text-align: right; border-bottom: 1px solid var(--line-soft); }
th:first-child, td:first-child { text-align: left; }
th { background: var(--bg-soft); color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); font-size: 9.5px; }
td.num { font-variant-numeric: tabular-nums; }
tr.group td { background: #eef3fb; color: var(--accent-2); font-weight: 600; font-size: 9.5px; text-align: left; border-bottom: 1px solid var(--line); }
.row-head { font-weight: 500; color: #33404f; }
.val-grid { display: flex; flex-direction: column; gap: 8px; }
.val-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; border: 1px solid var(--line-soft); border-radius: 6px; background: #fff; }
.val-item .lbl { font-size: 11px; color: var(--muted); }
.val-item .lbl b { display: block; font-size: 12px; color: var(--ink); font-weight: 600; }
.val-item .val { text-align: right; }
.val-item .val .v { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.val-item .val .pct { font-size: 10px; color: var(--faint); margin-top: 1px; }
.pct-low { color: var(--up) !important; font-weight: 600; }
.pct-high { color: var(--down) !important; font-weight: 600; }
.graham { margin-top: 10px; padding: 10px; background: var(--bg-soft); border-radius: 6px; }
.graham .g-title { font-size: 11px; font-weight: 700; color: var(--accent); margin-bottom: 6px; }
.graham .g-row { display: flex; justify-content: space-between; font-size: 10.5px; padding: 2px 0; color: var(--muted); }
.graham .g-row b { color: var(--ink); font-weight: 600; }
.graham .g-score { margin-top: 7px; padding-top: 7px; border-top: 1px dashed var(--line); font-size: 11px; color: var(--ink); }
.graham .g-score b { color: var(--down); }
.thesis, .risk { list-style: none; }
.thesis li, .risk li { font-size: 11px; line-height: 1.6; color: #33404f; padding: 4px 0 4px 16px; position: relative; border-bottom: 1px dashed var(--line-soft); }
.thesis li:last-child, .risk li:last-child { border-bottom: none; }
.thesis li::before { content: ""; position: absolute; left: 2px; top: 11px; width: 6px; height: 6px; border-radius: 50%; background: var(--accent-2); }
.risk li::before { content: "!"; position: absolute; left: 2px; top: 5px; font-size: 10px; font-weight: 700; color: var(--warn); }
.review { background: var(--amber-bg); border: 1px solid var(--amber-line); border-radius: 6px; padding: 11px 12px; }
.review .r-title { font-size: 12px; font-weight: 700; color: #7a5c0a; margin-bottom: 7px; display: flex; align-items: center; gap: 6px; }
.review .r-title .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--warn); }
.review .r-row { font-size: 10.5px; line-height: 1.6; padding: 3px 0; }
.review .r-row .k { font-weight: 600; color: #7a5c0a; margin-right: 4px; }
.review .r-row.verified .k { color: var(--down); }
.review .r-row.refuted .k { color: var(--up); }
.verify { font-size: 10px; color: var(--faint); line-height: 1.7; }
.verify b { color: var(--muted); font-weight: 600; }
.footer { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--line); font-size: 9.5px; color: var(--faint); line-height: 1.6; }
@media print { body { background: #fff; padding: 0; } .page { box-shadow: none; margin: 0; width: 100%; min-height: auto; } }
"""

# ============ HTML 骨架 ============
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ValueLine 一页研报 · 模板 v1.1</title>
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

  <div class="summary">
    <b>一句话定位：</b>煤、电、路、港、航一体化能源龙头，长协煤锁定盈利，现金流充沛、分红慷慨，具备类债券属性的周期成长型现金牛。
  </div>

  <div class="charts">
@@CHART_REV@@
@@CHART_PROFIT@@
@@CHART_ROE@@
  </div>

  <div class="body">
    <div class="col-main">
      <div class="section">
        <div class="sec-title">核心财务指标（上市以来全历史见上图） <span class="hint">近 5 年 + 最新报告期</span></div>
@@TABLE@@
        <div style="font-size:9px;color:var(--faint);margin-top:4px;">注：示例数据，仅演示模板版式，非实时行情，不作投资依据。</div>
      </div>
      <div class="section">
        <div class="sec-title">投资逻辑</div>
        <ul class="thesis">
          <li>一体化「煤电路港航」协同，长协煤占比高，盈利穿越煤价周期、波动显著低于纯煤企。</li>
          <li>高分红承诺：分红率长期不低于 60%，当前股息率 6%+，具备类债券收息属性。</li>
          <li>资产负债表干净，低负债 + 净现金，符合格雷厄姆式财务稳健与安全边际要求。</li>
          <li>火电 + 新能源装机构建第二增长曲线，从纯周期向「周期 + 公用」估值中枢迁移。</li>
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
    </div>

    <div class="col-side">
      <div class="section">
        <div class="sec-title">估值面板 <span class="hint">历史分位</span></div>
        <div class="val-grid">
          <div class="val-item">
            <div class="lbl">市盈率<b>PE（TTM）</b></div>
            <div class="val"><div class="v">13.2<span style="font-size:10px;color:var(--muted)">x</span></div><div class="pct pct-low">分位 35% · 偏低</div></div>
          </div>
          <div class="val-item">
            <div class="lbl">市净率<b>PB（MRQ）</b></div>
            <div class="val"><div class="v">1.45<span style="font-size:10px;color:var(--muted)">x</span></div><div class="pct">分位 40% · 合理</div></div>
          </div>
          <div class="val-item">
            <div class="lbl">股息率<b>Dividend Yield</b></div>
            <div class="val"><div class="v" style="color:var(--up)">6.1<span style="font-size:10px">%</span></div><div class="pct pct-low">分位 85% · 高位</div></div>
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
          <div><b>校验日期：</b>2026-08-20</div>
          <div><b>校验人：</b>李潇</div>
          <div><b>备注：</b>本页为模板演示，数据为示例值，正式发布前需逐项核对。</div>
        </div>
      </div>
    </div>
  </div>

  <div class="footer">
    本页为个人投研研究记录，仅供学习交流，不构成任何投资建议或买卖依据。数据可能存在误差或滞后，请以公司官方披露及监管文件为准。公开版遵循「完全去操作」原则，不含价格点位、仓位与买卖建议。
  </div>

</div>
</body>
</html>
"""


def build() -> None:
    html = (
        TEMPLATE
        .replace("@@CSS@@", CSS)
        .replace("@@CHART_REV@@", build_rev())
        .replace("@@CHART_PROFIT@@", build_profit())
        .replace("@@CHART_ROE@@", build_roe())
        .replace("@@TABLE@@", build_table())
    )
    out = Path(__file__).resolve().parent.parent / "templates" / "valueline.html"
    out.write_text(html, encoding="utf-8")
    print(f"已生成：{out}")


if __name__ == "__main__":
    build()
