#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ValueLine 一页研报模板 templates/valueline.html。

示例数据：中国神华（2007 上市 - 2025，上市以来全历史），仅供模板演示。
未来升级：从数据层读取真实财报数据，替换 FINANCIALS 后重新生成。

用法：
    python scripts/build_valueline.py
"""
from __future__ import annotations
from pathlib import Path

# ============ 上市以来全历史（示例数据，非精确） ============
YEARS = list(range(2007, 2026))  # 2007 上市 - 2025

# 结构：(分组标题 or None, 指标名, [逐年数据])
FINANCIALS = [
    ("利润表", None, None),
    (None, "营业收入（亿元）", [821, 1071, 1213, 1521, 2082, 2503, 2838, 2484, 1770, 1831, 2487, 2641, 2419, 2333, 3352, 3445, 3431, 3380, 3350]),
    (None, "归母净利润（亿元）", [197, 265, 303, 372, 449, 477, 457, 368, 161, 227, 450, 439, 432, 392, 502, 696, 597, 586, 570]),
    (None, "毛利率 %", [38.0, 36.5, 35.8, 36.2, 37.5, 36.0, 35.2, 33.8, 34.5, 35.0, 38.5, 39.2, 38.8, 38.6, 40.2, 40.2, 38.5, 37.9, 37.5]),
    (None, "净利率 %", [24.0, 24.7, 25.0, 24.5, 21.6, 19.0, 16.1, 14.8, 9.1, 12.4, 18.1, 16.6, 17.9, 16.8, 15.0, 20.2, 17.4, 17.3, 17.0]),
    (None, "经营现金流净额（亿元）", [299, 390, 559, 594, 723, 717, 543, 678, 554, 819, 951, 883, 635, 813, 946, 1097, 886, 902, 880]),
    (None, "ROE（摊薄）%", [20.1, 18.5, 17.9, 18.4, 19.2, 17.0, 14.3, 11.0, 5.9, 7.5, 13.8, 12.7, 12.1, 10.7, 13.1, 16.5, 14.9, 14.2, 13.8]),
    ("资产负债表", None, None),
    (None, "总资产（亿元）", [2626, 2940, 3210, 3677, 4235, 4661, 4919, 5263, 5584, 5701, 5814, 6069, 6217, 6301, 6438, 6550, 6600, 6700, 6800]),
    (None, "总负债（亿元）", [740, 830, 900, 1030, 1200, 1330, 1400, 1500, 1332, 1450, 1500, 1608, 1650, 1617, 1650, 1568, 1545, 1540, 1520]),
    (None, "净资产（归母）（亿元）", [1886, 2110, 2310, 2647, 3035, 3331, 3519, 3763, 4252, 4251, 4314, 4461, 4567, 4684, 4788, 4982, 5055, 5160, 5280]),
    (None, "货币资金（亿元）", [1200, 1300, 1400, 1500, 1600, 1700, 1650, 1600, 1420, 1450, 1500, 1580, 1600, 1620, 1500, 1580, 1610, 1650, 1680]),
    (None, "存货（亿元）", [90, 95, 100, 105, 110, 115, 120, 125, 128, 130, 132, 135, 133, 132, 126, 130, 142, 140, 138]),
    (None, "应收账款（亿元）", [120, 130, 140, 150, 160, 170, 175, 180, 189, 195, 200, 210, 208, 205, 198, 210, 218, 220, 215]),
    (None, "有息负债（亿元）", [350, 380, 400, 450, 500, 550, 580, 600, 620, 650, 680, 700, 690, 680, 650, 630, 610, 600, 580]),
    (None, "商誉（亿元）", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 30, 30, 30, 30, 30]),
    ("股东回报", None, None),
    (None, "股息率 %", [3.5, 3.8, 4.0, 4.2, 4.5, 4.6, 4.8, 5.0, 4.5, 4.2, 4.8, 5.2, 5.5, 5.8, 6.0, 5.6, 6.0, 6.3, 6.4]),
]


def _fmt(v) -> str:
    if v is None:
        return "—"
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

/* 全历史宽表 */
.table-scroll { overflow-x: auto; }
table.dense { width: 100%; border-collapse: collapse; font-size: 8.5px; table-layout: fixed; }
table.dense th, table.dense td { padding: 3px 2px; text-align: right; border-bottom: 1px solid var(--line-soft); overflow: hidden; }
table.dense th.name, table.dense td.name { text-align: left; width: 96px; }
table.dense th { background: var(--bg-soft); color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); font-size: 8px; }
table.dense td.num { font-variant-numeric: tabular-nums; }
table.dense tr.group td { background: #eef3fb; color: var(--accent-2); font-weight: 600; font-size: 9px; text-align: left; border-bottom: 1px solid var(--line); }
table.dense .row-head { font-weight: 500; color: #33404f; }

.body { display: flex; gap: 18px; flex: 1; }
.col-main { flex: 0 0 62%; }
.col-side { flex: 0 0 38%; }
.section { margin-bottom: 15px; }
.sec-title { font-size: 13px; font-weight: 700; color: var(--accent); padding-bottom: 5px; margin-bottom: 9px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: baseline; }
.sec-title .hint { font-size: 10px; font-weight: 400; color: var(--faint); }
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
<title>ValueLine 一页研报 · 模板 v1.2</title>
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

  <div class="section">
    <div class="sec-title">核心财务数据（上市以来全历史 2007–2025） <span class="hint">单位：亿元 / %</span></div>
@@TABLE@@
    <div style="font-size:9px;color:var(--faint);margin-top:4px;">注：示例数据，仅演示模板版式，非实时行情，不作投资依据；正式版覆盖招股书及上市前披露数据。</div>
  </div>

  <div class="body">
    <div class="col-main">
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
        .replace("@@TABLE@@", build_table())
    )
    out = Path(__file__).resolve().parent.parent / "templates" / "valueline.html"
    out.write_text(html, encoding="utf-8")
    print(f"generated: {out}")


if __name__ == "__main__":
    build()
