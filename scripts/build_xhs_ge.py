#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""格力电器（000651）→ 小红书轮播图。

为什么不直接用 scripts/build_xhs.py：
  那个脚本是给 600519 写的、且已在产（9 张图发过）。它的「业务产品」「渠道模式」
  「现金流」三张取的是 `operating_structure`（渠道/地区切片、产销量、现金流归因），
  而格力的 `build_template_data()` 里 **`operating_structure` 是 None** ——
  直接跑会静默落进茅台兜底文案（「基酒」「i 茅台」「批发代理」写进格力卡片）。

本脚本沿用 build_xhs 的**版式层**（_CSS / _PAGE / _slide / _note / _n / _yoy / C），
内容层 9 张全部重写，换成格力字段路径上真实有值的口径：

    1 封面        公司名片 + 现价 + 4 个关键数 + 林奇/格雷厄姆徽章
    2 业务结构     消费电器 82.9% 等四大板块（占比 / 毛利率 / 半年同比）
    3 行业地位     白色家电第 3 / 11 家、份额 15.67%、五家同业营收对比   ← 新增
    4 盈利能力     ROE / 毛利率 / 净利率 / 资产负债率 + 成本构成 + 财务质量
    5 成长轨迹     近 5 年营收与净利 + 年化增速（5年/10年）+ 2026Q2 单季 + 本季主要变动项
    6 估值分位     PE 7.75 / PB 1.45 / 股息率 7.78% + 十年位置 + 近一年区间
    7 股东回报     10 年每股股息（柱）× 分红比例（线）
    8 现金流与资产 全年经营现金流三期 + 现金含量 + 2026H1 季末资产结构
    9 风险提示     关键数据速览 + 3 条风险 + 行业位置 + 免责

设计原则同 build_xhs：零观点、红涨绿跌、每个数字都能在报告里对上、
口径不同处必须标注（半年 vs 全年、季末时点 vs 年度）。

用法：
    python scripts/build_xhs_ge.py [--html-only] [-o reports/xhs/000651_发布包]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.build_xhs as bx  # noqa: E402
from src.data.adapter import build_template_data  # noqa: E402

C = bx.C
CODE = "000651"
COMPANY = "格力电器"
OUT_DEFAULT = "reports/xhs/000651_发布包"

#: 本标的的图名（绝不能沿用 bx.SLIDE_NAMES —— 那是茅台的：第 2 张叫「业务产品」、
#: 第 3 张叫「渠道模式」、第 8 张叫「现金流」，与格力内容对不上，导出即错名）。
SLIDE_NAMES = [
    "封面", "业务结构", "行业地位", "盈利能力", "成长轨迹",
    "估值分位", "股东回报", "现金流质量", "风险提示",
]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _f(v, default=None):
    """安全转 float（numpy 标量 / None / 字符串都能吃）。"""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _cagr(v) -> str:
    """年化复合（annual_rates 里存的是小数）→ 带符号百分比串。"""
    x = _f(v)
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%"


def _cagr_html(v) -> str:
    """年化复合 → 带涨跌色（红正绿负）。"""
    x = _f(v)
    if x is None:
        return '<span class="flat">—</span>'
    cls = "upv" if x >= 0 else "dnv"
    return f'<span class="{cls}">{x * 100:+.1f}%</span>'


def _big(v, digits: int = 1) -> str:
    """亿元数值 → 短串（图表标签用，不带千分位之外的后缀）。"""
    return bx._n(v, digits)


def _amt(v, digits: int = 1) -> str:
    """亿元**金额** → 带涨跌色的 HTML（红正绿负）。

    🔴 别拿 bx._yoy 去格式化金额：它按「百分比」处理，会给 -59.57 印出
    「-59.6%」—— 金额被读成百分比，读者看到的是完全不同的量纲。
    """
    x = _f(v)
    if x is None:
        return '<span class="flat">—</span>'
    cls = "upv" if x >= 0 else "dnv"
    return f'<span class="{cls}">{x:+,.{digits}f}</span>'


def _price_note(val: dict) -> str:
    """价格口径标签：盘中价 / 收盘价 / 最新价。

    🔴 原先是硬编码的「收盘」两个字。但行情快照走的是腾讯**实时**接口，抓取时刻
    落在交易时段内（09:30–15:00）时拿到的就是盘中价，标成「收盘」是对外可被
    证伪的错误（报告链路早已为此刻意区分，见 build_valueline 的同名判定）。
    这里与那处同源，避免两套口径。

    判定优先级：is_intraday=True 且有时刻 → 「盘中价 HH:MM」；
                is_intraday=False → 「收盘价 MM-DD」；
                未知（None）→ 保守写「最新价 MM-DD」，不冒充收盘。

    🔴 不能写 `intraday is False`：从 parquet 读回来的是 **numpy.bool_**，
    `np.False_ is False` 为假（类型不同），那个分支永远走不到 ——
    build_valueline 里同样的写法导致「收盘价」成了死代码、收盘价一直被标成
    「最新价」（2026-09-22 发现）。一律用 `intraday is not None and not intraday`。
    """
    intraday = val.get("is_intraday")
    qt = val.get("quote_time")
    hhmm = qt.strftime("%H:%M") if hasattr(qt, "strftime") else None
    is_intra = intraday is not None and bool(intraday)
    if is_intra:
        if hhmm:
            return f"盘中价 {hhmm}"
        d0 = val.get("price_now_date") or val.get("quote_date")
        return f"盘中价 {d0.strftime('%m-%d')}" if hasattr(d0, "strftime") else "盘中价"
    d = val.get("price_now_date") or val.get("quote_date")
    ds = d.strftime("%m-%d") if hasattr(d, "strftime") else None
    if ds is None:
        return "最新价"
    if intraday is None:
        return f"最新价 {ds}"
    return f"收盘价 {ds}"


