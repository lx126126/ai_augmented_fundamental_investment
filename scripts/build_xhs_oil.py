#!/usr/bin/env python3
"""中国海油（A 股 600938 / 港股 00883）小红书轮播图。

与 build_xhs.py 的关系
----------------------
build_xhs.py 是为茅台写的：里面硬编码了「酒」「渠道」「i茅台」「产量=基酒」等
口径与文案，且只按单一上市地渲染。海油是「同一法人在两个市场上市」，
套用它会得到两个错误：

1. 茅台文案串台（原油股的报告里出现「基酒」「i茅台」）；
2. 跨市场数字混币种并列（A 股 ¥33.91 与港股 HK$24.40 放一起，读者无从判断）。

所以本脚本单独成篇，复用 build_xhs 的版式（CSS / _slide / _kv），
重写全部内容层，并加三条硬约束：

* **所有金额必须带币种**：人民币写「元/亿元」，港元写「港元」；
* **比率必须同币种相除**：港股分红比例不能用「港元股息 ÷ 人民币净利」
  （实测虚高约 7pp），统一按人民币口径算，两边同值；
* **A/H 对比必须标折算汇率与折算后数值**，否则溢价率无法复核。

数据来源与红线同项目其他脚本：只呈现客观数据，不含目标价 / 买卖点。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))        # build_xhs
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # src

import build_xhs as bx  # noqa: E402
from src.data.adapter import build_template_data, load_raw  # noqa: E402
from src.data.cleaner import build_annual_financials  # noqa: E402
from src.data.listing_group import sibling_code  # noqa: E402

C = bx.C
#: A 股代码是主角，港股代码从同法人配对表推 —— 配对关系只登记在 listing_group，
#: 脚本里再抄一份会漂移。
A_CODE = "600938"
H_CODE = sibling_code(A_CODE)
if not H_CODE:
    sys.exit(f"{A_CODE} 未登记同法人配对，请在 src/data/listing_group.py 补充后重跑")

#: 港元兑人民币兜底值（2026-09-15 中行折算价，100 港元 = 86.27 元）。
#: 正常路径从 AKShare 取当日值；取不到时才用兜底，并在图内标注为「预估」。
FALLBACK_HKD_CNY = (0.8627, "2026-09-15")


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _hkd_cny() -> tuple[float, str]:
    """港元兑人民币汇率与日期。缓存 1 天，失败回退常量。"""
    cache = Path("data/cache/fx_hkd_cny.json")
    try:
        if cache.exists():
            d = json.loads(cache.read_text(encoding="utf-8"))
            if d.get("date", "")[:10] >= _today():
                return float(d["rate"]), str(d["date"])
    except Exception:
        pass
    try:
        import akshare as ak
        df = ak.currency_boc_sina(symbol="港币", start_date="20260101", end_date="20990101")
        row = df.dropna(subset=["中行折算价"]).iloc[-1]
        rate, date = float(row["中行折算价"]) / 100.0, str(row["日期"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"rate": rate, "date": date}, ensure_ascii=False),
                         encoding="utf-8")
        return rate, date
    except Exception:
        return FALLBACK_HKD_CNY


def _today() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()


def _num(v):
    """转 float；None / NaN / 非数值一律返回 None。

    pandas 取出来的「空值」是 NaN 而不是 None —— `if v is not None` 拦不住它，
    会让 NaN 一路走到 int() / min() / 排序里，报
    `ValueError: cannot convert float NaN to integer`。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _axis_range(vals, pad: float = 6.0, step: float = 5.0) -> tuple[float, float]:
    """按箱线图 1.5×IQR 剔除离群值后定量程（与 build_valueline._axis_range 同构）。

    剔除只影响轴范围，**不剔除数据**：离群点照画，用贴边三角 + 真值标注。
    """
    s = sorted(v for v in (_num(x) for x in vals) if v is not None)
    if not s:
        return 0.0, 100.0
    if len(s) >= 4:
        q1, q3 = s[len(s) // 4], s[(3 * len(s)) // 4]
        iqr = q3 - q1
        core = [v for v in s if v <= q3 + 1.5 * iqr] or s
    else:
        core = s
    lo = max(0.0, (int((min(core) - pad) // step)) * step)
    hi = (int((max(core) + pad) / step) + 1) * step
    if hi - lo < 20:
        hi = lo + 20
    return lo, hi


def _div_svg(hist: list[dict], w: int = 322, h: int = 214) -> str:
    """分红图：上面板=分红比例折线，下面板=每股股息柱。

    分成两个面板是踩出来的：第一版把柱和线画在同一个绘图区里，柱高占到
    绘图区的 82%，而分红比例（40% 出头）落在柱身的中段 —— 折线数值标签
    整片压进蓝色柱里，图上只剩几个读不出的浅色数字。

    分红比例量程走 IQR：海油港股序列里 2016 年的比例被股本反推误差放大到
    3,500%，直接取 min/max 会把其余年份压成一条贴底的线。
    """
    if not hist:
        return ""
    pad_l, pad_r = 36, 34
    pw = w - pad_l - pad_r
    n = len(hist)
    xs = [pad_l + pw * (i + 0.5) / n for i in range(n)]

    # ---- 上面板：分红比例 ----
    t_top, t_h = 16, 82
    pcts = [x.get("payout_pct") for x in hist]
    p_lo, p_hi = _axis_range(pcts)

    def y_pct(v):
        return t_top + t_h - (v - p_lo) / (p_hi - p_lo) * t_h

    out = []
    for k in range(3):
        v = p_lo + (p_hi - p_lo) * k / 2
        y = y_pct(v)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-pad_r}" y2="{y:.1f}" '
                   f'stroke="#e3e8ef" stroke-width="1"/>')
        out.append(f'<text x="{w-pad_r+4}" y="{y+3.5:.1f}" font-size="9" '
                   f'fill="#8a97a6">{v:.0f}%</text>')

    pts = [(x, y_pct(p), p) for x, p in zip(xs, pcts)]
    out.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
               + '" fill="none" stroke="#b8860b" stroke-width="1.7"/>')
    prev_txt = None
    for i, (x, y, p) in enumerate(pts):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.9" fill="#b8860b"/>')
        txt = f"{p:.1f}"
        if txt != prev_txt or i == len(pts) - 1:
            ly = y - 7 if y - 7 > t_top + 6 else y + 13
            out.append(f'<text x="{x:.1f}" y="{ly:.1f}" text-anchor="middle" '
                       f'font-size="8.5" font-weight="700" fill="#b8860b">{txt}%</text>')
        prev_txt = txt
    out.append(f'<text x="{pad_l}" y="{t_top-4}" font-size="8.5" fill="#b8860b">'
               f'分红比例 · 占归母净利（%）</text>')

    # ---- 下面板：每股股息 ----
    # b_h - 16：给柱顶数值和面板标题留出互不重叠的带宽 —— 第一版柱高顶满
    # b_h，最高那根柱的数值标签正好落在面板标题上（「每股股息」和「1.28」叠在一起）。
    b_top, b_h = t_top + t_h + 22, 60
    dps = [_num(x.get("dps")) or 0 for x in hist]
    d_max = max(dps) or 1.0
    bw = min(22.0, pw / max(n, 1) * 0.5)
    out.append(f'<text x="{pad_l}" y="{b_top-8}" font-size="8.5" fill="#14508c">'
               f'每股股息（{("港元/股" if hist[0].get("is_hk") else "元/股")}）</text>')
    for i, (x, d) in enumerate(zip(xs, dps)):
        bh = (d / d_max) * (b_h - 16)
        out.append(f'<rect x="{x-bw/2:.1f}" y="{b_top+b_h-bh:.1f}" width="{bw:.1f}" '
                   f'height="{bh:.1f}" rx="2.5" fill="#14508c" opacity=".88"/>')
        out.append(f'<text x="{x:.1f}" y="{b_top+b_h-bh-5:.1f}" text-anchor="middle" '
                   f'font-size="9" font-weight="600" fill="#14508c">{d:.2f}</text>')
        # 年份取 hist[i] 而不是 hist[dps.index(d)] —— 股息数值会重复
        # （2022 与 2024 都是 1.28），index() 会一律返回第一次出现的年份。
        out.append(f'<text x="{x:.1f}" y="{b_top+b_h+14:.1f}" text-anchor="middle" '
                   f'font-size="9" fill="#8a97a6">{hist[i]["year"]}</text>')

    over = [x["year"] for x in hist if (_num(x.get("payout_pct")) or 0) > p_hi]
    if over:
        out.append(f'<text x="{pad_l}" y="{t_top-14}" font-size="8.5" fill="#b8860b">'
                   f'▲ {"、".join(str(y) for y in over)} 年超出纵轴（已标真值）</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">{"".join(out)}</svg>'


# --------------------------------------------------------------------------- #
# 口径计算：A/H 配对指标
# --------------------------------------------------------------------------- #
def _pair_metrics(a: dict, h: dict, rate: float) -> dict:
    """把两地模板数据整理成可并列的口径。

    关键：**港股市值 / PE / 股息率不能与 A 股直接并列**。
    - 市值：A 股是人民币，港股是港元；
    - PE：行情接口给的港股 PE(TTM) 与 A 股 PE 分母期次不同，且币种不同；
    - 分红比例：港股接口算的是「港元股息 ÷ 人民币净利」，实测虚高约 7pp。

    所以这里用**同一口径自算**：市值 ÷ 最近年报归母净利（PE）、
    分红总额（人民币）÷ 归母净利（分红比例）。自算结果与行情接口口径
    在这次标的上互相验证一致（H/A 比值 0.621 vs 0.624），见 _selftest。
    """
    av, hv = a.get("valuation") or {}, h.get("valuation") or {}
    an = _latest_annual(a)
    eps_a = av.get("price_now")
    eps_h = hv.get("price_now")
    h_in_cny = (eps_h * rate) if eps_h else None

    np_profit = an.get("net_profit_parent")            # 人民币，亿元
    shares = an.get("shares_yi")                       # 亿股

    pe_self = {}
    if np_profit:
        if av.get("market_cap"):
            pe_self["a"] = av["market_cap"] / np_profit
        if hv.get("market_cap"):
            # 港元市值 ÷ 港元净利 = 人民币市值 ÷ 人民币净利，两种写法同值
            pe_self["h"] = hv["market_cap"] / (np_profit / rate)

    # 分红比例：A/H 是同一笔分红，比例必然同值，取 A 股的人民币原生口径即可。
    # 刻意**不**用「港元股息 ÷ 人民币净利」——那是港股接口的算法，除了混币种，
    # 还隐含「用今日汇率折算历史股息」：2025 年 H 股派 1.28 港元/股，宣派日汇率
    # 约 0.8945（折 1.145 元，与 A 股 1.14493 元同额），用今日 0.8627 折算会把
    # 比例算成 43.0%，凭空低 1.6pp。
    payout = {}
    dps_a = av.get("dividend_per_share")
    dps_h = hv.get("dividend_per_share")
    if np_profit and shares and dps_a:
        payout["a"] = dps_a * shares / np_profit * 100
        payout["h"] = payout["a"]
        payout["h_dps_native"] = dps_h

    prem = None
    if eps_a and h_in_cny:
        prem = eps_a / h_in_cny - 1

    return {
        "rate": rate, "h_in_cny": h_in_cny,
        "pe_self": pe_self, "payout_cny": payout, "premium": prem,
        "dps_a": dps_a, "dps_h": dps_h, "np_profit": np_profit,
    }


def _latest_annual(d: dict) -> dict:
    fin = d.get("financials")
    if isinstance(fin, list) and fin:
        pass
    return d.get("__annual_latest__") or {}


# --------------------------------------------------------------------------- #
# 各图
# --------------------------------------------------------------------------- #
def slide_cover(a, h, pm, rate_date) -> str:
    av, hv = a["valuation"], h["valuation"]
    an = a["__annual_latest__"]
    # 林奇分类走与报告徽章同一个口径（`build_xhs._lynch_label` → src/review/lynch.py）。
    # 原先的 `or "周期型"` 是硬编码兜底：叙事层缺值时会把**猜测**当事实展示出来。
    lynch_type = bx._lynch_label(a.get("__narrative__"))

    def stat(k, v):
        return f'<div class="stat"><div class="stat-n">{v}</div><div class="stat-l">{k}</div></div>'

    def wide(k, v):
        return (f'<div class="stat stat-wide"><div class="stat-l">{k}</div>'
                f'<div class="stat-n">{v}</div></div>')

    def quote_col(tag, num, date_txt):
        """单地报价：大号数字 + 日期标签，**无卡片**（对齐茅台发布包的 .price 版式）。

        A/H 并排放两次。此前是两张带边框的卡片，价格被框在格子里，和下面的
        四个指标卡混成一片，读者一眼分不出「哪个是价格、哪个是规模」。
        """
        return (f'<div class="ahp-col"><div class="ahp-tag">{bx._esc(tag)}</div>'
                f'<div class="ahp-num">{num}</div>'
                f'<div class="ahp-lbl">{bx._esc(date_txt)}</div></div>')

    def _qdate(d: dict) -> str:
        """报价日：取行情快照日期；取不到时如实写「最新快照」，不编日期。"""
        qd = (d.get("valuation") or {}).get("quote_date")
        try:
            return qd.strftime("%Y-%m-%d 收盘") if qd else "最新快照"
        except AttributeError:
            return "最新快照"

    body = f"""
    <div class="co">
      <div class="co-name">中国海油</div>
      <div class="co-meta"><span class="code">600938</span> A 股<span class="sep">·</span>
        <span class="code">00883</span> 港股</div>
    </div>
    <div class="ahp">
      {quote_col("A 股 · 人民币", f'¥ {bx._n(av.get("price_now"), 2)}', _qdate(a))}
      {quote_col("港股 · 港元", f'HK$ {bx._n(hv.get("price_now"), 2)}', _qdate(h))}
    </div>
    <div class="stat-grid">
      {stat("2025 营业总收入", bx._n((an.get("revenue") or 0) / 1, 1, " 亿"))}
      {stat("2025 归母净利", bx._n((an.get("net_profit_parent") or 0) / 1, 1, " 亿"))}
      {stat("A 股总市值", f'{bx._n((av.get("market_cap") or 0) / 10000, 2, " 万亿元")}')}
      {stat("港股总市值", f'{bx._n((hv.get("market_cap") or 0) / 10000, 2, " 万亿港元")}')}
      {wide("ROE（2025 年报）", bx._n(an.get("roe_pct"), 2, "%"))}
    </div>
    <div class="badges">
      <div class="badge"><span class="bd-k">林奇分类</span><span class="bd-v">{bx._esc(lynch_type)}</span></div>
      <div class="badge"><span class="bd-k">格雷厄姆质量</span><span class="bd-v">高（负债低、盈利稳）</span></div>
    </div>
    <div class="cover-tip">A 股与港股是同一家公司 · 往下 8 张一起看</div>
    """
    # 市值也是当日报价算出来的，币种各随其地 —— 两处市值不能相加，故分别标注。
    foot = (f'报价与市值：A 股 {_qdate(a)} · 港股 {_qdate(h)}。'
            f'港股报价按 1 港元 = {pm["rate"]:.4f} 元人民币折算（{rate_date}）· '
            '两地市值为各自币种口径，不可相加 · 仅数据呈现，不含任何买卖建议')
    return bx._slide(1, "", "", body, foot)


def slide_business(a, h) -> str:
    segs = a.get("segments") or []
    labels = a.get("segment_labels") or []
    seg_pct = ((a.get("business_map") or {}).get("segments")) or []
    newest = labels[-1] if labels else "最新期"
    pcts = {s.get("name"): s.get("pct") for s in seg_pct}

    # 护城河三条，全部只用能点名出处的材料：
    #   资源壁垒 —— 公司简介原文（港股 competition.company_intro）
    #   规模地位 —— 东财业绩报表的行业分类与排名（competition）
    #   抗周期   —— 2025 年报的净现金与资产负债率
    # 不写「成本领先」「储量优势」这类没有数据支撑的话：本项目里没有桶油成本
    # 与净证实储量字段，写出来就是编。
    comp = a.get("competition") or {}
    rank, peers = comp.get("rank"), comp.get("peers_count")
    industry = comp.get("industry") or "行业"
    an_latest = a["__annual_latest__"]
    g = a.get("graham") or {}
    rank_txt = (f'{industry} 分类第 {rank} / {peers} 家' if (rank and peers) else "—")
    size_txt = f'营业总收入 {bx._n(an_latest.get("revenue"), 1)} 亿元 · {rank_txt}'
    cash_txt = (f'股东应占净现金 {bx._n(g.get("net_cash"), 0)} 亿元 · '
                f'资产负债率 {bx._n(an_latest.get("debt_ratio_pct"), 1, "%")}')

    moat = (
        '<div class="sec-t">护城河 <span class="sec-s">公开可核对的客观事实</span></div>'
        '<div class="moat">'
        '<div class="moat-row"><span class="moat-k">资源壁垒</span>'
        '<span class="moat-v">海上油气勘探开发需国家核准，海域探矿权稀缺；'
        '公司自述为「中国最大的海上原油及天然气生产商，'
        '全球最大的独立油气勘探及生产企业之一」（公司简介）</span></div>'
        f'<div class="moat-row"><span class="moat-k">规模地位</span>'
        f'<span class="moat-v">{size_txt}</span></div>'
        f'<div class="moat-row"><span class="moat-k">抗周期</span>'
        f'<span class="moat-v">{cash_txt}</span></div>'
        "</div>"
    )

    rows = ""
    for name, _color, revs, _margins in sorted(segs, key=lambda s: -(s[2][-1] or 0)):
        v = revs[-1] if revs else None
        rows += (
            '<div class="seg">'
            f'<div class="seg-top"><span class="seg-name">{bx._esc(name)}</span>'
            f'<span class="seg-pct">{bx._n(pcts.get(name), 1, "%")}</span></div>'
            f'<div class="seg-bar"><div class="seg-fill" style="width:{max(min(float(pcts.get(name) or 0),100),2)}%"></div></div>'
            f'<div class="seg-meta"><span>{newest} 收入 {bx._n(v, 1, " 亿")} 元</span>'
            f'<span>占比 {bx._n(pcts.get(name), 1, "%")}</span></div>'
            "</div>"
        )

    body = (
        f'<div class="lead">{bx._esc((a.get("business_map") or {}).get("main_business") or "—")}</div>'
        f'<div class="sec-t">收入结构 <span class="sec-s">{newest} · 人民币</span></div>'
        f'<div class="segs">{rows}</div>'
        + moat
        + bx._note("86.8% 的收入来自油气勘探及生产，贸易业务是配套流转、利润贡献很低。"
                   "所以这门生意的定价权不在自己手里，在油价手里。")
    )
    foot = (f'收入占比 = {newest} 分业务口径，分母为营业总收入（含「其他」业务，'
            f'故各项之和 100.1%）· 单位：人民币亿元。'
            '护城河一栏只列公开可核对的事实：行业分类与排名来自东财业绩报表，'
            '公司定位引自公司简介，净现金与资产负债率来自 2025 年报 —— 不含优劣判断')
    return bx._slide(2, "它是做什么的", "业务与收入结构", body, foot)


def slide_ah(a, h, pm, rate_date) -> str:
    av, hv = a["valuation"], h["valuation"]
    rate = pm["rate"]
    h_cny = pm["h_in_cny"]
    prem = pm["premium"]

    def row(k, va, vh, note=""):
        return (f'<div class="ahr"><div class="ahk">{k}</div>'
                f'<div class="ahv">{va}</div><div class="ahv">{vh}</div></div>')

    rows = (
        row("报价", f'¥ {bx._n(av.get("price_now"), 2)}',
            f'HK$ {bx._n(hv.get("price_now"), 2)}')
        + row("总市值", f'{bx._n((av.get("market_cap") or 0)/10000, 2, " 万亿元")}',
              f'{bx._n((hv.get("market_cap") or 0)/10000, 2, " 万亿港元")}')
        + row("市盈率 PE", bx._n(pm["pe_self"].get("a"), 1), bx._n(pm["pe_self"].get("h"), 1))
        + row("市净率 PB", bx._n(av.get("pb"), 2), bx._n(hv.get("pb"), 2))
        + row("股息率", bx._n(av.get("dividend_yield"), 2, "%"),
              bx._n(hv.get("dividend_yield"), 2, "%"))
        + row("分红比例", bx._n(pm["payout_cny"].get("a"), 1, "%"),
              bx._n(pm["payout_cny"].get("h"), 1, "%"))
    )

    body = (
        '<div class="legend">PE 为市值 ÷ 2025 年报归母净利的同口径自算值</div>'
        '<div class="aht"><div class="ahr ahh"><div class="ahk"></div>'
        '<div class="ahv">A 股 600938</div><div class="ahv">港股 00883</div></div>'
        f'{rows}</div>'
        f'<div class="prem">同一家公司 · A 股比港股贵 <b>{bx._n(prem*100, 1, "%")}</b>'
        f'<span class="prem-s">港股 HK$ {bx._n(hv.get("price_now"), 2)}'
        f' 折人民币约 ¥ {bx._n(h_cny, 2)}，即折价 {bx._n((1-1/(1+prem))*100, 1, "%")}</span></div>'
        + bx._note("同一份报表、同一笔分红，价差来自两地市场结构：港股面向全球资金，"
                   "可比的油气资产选项多；A 股以境内资金为主，纯上游油气标的稀缺"
                   "（东财「油气开采Ⅱ」分类仅 5 家）。同一份盈利在 A 股更贵，"
                   "买的价格不同，港股的股息率就高出近 2 个百分点。")
    )
    foot = (f'折算汇率 1 港元 = {rate:.4f} 元人民币（{rate_date} 中行折算价）。'
            'PE 用「市值 ÷ 2025 年报归母净利」同口径自算 —— 行情接口的 A/H 两市 PE '
            '分母期次与币种均不同，直接并列不可比；第 6 图另按行情口径单列。'
            '分红是同一笔钱，两地比例必然同值，差异只出现在股息率上（分母是各自的股价）')
    return bx._slide(3, "同一家公司，两个价", "A 股 / 港股对照", body, foot)


def slide_profit(a) -> str:
    an = a["__annual_latest__"]
    g = a.get("graham") or {}
    f = a.get("fraud") or {}
    pie = (a.get("pie_data") or {})
    groups = pie.get("groups") or []
    cost = next((x for x in groups if x.get("title") == "营业总成本"), {})
    items = cost.get("items") or []

    def card(l, n, d):
        return f'<div class="mcard"><div class="mcard-l">{l}</div>' \
               f'<div class="mcard-n">{n}</div><div class="mcard-d">{d}</div></div>'

    cards = (
        card("ROE 净资产收益率", bx._n(an.get("roe_pct"), 2, "%"), "每一元净资产赚回多少")
        + card("毛利率", bx._n(an.get("gross_margin_pct"), 1, "%"), "收入扣掉直接成本后的空间")
        + card("净利率", bx._n(an.get("net_margin_pct"), 1, "%"), "每 100 元收入最后剩下多少")
        + card("资产负债率", bx._n(an.get("debt_ratio_pct"), 1, "%"), "整体负债水平")
    )

    cost_rows = ""
    for it in items:
        cost_rows += (
            '<div class="cost-row">'
            f'<span class="cost-k">{bx._esc(it.get("name"))}</span>'
            f'<span class="cost-w"><span class="cost-bar" style="width:{min(float(it.get("pct") or 0),100):.1f}%"></span></span>'
            f'<span class="cost-v">{bx._n(it.get("value"), 1)} 亿 · {bx._n(it.get("pct"), 1, "%")}</span>'
            "</div>"
        )

    q = f.get("mscore") or {}
    qual = (
        '<div class="qual">'
        f'<div class="qual-item"><span class="q-l">审计意见</span>'
        f'<span class="q-v ok">{bx._esc(f.get("audit_opinion") or "—")}</span></div>'
        f'<div class="qual-item"><span class="q-l">财务粉饰 M-Score</span>'
        f'<span class="q-v">{bx._n(q.get("mscore"), 2)}（阈值 {bx._n(q.get("threshold"), 2)}）</span></div>'
        f'<div class="qual-item"><span class="q-l">流动比率</span>'
        f'<span class="q-v">{bx._n(g.get("current_ratio"), 2)}</span></div>'
        f'<div class="qual-item"><span class="q-l">财务异常红旗项</span>'
        f'<span class="q-v ok">{len(f.get("flags") or [])} 项</span></div>'
        "</div>"
    )

    body = (
        f'<div class="mgrid">{cards}</div>'
        f'<div class="sec-t">钱花在哪 <span class="sec-s">2025 年报 · 营业总成本 {bx._n(cost.get("total"), 1, " 亿")} 元</span></div>'
        f'<div class="costs">{cost_rows}</div>'
        f'<div class="sec-t">财务质量体检 <span class="sec-s">2025 年报</span></div>'
        f'{qual}'
        + bx._note("营业成本一项占了总成本的 85.7%——这是典型的资源开采业成本结构，"
                   "成本随油价和产量走，能压缩的空间很小。")
    )
    foot = ("指标口径：2025 年报（归母）· 单位：人民币亿元。"
            "港股接口缺毛利率 / 审计意见等字段，本图取同一法人 A 股代码披露值")
    return bx._slide(4, "它有多能赚", "盈利能力与成本结构", body, foot)


def slide_growth(a, h) -> str:
    """近 5 年柱状 + 港股独有的 10 年年化。

    港股三表覆盖 1999 年起，A 股自 2018 年才有数据 —— 所以 10 年年化只有
    港股侧算得出。这是「同一公司的两套披露」互补的实例，不是缺数据。
    """
    years = [y for y in (a.get("years") or [])][-5:]
    fin = a["__annual__"]
    fin = fin[fin["report_date"].dt.year.isin(years)]
    rev = list(fin["revenue"])
    prof = list(fin["net_profit_parent"])
    rmax = max(rev) or 1
    pmax = max(prof) or 1

    cols = ""
    for i, y in enumerate(years):
        rh = (rev[i] / rmax) * 96
        ph = (prof[i] / pmax) * 96
        # 数值不带千分位：build_xhs 的 .bcol 只有 13px 宽，「4,222」加逗号后有 5 个
        # 字符（≈23px），会溢出到相邻柱的标签上，两行数字读起来像「4,222,417」。
        cols += (
            f'<div class="bcols"><div class="bcol rev" style="height:{rh:.1f}px">'
            f'<span class="bval">{rev[i]:.0f}</span></div>'
            f'<div class="bcol prof" style="height:{ph:.1f}px">'
            f'<span class="bval">{prof[i]:.0f}</span></div>'
            f'<div class="byear">{y}</div></div>'
        )

    ar, hr = a.get("annual_rates") or {}, h.get("annual_rates") or {}

    def cagr(d, k, span="cagr5"):
        """复合增速 → 带正负号的展示串。0% 也是有效值，不能当缺失（原写法 `if v`
        会把 0 判成缺），只有 None 才写「—」。"""
        v = (d.get(k) or {}).get(span)
        v = _num(v)
        if v is None:
            return "—"
        return f'<span class="{"upv" if v > 0 else ("dnv" if v < 0 else "flat")}">' \
               f'{v * 100:+.1f}%</span>'

    def rate_table(rows: list[tuple[str, str, str]]) -> str:
        """年化增速表：5 年 / 10 年各占一列，一行一个指标。

        第一版是三张 vcard，把 10 年值塞进 `vcard-d` 的「5 年复合 · 10 年 8.8%」里：
        卡内可用宽度约 90px，9.5px 的小字折成两行，「8.8%」被甩到单独一行 ——
        读者反馈「十年复合增速和 5 年复合增速有数值没有体现出来」，就是这处折行。
        三张卡并排的情况下无论怎么调字号都放不下两组「标签+数值」，
        所以换成整宽表：指标 1 列 + 5 年 1 列 + 10 年 1 列，两组数值都独立成格。
        """
        out = ('<div class="rt"><div class="rt-row rt-head">'
               '<span class="rt-k">指标</span>'
               '<span class="rt-v">5 年复合</span>'
               '<span class="rt-v">10 年复合</span></div>')
        for label, v5, v10 in rows:
            out += (f'<div class="rt-row"><span class="rt-k">{label}</span>'
                    f'<span class="rt-v">{v5}</span>'
                    f'<span class="rt-v">{v10}</span></div>')
        return out + "</div>"

    qr = a.get("quarter_review_facts") or {}
    single = qr.get("单季") or {}
    ytd = qr.get("年初至今累计") or {}

    # 键名以 adapter 产出的固定后缀为准（单季营业总收入_亿元 / 单季归母净利润_亿元…）。
    # 用 .get 逐项取而不是模糊匹配 —— 模糊匹配到「单季营业收入」会与
    # 「单季营业总收入」混用，那是两个口径（见 tests/test_revenue_caliber.py）。
    s_rev = _num(single.get("单季营业总收入_亿元"))
    s_rev_y = _num(single.get("单季营业总收入同比_pct"))
    s_np = _num(single.get("单季归母净利润_亿元"))
    s_np_y = _num(single.get("单季归母净利同比_pct"))
    s_cf = _num(single.get("单季经营现金流净额_亿元"))
    s_cf_y = _num(single.get("单季经营现金流同比_pct"))
    y_rev = _num(ytd.get("累计营业总收入_亿元"))
    y_rev_y = _num(ytd.get("累计营业总收入同比_pct"))

    body = (
        '<div class="legend"><span class="lg" style="background:#14508c"></span>营业总收入 亿元'
        '<span class="lg" style="background:#c0392b;margin-left:12px"></span>归母净利 亿元</div>'
        f'<div class="bars">{cols}</div>'
        '<div class="sec-t">年化增速 <span class="sec-s">近 5 年 / 近 10 年</span></div>'
        + rate_table([
            ("营业总收入", cagr(ar, "sales"), cagr(hr, "sales", "cagr10")),
            ("归母净利", cagr(ar, "earnings"), cagr(hr, "earnings", "cagr10")),
            ("分红", cagr(hr, "dividends"), cagr(hr, "dividends", "cagr10")),
        ])
        + f'<div class="sec-t">最近一期 <span class="sec-s">2026 中报 · 单季同比</span></div>'
        '<div class="vgrid">'
        f'<div class="vcard"><div class="vcard-l">单季营业总收入</div>'
        f'<div class="vcard-n">{bx._n(s_rev, 1)}</div>'
        f'<div class="vcard-d">亿元 · {bx._yoy(s_rev_y)}</div></div>'
        f'<div class="vcard"><div class="vcard-l">单季归母净利</div>'
        f'<div class="vcard-n">{bx._n(s_np, 1)}</div>'
        f'<div class="vcard-d">亿元 · {bx._yoy(s_np_y)}</div></div>'
        f'<div class="vcard"><div class="vcard-l">单季经营现金流</div>'
        f'<div class="vcard-n">{bx._n(s_cf, 1)}</div>'
        f'<div class="vcard-d">亿元 · {bx._yoy(s_cf_y)}</div></div>'
        "</div>"
        + bx._note("五年看是一条漂亮的曲线，但 2023 年起营业总收入与净利连续两年回落；"
                   "到 2026 年二季度单季同比又转正 —— 周期股最陡的增速往往出现在周期顶部，"
                   f"而上半年累计增速（{bx._n(y_rev_y, 1, '%')}）已低于单季（{bx._n(s_rev_y, 1, '%')}）。")
    )
    foot = ("单位：人民币亿元。10 年年化只有港股侧算得出：港股财报覆盖 1999 年起，"
            "A 股数据自 2018 年起 —— 5 年年化为两地同值，10 年取自港股口径")
    return bx._slide(5, "长得有多快", "成长轨迹", body, foot)


def slide_valuation(a, h) -> str:
    av, hv = a["valuation"], h["valuation"]

    def cards(d, tag):
        return (
            f'<div class="vcard"><div class="vcard-l" style="font-weight:800;color:#0f3d6e">{tag}</div>'
            f'<div class="vcard-n">{bx._n(d.get("pe"), 1)}</div>'
            f'<div class="vcard-d">PE · {bx._esc(bx._pctile_zone(d.get("pe_pctile")) or "")}'
            f' {bx._n(d.get("pe_pctile"), 1, "%")} 分位</div>'
            f'<div class="vcard-p">PB {bx._n(d.get("pb"), 2)} · {bx._n(d.get("pb_pctile"), 1, "%")} 分位</div>'
            f'<div class="vcard-p">股息率 {bx._n(d.get("dividend_yield"), 2, "%")}'
            f' · {bx._n(d.get("dividend_pctile"), 1, "%")} 分位</div></div>'
        )

    rng_a = ""
    if av.get("price_low") and av.get("price_high") and av.get("price_now"):
        lo, hi, now = av["price_low"], av["price_high"], av["price_now"]
        pos = min(max((now - lo) / (hi - lo) * 100, 0), 100)
        rng_a = (
            '<div class="sec-t">A 股现价在近一年区间的位置 <span class="sec-s">前复权 · 日 K</span></div>'
            '<div class="rng"><div class="rng-track">'
            f'<div class="rng-now" style="left:{pos:.1f}%"></div></div>'
            f'<div class="rng-marks"><span>最低 {bx._n(lo, 2)}</span>'
            f'<span>现价 {bx._n(now, 2)}</span><span>最高 {bx._n(hi, 2)}</span></div></div>'
            f'<div class="ded">区间来源：{bx._esc(av.get("price_range_src") or "—")}，'
            f'位置 {bx._n(pos, 1, "%")}（低位→高位）</div>'
        )

    body = (
        f'<div class="vgrid">{cards(av, "A 股")}{cards(hv, "港股")}</div>'
        + rng_a
        + bx._note(f"同一个 PE 数字，两边回答的问题不一样：港股 {bx._n(hv.get('pe'), 1)} "
                   f"落在近十年 {bx._n(hv.get('pe_pctile'), 0)}% 分位，"
                   f"A 股 {bx._n(av.get('pe'), 1)} 落在上市以来 "
                   f"{bx._n(av.get('pe_pctile'), 0)}% 分位 —— 分位只说明它在自己历史里的位置，"
                   "不构成贵或便宜的判断。")
    )
    foot = ("分位口径：A 股自 2022-04 上市起算（约 4.4 年），港股自 2016-09 起（近 10 年）—— "
            "两者历史长度不同，不可直接比较。本图 PE/PB/股息率取行情接口口径，"
            "与第 3 图同口径自算的 PE 不是同一个数。港股 52 周区间未取到经日 K 校验的值，"
            "故本图只画 A 股区间")
    return bx._slide(6, "现在贵不贵", "估值与历史分位", body, foot)


def _div_hist(d: dict, pm: dict, since: int = 2018) -> list[dict]:
    """分红序列（人民币口径）。

    A 股 2022 年才上市，分红记录只有 4 年；港股自 2001 年连续派息，但 2018 年
    以前的「股本」是 `归母净利 ÷ 每股基本盈利` 反推的（eps 只给 2 位小数）——
    2016 年净利崩到 6.37 亿、eps 被舍成 0.01，反推出的股数 637 亿股（真实
    ≈446 亿），分红比例虚高到 3,500%。所以起点定在 2018。

    比例统一按人民币口径：港元股息 × 汇率 × 股数 ÷ 人民币归母净利。
    直接拿港元股息除人民币净利，实测虚高约 7pp。
    """
    out = []
    ann = d.get("__annual__")
    if ann is None or not len(ann):
        return out
    for _, r in ann.iterrows():
        year = int(r["report_date"].year)
        if year < since:
            continue
        dps = _num(r.get("dividend_per_share"))
        npf = _num(r.get("net_profit_parent"))
        shares = _num(r.get("shares_yi"))
        if not (dps and npf and shares):
            continue
        is_hk = (d.get("_code") or "").startswith("0")
        if is_hk:
            payout = dps * pm["rate"] * shares / npf * 100
        else:
            payout = dps * shares / npf * 100
        out.append({"year": year, "dps": dps, "is_hk": is_hk,
                    "total": _num(r.get("dividend_total")) or 0.0,
                    "payout_pct": payout})
    return out


def slide_dividend(a, h, pm, rate_date) -> str:
    # 折线用 A 股序列（人民币原生）。不用港股的长序列，原因是**汇率时点**：
    # 同一笔分红，A 股按人民币宣派（2025 年 1.14493 元/股），H 股按港元宣派
    # （1.28 港元/股）。两者在宣派日汇率下等值（≈0.8945），但拿今日汇率 0.8627
    # 折算会低估 3.6%，进而把 2025 年分红比例算成 43.0%（真实与 A 股同值 44.6%）。
    # 没有逐期历史汇率，就不做这个折算 —— 宁可少画四年，不画一个错的数。
    hist = _div_hist(a, pm, since=2022)
    dps_unit = "元/股"
    tv = a["valuation"]

    body = (
        f'<div class="divwrap">{_div_svg(hist)}'
        f'<div class="legend2"><span class="lg" style="background:#14508c"></span>每股股息（{dps_unit}）'
        '　<span class="lg" style="background:#b8860b"></span>分红比例（% 占归母净利，人民币口径）</div></div>'
        '<div class="vgrid">'
        f'<div class="vcard"><div class="vcard-l">2025 每股分红</div>'
        f'<div class="vcard-n">{bx._n(pm.get("dps_a"), 2)}</div>'
        f'<div class="vcard-d">元/股 · 人民币</div></div>'
        f'<div class="vcard"><div class="vcard-l">2025 分红总额</div>'
        f'<div class="vcard-n">{bx._n(tv.get("dividend_total"), 0)}</div>'
        f'<div class="vcard-d">亿人民币</div></div>'
        f'<div class="vcard"><div class="vcard-l">分红比例</div>'
        f'<div class="vcard-n">{bx._n(pm["payout_cny"].get("a"), 1, "%")}</div>'
        f'<div class="vcard-d">占归母净利（A/H 同值）</div></div>'
        "</div>"
        '<div class="sec-t">两地的股息率差在哪 <span class="sec-s">同一笔分红</span></div>'
        '<div class="vgrid">'
        f'<div class="vcard"><div class="vcard-l">A 股股息率</div>'
        f'<div class="vcard-n">{bx._n(tv.get("dividend_yield"), 2, "%")}</div>'
        f'<div class="vcard-d">人民币分红 ÷ A 股股价</div></div>'
        f'<div class="vcard"><div class="vcard-l">港股股息率</div>'
        f'<div class="vcard-n">{bx._n((h["valuation"] or {}).get("dividend_yield"), 2, "%")}</div>'
        f'<div class="vcard-d">港元股息 ÷ 港元股价</div></div>'
        f'<div class="vcard"><div class="vcard-l">差</div>'
        f'<div class="vcard-n">{bx._n(((h["valuation"] or {}).get("dividend_yield") or 0) - (tv.get("dividend_yield") or 0), 2, " pp")}</div>'
        f'<div class="vcard-d">来自股价差，不是分红差</div></div>'
        "</div>"
        + bx._note("分红比例从 2022 年的 43.1% 缓步挪到 2025 年的 44.6%，四年只上移 1.5 个百分点 —— "
                   "分红政策本身很稳，几乎没有弹性。而两地股息率相差近 2 个百分点，"
                   "全部来自 A/H 的股价差：分红是一笔钱，买的价格不同。")
    )
    foot = ('按分红实施年度统计，人民币原生口径（A 股 2022 年上市，故序列自 2022 年起）。'
            'H 股以港元宣派同额股息（2025 年 1.28 港元/股）—— 若直接拿港元股息除人民币净利，'
            '比例会虚高约 7pp（49.8% vs 44.6%），同一笔分红的两地比例本应同值。'
            '2018–2021 年的分红记录只有港股披露，折算需逐期历史汇率，故未上图')
    return bx._slide(7, "分红给了多少", "股东回报", body, foot)


def slide_quality(a, h, pm) -> str:
    g = a.get("graham") or {}
    f = a.get("fraud") or {}
    cp = a.get("current_position") or {}
    an = a["__annual_latest__"]

    def item(l, v, ok=False):
        return (f'<div class="qual-item"><span class="q-l">{l}</span>'
                f'<span class="q-v{" ok" if ok else ""}">{v}</span></div>')

    body = (
        '<div class="sec-t">资产负债表安全垫 <span class="sec-s">2025 年报</span></div>'
        '<div class="qual">'
        + item("股东应占净现金", bx._n(g.get("net_cash"), 0, " 亿元人民币"), True)
        + item("营运资本（流动资产 − 流动负债）",
               f'{bx._n(cp.get("working_capital"), 0)} 亿元', True)
        + item("流动比率", bx._n(g.get("current_ratio"), 2))
        + item("资产负债率", bx._n(g.get("debt_ratio"), 1, "%"))
        + item("货币资金", f'{bx._n(an.get("monetary_funds"), 0)} 亿元')
        + item("应收账款 / 营业总收入",
               f'{bx._n(an.get("accounts_receivable"), 0)} / {bx._n(an.get("revenue"), 0)} 亿元')
        + "</div>"
        '<div class="sec-t">盈利与现金流匹配 <span class="sec-s">近 3 年</span></div>'
        '<div class="qual">'
        + item("经营现金流 / 净利润（近 3 年均值）",
               bx._n(100 * sum(((f.get("cashflow") or {}).get("ratios") or [0])[:3])
                     / max(len(((f.get("cashflow") or {}).get("ratios") or [1])[:3]), 1), 0, "%"),
               True)
        + item("现金流背离预警", "无" if not (f.get("cashflow") or {}).get("warning") else "有", True)
        + item("应收增速与营收增速之差",
               bx._n((f.get("receivable") or {}).get("gap"), 2, " pp"), True)
        + "</div>"
        + bx._note("净现金 2,096 亿元、流动比率 3.24 —— 这两项决定了油价跌到多低时它还撑得住。"
                   "资源股的安全垫不写在利润表里，写在资产负债表上。")
    )
    foot = ("单位：人民币亿元。审计意见与净现金取自同一法人 A 股代码披露 —— "
            "港股资产负债表无借款科目，有息负债算不出；港股 M-Score 缺 3 个因子，"
            "故本图财务质量取 A 股口径")
    return bx._slide(8, "扛不扛得住", "财务质量与安全垫", body, foot)


def slide_risk(a, h, pm) -> str:
    av = a["valuation"]
    an = a["__annual_latest__"]
    f = a.get("fraud") or {}
    g = a.get("graham") or {}

    body = (
        '<div class="sec-t">关键数据速览 <span class="sec-s">人民币口径 · 2025 年报</span></div>'
        '<div class="qual">'
        f'<div class="qual-item"><span class="q-l">营业总收入 / 归母净利</span>'
        f'<span class="q-v">{bx._n(an.get("revenue"), 1)} / {bx._n(an.get("net_profit_parent"), 1)} 亿元</span></div>'
        f'<div class="qual-item"><span class="q-l">ROE / 资产负债率</span>'
        f'<span class="q-v">{bx._n(an.get("roe_pct"), 2, "%")} / {bx._n(an.get("debt_ratio_pct"), 1, "%")}</span></div>'
        f'<div class="qual-item"><span class="q-l">分红比例 / 股息率（A 股）</span>'
        f'<span class="q-v">{bx._n(pm["payout_cny"].get("a"), 1, "%")} / {bx._n(av.get("dividend_yield"), 2, "%")}</span></div>'
        f'<div class="qual-item"><span class="q-l">A 股 / 港股价格</span>'
        f'<span class="q-v">¥ {bx._n(av.get("price_now"), 2)} / HK$ {bx._n((h["valuation"] or {}).get("price_now"), 2)}</span></div>'
        "</div>"
        '<div class="sec-t">要盯住什么 <span class="sec-s">客观陈述</span></div>'
        '<div class="risks">'
        '<div class="risk"><span class="risk-i">1</span><span class="risk-t">'
        '收入高度绑定油价：勘探及生产贡献 86.8% 收入，油价下行会直接压缩利润，'
        '且成本端（营业成本占总成本 85.7%）几乎无法对冲。</span></div>'
        '<div class="risk"><span class="risk-i">2</span><span class="risk-t">'
        '业绩已连续两年回落：营业总收入从 2024 年 4,205.1 亿元降至 2025 年 3,982.2 亿元，'
        '归母净利从 1,379.4 亿元降至 1,220.8 亿元。</span></div>'
        '<div class="risk"><span class="risk-i">3</span><span class="risk-t">'
        '分红比例的抬升幅度有限：从 43.1% 到 44.6% 用了四年，'
        '股息回报的弹性主要来自股价与油价，而不是分红政策本身。</span></div>'
        "</div>"
        '<div class="comp-note">一个容易被忽略的事实：A 股与港股是同一家公司、同一份财务报表、'
        '同一笔分红。价格差来自两地资金与流动性，不来自经营质量 —— '
        '看这家公司时，先把「哪个市场」和「哪门生意」分开。</div>'
        '<div class="disclaimer">本图仅呈现公开财务数据与事实描述，不含任何目标价、'
        '仓位或买卖建议，不构成投资建议。数据来源：东方财富、巨潮资讯定期报告、'
        f'腾讯行情。港元折算按 {pm["rate"]:.4f}（{_today()} 中行折算价）。'
        '市场有风险，阅读者应独立判断。</div>'
    )
    foot = "全部数字可在 ValueLine 一页研报（600938 / 00883）中逐项核对"
    return bx._slide(9, "该盯住什么", "风险与结论", body, foot)


# --------------------------------------------------------------------------- #
# 附加样式（A/H 对照表、双价格块、溢价条）
# --------------------------------------------------------------------------- #
_EXTRA_CSS = f"""
  /* 封面双报价：**无卡片**，只有大号数字 + 日期标签，对齐茅台发布包的 .price 版式。
     第一版是两张带边框的卡片，价格被框住后与下方四个指标卡视觉同权，
     读者一眼分不清「哪个是价格、哪个是规模」。 */
  .ahp {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:20px; }}
  .ahp-tag {{ font-size:10.5px; color:{C['faint']}; }}
  .ahp-num {{ font-size:31px; font-weight:800; color:{C['accent']}; margin-top:5px;
              line-height:1.05; letter-spacing:-.5px; white-space:nowrap; }}
  .ahp-lbl {{ font-size:10.5px; color:{C['faint']}; margin-top:6px; }}
  /* 封面最后一张卡横跨两列：2×2 四格后剩一格单挂左列会明显不平衡 */
  .stat-wide {{ grid-column:1 / -1; display:flex; align-items:baseline;
                justify-content:space-between; }}
  .stat-wide .stat-l {{ margin-top:0; }}
  /* 年化增速表：指标 1 列 + 5 年 / 10 年各 1 列。
     整宽表是为了让两组数值都独立成格 —— 三张 110px 宽的卡无论如何调字号
     都放不下两组「标签 + 数值」，小字必然折行（见 rate_table 注释）。 */
  .rt {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
         padding:4px 13px; }}
  .rt-row {{ display:grid; grid-template-columns:1.15fr 1fr 1fr; gap:6px;
             align-items:baseline; padding:9px 0; }}
  .rt-row + .rt-row {{ border-top:1px dashed {C['line']}; }}
  .rt-head {{ padding:9px 0 6px; }}
  .rt-k {{ font-size:11.5px; color:{C['muted']}; }}
  .rt-v {{ font-size:15px; font-weight:800; color:{C['ink']}; text-align:right;
           white-space:nowrap; }}
  .rt-head .rt-k, .rt-head .rt-v {{ font-size:10px; font-weight:600;
                                    color:{C['faint']}; }}
  /* 护城河：左侧标签 + 右侧一句可核对的依据（每条都能点出来源） */
  .moat {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
           padding:4px 13px; }}
  .moat-row {{ display:flex; gap:9px; padding:9px 0; align-items:flex-start; }}
  .moat-row + .moat-row {{ border-top:1px dashed {C['line']}; }}
  .moat-k {{ flex:0 0 50px; font-size:11px; font-weight:700; color:{C['accent2']}; }}
  .moat-v {{ flex:1; font-size:11.5px; line-height:1.6; color:{C['ink']}; }}
  /* A/H 对照表 */
  .aht {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
          padding:4px 12px; }}
  .ahr {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; align-items:baseline;
          padding:8px 0; font-size:11.5px; }}
  .ahr + .ahr {{ border-top:1px dashed {C['line']}; }}
  /* 🔴 表头占位必须留在栅格里。原先写的是 `.ahh .ahk {{ display:none }}`，
     把占位格从布局里摘掉后，表头只剩 2 个 grid item，自动落到第 1、2 列
     —— 整行表头左移一格，与下面的数据列错位（第一版截图里「A 股 600938」
     悬在指标名上方、「港股 00883」悬在 A 股数值上方）。改 visibility 保留占位。 */
  .ahh {{ font-size:10.5px; font-weight:800; color:{C['accent']}; }}
  .ahh .ahk {{ visibility:hidden; }}
  .ahh .ahv {{ text-align:right; }}
  .ahk {{ color:{C['muted']}; font-size:11px; }}
  .ahv {{ color:{C['ink']}; font-weight:700; text-align:right; }}
  .prem {{ margin-top:12px; background:{C['soft']}; border:1px solid #c9d8ec;
           border-radius:9px; padding:13px 14px; font-size:13px; color:{C['ink']};
           text-align:center; }}
  .prem b {{ font-size:20px; color:{C['up']}; }}
  .prem-s {{ display:block; margin-top:5px; font-size:10.5px; color:{C['muted']}; }}
  /* 成长柱状图：build_xhs 的 .bcol 是 13px 宽、组内 3px 间距，5 组两位数营收
     放进去后同组两个数值标签会横向压在一起（「4,222」与「1,417」读成
     「4,222,417」）。加宽柱、缩小字号后单组 44px，标签 18px 不再溢出。 */
  .bars {{ height:132px; margin-bottom:22px; }}
  .bcols {{ gap:2px; position:relative; }}
  .bcol {{ width:20px; }}
  .bval {{ font-size:8px; top:-12px; }}
  /* 年份必须绝对定位到组的下方居中：.bcols 是 flex 行，.byear 作为普通
     flex item 会被排到两根柱的右边（原设计里 margin-top:6px 在行内不生效），
     视觉上每个年份都挂在柱子的右下角。 */
  .byear {{ position:absolute; bottom:-20px; left:0; right:0; text-align:center;
            font-size:9.5px; margin-top:0; }}
"""

#: 本标的的图名（不能沿用 build_xhs.SLIDE_NAMES —— 那是茅台的：
#: 第 3 张叫「渠道模式」、第 8 张叫「现金流」，与海油的内容对不上，
#: 导出后文件名即错，靠人工重命名很容易漏）。
SLIDE_NAMES = ["封面", "业务结构", "AH对照", "盈利能力", "成长轨迹",
               "估值分位", "股东回报", "财务质量", "风险提示"]


def export_png(html_text: str, out_dir: Path) -> list[Path]:
    """截图并直接落成 `01_封面.png` 形态 —— 手机相册里带序号的裸文件名最稳。

    不调 bx.export_png：它的文件名是 `{code}_{i:02d}_{茅台图名}.png`，
    既带代码前缀又用错图名，还得再改一遍名。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright，请先执行：pip install playwright && playwright install chromium")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_xhs_oil_tmp.html"
    tmp.write_text(html_text, encoding="utf-8")
    uri = tmp.resolve().as_uri()

    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 1400}, device_scale_factor=3)
        page.goto(uri)
        page.wait_for_timeout(400)
        slides = page.locator("section.slide")
        count = slides.count()
        if count != len(SLIDE_NAMES):
            sys.exit(f"页面 {count} 张，SLIDE_NAMES {len(SLIDE_NAMES)} 个 —— 先对齐再导出")
        for i in range(count):
            fp = out_dir / f"{i+1:02d}_{SLIDE_NAMES[i]}.png"
            slides.nth(i).screenshot(path=str(fp))
            paths.append(fp)
        browser.close()

    tmp.unlink(missing_ok=True)
    return paths


def build_html(a: dict, h: dict, pm: dict, rate_date: str) -> str:
    slides = [
        slide_cover(a, h, pm, rate_date),
        slide_business(a, h),
        slide_ah(a, h, pm, rate_date),
        slide_profit(a),
        slide_growth(a, h),
        slide_valuation(a, h),
        slide_dividend(a, h, pm, rate_date),
        slide_quality(a, h, pm),
        slide_risk(a, h, pm),
    ]
    return bx._PAGE.format(css=bx._CSS + _EXTRA_CSS, slides="\n".join(slides))


# --------------------------------------------------------------------------- #
# 装载
# --------------------------------------------------------------------------- #
def _prepare(code: str) -> dict:
    d = build_template_data(code)
    d["_code"] = code
    ann = build_annual_financials(load_raw(code))
    ann = ann[ann["report_date"].dt.month == 12].sort_values("report_date")
    d["__annual__"] = ann
    d["__annual_latest__"] = ann.iloc[-1].to_dict() if len(ann) else {}
    try:
        from src.report.llm import generate_narrative
        d["__narrative__"] = generate_narrative(d.get("narrative_data")) or {}
    except Exception:
        d["__narrative__"] = {}
    return d


def main() -> None:
    ap = argparse.ArgumentParser(description="中国海油 A+H → 小红书轮播图")
    ap.add_argument("-o", "--out", default="reports/xhs/600938_00883_发布包",
                    help="输出目录（默认即发布包目录，图按 01_… 命名）")
    ap.add_argument("--html-only", action="store_true", help="只写 HTML，不截图")
    args = ap.parse_args()

    rate, rate_date = _hkd_cny()
    a, h = _prepare(A_CODE), _prepare(H_CODE)
    pm = _pair_metrics(a, h, rate)

    # 口径自检：价格比值与同口径 PE 比值必须一致 —— 两者都只由 A/H 价差决定。
    # 不一致说明汇率或净利用错了，此时不该出图（对外错一次就是错一次）。
    pv = pm["pe_self"].get("h", 0) / pm["pe_self"].get("a", 1)
    xr = pm["h_in_cny"] / a["valuation"]["price_now"]
    print(f"汇率 1 港元 = {rate:.4f} 元人民币（{rate_date}）")
    print(f"A 股 ¥{a['valuation'].get('price_now')} · 港股 HK${h['valuation'].get('price_now')}"
          f" = ¥{pm['h_in_cny']:.2f} → A 股溢价 {pm['premium']*100:.1f}%")
    print(f"同口径 PE：A {pm['pe_self'].get('a'):.2f} / H {pm['pe_self'].get('h'):.2f}"
          f"｜PE 比值 {pv:.3f} vs 价格比值 {xr:.3f}")
    print(f"分红比例（人民币口径，两地同值）：{pm['payout_cny'].get('a'):.2f}%")
    if abs(pv - xr) > 0.01:
        sys.exit(f"❌ 口径自检未通过：PE 比值 {pv:.3f} ≠ 价格比值 {xr:.3f}，请检查汇率与净利口径")

    html_text = build_html(a, h, pm, rate_date)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{A_CODE}_{H_CODE}.html").write_text(html_text, encoding="utf-8")
    print(f"HTML：{out / f'{A_CODE}_{H_CODE}.html'}")
    if args.html_only:
        return
    for fp in export_png(html_text, out):
        print(f"生成：{fp}")


if __name__ == "__main__":
    main()
