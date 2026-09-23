# -*- coding: utf-8 -*-
"""宏观看板构建：拉数 → 渲染 `web/macro.html`。

用法
----
    python scripts/build_macro.py                  # 拉数 + 渲染
    python scripts/build_macro.py --json tmp.json  # 顺带导出 payload（排查用）

产物约定
--------
`web/macro.html` 是**跟踪的构建产物**（与 `web/index.html`、`web/watchlist.html` 同约定）：
它反映「最近一次构建的样子」，所以周更之后工作区会变脏，需要连同数据一起提交。
这与 `reports/` 的处置**不同**（那整目录不入库）—— 因为看板没有逐标的归档需求，
它就是一张页面。

页面设计约束（与项目既有约定一致）
----------------------------------
- 红涨绿跌（中国惯例）
- **色阶只表示变动方向，不表示利好利空**：失业率上行与用电量上行，同一箭头含义相反
- 每个指标**必须显示自己的数据日期**（宏观发布时点差异极大：M2 到 8 月、社融只到 4 月）
- 绝对水平没有意义 → 一律配**历史分位**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.macro.build import build_payload  # noqa: E402

OUT = ROOT / "web" / "macro.html"

# --------------------------------------------------------------------------- #
# 样式：沿用 web/index.html 的主题变量与卡片语言，保证整站一致
# --------------------------------------------------------------------------- #
_CSS = """
:root {
  --ink:#1a2330; --muted:#5c6b7a; --faint:#8a97a6;
  --line:#dde3ea; --accent:#0f3d6e; --accent-2:#14508c; --bg-soft:#f5f7fa;
  --up:#c92a2a; --down:#2f9e44; --warn:#a35b00; --warn-bg:#fff6e8; --warn-line:#f0cfa0;
}
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
body {
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: var(--ink); background: #e9edf1; -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 640px; margin: 0 auto; padding: 18px 16px 44px; }
a { color: var(--accent-2); text-decoration: none; }

.eyebrow { font-size: 12px; color: var(--faint); letter-spacing: .04em; padding: 2px 2px 9px;
  display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.eyebrow b { color: var(--accent); font-weight: 700; font-size: 13px; letter-spacing: .1em; }
.eyebrow .back { font-size: 12px; }

/* ---------- ① 一句话定位 ---------- */
.hero { background: #fff; border-radius: 12px; padding: 18px 16px;
  box-shadow: 0 3px 16px rgba(15,61,110,0.10); }
.hero-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.hero-tag { font-size: 11px; color: var(--accent); background: var(--bg-soft);
  border: 1px solid var(--line); border-radius: 4px; padding: 3px 8px; letter-spacing: .05em; }
.hero-quad { font-size: 30px; font-weight: 700; letter-spacing: .08em; line-height: 1.15; }
.hero-quad.diverge { font-size: 22px; color: var(--warn); }
.hero-def { font-size: 13px; color: var(--muted); margin-top: 3px; width: 100%; }
.hero-basis { font-size: 12px; color: var(--faint); margin-top: 10px; line-height: 1.7;
  font-variant-numeric: tabular-nums; padding-top: 10px; border-top: 1px dashed var(--line); }
.hero-note { font-size: 12px; color: var(--warn); background: var(--warn-bg);
  border: 1px solid var(--warn-line); border-radius: 8px; padding: 10px 12px;
  margin-top: 10px; line-height: 1.7; }

/* 美林时钟 2x2 */
.clock { margin-top: 14px; }
.clock-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.q { border: 1px solid var(--line); border-radius: 8px; padding: 10px 11px;
  background: var(--bg-soft); }
.q .qn { font-size: 14px; font-weight: 600; color: var(--muted); }
.q .qd { font-size: 11px; color: var(--faint); margin-top: 3px; }
.q.on { background: linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%);
  border-color: var(--accent); box-shadow: 0 3px 12px rgba(15,61,110,0.24); }
.q.on .qn { color: #fff; }
.q.on .qd { color: rgba(255,255,255,0.86); }
/* 方向分歧：四格都不选中，整体降透明度表达「未定位」而非「渲染失败」 */
.q.dim { opacity: .5; }
.clock-axis { display: flex; font-size: 10px; color: var(--faint); letter-spacing: .06em; }
.clock-axis.x { justify-content: space-between; margin-top: 6px; padding: 0 4px; }
.clock-axis.y { justify-content: space-between; margin-bottom: 5px; padding: 0 4px; }

/* 投票明细 */
.votes { margin-top: 13px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.vote-col h4 { font-size: 11px; color: var(--faint); font-weight: 600; letter-spacing: .05em;
  margin-bottom: 6px; }
.vote { display: flex; align-items: baseline; gap: 6px; font-size: 12px; padding: 3px 0;
  border-bottom: 1px solid var(--bg-soft); }
.vote:last-child { border-bottom: 0; }
.vote .m { flex: 0 0 12px; font-weight: 700; text-align: center; }
.vote .m.up { color: var(--up); } .vote .m.down { color: var(--down); }
.vote .m.flat { color: var(--faint); }
.vote .n { flex: 1; min-width: 0; color: var(--muted); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.vote .v { font-variant-numeric: tabular-nums; color: var(--ink); font-size: 11px; }

/* ---------- ②③④⑤ 指标分组 ---------- */
.section { margin-top: 22px; }
.section-head { margin-bottom: 10px; }
.section-head h2 { font-size: 14px; color: var(--accent); font-weight: 700; letter-spacing: .03em; }
.section-head p { font-size: 11px; color: var(--faint); margin-top: 4px; line-height: 1.6; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.card { background: #fff; border-radius: 10px; padding: 12px 13px 11px;
  box-shadow: 0 2px 10px rgba(15,61,110,0.07); display: flex; flex-direction: column; }
.card.miss { background: #fbfbfc; border: 1px dashed var(--line); box-shadow: none; }
.card .k { font-size: 12px; color: var(--muted); line-height: 1.35; min-height: 32px;
  display: flex; align-items: center; }
.card .v { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums;
  margin-top: 5px; line-height: 1.1; letter-spacing: -.01em; }
.card .v .u { font-size: 11px; font-weight: 400; color: var(--faint); margin-left: 3px;
  letter-spacing: 0; }
.card .v.na { font-size: 13px; font-weight: 400; color: var(--faint); }
.card .row { display: flex; align-items: baseline; justify-content: space-between; gap: 6px;
  margin-top: 5px; font-size: 11px; color: var(--faint); font-variant-numeric: tabular-nums; }
.card .chg { font-weight: 600; }
.card .chg.up { color: var(--up); } .card .chg.down { color: var(--down); }
.card .chg.flat { color: var(--faint); }

.spark { display: block; width: 100%; height: 26px; margin-top: 7px; overflow: visible; }
.spark path { fill: none; stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; }
.spark .base { stroke: var(--line); stroke-width: 1; stroke-dasharray: 2 3; }

.rankbar { margin-top: 7px; }
.rankbar .track { height: 4px; background: var(--bg-soft); border-radius: 2px;
  position: relative; overflow: hidden; }
.rankbar .track i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 2px;
  background: var(--accent-2); opacity: .75; }
.rankbar .lab { font-size: 10px; color: var(--faint); margin-top: 3px;
  display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; }

.card .stamp { font-size: 10px; color: var(--faint); margin-top: 7px; padding-top: 6px;
  border-top: 1px solid var(--bg-soft); font-variant-numeric: tabular-nums; }
.card .flag { font-size: 10px; color: var(--warn); background: var(--warn-bg);
  border: 1px solid var(--warn-line); border-radius: 4px; padding: 3px 6px; margin-top: 6px;
  line-height: 1.5; }

/* ---------- 底部说明 ---------- */
.notes { margin-top: 26px; }
.note-block { background: #fff; border-radius: 10px; padding: 14px 15px; margin-top: 12px;
  box-shadow: 0 2px 10px rgba(15,61,110,0.07); }
.note-block h3 { font-size: 13px; color: var(--accent); margin-bottom: 9px; }
.note-block p { font-size: 11px; color: var(--muted); line-height: 1.8; }
.gap { font-size: 11px; line-height: 1.7; padding: 8px 0; border-bottom: 1px solid var(--bg-soft); }
.gap:last-child { border-bottom: 0; }
.gap b { color: var(--ink); font-size: 12px; }
.gap .r { color: var(--warn); }
.gap .p { color: var(--faint); }
details { margin-top: 8px; }
details summary { font-size: 12px; color: var(--accent-2); cursor: pointer;
  padding: 6px 0; list-style: none; }
details summary::-webkit-details-marker { display: none; }
details summary::before { content: "▸ "; }
details[open] summary::before { content: "▾ "; }
table.cal { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 6px; }
table.cal th { text-align: left; color: var(--faint); font-weight: 600; padding: 6px 4px;
  border-bottom: 1px solid var(--line); font-size: 10px; }
table.cal td { padding: 6px 4px; border-bottom: 1px solid var(--bg-soft); color: var(--muted);
  vertical-align: top; line-height: 1.6; }
table.cal td.n { color: var(--ink); }
table.cal td.d { font-variant-numeric: tabular-nums; white-space: nowrap; }

.foot { text-align: center; font-size: 11px; color: var(--faint); margin-top: 24px;
  line-height: 1.9; padding: 0 8px; }
"""

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0f3d6e">
<title>宏观周期看板 · 投研排雷</title>
<style>__CSS__</style>
</head>
<body>
<div class="wrap">

  <div class="eyebrow">
    <span><b>投研排雷</b> · 宏观周期看板</span>
    <a class="back" href="@@HOME@@">← 返回首页</a>
  </div>

  <!-- ① 一句话定位 -->
  <div class="hero">
    <div class="hero-top">
      <span class="hero-tag">周期定位</span>
      <span class="hero-quad @@QUAD_CLS@@">@@QUAD@@</span>
      <span class="hero-def">@@QUAD_DEF@@</span>
    </div>
    <div class="hero-basis">@@BASIS@@
      <br><span style="color:var(--faint)">判定规则：对全部参与指标<b>等权计票</b>（未做加权），
        「方向」指该指标本期相对上一期的变化方向；第 2 段列出每一票，可自行复核。</span></div>
    @@NOTE@@
    <div class="clock">
      <!-- 🔴 网格实际语义：**行 = 增长**（上排回升、下排回落）、**列 = 通胀**（左列回落、右列回升）。
           所以行标签必须写成一句话，不能像列标签那样左右分置 —— 分置会被读成「列」，与网格相反。 -->
      <div class="clock-axis y" style="justify-content:center">
        <span>上排：增长 ↑　·　下排：增长 ↓</span></div>
      <div class="clock-grid">@@QUADRANTS@@</div>
      <div class="clock-axis x"><span>左列：通胀 ↓</span><span>右列：通胀 ↑</span></div>
    </div>
    <div class="votes">@@VOTES@@</div>
  </div>

  @@SECTIONS@@

  <!-- 数据说明 -->
  <div class="notes">
    @@GAPS_BLOCK@@
    <div class="note-block">
      <h3>指标口径与数据来源</h3>
      <p>下表逐项列出每个指标的发布频率、数据源接口、原生单位与口径说明，
         以及<b>本次实际取到的最新数据日期</b> —— 宏观数据的发布时点差异极大，
         不同指标的「最新」并不在同一天。</p>
      <details>
        <summary>展开逐项口径（@@N_CALIBERS@@ 项）</summary>
        <table class="cal">
          <thead><tr><th>指标</th><th>频率</th><th>数据日期</th><th>数据源</th><th>口径</th></tr></thead>
          <tbody>@@CALIBERS@@</tbody>
        </table>
      </details>
    </div>
    @@ERRORS_BLOCK@@
  </div>

  <div class="foot">
    构建于 @@GENERATED_AT@@ · 共 @@N_TOTAL@@ 个指标（成功 @@N_OK@@ / 失败 @@N_FAIL@@）<br>
    数据来源：AKShare（东财 / 新浪 / 中债 / 中证指数 / 央行等公开接口）<br>
    本页为公开数据的结构化呈现，<b>不含任何投资建议</b>；指标方向不代表利好或利空。<br>
    数据可能存在延迟、缺口或口径差异，据此决策的风险由使用者自行承担。
  </div>

</div>
</body>
</html>
"""

_QUAD_TEMPLATE = ('<div class="q @@ON@@"><div class="qn">@@NAME@@</div>'
                  '<div class="qd">@@DEF@@</div></div>')

_VOTE_COL = """<div class="vote-col">
      <h4>@@TITLE@@（净 @@SCORE@@ · @@DIR@@）</h4>
      @@ROWS@@
    </div>"""

_VOTE_ROW = ('<div class="vote"><span class="m @@CLS@@">@@MARK@@</span>'
             '<span class="n">@@NAME@@</span><span class="v">@@VALUE@@</span></div>')

_CARD = """<div class="card">
        <div class="k">@@NAME@@</div>
        <div class="v">@@VALUE@@<span class="u">@@UNIT@@</span></div>
        <div class="row"><span>上期 @@PREV@@</span><span class="chg @@CLS@@">@@CHG@@</span></div>
        @@SPARK@@
        @@RANK@@
        @@FLAG@@
        <div class="stamp">@@STAMP@@</div>
      </div>"""

_CARD_MISS = """<div class="card miss">
        <div class="k">@@NAME@@</div>
        <div class="v na">数据缺失</div>
        <div class="flag">@@ERROR@@</div>
        <div class="stamp">@@STAMP@@</div>
      </div>"""

_CARD_SNAPSHOT = """<div class="card">
        <div class="k">@@NAME@@</div>
        <div class="v">@@VALUE@@<span class="u">@@UNIT@@</span></div>
        <div class="row"><span>单点快照 · 无历史序列</span></div>
        @@FLAG@@
        <div class="stamp">@@STAMP@@</div>
      </div>"""

_SECTION = """<div class="section">
    <div class="section-head">
      <h2>@@TITLE@@</h2>
      <p>@@SUB@@</p>
    </div>
    <div class="grid">@@CARDS@@</div>
  </div>"""

_GAP = ('<div class="gap"><b>@@TITLE@@</b><br>'
        '<span class="r">现状：</span>@@REASON@@<br>'
        '<span class="p">计划：@@PLAN@@</span></div>')

_GAPS_BLOCK = """<div class="note-block">
      <h3>数据源能力边界（本期未覆盖）</h3>
      <p>下列指标<b>在纯 AKShare 方案下拿不到及时数据</b>，因此本期不展示 ——
         宁可留白并说明原因，也不放一个看起来正常但已过期一年的数字。</p>
      @@GAPS@@
    </div>"""

_ERRORS_BLOCK = """<div class="note-block">
      <h3>本次构建中取数失败的指标</h3>
      <p>下列指标本次未能取到数据（单指标失败不会影响其它指标，也不会静默跳过）：</p>
      @@ROWS@@
    </div>"""


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _period_text(p: str) -> str:
    """期间字符串 → 可读文本。"""
    if not p:
        return "—"
    return p


def _spark_svg(item: dict) -> str:
    path = item.get("spark_path") or ""
    if not path:
        return ""
    color = {"up": "var(--up)", "down": "var(--down)"}.get(item.get("spark_dir"), "var(--faint)")
    n = len(item.get("spark") or [])
    return (f'<svg class="spark" viewBox="0 0 104 26" preserveAspectRatio="none" '
            f'aria-label="近 {n} 期走势">'
            f'<path class="base" d="M0,13 L104,13"/>'
            f'<path d="{path}" stroke="{color}"/></svg>')


def _rank_html(item: dict) -> str:
    r = item.get("rank")
    if r is None:
        return ""
    pct = max(0.0, min(1.0, float(r))) * 100.0
    # 🔴 观测太少时必须印样本量：`csi300_pe` 这类中证官方源一次只回传约 20 个
    #    交易日，20 点算出的分位与 6177 点算出的分位在页面上长得一模一样。
    n = item.get("n_obs") or 0
    label = f"本序列窗口内分位（仅 {n} 个观测）" if 0 < n < 60 else "本序列窗口内分位"
    return (f'<div class="rankbar"><div class="track"><i style="width:{pct:.0f}%"></i></div>'
            f'<div class="lab"><span>{label}</span><span>{item["rank_pct"]}</span></div>'
            f'</div>')


def _flag_html(item: dict) -> str:
    notes = []
    if item.get("stale"):
        notes.append(item["stale"])
    if item.get("note"):
        notes.append(item["note"])
    if not notes:
        return ""
    return f'<div class="flag">{_esc(" · ".join(notes))}</div>'


def _card(item: dict) -> str:
    stamp = f"数据日期 {_period_text(item['period'])} · {item['freq']}频 · {_esc(item['group'])}"
    if item["error"]:
        return (_CARD_MISS.replace("@@NAME@@", _esc(item["short"]))
                .replace("@@ERROR@@", _esc(item["error"][:120]))
                .replace("@@STAMP@@", stamp))
    if item["snapshot"]:
        return (_CARD_SNAPSHOT.replace("@@NAME@@", _esc(item["short"]))
                .replace("@@VALUE@@", _esc(item["latest_text"]))
                .replace("@@UNIT@@", _esc(item["unit"]))
                .replace("@@FLAG@@", _flag_html(item))
                .replace("@@STAMP@@", stamp))
    return (_CARD.replace("@@NAME@@", _esc(item["short"]))
            .replace("@@VALUE@@", _esc(item["latest_text"]))
            .replace("@@UNIT@@", _esc(item["unit"]))
            .replace("@@PREV@@", _esc(item["prev_period"] or "—"))
            .replace("@@CLS@@", item["chg_dir"])
            .replace("@@CHG@@", _esc(item["chg_text"]))
            .replace("@@SPARK@@", _spark_svg(item))
            .replace("@@RANK@@", _rank_html(item))
            .replace("@@FLAG@@", _flag_html(item))
            .replace("@@STAMP@@", stamp))


def _section(group: dict) -> str:
    cards = "".join(_card(it) for it in group["items"])
    return (_SECTION.replace("@@TITLE@@", _esc(group["title"]))
            .replace("@@SUB@@", _esc(group["sub"]))
            .replace("@@CARDS@@", cards))


def _quadrants(quadrant: str) -> str:
    """2×2 时钟：上一行「增长回升」、下一行「增长回落」；左列通胀回落、右列通胀回升。

    当前象限高亮。**方向分歧时不点亮任何一格**，改为整体降透明度 ——
    否则会出现「四格全灰」的观感，读者会以为页面坏了；降透明度能表达
    「这四格都还没有被选中」而不是「渲染失败」。
    """
    layout = [("复苏", "增长回升 · 通胀回落"), ("过热", "增长回升 · 通胀回升"),
              ("衰退", "增长回落 · 通胀回落"), ("滞胀", "增长回落 · 通胀回升")]
    names = {n for n, _ in layout}
    diverge = quadrant not in names
    out = []
    for name, d in layout:
        cls = "on" if name == quadrant else ("dim" if diverge else "")
        out.append(_QUAD_TEMPLATE.replace("@@ON@@", cls)
                   .replace("@@NAME@@", _esc(name))
                   .replace("@@DEF@@", _esc(d)))
    return "".join(out)


def _vote_col(title: str, votes: list[dict], score: int, direction: str) -> str:
    rows = []
    for v in votes:
        cls = {"↑": "up", "↓": "down"}.get(v["mark"], "flat")
        val = "—" if v.get("abstain") else f'{v["latest"]:,.2f}'
        rows.append(_VOTE_ROW.replace("@@CLS@@", cls)
                    .replace("@@MARK@@", _esc(v["mark"]))
                    .replace("@@NAME@@", _esc(v["name"]))
                    .replace("@@VALUE@@", _esc(val)))
    return (_VOTE_COL.replace("@@TITLE@@", _esc(title))
            .replace("@@SCORE@@", f"{score:+d}")
            .replace("@@DIR@@", _esc(direction))
            .replace("@@ROWS@@", "".join(rows)))


def render(payload: dict) -> str:
    c = payload["clock"]
    quad_cls = "diverge" if c["quadrant"] == "方向分歧" else ""

    note_html = ""
    if c["note"]:
        note_html = f'<div class="hero-note">{_esc(c["note"])}</div>'

    votes = (_vote_col("增长侧", c["growth_votes"], c["growth_score"], c["growth_dir"])
             + _vote_col("通胀侧", c["inflation_votes"], c["inflation_score"], c["inflation_dir"]))

    sections = "".join(_section(g) for g in payload["groups"])

    gaps = "".join(_GAP.replace("@@TITLE@@", _esc(g["title"]))
                   .replace("@@REASON@@", _esc(g["reason"]))
                   .replace("@@PLAN@@", _esc(g["plan"])) for g in payload["data_gaps"])
    gaps_block = _GAPS_BLOCK.replace("@@GAPS@@", gaps) if gaps else ""

    errors_block = ""
    if payload["errors"]:
        rows = "".join(f'<div class="gap"><b>{_esc(e["name"])}</b> '
                       f'<span class="r">{_esc(e["error"][:160])}</span></div>'
                       for e in payload["errors"])
        errors_block = _ERRORS_BLOCK.replace("@@ROWS@@", rows)

    cal_rows = "".join(
        f'<tr><td class="n">{_esc(x["name"])}</td><td>{_esc(x["freq"])}</td>'
        f'<td class="d">{_esc(x["period"] or "—")}</td>'
        f'<td>{_esc(x["source"])}</td>'
        f'<td>{_esc(x["caliber"])}{("<br>" + _esc(x["note"])) if x["note"] else ""}</td></tr>'
        for x in payload["calibers"])

    html = (_PAGE.replace("__CSS__", _CSS)
            .replace("@@HOME@@", "index.html")
            .replace("@@QUAD_CLS@@", quad_cls)
            .replace("@@QUAD@@", _esc(c["quadrant"]))
            .replace("@@QUAD_DEF@@", _esc(c["definition"] or "多指标方向互相抵消"))
            .replace("@@BASIS@@", _esc(c["basis"]))
            .replace("@@NOTE@@", note_html)
            .replace("@@QUADRANTS@@", _quadrants(c["quadrant"]))
            .replace("@@VOTES@@", votes)
            .replace("@@SECTIONS@@", sections)
            .replace("@@GAPS_BLOCK@@", gaps_block)
            .replace("@@ERRORS_BLOCK@@", errors_block)
            .replace("@@CALIBERS@@", cal_rows)
            .replace("@@N_CALIBERS@@", str(len(payload["calibers"])))
            .replace("@@GENERATED_AT@@", _esc(payload["generated_at"]))
            .replace("@@N_TOTAL@@", str(payload["stats"]["total"]))
            .replace("@@N_OK@@", str(payload["stats"]["ok"]))
            .replace("@@N_FAIL@@", str(payload["stats"]["failed"])))
    return html


def main() -> None:
    ap = argparse.ArgumentParser(description="构建宏观周期看板")
    ap.add_argument("--json", metavar="PATH", help="同时导出 payload JSON（排查用）")
    ap.add_argument("--from-json", metavar="PATH",
                    help="用已有 payload 直接渲染（**跳过拉数**，调样式/改文案时用）")
    ap.add_argument("--quiet", action="store_true", help="不逐条打印指标")
    args = ap.parse_args()

    if args.from_json:
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        print(f"[macro] 从 {args.from_json} 读取 payload（未拉数）")
    else:
        payload = build_payload(verbose=not args.quiet)

    html = render(payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")

    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    c = payload["clock"]
    print(f"[macro] 周期定位：{c['quadrant']}"
          + (f"（{c['definition']}）" if c["definition"] else ""))
    print(f"[macro] {c['basis']}")
    print(f"[macro] 指标 {payload['stats']['total']} 个：成功 {payload['stats']['ok']}、"
          f"失败 {payload['stats']['failed']}")
    for e in payload["errors"]:
        print(f"[macro] ✗ {e['key']}: {e['error'][:100]}")
    print(f"[macro] 已写出 {OUT}  ({len(html):,} 字符)")


if __name__ == "__main__":
    main()