# --------------------------------------------------------------------------- #
# 附加样式
# --------------------------------------------------------------------------- #
_EXTRA_CSS = f"""
  /* 整宽表（行业对比 / 年化增速 / 板块半年对比 / 主要变动项）
     🔴 为什么要整宽表而不是并排小卡：三张 110px 宽的 .mini 减掉 padding 只剩约
     90px，只够放一组「标签 + 数值」。放两组时 9.5px 小字必然折行，第二组的数值
     被甩到单独一行，读者会当成「这一项没数据」（上一版读者反馈的原话）。 */
  .dt {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
         padding:4px 13px; }}
  .dt-r {{ display:grid; gap:6px; align-items:baseline; padding:9px 0; font-size:11.5px; }}
  .dt-r + .dt-r {{ border-top:1px dashed {C['line']}; }}
  .dt-h {{ padding:9px 0 6px; }}
  .dt-h .dt-k, .dt-h .dt-v {{ font-size:10px; font-weight:600; color:{C['faint']}; }}
  .dt-k {{ color:{C['muted']}; }}
  .dt-v {{ color:{C['ink']}; font-weight:700; text-align:right; white-space:nowrap; }}
  /* 表头占位必须留在栅格里：用 visibility（保留盒子）。
     若写 display:none，占位格被摘出布局 → 表头 item 数少一个、整行自动落位左移一格。 */
  .dt-hide {{ visibility:hidden; }}

  /* 行业地位：五家同业营收对比（自己那行高亮） */
  .rk {{ display:flex; flex-direction:column; gap:9px; }}
  .rk-row {{ display:grid; grid-template-columns:56px 1fr 62px; gap:8px; align-items:center;
             font-size:11.5px; }}
  .rk-k {{ color:{C['muted']}; }}
  .rk-w {{ height:9px; background:{C['line']}; border-radius:4px; overflow:hidden; }}
  .rk-bar {{ display:block; height:100%; background:{C['accent3']}; border-radius:4px; }}
  .rk-v {{ color:{C['ink']}; font-weight:600; text-align:right; white-space:nowrap; }}
  .rk-row.me .rk-k {{ color:{C['accent']}; font-weight:800; }}
  .rk-row.me .rk-bar {{ background:{C['accent']}; }}
  .rk-row.me .rk-v {{ color:{C['accent']}; font-weight:800; }}

  /* 主要变动指标（季报口径，损益/现金流为累计、资产负债为季末时点） */
  .swt {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
          padding:4px 12px; }}
  /* 第一列给 2fr：「投资活动产生的现金流量净额」14 字，1.5fr（约 103px）下只能
     排 9 字/行、要折 3 行；加宽到约 124px 后 2 行放得下。
     行距 8px → 7px：图 8 加上「同比口径注」后长宽比到了 2.86（全图上限 2.82），
     收 5 行 × 2px = 10px CSS 把它压回去 —— 比再删一条信息划算。 */
  .swt-r {{ display:grid; grid-template-columns:2fr 1fr 1fr 0.8fr; gap:6px;
            align-items:baseline; padding:7px 0; font-size:11px; }}
  .swt-r + .swt-r {{ border-top:1px dashed {C['line']}; }}
  .swt-h {{ padding:8px 0 5px; }}
  .swt-h .swt-k, .swt-h .swt-v {{ font-size:9.5px; font-weight:600; color:{C['faint']}; }}
  .swt-k {{ color:{C['ink']}; line-height:1.35; }}
  .swt-v {{ text-align:right; white-space:nowrap; font-weight:700; color:{C['ink']}; }}
  .swt-g {{ font-size:9px; color:{C['faint']}; display:block; font-weight:400; }}
  /* 「同比」的口径注。只在榜上真的有「上期为负」的行时才出现（见 _swing_table）——
     投资活动现金流今年 -27.5 亿、去年 -342.7 亿，同比 +92.0% 说的是**流出收窄 92%**，
     不点明会被读成「投资现金流同比大增 92%」。星标与被注行在同一行内对应，
     比在脚注里写科目名省一行 —— 14 字的科目名会把脚注挤成两行、长宽比超上限。 */
  .swt-star {{ font-size:8px; font-weight:800; color:{C['accent']}; }}
  .swt-n {{ font-size:9px; color:{C['faint']}; line-height:1.45; padding:5px 0 1px;
            border-top:1px dashed {C['line']}; }}

  /* 现金流：**双序列分组柱**（经营现金流净额 vs 归母净利润）+ 年份下方标现金含量。
     🔴 为什么要两根柱：原先只画经营现金流净额，而图下的解读句讲的是
     「现金流净额 463.8 亿 ÷ 归母净利 290.0 亿 = 1.60 倍」—— 归母净利和这个
     比值在图上**根本没有出现**，读者无法核对，那句话等于悬空。加上浅色柱后，
     「利润有没有变成现金」变成一眼可见的柱高对比。
     🔴 .cash-bars 必须有显式高度：柱子的 height 是**百分比**，若父级高度为 auto，
     百分比无从解析，六根柱会一起塌成 0。
     🔴 .cash-g 必须 justify-content:flex-end —— 它是 column 方向的 flex 容器，
     默认 flex-start 会把柱子贴在容器**顶部**，年份与比值标签跟着上浮、三组不在一条线上。 */
  .cash {{ display:flex; gap:24px; align-items:flex-end; padding:0 4px;
           justify-content:center; }}
  .cash-g {{ flex:0 0 68px; display:flex; flex-direction:column;
             justify-content:flex-end; align-items:center; }}
  .cash-bars {{ display:flex; gap:5px; align-items:flex-end; justify-content:center;
                height:92px; width:100%; }}
  .cash-b {{ width:26px; border-radius:3px 3px 0 0; background:{C['accent']};
             position:relative; min-height:2px; }}
  .cash-b.np {{ background:{C['accent3']}; }}
  .cash-bv {{ position:absolute; top:-14px; left:50%; transform:translateX(-50%);
              font-size:9px; font-weight:700; color:{C['ink']}; white-space:nowrap; }}
  .cash-y {{ font-size:10px; color:{C['faint']}; margin-top:8px; }}
  /* 现金含量只写「1.60×」：.cash-g 宽 68px，「现金含量 1.60×」约 78px 会溢出、
     贴到相邻组。指标名统一由图例交代。 */
  .cash-r {{ font-size:10.5px; font-weight:800; color:{C['accent']}; margin-top:2px;
             white-space:nowrap; }}

  /* 现金流头部第二行：2026H1 的归母净利润与现金含量。
     图下解读句要用到这两个数 —— 不放到图上就是悬空引用（读者 2026-09-22 指出）。
     顶部细线把它与「本期现金流净额」主数分开，避免被读成第二个大数。 */
  .cf-h-x {{ font-size:11px; opacity:.9; margin-top:6px; padding-top:6px;
             border-top:1px solid rgba(255,255,255,.3); }}
  .cf-h-x b {{ font-weight:800; }}
  .cf-h-sep {{ margin:0 6px; opacity:.45; }}

  /* PE 位置条：两个标签必须错开上下两行。
     原先都在同一行、各自 translateX(-50%) 居中：卡片内宽约 314px，
     格力 PE 7.75 落在 71%（223px）、十年中位数 9.81 落在 90%（283px），
     中心间距 60px —— 而「十年中位数 9.81」半宽 38.5px + 「现在 7.75」
     半宽 23.5px = 62px > 60px，两个标签直接贴在一起、右侧还溢出卡片。
     错开垂直位置后无论两个点多近都不会压上。 */
  .pc-lbls {{ position:relative; height:32px; margin-top:7px; }}
  .pc-l {{ position:absolute; transform:translateX(-50%); font-size:10px;
           white-space:nowrap; }}
  .pc-l.now {{ color:{C['accent']}; font-weight:800; top:0; }}
  /* 中位数标签改为**向左锚定**（translateX(-100%)，文字右边缘落在竖线处）。
     居中锚定时「十年中位数 9.81」宽约 77px、半宽 38.5px，在 90% 位置
     （283px）会伸到 321.5px，而卡片内宽只有 314px —— 右侧被裁掉一截。 */
  .pc-l.med {{ color:{C['faint']}; top:15px; transform:translateX(-100%);
               padding-right:3px; }}

  /* 成长柱状图加宽：13px 柱装不下「1,896」，同组两根柱的数值标签会横向粘连
     （「1,896」与「231」读成一串）。可用宽度估算：数字 ≈ 0.55 × 字号 px，
     8px 字号下的 5 字符 ≈ 22px，柱宽给到 23px 才不与邻柱标签压上。 */
  .bars {{ height:132px; margin-bottom:20px; }}
  .bcols {{ gap:2px; }}
  .bcol {{ width:23px; }}
  .bval {{ font-size:8px; top:-12px; }}
  /* 年份绝对定位到组下方居中：.bcols 是 flex 行，.byear 作为普通 flex item
     会被排到两根柱的右边（margin-top:6px 在行内不生效）。 */
  .byear {{ position:absolute; bottom:-19px; left:0; right:0; text-align:center;
            font-size:9.5px; margin-top:0; }}
"""


# --------------------------------------------------------------------------- #
# 图 1 · 封面
# --------------------------------------------------------------------------- #
def slide_cover(d, nar) -> str:
    comp = d.get("competition") or {}
    val = d.get("valuation") or {}
    nd = d.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    year = nd.get("latest_year") or "—"

    rank_txt = (f"{comp.get('industry')} 行业第 {comp.get('rank')}"
                f" / {comp.get('peers_count')} 家")

    stats = [
        ("总市值", bx._n(val.get("market_cap"), 1, " 亿")),
        (f"{year} 营业总收入", bx._n(latest.get("revenue"), 1, " 亿")),
        (f"{year} 归母净利", bx._n(latest.get("net_profit"), 1, " 亿")),
        ("ROE", bx._n(latest.get("roe"), 2, "%")),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="stat-n">{v}</div><div class="stat-l">{k}</div></div>'
        for k, v in stats
    )

    lynch = bx._lynch_label(nar)
    gbadge = (nar or {}).get("graham_badge") or "—"
    gbadge_short = gbadge.split("（")[0].strip() if gbadge else "—"

    body = f"""
    <div class="co">
      <div class="co-name">{bx._esc(d.get('company_name'))}</div>
      <div class="co-meta"><span class="code">{CODE}</span><span class="sep">·</span>{bx._esc(rank_txt)}</div>
    </div>
    <div class="price">
      <div class="price-num">¥ {bx._n(val.get("price_now"), 2)}</div>
      <div class="price-lbl">{bx._esc(_price_note(val))}</div>
    </div>
    <div class="stat-grid">{stat_html}</div>
    <div class="badges">
      <div class="badge"><span class="bd-k">林奇分类</span><span class="bd-v">{bx._esc(lynch)}</span></div>
      <div class="badge"><span class="bd-k">格雷厄姆质量</span><span class="bd-v">{bx._esc(gbadge_short)}</span></div>
    </div>
    <div class="cover-tip">往下 8 张 · 从「它做什么」看到「该盯住什么」</div>
    """
    foot = (f"更新于 {bx._esc(d.get('report_period') or '—')} · "
            f"数据来源：东方财富 / 巨潮资讯定期报告")
    return bx._slide(1, "", "", body, foot)


