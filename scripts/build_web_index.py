# -*- coding: utf-8 -*-
"""生成手机网页版首页 `web/index.html`：**搜索框 → 已生成报告列表 → 横向对比按钮**。

页面结构（2026-09-17 按产品要求调整）
--------------------------------------
    ┌────────────────────────────────────┐
    │  ▸ 搜索框（页面最上面）                │  输入代码/名称：有报告直接打开，
    │                                    │  没报告就地生成（含进度条）
    │  ▸ 已生成的报告 · N 只                │  卡片含现价/涨跌幅/数据日期，
    │    [卡片] [卡片] …                   │  点击 → 该标的一页报告
    │                                    │
    │  ▸ [ 横向对比 · N 只  › ]            │  列表**最下面**，跳 watchlist.html
    └────────────────────────────────────┘

改动前是「顶部两个导航按钮 + 纯卡片列表」，搜索要另开一页（旧 `web/query.html`）。
现在搜索与对比入口都归到本页，`web/query.html` 已删除（功能完全被本页覆盖，
两处搜索入口 = 两份同功能 JS = 必然漂移）。

🔴 链接为什么一律用相对路径
--------------------------
同一个 `index.html` 有两种打开方式，必须都成立：

| 打开方式 | 卡片链接 `../reports/2026Q2/601088.html` 解析为 |
|---|---|
| 双击文件（`file://`） | `file:///…/fqf/reports/2026Q2/601088.html` ✅ |
| 报告服务（访问 `/`） | `/reports/2026Q2/601088.html` ✅ 由 server 的 `/reports` 静态挂载提供 |

写绝对路径 `/report/600519` 则第一种必失败（服务专用路由）。所以卡片与对比表
互链一律相对路径，服务端负责把 `/reports/**` 与 `/watchlist.html` 也映射出来。

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
    """一张报告卡片 —— 整块可点，跳该标的一页报告（相对路径，见模块 docstring）。"""
    name = meta.get("name", code)
    industry = meta.get("industry", "")
    lynch = meta.get("lynch", "")
    sub = " · ".join(x for x in (industry, lynch) if x)
    color = meta.get("color", "#868e96")
    period = path.split("/")[1] if "/" in path else ""

    if q and q["price"] is not None:
        chg, chg_color = _fmt_change(q["change_pct"])
        price_html = (
            f'<div class="px"><span class="p">{q["price"]:.2f}</span>'
            f'<span class="chg" style="color:{chg_color}">{chg}</span></div>'
        )
        bits = [period, f'{(q["date"] or "—")} {"盘中" if q["is_intraday"] else "收盘"}',
                f"已归档 {hist_n} 个交易日"]
    else:
        price_html = '<div class="px"><span class="p na">暂无行情</span></div>'
        bits = [period, "尚未拉取行情"]

    stamp = " · ".join(x for x in bits if x)
    return (
        f'      <a class="stock-card" href="../{path}">\n'
        f'        <div class="row1"><span class="name">{name}</span>'
        f'<span class="code">{code}</span></div>\n'
        f'        <div class="row2"><span class="dot" style="background:{color}"></span>'
        f'<span class="ind">{sub}</span></div>\n'
        f'        {price_html}\n'
        f'        <div class="stamp">{stamp}</div>\n'
        f'      </a>'
    )


def build_cards(newest_report: dict[str, str]) -> tuple[str, list[str]]:
    """生成卡片 HTML。顺序：跟踪池顺序优先，池外有报告的追加在后。

    ⚠️ 兜底卡片要**排除刻意移出的标的**（`wl.removed_codes()`）：那两个 for 循环
    处理的是两类完全不同的情况 —— 池内是正常态，池外是异常态。若不排除移出的，
    「移出跟踪池」在首页上就不生效（卡片仍在，名字退化成裸代码）。
    """
    pool = wl.stocks()
    pool_codes = [s["bare"] for s in pool]
    removed = wl.removed_codes()
    extra = [c for c in sorted(newest_report) if c not in pool_codes and c not in removed]

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


# --------------------------------------------------------------------------- #
# 页面模板
# --------------------------------------------------------------------------- #
#: 用占位符替换而不是 f-string 排版整个模板 —— 模板里有大量 CSS/JS 花括号，
#: 走 f-string 要逐个双写 `{{` `}}`，极易漏改且报错位置难找（本项目踩过同类坑）。
_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0f3d6e">
<title>投研排雷 · 一页报告</title>
<style>
:root {
  --ink:#1a2330; --muted:#5c6b7a; --faint:#8a97a6;
  --line:#dde3ea; --accent:#0f3d6e; --accent-2:#14508c; --bg-soft:#f5f7fa;
  --up:__UP__; --down:__DOWN__;
}
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
body {
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: var(--ink); background: #e9edf1; -webkit-font-smoothing: antialiased; min-height: 100vh;
}
.wrap { max-width: 640px; margin: 0 auto; padding: 18px 16px 44px; }

/* ---------- ① 搜索框：页面最上面 ---------- */
.eyebrow { font-size: 12px; color: var(--faint); letter-spacing: .04em; padding: 2px 2px 9px; }
.eyebrow b { color: var(--accent); font-weight: 700; font-size: 13px; letter-spacing: .1em; }
.searchbox { display: flex; gap: 8px; }
.searchbox input {
  flex: 1; min-width: 0; font-size: 16px; padding: 14px; color: var(--ink);
  background: #fff; border: 1px solid var(--line); border-radius: 12px; outline: none;
  box-shadow: 0 2px 12px rgba(15,61,110,0.07);
}
.searchbox input:focus { border-color: var(--accent-2); }
.searchbox button {
  flex: 0 0 auto; padding: 0 20px; font-size: 15px; font-weight: 600; color: #fff;
  background: var(--accent); border: 0; border-radius: 12px; cursor: pointer;
}
.searchbox button:active { background: var(--accent-2); }
.searchbox button:disabled { opacity: .5; }

.hint { font-size: 12px; color: var(--faint); margin-top: 9px; min-height: 16px; line-height: 1.6; }
.hint.warn { color: #a35b00; }

.cands { margin-top: 8px; background: #fff; border-radius: 12px; overflow: hidden;
  box-shadow: 0 2px 12px rgba(15,61,110,0.07); }
.cands:empty { display: none; }
.cand { display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 13px 14px; border-bottom: 1px solid var(--line); cursor: pointer; }
.cand:last-child { border-bottom: 0; }
.cand:active { background: var(--bg-soft); }
.c-main { min-width: 0; }
.c-name { font-size: 15px; font-weight: 600; }
.c-code { font-size: 12px; color: var(--faint); margin-left: 8px; font-variant-numeric: tabular-nums; }
.c-tag { flex: 0 0 auto; font-size: 11px; color: var(--muted); background: var(--bg-soft);
  border: 1px solid var(--line); border-radius: 4px; padding: 3px 7px; white-space: nowrap; }
.c-tag.ready { color: #0f6e56; border-color: #9FE1CB; background: #E1F5EE; }
.cands .empty { padding: 16px; font-size: 13px; color: var(--faint); text-align: center; }

.progress { margin-top: 12px; background: #fff; border-radius: 12px; padding: 16px 14px;
  box-shadow: 0 2px 12px rgba(15,61,110,0.07); }
.progress[hidden] { display: none; }
.bar { height: 6px; background: var(--bg-soft); border-radius: 3px; overflow: hidden; }
.bar i { display: block; height: 100%; width: 0; background: var(--accent-2);
  border-radius: 3px; transition: width .4s ease; }
.step { font-size: 13px; color: var(--muted); margin-top: 10px; line-height: 1.6; }
.step.err { color: #a32d2d; }
.step .retry { color: var(--accent-2); text-decoration: underline; cursor: pointer; }

.facts { margin-top: 12px; }
.pill { display: inline-block; margin: 0 6px 6px 0; font-size: 11px; color: var(--muted);
  background: var(--bg-soft); border: 1px solid var(--line); border-radius: 4px; padding: 3px 10px; }
.pill.warn { color: #a35b00; border-color: #f0cfa0; background: #fff6e8; }

/* ---------- ② 已生成的报告列表 ---------- */
.section { margin-top: 24px; }
.section-title { font-size: 13px; color: var(--muted); margin-bottom: 10px;
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.section-title b { color: var(--accent); font-size: 15px; }
.section-title .tip { font-size: 11px; color: var(--faint); }
.stock-list { display: flex; flex-direction: column; gap: 12px; }
.stock-card {
  display: block; text-decoration: none; color: inherit;
  background: #fff; border-radius: 10px; padding: 15px 18px;
  box-shadow: 0 2px 10px rgba(15,61,110,0.07);
  transition: transform .08s ease;
}
.stock-card:active { transform: scale(0.99); }
.stock-card .row1 { display: flex; justify-content: space-between; align-items: baseline; }
.stock-card .name { font-size: 17px; font-weight: 700; color: var(--ink); }
.stock-card .code { font-size: 12px; color: var(--faint); font-variant-numeric: tabular-nums; }
.stock-card .row2 { font-size: 12px; color: var(--muted); margin-top: 5px; }
.stock-card .row2 .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  margin-right: 5px; vertical-align: middle; }
.stock-card .px { display: flex; align-items: baseline; gap: 10px; margin-top: 9px; }
.stock-card .px .p { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stock-card .px .p.na { font-size: 13px; font-weight: 400; color: var(--faint); }
.stock-card .px .chg { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stock-card .stamp { font-size: 10px; color: var(--faint); margin-top: 4px; }
.stock-list .empty { text-align: center; color: var(--faint); padding: 40px 8px; font-size: 13px; }

/* ---------- ③ 横向对比：报告列表最下面 ---------- */
.compare {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-top: 14px; padding: 16px 18px; text-decoration: none; color: #fff;
  background: linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%);
  border-radius: 12px; box-shadow: 0 3px 14px rgba(15,61,110,0.22);
}
.compare:active { opacity: .88; }
.compare .c-title { font-size: 16px; font-weight: 700; letter-spacing: .5px; }
.compare .c-sub { font-size: 11px; opacity: .85; margin-top: 4px; line-height: 1.5; }
.compare .c-arrow { font-size: 24px; opacity: .85; line-height: 1; }

.foot { text-align: center; font-size: 11px; color: var(--faint); margin-top: 28px; line-height: 1.8; }
</style>
</head>
<body>
<div class="wrap">

  <!-- ① 搜索框：页面最上面 -->
  <div class="eyebrow"><b>投研排雷</b> · ValueLine 一页报告</div>
  <div class="searchbox">
    <input id="q" type="search" inputmode="search" enterkeyhint="search"
           placeholder="600519 / 茅台 / 00700 / 腾讯" autocomplete="off">
    <button id="go">生成</button>
  </div>
  <div id="hint" class="hint"></div>
  <div id="cands" class="cands"></div>
  <div id="progress" class="progress" hidden>
    <div class="bar"><i id="barfill"></i></div>
    <div id="steptext" class="step">准备中…</div>
  </div>
  <div class="facts">__FRESH__<span class="pill">已生成 __N_REPORTS__ 份报告</span></div>

  <!-- ② 已生成的报告（点击 → 该标的一页报告） -->
  <div class="section">
    <div class="section-title">
      <span>已生成的报告 <b>__N_REPORTS__</b> 只</span>
      <span class="tip">点击卡片进入报告</span>
    </div>
    <div class="stock-list">
__CARDS__
    </div>
  </div>

  <!-- ③ 横向对比：列表最下面 -->
  <a class="compare" href="watchlist.html">
    <div>
      <div class="c-title">横向对比</div>
      <div class="c-sub">__N_REPORTS__ 只标的 · 同口径决策指标并列比较，一眼看出谁便宜、谁安全、谁有雷</div>
    </div>
    <span class="c-arrow">›</span>
  </a>

  <div class="foot">
    数据来源：AKShare / 东方财富 / 腾讯行情 / 巨潮年报（官方 PDF 金标准交叉校验）<br>
    涨跌配色遵循中国习惯（红涨绿跌）· 本页仅供个人研究，不含任何操作建议。
  </div>
</div>

<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var searchTimer = null, pollTimer = null, busy = false, online = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function setHint(t, warn) {
    var el = $('hint');
    el.textContent = t || '';
    el.className = warn ? 'hint warn' : 'hint';
  }

  function showProgress(pct, text, isErr) {
    $('progress').hidden = false;
    $('barfill').style.width = (pct || 0) + '%';
    var el = $('steptext');
    el.className = 'step' + (isErr ? ' err' : '');
    el.innerHTML = text;
  }

  function hideProgress() { $('progress').hidden = true; }

  // ---------- 探测服务是否可用 ----------
  // 直接双击文件打开（file://）时，下面所有 /api/* 调用都会失败。
  // 与其让用户输入半天没反应，不如在加载时就说清「现在是离线打开」。
  fetch('/api/health', { cache: 'no-store' })
    .then(function (r) { online = !!r.ok; })
    .catch(function () { online = false; })
    .then(function () {
      if (!online) {
        setHint('当前是离线打开（双击文件）—— 搜索与生成需要报告服务；'
                + '下方报告列表仍可直接点开。启动服务：'
                + 'python -m uvicorn web.server:app --host 0.0.0.0 --port 8000', true);
        $('go').disabled = true;
      }
    });

  // ---------- 搜索 ----------
  function renderCands(list) {
    if (!list || !list.length) {
      $('cands').innerHTML = '<div class="empty">全市场索引里没有匹配的标的</div>';
      return;
    }
    $('cands').innerHTML = list.map(function (x) {
      var tag = x.has_report ? '已有报告，点击直达'
              : (x.has_data ? '可生成（约 3-4 分钟）' : '首次生成（约 3-5 分钟）');
      var cls = x.has_report || x.has_data ? 'c-tag ready' : 'c-tag';
      return '<div class="cand" data-code="' + esc(x.symbol) + '" data-ready="'
           +   (x.has_report ? '1' : '0') + '">'
           +   '<div class="c-main"><span class="c-name">' + esc(x.name) + '</span>'
           +   '<span class="c-code">' + esc(x.symbol) + ' · ' + esc(x.market) + '</span></div>'
           +   '<div class="' + cls + '">' + tag + '</div>'
           + '</div>';
    }).join('');
    Array.prototype.forEach.call(document.querySelectorAll('.cand'), function (el) {
      el.addEventListener('click', function () {
        // 已有报告 → 直接打开那一页；没有 → 就地生成（旧版无论有无都去生成，慢）
        if (el.dataset.ready === '1') { location.href = '/report/' + el.dataset.code; return; }
        generate(el.dataset.code);
      });
    });
  }

  function doSearch(q) {
    if (!q.trim()) { $('cands').innerHTML = ''; setHint(''); return; }
    if (!online) { setHint('离线打开，搜索不可用；请从下方报告列表直接点开', true); return; }
    setHint('搜索中…');
    fetch('/api/search?limit=12&q=' + encodeURIComponent(q))
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        setHint(d.count ? ('匹配 ' + d.count + ' 个标的 · 点「已有报告」的直达，其余点一下就地生成')
                        : '');
        renderCands(d.results);
      })
      .catch(function (e) { setHint('搜索失败：' + e.message, true); });
  }

  // ---------- 生成 ----------
  function generate(query) {
    if (busy || !query.trim()) return;
    busy = true;
    $('go').disabled = true;
    $('cands').innerHTML = '';
    showProgress(3, '正在提交任务…');

    fetch('/api/report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var d = res.d;
        if (!res.ok) throw new Error(d.detail || '提交失败');
        if (d.state === 'cached') {
          showProgress(100, '已有报告，正在打开…');
          setTimeout(function () { location.href = d.url; }, 200);
          return;
        }
        // 多命中（如「招商银行」= A股 600036 + 港股 03968）：不猜，让用户选
        if (d.state === 'ambiguous') {
          busy = false; $('go').disabled = false;
          hideProgress();
          setHint(d.message || '匹配到多个标的，请选择');
          renderCands(d.candidates || []);
          return;
        }
        setHint('正在生成：' + esc(d.name || '') + ' ' + esc(d.code || ''));
        poll(d.job_id);
      })
      .catch(function (e) {
        busy = false; $('go').disabled = false;
        showProgress(0, '失败：' + esc(e.message)
          + ' <span class="retry" onclick="location.reload()">重试</span>', true);
      });
  }

  function poll(jobId) {
    var start = Date.now();
    var tick = function () {
      fetch('/api/report/status/' + encodeURIComponent(jobId))
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.state === 'done') {
            showProgress(100, '报告已生成，正在打开…（已加入下方列表与横向对比）');
            setTimeout(function () { location.href = j.url || ('/report/' + j.code); }, 250);
            return;
          }
          if (j.state === 'error') {
            busy = false; $('go').disabled = false;
            showProgress(0, '生成失败：' + esc(j.step)
              + (j.error_detail ? '<br><span style="font-size:11px;color:#8a97a6">'
                  + esc(String(j.error_detail).slice(-300)) + '</span>' : ''), true);
            return;
          }
          var sec = Math.round((Date.now() - start) / 1000);
          showProgress(j.pct || 5, esc(j.step || '处理中…') + '　<span style="color:#8a97a6">'
            + sec + 's</span>');
          pollTimer = setTimeout(tick, 1500);
        })
        .catch(function () { pollTimer = setTimeout(tick, 2500); });
    };
    tick();
  }

  // ---------- 事件 ----------
  $('q').addEventListener('input', function (e) {
    clearTimeout(searchTimer);
    var v = e.target.value;
    searchTimer = setTimeout(function () { doSearch(v); }, 280);
  });
  $('q').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); generate($('q').value); }
  });
  $('go').addEventListener('click', function () { generate($('q').value); });
})();
</script>
</body>
</html>
"""


