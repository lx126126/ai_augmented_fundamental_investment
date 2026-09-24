#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""伊利股份（600887）→ 小红书轮播图。

为什么不复用现成的三个脚本（每个都有它自己的标的）：

  * `build_xhs.py`      —— 600519 茅台。图 2「业务产品」/图 3「渠道模式」取的是
                           `operating_structure` 的 i 茅台、经销商、产销量归因；
                           伊利这两个字段都是 None，会落进茅台兜底文案。
  * `build_xhs_ge.py`   —— 000651 格力。图 2 用 `quarter_review_facts.分业务收入占比`，
                           伊利在这一项上只有 **1 条**（「液体乳及乳制品制造业 97.7%」），
                           画出来是一根 97.7% 的满格条 + 一行「其他 2.3%」，没有信息量；
                           它的解读句还硬编码了「体量最小的智能装备同期是增长的」。
  * `build_xhs_oil.py`  —— 600938/00883 海油，跨市场口径。

伊利的字段路径与上面三家都不同，所以本脚本沿用 `build_xhs` 的**版式层**
（`_CSS / _PAGE / _slide / _note / _n / _yoy / _esc`）与 `build_xhs_ge` 的整宽表、
双序列柱等加固样式，**内容层 9 张全部重写**：

    1 封面        公司名片 + 现价 + 4 个关键数 + 林奇/格雷厄姆徽章
    2 业务结构     分产品 4 类（收入 / 占比 / 同比 / 毛利率）+ 分销售模式
    3 行业地位     饮料乳品第 1 / 26 家、份额 52.0%、五家同业营收对比
    4 盈利能力     ROE / 毛利率 / 净利率 / 资产负债率 + 成本构成 + 财务质量
    5 成长轨迹     近 5 年营收与净利 + 年化（5年/10年）+ 2026Q2 单季与 H1 累计
    6 估值分位     PE 16.82 / PB 3.17 / 股息率 5.13% + 十年位置 + 近一年区间
    7 股东回报     10 年每股股息（柱）× 分红比例（线）
    8 现金流质量   经营现金流 vs 归母净利三期 + 现金含量 + 本季主要变动项
    9 风险提示     关键数据速览 + 3 条风险 + 行业位置 + 免责

🔴 本标的特有的两个数据层坑（出图时必须绕开，不能直接取字段）：

  1. `operating_structure.产销量[0].口径` 写死为「生产量为当年基酒产量（含茅台酒与
     系列酒基酒），不是成品酒出库量」—— 那是**茅台的硬编码**。伊利与长江电力都有
     产销量数据，取回来都印着「基酒」。本脚本因此**不使用产销量**。
  2. `operating_structure.切片.分行业[0].名称` 是「**品制造业**」—— PDF 表格抽取时
     行首被吃掉了几个字（正确是「液体乳及乳制品制造业」，同一份数据在
     `narrative_data.segments` 里名称是完整的）。本脚本因此**不使用分行业名称**，
     改用名称正常的「分产品」与「分销售模式」两个切片。

口径提示：`切片` 里所有收入的 `期间` 字段写的是 2026H1，但各切片金额合计
1145.45 亿 ≈ 2025 年营业总收入 1159.3 亿 —— **实际是 2025 年报口径**（分产品表中
的毛利率也一样）。图内一律标「2025 年报」，不与半年报混比。

用法：
    python scripts/build_xhs_yili.py [--html-only] [-o reports/xhs/600887_发布包]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.build_xhs as bx  # noqa: E402
from src.data.adapter import build_template_data  # noqa: E402

C = bx.C
CODE = "600887"
COMPANY = "伊利股份"
OUT_DEFAULT = "reports/xhs/600887_发布包"

#: 本标的的图名。绝不能沿用 bx.SLIDE_NAMES（那是茅台的：第 2 张叫「业务产品」、
#: 第 3 张叫「渠道模式」、第 8 张叫「现金流」），导出即错名。
SLIDE_NAMES = [
    "封面", "业务结构", "行业地位", "盈利能力", "成长轨迹",
    "估值分位", "股东回报", "现金流质量", "风险提示",
]


# --------------------------------------------------------------------------- #
# 小工具（与 build_xhs_ge 同源，避免两份实现漂移）
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


def _amt(v, digits: int = 1) -> str:
    """亿元**金额** → 带涨跌色的 HTML（红正绿负）。

    🔴 别拿 bx._yoy 去格式化金额：它按「百分比」处理，会给 -59.57 印出「-59.6%」。
    """
    x = _f(v)
    if x is None:
        return '<span class="flat">—</span>'
    cls = "upv" if x >= 0 else "dnv"
    return f'<span class="{cls}">{x:+,.{digits}f}</span>'