# --------------------------------------------------------------------------- #
# 图 2 · 业务结构
# --------------------------------------------------------------------------- #
def _half_series(d) -> dict:
    """从 `segments` 里抽「中报口径」两期，返回 {板块: (本期, 上期, 本期毛利率, 颜色)}。

    ⚠️ 口径：`segments` 的 5 列是 24中报 / 24年报 / 25中报 / 25年报 / 26中报。
    **中报是半年累计、年报是全年**，两类不能直接比 —— 这里只取两期中报，
    保证分母一致（这正是「同口径」要守的地方）。
    """
    labels = list(d.get("segment_labels") or [])
    try:
        i_cur, i_prev = labels.index("26中报"), labels.index("25中报")
    except ValueError:
        return {}
    out = {}
    for row in d.get("segments") or []:
        name, color, revs, margins = row[0], row[1], row[2], row[3]
        cur, prev = _f(revs[i_cur]), _f(revs[i_prev])
        if cur is None:
            continue
        out[name] = {
            "cur": cur,
            "prev": prev,
            "margin": _f(margins[i_cur]),
            "color": color,
        }
    return out


def _half_yoy(cur, prev):
    """半年同比 —— 上期为 None 或 ≤0 时不判（不做无依据的百分比）。"""
    if cur is None or prev is None or prev <= 0:
        return None
    return round((cur / prev - 1) * 100, 1)


def slide_business(d, nar) -> str:
    bm = (nar or {}).get("business_model") or {}
    main = bm.get("revenue_source") or (d.get("business_map") or {}).get("main_business") or "—"
    pcts = (d.get("quarter_review_facts") or {}).get("分业务收入占比") or []
    half = _half_series(d)

    # 占比条（按 2025 年报收入占比，口径见脚注）
    rows = ""
    for p in pcts:
        name = p.get("业务")
        pct = _f(p.get("占比_pct"), 0.0)
        w = max(min(pct, 100), 2)
        h = half.get(name) or {}
        mg = h.get("margin")
        yoy = _half_yoy(h.get("cur"), h.get("prev"))
        rows += (
            '<div class="seg">'
            f'<div class="seg-top"><span class="seg-name">{bx._esc(name)}</span>'
            f'<span class="seg-pct">{bx._n(pct, 1, "%")}</span></div>'
            f'<div class="seg-bar"><div class="seg-fill" style="width:{w}%"></div></div>'
            f'<div class="seg-meta">'
            f'<span>2026 中报 {bx._n(h.get("cur"), 1, " 亿")}</span>'
            f'<span>同比 {bx._yoy(yoy)}</span>'
            f'<span>毛利率 {bx._n(mg, 1, "%")}</span></div>'
            "</div>"
        )

    # 半年口径整宽表（两期中报并列，不与年报混比）
    body_rows = ""
    tot_cur = tot_prev = 0.0
    for name, h in half.items():
        yoy = _half_yoy(h["cur"], h["prev"])
        tot_cur += h["cur"]
        tot_prev += (h["prev"] or 0.0)
        body_rows += (
            '<div class="dt-r" style="grid-template-columns:1.6fr 1fr 1fr 0.85fr;">'
            f'<span class="dt-k">{bx._esc(name)}</span>'
            f'<span class="dt-v">{bx._n(h["cur"], 1)}</span>'
            f'<span class="dt-v">{bx._n(h["prev"], 1)}</span>'
            f'<span class="dt-v">{bx._yoy(yoy)}</span>'
            "</div>"
        )
    tot_yoy = _half_yoy(tot_cur, tot_prev)
    head = (
        '<div class="dt-r dt-h" style="grid-template-columns:1.6fr 1fr 1fr 0.85fr;">'
        '<span class="dt-k">板块</span>'
        '<span class="dt-v">2026 中报</span>'
        '<span class="dt-v">2025 中报</span>'
        '<span class="dt-v">同比</span>'
        "</div>"
    )
    tail = (
        '<div class="dt-r" style="grid-template-columns:1.6fr 1fr 1fr 0.85fr;'
        'border-top:1px solid ' + C["line"] + ';">'
        '<span class="dt-k" style="font-weight:700;color:' + C["ink"] + '">四板块合计</span>'
        f'<span class="dt-v">{bx._n(tot_cur, 1)}</span>'
        f'<span class="dt-v">{bx._n(tot_prev, 1)}</span>'
        f'<span class="dt-v">{bx._yoy(tot_yoy)}</span>'
        "</div>"
    )

    # 主板块的占比与毛利率都从数据取，不硬编码
    top = max(pcts, key=lambda p: _f(p.get("占比_pct"), 0.0)) if pcts else {}
    top_name = top.get("业务") or "—"
    top_pct = _f(top.get("占比_pct"))
    top_h = half.get(top_name) or {}
    top_mg = _f(top_h.get("margin"))
    top_yoy = _half_yoy(top_h.get("cur"), top_h.get("prev"))

    note = (
        f'{top_name}贡献 {bx._n(top_pct, 1, "%")} 的营业总收入、毛利率 '
        f'{bx._n(top_mg, 1, "%")}，是四个板块里最厚的一块；'
        f'2026 中报它同比 {bx._pct_plain(top_yoy)}，'
        f'而体量最小的智能装备同期是增长的（见上表）。'
    )

    body = (
        f'<div class="lead">{bx._esc(main)}</div>'
        '<div class="sec-t">业务构成 <span class="sec-s">占 2025 年营业总收入</span></div>'
        f'<div class="segs">{rows}</div>'
        '<div class="sec-t">半年口径对比 <span class="sec-s">亿元 · 两期中报</span></div>'
        f'<div class="dt">{head}{body_rows}{tail}</div>'
        + bx._note(note)
    )
    foot = ("收入占比 = 2025 年报分部口径；同比与毛利率 = 2026 中报 vs 2025 中报"
            "「主营业务分产品情况」表。⚠️ 中报为半年累计、年报为全年，两者不混比")
    return bx._slide(2, "它靠什么赚钱", "业务结构与板块盈利", body, foot)