def main() -> None:
    newest_report = _scan_reports()
    cards_html, shown = build_cards(newest_report)
    fresh = _freshness(shown)

    n_pool = len(wl.codes())
    n_reports = len(shown)
    if not cards_html:
        cards_html = ('      <div class="empty">暂无报告，请先运行 '
                      '<code>python scripts/build_valueline.py</code> 生成</div>')

    if fresh["date"] and fresh["time"]:
        badge_cls = "pill warn" if fresh["stale"] else "pill"
        fresh_html = (f'<span class="{badge_cls}">数据更新于 {fresh["date"]} '
                      f'（{fresh["time"]} 写入）</span>')
    else:
        fresh_html = '<span class="pill warn">尚无行情数据</span>'

    html = (_TEMPLATE
            .replace("__UP__", UP_COLOR)
            .replace("__DOWN__", DOWN_COLOR)
            .replace("__FRESH__", fresh_html)
            .replace("__N_REPORTS__", str(n_reports))
            .replace("__CARDS__", cards_html))

    if not WEB_DIR.exists():
        WEB_DIR.mkdir(parents=True)
    out = WEB_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"[web] 首页已生成：{out.relative_to(ROOT)}"
          f"（跟踪池 {n_pool} 只 · 有报告 {n_reports} 只 · 数据日期 {fresh['date']}）")
    for code in shown:
        meta = wl.get(code) or {}
        q = _quote(code) or {}
        px = f"{q['price']:.2f}" if q.get("price") is not None else "—"
        print(f"  - {code} {meta.get('name', code)}: {px}  {newest_report[code]}")


if __name__ == "__main__":
    main()
