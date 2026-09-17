# -*- coding: utf-8 -*-
"""生成手机网页版首页 `web/index.html`（跟踪池卡片列表）。

设计要点
--------
1. **清单只有一处来源**：跟踪池读 `src.data.watchlist_store`（= `watchlist/watchlist.json`）。
   以前这个脚本自带一份 `STOCK_META` 硬编码，与 json 和 build_watchlist 三处漂移 ——
   实测出现过「有报告但首页不显示」（腾讯 00700 就在报告里却不在卡片里）。
2. **卡片带现价与涨跌幅** 🔴 这是「感知不到数据更新」的直接修复。
   旧版卡片只有公司名 + 行业，**没有任何数字** —— 日更跑完后整页逐字节一样，
   mtime 变了而已，肉眼当然看不出来。现在卡片显示现价、涨跌幅（红涨绿跌）、数据日期。
3. **顶部显示数据更新时间**，并在超过 1 个交易日未更新时标黄，把"没更新"变成可见状态。

用法：
    python scripts/build_web_index.py
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import watchlist_store as wl  # noqa: E402

REPORTS_DIR = ROOT / "reports"
WEB_DIR = ROOT / "web"
RAW_DIR = ROOT / "data" / "raw"
MARKET_DIR = ROOT / "data" / "market"

#: 中国语境配色：涨=红、跌=绿（与欧美相反）
UP_COLOR = "#c92a2a"
DOWN_COLOR = "#2f9e44"
FLAT_COLOR = "#5c6b7a"

#: 超过这么多个自然日没更新，就把首页的「数据更新于」标黄提醒
_STALE_DAYS = 4


def _period_key(p: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})Q([1-4])", p)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _scan_reports() -> dict[str, str]:
    """扫描 reports/，返回 {code: 最新报告期相对路径}。"""
    result: dict[str, str] = {}
    if not REPORTS_DIR.exists():
        return result
    for period_dir in sorted(REPORTS_DIR.iterdir(), key=lambda d: _period_key(d.name), reverse=True):
        if not period_dir.is_dir() or period_dir.name == "xhs":
            continue
        for html in period_dir.glob("*.html"):
            result.setdefault(html.stem, html.relative_to(ROOT).as_posix())
    return result


def _quote(code: str) -> dict | None:
    """读报告用的当日行情快照 `data/raw/{code}/quote.parquet`（单行）。"""
    p = RAW_DIR / code / "quote.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty:
        return None
    r = df.iloc[-1]
    d = r.get("report_date")
    return {
        "price": float(r["price"]) if pd.notna(r.get("price")) else None,
        "change_pct": float(r["change_pct"]) if pd.notna(r.get("change_pct")) else None,
        "date": pd.Timestamp(d).strftime("%Y-%m-%d") if pd.notna(d) else None,
        "is_intraday": bool(r.get("is_intraday")) if pd.notna(r.get("is_intraday")) else False,
    }


def _quote_history_dates(code: str) -> list[str]:
    """`data/market/{code}_quote.parquet` 里已归档的日期（按日追加的历史）。"""
    p = MARKET_DIR / f"{code}_quote.parquet"
    if not p.exists():
        return []
    try:
        df = pd.read_parquet(p, columns=["report_date"])
    except Exception:
        return []
    return sorted(pd.to_datetime(df["report_date"]).dt.strftime("%Y-%m-%d").unique().tolist())


def _fmt_change(pct: float | None) -> tuple[str, str]:
    if pct is None:
        return "—", FLAT_COLOR
    if pct > 0:
        return f"+{pct:.2f}%", UP_COLOR
    if pct < 0:
        return f"{pct:.2f}%", DOWN_COLOR
    return "0.00%", FLAT_COLOR


def _card(code: str, meta: dict, path: str, q: dict | None, hist_n: int) -> str:
    name = meta.get("name", code)
    industry = meta.get("industry", "")
    lynch = meta.get("lynch", "")
    sub = " · ".join(x for x in (industry, lynch) if x)
    color = meta.get("color", "#868e96")

    if q and q["price"] is not None:
        chg, chg_color = _fmt_change(q["change_pct"])
        price_html = (
            f'<div class="px"><span class="p">{q["price"]:.2f}</span>'
            f'<span class="chg" style="color:{chg_color}">{chg}</span></div>'
        )
        date_txt = q["date"] or "—"
        src = "盘中" if q["is_intraday"] else "收盘"
        stamp = f'{date_txt} {src} · 已归档 {hist_n} 个交易日'
    else:
        price_html = '<div class="px"><span class="p na">暂无行情</span></div>'
        stamp = "尚未拉取行情"

    return (
        f'    <a class="stock-card" href="../{path}">\n'
        f'      <div class="row1"><span class="name">{name}</span>'
        f'<span class="code">{code}</span></div>\n'
        f'      <div class="row2"><span class="dot" style="background:{color}"></span>'
        f'<span class="ind">{sub}</span></div>\n'
        f'      {price_html}\n'
        f'      <div class="stamp">{stamp}</div>\n'
        f'    </a>'
    )


def build_cards(newest_report: dict[str, str]) -> tuple[str, list[str]]:
    """生成卡片 HTML。顺序：跟踪池顺序优先，池外有报告的追加在后。"""
    pool = wl.stocks()
    pool_codes = [s["bare"] for s in pool]
    extra = [c for c in sorted(newest_report) if c not in pool_codes]

    cards, shown = [], []
    for s in pool:
        code = s["bare"]
        if code not in newest_report:
            continue
        cards.append(_card(code, s, newest_report[code], _quote(code),
                           len(_quote_history_dates(code))))
        shown.append(code)

    # 兜底：有报告但不在池里（正常流程下自动入池后不会出现；出现即说明入池链路断了）
    for code in extra:
        meta = wl.get(code) or {"name": code, "industry": "未入池", "lynch": "", "color": "#868e96"}
        cards.append(_card(code, meta, newest_report[code], _quote(code),
                           len(_quote_history_dates(code))))
        shown.append(code)
    return "\n".join(cards), shown


def _freshness(shown: list[str]) -> dict:
    """汇总「数据更新于」：取所有标的行情日期的最大值 + 数据文件最新 mtime。"""
    dates, mtimes = [], []
    for code in shown:
        q = _quote(code)
        if q and q["date"]:
            dates.append(q["date"])
        d = RAW_DIR / code
        if d.is_dir():
            mtimes.extend(f.stat().st_mtime for f in d.glob("quote.parquet"))
    newest = max(dates) if dates else None
    mtime = max(mtimes) if mtimes else None
    stale = False
    if newest:
        try:
            delta = (date.today() - datetime.strptime(newest, "%Y-%m-%d").date()).days
            stale = delta > _STALE_DAYS
        except Exception:
            pass
    return {
        "date": newest,
        "time": datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M") if mtime else None,
        "stale": stale,
    }


def main() -> None:
    newest_report = _scan_reports()
    cards_html, shown = build_cards(newest_report)
    fresh = _freshness(shown)

    n_pool = len(wl.codes())
    if not cards_html:
        cards_html = ('    <div class="empty">暂无报告，请先运行 '
                      '<code>python scripts/build_valueline.py</code> 生成</div>')

    if fresh["date"] and fresh["time"]:
        badge_cls = "pill warn" if fresh["stale"] else "pill"
        fresh_html = (f'<span class="{badge_cls}">数据更新于 {fresh["date"]} '
                      f'（{fresh["time"]} 写入）</span>')
    else:
        fresh_html = '<span class="pill warn">尚无行情数据</span>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0f3d6e">
<title>投研排雷 · 跟踪池</title>
<style>
:root {{
  --ink: #1a2330; --muted: #5c6b7a; --faint: #8a97a6;
  --line: #dde3ea; --accent: #0f3d6e; --accent-2: #14508c; --bg-soft: #f5f7fa;
  --up: {UP_COLOR}; --down: {DOWN_COLOR};
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}
body {{
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: var(--ink); background: #e9edf1; -webkit-font-smoothing: antialiased; min-height: 100vh;
}}
.wrap {{ max-width: 640px; margin: 0 auto; padding: 28px 16px 48px; }}
.top {{ padding: 8px 0 18px; }}
.top h1 {{ font-size: 24px; font-weight: 700; color: var(--accent); letter-spacing: 1px; }}
.top p {{ font-size: 13px; color: var(--muted); margin-top: 8px; line-height: 1.7; }}
.pill {{ display: inline-block; margin: 10px 6px 0 0; font-size: 11px; color: var(--muted);
  background: var(--bg-soft); border: 1px solid var(--line); border-radius: 4px; padding: 3px 10px; }}
.pill.warn {{ color: #a35b00; border-color: #f0cfa0; background: #fff6e8; }}

.nav {{ display: flex; gap: 8px; margin: 14px 0 18px; }}
.nav a {{ flex: 1; text-align: center; text-decoration: none; font-size: 13px; font-weight: 600;
  color: #fff; background: var(--accent); border-radius: 10px; padding: 11px 8px; }}
.nav a.ghost {{ color: var(--accent); background: #fff; border: 1px solid var(--line); }}
.nav a:active {{ opacity: .85; }}

.stock-list {{ display: flex; flex-direction: column; gap: 12px; }}
.stock-card {{
  display: block; text-decoration: none; color: inherit;
  background: #fff; border-radius: 10px; padding: 15px 18px;
  box-shadow: 0 2px 10px rgba(15,61,110,0.07);
  transition: transform .08s ease;
}}
.stock-card:active {{ transform: scale(0.99); }}
.stock-card .row1 {{ display: flex; justify-content: space-between; align-items: baseline; }}
.stock-card .name {{ font-size: 17px; font-weight: 700; color: var(--ink); }}
.stock-card .code {{ font-size: 12px; color: var(--faint); font-variant-numeric: tabular-nums; }}
.stock-card .row2 {{ font-size: 12px; color: var(--muted); margin-top: 5px; }}
.stock-card .row2 .dot {{ display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  margin-right: 5px; vertical-align: middle; }}
.stock-card .px {{ display: flex; align-items: baseline; gap: 10px; margin-top: 9px; }}
.stock-card .px .p {{ font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }}
.stock-card .px .p.na {{ font-size: 13px; font-weight: 400; color: var(--faint); }}
.stock-card .px .chg {{ font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }}
.stock-card .stamp {{ font-size: 10px; color: var(--faint); margin-top: 4px; }}
.empty {{ text-align: center; color: var(--faint); padding: 40px 8px; font-size: 13px; }}
.foot {{ text-align: center; font-size: 11px; color: var(--faint); margin-top: 30px; line-height: 1.8; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <h1>投研排雷</h1>
    <p>纯客观数据工具 · ValueLine 一页报告<br>数据加工 + 指标 + 风险检测，帮自己做投资决策前先排雷。</p>
    {fresh_html}
    <span class="pill">跟踪池 {n_pool} 只</span>
  </div>

  <div class="nav">
    <a href="watchlist.html">跟踪池横向对比</a>
    <a class="ghost" href="/">搜索生成报告</a>
  </div>

  <div class="stock-list">
{cards_html}
  </div>

  <div class="foot">
    数据来源：AKShare / 东方财富 / 腾讯行情 / 巨潮年报（官方 PDF 金标准交叉校验）<br>
    涨跌配色遵循中国习惯（红涨绿跌）· 本页仅供个人研究，不含任何操作建议。
  </div>
</div>
</body>
</html>
"""
    if not WEB_DIR.exists():
        WEB_DIR.mkdir(parents=True)
    out = WEB_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"[web] 首页已生成：{out.relative_to(ROOT)}"
          f"（跟踪池 {n_pool} 只 · 有报告 {len(shown)} 只 · 数据日期 {fresh['date']}）")
    for code in shown:
        meta = wl.get(code) or {}
        q = _quote(code) or {}
        px = f"{q['price']:.2f}" if q.get("price") is not None else "—"
        print(f"  - {code} {meta.get('name', code)}: {px}  {newest_report[code]}")


if __name__ == "__main__":
    main()