# --------------------------------------------------------------------------- #
# 图 3 · 行业地位（新增 —— 替代原「渠道模式」，因为格力没有渠道切片数据）
# --------------------------------------------------------------------------- #
def slide_rank(d) -> str:
    comp = d.get("competition") or {}
    peers = comp.get("top_peers") or []
    ind = comp.get("industry") or "—"
    year = comp.get("report_year") or "—"
    ind_rev = _f(comp.get("industry_revenue"))
    share = _f(comp.get("share_pct"))

    mx = max((_f(p.get("revenue_yi"), 0.0) for p in peers), default=0.0)
    rows = ""
    for p in peers:
        v = _f(p.get("revenue_yi"), 0.0)
        w = max(min(v / mx * 100, 100), 2) if mx else 2
        me = " me" if p.get("is_self") else ""
        rows += (
            f'<div class="rk-row{me}">'
            f'<span class="rk-k">{bx._esc(p.get("name"))}</span>'
            f'<span class="rk-w"><span class="rk-bar" style="width:{w:.1f}%"></span></span>'
            f'<span class="rk-v">{bx._n(v, 0)} 亿</span>'
            "</div>"
        )

    cards = (
        '<div class="mini-grid">'
        f'<div class="mini"><div class="mini-n">{bx._n(comp.get("rank"), 0)}</div>'
        f'<div class="mini-l">{bx._esc(ind)}排名</div>'
        f'<div class="mini-y flat">共 {bx._n(comp.get("peers_count"), 0)} 家</div></div>'
        f'<div class="mini"><div class="mini-n">{bx._n(share, 1, "%")}</div>'
        '<div class="mini-l">行业收入份额</div>'
        f'<div class="mini-y flat">格力 {bx._n(comp.get("revenue_yi"), 0)} 亿</div></div>'
        f'<div class="mini"><div class="mini-n">{bx._n(ind_rev, 0)}</div>'
        '<div class="mini-l">行业营业总收入</div>'
        '<div class="mini-y flat">亿元</div></div>'
        "</div>"
    )

    self_v = _f(comp.get("revenue_yi"))
    top_peer = max((p for p in peers if not p.get("is_self")),
                   key=lambda p: _f(p.get("revenue_yi"), 0.0), default={})
    top_v = _f(top_peer.get("revenue_yi"))
    ratio = (top_v / self_v) if (top_v and self_v) else None

    note = (
        f'三家白电龙头里，排名第一的 {top_peer.get("name") or "—"} '
        f'{bx._n(top_v, 0)} 亿营收是格力的 {bx._n(ratio, 2)} 倍；'
        f'格力以 {bx._n(share, 1, "%")} 的份额排第 {bx._n(comp.get("rank"), 0)}，'
        f'行业前五合计占行业总额的 '
        f'{bx._n(sum(_f(p.get("revenue_yi"), 0.0) for p in peers) / ind_rev * 100 if ind_rev else None, 1, "%")}。'
    )

    body = (
        '<div class="sec-t">关键位置 <span class="sec-s">'
        + bx._esc(f"{year} 年营业总收入口径") + "</span></div>"
        + cards
        + f'<div class="sec-t">同业营收对比 <span class="sec-s">{bx._esc(ind)} · 亿元</span></div>'
        + f'<div class="rk">{rows}</div>'
        + bx._note(note)
    )
    foot = (f"数据来源：{bx._esc(ind)}行业 {bx._esc(str(year))} 年营业总收入排名；"
            "口径为归母口径年报营业总收入，与格力 2025 年报 1711.2 亿一致")
    return bx._slide(3, "它在行业里排第几", "同业规模与份额", body, foot)


# --------------------------------------------------------------------------- #
# 图 4 · 盈利能力与财务质量
# --------------------------------------------------------------------------- #
def slide_profit(d) -> str:
    nd = d.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    graham = d.get("graham") or {}
    pie = d.get("pie_data") or {}
    fraud = d.get("fraud") or {}
    year = nd.get("latest_year") or "—"

    cards = [
        ("ROE（净资产收益率）", bx._n(latest.get("roe"), 2, "%"), "每一元净资产赚回多少"),
        ("毛利率", bx._n(latest.get("gross_margin"), 1, "%"), "卖家电的差价空间"),
        ("净利率", bx._n(latest.get("net_margin"), 1, "%"), "每一元收入最后剩下多少"),
        ("资产负债率", bx._n(latest.get("debt_ratio"), 1, "%"), "总负债 ÷ 总资产"),
    ]
    card_html = "".join(
        f'<div class="mcard"><div class="mcard-l">{k}</div>'
        f'<div class="mcard-n">{v}</div><div class="mcard-d">{d_}</div></div>'
        for k, v, d_ in cards
    )

    cost_groups = [g for g in (pie.get("groups") or []) if g.get("title") == "营业总成本"]
    cost_html = ""
    if cost_groups:
        g = cost_groups[0]
        total = _f(g.get("total"))
        cost_html = (
            f'<div class="sec-t">钱花在哪 <span class="sec-s">营业总成本 {bx._n(total, 1, " 亿")}</span></div>'
            '<div class="costs">'
        )
        for it in g.get("items") or []:
            pct = _f(it.get("pct"), 0.0)
            cost_html += (
                '<div class="cost-row">'
                f'<span class="cost-k">{bx._esc(it.get("name"))}</span>'
                f'<span class="cost-w"><span class="cost-bar" style="width:{max(min(pct,100),1)}%"></span></span>'
                f'<span class="cost-v">{bx._n(it.get("value"), 1, " 亿")} · {bx._n(pct, 1, "%")}</span>'
                "</div>"
            )
        cost_html += "</div>"
        ded = g.get("deductions") or []
        if ded:
            dv = _f(ded[0].get("value"))
            if dv is not None and dv < 0:
                cost_html += (
                    f'<div class="ded">{bx._esc(ded[0].get("name"))}为负 '
                    f'{bx._n(dv, 2, " 亿")} —— 利息收入大于利息支出，'
                    "账上资金净贡献了正的收益，抵掉了一部分成本</div>"
                )

    mc = fraud.get("mscore") or {}
    audit = fraud.get("audit_opinion") or "—"
    flags = fraud.get("flags") or []

    qual = (
        '<div class="qual">'
        f'<div class="qual-item"><span class="q-l">审计意见</span><span class="q-v ok">{bx._esc(audit)}</span></div>'
        f'<div class="qual-item"><span class="q-l">财务粉饰 M-Score</span>'
        f'<span class="q-v ok">{bx._n(mc.get("mscore"), 2)}（识别阈值 {bx._n(mc.get("threshold"), 2)}，'
        "低于阈值即无粉饰迹象）</span></div>"
        f'<div class="qual-item"><span class="q-l">流动比率</span>'
        f'<span class="q-v">{bx._n(graham.get("current_ratio"), 2)}'
        "（流动资产 ÷ 流动负债）</span></div>"
        f'<div class="qual-item"><span class="q-l">财务异常红旗项</span>'
        f'<span class="q-v ok">{len(flags)} 项</span></div>'
        "</div>"
    )

    roe, dr, cr = (_f(latest.get("roe")), _f(latest.get("debt_ratio")),
                   _f(graham.get("current_ratio")))
    note = (
        f'ROE {bx._n(roe, 2, "%")}、净利率 {bx._n(latest.get("net_margin"), 1, "%")} '
        "是这家公司的盈利底色；同时资产负债率 "
        f'{bx._n(dr, 1, "%")}、流动比率 {bx._n(cr, 2)} —— '
        "资产里有一块是靠负债撑起来的，这两组数字要放在一起读。"
    )

    body = (
        f'<div class="mgrid">{card_html}</div>'
        + cost_html
        + f'<div class="sec-t">财务质量体检 <span class="sec-s">{bx._esc(str(year))} 年报</span></div>'
        + qual
        + bx._note(note)
    )
    foot = f"指标口径：{bx._esc(str(year))} 年报（归母）· 行业分类：白色家电"
    return bx._slide(4, "它有多能赚", "盈利能力与财务质量", body, foot)


