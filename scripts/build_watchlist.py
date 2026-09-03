# -*- coding: utf-8 -*-
"""跟踪池横向对比表：把跟踪池全部标的关键决策指标拉到一张表上，一眼看出该重点盯谁。

设计理念（潇姐 2026-09-03 定，产品第一性原则）：
- 第一性检验：「这张表帮我做决策了吗」——是的，横向对比能立刻暴露「谁便宜、谁安全、谁有雷」。
- 完全复用 build_template_data 的既有输出，不引入新数据源、不重复算指标。
- 客观数据 + 第三方视角，无本人观点、无买卖建议。

用法：
    python scripts/build_watchlist.py            # 生成 web/watchlist.html
    python scripts/build_watchlist.py 601088 00700  # 只对比指定标的

输出：web/watchlist.html（单页，纵向长图风格，宽 1080px，随标的数自适应高度）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.adapter import build_template_data  # noqa: E402

WEB_DIR = ROOT / "web"

# 跟踪池（6 只，与 build_web_index.py 的 STOCK_META 保持一致；新增标的加一行）
STOCK_META = {
    "601088": ("中国神华", "煤炭开采 · 周期型稳健收息", "#378ADD"),
    "600519": ("贵州茅台", "白酒 · 稳健增长型", "#E24B4A"),
    "000651": ("格力电器", "家电 · 稳健增长型收息", "#BA7517"),
    "601328": ("交通银行", "银行 · 稳健增长型收息", "#888780"),
    "00700": ("腾讯控股", "互联网 · 平台型", "#00A4FF"),
    "09992": ("泡泡玛特", "潮玩消费 · 快速增长型", "#F08BB0"),
}

RISK_LABEL = {"low": "低", "medium": "中", "high": "高"}


def _fmt(v, digits=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _pct(v, digits=0):
    """百分位/百分比格式化，None 返回 —。"""
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"


def _risk_color(level: str) -> str:
    return {"low": "#2f9e44", "medium": "#e8590c", "high": "#c92a2a"}.get(level, "#868e96")


def _pe_color(pe_pct):
    """PE 分位越低越便宜（绿），越高越贵（红）。中国语境：红=贵/警惕，绿=便宜/机会。"""
    if pe_pct is None:
        return "#868e96"
    if pe_pct < 30:
        return "#2f9e44"   # 便宜
    if pe_pct < 70:
        return "#e8590c"   # 中性
    return "#c92a2a"       # 贵


def collect(code: str) -> dict | None:
    """抽取单只标的的决策关键指标；数据缺失返回 None。"""
    try:
        real = build_template_data(code)
    except Exception as e:
        print(f"[watchlist] 跳过 {code}: {e}")
        return None

    name = STOCK_META.get(code, (real.get("company_name") or code, "", "#868e96"))[0]
    industry = STOCK_META.get(code, ("", "", ""))[1]

    val = real.get("valuation") or {}
    graham = real.get("graham") or {}
    fraud = real.get("fraud") or {}
    comp = real.get("competition") or {}
    narr = real.get("narrative_data") or {}
    latest = narr.get("latest") or {}

    return {
        "code": code,
        "name": name,
        "industry": industry,
        "year": narr.get("latest_year", "—"),
        "price": val.get("price_now"),
        "pe": val.get("pe"),
        "pe_pctile": val.get("pe_pctile"),
        "pb": val.get("pb"),
        "pb_pctile": val.get("pb_pctile"),
        "dividend_yield": val.get("dividend_yield"),
        "revenue": latest.get("revenue"),
        "net_profit": latest.get("net_profit"),
        "roe": latest.get("roe"),
        "debt_ratio": graham.get("debt_ratio"),
        "profit_stable": graham.get("profit_stable"),
        "net_cash": graham.get("net_cash"),
        "risk": fraud.get("overall_risk", ""),
        "flags": fraud.get("flags", []) or [],
        "audit": fraud.get("audit_opinion", ""),
        "industry_rank": comp.get("rank"),
        "peers_count": comp.get("peers_count"),
    }


def _render_rows(rows: list[dict]) -> str:
    """生成对比表 HTML 行。"""
    # 表头
    thead = (
        "<tr>"
        "<th class='sticky'>标的</th>"
        "<th>报告期</th>"
        "<th>现价</th>"
        "<th>PE(分位)</th>"
        "<th>PB(分位)</th>"
        "<th>股息率</th>"
        "<th>营收/净利(亿)</th>"
        "<th>ROE</th>"
        "<th>负债率</th>"
        "<th>净现金(亿)</th>"
        "<th>盈利稳定</th>"
        "<th>造假风险</th>"
        "<th>行业排名</th>"
        "</tr>"
    )

    body = []
    for r in rows:
        # 造假风险徽章
        risk = RISK_LABEL.get(r["risk"], r["risk"] or "未知")
        risk_c = _risk_color(r["risk"])
        flags = r["flags"]
        flag_txt = f"<span class='flag'>{'、'.join(flags)}</span>" if flags else ""
        audit_txt = f"<div class='audit'>{r['audit']}</div>" if r.get("audit") else ""

        # 盈利稳定
        if r["profit_stable"] is None:
            stable_txt = "—"
        elif r["profit_stable"]:
            stable_txt = "<span style='color:#2f9e44'>稳定</span>"
        else:
            stable_txt = "<span style='color:#e8590c'>波动</span>"

        # 行业排名
        if r["industry_rank"] and r["peers_count"]:
            rank_txt = f"第{r['industry_rank']}/{r['peers_count']}"
        else:
            rank_txt = "—"

        pe_c = _pe_color(r["pe_pctile"])
        pe_cell = (
            f"<span style='color:{pe_c};font-weight:600'>{_fmt(r['pe'], 1)}</span>"
            f"<div class='sub'>{_pct(r['pe_pctile'])}</div>"
        )

        body.append(
            "<tr>"
            f"<td class='sticky name'><span class='dot' style='background:{STOCK_META.get(r['code'], ('', '', '#868e96'))[2]}'></span>"
            f"{r['name']}<div class='code'>{r['code']}</div><div class='ind'>{r['industry']}</div></td>"
            f"<td>{r['year']}</td>"
            f"<td>{_fmt(r['price'], 2)}</td>"
            f"<td>{pe_cell}</td>"
            f"<td>{_fmt(r['pb'], 2)}<div class='sub'>{_pct(r['pb_pctile'])}</div></td>"
            f"<td>{_pct(r['dividend_yield'], 1)}</td>"
            f"<td class='num'>{_fmt(r['revenue'], 0)} / {_fmt(r['net_profit'], 0)}</td>"
            f"<td class='num'>{_pct(r['roe'], 1)}</td>"
            f"<td class='num'>{_pct(r['debt_ratio'], 0)}</td>"
            f"<td class='num'>{_fmt(r['net_cash'], 0)}</td>"
            f"<td>{stable_txt}</td>"
            f"<td><span class='risk' style='background:{risk_c}'>{risk}</span>{flag_txt}{audit_txt}</td>"
            f"<td class='num'>{rank_txt}</td>"
            "</tr>"
        )
    return thead + "\n".join(body)


def build_html(rows: list[dict], codes: list[str]) -> str:
    """组装完整 HTML 页面。"""
    # 仅显示成功抽取的行
    shown_codes = [r["code"] for r in rows]
    missing = [c for c in codes if c not in shown_codes]
    missing_note = ""
    if missing:
        missing_note = (
            f"<div class='missing'>⚠ 以下标的缺数据被跳过（请先运行 fetch_stock）：{'、'.join(missing)}</div>"
        )

    table = _render_rows(rows)
    legend = (
        "<div class='legend'>"
        "<span><i style='background:#2f9e44'></i>造假风险低</span>"
        "<span><i style='background:#e8590c'></i>造假风险中</span>"
        "<span><i style='background:#c92a2a'></i>造假风险高</span>"
        "<span class='sep'>|</span>"
        "<span>PE 分位：<b style='color:#2f9e44'>＜30% 便宜</b> · "
        "<b style='color:#e8590c'>30~70% 中性</b> · <b style='color:#c92a2a'>＞70% 贵</b></span>"
        "</div>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1080">
<title>跟踪池横向对比</title>
<style>
  :root {{
    --bg: #f5f6f8; --card: #ffffff; --line: #e9ecef;
    --text: #1a1d24; --sub: #8a97a6; --head-bg: #1f2733;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; color: var(--text); padding: 24px 0 48px; }}
  .wrap {{ width: 1080px; margin: 0 auto; background: var(--card); border-radius: 12px; overflow: hidden; box-shadow: 0 2px 16px rgba(0,0,0,.06); }}
  .head {{ padding: 28px 32px 20px; border-bottom: 1px solid var(--line); }}
  .head h1 {{ font-size: 22px; font-weight: 700; }}
  .head p {{ margin-top: 6px; color: var(--sub); font-size: 13px; }}
  .legend {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 12px 32px; background: #fafbfc; border-bottom: 1px solid var(--line); font-size: 12px; color: var(--sub); }}
  .legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .legend .sep {{ color: #d0d5db; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: var(--head-bg); color: #fff; font-weight: 600; padding: 12px 10px; text-align: center; white-space: nowrap; position: sticky; top: 0; z-index: 2; }}
  td {{ padding: 14px 10px; text-align: center; border-bottom: 1px solid var(--line); vertical-align: middle; }}
  tr:hover td {{ background: #f8fafc; }}
  td.sticky, th.sticky {{ position: sticky; left: 0; background: var(--card); z-index: 1; text-align: left; }}
  th.sticky {{ background: var(--head-bg); z-index: 3; }}
  tr:hover td.sticky {{ background: #f8fafc; }}
  .name {{ font-weight: 600; min-width: 150px; }}
  .name .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }}
  .code {{ color: var(--sub); font-weight: 400; font-size: 12px; }}
  .ind {{ color: var(--sub); font-weight: 400; font-size: 11px; margin-top: 2px; }}
  .sub {{ color: var(--sub); font-size: 11px; }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .risk {{ color: #fff; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; }}
  .flag {{ display: block; margin-top: 4px; color: #c92a2a; font-size: 11px; }}
  .audit {{ color: var(--sub); font-size: 11px; margin-top: 2px; }}
  .missing {{ padding: 14px 32px; color: #e8590c; font-size: 13px; background: #fff4e6; }}
  .foot {{ padding: 16px 32px; color: var(--sub); font-size: 11px; line-height: 1.6; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>跟踪池横向对比</h1>
    <p>同口径决策关键指标一览 · 客观数据 + 第三方视角 · 非本人观点 · 非荐股 · 生成于数据最新状态</p>
  </div>
  {legend}
  {missing_note}
  <div style="overflow-x:auto;">
    <table>{table}</table>
  </div>
  <div class="foot">
    指标口径：PE/PB 及分位为近10年分位（港股为港元市值 ÷ 人民币净利的混合口径，彭博/Wind 惯例）；
    造假风险 = Beneish M-Score + 现金流背离 + 应收背离 + 审计意见综合评级（非标一票否决）；
    净现金 = 货币资金 − 有息负债；盈利稳定 = 近5年归母净利连续为正。
    <br>本表仅作横向比较决策辅助，不构成任何买卖建议。
  </div>
</div>
</body>
</html>"""


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    # 指定标的 → 用指定；否则全跟踪池
    codes = args if args else list(STOCK_META.keys())

    rows = []
    for code in codes:
        r = collect(code)
        if r:
            rows.append(r)

    if not rows:
        print("无可用数据，请先运行 fetch_stock 拉取数据")
        return

    WEB_DIR.mkdir(parents=True, exist_ok=True)
    html = build_html(rows, codes)
    out = WEB_DIR / "watchlist.html"
    out.write_text(html, encoding="utf-8")
    print(f"已生成: {out}（{len(rows)} 只标的）")


if __name__ == "__main__":
    main()
