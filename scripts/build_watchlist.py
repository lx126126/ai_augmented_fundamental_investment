# -*- coding: utf-8 -*-
"""跟踪池横向对比表：把跟踪池全部标的关键决策指标拉到一张表上，一眼看出该重点盯谁。

设计理念（潇姐 2026-09-03 定，产品第一性原则）：
- 第一性检验：「这张表帮我做决策了吗」——是的，横向对比能立刻暴露「谁便宜、谁安全、谁有雷」。
- 完全复用 build_template_data 的既有输出，不引入新数据源、不重复算指标。
- 客观数据 + 第三方视角，无本人观点、无买卖建议。

2026-09-17 三处改动
-------------------
1. **清单收敛**：删掉本文件里的 `STOCK_META` 硬编码，改读 `src.data.watchlist_store`
   （唯一来源 = `watchlist/watchlist.json`）。此前三处清单漂移，实测对比表只列 6 只
   而实际有 11 份报告 —— 生成过的报告在对比表里"消失"。
2. **行缓存**：单只标的要跑一遍 `build_template_data`，实测 6 只 86 秒（约 14 秒/只），
   11 只就 2.6 分钟。缓存键 = 该标的 `data/raw/{code}/*.parquet` 的最大 mtime，
   只重算数据变动过的标的。日更后全部失效（2.6 分钟，一次性）；单独生成一份报告
   只重算 1 只（14 秒）。
3. **移动端**：原来 `.wrap { width: 1080px }` 固定宽，手机上横向被裁。
   现在容器自适应 + 表格独立横向滚动（首列冻结），1080 视口下渲染结果与原来一致，
   长图导出不受影响。

用法：
    python scripts/build_watchlist.py                  # 全跟踪池
    python scripts/build_watchlist.py --only 601088    # 只重算指定标的的行（整表照常重建）
    python scripts/build_watchlist.py --refresh        # 忽略缓存，全部重算

输出：web/watchlist.html
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import watchlist_store as wl  # noqa: E402
from src.data.adapter import build_template_data  # noqa: E402

WEB_DIR = ROOT / "web"
RAW_DIR = ROOT / "data" / "raw"
CACHE_PATH = ROOT / "data" / "cache" / "watchlist_rows.json"

RISK_LABEL = {"low": "低", "medium": "中", "high": "高"}


def _fmt(v, digits=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _pct(v, digits=0):
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
        return "#2f9e44"
    if pe_pct < 70:
        return "#e8590c"
    return "#c92a2a"


def _data_fingerprint(code: str) -> str:
    """该标的的行数据指纹 = raw parquet 最大 mtime **+ 池子里该标的的展示字段**。

    ⚠️ 后半截不能省。行数据里有 `industry` / `lynch` / `color` 三项，它们来自
    `watchlist.json`（见 `collect()` 里的 `meta.get(...)`），跟 raw parquet 毫无关系。
    2026-09-18 实测踩到：把全池 Lynch 对齐成报告口径（只改了 json，没动 raw）后重建
    对比表，11 只**全部命中缓存**、表里还是旧的「稳健增长型 · 收息」—— 改了等于没改。

    只取**该标的自己**的字段（不是整个 json 的 mtime），所以改一只不会让全池重算
    （全池重算约 2.6 分钟，代价不小）。
    """
    d = RAW_DIR / code
    mtime = max((f.stat().st_mtime for f in d.glob("*.parquet")), default=0.0) if d.is_dir() else 0.0
    s = wl.get(code, include_removed=True) or {}
    meta = json.dumps([s.get(k) for k in ("name", "industry", "lynch", "color")],
                      ensure_ascii=False)
    return f"{mtime:.6f}|{hashlib.md5(meta.encode('utf-8')).hexdigest()[:10]}"


# --------------------------------------------------------------------------- #
# 行数据（含缓存）
# --------------------------------------------------------------------------- #

def collect(code: str) -> dict | None:
    """抽取单只标的的决策关键指标；数据缺失返回 None。"""
    try:
        real = build_template_data(code)
    except Exception as e:
        print(f"[watchlist] 跳过 {code}: {e}")
        return None

    meta = wl.get(code) or {}
    val = real.get("valuation") or {}
    graham = real.get("graham") or {}
    fraud = real.get("fraud") or {}
    comp = real.get("competition") or {}
    narr = real.get("narrative_data") or {}
    latest = narr.get("latest") or {}

    # 现价日期：取自报告用的当日行情快照（与卡片首页同源，避免两处日期打架）
    quote_date = None
    try:
        import pandas as pd
        qf = RAW_DIR / code / "quote.parquet"
        if qf.exists():
            q = pd.read_parquet(qf)
            if not q.empty and pd.notna(q.iloc[-1].get("report_date")):
                quote_date = pd.Timestamp(q.iloc[-1]["report_date"]).strftime("%Y-%m-%d")
    except Exception:
        pass

    return {
        "code": code,
        "name": meta.get("name") or real.get("company_name") or code,
        "industry": meta.get("industry", ""),
        "lynch": meta.get("lynch", ""),
        "color": meta.get("color", "#868e96"),
        "quote_date": quote_date,
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
        "_fingerprint": _data_fingerprint(code),
        "_built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def collect_with_cache(codes: list[str], refresh: bool = False,
                       force: list[str] | None = None) -> list[dict]:
    """带缓存地收集行数据：只重算数据指纹变了的标的（`force` 里的强制重算）。"""
    cache = {} if refresh else _load_cache()
    force_set = set(force or [])
    rows, recomputed, reused, failed = [], [], [], []

    for code in codes:
        fp = _data_fingerprint(code)
        hit = cache.get(code)
        # 指纹是字符串（`mtime|meta_hash`），精确比较；旧的纯数字指纹自然不等 → 重算一次
        if hit and code not in force_set and hit.get("_fingerprint") == fp:
            rows.append(hit)
            reused.append(code)
            continue
        r = collect(code)
        if r is None:
            failed.append(code)
            continue
        cache[code] = r
        rows.append(r)
        recomputed.append(code)

    # 清掉已不在池里的标的（避免缓存文件无限增长）
    for dead in [c for c in cache if c not in codes]:
        cache.pop(dead, None)
    _save_cache(cache)

    print(f"[watchlist] 重算 {len(recomputed)} 只 {recomputed} · "
          f"命中缓存 {len(reused)} 只 {reused}"
          + (f" · 跳过无数据 {failed}" if failed else ""))
    return rows


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def _render_rows(rows: list[dict]) -> str:
    thead = (
        "<tr>"
        "<th class='sticky'>标的</th>"
        "<th>报告期</th>"
        "<th>现价</th>"
        "<th>PE(分位)</th>"
        "<th>PB(分位)</th>"
        "<th>股息率</th>"
        "<th>营业总收入/净利(亿)</th>"
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
        risk = RISK_LABEL.get(r["risk"], r["risk"] or "未知")
        risk_c = _risk_color(r["risk"])
        flags = r["flags"]
        flag_txt = f"<span class='flag'>{'、'.join(flags)}</span>" if flags else ""
        audit_txt = f"<div class='audit'>{r['audit']}</div>" if r.get("audit") else ""

        if r["profit_stable"] is None:
            stable_txt = "—"
        elif r["profit_stable"]:
            stable_txt = "<span style='color:#2f9e44'>稳定</span>"
        else:
            stable_txt = "<span style='color:#e8590c'>波动</span>"

        if r["industry_rank"] and r["peers_count"]:
            rank_txt = f"第{r['industry_rank']}/{r['peers_count']}"
        else:
            rank_txt = "—"

        pe_c = _pe_color(r["pe_pctile"])
        pe_cell = (
            f"<span style='color:{pe_c};font-weight:600'>{_fmt(r['pe'], 1)}</span>"
            f"<div class='sub'>{_pct(r['pe_pctile'])}</div>"
        )
        price_cell = (
            f"{_fmt(r['price'], 2)}"
            f"<div class='sub'>{r.get('quote_date') or '—'}</div>"
        )
        ind_txt = " · ".join(x for x in (r.get("industry", ""), r.get("lynch", "")) if x)

        body.append(
            "<tr>"
            f"<td class='sticky name'><span class='dot' style='background:{r.get('color', '#868e96')}'></span>"
            f"{r['name']}<div class='code'>{r['code']}</div><div class='ind'>{ind_txt}</div></td>"
            f"<td>{r['year']}</td>"
            f"<td>{price_cell}</td>"
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


def build_html(rows: list[dict], all_codes: list[str]) -> str:
    shown = [r["code"] for r in rows]
    missing = [c for c in all_codes if c not in shown]
    missing_note = ""
    if missing:
        names = "、".join(f"{wl.get(c).get('name', c) if wl.get(c) else c}（{c}）" for c in missing)
        missing_note = (
            f"<div class='missing'>⚠ 以下标的缺数据被跳过：{names}"
            "　→ 先跑 <code>python scripts/update_financials.py --codes "
            f"{','.join(missing)}</code> 补数据，再重跑本脚本</div>"
        )

    dates = [r.get("quote_date") for r in rows if r.get("quote_date")]
    newest = max(dates) if dates else "—"
    built = datetime.now().strftime("%Y-%m-%d %H:%M")
    n, cap = len(rows), wl.max_size()
    cap_note = (f"<span class='cap ok'>跟踪池 {n} 只</span>" if n <= cap
                else f"<span class='cap over'>跟踪池 {n} 只 · 已超名义上限 {cap} 只</span>")

    table = _render_rows(rows)
    legend = (
        "<div class='legend'>"
        "<span><i style='background:#2f9e44'></i>造假风险低</span>"
        "<span><i style='background:#e8590c'></i>造假风险中</span>"
        "<span><i style='background:#c92a2a'></i>造假风险高</span>"
        "<span class='sep'>|</span>"
        "<span>PE 分位：<b style='color:#2f9e44'>＜30% 便宜</b> · "
        "<b style='color:#e8590c'>30~70% 中性</b> · <b style='color:#c92a2a'>＞70% 贵</b></span>"
        "<span class='sep'>|</span>"
        "<span>👉 手机上左右滑动看完整指标</span>"
        "</div>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>跟踪池横向对比</title>
<style>
  :root {{
    --bg: #f5f6f8; --card: #ffffff; --line: #e9ecef;
    --text: #1a1d24; --sub: #8a97a6; --head-bg: #1f2733;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }}
  body {{ background: var(--bg); font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; color: var(--text); padding: 24px 0 48px; }}
  /* ⚠️ 宽度自适应而不是固定 1080：固定宽在手机上会被裁掉右半边。
     1080 视口下仍是 1080（长图导出结果不变），窄屏时容器收缩、由内层 .scroller 横滑。 */
  .wrap {{ width: 1080px; max-width: 100%; margin: 0 auto; background: var(--card); border-radius: 12px; overflow: hidden; box-shadow: 0 2px 16px rgba(0,0,0,.06); }}
  .head {{ padding: 28px 32px 20px; border-bottom: 1px solid var(--line); }}
  .head h1 {{ font-size: 22px; font-weight: 700; }}
  .head p {{ margin-top: 6px; color: var(--sub); font-size: 13px; }}
  .head .facts {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .cap {{ font-size: 12px; padding: 3px 10px; border-radius: 10px; border: 1px solid var(--line); color: var(--sub); background: #fafbfc; }}
  .cap.ok {{ color: #0f6e56; border-color: #9FE1CB; background: #E1F5EE; }}
  .cap.over {{ color: #a35b00; border-color: #f0cfa0; background: #fff6e8; }}
  .head .back {{ font-size: 12px; color: #14508c; text-decoration: none; }}
  .legend {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 12px 32px; background: #fafbfc; border-bottom: 1px solid var(--line); font-size: 12px; color: var(--sub); }}
  .legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .legend .sep {{ color: #d0d5db; }}
  .scroller {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  table {{ width: 100%; min-width: 1040px; border-collapse: collapse; font-size: 13px; }}
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
    <p>同口径决策关键指标一览 · 客观数据 + 第三方视角 · 非本人观点 · 非荐股</p>
    <div class="facts">
      {cap_note}
      <span class="cap">股价日期 {newest}</span>
      <span class="cap">页面生成 {built}</span>
      <a class="back" href="index.html">← 返回跟踪池首页</a>
    </div>
  </div>
  {legend}
  {missing_note}
  <div class="scroller">
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


def main() -> int:
    ap = argparse.ArgumentParser(description="跟踪池横向对比表")
    ap.add_argument("codes", nargs="*", help="只重算指定标的的行（整表照常重建）")
    ap.add_argument("--only", help="同上，逗号分隔，如 --only 601088,00700")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，全部重算")
    a = ap.parse_args()

    pool = wl.codes()
    if not pool:
        print("❌ 跟踪池为空（watchlist/watchlist.json 没有 stocks）")
        return 1

    only_raw = a.codes + ([x.strip() for x in a.only.split(",") if x.strip()] if a.only else [])
    only = [wl.bare(c) for c in only_raw]

    if only:
        # 只影响「哪些行被强制重算」，不改「表里有哪些行」——
        # 否则一条 `build_watchlist.py 601088` 就会把整张表刷成 1 行（旧版就是这样）。
        for c in only:
            if c not in pool:
                print(f"⚠ {c} 不在跟踪池里，忽略（先 wl.add 或手动加进 watchlist.json）")
        rows = collect_with_cache(pool, refresh=a.refresh, force=only)
    else:
        rows = collect_with_cache(pool, refresh=a.refresh)

    if not rows:
        print("❌ 无可用数据，请先补齐财报数据")
        return 1

    if not WEB_DIR.exists():
        WEB_DIR.mkdir(parents=True)
    html = build_html(rows, pool)
    out = WEB_DIR / "watchlist.html"
    out.write_text(html, encoding="utf-8")
    print(f"✅ 已生成: {out.relative_to(ROOT)}（{len(rows)} 只标的）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