def _swing_table(qr: dict, per_group: dict | None = None) -> str:
    """本季「主要变动项」表（来自 src/report/swing.py 的候选榜）。

    🔴 只放在图 8。9 张图的长宽比要尽量一致（已发布的茅台/海油版最长 1:2.48），
    柱图 + 年化表 + 最近一期 4 卡 + 榜单 4 块叠在一张上会到 1:3.6 —— 轮播滑到
    那张会明显「变长」。榜单讲的是「报表上哪些科目动了」，与第 8 张同主题。

    ⚠️ 不能简单地 `cand[:4]`：候选榜是**按分组配额**排的（损益 → 现金流 →
    资产负债），每组内部才按变动额降序。直接截前 4 只会拿到损益组，把变动最大的
    投资活动现金流（+315.2 亿）挡在榜外 —— 而图 8 的主题正是现金流。
    `per_group` 指定每组取几条，取不到就不占位。

    口径：损益与现金流组为年初至今累计、资产负债组为季末时点；分组标在科目名下方，
    统一口径说明写在图 8 的脚注里，不逐行重复。
    """
    sw = qr.get("主要变动指标") or {}
    cand = sw.get("候选") or []
    if not cand:
        return ""
    if per_group:
        picked = []
        for g, k in per_group.items():
            picked += [c for c in cand if c.get("分组") == g][:k]
    else:
        picked = list(cand)
    if not picked:
        return ""

    rows = ""
    for it in picked:
        delta = _f(it.get("变动_亿元"))
        yoy = _f(it.get("同比_pct"))
        tag = it.get("同比口径")
        # 星标只给「上期为负」的行：那行的 `+92.0%` 需要脚注才能读对（正号是流出收窄、
        # 不是增长）。而「由负转正 / 由正转负」四个字本身自解释，不必再挂脚注 ——
        # 少一句就少一行，图 8 的长宽比才压得住（见 _swing_table 末尾的口径注）。
        # 用星标而不是「在脚注里写科目名」：科目最长 14 字（投资活动产生的现金流量净额），
        # 写进脚注会把 9px 那行挤成两行、长宽比从 2.79 涨到 2.86，超过全图上限。
        star = '<sup class="swt-star">*</sup>' if tag == "上期为负" else ""
        # 跨越零点时不给百分比：分母过小会算出 +8265.9% 这种失真比值。
        # 写「由负转正」而不写「转正」—— 后者对财务费用这类科目听起来像好事，
        # 而这张表不做利好利空判断。
        if tag in ("由负转正", "由正转负"):
            yoy_html = f'<span class="flat">{tag}</span>'
        else:
            yoy_html = bx._yoy(yoy, na="—")
        rows += (
            '<div class="swt-r">'
            f'<span class="swt-k">{bx._esc(it.get("名称"))}'
            f'<span class="swt-g">{bx._esc(it.get("分组") or "")}</span></span>'
            f'<span class="swt-v">{bx._n(it.get("本期_亿元"), 1)}</span>'
            f'<span class="swt-v">{_amt(delta)}</span>'
            f'<span class="swt-v">{yoy_html}{star}</span>'
            "</div>"
        )
    head = (
        '<div class="swt-r swt-h">'
        '<span class="swt-k">主要变动科目</span>'
        '<span class="swt-v">本期</span>'
        '<span class="swt-v">变动</span>'
        '<span class="swt-v">同比</span>'
        "</div>"
    )
    # 星标脚注：只在榜上真有「上期为负」的行时才加。那种行印的是 `+92.0%`，说的是
    # **流出收窄 92%**、不是「同比增长 92%」—— 不点明就是这一行的含义整个反掉。
    # 「由负转正 / 由正转负」不挂脚注（四个字自解释），这也是脚注能保持一行的原因。
    has_neg = any(it.get("同比口径") == "上期为负" for it in picked)
    note = (
        '<div class="swt-n">* 上期净流出：同比 = 变动额 ÷ |上期|，正号 = 流出收窄</div>'
        if has_neg else ""
    )
    return (
        f'<div class="sec-t">本季主要变动项 <span class="sec-s">'
        f'{bx._esc(sw.get("报告期") or "")} vs {bx._esc(sw.get("上期") or "")} · 亿元</span></div>'
        f'<div class="swt">{head}{rows}{note}</div>'
    )


# --------------------------------------------------------------------------- #
# 图 5 · 成长轨迹
# --------------------------------------------------------------------------- #
def slide_growth(d) -> str:
    nd = d.get("narrative_data") or {}
    recent = nd.get("recent") or []
    rates = d.get("annual_rates") or {}
    qr = d.get("quarter_review_facts") or {}
    q = qr.get("单季") or {}
    ytd = qr.get("年初至今累计") or {}

    max_v = 0.0
    for r in recent:
        max_v = max(max_v, _f(r.get("revenue"), 0.0), _f(r.get("profit"), 0.0))

    bars = ""
    for r in recent:
        rev = _f(r.get("revenue"), 0.0)
        prof = _f(r.get("profit"), 0.0)
        bars += (
            '<div class="bg">'
            '<div class="bcols">'
            f'<div class="bcol rev" style="height:{round(rev/max_v*100,1) if max_v else 0}%">'
            f'<span class="bval">{bx._n(rev, 0)}</span></div>'
            f'<div class="bcol prof" style="height:{round(prof/max_v*100,1) if max_v else 0}%">'
            f'<span class="bval">{bx._n(prof, 0)}</span></div>'
            "</div>"
            f'<div class="byear">{bx._esc(r.get("year"))}</div>'
            "</div>"
        )

    # 年化增速：整宽表（5 年 / 10 年各一列）—— 并排小卡放不下两组「标签 + 数值」
    rate_rows = [
        ("营业总收入", (rates.get("sales") or {}).get("cagr5"), (rates.get("sales") or {}).get("cagr10")),
        ("归母净利", (rates.get("earnings") or {}).get("cagr5"), (rates.get("earnings") or {}).get("cagr10")),
        ("经营现金流", (rates.get("cash_flow") or {}).get("cagr5"), (rates.get("cash_flow") or {}).get("cagr10")),
        ("每股分红", (rates.get("dividends") or {}).get("cagr5"), (rates.get("dividends") or {}).get("cagr10")),
        ("每股净资产", (rates.get("book_value") or {}).get("cagr5"), (rates.get("book_value") or {}).get("cagr10")),
    ]
    rt_head = (
        '<div class="dt-r dt-h" style="grid-template-columns:1.3fr 1fr 1fr;">'
        '<span class="dt-k">指标</span>'
        '<span class="dt-v">近 5 年复合</span>'
        '<span class="dt-v">近 10 年复合</span>'
        "</div>"
    )
    rt_body = "".join(
        '<div class="dt-r" style="grid-template-columns:1.3fr 1fr 1fr;">'
        f'<span class="dt-k">{k}</span>'
        f'<span class="dt-v">{_cagr_html(v5)}</span>'
        f'<span class="dt-v">{_cagr_html(v10)}</span>'
        "</div>"
        for k, v5, v10 in rate_rows
    )

    q_html = (
        '<div class="qgrid">'
        f'<div class="qcard"><div class="q-l">单季营业总收入</div>'
        f'<div class="q-n">{bx._n(q.get("单季营业总收入_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {bx._yoy(q.get("单季营业总收入同比_pct"))}</div></div>'
        f'<div class="qcard"><div class="q-l">单季归母净利</div>'
        f'<div class="q-n">{bx._n(q.get("单季归母净利润_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {bx._yoy(q.get("单季归母净利同比_pct"))}</div></div>'
        f'<div class="qcard"><div class="q-l">上半年累计营业总收入</div>'
        f'<div class="q-n">{bx._n(ytd.get("累计营业总收入_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {bx._yoy(ytd.get("累计营业总收入同比_pct"))}</div></div>'
        f'<div class="qcard"><div class="q-l">上半年累计归母净利</div>'
        f'<div class="q-n">{bx._n(ytd.get("累计归母净利润_亿元"), 1, " 亿")}</div>'
        f'<div class="q-y">同比 {bx._yoy(ytd.get("累计归母净利同比_pct"))}</div></div>'
        "</div>"
    )

    # 「本季主要变动项」不在这张（见 _swing_table 的说明），由第 8 张渲染。

    sales5 = _f((rates.get("sales") or {}).get("cagr5"))
    q_rev, q_ytd = _f(q.get("单季营业总收入同比_pct")), _f(ytd.get("累计营业总收入同比_pct"))
    note = (
        f'要把两件事分开看：近 10 年营业总收入复合 {_cagr((rates.get("sales") or {}).get("cagr10"))}，'
        f'而近 5 年复合只有 {_cagr(sales5)}；'
        f'最近一期单季营业总收入同比 {bx._pct_plain(q_rev)}、'
        f'上半年累计 {bx._pct_plain(q_ytd)} —— '
        "长期规模与近两年收入曲线的方向并不一致。"
    )

    body = (
        f'<div class="sec-t">近 5 年营业总收入 / 归母净利 <span class="sec-s">亿元</span></div>'
        '<div class="chart"><div class="legend">'
        f'<span class="lg" style="background:{C["accent2"]}"></span>营业总收入'
        f'<span class="lg" style="background:{C["up"]};margin-left:14px;"></span>归母净利润'
        '</div><div class="bars">' + bars + "</div></div>"
        '<div class="sec-t">年化复合增速 <span class="sec-s">年度口径</span></div>'
        f'<div class="dt">{rt_head}{rt_body}</div>'
        f'<div class="sec-t">最近一期怎么样 <span class="sec-s">'
        f'{bx._esc(qr.get("报告期") or "—")}</span></div>'
        f'{q_html}'
        + bx._note(note)
    )
    foot = ("年报口径（归母）· 单季为 2026Q2、累计为 2026 上半年 · "
            "「本季主要变动项」见图 8")
    return bx._slide(5, "长得有多快", "成长轨迹与最新一期", body, foot)