def _tidy(text) -> str:
    """把中文文案里的数字写法与图内其它位置统一（**纯格式，不改任何数值**）。

    风险条目来自 `data/cache/narrative/{code}.json`，是 LLM 写的原文，写法自带一套：
    「2024年营业总收入同比下滑至1157.8亿」—— 中英数之间没有空格，四位金额没有千分位。
    而图内其它位置（表格、卡片）走 `bx._n()`，一律是「1,157.8 亿」。

    同一张图上两种写法并存，读者虽不会读错，但日后自动核对要额外解释一次
    （所以这里的规整是**为了核对脚本能保持严格**，不是为了好看）。

    ⚠️ 只动空格与千分位分隔符，**不动任何一位数字**：
      * 4–5 位整数补千分位；6 位及以上不动（可能是股票代码 600887）；
      * 1900–2100 的四位数（年份）不补；
      * 中文字与半角数字之间补一个空格。
    """
    import re as _re
    s = str(text if text is not None else "")

    def _num(m):
        v = m.group(0)
        if len(v) > 5:
            return v                                  # 6 位以上：可能是代码
        if len(v) == 4 and 1900 <= int(v) <= 2100:
            return v                                  # 年份
        return f"{int(v):,}"

    s = _re.sub(r"(?<![\d.,])(\d{4,})(?!\d)", _num, s)
    s = _re.sub(r"(?<=[\u4e00-\u9fff])(?=\d)", " ", s)   # 中文 → 数字
    s = _re.sub(r"(?<=\d)(?=[\u4e00-\u9fff])", " ", s)   # 数字 → 中文
    return s