# --------------------------------------------------------------------------- #
# 图 6 · 估值与历史分位
# --------------------------------------------------------------------------- #
def slide_valuation(d) -> str:
    val = d.get("valuation") or {}
    pe, pb, dy = _f(val.get("pe")), _f(val.get("pb")), _f(val.get("dividend_yield"))
    pe_p, pb_p, dy_p = (_f(val.get("pe_pctile")), _f(val.get("pb_pctile")),
                        _f(val.get("dividend_pctile")))
    pe_med = _f(val.get("pe_median"))

    cards = [
        ("市盈率 PE", bx._n(pe, 2), pe_p, "股价 ÷ 每股收益"),
        ("市净率 PB", bx._n(pb, 2), pb_p, "股价 ÷ 每股净资产"),
        ("股息率", bx._n(dy, 2, "%"), dy_p, "每股分红 ÷ 股价"),
    ]
    card_html = ""
    for label, v, p, dsc in cards:
        zone = bx._pctile_zone(p)
        card_html += (
            '<div class="vcard">'
            f'<div class="vcard-l">{label}</div>'
            f'<div class="vcard-n">{v}</div>'
            f'<div class="vcard-d">{dsc}</div>'
            f'<div class="vcard-p">{bx._pctile_text(p)}</div>'
            + (f'<div class="vcard-z">{zone}</div>' if zone else "")
            + "</div>"
        )

    cmp_html = ""
    if pe and pe_med:
        top = max(pe, pe_med) * 1.16
        pos_now = max(0.0, min(pe / top * 100, 100.0))
        pos_med = max(0.0, min(pe_med / top * 100, 100.0))
        cmp_html = (
            '<div class="sec-t">现在的 PE 在十年区间里的位置</div>'
            '<div class="pc">'
            '<div class="pc-track">'
            f'<span class="pc-fill" style="width:{pos_now:.1f}%"></span>'
            f'<span class="pc-now" style="left:{pos_now:.1f}%"></span>'
            f'<span class="pc-med" style="left:{pos_med:.1f}%"></span>'
            "</div>"
            '<div class="pc-lbls">'
            f'<span class="pc-l now" style="left:{pos_now:.1f}%">现在 {bx._n(pe,2)}</span>'
            f'<span class="pc-l med" style="left:{pos_med:.1f}%">十年中位数 {bx._n(pe_med,2)}</span>'
            "</div></div>"
        )

    high, low, now = _f(val.get("price_high")), _f(val.get("price_low")), _f(val.get("price_now"))
    range_html = ""
    if high and low and now and high > low:
        pos = max(0.0, min((now - low) / (high - low) * 100, 100.0))
        range_html = (
            '<div class="sec-t">现价在近一年区间的位置 <span class="sec-s">前复权</span></div>'
            '<div class="rng">'
            f'<div class="rng-track"><div class="rng-now" style="left:{round(pos,1)}%"></div></div>'
            f'<div class="rng-marks"><span>最低 {bx._n(low,2)}</span>'
            f'<span>现价 {bx._n(now,2)}</span><span>最高 {bx._n(high,2)}</span></div>'
            "</div>"
        )

    note = (
        "分位数说的是「这个数字在它自己近十年的历史序列里排第几」，不构成好坏判断。"
        f'注意股息率的分位方向与 PE / PB 相反：股息率 = 每股分红 ÷ 股价，'
        f'分位越高说明按现价能拿到的股息回报在历史上越靠前'
        f'（当前 {bx._n(dy_p, 1, "%")} 分位）。'
    )

    body = (
        f'<div class="vgrid">{card_html}</div>'
        + cmp_html + range_html
        + bx._note(note)
    )
    foot = (f'{bx._esc(_price_note(val))} · 分位基于近 10 年历史序列'
            + (f'（{bx._esc(val.get("val_series_start"))} 起，共 '
               f'{bx._n(val.get("val_series_n"), 0)} 个交易日）' if val.get("val_series_start") else ""))
    return bx._slide(6, "现在贵不贵", "估值与历史分位", body, foot)