def _price_note(val: dict) -> str:
    """价格口径标签：盘中价 / 收盘价 / 最新价（与报告链路同源判定）。

    行情快照走腾讯**实时**接口，抓取时刻落在交易时段内（09:30–15:00）拿到的就是
    盘中价，标成「收盘」是对外可被证伪的错误。

    🔴 不能写 `intraday is False`：从 parquet 读回来的是 numpy.bool_，
    `np.False_ is False` 为假（类型不同），那个分支永远走不到 ——
    build_valueline 里同样的写法曾让「收盘价」成了死代码。
    一律用 `intraday is not None and not intraday`。
    """
    intraday = val.get("is_intraday")
    qt = val.get("quote_time")
    hhmm = qt.strftime("%H:%M") if hasattr(qt, "strftime") else None
    if intraday is not None and bool(intraday):
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
# 附加样式（整宽表 / 同业对比 / 变动项表 / 双序列柱 / PE 位置条）
# --------------------------------------------------------------------------- #
_EXTRA_CSS = f"""
  /* 整宽表。🔴 为什么要整宽表而不是并排小卡：三张 110px 宽的 .mini 减掉 padding
     只剩约 90px，只够放一组「标签 + 数值」。放两组时 9.5px 小字必然折行，第二组
     的数值被甩到单独一行，读者会当成「这一项没数据」。 */
  .dt {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
         padding:4px 13px; }}
  .dt-r {{ display:grid; gap:6px; align-items:baseline; padding:9px 0; font-size:11.5px; }}
  .dt-r + .dt-r {{ border-top:1px dashed {C['line']}; }}
  .dt-h {{ padding:9px 0 6px; }}
  .dt-h .dt-k, .dt-h .dt-v {{ font-size:10px; font-weight:600; color:{C['faint']}; }}
  .dt-k {{ color:{C['muted']}; }}
  .dt-v {{ color:{C['ink']}; font-weight:700; text-align:right; white-space:nowrap; }}
  /* 表头占位必须留在栅格里：用 visibility（保留盒子）。
     若写 display:none，占位格被摘出布局 → 表头 item 数少一个、整行落位左移一格。 */
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

  /* 本季主要变动指标（损益/现金流为年初至今累计、资产负债为季末时点） */
  .swt {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:10px;
          padding:4px 12px; }}
  /* 第一列给 2fr：「投资活动产生的现金流量净额」14 字，1.5fr（约 103px）下只能排
     9 字/行、要折 3 行；加宽到约 124px 后 2 行放得下。行距 8px → 7px 用于压长宽比。 */
  .swt-r {{ display:grid; grid-template-columns:2fr 1fr 1fr 0.8fr; gap:6px;
            align-items:baseline; padding:4px 0; font-size:11px; }}
  .swt-r + .swt-r {{ border-top:1px dashed {C['line']}; }}
  .swt-h {{ padding:8px 0 5px; }}
  .swt-h .swt-k, .swt-h .swt-v {{ font-size:9.5px; font-weight:600; color:{C['faint']}; }}
  .swt-k {{ color:{C['ink']}; line-height:1.35; }}
  .swt-v {{ text-align:right; white-space:nowrap; font-weight:700; color:{C['ink']}; }}
  .swt-g {{ font-size:9px; color:{C['faint']}; display:block; font-weight:400; }}
  /* 变动原因行。数据层的候选榜只给「动了多少」（本期/变动/同比三列），**不给
     「为什么动」**—— 原因得从半年报原文里取（见 _SWING_WHY），所以它是图上
     唯一一处「不是数据层直出」的文字。用 muted 而非 faint：它是这一行真正的
     信息，分组标签只是口径前缀。 */
  .swt-why {{ display:block; font-size:9px; color:{C['muted']}; font-weight:400;
             line-height:1.32; margin-top:1px; }}
  /* 「同比」的口径注：只在榜上真的有「上期为负」的行时才出现。
     那种行印的是 `-76.4%`，说的是**流出扩大 76.4%**、不是「下降 76.4%」——
     不点明就是这一行的含义整个反掉。星标与被注行在同一行内对应，
     比在脚注里写科目名省一行（14 字的科目名会把脚注挤成两行）。 */
  .swt-star {{ font-size:8px; font-weight:800; color:{C['accent']}; }}
  .swt-n {{ font-size:9px; color:{C['faint']}; line-height:1.45; padding:5px 0 1px;
            border-top:1px dashed {C['line']}; }}

  /* 现金流：**双序列分组柱**（经营现金流净额 vs 归母净利润）+ 年份下方标现金含量。
     🔴 .cash-bars 必须有显式高度：柱子的 height 是**百分比**，父级高度为 auto 时
     百分比无从解析，六根柱会一起塌成 0。
     🔴 .cash-g 必须 justify-content:flex-end —— 它是 column 方向 flex 容器，
     默认 flex-start 会把柱子贴在容器**顶部**，标签跟着上浮、几组不在一条线上。 */
  .cash {{ display:flex; gap:24px; align-items:flex-end; padding:0 4px;
           justify-content:center; }}
  .cash-g {{ flex:0 0 68px; display:flex; flex-direction:column;
             justify-content:flex-end; align-items:center; }}
  .cash-bars {{ display:flex; gap:5px; align-items:flex-end; justify-content:center;
                height:62px; width:100%; }}
  .cash-b {{ width:26px; border-radius:3px 3px 0 0; background:{C['accent']};
             position:relative; min-height:2px; }}
  .cash-b.np {{ background:{C['accent3']}; }}
  .cash-bv {{ position:absolute; top:-14px; left:50%; transform:translateX(-50%);
              font-size:9px; font-weight:700; color:{C['ink']}; white-space:nowrap; }}
  .cash-y {{ font-size:10px; color:{C['faint']}; margin-top:8px; }}
  /* 现金含量只写「1.70×」：.cash-g 宽 68px，「现金含量 1.70×」约 78px 会溢出。 */
  .cash-r {{ font-size:10.5px; font-weight:800; color:{C['accent']}; margin-top:2px;
             white-space:nowrap; }}

  /* 现金流头部第二行：2026H1 的归母净利润与现金含量。
     图下解读句要用这两个数 —— 不放到图上就是悬空引用。 */
  .cf-h-x {{ font-size:11px; opacity:.9; margin-top:6px; padding-top:6px;
             border-top:1px solid rgba(255,255,255,.3); }}
  .cf-h-x b {{ font-weight:800; }}
  .cf-h-sep {{ margin:0 6px; opacity:.45; }}

  /* PE 位置条：两个标签必须错开上下两行。
     原先都在同一行、各自 translateX(-50%) 居中：伊利 PE 16.82 落在约 60%、
     十年中位数 23.74 落在约 85%，中心间距小，两个标签会贴成一串。
     错开垂直位置后无论两个点多近都不会压上。
     中位数标签改为**向左锚定**（translateX(-100%)），否则「十年中位数 23.74」
     约 81px 宽、半宽 40.5px，在 85% 位置会伸到卡片外被裁。 */
  .pc-lbls {{ position:relative; height:32px; margin-top:7px; }}
  .pc-l {{ position:absolute; transform:translateX(-50%); font-size:10px;
           white-space:nowrap; }}
  .pc-l.now {{ color:{C['accent']}; font-weight:800; top:0; }}
  .pc-l.med {{ color:{C['faint']}; top:15px; transform:translateX(-100%);
               padding-right:3px; }}

  /* 成长柱状图：13px 柱装不下「1,262」，同组两根柱的数值标签会横向粘连
     （「1,262」与「87」读成一串）。可用宽度估算：数字 ≈ 0.55 × 字号 px，
     8px 字号下的 5 字符 ≈ 22px → 柱宽给到 23px。 */
  .bars {{ height:100px; margin-bottom:16px; }}
  .bcols {{ gap:2px; }}
  .bcol {{ width:23px; }}
  .bval {{ font-size:8px; top:-12px; }}
  /* 年份绝对定位到组下方居中：.bcols 是 flex 行，.byear 作为普通 flex item
     会被排到两根柱的右边（margin-top 在行内不生效）。 */
  .byear {{ position:absolute; bottom:-19px; left:0; right:0; text-align:center;
            font-size:9.5px; margin-top:0; }}

  /* 上半年累计那一行小字（图 5）。原来是独立的 4 张 qcard，与「近四个季度单季」
     的 4 张卡同时出现 —— 两块都含 26Q2 的数，重复占高度、整图长宽比到了 1:2.92。
     合并成一张单季表 + 这一行小字后回到 1:2.45。 */
  .qtd {{ font-size:10px; color:{C['faint']}; padding:7px 2px 0; line-height:1.5; }}
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
def _slice_by(d: dict, key: str) -> list[dict]:
    """取 `operating_structure.切片[key]`（分产品 / 分销售模式 / 分地区）。

    ⚠️ 不取「分行业」：那个切片的名称在数据层被截断（伊利是「品制造业」，
    正确为「液体乳及乳制品制造业」），直接印出来是错的公司名。
    """
    os_ = d.get("operating_structure") or {}
    return list((os_.get("切片") or {}).get(key) or [])


def _struct_period(d: dict) -> str:
    """分部数据的**真实**口径期。

    🔴 `operating_structure.期间` 这个字段不能直接用：伊利写的是 2026H1，
    但各切片金额合计 1145.45 亿 ≈ 2025 年营业总收入 1159.3 亿 —— 实际是
    **2025 年报**口径（同一份数据在茅台那边标的是 2026H1，也是这个问题）。
    与其信任字段，不如用「分部合计 ÷ 最近年报营业总收入」自证：
    比值落在 0.9–1.05 之间即判为年报口径，否则退回字段值。
    """
    prods = _slice_by(d, "分产品")
    tot = sum(_f(p.get("收入_亿元"), 0.0) or 0.0 for p in prods)
    nd = d.get("narrative_data") or {}
    rev = _f((nd.get("latest") or {}).get("revenue"))
    year = nd.get("latest_year")
    if rev and 0.9 <= tot / rev <= 1.05 and year:
        return f"{year} 年报"
    return str((d.get("operating_structure") or {}).get("期间") or "—")


def slide_business(d, nar) -> str:
    bm = (nar or {}).get("business_model") or {}
    main = bm.get("revenue_source") or (d.get("business_map") or {}).get("main_business") or "—"
    prods = _slice_by(d, "分产品")
    modes = _slice_by(d, "分销售模式")
    period = _struct_period(d)

    # 分产品：占比条 + 收入 / 同比 / 毛利率（全部从数据取，不硬编码）
    rows = ""
    for p in prods:
        name = p.get("名称")
        pct = _f(p.get("占比_pct"), 0.0)
        w = max(min(pct, 100), 2)
        rows += (
            '<div class="seg">'
            f'<div class="seg-top"><span class="seg-name">{bx._esc(name)}</span>'
            f'<span class="seg-pct">{bx._n(pct, 1, "%")}</span></div>'
            f'<div class="seg-bar"><div class="seg-fill" style="width:{w}%"></div></div>'
            f'<div class="seg-meta">'
            f'<span>{bx._n(p.get("收入_亿元"), 1, " 亿")}</span>'
            f'<span>同比 {bx._yoy(p.get("收入同比_pct"))}</span>'
            f'<span>毛利率 {bx._n(p.get("毛利率_pct"), 1, "%")}</span></div>'
            "</div>"
        )

    # 分产品销售模式：只有 2 行，用整宽表
    mode_rows = ""
    for m in modes:
        mode_rows += (
            '<div class="dt-r" style="grid-template-columns:1.4fr 1fr 1fr 1fr;">'
            f'<span class="dt-k">{bx._esc(m.get("名称"))}</span>'
            f'<span class="dt-v">{bx._n(m.get("收入_亿元"), 1)}</span>'
            f'<span class="dt-v">{bx._n(m.get("占比_pct"), 1, "%")}</span>'
            f'<span class="dt-v">{bx._yoy(m.get("收入同比_pct"))}</span>'
            "</div>"
        )
    mode_head = (
        '<div class="dt-r dt-h" style="grid-template-columns:1.4fr 1fr 1fr 1fr;">'
        '<span class="dt-k">销售模式</span>'
        '<span class="dt-v">收入（亿）</span>'
        '<span class="dt-v">占比</span>'
        '<span class="dt-v">同比</span>'
        "</div>"
    )

    # 主产品：占比最高那一条，数字全部从数据取
    top = max(prods, key=lambda p: _f(p.get("占比_pct"), 0.0)) if prods else {}
    top_name = top.get("名称") or "—"
    top_pct = _f(top.get("占比_pct"))
    top_mg = _f(top.get("毛利率_pct"))
    top_yoy = _f(top.get("收入同比_pct"))
    # 同比为正、且占比最大的那一项（用来讲「增长来自哪」，不预设是哪个品类）
    grow = [p for p in prods if (_f(p.get("收入同比_pct")) or 0) > 0 and p is not top]
    grow_txt = ""
    if grow:
        g = max(grow, key=lambda p: _f(p.get("毛利率_pct"), 0.0))
        grow_txt = (f'占比较小的「{bx._esc(g.get("名称"))}」'
                    f'{bx._n(g.get("收入_亿元"), 1, " 亿")}、同比 '
                    f'{bx._pct_plain(g.get("收入同比_pct"))}，毛利率 '
                    f'{bx._n(g.get("毛利率_pct"), 1, "%")} 反而是四块里最高的。')

    note = (
        f'{top_name}贡献 {bx._n(top_pct, 1, "%")} 的收入、毛利率 '
        f'{bx._n(top_mg, 1, "%")}，是四块里最大的一块，{bx._esc(period)}同比 '
        f'{bx._pct_plain(top_yoy)}；{grow_txt}'
    )

    body = (
        f'<div class="lead">{bx._esc(_tidy(main))}</div>'
        f'<div class="sec-t">分产品构成 <span class="sec-s">占 {bx._esc(period)}营业总收入</span></div>'
        f'<div class="segs">{rows}</div>'
        f'<div class="sec-t">怎么卖出去的 <span class="sec-s">{bx._esc(period)} · 亿元</span></div>'
        f'<div class="dt">{mode_head}{mode_rows}</div>'
        + bx._note(note)
    )
    foot = (f"全部为 {bx._esc(period)}「主营业务分产品 / 分销售模式情况」口径，"
            "同比为 2025 vs 2024 年报。⚠️ 中报为半年累计、年报为全年，两者不混比")
    return bx._slide(2, "它靠什么赚钱", "业务结构与销售模式", body, foot)


# --------------------------------------------------------------------------- #
# 图 3 · 行业地位
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

    self_v = _f(comp.get("revenue_yi"))
    cards = (
        '<div class="mini-grid">'
        f'<div class="mini"><div class="mini-n">{bx._n(comp.get("rank"), 0)}</div>'
        f'<div class="mini-l">{bx._esc(ind)}排名</div>'
        f'<div class="mini-y flat">共 {bx._n(comp.get("peers_count"), 0)} 家</div></div>'
        f'<div class="mini"><div class="mini-n">{bx._n(share, 1, "%")}</div>'
        '<div class="mini-l">行业收入份额</div>'
        f'<div class="mini-y flat">自身 {bx._n(self_v, 0)} 亿</div></div>'
        f'<div class="mini"><div class="mini-n">{bx._n(ind_rev, 0)}</div>'
        '<div class="mini-l">行业营业总收入</div>'
        '<div class="mini-y flat">亿元</div></div>'
        "</div>"
    )

    top_peer = max((p for p in peers if not p.get("is_self")),
                   key=lambda p: _f(p.get("revenue_yi"), 0.0), default={})
    top_v = _f(top_peer.get("revenue_yi"))
    ratio = (self_v / top_v) if (top_v and self_v) else None
    top5 = sum(_f(p.get("revenue_yi"), 0.0) for p in peers)

    note = (
        f'规模第二的{bx._esc(top_peer.get("name") or "—")} '
        f'{bx._n(top_v, 0)} 亿，只有它的 {bx._n(top_v / self_v * 100 if self_v else None, 1, "%")}；'
        f'{bx._esc(ind)}前五家合计 {bx._n(top5, 1, " 亿")}，占行业总额 '
        f'{bx._n(top5 / ind_rev * 100 if ind_rev else None, 1, "%")}'
        f'（行业共 {bx._n(comp.get("peers_count"), 0)} 家），份额高度集中在这几家。'
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
            f"口径为年报营业总收入，与本公司 {bx._esc(str(year))} 年报 "
            f"{bx._n(comp.get('revenue_yi'), 1, ' 亿')} 一致")
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
    comp = d.get("competition") or {}
    year = nd.get("latest_year") or "—"

    cards = [
        ("ROE（净资产收益率）", bx._n(latest.get("roe"), 2, "%"), "每一元净资产赚回多少"),
        ("毛利率", bx._n(latest.get("gross_margin"), 1, "%"), "卖乳制品的差价空间"),
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
    foot = (f"指标口径：{bx._esc(str(year))} 年报（归母）· "
            f"行业分类：{bx._esc(comp.get('industry') or '—')}")
    return bx._slide(4, "它有多能赚", "盈利能力与财务质量", body, foot)


# --------------------------------------------------------------------------- #
# 本季主要变动项的「变动原因」
# --------------------------------------------------------------------------- #
# 🔴 这是全图唯一「不由数据层直出」的文字，所以每一条的出处都必须指得出来：
#    · 现金流量三条 = 2026 半年报「第三节 管理层讨论与分析 → 四、报告期内主要经营情况 →
#      (一)主营业务分析 → 1、财务报表相关科目变动分析表」下面紧随的三句
#      「…活动产生的现金流量净额变动原因说明」，压缩自原文、未改口径；
#    · 「资产减值损失」= 附注七(73)「资产减值损失」其他说明（原文：本期计提澳优乳业
#      商誉减值损失以及存货减值损失增加所致）＋ 附注七(27) 商誉减值明细表
#      （影响合并报表的商誉减值 154,654.49 万元 → 15.5 亿）；
#    · 「商誉」= 2026 半年报「(三)资产、负债情况分析 → 其他说明（11）」原文
#      「商誉减少原因：本期计提澳优乳业商誉减值所致」。
# ⚠️ 2026-09-24 起「资产减值损失」**已经是数据层给得出的数**了（`fields.IMPAIRMENT_FIELDS`
#    合并了东财的新旧准则双字段，见发布说明「附二」），所以它可以在榜上占一行。
#    这条注释原先写的是「别顺手加成一行」—— 那是因为当时 raw 层该列 2018Q2 起全空。
_SWING_WHY: dict[str, str] = {
    "资产减值损失": "计提澳优乳业商誉减值 15.5 亿及存货跌价 9.1 亿",
    "商誉": "本期计提澳优乳业商誉减值，账面商誉相应减少",
    "扣非归母净利润": "资产减值 24.6 亿（上期仅 3.4 亿）是主要减项",
    "经营活动产生的现金流量净额": "销售商品收现增加、购买商品付现减少",
    "投资活动产生的现金流量净额": "购买与赎回大额存单、定期存款的净流出增加",
    "筹资活动产生的现金流量净额": "支付的股利减少、借款净增加额增加",
}


def _swing_table(qr: dict, per_group: dict | None = None,
                 reasons: dict | None = None) -> str:
    """本季「主要变动项」表（来自 src/report/swing.py 的候选榜）。

    ⚠️ 不能简单地 `cand[:N]`：候选榜是**按分组配额**排的（损益 → 现金流 →
    资产负债），每组内部才按变动额降序。直接截前 N 只会拿到损益组，把变动最大的
    现金流科目挡在榜外 —— 而图 8 的主题正是现金流。`per_group` 指定每组取几条。

    口径：损益与现金流组为年初至今累计、资产负债组为季末时点。

    `reasons` 是「科目名 → 变动原因」，值必须是**半年报原文**（见 `_SWING_WHY`）。
    数据层的候选榜只回答「动了多少」，不回答「为什么动」—— 读者看到「扣非 -14.2 亿」
    必然要问一句为什么，所以这一列不能空着。没有配到原因的科目只印分组标签。
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

    reasons = reasons or {}
    rows = ""
    for it in picked:
        name = it.get("名称") or ""
        delta = _f(it.get("变动_亿元"))
        yoy = _f(it.get("同比_pct"))
        tag = it.get("同比口径")
        # 星标只给「上期为负」的行：那行的 `-76.4%` 需要脚注才能读对
        # （负号是**流出扩大**，不是「下降」）。跨零点写「由正转负」四个字自解释，不挂脚注。
        star = '<sup class="swt-star">*</sup>' if tag == "上期为负" else ""
        if tag in ("由负转正", "由正转负"):
            yoy_html = f'<span class="flat">{tag}</span>'
        else:
            yoy_html = bx._yoy(yoy, na="—")
        why = reasons.get(name)
        group = bx._esc(it.get("分组") or "")
        if why:
            sub_html = f'<span class="swt-why">{group} · {bx._esc(why)}</span>'
        else:
            sub_html = f'<span class="swt-g">{group}</span>'
        rows += (
            '<div class="swt-r">'
            f'<span class="swt-k">{bx._esc(name)}{sub_html}</span>'
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
    has_neg = any(it.get("同比口径") == "上期为负" for it in picked)
    note = (
        '<div class="swt-n">* 上期净流出：同比 = 变动额 ÷ |上期|，正号收窄、负号扩大</div>'
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
    series = qr.get("单季走势序列") or []

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

    # 单季走势：把原来的「最近一期 4 卡」与「近四个季度 4 卡」合并成一张表。
    # 🔴 两块都含 26Q2 的数，并排出现等于同一件事说两遍，而整图长宽比被顶到 1:2.92
    #    （全组上限 1:2.8）。合并成「期 × 营收 / 归母净利 / 营收同比」后信息更全、更矮。
    last4 = series[-4:]
    qtr_rows = ""
    for s in last4:
        qtr_rows += (
            '<div class="dt-r" style="grid-template-columns:0.8fr 1fr 1fr 1fr;">'
            f'<span class="dt-k">{bx._esc(s.get("期"))}</span>'
            f'<span class="dt-v">{bx._n(s.get("营业总收入_亿元"), 1)}</span>'
            f'<span class="dt-v">{bx._n(s.get("归母净利_亿元"), 1)}</span>'
            f'<span class="dt-v">{bx._yoy(s.get("营业总收入同比_pct"))}</span>'
            "</div>"
        )
    qtr_head = (
        '<div class="dt-r dt-h" style="grid-template-columns:0.8fr 1fr 1fr 1fr;">'
        '<span class="dt-k">单季</span>'
        '<span class="dt-v">营业总收入</span>'
        '<span class="dt-v">归母净利</span>'
        '<span class="dt-v">营收同比</span>'
        "</div>"
    )
    ytd_line = (
        f'<div class="qtd">2026 上半年累计：营业总收入 '
        f'{bx._n(ytd.get("累计营业总收入_亿元"), 1, " 亿")}'
        f'（同比 {bx._pct_plain(_f(ytd.get("累计营业总收入同比_pct")))}），'
        f'归母净利 {bx._n(ytd.get("累计归母净利润_亿元"), 1, " 亿")}'
        f'（同比 {bx._pct_plain(_f(ytd.get("累计归母净利同比_pct")))}）</div>'
    )

    # 🔴 这句话引用的每个数字都必须能在图上找到：单季营收同比与两个单季归母净利
    #    → 上面那张单季表；累计两个同比 → 表下的小字行；近 5 年 / 近 10 年复合 → 年化表。
    sales5 = _f((rates.get("sales") or {}).get("cagr5"))
    sales10 = _f((rates.get("sales") or {}).get("cagr10"))
    q_rev = _f(q.get("单季营业总收入同比_pct"))
    q_np = _f(q.get("单季归母净利润_亿元"))
    q_prev_np = _f(series[-2].get("归母净利_亿元")) if len(series) >= 2 else None
    ytd_np_yoy = _f(ytd.get("累计归母净利同比_pct"))
    note = (
        f'要把两件事分开看：近 10 年营业总收入复合 {_cagr(sales10)}、近 5 年只有 '
        f'{_cagr(sales5)}，规模曲线在 2023 年见顶后走平；'
        f'收入端最新单季同比 {bx._pct_plain(q_rev)} 还是正的，'
        f'但单季归母净利从上一季的 {bx._n(q_prev_np, 1, " 亿")} 掉到 '
        f'{bx._n(q_np, 1, " 亿")}，上半年累计归母净利同比 '
        f'{bx._pct_plain(ytd_np_yoy)} —— 收入与利润的方向并不一致。'
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
        f'{bx._esc(qr.get("报告期") or "—")} · 单季走势与上半年累计</span></div>'
        f'<div class="dt">{qtr_head}{qtr_rows}</div>'
        + ytd_line
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
        f'三个数字里股息率的分位方向与 PE / PB 相反：股息率 = 每股分红 ÷ 股价，'
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
    # ⚠️ PR 给 26：右侧刻度从 x=W-PR+2 起、text-anchor=start，两位数的「70%」放得下；
    #    若上界涨到三位数（如 110%），PR=12 会溢出 viewBox 右边界被 SVG 裁成「11C」。
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
            f'text-anchor="end">{v:.1f}</text>'
        )

    svg = (
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="每股股息与分红比例历史">'
        + "".join(bars) + line + dots + "".join(ticks) + "".join(labels) + "</svg>"
    )

    last, first = hist[-1], hist[0]
    lo_i = min(hist, key=lambda h: _f(h.get("payout_pct"), 999))
    hi_i = max(hist, key=lambda h: _f(h.get("payout_pct"), -999))
    # 「有没有哪一年下调过」由数据现算，不写死结论 —— 换标的后这句话必须跟着变。
    never_cut = all(_f(hist[i]["dps"], 0.0) <= _f(hist[i + 1]["dps"], 0.0)
                    for i in range(len(hist) - 1))
    cut_txt = ("每股分红从 " + bx._n(first.get("dps"), 2, " 元") + "走到 "
               + bx._n(last.get("dps"), 2, " 元") + "，这十年里没有一年下调。"
               if never_cut else
               "每股分红从 " + bx._n(first.get("dps"), 2, " 元") + "走到 "
               + bx._n(last.get("dps"), 2, " 元") + "，中间有过下调。")

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
        # 🔴 这句话的数字全部从数据取，形态也按数据说。伊利的比例形态是「长期钉在
        #    70% 一线、2024 年冲到 91.3%、2025 年回到 75.5%」—— 既不是逐级上台阶
        #    （茅台的形态），也不是格力那种 29%→108% 的剧烈摆动。按数据现算，不套模板。
        + bx._note(
            f'分红比例这十年大体在 70% 一线：最低 {bx._n(lo_i.get("payout_pct"),1,"%")}'
            f'（{lo_i.get("year")}）、最高 {bx._n(hi_i.get("payout_pct"),1,"%")}'
            f'（{hi_i.get("year")}），{last.get("year")} 年是 '
            f'{bx._n(last.get("payout_pct"),1,"%")}。{cut_txt}'
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

    sw_facts = qr.get("主要变动指标") or {}
    period_ytd = sw_facts.get("报告期") or qr.get("报告期") or "本期"

    from src.data.adapter import build_annual_financials, load_raw
    ann = build_annual_financials(load_raw(CODE))
    ann = ann[ann["report_date"].dt.month == 12].sort_values("report_date")
    hist = [(int(r["report_date"].year), _f(r["ocf"]), _f(r["net_profit_parent"]))
            for _, r in ann.tail(3).iterrows()]

    def _ratio(v):
        """现金含量 → 「1.70×」；缺数写 —（不写「—×」那种半截串）。"""
        return f"{bx._n(v, 2)}×" if v is not None else "—"

    # 双序列分组柱：深色 = 经营现金流净额、浅色 = 归母净利润。
    # 归一化基准取两组里的最大值 → 两根柱可直接比高矮（浅色高过深色 = 现金含量 > 1）。
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
    prev_ratio = None
    if len(hist) >= 2 and hist[-2][1] and hist[-2][2]:
        prev_ratio = hist[-2][1] / hist[-2][2]

    head = (
        '<div class="cf-head">'
        f'<div class="cf-h-l">{bx._esc(period_ytd)} 累计经营现金流净额</div>'
        f'<div class="cf-h-n">{bx._n(ocf_h1, 1, " 亿")}</div>'
        f'<div class="cf-h-y">同比 {bx._yoy(ytd.get("累计经营现金流同比_pct"))}</div>'
        # 第二行把「同期归母净利润」与「现金含量」放上图：解读句要用这两个数。
        f'<div class="cf-h-x">同期归母净利润 <b>{bx._n(np_h1, 1, " 亿")}</b>'
        f'<span class="cf-h-sep">·</span>现金含量 <b>{bx._n(cover_h1, 2)} 倍</b></div>'
        "</div>"
    )

    # ⚠️ 年份不能过 _n()：它会给 2025 加千分位变成「2,025」。
    last_yr = latest.get("latest_year") or hist[-1][0]
    prev_year = hist[-2][0] if len(hist) >= 2 else None
    # 「资产减值损失」本期值**从候选榜取**，不硬编码。
    # 🔴 这条依赖数据层已修好（`fields.IMPAIRMENT_FIELDS` 合并新旧准则字段）：
    #    修复前它在 raw 里 2018Q2 起全空、永远进不了榜，取回来必然是 None。
    #    取不到就不写这句（而不是写「— 亿」），措辞由 `_imp_tail` 决定。
    imp_amt = next((c.get("本期_亿元") for c in
                    (sw_facts.get("候选") or [])
                    if c.get("名称") == "资产减值损失"), None)
    # 🔴 这里的每一个数字都必须能在图上找到（柱标的数、年份下方的倍数、头部两行、
    #    变动项表的本期列）。措辞保持中性：「2.57 倍」是三年里最高的一年，
    #    写「只有 2.57 倍」会读成它最低。
    seq = []
    if prev_ratio is not None:
        seq.append(f"{prev_year} 年 {bx._n(prev_ratio, 2)} 倍")
    seq.append(f"{last_yr} 年 {bx._n(cover, 2)} 倍")
    seq.append(f"2026 上半年 {bx._n(cover_h1, 2)} 倍")
    # ⚠️ 别再复述图例那半句（「深色柱是…浅色是…」）—— `.legend2` 就在柱图正下方，
    #    图注里重复一遍会白白吃掉两行高度，而这张图是整组里最高的。
    # 末句接到「净利为什么下滑」上：上面的变动项表已经给出了答案（资产减值损失
    # +21.2 亿、商誉 -17.2 亿），图注不复述原因，只把两处连起来 —— 否则读者看到
    # 「归母净利 -20.0%」会停在半路，得自己回头找。
    imp_tail = ""
    if imp_amt is not None:
        imp_tail = ("净利下滑的主因是当期计提资产减值损失 "
                    + bx._n(imp_amt, 1, " 亿") + "（明细见上表）。")
    note = (
        "现金含量 " + "、".join(seq) + "，逐年并不一样。2026 上半年净额本身同比 "
        + f"{bx._pct_plain(_f(ytd.get('累计经营现金流同比_pct')))}"
        + " 是低基数上的回升，同期归母净利同比 "
        + f"{bx._pct_plain(_f(ytd.get('累计归母净利同比_pct')))}，不是同步在涨；"
        + imp_tail
    )

    body = (
        head
        + f'<div class="sec-t">现金流 vs 利润 <span class="sec-s">全年口径 · 亿元</span></div>'
        + f'<div class="chart"><div class="cash">{bars}</div>'
        + '<div class="legend2">深色 = 经营现金流净额　浅色 = 归母净利润　倍数 = 现金含量</div></div>'
        # 取 1 + 2 + 1 = 4 行。这张图已经有头部 + 柱图 + 解读句三块，榜单再加到 5 行
        # 会把整图长宽比顶到 1:2.89（超过全组上限 1:2.8）。
        #
        # 分组配额是**按这张图的叙事**定的，不是「每组都取两条」：
        #   · 损益只取 1 条 —— 取 2 条时「资产减值损失 +21.19 亿」与「扣非归母净利
        #     -14.2 亿」是一因一果（后者就是前者造成的），并排两行等于说两遍；
        #   · 资产负债取 1 条「商誉」—— 账面商誉本期从 23.52 亿掉到 6.33 亿，
        #     与资产减值损失互为印证，读者能顺着看到「减值从哪来」；
        #   · 现金流两条：经营活动已在头部讲过，投资与筹资够说明「钱去哪了」。
        # 🔴 这个配额依赖候选榜里「资产减值损失」入榜 —— 它 2026-09-24 才被修好
        #    （见 _SWING_WHY 顶部的说明）。若哪天它掉出榜单，`_swing_table` 会少取一行，
        #    图会变矮（不会画错），但叙事会缺掉「利润为什么下滑」这一环。
        + _swing_table(qr, {"损益": 1, "现金流": 2, "资产负债": 1}, _SWING_WHY)
        + bx._note(note)
    )
    foot = ("⚠️ 口径：柱状图为最近三个完整年度，与 2026 上半年累计不可直接比；"
            "现金含量 = 经营现金流净额 ÷ 归母净利润；「主要变动项」中损益与现金流"
            "为年初至今累计口径、资产负债为季末时点；变动原因取自 2026 半年报原文")
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
            .format(i, bx._esc(_tidy(r)))   # 纯格式统一，见 _tidy 的说明
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
    if bs.get("总资产_亿元") is not None:
        # 时点日不在 `季末资产负债` 里，取候选榜的同名键（它带 2026-06-30）。
        _spot = (qr.get("主要变动指标") or {}).get("时点日") or "最近一期"
        facts.append(
            f'资产结构（{_spot} 季末）：总资产 {bx._n(bs.get("总资产_亿元"), 0, " 亿")}，'
            f'归母净资产 {bx._n(bs.get("归母净资产_亿元"), 0, " 亿")}，'
            f'货币资金 {bx._n(bs.get("货币资金_亿元"), 0, " 亿")}'
        )
    # 收入三年序列：图 9 的风险条目讲「2024 年收入下滑至 1157.8 亿」，这三个数字
    # 必须出现在图上，读者才能自己核 —— 它也是「增长停滞」这个说法最直接的对照。
    recent = nd.get("recent") or []
    if len(recent) >= 3:
        tail = recent[-3:]
        facts.append(
            '收入与利润：' + " → ".join(
                f'{r.get("year")} 年营收 {bx._n(r.get("revenue"), 1, " 亿")}' for r in tail
            )
            + f'，{nd.get("latest_year")} 年归母净利 {bx._n(latest.get("net_profit"), 1, " 亿")}'
        )
    else:
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
    foot = f"全部数字可在 ValueLine 一页研报（{CODE}）中逐项核对"
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
    """截图并直接落成 `01_封面.png` 形态 —— 手机相册里带序号的裸文件名最稳。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright，请先执行：pip install playwright && playwright install chromium")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_xhs_yili_tmp.html"
    tmp.write_text(html_text, encoding="utf-8")
    uri = tmp.resolve().as_uri()

    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 1400},
                                device_scale_factor=3)
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
    ap = argparse.ArgumentParser(description="伊利股份 → 小红书轮播图")
    ap.add_argument("-o", "--out", default=OUT_DEFAULT,
                    help="输出目录（默认即发布包目录，图按 01_… 命名）")
    ap.add_argument("--html-only", action="store_true", help="只写 HTML，不截图")
    args = ap.parse_args()

    d, nar = _load(CODE)
    if not nar:
        print(f"⚠️ 未读到叙事缓存 data/cache/narrative/{CODE}.json —— 徽章与风险段会退化")

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