# --------------------------------------------------------------------------- #
# 图 7 · 股东回报
# --------------------------------------------------------------------------- #
def slide_dividend(d) -> str:
    val = d.get("valuation") or {}
    hist = [h for h in (val.get("dividend_history") or []) if _f(h.get("dps"))]
    if not hist:
        return bx._slide(7, "分红给了多少", "股东回报",
                         '<div class="lead">暂无分红序列数据。</div>')

    W, H = 342, 190
    # ⚠️ PR 给 26：原版 build_xhs 是 12，右侧刻度从 x=W-PR+2 起、text-anchor=start，
    #    两位数（如「60%」）放得下；格力的分红比例上界是 110 → 刻度串「110%」约
    #    18px 宽，342-12+2=332 起算会溢出 viewBox 右边界，被 SVG 裁成「11C」。
    PL, PR, PT, PB = 34, 26, 16, 28
    pw, ph = W - PL - PR, H - PT - PB
    n = len(hist)

    dps_max = max(_f(h["dps"], 0.0) for h in hist) or 1
    pcts = [_f(h["payout_pct"]) for h in hist if _f(h.get("payout_pct")) is not None]
    p_lo, p_hi = (min(pcts), max(pcts)) if pcts else (0.0, 100.0)
    p_lo = max(0.0, ((int(p_lo) - 5) // 5) * 5)
    p_hi = ((int(p_hi) + 5) // 5) * 5
    if p_hi <= p_lo:
        p_hi = p_lo + 10

    def x(i):
        return PL + (i + 0.5) / n * pw

    def y_d(v):
        return PT + ph - (v / dps_max) * ph * 0.92

    def y_p(v):
        return PT + ph - ((v - p_lo) / (p_hi - p_lo)) * ph

    bw = min(pw / n * 0.56, 22)
    bars, labels = [], []
    for i, h in enumerate(hist):
        yy = y_d(_f(h["dps"], 0.0))
        bars.append(
            f'<rect x="{x(i)-bw/2:.1f}" y="{yy:.1f}" width="{bw:.1f}" '
            f'height="{PT+ph-yy:.1f}" rx="2" fill="{C["accent2"]}" '
            f'fill-opacity="{0.95 if i==n-1 else 0.6}"/>'
        )
        if i % 2 == 0 or i == n - 1:
            labels.append(
                f'<text x="{x(i):.1f}" y="{PT+ph+14:.1f}" text-anchor="middle" '
                f'font-size="8.5" fill="{C["faint"]}">{h["year"]}</text>'
            )

    pts = [(i, _f(h["payout_pct"])) for i, h in enumerate(hist) if _f(h.get("payout_pct")) is not None]
    poly = " ".join(f"{x(i):.1f},{y_p(v):.1f}" for i, v in pts)
    line = (f'<polyline points="{poly}" fill="none" stroke="{C["up"]}" '
            f'stroke-width="1.8" stroke-linejoin="round"/>') if poly else ""
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y_p(v):.1f}" r="2.3" fill="{C["up"]}"/>' for i, v in pts
    )

    ticks = []
    for v in (p_lo, p_hi):
        yy = y_p(v)
        ticks.append(
            f'<text x="{W-PR+2:.1f}" y="{yy+3:.1f}" font-size="8" fill="{C["up"]}" '
            f'text-anchor="start">{v:.0f}%</text>'
        )
    for frac in (0.0, 1.0):
        v = dps_max * frac
        yy = y_d(v)
        ticks.append(
            f'<text x="{PL-4:.1f}" y="{yy+3:.1f}" font-size="8" fill="{C["faint"]}" '
            f'text-anchor="end">{v:.0f}</text>'
        )

    svg = (
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="每股股息与分红比例历史">'
        + "".join(bars) + line + dots + "".join(ticks) + "".join(labels) + "</svg>"
    )

    last, first = hist[-1], hist[0]
    lo_i = min(hist, key=lambda h: _f(h.get("payout_pct"), 999))
    hi_i = max(hist, key=lambda h: _f(h.get("payout_pct"), -999))

    body = (
        '<div class="sec-t">每股分红（柱）与分红比例（线） <span class="sec-s">元 / %</span></div>'
        f'<div class="divwrap">{svg}</div>'
        '<div class="legend2">'
        f'<span class="lg" style="background:{C["accent2"]}"></span>每股股息（左轴，元）'
        f'<span class="lg" style="background:{C["up"]};margin-left:12px;"></span>分红比例（右轴，% 占归母净利）'
        "</div>"
        '<div class="mini-grid">'
        f'<div class="mini"><div class="mini-n">{bx._n(last.get("dps"), 2, " 元")}</div>'
        f'<div class="mini-l">{last.get("year")} 每股分红</div></div>'
        f'<div class="mini"><div class="mini-n">{bx._n(last.get("total"), 0, " 亿")}</div>'
        '<div class="mini-l">分红总额</div></div>'
        f'<div class="mini"><div class="mini-n">{bx._n(last.get("payout_pct"), 1, "%")}</div>'
        '<div class="mini-l">分红比例</div></div>'
        "</div>"
        # 🔴 这句话的数字全部从数据取。原版 build_xhs 的措辞是「分红比例长期停在 X% 附近，
        #    Y 年抬到了 Z%」—— 那是茅台的形态（单向上台阶）。格力的比例是波动的
        #    （2019 年 29.2% 最低、2020 年 108.5% 最高），照抄会写出一句与图相反的话。
        + bx._note(
            f'分红比例这十年不是一条平滑上升的线：最低 {bx._n(lo_i.get("payout_pct"),1,"%")}'
            f'（{lo_i.get("year")}）、最高 {bx._n(hi_i.get("payout_pct"),1,"%")}'
            f'（{hi_i.get("year")}）；{first.get("year")} 年是 '
            f'{bx._n(first.get("payout_pct"),1,"%")}，{last.get("year")} 年是 '
            f'{bx._n(last.get("payout_pct"),1,"%")}。'
            f'每股分红从 {bx._n(first.get("dps"),2," 元")} 走到 '
            f'{bx._n(last.get("dps"),2," 元")}。'
        )
    )
    foot = (f'按分红实施年度统计 · 分红比例 = 现金分红 ÷ 当年归母净利 · '
            f'股息率 {bx._n(val.get("dividend_yield"),2,"%")}（'
            f'{bx._n(val.get("dividend_pctile"),1,"%")} 分位）')
    return bx._slide(7, "分红给了多少", "股东回报", body, foot)


# --------------------------------------------------------------------------- #
# 图 8 · 现金流质量（利润有没有变成现金）
# --------------------------------------------------------------------------- #
def slide_cash_assets(d) -> str:
    qr = d.get("quarter_review_facts") or {}
    ytd = qr.get("年初至今累计") or {}
    nd = d.get("narrative_data") or {}
    latest = nd.get("latest") or {}

    # 季末资产结构原先在这张，已移到图 9 的「关键数据速览」（见该函数）。
    # 报告期标记：季报「报告期」是 26Q2，但现金流是**累计**口径，须写 2026H1。
    sw_facts = qr.get("主要变动指标") or {}
    period_ytd = sw_facts.get("报告期") or qr.get("报告期") or "本期"

    from src.data.adapter import build_annual_financials, load_raw
    ann = build_annual_financials(load_raw(CODE))
    ann = ann[ann["report_date"].dt.month == 12].sort_values("report_date")
    hist = [(int(r["report_date"].year), _f(r["ocf"]), _f(r["net_profit_parent"]))
            for _, r in ann.tail(3).iterrows()]

    def _ratio(v):
        """现金含量 → 「1.60×」；缺数写 —（不写「—×」那种半截串）。"""
        return f"{bx._n(v, 2)}×" if v is not None else "—"

    # 双序列分组柱：深色 = 经营现金流净额、浅色 = 归母净利润。
    # 归一化基准取两组里的最大值 → 两根柱可直接比高矮（浅色高过深色 = 当年现金含量 > 1）。
    # 🔴 原先只画现金流单柱，而图下解读句讲的是「463.8 亿 ÷ 归母净利 290.0 亿 = 1.60 倍」，
    #    归母净利与这个比值在图上根本没出现，读者无从核对。
    mx = max((v for _, o, n in hist for v in (o, n) if v is not None), default=0.0)
    bars = ""
    for yr, ocf, np_ in hist:
        ho = round((ocf or 0) / mx * 100, 1) if mx else 0
        hn = round((np_ or 0) / mx * 100, 1) if mx else 0
        ratio = (ocf / np_) if (ocf and np_) else None
        bars += (
            '<div class="cash-g">'
            '<div class="cash-bars">'
            f'<div class="cash-b" style="height:{ho}%">'
            f'<span class="cash-bv">{bx._n(ocf, 0)}</span></div>'
            f'<div class="cash-b np" style="height:{hn}%">'
            f'<span class="cash-bv">{bx._n(np_, 0)}</span></div>'
            "</div>"
            f'<div class="cash-y">{yr}</div>'
            f'<div class="cash-r">{_ratio(ratio)}</div>'
            "</div>"
        )

    ocf_last, np_last = hist[-1][1], hist[-1][2]
    cover = (ocf_last / np_last) if (ocf_last and np_last) else None
    ocf_h1 = _f(ytd.get("累计经营现金流净额_亿元"))
    np_h1 = _f(ytd.get("累计归母净利润_亿元"))
    cover_h1 = (ocf_h1 / np_h1) if (ocf_h1 and np_h1) else None
    # 上一年的现金含量：解读句里用来对照「逐年不一样」，图上同样有这根柱的倍数。
    prev_ratio = None
    if len(hist) >= 2 and hist[-2][1] and hist[-2][2]:
        prev_ratio = hist[-2][1] / hist[-2][2]

    head = (
        '<div class="cf-head">'
        f'<div class="cf-h-l">{bx._esc(period_ytd)} 累计经营现金流净额</div>'
        f'<div class="cf-h-n">{bx._n(ocf_h1, 1, " 亿")}</div>'
        f'<div class="cf-h-y">同比 {bx._yoy(ytd.get("累计经营现金流同比_pct"))}</div>'
        # 第二行把「同期归母净利润」与「现金含量」也放上图：
        # 解读句要用这两个数，不在图上出现就是悬空引用。
        f'<div class="cf-h-x">同期归母净利润 <b>{bx._n(np_h1, 1, " 亿")}</b>'
        f'<span class="cf-h-sep">·</span>现金含量 <b>{bx._n(cover_h1, 2)} 倍</b></div>'
        "</div>"
    )

    # 季末资产结构不在这张：head + 柱图 + 榜单 + 解读句已经有 4 块，再加
    # 3 张 mini 卡 + 一行小字会到 1:2.97（其余 8 张 1.5–2.8），轮播滑到这张
    # 明显「变长」；而且「资产结构」讲的是家底、与「钱赚到了没有」是两个问题。
    # → 移到图 9 的「关键数据速览」（那里本来就在讲盈利与杠杆，同主题）。

    # ⚠️ 年份不能过 _n()：它会给 2025 加千分位变成「2,025」。
    last_yr = latest.get("latest_year") or hist[-1][0]
    prev_year = hist[-2][0] if len(hist) >= 2 else None
    # 🔴 这里的每一个数字都必须能在图上找到（柱标的数、年份下方的倍数、头部两行）：
    #    cover / prev_ratio → 柱图年份下方；cover_h1 → 头部第二行；
    #    同比 → 头部第一行。这样读者可以逐项核对这句话。
    # ⚠️ 长度要克制：这张已经有 4 块内容，解读句每多一行，整图长宽比就多 0.05。
    seq = []
    if prev_ratio is not None:
        seq.append(f"{prev_year} 年只有 {bx._n(prev_ratio, 2)} 倍")
    seq.append(f"{last_yr} 年 {bx._n(cover, 2)} 倍")
    seq.append(f"2026 上半年 {bx._n(cover_h1, 2)} 倍")
    note = (
        "深色柱是经营现金流净额、浅色是归母净利润，两根柱的比值就是现金含量。"
        + "、".join(seq)
        + " —— 赚到的利润有没有变成现金，逐年并不一样；而 2026 上半年净额本身同比 "
        f"{bx._pct_plain(_f(ytd.get('累计经营现金流同比_pct')))}，比值不低、绝对额在降。"
    )

    body = (
        head
        + f'<div class="sec-t">现金流 vs 利润 <span class="sec-s">全年口径 · 亿元</span></div>'
        + f'<div class="chart"><div class="cash">{bars}</div>'
        + '<div class="legend2">深色 = 经营现金流净额　浅色 = 归母净利润　倍数 = 现金含量</div></div>'
        + _swing_table(qr, {"损益": 3, "现金流": 2})
        + bx._note(note)
    )
    foot = ("⚠️ 口径：柱状图为 2023–2025 全年值（经营现金流净额 / 归母净利润），"
            "与 2026 上半年累计不可直接比；现金含量 = 经营现金流净额 ÷ 归母净利润；"
            "「主要变动项」中损益与现金流为年初至今累计口径")
    return bx._slide(8, "钱赚到了没有", "现金流质量 · 利润有没有变成现金", body, foot)


# --------------------------------------------------------------------------- #
# 图 9 · 风险与结论
# --------------------------------------------------------------------------- #
def slide_risk(d, nar) -> str:
    risks = (nar or {}).get("risks") or []
    comp = d.get("competition") or {}
    nd = d.get("narrative_data") or {}
    latest = nd.get("latest") or {}
    val = d.get("valuation") or {}
    graham = d.get("graham") or {}
    qr = d.get("quarter_review_facts") or {}
    qtd = qr.get("年初至今累计") or {}
    bs = qr.get("季末资产负债") or {}

    risk_html = ""
    for i, r in enumerate(risks[:4], 1):
        risk_html += (
            '<div class="risk"><span class="risk-i">{}</span><span class="risk-t">{}</span></div>'
            .format(i, bx._esc(r))
        )
    if not risk_html:
        risk_html = '<div class="risk"><span class="risk-t">数据源未覆盖风险字段。</span></div>'

    # 关键数据速览：全部由本地事实拼装成纯数字陈述（不用 LLM 的 thesis —— 它写过
    # 「下行风险有限」这类判断，本产品遵循完全去操作原则，结论性表述不进图文）。
    facts = []
    if latest.get("roe") is not None:
        facts.append(
            f'盈利与杠杆：ROE {bx._n(latest.get("roe"), 2, "%")}，'
            f'资产负债率 {bx._n(graham.get("debt_ratio"), 1, "%")}，'
            f'流动比率 {bx._n(graham.get("current_ratio"), 2)}'
        )
    if val.get("pe") is not None:
        facts.append(
            f'估值位置：PE {bx._n(val.get("pe"), 2)}（近 10 年 {bx._n(val.get("pe_pctile"), 1)}% 分位），'
            f'PB {bx._n(val.get("pb"), 2)}（{bx._n(val.get("pb_pctile"), 1)}% 分位）'
        )
    if val.get("dividend_yield") is not None:
        facts.append(
            f'股东回报：股息率 {bx._n(val.get("dividend_yield"), 2, "%")}，'
            f'分红比例 {bx._n(val.get("dividend_payout_pct"), 1, "%")}'
            f'（近 10 年 {bx._n(val.get("dividend_pctile"), 1)}% 分位）'
        )
    if qtd.get("累计营业总收入_亿元") is not None:
        facts.append(
            f'2026 上半年：营业总收入 {bx._n(qtd.get("累计营业总收入_亿元"), 1, " 亿")}'
            f'（同比 {bx._pct_plain(_f(qtd.get("累计营业总收入同比_pct")))}），'
            f'归母净利 {bx._n(qtd.get("累计归母净利润_亿元"), 1, " 亿")}'
            f'（同比 {bx._pct_plain(_f(qtd.get("累计归母净利同比_pct")))}）'
        )
    # 资产结构自 2026-09-22 起从图 8 移到这里：图 8 专讲「利润有没有变成现金」，
    # 家底（总资产/净资产/货币资金）与「盈利与杠杆」同属存量视角，放一起更好读。
    if bs.get("总资产_亿元") is not None:
        # 时点日不在 `季末资产负债` 里，取候选榜的同名键（它带 2026-06-30）。
        _spot = (qr.get("主要变动指标") or {}).get("时点日") or "最近一期"
        facts.append(
            f'资产结构（{_spot} 季末）：总资产 {bx._n(bs.get("总资产_亿元"), 0, " 亿")}，'
            f'归母净资产 {bx._n(bs.get("归母净资产_亿元"), 0, " 亿")}，'
            f'货币资金 {bx._n(bs.get("货币资金_亿元"), 0, " 亿")}'
        )
    facts.append(
        f'{nd.get("latest_year")} 年报：营业总收入 {bx._n(latest.get("revenue"), 1, " 亿")}，'
        f'归母净利 {bx._n(latest.get("net_profit"), 1, " 亿")}'
    )
    thesis_html = '<div class="sec-t">关键数据速览 <span class="sec-s">纯数字陈述</span></div>'
    for f_ in facts:
        thesis_html += f'<div class="fact"><span class="fact-d"></span>{bx._esc(f_)}</div>'

    share, ind_rev = _f(comp.get("share_pct")), _f(comp.get("industry_revenue"))
    body = (
        thesis_html
        + '<div class="sec-t">读这家公司要盯住什么</div>'
        + f'<div class="risks">{risk_html}</div>'
        + (f'<div class="comp-note">行业位置：{bx._esc(comp.get("industry"))} '
           f'{comp.get("rank")}/{comp.get("peers_count")}，'
           f'2025 年营业总收入 {bx._n(comp.get("revenue_yi"), 1, " 亿")}，'
           f'行业总额 {bx._n(ind_rev, 1, " 亿")}，份额 {bx._n(share, 1, "%")}</div>'
           if share and ind_rev else "")
        + '<div class="disclaimer">本图仅呈现客观数据与事实描述，不含任何价格点位、'
          '仓位或买卖建议，不构成投资建议。数据来源：东方财富、巨潮资讯定期报告。'
          '市场有风险，阅读者应独立判断。</div>'
    )
    foot = "全部数字可在 ValueLine 一页研报（000651）中逐项核对"
    return bx._slide(9, "该盯住什么", "风险与结论", body, foot)


# --------------------------------------------------------------------------- #
# 装载 / 组装 / 导出
# --------------------------------------------------------------------------- #
def _load(code: str):
    """数据 + 叙事。叙事直接读缓存（构建报告时已生成，避免每次出图都调 LLM）。"""
    import json
    d = build_template_data(code)
    nar = None
    try:
        cache = Path("data/cache/narrative") / f"{code}.json"
        if cache.exists():
            nar = json.loads(cache.read_text(encoding="utf-8")).get("narrative")
    except Exception:
        nar = None
    return d, nar


def build_html(d: dict, nar: dict | None) -> str:
    slides = [
        slide_cover(d, nar),
        slide_business(d, nar),
        slide_rank(d),
        slide_profit(d),
        slide_growth(d),
        slide_valuation(d),
        slide_dividend(d),
        slide_cash_assets(d),
        slide_risk(d, nar),
    ]
    return bx._PAGE.format(css=bx._CSS + _EXTRA_CSS, slides="\n".join(slides))


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
    tmp = out_dir / "_xhs_ge_tmp.html"
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


def main() -> None:
    ap = argparse.ArgumentParser(description="格力电器 → 小红书轮播图")
    ap.add_argument("-o", "--out", default=OUT_DEFAULT,
                    help="输出目录（默认即发布包目录，图按 01_… 命名）")
    ap.add_argument("--html-only", action="store_true", help="只写 HTML，不截图")
    args = ap.parse_args()

    d, nar = _load(CODE)
    if not nar:
        print("⚠️ 未读到叙事缓存 data/cache/narrative/000651.json —— 徽章与风险段会退化")

    html_text = build_html(d, nar)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{CODE}.html").write_text(html_text, encoding="utf-8")
    print(f"HTML：{out / f'{CODE}.html'}")
    if args.html_only:
        return
    for fp in export_png(html_text, out):
        print(f"生成：{fp}")


if __name__ == "__main__":
    main()
