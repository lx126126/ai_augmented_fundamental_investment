#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ValueLine 一页研报。

形态：纵向长图（小程序上下滑动 / 小红书笔记），宽度固定 1080px、高度自适应。
数据来源：优先从 parquet 读取真实财报（src/data/adapter.py），无数据时降级为示例数据。
输出：templates/valueline.html（预览） + reports/{报告期}/601088.html（归档）。

用法：
    python scripts/build_valueline.py [股票代码] [--daily] [--refresh-narrative]

    --daily              只刷新行情/估值板块，跳过 PDF 校验与 LLM 叙事
    --refresh-narrative  忽略叙事层缓存，强制重新调用 LLM 生成
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 尝试导入数据适配层（可选，无 parquet 数据时降级为示例数据）
try:
    from src.data.adapter import build_template_data, _norm_code, _pct as _share_pct
    _HAS_DATA = True
except Exception:
    _HAS_DATA = False
    _norm_code = None

# 尝试导入 LLM 叙事层生成（可选，无 key 时降级为占位）
try:
    from src.report.llm import generate_narrative
    _HAS_LLM = True
except Exception:
    _HAS_LLM = False

# 尝试导入季度财报解读（可选，无 key / 无网络时降级为占位）
try:
    from src.report.quarterly_review import get_or_generate as get_quarter_review
    _HAS_QREVIEW = True
except Exception:
    _HAS_QREVIEW = False

# 示例数据（仅降级用；真实渲染用 adapter 从 parquet 读取）
from _sample_data import (
    SAMPLE_YEARS,
    SAMPLE_FINANCIALS,
    SAMPLE_QUARTER_LABELS,
    SAMPLE_QUARTERLY,
    SAMPLE_SEGMENTS,
)


def _env(name: str, default: str | None = None) -> str | None:
    """读环境变量，缺失时回退读仓库根目录 .env（与 src/report/llm._load_config 同口径）。"""
    val = os.environ.get(name)
    if val is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{name}="):
                    val = line.split("=", 1)[1].strip()
                    break
    val = (val or "").strip()
    return val or default


def _verifier_row() -> str:
    """校验人署名。

    ⚠️ 此前该行硬编码真实姓名，而报告会用于公开发布（小红书 / 小程序），等于在
    每份对外材料上署名泄露真实身份。改为从 REPORT_VERIFIER 读取；未配置时
    整行不渲染（隐私安全的默认值）。
    """
    name = _env("REPORT_VERIFIER")
    return f'<div><b>校验人：</b>{name}</div>' if name else ""

# 当前渲染用的数据（默认示例，build() 时若 parquet 存在则被真实数据覆盖）
YEARS = SAMPLE_YEARS
FINANCIALS = SAMPLE_FINANCIALS
QUARTER_LABELS = SAMPLE_QUARTER_LABELS
QUARTERLY = SAMPLE_QUARTERLY
SEGMENT_LABELS = SAMPLE_QUARTER_LABELS  # 示例时分业务用季度标签；真实数据用半年度标签
SEGMENTS = SAMPLE_SEGMENTS
VALUATION = None  # 估值面板（真实数据时由 adapter 提供）
GRAHAM = None     # 格雷厄姆体检（真实数据时由 adapter 提供）
RATING = None     # 机构评级分布（真实数据时由 adapter 提供）
FRAUD = None      # 财务造假检测（真实数据时由 adapter 提供）
COMPETITION = None  # 竞争地位（行业排名/营收份额，真实数据时由 adapter 提供）
BUSINESS_MAP = None  # 业务版图（主营业务一句话 + 各业务收入占比，真实数据时由 adapter 提供）
CURRENT_POSITION = None  # 流动状况（流动资产 vs 流动负债明细，ValueLine Current Position）
ANNUAL_RATES = None      # 年增长率（销售/现金流/盈利/股息/账面价值 CAGR，ValueLine Annual Rates）
PIE_DATA = None          # 构成饼图（最新年报五大类子科目构成，真实数据时由 adapter 提供）
COMPANY_NAME = "中国神华"  # 公司名（真实数据时由 adapter 提供）
COMPANY_CODE = "601088"    # 股票代码
NARRATIVE = None           # LLM 叙事层（真实数据时由 generate_narrative 生成）
QUARTER_REVIEW = None      # 季度财报解读（定期报告原文 + 单季事实 → DeepSeek）
OPERATING = None           # 经营结构：分产品/渠道/地区 收入占比·毛利率·同比 + 年度经营计划
RECONCILE_LOG = []         # 数据交叉校验覆盖记录（官方年报 PDF 修正接口错误字段）
SANITY = None              # 业务勾稽体检结果（会计恒等式/利润勾稽/比率边界/同比异常）
CURRENCY_NOTE = ""         # 货币口径说明（港股标的标注：财务人民币，股价/市值港元）
VAL_CURRENCY_HINT = ""     # 估值面板 PE/PB 币种提示（港股：港元市值÷人民币财务）


# 构成饼图调色板（20 色，覆盖最多的「流动负债」子科目数）
PIE_PALETTE = [
    "#378ADD", "#E24B4A", "#BA7517", "#5B8FF9", "#F6903D", "#61A0A8", "#9270CA",
    "#2F9E6E", "#C9557A", "#7A8B99", "#D4A017", "#4FB0C6", "#8A6FBF", "#6B9E5A",
    "#E8797C", "#5C8A8A", "#C79A3B", "#7B6FA0", "#A06B5B", "#5AA08B",
]


def _is_hk(code: str) -> bool:
    """判断是否港股标的（带 .HK 后缀，或 0 开头 5 位码）。"""
    c = str(code).upper()
    if c.endswith(".HK"):
        return True
    bare = c.split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, str):
        return v  # 已是格式化字符串（来自 adapter）
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


def build_quarter_table() -> str:
    head = "".join(f"<th>{q}</th>" for q in QUARTER_LABELS)
    rows = []
    for group, name, vals in QUARTERLY:
        if group:
            rows.append(f'<tr class="group"><td colspan="{len(QUARTER_LABELS) + 1}">{group}</td></tr>')
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


def build_segments() -> str:
    if not SEGMENTS:
        return ('<div style="font-size:11px;color:var(--faint);padding:8px 0;">'
                '分业务收入构成数据待接入（当前数据源暂未覆盖该标的）。</div>')
    n_periods = len(SEGMENTS[0][2]) if SEGMENTS else 0

    # 各期收入占比（该条线收入 / 当期总收入 × 100；极小占比保留 2 位避免显示成 0）
    shares = []
    for _name, _color, revs, _margins in SEGMENTS:
        s = []
        for i in range(n_periods):
            total = sum((seg[2][i] or 0) for seg in SEGMENTS)
            s.append(_share_pct(revs[i], total))
        shares.append(s)

    # 单一表：每个报告期下分「收入 / 占比 / 利润率」三列
    # 表头两层：第一层 report_date（colspan=3），第二层 收入(亿元)/占比(%)/利润率(%)
    # 报告期/列名均为文字标签，居中显示（数字右对齐由 td.num 控制，表头文字居中更整齐）
    head1 = '<th class="name" rowspan="2">业务条线</th>'
    head2 = ""
    for q in SEGMENT_LABELS:
        head1 += f'<th colspan="3" style="text-align:center">{q}</th>'
        head2 += '<th style="text-align:center">收入</th><th style="text-align:center">占比</th><th style="text-align:center">利润率</th>'
    rows = []
    for (name, color, revs, margins), share_vals in zip(SEGMENTS, shares):
        cells = [f'<td class="row-head"><span class="seg-dot" style="background:{color}"></span>{name}</td>']
        for i in range(n_periods):
            rev = revs[i]
            sh = share_vals[i]
            mg = margins[i]
            rev_txt = _fmt(rev) if rev is not None else "—"
            sh_txt = _fmt_pct_val(sh)
            mg_txt = f"{mg:.1f}" if mg is not None else "—"
            cells.append(f'<td class="num">{rev_txt}</td>')
            cells.append(f'<td class="num">{sh_txt}</td>')
            cells.append(f'<td class="num">{mg_txt}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    table = (
        '<div class="table-scroll">'
        f'<table class="dense"><thead><tr>{head1}</tr><tr>{head2}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        "</div>"
    )

    # 最新报告期收入占比（堆叠条，None 视为 0）
    latest = [s[2][-1] if s[2][-1] is not None else 0 for s in SEGMENTS]
    total = sum(latest)
    if total > 0:
        bar = "".join(
            f'<div class="seg" style="width:{_fmt_pct_val(_share_pct(v, total))}%;background:{s[1]}"></div>'
            for s, v in zip(SEGMENTS, latest)
        )
        legend = "".join(
            f'<span class="seg-legend"><span class="seg-dot" style="background:{s[1]}"></span>{s[0]} {_fmt_pct_val(_share_pct(v, total))}%</span>'
            for s, v in zip(SEGMENTS, latest)
        )
    else:
        bar = ""
        legend = ""

    return (
        f'<div class="seg-block seg-full">{table}</div>'
        f'<div class="seg-bar">{bar}</div>'
        f'<div class="seg-legend-row">{legend}<span class="seg-note">（最新报告期收入占比）</span></div>'
        f'<div class="seg-note" style="font-size:10px;color:var(--faint);margin-top:5px;">收入单位亿元；利润率口径随行业而异：金融业为利差率/利润率，制造业为毛利率（数据源披露口径）。</div>'
    )


def _donut_svg(items: list[dict], size: int = 140) -> str:
    """items: [{"name","value","pct"}] → SVG 环形图（纯 stroke-dasharray 扇形，从 12 点顺时针）。"""
    import math
    # 半径/线宽按画布等比缩放：多图并排后卡片变窄，需按 size 缩放且不溢出 viewBox
    r = size * 0.39
    stroke = size * 0.167
    C = 2 * math.pi * r
    cx = cy = size / 2
    circles = []
    offset = 0.0
    for i, it in enumerate(items):
        color = PIE_PALETTE[i % len(PIE_PALETTE)]
        seg_len = it["pct"] / 100.0 * C
        circles.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-dasharray="{seg_len:.2f} {C - seg_len:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx:.1f} {cy:.1f})"/>'
        )
        offset += seg_len
    return f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" style="flex-shrink:0">{"".join(circles)}</svg>'


def _pie_legend(items: list[dict], deductions: list[dict] | None = None) -> str:
    rows = []
    for i, it in enumerate(items):
        color = PIE_PALETTE[i % len(PIE_PALETTE)]
        rows.append(
            f'<span class="pie-lg"><span class="pie-dot" style="background:{color}"></span>'
            f'<span class="pie-nm">{it["name"]}</span><b>{it["value"]:.1f}</b><i>{it["pct"]:.1f}%</i></span>'
        )
    for d in (deductions or []):
        rows.append(
            f'<span class="pie-lg pie-deduct"><span class="pie-dot" style="background:#c8ced6"></span>'
            f'<span class="pie-nm">{d["name"]}</span><b>{d["value"]:.1f}</b><i>抵减</i></span>'
        )
    return '<div class="pie-legend">' + "".join(rows) + "</div>"


def build_pie() -> str:
    """构成饼图：最新年报五大类子科目构成（营业总成本/流动资产/非流动资产/流动负债/非流动负债）。"""
    if not PIE_DATA:
        return (
            '<div class="sub-title">最近年度报告主要科目构成</div>'
            '<div style="font-size:11px;color:var(--faint);padding:8px 0;">'
            '该标的资产负债科目体系特殊（金融/银行）或子科目明细未接入，暂无构成饼图。</div>'
        )
    year = PIE_DATA.get("year")
    cards = []
    for g in PIE_DATA.get("groups", []):
        items = g.get("items") or []
        if not items:
            continue
        cards.append(
            '<div class="pie-card">'
            f'<div class="pie-title">{g["title"]}构成 <span class="pie-total">合计 {g["total"]:.1f} 亿元</span></div>'
            f'<div class="pie-body">{_donut_svg(items)}{_pie_legend(items, g.get("deductions"))}</div>'
            "</div>"
        )
    if not cards:
        return ""
    year_txt = f"{year} 年报" if year else "最新年报"
    return (
        f'<div class="sub-title">最近年度报告（{year_txt}）主要科目构成</div>'
        + '<div class="pie-row">' + "".join(cards) + "</div>"
        + '<div style="font-size:10px;color:var(--faint);margin-top:6px;">'
        '环形图为各科目金额（亿元）及占已列科目加总比例；「其他」为已列科目与总额的差额（含未单列明细；港股标的资产负债表子科目明细暂未接入）。'
        "</div>"
    )


def _pct_text(pct):
    """估值分位 → (百分比文本, css类, 定性)。"""
    if pct is None:
        return "—", "", "—"
    if pct < 30:
        return f"{pct:.0f}%", "pct-low", "偏低"
    if pct > 70:
        return f"{pct:.0f}%", "pct-high", "高位"
    return f"{pct:.0f}%", "", "合理"


def _fmt_pct_val(p):
    """占比数值 → 文本（≥1% 保留 1 位，<1% 保留 2 位，避免极小占比显示成 0）。"""
    if p is None:
        return "—"
    return f"{p:.2f}" if p < 1.0 else f"{p:.1f}"


def build_val_grid() -> str:
    if not VALUATION:
        return '<div style="font-size:11px;color:var(--faint);padding:8px 0;">估值数据待接入（行情接口受网络限制）。</div>'
    v = VALUATION
    # 总市值（亿元；港股为港元。万亿以上转「万亿」更易读）
    mcap = v.get("market_cap")
    if mcap is not None:
        if mcap >= 10000:
            mcap_txt = f"{mcap/10000:.2f}<small>万亿</small>"
        else:
            mcap_txt = f"{mcap:.0f}<small>亿</small>"
    else:
        mcap_txt = "—"
    pe = f"{v['pe']:.1f}<small>x</small>" if v.get("pe") else "—"
    pb = f"{v['pb']:.2f}<small>x</small>" if v.get("pb") else "—"
    dy = f"{v['dividend_yield']:.1f}<small>%</small>" if v.get("dividend_yield") else "—"
    pe_pct, pe_cls, _ = _pct_text(v.get("pe_pctile"))
    pb_pct, pb_cls, _ = _pct_text(v.get("pb_pctile"))
    dy_pct, dy_cls, _ = _pct_text(v.get("dividend_pctile"))

    def _fmt_dt(x, fmt: str) -> str | None:
        return x.strftime(fmt) if hasattr(x, "strftime") else None

    # 价格口径标注：盘中价 / 收盘价。此前恒标「最新收盘」，但行情快照可能取自交易时段内
    # （实测 600519 快照时间为 11:55），把盘中价标成「收盘」是对外可被证伪的错误。
    intraday = v.get("is_intraday")
    hhmm = _fmt_dt(v.get("quote_time"), "%H:%M")
    if intraday and hhmm:
        price_note = f"盘中价 {hhmm}"
    else:
        _d = _fmt_dt(v.get("price_now_date") or v.get("quote_date"), "%m-%d")
        if _d is None:
            price_note = "最新价"
        else:
            price_note = f"{'收盘价' if intraday is False else '最新价'} {_d}"

    # 股息率口径 = 最近年度分红总额 ÷ 当前市值，与 PE/PB 同分母（当前市值），
    # 所以它能像 PE/PB 一样给历史分位。年度必须标出来：2026 中报分红行存在但为空，
    # 不标年度会让人误读成「2026 年股息率」。
    _dy_year = v.get("dividend_year")
    dy_lbl = f"股息率（{_dy_year} 年度）" if _dy_year else "股息率"
    dy_note = f"近10年分位 {dy_pct}" if dy_pct != "—" else "分位 —"

    # 每股股息卡片：把「每股股息 / 分红总额 / 分红比例」三个口径放在一起，
    # 单看股息率无法判断是「分红多」还是「股价跌下来的」。
    dps = v.get("dividend_per_share")
    dps_txt = f"{dps:.2f}<small>元/股</small>" if dps else "—"
    _dt, _dp = v.get("dividend_total"), v.get("dividend_payout_pct")
    _bits = []
    if _dt:
        _bits.append(f"分红总额 {_dt:.1f} 亿")
    if _dp:
        _bits.append(f"分红比例 {_dp:.1f}%")
    dps_note = " · ".join(_bits) if _bits else "—"

    return (
        '<div class="val-grid">'
        f'<div class="val-item"><div class="lbl">总市值</div><div class="v">{mcap_txt}</div><div class="pct">{price_note}</div></div>'
        f'<div class="val-item"><div class="lbl">市盈率 PE（TTM）</div><div class="v">{pe}</div><div class="pct {pe_cls}">近10年分位 {pe_pct}</div></div>'
        f'<div class="val-item"><div class="lbl">市净率 PB（MRQ）</div><div class="v">{pb}</div><div class="pct {pb_cls}">近10年分位 {pb_pct}</div></div>'
        f'<div class="val-item"><div class="lbl">{dy_lbl}</div><div class="v" style="color:var(--up)">{dy}</div><div class="pct {dy_cls}">{dy_note}</div></div>'
        f'<div class="val-item"><div class="lbl">每股股息</div><div class="v">{dps_txt}</div><div class="pct">{dps_note}</div></div>'
        "</div>"
    )


def build_pe_chart() -> str:
    """PE（TTM）近十年走势图：自绘 SVG，无外部依赖，可直接被 Playwright 导成 PNG/PDF。

    「PE 19.5 处近 10 年 1% 分位」是这份报告里最强的估值断言，但只给一个分位数，
    读者分不清是「十年低位横盘」还是「刚从高位砸下来」——走势图就是这条断言的证据。
    序列与 PE 分位同源（见 cleaner._pe_series），不会出现「图上是 25 倍、文案说 19.5 倍」。
    """
    if not VALUATION:
        return ""
    series = VALUATION.get("pe_chart") or []
    if len(series) < 8:
        return ""
    med = VALUATION.get("pe_median")
    rng = VALUATION.get("pe_range") or {}
    lo = rng.get("lo") if rng.get("lo") is not None else min(p["pe"] for p in series)
    hi = rng.get("hi") if rng.get("hi") is not None else max(p["pe"] for p in series)
    if hi <= lo:
        hi = lo + 1.0

    # 上下各留 10% 余量，避免极值点贴着边框
    span = hi - lo
    lo2, hi2 = lo - span * 0.10, hi + span * 0.10
    W, H, PL, PR, PT, PB = 960, 200, 54, 14, 16, 30
    pw, ph = W - PL - PR, H - PT - PB
    n = len(series)

    def _x(i: int) -> float:
        return PL + (i / (n - 1)) * pw

    def _y(val: float) -> float:
        return PT + (hi2 - val) / (hi2 - lo2) * ph

    pts = [(_x(i), _y(p["pe"])) for i, p in enumerate(series)]
    # 末点强制对齐「估值面板里的 PE」：面板的 PE 取自行情接口、序列末点是自算值，
    # 两者通常只差 0.0x，但一旦有差异，图上写 19.5x、面板写 20.3x 就成了同页矛盾。
    now_pe = VALUATION.get("pe") or series[-1]["pe"]
    pts[-1] = (pts[-1][0], _y(now_pe))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{PL:.1f},{PT + ph:.1f} " + line + f" {PL + pw:.1f},{PT + ph:.1f}"

    # 横向参考线：最低 / 中位数 / 最高（中位数为虚线，是「贵不贵」的锚）
    ticks = []
    for val, lab in ((hi, f"{hi:.0f}x"), (med, f"{med:.0f}x"), (lo, f"{lo:.0f}x")):
        if val is None:
            continue
        yv = _y(val)
        if not (PT - 1 <= yv <= PT + ph + 1):
            continue
        dash = ' stroke-dasharray="4 3"' if lab == f"{med:.0f}x" and med is not None else ""
        ticks.append(
            f'<line x1="{PL}" y1="{yv:.1f}" x2="{PL + pw:.1f}" y2="{yv:.1f}" '
            f'stroke="var(--line)" stroke-width="0.6"{dash}/>'
            f'<text x="{PL - 6}" y="{yv + 3:.1f}" text-anchor="end" font-size="11" '
            f'fill="var(--faint)">{lab}</text>'
        )

    med_lab = ""
    if med is not None:
        # 白描边（paint-order: stroke）给文字垫底：中位线横穿折线区，不垫底会读不清
        med_lab = (f'<text x="{PL + 6}" y="{_y(med) - 5:.1f}" font-size="11" '
                   f'fill="var(--muted)" stroke="#ffffff" stroke-width="3" paint-order="stroke">'
                   f'近十年中位数 {med:.1f}x</text>')

    # 现值：点 + 标签（标签右对齐并夹在绘图区内，避免贴边被裁）
    nx, ny = pts[-1]
    now_lab_y = max(PT + 11, ny - 11)
    now_pt = (
        f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="3.5" fill="var(--accent-2)"/>'
        f'<text x="{nx:.1f}" y="{now_lab_y:.1f}" text-anchor="end" font-size="12" '
        f'font-weight="600" fill="var(--accent)" stroke="#ffffff" stroke-width="3" '
        f'paint-order="stroke">现值 {now_pe:.1f}x</text>'
    )

    # 横轴：起点 / 终点（年月，避免只写年份无法判断跨度）
    d0, d1 = series[0]["d"], series[-1]["d"]
    xlab = (
        f'<text x="{PL}" y="{H - 10}" font-size="10" fill="var(--faint)">{d0.strftime("%Y-%m")}</text>'
        f'<text x="{PL + pw:.1f}" y="{H - 10}" text-anchor="end" font-size="10" '
        f'fill="var(--faint)">{d1.strftime("%Y-%m")}</text>'
    )

    return (
        '<div class="pe-chart">'
        '<div class="pe-title">PE（TTM）近十年走势'
        '<span class="pe-note">与「近10年分位」同一条序列 · 历史市值 ÷ 同期已披露年报归母净利</span></div>'
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="PE 十年走势，现值 {now_pe:.1f} 倍，中位数 {med:.1f} 倍">'
        f'<polygon points="{area}" fill="var(--accent-2)" fill-opacity="0.07"/>'
        + "".join(ticks)
        + med_lab
        + f'<polyline points="{line}" fill="none" stroke="var(--accent-2)" stroke-width="1.4"/>'
        + now_pt
        + xlab
        + "</svg></div>"
    )


def build_div_history() -> str:
    """分红历史：上下两块面板，共用同一条年份轴。

    上面板 = 分红比例（%，折线），下面板 = 每股股息（元，柱）。

    为什么拆成两块而不是挤在一张双轴图里（上一版的失败教训）：
    1. 上一版在柱顶又叠了「分红总额」数字。分红总额 = 每股股息 × 股本，而茅台股本十年未变，
       两条曲线形状完全重合，是纯粹的冗余编码；更要命的是它占据了柱顶位置，与右侧
       「现值 xx%」标注在最后一年直接压在一起（实测 651 与「现值 79.1%」重叠）。
    2. 同时把「比例」轴拉满到 0–100%，而数据只在 51–79% 之间活动，台阶被压成一条平线；
       下面板与上面板各自用贴合数据的量程，51.9% 的十年平坦段与 2024 年的跳动才分得开。
    3. 双轴图还有一个隐性代价：读者无法判断两根柱子的高度差对应右轴多少个百分点。
       拆成两块后每块只有一个量纲，不需要在脑子里做轴换算。

    分红总额不再是图形，改为写在标题右侧的一句话摘要——它是结论不是趋势，不必占图形。
    """
    if not VALUATION:
        return ""
    hist = [h for h in (VALUATION.get("dividend_history") or []) if h.get("dps")]
    if len(hist) < 3:
        return ""

    W = 960
    PL, PR = 46, 30
    pw = W - PL - PR
    PH = 74                      # 单块绘图区高度
    PT1 = 18                     # 上面板（分红比例）顶
    PT2 = PT1 + PH + 34          # 下面板（每股股息）顶，34 = 上面板轴标签 + 间隙
    XL = PT2 + PH + 17           # 年份标签基线
    H = XL + 8

    n = len(hist)
    base1 = PT1 + PH
    base2 = PT2 + PH

    def _x(c: int) -> float:
        return PL + (c + 0.5) / n * pw

    def _nice(v: float, step: float) -> float:
        return step * (int(v / step) + (1 if v % step else 0))

    # ---- 上面板：分红比例（%）----
    pcts = [h["payout_pct"] for h in hist if h.get("payout_pct")]
    p_min, p_max = (min(pcts), max(pcts)) if pcts else (0.0, 100.0)
    p_lo = max(0.0, (int((p_min - 6) // 5)) * 5)
    p_hi = _nice(p_max + 6, 5.0)
    if p_hi - p_lo < 20:
        p_hi = p_lo + 20

    def _yp(v: float) -> float:
        return base1 - (v - p_lo) / (p_hi - p_lo) * PH

    # ---- 下面板：每股股息（元）----
    dps_max = max(h["dps"] for h in hist)
    d_hi = _nice(dps_max * 1.10, 10.0)

    def _yd(v: float) -> float:
        return base2 - (v / d_hi) * PH

    # 年份轴（两块共用，只画一次）
    xlabels = "".join(
        f'<text x="{_x(c):.1f}" y="{XL}" text-anchor="middle" font-size="10" '
        f'fill="var(--faint)">{h["year"]}</text>'
        for c, h in enumerate(hist)
    )

    # 上面板网格 + 左刻度（%）
    grid1, tick1 = [], []
    for v in (p_lo, (p_lo + p_hi) / 2, p_hi):
        y = _yp(v)
        grid1.append(
            f'<line x1="{PL}" y1="{y:.1f}" x2="{PL + pw:.1f}" y2="{y:.1f}" '
            f'stroke="var(--line)" stroke-width="0.5" stroke-opacity="0.7"/>'
        )
        tick1.append(
            f'<text x="{PL - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9.5" '
            f'fill="var(--faint)">{v:.0f}%</text>'
        )

    # 下面板网格 + 左刻度（元）
    grid2, tick2 = [], []
    for v in (0, d_hi / 2, d_hi):
        y = _yd(v)
        grid2.append(
            f'<line x1="{PL}" y1="{y:.1f}" x2="{PL + pw:.1f}" y2="{y:.1f}" '
            f'stroke="var(--line)" stroke-width="0.5" stroke-opacity="0.7"/>'
        )
        tick2.append(
            f'<text x="{PL - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9.5" '
            f'fill="var(--faint)">{v:.0f}</text>'
        )

    # 上面板：折线（缺值断线，不插值——插值会凭空造出一个派息率）
    pts = [(c, h["payout_pct"]) for c, h in enumerate(hist) if h.get("payout_pct")]

    # 十年中位数（虚线位置）。放在标题里说明，而不是贴着虚线左侧写注释：
    # 左侧第一个数据点（2016 = 51.0%）的数值标签与虚线几乎同高，两段文本会直接叠在
    # 一起（实测截图里「十年中位数 51.9%」把「51.0%」压住了）。虚线已把「常态在哪」
    # 画出来，文字只需说明它是什么，放右上角不会碰到任何数据。
    med = None
    if pts:
        vals = sorted(v for _, v in pts)
        med = (vals[len(vals) // 2] if len(vals) % 2
               else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2)

    # 面板小标题（左上角）
    ptitle = (
        f'<text x="{PL}" y="{PT1 - 6}" font-size="10" font-weight="600" '
        f'fill="var(--muted)">分红比例（%）</text>'
        + (f'<text x="{PL + pw}" y="{PT1 - 6}" text-anchor="end" font-size="9.5" '
           f'fill="var(--faint)">虚线 = 十年中位数 {med:.1f}%</text>' if med is not None else "")
        + f'<text x="{PL}" y="{PT2 - 6}" font-size="10" font-weight="600" '
          f'fill="var(--muted)">每股股息（元）</text>'
    )
    poly = " ".join(f"{_x(c):.1f},{_yp(v):.1f}" for c, v in pts)
    line = (
        f'<polyline points="{poly}" fill="none" stroke="var(--warn)" '
        f'stroke-width="1.6" stroke-linejoin="round"/>' if poly else ""
    )
    dots = "".join(
        f'<circle cx="{_x(c):.1f}" cy="{_yp(v):.1f}" r="2.5" fill="var(--warn)"/>'
        for c, v in pts
    )

    # 十年平坦段的中位参考线（不写死 51.9%，换标的自适应）
    ref = ""
    if med is not None:
        my = _yp(med)
        ref = (
            f'<line x1="{PL}" y1="{my:.1f}" x2="{PL + pw:.1f}" y2="{my:.1f}" '
            f'stroke="var(--warn)" stroke-width="0.8" stroke-dasharray="4 3" '
            f'stroke-opacity="0.55"/>'
        )

    # 上面板：只在「比例发生变化」的年份标数（2016–2023 全等于 51.9%，标十个数字纯属噪音）
    plabels = []
    prev_v = None
    for c, v in pts:
        changed = prev_v is None or abs(v - prev_v) >= 0.5
        prev_v = v
        if not changed and c != len(hist) - 1:
            continue
        ly = _yp(v) + (14 if c == pts[-1][0] else -6)
        anchor = "end" if c >= n - 2 else "middle"
        dx = -4 if c >= n - 2 else 0
        plabels.append(
            f'<text x="{_x(c) + dx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="10" font-weight="600" fill="var(--warn)">{v:.1f}%</text>'
        )

    # 下面板：柱 + 每股股息数值（标在柱顶，是这块面板唯一的数字）
    bw = min(40.0, pw / n * 0.52)
    bars, dlabels = [], []
    for c, h in enumerate(hist):
        x = _x(c)
        y = _yd(h["dps"])
        last_one = c == n - 1
        bars.append(
            f'<rect x="{x - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
            f'height="{base2 - y:.1f}" rx="2" fill="var(--accent-2)" '
            f'fill-opacity="{"0.95" if last_one else "0.68"}"/>'
        )
        dlabels.append(
            f'<text x="{x:.1f}" y="{y - 4:.1f}" text-anchor="middle" font-size="9.5" '
            f'fill="var(--muted)">{h["dps"]:.2f}</text>'
        )

    yrs = f'{hist[0]["year"]}–{hist[-1]["year"]}'
    last = hist[-1]
    note = (
        f'{yrs} · 最新 {last["year"]} 分红总额 {last["total"]:.0f} 亿 · 比例 {last["payout_pct"]:.1f}%'
        if last.get("total") and last.get("payout_pct") else yrs
    )

    return (
        '<div class="div-hist">'
        f'<div class="dh-title">分红历史：比例台阶与每股股息'
        f'<span class="dh-note">{note}</span></div>'
        f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
        f'aria-label="分红历史（{yrs}）：上为分红比例、下为每股股息">'
        + "".join(grid1) + "".join(tick1)
        + "".join(grid2) + "".join(tick2)
        + ptitle + ref + line + dots + "".join(plabels)
        + "".join(bars) + "".join(dlabels)
        + xlabels
        + "</svg>"
        + '<div class="dh-legend">'
        '<span class="lg"><span class="sw line" style="background:var(--warn)"></span>分红比例（占归母净利，%）</span>'
        '<span class="lg"><span class="sw" style="background:var(--accent-2)"></span>每股股息（元）</span>'
        '<span class="lg"><span class="sw" style="background:var(--warn);height:2px;opacity:.55"></span>十年中位数参考线</span>'
        "</div></div>"
    )



def build_market_row() -> str:
    if not VALUATION:
        return ""
    v = VALUATION
    low, now, high = v.get("price_low"), v.get("price_now"), v.get("price_high")
    if low is None or high is None or now is None:
        return ""
    pos = (now - low) / (high - low) * 100 if high > low else 50
    # 股价数据日期（与头部「发布日期」一致）
    qd = v.get("quote_date")
    date_note = f'<span class="cp-note" style="font-weight:400;"> · 股价日期 {qd.isoformat()}</span>' if qd else ""
    # 高低点发生日期：给出日期才能被独立复核（此前只给数值，无法验证）
    def _d(x) -> str:
        return f' <span class="cp-note" style="font-weight:400;">{x.strftime("%m-%d")}</span>' if hasattr(x, "strftime") else ""
    lo_d, hi_d = _d(v.get("price_low_date")), _d(v.get("price_high_date"))
    # 口径标注：区间取自近一年日 K（默认前复权，与腾讯自选股一致；取不到除权日时降级不复权）
    src_note = v.get("price_range_src")
    src_txt = f'<span class="cp-note" style="font-weight:400;"> · {src_note}</span>' if src_note else ""

    # 复权口径脚注：不复权值 + 区间内除权明细。没有这一行，读者拿 App 里的不复权价
    # 来对（或反过来）就会认为数字错了——2026-02-06 那个高点 1568.00 / 1539.98 差 28.02，
    # 差的正是 2026-06-26 那次派息，必须写明。
    adj_events = v.get("price_adjust_events") or []
    raw_hi = v.get("price_high_raw")
    raw_lo = v.get("price_low_raw")
    note_html = ""
    if v.get("price_adjusted"):
        bits = ["口径：<b>前复权</b>（腾讯「减法」口径，已剔除分红除权造成的价格跳空）"]
        if raw_hi is not None and raw_lo is not None:
            bits.append(f"不复权区间 {raw_lo:.2f} ~ {raw_hi:.2f}")
        if adj_events:
            ev = "、".join(
                f'{e["ex_date"].strftime("%Y-%m-%d")} 每股派 {e["dps"]:.4f} 元' for e in adj_events
            )
            tot = sum(float(e["dps"]) for e in adj_events)
            bits.append(f"区间内除权 {len(adj_events)} 次、合计 {tot:.4f} 元/股（{ev}）")
        note_html = f'<div class="pr-note">{" · ".join(bits)}。</div>'
    elif src_note:
        note_html = '<div class="pr-note">口径：不复权（未取到除权除息日，无法计算前复权）。</div>'

    return (
        '<div class="market-row">'
        '<div class="price-range">'
        f'<div class="pr-title">52周价格区间（元）{src_txt}{date_note}</div>'
        f'<div class="pr-bar"><div class="pr-marker" style="left:{pos:.1f}%"></div></div>'
        '<div class="pr-labels">'
        f'<span>52周最低 <b>{low:.2f}</b>{lo_d}</span>'
        f'<span>现价 <b>{now:.2f}</b></span>'
        f'<span>52周最高 <b>{high:.2f}</b>{hi_d}</span>'
        "</div>"
        + note_html
        + "</div>"
        + build_consensus()
        + "</div>"
    )


def build_val_note() -> str:
    """估值口径脚注。

    「PE 19.5 处于近 10 年 1% 分位」是本报告里最强的断言，也最容易被专业读者质疑，
    所以必须把构造方法写在纸面上：现值取自行情接口，分位来自自建序列，两者分母口径
    不同，属近似值。不写清楚 = 不可复核。
    """
    if not VALUATION:
        return ""
    v = VALUATION
    start, n = v.get("val_series_start"), v.get("val_series_n")
    span = f"起点 {start.strftime('%Y-%m')}" if hasattr(start, "strftime") else "近 10 年"
    cnt = f"{n} 个交易日采样（周频）" if n else "日频采样"
    return (
        '<div class="val-note">'
        "口径说明：PE(TTM)·PB(MRQ) <b>现值</b>取自行情接口；<b>PE 历史分位</b>由「历史市值 ÷ "
        "同期已披露年报归母净利」构造序列计算，<b>PB 历史分位</b>直接用行情 PB 序列"
        f"（{span}，{cnt}）。<b>股息率</b> = 最近年度分红总额（每股股息 × 总股本）÷ 当前市值，"
        "与 PE/PB 同分母；<b>股息率分位</b>用同构序列计算，其中分红总额按「次年 4 月末起计入」"
        "处理（年报与股东大会决议的披露时点）。各分位序列的分母口径与现值不完全一致，分位为近似值。"
        "</div>"
    )


def build_consensus() -> str:
    """机构评级分布 + 预测每股收益（客观第三方数据）。"""
    if not RATING:
        return ('<div class="consensus">'
                '<div>机构评级（<b>数据待接入</b>）</div>'
                '<div style="font-size:10px;color:var(--faint);margin-top:4px;">机构评级与盈利预测数据源待接入。</div>'
                "</div>")
    r = RATING
    total = r.get("total") or 0
    cats = [
        ("买入", r.get("buy"), "#c0392b"),
        ("增持", r.get("overweight"), "#e67e22"),
        ("中性", r.get("neutral"), "#95a5a6"),
        ("减持", r.get("underweight"), "#2ecc71"),
        ("卖出", r.get("sell"), "#1e8e5a"),
    ]
    segs = []
    for label, val, color in cats:
        v = int(val) if val is not None else 0
        if v > 0:
            pct = v / total * 100 if total else 0
            segs.append(f'<span style="background:{color};width:{pct:.1f}%">{label} {v}</span>')
    bar = '<div class="rating-bar">' + "".join(segs) + "</div>" if segs else ""

    eps = r.get("eps_forecast", [])
    # 统一加 E 后缀：与已披露的实际 EPS 区分开，避免读者把机构预估当成既成事实
    eps_txt = " / ".join(
        f"{e['year']}E {e['eps']:.2f}" for e in eps if e.get("eps") is not None
    )
    eps_line = (
        f'<div style="margin-top:5px;">机构预测每股收益（E）：{eps_txt} 元</div>'
        if eps_txt else ""
    )

    # 目标价（港股经济通有，A 股无）
    tp = r.get("target_price")
    tp_line = ""
    if tp is not None:
        tp_line = f'<div style="margin-top:5px;">券商目标价均值：{tp:.2f}（第三方观点）</div>'

    return (
        '<div class="consensus">'
        f'<div>机构评级（近6个月 · <b>{int(total)}家</b>）</div>'
        + bar + eps_line + tp_line
        + '<div style="font-size:10px;color:var(--faint);margin-top:5px;">第三方机构观点汇总，非本人建议。</div>'
        "</div>"
    )


def build_graham_badge() -> str:
    """头部「格雷厄姆质量」徽章：评级 + 四项体检数据。

    这四项（资产负债率 / 流动比率 / 盈利稳定性 / 净现金）原本单独占一个面板，但都是
    「一句话说得完」的静态体检值，撑不起一块版面；并入头部徽章后，估值段只留真正需要
    解释的内容（估值分位、走势、分红），版面效率更高，也不影响信息完整性。

    评级词沿用 LLM 给的定性判断，括号里的依据改用体检四项原始数据——LLM 原来写的依据
    （ROE / 分红比例）与估值面板重复，换成这四项才不浪费一整块版面的信息。
    """
    g = GRAHAM or {}
    raw = (_narr(["graham_badge"]) or "").strip()
    rating = raw.split("（")[0].split("(")[0].strip() or "—"
    bits = []
    if g.get("debt_ratio") is not None:
        bits.append(f"资产负债率 {g['debt_ratio']:.1f}%")
    if g.get("current_ratio") is not None:
        bits.append(f"流动比率 {g['current_ratio']:.2f}")
    if g.get("profit_stable") is not None:
        bits.append("近5年连续盈利" if g["profit_stable"] else "近5年存在亏损")
    if g.get("net_cash") is not None:
        bits.append("净现金" if g["net_cash"] > 0 else "有息负债＞货币资金")
    return f"{rating}（{' · '.join(bits)}）" if bits else rating


def _sanity_rows() -> str:
    """业务勾稽体检（会计恒等式 / 利润勾稽 / 比率边界 / 同比异常）在报告里的展示。"""
    if not SANITY:
        return ""
    ok_n = sum(1 for c in SANITY if c["ok"])
    fails = [c for c in SANITY if not c["ok"]]
    # 措辞用「待复核」而非「异常」：同比暴增可能是真实业务变化（如泡泡玛特 2025 净利 +309%），
    # 直接标「异常」会让读者误以为是数据错误。
    detail = "；".join(f'{c["check"]}（{c["detail"]}）' for c in fails[:3])
    warn = f'　<span style="color:var(--warn)">待复核：{detail}</span>' if fails else ""
    return f'<div><b>业务勾稽：</b>{ok_n}/{len(SANITY)} 项通过{warn}</div>'


def _stats_hint() -> str:
    """经营统计副标题：银行无流动/非流动划分，标题要跟着实际内容变，不能写死「流动状况」。"""
    t = (CURRENT_POSITION or {}).get("title") or ""
    left = "存贷结构" if "存贷" in t else "流动状况"
    return f"{left} · 年增长率"


def build_current_position() -> str:
    """经营统计左块：非金融=流动状况（流动资产 vs 流动负债 + 营运资本）；银行=存贷结构 + 存贷比。"""
    if not CURRENT_POSITION:
        return ""
    cp = CURRENT_POSITION
    year = cp.get("year")
    assets = cp.get("assets") or []
    liabs = cp.get("liabilities") or []
    title = cp.get("title") or "流动状况（Current Position）"

    def _row(label, val, bold=False):
        v = f"{val:.1f}" if val is not None else "—"
        cls = " total" if bold else ""  # 注意是类名而非整个属性，否则会拼出嵌套引号的坏 HTML
        return f'<div class="cp-row{cls}"><span>{label}</span><b>{v}</b></div>'

    def _col(title_, items):
        # 合计行 = 该列最后一行（非金融「流动资产/流动负债」，银行「资产总计/负债总计」）
        n = len(items)
        rows = [_row(label, val, bold=(i == n - 1)) for i, (label, val) in enumerate(items)]
        return f'<div class="cp-col"><div class="cp-col-title">{title_}</div>{"".join(rows)}</div>'

    is_bank = "存贷" in title
    a_col, l_col = ("资产", "负债") if is_bank else ("流动资产", "流动负债")

    # 底部汇总行：非金融=营运资本（亿元，正负着色）；银行=存贷比（%，恒正不着色）
    f = cp.get("footer") or {}
    f_val = f.get("value")
    digits = f.get("digits", 1)
    f_txt = f"{f_val:.{digits}f}" if f_val is not None else "—"
    f_cls = ""
    if not is_bank and f_val is not None:
        f_cls = ' class="pos"' if f_val > 0 else ' class="neg"'

    return (
        '<div class="current-pos">'
        f'<div class="cp-title">{title}<span class="cp-note">{year} 年报 · 单位：亿元</span></div>'
        '<div class="cp-grid">'
        + _col(a_col, assets)
        + _col(l_col, liabs)
        + "</div>"
        f'<div class="cp-wc">{f.get("label", "")}：<b{f_cls}>{f_txt}</b> {f.get("unit", "")}</div>'
        "</div>"
    )


def build_annual_rates() -> str:
    """年增长率（ValueLine Annual Rates）：销售/现金流/盈利/股息/账面价值 CAGR。"""
    if not ANNUAL_RATES:
        return ""
    # 科目名称与「年度全历史表」保持完全一致（原 ValueLine 美式叫法「销售收入/账面价值」
    # 与 A 股报表科目对不上，统一改用报表科目名）
    labels = [
        ("sales", "营业总收入"),
        ("cash_flow", "经营现金流净额"),
        ("earnings", "归母净利润"),
        ("dividends", "每股股息（元）"),
        ("book_value", "归母所有者权益"),
    ]
    rows = []
    for key, label in labels:
        r = ANNUAL_RATES.get(key)
        if not r:
            continue
        c5 = f"{r['cagr5'] * 100:+.1f}%" if r.get("cagr5") is not None else "—"
        c10 = f"{r['cagr10'] * 100:+.1f}%" if r.get("cagr10") is not None else "—"
        rows.append(f'<div class="ar-row"><span>{label}</span><b>{c5}</b><b>{c10}</b></div>')
    if not rows:
        return ""
    return (
        '<div class="annual-rates">'
        '<div class="ar-title">年增长率（Annual Rates）<span class="ar-note">复合年增长率 CAGR</span></div>'
        '<div class="ar-row ar-head"><span></span><b>近5年</b><b>近10年</b></div>'
        + "".join(rows)
        + "</div>"
    )


def _narr(path, default=""):
    """从 NARRATIVE 取嵌套字段，缺失返回 default。"""
    if not NARRATIVE:
        return default
    node = NARRATIVE
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node if node else default


def build_business_map() -> str:
    """业务版图（客观）：主营业务一句话（巨潮）+ 各业务条线收入占比文字（分业务构成）。

    与盈利来源/盈利结构/护城河（LLM 叙事）合并为一段连贯文字，避免内容重复。
    此处仅返回「主营业务 + 各业务占比」的数据文字，供 build_business_model 拼装。
    """
    if not BUSINESS_MAP:
        return ""
    bm = BUSINESS_MAP
    main = bm.get("main_business")
    segs = bm.get("segments") or []

    # 各业务条线占比（文字，如「消费电器 82.9%、工业制品 9.7%」）
    seg_txt = "、".join(
        f"{s.get('name', '')} {_fmt_pct_val(s.get('pct'))}%" for s in segs if s.get("pct") is not None
    )

    parts = []
    if main:
        main = main.rstrip("。").rstrip("，").strip()
        parts.append(f"主营业务为{main}")
    if seg_txt:
        parts.append(f"分业务收入占比：{seg_txt}")
    if not parts:
        return ""
    return "；".join(parts)


def build_business_model() -> str:
    """商业模式文字段：业务版图（客观占比） + 盈利来源/盈利结构/护城河（LLM 叙事）。

    合并成一段连贯文字（不再分行贴标签），避免「业务版图 / 盈利来源 / 盈利结构」
    三者内容重复。顺序：主营业务 → 各业务真实占比 → 盈利来源/结构 → 护城河。
    """
    bm = NARRATIVE.get("business_model", {}) if NARRATIVE else {}
    revenue_source = (bm.get("revenue_source") or "").strip()
    profit_structure = (bm.get("profit_structure") or "").strip()
    moat = (bm.get("moat") or "").strip()

    # 业务版图客观数据（主营业务 + 各业务占比）
    bizmap_txt = build_business_map()

    # 拼接为一段连贯文字：业务版图 → 盈利来源/结构 → 护城河
    segs = []
    if bizmap_txt:
        segs.append(bizmap_txt.rstrip("。"))
    for val in (revenue_source, profit_structure):
        if val:
            # 去掉 LLM 可能自带的前缀（「盈利来源：」「盈利结构：」），避免重复
            val = val.split("：", 1)[-1].strip() if "：" in val else val
            segs.append(val.rstrip("。"))
    if not segs:
        return '<div class="biz"><div class="biz-v" style="color:var(--faint);">商业模式待 LLM 生成</div></div>'

    # 一段正文 + 护城河（相对独立，加粗引出，仍属同一段落）
    body = "。".join(segs) + "。"
    if moat:
        moat = moat.split("：", 1)[-1].strip() if "：" in moat else moat
        body += f'<span style="color:var(--accent);font-weight:600;">护城河：</span>{moat}'
    return '<div class="biz"><div class="biz-v" style="font-size:12px;line-height:1.9;color:#33404f;">' + body + "</div></div>"


def build_competition() -> str:
    """竞争地位（客观数据）：行业营收排名 + 份额 + 同行对比。

    港股降级：无全市场营收排名接口，仅展示行业定位 + 公司介绍。
    """
    if not COMPETITION:
        return ""
    c = COMPETITION

    # 港股简化版：行业定位 + 公司介绍（无排名/份额/同行）
    if c.get("is_hk"):
        industry = c.get("industry") or "—"
        intro = c.get("company_intro") or ""
        intro_html = f'<div class="comp-intro">{intro}</div>' if intro else ""
        return (
            '<div class="competition">'
            f'<div class="comp-title">竞争地位 · {industry}'
            '<span class="comp-note">港股行业分类（恒生）</span></div>'
            + intro_html
            + '<div class="comp-note" style="margin-top:6px;">港股暂无全市场营收排名接口，'
              '仅展示行业定位与公司介绍，未做估算或替代口径。</div>'
            "</div>"
        )

    industry = c.get("industry") or "—"
    year = c.get("report_year")
    rank = c.get("rank")
    peers = c.get("peers_count")
    share = c.get("share_pct")
    revenue = c.get("revenue_yi")

    rank_txt = f"第 {rank} / {peers}" if (rank is not None and peers) else "—"
    share_txt = f"{share:.1f}%" if share is not None else "—"
    rev_txt = f"{revenue:.0f} 亿" if revenue is not None else "—"
    title = f"竞争地位 · {industry}" + (f"（{year} 年报）" if year else "")

    top = c.get("top_peers", [])
    max_rev = max((p.get("revenue_yi") or 0) for p in top) if top else 0
    bars = []
    for p in top:
        name = p.get("name", "")
        rv = p.get("revenue_yi")
        is_self = bool(p.get("is_self"))
        w = (rv / max_rev * 100) if (rv and max_rev) else 0
        cls = " self" if is_self else ""
        bars.append(
            f'<div class="peer-row{cls}">'
            f'<span class="peer-name">{name}</span>'
            f'<div class="peer-track"><div class="peer-bar" style="width:{w:.1f}%"></div></div>'
            f'<span class="peer-val">{rv:.0f}</span>'
            f'</div>'
        )

    return (
        '<div class="competition">'
        f'<div class="comp-title">{title}<span class="comp-note">营收口径：东财业绩报表</span></div>'
        '<div class="comp-grid">'
        f'<div class="comp-item"><div class="lbl">营收排名</div><div class="v">{rank_txt}</div></div>'
        f'<div class="comp-item"><div class="lbl">营收份额</div><div class="v" style="color:var(--accent-2)">{share_txt}</div></div>'
        f'<div class="comp-item"><div class="lbl">营收</div><div class="v">{rev_txt}</div></div>'
        "</div>"
        f'<div class="peer-list">{"".join(bars)}</div>'
        "</div>"
    )


def _quarter_review_hint() -> str:
    """季度解读段的副标题：标明依据的是哪一期报告，让读者知道这段在讲什么时间范围。"""
    meta = (QUARTER_REVIEW or {}).get("_meta") or {}
    kind = meta.get("kind") or "最新定期报告"
    return f"AI 摘要 · 依据 {kind}原文 + 单季财务数据"


def build_quarter_review() -> str:
    """季度财报解读：LLM 基于「最新定期报告原文 + 单季财务事实」生成的三段解读。

    原文取不到时（一季报/三季报通常没有管理层讨论章节）不隐藏这一段，而是把
    「未获取到原文」如实写出来——读者需要知道这段结论的证据强度有多少。
    """
    if not QUARTER_REVIEW:
        return ('<div style="font-size:11px;color:var(--faint);padding:8px 0;">'
                '季度财报解读待生成（需配置 DEEPSEEK_API_KEY，且能访问巨潮资讯网）。</div>')
    r = QUARTER_REVIEW
    meta = r.get("_meta") or {}

    def _p(key: str) -> str:
        txt = (r.get(key) or "").strip()
        return f"<p>{txt}</p>" if txt else '<p style="color:var(--faint)">—</p>'

    watch = r.get("watch") or []
    if isinstance(watch, str):
        watch = [watch]
    watch_html = "".join(f"<li>{w}</li>" for w in watch if str(w).strip())
    watch_block = (
        f'<ul class="qr-watch">{watch_html}</ul>' if watch_html
        else '<p style="color:var(--faint)">—</p>'
    )

    # 证据来源如实标注：有原文 / 无原文，是这段解读可信度的分水岭
    src_bits = []
    if meta.get("title"):
        src_bits.append(f'依据：{meta["title"]}')
    if meta.get("has_mdd"):
        src_bits.append(f'原文「管理层讨论与分析」{meta.get("mdd_chars", 0)} 字')
    else:
        note = meta.get("note") or "未获取到报告原文"
        src_bits.append(f'<span style="color:var(--warn)">{note}，管理层观点部分无原文支撑</span>')
    src_bits.append("由 DeepSeek 基于上述材料生成，AI 摘要非本人观点")

    return (
        '<div class="qrev">'
        '<div class="qr-grid">'
        f'<div class="qr-col"><div class="qr-h">季度数据表现</div>{_p("data_read")}</div>'
        f'<div class="qr-col"><div class="qr-h">经营结构解读</div>{_p("structure")}</div>'
        f'<div class="qr-col"><div class="qr-h">管理层观点与战略</div>{_p("management")}</div>'
        "</div>"
        + _cashflow_block(r)
        + f'<div class="qr-watch-wrap"><div class="qr-h">投资者需要关注</div>{watch_block}</div>'
        f'<div class="qr-foot">{" · ".join(src_bits)}</div>'
        "</div>"
        + build_operating_structure_block()
    )


def _cashflow_block(review: dict) -> str:
    """现金流异动归因：LLM 的科目级解释 + 一条口径对照条（总额 vs 剔除财务公司后）。

    对照条是硬性的一部分而不是装饰：市场对「经营现金流同比 +438%」的第一反应是
    「回款大幅改善」，而这类跳变在带财务公司的公司里十有八九来自吸收存款/缴存央行/
    同业拆放的搬动。把两个口径并排放在一句话里，读者不必读正文就知道该信哪个。
    """
    txt = (review.get("cashflow") or "").strip()
    ocf = (OPERATING or {}).get("现金流归因") or {}
    yoy = ocf.get("净额同比_pct")
    adj = ocf.get("剔除财务公司科目后") or {}
    strip = ""
    if ocf:
        bits = [f'{ocf.get("期间") or ""} 经营现金流净额 '
                f'{(ocf.get("经营活动产生的现金流量净额_亿元") or {}).get("本期", 0):,.1f} 亿元']
        if yoy is not None:
            cls = "up" if yoy > 0 else "down"
            bits.append(f'同比 <b class="{cls}">{yoy:+.1f}%</b>')
        if adj.get("同比_pct") is not None:
            cls2 = "up" if adj["同比_pct"] > 0 else "down"
            bits.append(
                f'剔除财务公司科目后 <b class="{cls2}">{adj["同比_pct"]:+.1f}%</b>'
                f'（{adj.get("上期_亿元", 0):,.1f} → {adj.get("经营性现金净额_亿元", 0):,.1f} 亿元）'
            )
        strip = f'<div class="cf-strip">{" · ".join(bits)}</div>'
    items = (ocf.get("主要变动科目") or [])[:4]
    rows = "".join(
        f'<tr><td class="cf-name">{s["科目"]}'
        + ('<span class="cf-tag">财务公司</span>' if s.get("是否财务公司科目") else "")
        + "</td>"
        f'<td class="op-num">{s.get("本期_亿元"):,.1f}</td>'
        f'<td class="op-num">{s.get("上期_亿元"):,.1f}</td>'
        f'<td class="op-num">{_op_chg(s.get("变动_亿元"))}</td></tr>'
        for s in items if s.get("本期_亿元") is not None
    )
    table = (
        '<table class="op-table cf-table"><thead><tr>'
        '<th>主要变动科目</th><th>本期（亿元）</th><th>上期（亿元）</th><th>变动（亿元）</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>'
    ) if rows else ""
    if not txt and not strip and not table:
        return ""
    return (
        '<div class="qr-cf-wrap">'
        '<div class="qr-h">现金流异动归因</div>'
        + (f'<p>{txt}</p>' if txt else "")
        + strip + table
        + "</div>"
    )


def _op_yoy(v) -> str:
    """同比着色：涨红跌绿（A 股习惯），并显式带正负号。"""
    if v is None:
        return '<span class="op-na">—</span>'
    cls = "op-up" if v > 0 else ("op-down" if v < 0 else "op-na")
    return f'<span class="{cls}">{v:+.1f}%</span>'


def _op_chg(v) -> str:
    """金额变动着色：与 _op_yoy 同色系，但单位是亿元、显式带正负号（不是百分比）。"""
    if v is None:
        return '<span class="op-na">—</span>'
    cls = "op-up" if v > 0 else ("op-down" if v < 0 else "op-na")
    return f'<span class="{cls}">{v:+,.1f}</span>'


def build_operating_structure_block() -> str:
    """经营结构表：分产品/渠道/地区的收入·占比·同比·毛利率，附年度经营计划。

    这张表存在的理由：只看合并利润表，读者无法回答「增长到底来自哪」。
    2026H1 茅台整体营收 +1.3%，但拆开看是直销 +29.9%、批发代理 -21.6%，
    渠道结构的位移才是当期最关键的事实——这类信息只存在于定期报告原文，接口里没有。
    """
    if not OPERATING:
        return ""
    op = OPERATING
    cuts = op.get("切片") or {}
    order = [c for c in ("产品", "渠道", "地区", "行业") if cuts.get(c)]
    if not order:
        return ""

    total = None
    for c in order:
        s = sum(r.get("收入_亿元") or 0 for r in cuts[c])
        if s:
            total = s
            break

    rows_html = []
    for cut in order:
        for i, r in enumerate(cuts[cut]):
            cut_cell = f'<td class="op-cut" rowspan="{len(cuts[cut])}">{cut}</td>' if i == 0 else ""
            mg = r.get("毛利率_pct")
            rows_html.append(
                "<tr>"
                + cut_cell
                + f'<td class="op-name">{r["名称"]}</td>'
                + f'<td class="op-num">{r["收入_亿元"]:,.1f}</td>'
                + f'<td class="op-num">{r["占比_pct"]:,.1f}%</td>'
                + f'<td class="op-num">{_op_yoy(r.get("收入同比_pct"))}</td>'
                + f'<td class="op-num">{f"{mg:.1f}%" if mg is not None else "—"}</td>'
                + "</tr>"
            )

    # 口径脚注：占比是「占酒类收入」而非占营业总收入；毛利率与收入不同报告期
    notes = []
    if total:
        notes.append(f"占比为占酒类收入（三切面合计 {total:,.1f} 亿元）的比例")
    if op.get("毛利率口径"):
        notes.append(
            f"毛利率来自 {op['毛利率口径']}，与本期收入（{op.get('期间')}）非同报告期，"
            "仅表示该切面的盈利水平"
        )

    extra = []
    if op.get("i茅台_亿元"):
        im = op["i茅台_亿元"]
        share = None
        for r in cuts.get("渠道", []):
            if r["名称"] == "直销" and r.get("收入_亿元"):
                share = im / r["收入_亿元"] * 100
        extra.append(
            f'"i 茅台"数字营销平台酒类不含税收入 <b>{im:,.1f} 亿元</b>'
            + (f"（占直销收入 {share:.1f}%）" if share else "")
        )
    dl = op.get("经销商") or {}
    if dl:
        bits = []
        for region in ("国内", "国外"):
            d = dl.get(region)
            if not d:
                continue
            s = f"{region} {d['期末_家']:,} 家"
            chg = []
            if d.get("增加_家") is not None:
                chg.append(f"+{d['增加_家']}")
            if d.get("减少_家") is not None:
                chg.append(f"-{d['减少_家']}")
            if chg:
                s += f"（期末较期初 {'/'.join(chg)}）"
            bits.append(s)
        if bits:
            extra.append("经销商 " + " · ".join(bits))

    plan = op.get("年度经营计划") or {}
    plan_html = ""
    if plan.get("要点") or plan.get("主题"):
        chips = "".join(f'<span class="op-chip">{t}</span>' for t in (plan.get("要点") or []))
        quant = plan.get("量化目标")
        if quant:
            quant_txt = f"量化目标：{quant}"
        else:
            quant_txt = "量化目标：公司未披露量化营收增长目标，仅给出上述方向性部署"
        plan_html = (
            '<div class="op-plan">'
            f'<div class="op-plan-h">{plan.get("年度") or ""} 年度经营计划'
            + (f'　主题：{plan["主题"]}' if plan.get("主题") else "")
            + "</div>"
            + (f'<div class="op-chips">{chips}</div>' if chips else "")
            + f'<div class="op-quant">{quant_txt}</div>'
            "</div>"
        )

    return (
        '<div class="operating">'
        f'<div class="op-title">经营结构 <span class="op-note">'
        f'来源：{op.get("报告") or "定期报告"}（{op.get("期间")}）</span></div>'
        '<table class="op-table"><thead><tr>'
        '<th>切片</th><th>名称</th><th>收入（亿元）</th><th>占比</th><th>收入同比</th><th>毛利率</th>'
        "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table>"
        + (f'<div class="op-foot">{"；".join(notes)}。</div>' if notes else "")
        + (f'<div class="op-foot">{" · ".join(extra)}。</div>' if extra else "")
        + plan_html
        + "</div>"
    )


def build_thesis() -> str:
    items = NARRATIVE.get("thesis", []) if NARRATIVE else []
    if not items:
        return '<ul class="thesis"><li>投资逻辑待 LLM 生成</li></ul>'
    return '<ul class="thesis">' + "".join(f"<li>{t}</li>" for t in items) + "</ul>"


def build_risks() -> str:
    items = NARRATIVE.get("risks", []) if NARRATIVE else []
    if not items:
        return '<ul class="risk"><li>风险提示待 LLM 生成</li></ul>'
    return '<ul class="risk">' + "".join(f"<li>{t}</li>" for t in items) + "</ul>"


def build_fraud() -> str:
    """财务造假检测：Beneish M-Score + 现金流背离 + 应收异常（客观算法）。"""
    if not FRAUD:
        return '<div class="fraud"><div class="f-row"><span>造假检测</span><b>数据待接入</b></div></div>'
    f = FRAUD
    rows = []

    m = f.get("mscore")
    if m:
        val = m["mscore"]
        risk = m["risk"]
        cls = "bad" if risk == "high" else "ok"
        label = "高风险" if risk == "high" else "安全"
        rows.append(f'<div class="f-row"><span>M-Score（Beneish）</span><b class="{cls}">{val:.2f} · {label}</b></div>')
    else:
        rows.append('<div class="f-row"><span>M-Score（Beneish）</span><b>数据不足</b></div>')

    cf = f.get("cashflow")
    if cf:
        ratios = " / ".join(f"{x:.1f}" if x is not None else "—" for x in cf["ratios"])
        cls = "bad" if cf["warning"] else "ok"
        txt = "背离" if cf["warning"] else "健康"
        rows.append(f'<div class="f-row"><span>经营现金流 / 净利润（近3年）</span><b class="{cls}">{ratios} · {txt}</b></div>')

    rc = f.get("receivable")
    if rc:
        cls = "bad" if rc["warning"] else "ok"
        txt = "背离" if rc["warning"] else "正常"
        rows.append(f'<div class="f-row"><span>应收增速 vs 营收增速</span><b class="{cls}">应收 {rc["ar_yoy"]:.1f}% vs 营收 {rc["rev_yoy"]:.1f}% · {txt}</b></div>')

    # 审计意见：A 股取东财资产负债表 OPINION_TYPE；港股该列不存在（数据源未覆盖），
    # 按「整行无数据则隐藏」处理——不显示「数据待接入」这种占位噪声，改在末尾统一说明覆盖范围。
    audit_op = f.get("audit_opinion")
    audit_level = f.get("audit_level")
    skipped = []
    if audit_op:
        cls = {"clean": "ok", "watch": "warn", "high": "bad"}.get(audit_level, "")
        rows.append(f'<div class="f-row"><span>审计意见</span><b class="{cls}">{audit_op}</b></div>')
    else:
        skipped.append("审计意见")

    overall = f.get("overall_risk", "low")
    cls = {"low": "ok", "medium": "warn", "high": "bad"}.get(overall, "ok")
    label = {"low": "低", "medium": "中", "high": "高"}.get(overall, "低")
    flags = f.get("flags", [])
    flag_txt = "（" + "、".join(flags) + "）" if flags else ""
    rows.append(f'<div class="f-score">综合造假风险：<b class="{cls}">{label}</b>{flag_txt}</div>')

    if skipped:
        rows.append(
            '<div class="f-note">未覆盖检测项：' + "、".join(skipped)
            + "（数据源未提供，未做估算，请自行查阅年报）</div>"
        )

    return '<div class="fraud">' + "".join(rows) + "</div>"


def build_verify() -> str:
    """数据校验记录：运行 validate()，与巨潮官方年报 PDF 逐项对比。"""
    from datetime import date
    today = date.today().isoformat()

    year = YEARS[-1] if YEARS else None
    if not year or not _HAS_DATA:
        return ('<div class="verify">'
                '<div><b>数据来源：</b>AKShare（主）+ 东方财富（备用）</div>'
                '<div><b>校验状态：</b>待运行（需 parquet 数据 + 官方年报）</div>'
                f'<div><b>校验日期：</b>{today}</div>'
                "</div>")

    try:
        from src.validation import validate
        result = validate(COMPANY_CODE, year)
        marks = {"一致": "✓", "差异": "✗", "缺失": "—", "接口缺失": "—"}
        rows = []
        for it in result["items"]:
            mark = marks.get(it["status"], "—")
            note = "一致" if it["status"] == "一致" else it["status"]
            rows.append(f'<div><b>{mark} {it["label"]}</b>：{note}</div>')

        # 数据交叉校验覆盖记录（接口原始值 vs 官方 PDF 金标准）
        reconcile_rows = ""
        if RECONCILE_LOG:
            items = "".join(
                f'<div>· {c["label"]}：接口 {c["api_yi"]:,.2f}亿 → 官方 {c["pdf_yi"]:,.2f}亿（差 {c["diff_pct"]:.0f}%）</div>'
                for c in RECONCILE_LOG
            )
            reconcile_rows = (
                '<div class="reconcile">'
                f'<div><b>⚠ 接口数据修正：</b>第三方接口（东财/新浪同源）在「同一控制下企业合并追溯重述」'
                f'情形下抓取错误，已用官方年报 PDF 金标准覆盖 {len(RECONCILE_LOG)} 项：</div>'
                + items
                + '<div style="margin-top:4px;">注：同一控制下企业合并会追溯重述比较期，公司通常只重述'
                  '最近 2 个比较年度；更早年份接口仍为<strong>重述前</strong>口径，'
                  '与近年的重述后口径不完全可比，同比与 CAGR 会受此影响。</div>'
                "</div>"
            )

        return (
            '<div class="verify">'
            '<div><b>数据来源：</b>AKShare（主）+ 东方财富（备用）；金标准：巨潮官方年报 PDF</div>'
            f'<div><b>校验结果：</b>{result["passed"]}/{result["total"]} 项与官方年报一致（容差 &lt;0.1%）</div>'
            + "".join(rows)
            + _sanity_rows()
            + reconcile_rows
            + f'<div><b>校验日期：</b>{today}</div>'
            + _verifier_row()
            + "</div>"
        )
    except Exception as e:
        # 港股无巨潮年报源，校验本就不适用——直接抛异常类型（KeyError）读者看不懂
        is_hk = len(str(COMPANY_CODE)) == 5
        status = ("不适用（港股年报源未接入，金标准校验暂覆盖 A 股）" if is_hk
                  else f"未运行（{type(e).__name__}）")
        # 业务勾稽体检不依赖官方 PDF，港股同样适用（且港股没有金标准兜底，更该展示）
        return ('<div class="verify">'
                '<div><b>数据来源：</b>AKShare（主）+ 东方财富（备用）</div>'
                f'<div><b>校验状态：</b>{status}</div>'
                + _sanity_rows()
                + f'<div><b>校验日期：</b>{today}</div>'
                "</div>")


# ============ CSS ============
CSS = """
/* ⚠️ 样式改这里，不要改 templates/valueline.html。
   构建脚本把下面这段 CSS 注入模板 head 的样式元素（占位符 @@CSS@@），
   再写到 templates/valueline.html 与 reports/<期>/<code>.html —— 那两个都是产物，
   直接改会被下一次构建整体覆盖，且完全不报错（页面只是少了新加的规则）。
   踩过一次：新板块的段落退回浏览器默认 16px，在一堆 11.5px 段落里格外显眼。

   🔴 本注释里绝对不能出现样式元素/脚本元素的「闭合标签字面量」。
   HTML 解析器在样式元素内只认那个字符串，一旦出现就会提前结束样式块，
   后面的整份 CSS 会被当成正文渲染 —— 页面照样能打开、不报错，只是样式全丢。
   （真实事故：为了写清用法在注释里引了闭合标签，结果整页字号回到 13px、
     宽表失去 table-layout:fixed 被撑到 1260px、长图导出右边多出一条白边。） */

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
  width: 100%;
  max-width: 1080px;
  margin: 0 auto;
  background: #ffffff;
  box-shadow: 0 2px 16px rgba(15, 61, 110, 0.10);
  padding: 40px 48px 36px;
}
.header { display: flex; justify-content: space-between; align-items: flex-start; padding-bottom: 16px; border-bottom: 3px solid var(--accent); }
.co-name { font-size: 30px; font-weight: 700; letter-spacing: 1px; color: var(--accent); line-height: 1.15; }
.co-name .en { font-size: 14px; font-weight: 400; color: var(--faint); letter-spacing: 0.5px; margin-left: 10px; }
.co-meta { margin-top: 9px; font-size: 12px; color: var(--muted); line-height: 1.8; }
.co-meta .tag { display: inline-block; padding: 2px 9px; border-radius: 3px; font-size: 11px; margin-right: 7px; border: 1px solid var(--line); background: var(--bg-soft); color: var(--muted); }
.co-meta .code { font-weight: 600; color: var(--ink); }
.currency-note { margin-top: 4px; font-size: 11px; color: var(--accent); }
.currency-note:empty { display: none; }
.header-right { text-align: right; flex-shrink: 0; margin-left: 16px; }
.quarter { display: inline-block; background: var(--accent); color: #fff; padding: 6px 14px; border-radius: 4px; font-size: 13px; font-weight: 600; letter-spacing: 0.5px; }
.badges { margin-top: 9px; display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
.badge { font-size: 11px; padding: 3px 10px; border-radius: 3px; font-weight: 500; }
.badge.lynch { background: #eef3fb; color: var(--accent-2); border: 1px solid #c9d8ec; }
.badge.graham { background: #eef7f1; color: var(--down); border: 1px solid #c4e3d2; }
.summary { margin: 16px 0; padding: 12px 16px; background: var(--bg-soft); border-left: 3px solid var(--accent-2); font-size: 13px; line-height: 1.8; color: #33404f; }
.summary b { color: var(--ink); }

.section { margin-bottom: 20px; }
.sec-title { font-size: 15px; font-weight: 700; color: var(--accent); padding-bottom: 6px; margin-bottom: 11px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; align-items: baseline; }
.sec-title .hint { font-size: 11px; font-weight: 400; color: var(--faint); }
.sub-title { font-size: 12px; font-weight: 600; color: var(--muted); margin: 14px 0 7px; }
.disclaim { font-size: 10px; color: var(--faint); margin-bottom: 8px; }
.val-note { font-size: 10px; color: var(--faint); line-height: 1.7; margin-top: 9px; }

/* PE 十年走势图（自绘 SVG） */
.pe-chart { margin-top: 12px; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.pe-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px; }
.pe-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.pe-chart svg { display: block; }

/* 分红历史合图（柱 = 每股股息 / 柱顶 = 分红总额 / 折线 = 分红比例） */
.div-hist { margin-top: 12px; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.dh-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.dh-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.dh-legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 7px; font-size: 10px; color: var(--muted); }
.dh-legend .lg { display: inline-flex; align-items: center; gap: 5px; }
.dh-legend .sw { width: 12px; height: 9px; border-radius: 2px; display: inline-block; }
.dh-legend .sw.line { height: 2px; border-radius: 0; }

/* 经营结构（分产品/渠道/地区）与年度经营计划 */
.operating { margin-top: 12px; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.op-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.op-note { font-size: 10px; font-weight: 400; color: var(--faint); }
/* 表体 11.5px = 本板块正文（.qr-col p）的字号，表头低 1px 做层级。
   同一板块里正文与两张表（经营结构 / 现金流归因）必须共用一套字阶，
   否则会出现「11.5 / 11 / 10」三种尺寸并排，看起来像三个人拼的版面。 */
.op-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
.op-table th { font-size: 10.5px; font-weight: 600; color: var(--muted); text-align: right; padding: 4px 6px; border-bottom: 1px solid var(--line); background: var(--bg-soft); }
.op-table th:first-child, .op-table th:nth-child(2) { text-align: left; }
.op-table td { padding: 4px 6px; border-bottom: 1px solid var(--line-soft); }
.op-table tr:last-child td { border-bottom: none; }
.op-cut { color: var(--accent-2); font-weight: 600; font-size: 10px; }
.op-name { color: var(--ink); }
.op-num { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
.op-up { color: var(--up); font-weight: 600; }
.op-down { color: var(--down); font-weight: 600; }
.op-na { color: var(--faint); }
.op-foot { margin-top: 7px; font-size: 10px; color: var(--faint); line-height: 1.7; }
.op-plan { margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--line); }
.op-plan-h { font-size: 11px; font-weight: 600; color: var(--ink); margin-bottom: 6px; }
.op-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.op-chip { font-size: 10px; padding: 2px 8px; border-radius: 10px; background: var(--bg-soft); color: var(--muted); border: 1px solid var(--line-soft); }
.op-quant { margin-top: 7px; font-size: 10px; color: var(--warn); }

/* 季度财报解读 */
.qrev { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.qr-grid { display: flex; gap: 16px; }
.qr-col { flex: 1; }
.qr-h { font-size: 11px; font-weight: 700; color: var(--accent); margin-bottom: 5px; }
.qr-col p, .qr-watch-wrap p { font-size: 11.5px; line-height: 1.8; color: #33404f; }
.qr-watch-wrap { margin-top: 11px; padding-top: 9px; border-top: 1px dashed var(--line); }
/* 现金流异动归因：正文字号/行高/颜色必须与「季度数据表现 / 经营结构解读」逐项相同。
   漏写这段规则时，<p> 会退回浏览器默认 16px + var(--ink)，在一堆 11.5px/#33404f 的
   段落里明显像是从别的板块粘过来的——而且不报错，只有肉眼看才看得出来。 */
.qr-cf-wrap { margin-top: 11px; padding-top: 9px; border-top: 1px solid var(--line-soft); }
.qr-cf-wrap p { font-size: 11.5px; line-height: 1.8; color: #33404f; }
.cf-strip { margin-top: 4px; font-size: 11.5px; line-height: 1.8; color: var(--muted); }
.cf-strip b { font-weight: 700; }
.cf-strip b.up { color: var(--up); }
.cf-strip b.down { color: var(--down); }
.cf-table { margin-top: 7px; }
.cf-table th { font-size: 10.5px; }   /* 与 .op-table th 同尺寸（op-table th 为 10px，此处统一抬高） */
.cf-table th:first-child, .cf-table td:first-child { text-align: left; }
/* 科目名列用 var(--ink)，与下方「经营结构」表的 .op-name 同色；
   数值列沿用 .op-num 的 var(--muted)。两张表里同类单元格必须同色。 */
.cf-name { color: var(--ink); }
.cf-tag { margin-left: 5px; padding: 0 5px; border-radius: 8px; font-size: 10px;
  background: var(--bg-soft); border: 1px solid var(--line-soft); color: var(--faint); }
.qr-watch { list-style: none; }
.qr-watch li { font-size: 11.5px; line-height: 1.7; color: #33404f; padding: 4px 0 4px 16px; position: relative; }
.qr-watch li::before { content: ""; position: absolute; left: 3px; top: 11px; width: 6px; height: 6px; border-radius: 50%; background: var(--accent-2); }
.qr-foot { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 10px; color: var(--faint); line-height: 1.7; }

/* 全历史宽表 */
.table-scroll { overflow-x: auto; }
table.dense { width: 100%; border-collapse: collapse; font-size: 10px; table-layout: fixed; }
table.dense th, table.dense td { padding: 4px 3px; text-align: right; border-bottom: 1px solid var(--line-soft); overflow: hidden; }
table.dense th.name, table.dense td.name { text-align: left; width: 118px; }
table.dense th { background: var(--bg-soft); color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--line); font-size: 9.5px; }
table.dense td.num { font-variant-numeric: tabular-nums; }
table.dense tr.group td { background: #eef3fb; color: var(--accent-2); font-weight: 600; font-size: 10px; text-align: left; border-bottom: 1px solid var(--line); }
table.dense .row-head { font-weight: 500; color: #33404f; }

/* 业务收入构成 */
.seg-row { display: flex; gap: 18px; }
.seg-block { flex: 1; }
.seg-full { margin-bottom: 14px; }
.seg-block-title { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 6px; }

/* 商业模式 */
.biz-row { display: flex; padding: 7px 0; border-bottom: 1px dashed var(--line-soft); font-size: 12px; line-height: 1.7; }
.biz-row:last-child { border-bottom: none; }
.biz-k { flex: 0 0 78px; font-weight: 700; color: var(--accent); }
.biz-v { flex: 1; color: #33404f; }

/* 业务版图（客观数据） */
.bizmap { margin-bottom: 12px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.bizmap-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; }
.bizmap-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.bizmap-main { font-size: 11.5px; color: var(--ink); line-height: 1.6; margin: 8px 0 10px; }
.bizmap-list { display: flex; flex-direction: column; gap: 5px; }
.bizmap-row { display: flex; align-items: center; gap: 8px; font-size: 10.5px; color: var(--muted); }
.bizmap-name { flex: 0 0 88px; text-align: right; overflow: hidden; white-space: nowrap; }
.bizmap-track { flex: 1; height: 9px; background: #eef1f5; border-radius: 4px; overflow: hidden; }
.bizmap-bar { height: 100%; background: var(--accent); border-radius: 4px; }
.bizmap-val { flex: 0 0 40px; text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }

/* 竞争地位（客观数据） */
.competition { margin-top: 14px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.comp-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; }
.comp-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.comp-intro { font-size: 11px; line-height: 1.6; color: var(--muted); margin-top: 8px; }
.comp-grid { display: flex; gap: 12px; margin: 10px 0; }
.comp-item { flex: 1; padding: 9px 12px; background: #fff; border: 1px solid var(--line-soft); border-radius: 6px; }
.comp-item .lbl { font-size: 10px; color: var(--muted); }
.comp-item .v { font-size: 17px; font-weight: 700; color: var(--ink); margin-top: 3px; font-variant-numeric: tabular-nums; }
.peer-list { display: flex; flex-direction: column; gap: 4px; }
.peer-row { display: flex; align-items: center; gap: 8px; font-size: 10.5px; color: var(--muted); }
.peer-row.self { color: var(--accent-2); font-weight: 600; }
.peer-name { flex: 0 0 72px; text-align: right; overflow: hidden; white-space: nowrap; }
.peer-track { flex: 1; height: 8px; background: #eef1f5; border-radius: 4px; overflow: hidden; }
.peer-bar { height: 100%; background: #aebccb; border-radius: 4px; }
.peer-row.self .peer-bar { background: var(--accent-2); }
.peer-val { flex: 0 0 56px; text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }

/* 业务收入构成 */
.seg-dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }
.seg-bar { display: flex; height: 12px; border-radius: 6px; overflow: hidden; margin-top: 12px; }
.seg-bar .seg { height: 100%; }
.seg-legend-row { font-size: 10px; color: var(--faint); margin-top: 7px; display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }
.seg-legend { display: inline-flex; align-items: center; gap: 3px; color: var(--muted); }
.seg-note { color: var(--faint); }

/* 构成饼图（最新年报各大类子科目）：多图横向并排，图在上、图例在下 */
.pie-row { display: flex; flex-wrap: wrap; gap: 10px; }
.pie-card { flex: 1 1 180px; min-width: 170px; padding: 10px 12px; border: 1px solid var(--line-soft); border-radius: 8px; }
.pie-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.pie-total { font-size: 10px; font-weight: 400; color: var(--faint); }
.pie-body { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.pie-legend { width: 100%; display: flex; flex-direction: column; gap: 3px; font-size: 10.5px; color: var(--muted); }
.pie-lg { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
.pie-nm { flex: 1; overflow: hidden; text-overflow: ellipsis; }
.pie-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
.pie-lg b { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; min-width: 48px; text-align: right; }
.pie-lg i { color: var(--faint); font-style: normal; font-variant-numeric: tabular-nums; min-width: 40px; text-align: right; }
.pie-deduct b { color: var(--faint); }
.pie-deduct i { color: var(--faint); }

/* 估值三件套（横排） */
.val-grid { display: flex; gap: 12px; }
.val-item { flex: 1; padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: #fff; }
.val-item .lbl { font-size: 11px; color: var(--muted); }
.val-item .v { font-size: 21px; font-weight: 700; color: var(--ink); margin: 4px 0; font-variant-numeric: tabular-nums; }
.val-item .v small { font-size: 12px; font-weight: 400; color: var(--muted); }
.val-item .pct { font-size: 10px; color: var(--faint); }
.pct-low { color: var(--up) !important; font-weight: 600; }
.pct-high { color: var(--down) !important; font-weight: 600; }

/* 市场数据（52周 + 机构预期 横排） */
.market-row { display: flex; gap: 12px; margin-top: 12px; }
.market-row > div { flex: 1; }
.price-range { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.price-range .pr-title { font-size: 11px; color: var(--muted); margin-bottom: 9px; }
.pr-bar { position: relative; height: 8px; background: #eef1f5; border-radius: 4px; margin-bottom: 7px; }
.pr-marker { position: absolute; top: -3px; width: 2px; height: 14px; background: var(--accent-2); border-radius: 1px; }
.pr-labels { display: flex; justify-content: space-between; font-size: 10.5px; color: var(--muted); }
.pr-labels b { color: var(--ink); font-variant-numeric: tabular-nums; }
.pr-note { margin-top: 6px; font-size: 9.5px; line-height: 1.6; color: var(--faint); }
.pr-note b { color: var(--muted); }
.consensus { padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; font-size: 11px; color: var(--muted); line-height: 1.9; }
.consensus b { color: var(--ink); }
.rating-bar { display: flex; height: 18px; border-radius: 4px; overflow: hidden; margin: 6px 0; }
.rating-bar span { display: flex; align-items: center; justify-content: center; font-size: 10px; color: #fff; white-space: nowrap; }
.tp-grid { display: flex; gap: 8px; margin: 7px 0; }
.tp-cell { flex: 1; text-align: center; padding: 7px 4px; background: #fff; border: 1px solid var(--line-soft); border-radius: 6px; }
.tp-cell .k { font-size: 9.5px; color: var(--faint); }
.tp-cell .v { font-size: 15px; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
.rating-bar { display: flex; height: 11px; border-radius: 5px; overflow: hidden; margin: 6px 0 5px; }
.rating-bar .seg { height: 100%; }
.rating-legend { font-size: 10px; color: var(--faint); display: flex; gap: 12px; }

/* 格雷厄姆体检 */
.graham { margin-top: 12px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.graham .g-title { font-size: 12px; font-weight: 700; color: var(--accent); margin-bottom: 7px; }
.graham .g-row { display: flex; justify-content: space-between; font-size: 11px; padding: 3px 0; color: var(--muted); }
.graham .g-row b { color: var(--ink); font-weight: 600; }
.graham .g-score { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11.5px; color: var(--ink); }
.graham .g-score b { color: var(--down); }

/* ValueLine 统计：流动状况 + 年增长率 */
.current-pos { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.cp-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 9px; }
.cp-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.cp-grid { display: flex; gap: 16px; }
.cp-col { flex: 1; }
.cp-col-title { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 5px; padding-bottom: 4px; border-bottom: 1px solid var(--line); }
.cp-row { display: flex; justify-content: space-between; font-size: 11px; padding: 3px 0; color: var(--muted); }
.cp-row b { color: var(--ink); font-weight: 500; font-variant-numeric: tabular-nums; }
.cp-row.total { border-top: 1px dashed var(--line); margin-top: 3px; padding-top: 5px; }
.cp-row.total span { font-weight: 600; color: var(--ink); }
.cp-row.total b { font-weight: 700; color: var(--accent-2); }
.cp-wc { margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11.5px; color: var(--muted); }
.cp-wc b { font-variant-numeric: tabular-nums; }
.cp-wc b.pos { color: var(--down); }
.cp-wc b.neg { color: var(--up); }

.annual-rates { margin-top: 12px; padding: 12px 14px; background: var(--bg-soft); border-radius: 8px; }
.ar-title { font-size: 12px; font-weight: 700; color: var(--accent); display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.ar-note { font-size: 10px; font-weight: 400; color: var(--faint); }
.ar-row { display: grid; grid-template-columns: 1fr 80px 80px; font-size: 11px; padding: 3px 0; color: var(--muted); }
.ar-row span { color: #33404f; }
.ar-row b { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); font-weight: 600; }
.ar-row.ar-head { color: var(--faint); font-size: 10px; border-bottom: 1px solid var(--line-soft); margin-bottom: 3px; }
.ar-row.ar-head b { color: var(--faint); font-weight: 500; }

/* 财务造假检测 */
.fraud { padding: 12px 14px; border: 1px solid var(--line-soft); border-radius: 8px; }
.fraud .f-row { display: flex; justify-content: space-between; font-size: 11px; padding: 4px 0; color: var(--muted); }
.fraud .f-row b { color: var(--ink); font-weight: 600; }
.fraud .f-row b.ok { color: var(--down); }
.fraud .f-row b.warn { color: var(--warn); }
.fraud .f-row b.bad { color: var(--up); }
.fraud .f-score { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); font-size: 11.5px; color: var(--ink); }
.fraud .f-score b.ok { color: var(--down); }
/* 「未覆盖检测项」说明行：只在数据源缺项时出现（如港股无审计意见字段），
   长期没人看到过，所以一直漏着样式 → 一旦出现就是 16px 默认字号。
   按本板块的脚注层级补上（比 f-row 小、比正文浅）。 */
.fraud .f-note { margin-top: 7px; padding-top: 6px; border-top: 1px dashed var(--line);
  font-size: 10.5px; line-height: 1.7; color: var(--faint); }

/* 列表 */
.thesis, .risk { list-style: none; }
.thesis li, .risk li { font-size: 12px; line-height: 1.7; color: #33404f; padding: 5px 0 5px 18px; position: relative; border-bottom: 1px dashed var(--line-soft); }
.thesis li:last-child, .risk li:last-child { border-bottom: none; }
.thesis li::before { content: ""; position: absolute; left: 3px; top: 12px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent-2); }
.risk li::before { content: "!"; position: absolute; left: 3px; top: 6px; font-size: 11px; font-weight: 700; color: var(--warn); }

/* 数据校验 */
.verify { font-size: 11px; color: var(--faint); line-height: 1.8; }
.verify b { color: var(--muted); font-weight: 600; }
.reconcile { margin-top: 8px; padding: 8px 10px; background: var(--amber-bg); border: 1px solid var(--amber-line); border-radius: 6px; color: var(--warn); line-height: 1.7; }
.reconcile b { color: #8a6d0b; }
.footer { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 10px; color: var(--faint); line-height: 1.7; }
@media print { body { background: #fff; padding: 0; } .page { box-shadow: none; margin: 0; width: 100%; } }

/* ===== 移动端响应式（手机浏览器直开，上下滑动长图体验） ===== */
@media (max-width: 768px) {
  body { padding: 0; background: #fff; }
  .page { max-width: 100%; box-shadow: none; padding: 18px 14px 22px; }
  .header { flex-direction: column; gap: 12px; }
  .header-right { text-align: left; margin-left: 0; align-self: flex-start; }
  .badges { align-items: flex-start; }
  .co-name { font-size: 22px; }
  .co-name .en { display: block; margin-left: 0; margin-top: 3px; }

  /* 横排卡片改纵向堆叠 */
  .biz-row { flex-direction: column; gap: 2px; }
  .biz-k { flex: none; }
  .val-grid, .market-row, .cp-grid, .comp-grid, .seg-row, .qr-grid { flex-direction: column; gap: 10px; }
  .tp-grid { flex-wrap: wrap; }

  /* 业务版图 / 竞争地位 */
  .bizmap-name { flex-basis: 60px; }
  .peer-name { flex-basis: 56px; }

  /* 构成饼图：手机端一行一张，科目名可换行显示完整 */
  .pie-card { flex: 1 1 100%; min-width: 0; }
  .pie-lg { white-space: normal; }
  .pie-nm { overflow: visible; text-overflow: clip; white-space: normal; }

  /* 宽表保持横向滚动（.table-scroll 已有 overflow-x:auto） */
  table.dense { font-size: 9px; }
  table.dense th.name, table.dense td.name { width: 88px; }

  .sec-title { font-size: 14px; }
  .summary { font-size: 12px; }
  .thesis li, .risk li { font-size: 12px; }
}
"""

# ============ HTML 骨架（单栏纵向长图） ============
TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ValueLine 一页研报 · 模板 v1.5</title>
<style>@@CSS@@</style>
</head>
<body>
<div class="page">

  <div class="header">
    <div>
      <div class="co-name">@@COMPANY_NAME@@</div>
      <div class="co-meta">
        <span class="code">@@COMPANY_CODE@@</span>
        <span class="tag">@@INDUSTRY@@</span>
        <div style="margin-top:3px;">报告期：@@REPORT_PERIOD@@ · 发布日期：@@PUBLISH_DATE@@</div>
        <div class="currency-note">@@CURRENCY_NOTE@@</div>
      </div>
    </div>
    <div class="header-right">
      <span class="quarter">@@REPORT_PERIOD@@ 更新</span>
      <div class="badges">
        <span class="badge lynch">林奇分类：@@LYNCH_TYPE@@</span>
        <span class="badge graham">格雷厄姆质量：@@GRAHAM_BADGE@@</span>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="sec-title">商业模式 <span class="hint">靠什么赚钱 · 竞争地位 · 护城河</span></div>
@@BIZ@@
@@COMPETITION@@
  </div>

  <div class="section">
    <div class="sec-title">估值与市场 <span class="hint">数据来源：百度估值 + 财报计算@@VAL_CURRENCY_HINT@@</span></div>
    <div class="disclaim">市场数据与第三方机构观点汇总，非投资建议。</div>
@@VAL_GRID@@
@@MARKET_ROW@@
@@PE_CHART@@
@@DIV_HISTORY@@
@@VAL_NOTE@@
  </div>

  <div class="section">
    <div class="sec-title">季度财报解读 <span class="hint">@@QUARTER_REVIEW_HINT@@</span></div>
@@QUARTER_REVIEW@@
  </div>

  <div class="section">
    <div class="sec-title">核心财务数据（上市以来全历史 @@YEAR_RANGE@@） <span class="hint">单位：亿元 / 亿股 / %</span></div>
@@TABLE@@
    <div style="font-size:10px;color:var(--faint);margin-top:6px;">该标的不适用或数据源未提供的科目（整行无数据）已隐藏，未做补零或估算。</div>

@@PIE@@

    <div class="sub-title">近三年季度（@@QUARTER_RANGE@@）</div>
@@QUARTER_TABLE@@
    <div style="font-size:10px;color:var(--faint);margin-top:6px;">单季 = 本季发生额；资产负债表为季度末时点值。「单季同比」对去年同一季度。</div>
  </div>

  <div class="section">
    <div class="sec-title">经营统计（ValueLine 口径） <span class="hint">@@STATS_HINT@@</span></div>
@@CURRENT_POSITION@@
@@ANNUAL_RATES@@
  </div>

  <div class="section">
    <div class="sec-title">投资逻辑</div>
@@THESIS@@
  </div>

  <div class="section">
    <div class="sec-title">风险提示</div>
@@RISKS@@
  </div>

  <div class="section">
    <div class="sec-title">财务造假检测 <span class="hint">Beneish M-Score</span></div>
@@FRAUD@@
    <div style="font-size:10px;color:var(--faint);margin-top:5px;">M-Score 阈值 -1.78（Beneish 1999 模型），基于近两年财报计算，仅供参考，不构成投资建议。</div>
  </div>

  <div class="section">
    <div class="sec-title">数据校验记录</div>
@@VERIFY@@
  </div>

  <div class="footer">
    本页为个人投研研究记录，仅供学习交流，不构成任何投资建议或买卖依据。52周价格、机构评级与盈利预测均为公开市场数据及第三方机构观点，非本人建议。数据可能存在误差或滞后，请以公司官方披露及监管文件为准。公开版遵循「完全去操作」原则，不含本人操作建议。
  </div>

</div>
</body>
</html>
"""


def _reconcile(code: str) -> list[dict]:
    """生成报告前，用官方年报 PDF 金标准交叉校验并生成修正记录。

    背景：东财/新浪等第三方接口同源，在「同一控制下企业合并追溯重述」等特殊情形下
    会抓取错误（如神华 2025 年总资产 9038 亿 vs 官方 6278 亿）。此步骤在渲染前用官方
    年报 PDF 的三张主表（资产负债表 + 利润表 + 现金流量表）对比接口值，差异 >1% 记录
    修正项（落盘 reconcile.json，由 adapter.load_raw 读 raw 后统一应用，raw 层保持接口
    原始值）。失败则降级跳过。
    """
    try:
        from src.validation import reconcile_all, load_reconcile_log
        import pandas as pd
        bs_path = Path("data/raw") / code / "balance_sheet.parquet"
        if not bs_path.exists():
            return []
        bs = pd.read_parquet(bs_path)
        d = pd.to_datetime(bs["report_date"])
        annual_dates = d[d.dt.month == 12]
        if annual_dates.empty:
            return []
        year = int(annual_dates.dt.year.max())  # 最新年报年份（12-31），非季度
        result = reconcile_all(code, year)
        n = sum(len(c) for c in result["corrections"].values())
        if n:
            print(f"[reconcile] {code} {year} 记录 {n} 个接口错误字段（官方PDF金标准，adapter 读 raw 时应用）")
        # 读历史修正记录（供报告「数据校验」区展示）
        log = load_reconcile_log(code, year)
        if log:
            print(f"[reconcile] {code} {year} 历史修正记录 {len(log)} 项（官方PDF金标准）")
        return log
    except Exception as e:
        print(f"[reconcile] 跳过（{type(e).__name__}: {e}）")
        return []


def _load_real_data(code: str) -> dict | None:
    """尝试加载真实数据，失败返回 None。"""
    if not _HAS_DATA:
        return None
    try:
        return build_template_data(code)
    except Exception as e:
        print(f"[build_valueline] 加载真实数据失败，降级为示例数据: {e}")
        return None


NARRATIVE_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "narrative"


def _facts_hash(facts) -> str:
    """叙事层输入（事实数据）的稳定哈希，用于判断缓存是否仍适用。"""
    import hashlib
    import json
    blob = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def _load_cached_narrative(code: str, facts):
    """读叙事层缓存：仅当事实数据哈希一致时复用，数据一变自动失效。

    LLM 输出本身不确定（同样的事实每次生成文本都不同），每次重建都重调既烧 token
    又让报告内容无意义地漂移。缓存后「数据没变 → 叙事不变」。
    """
    import json
    p = NARRATIVE_CACHE_DIR / f"{code}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if obj.get("hash") == _facts_hash(facts):
            return obj.get("narrative")
    except Exception:
        return None
    return None


def _save_narrative(code: str, facts, narrative) -> None:
    import json
    NARRATIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = NARRATIVE_CACHE_DIR / f"{code}.json"
    p.write_text(
        json.dumps({"hash": _facts_hash(facts), "narrative": narrative},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def build(code: str = "601088", daily: bool = False, refresh_narrative: bool = False) -> None:
    global YEARS, FINANCIALS, QUARTER_LABELS, QUARTERLY, SEGMENT_LABELS, SEGMENTS, VALUATION, GRAHAM, RATING, FRAUD, COMPETITION, BUSINESS_MAP, CURRENT_POSITION, ANNUAL_RATES, PIE_DATA, COMPANY_NAME, COMPANY_CODE, NARRATIVE, RECONCILE_LOG, SANITY, CURRENCY_NOTE, VAL_CURRENCY_HINT, QUARTER_REVIEW, OPERATING
    # 货币口径：港股财报原生人民币，市值/股价原生港元，双币种标注避免误读
    CURRENCY_NOTE = (
        "港股标的 · 财务数据为人民币，股价/市值为港元"
        if _is_hk(code) else ""
    )
    # 估值面板币种提示：PE/PB 分子(市值)为港元、分母(利润/净资产)为人民币（港股市场惯例口径）
    VAL_CURRENCY_HINT = " · PE/PB 为港元市值÷人民币财务（港股惯例）" if _is_hk(code) else ""
    # 代码规范化（港股 00700.HK→00700）：归档文件名与 parquet 目录统一为 5 位纯数字
    if _norm_code:
        code = _norm_code(code)
    # 先做数据交叉校验（官方 PDF 金标准覆盖接口错误字段）。
    # 每日行情刷新（--daily）跳过：财务数据未变，PDF 校验/LLM 叙事无需重跑，只更新估值板块。
    RECONCILE_LOG = ([] if daily else _reconcile(code)) if _HAS_DATA else []
    real = _load_real_data(code)
    if real:
        YEARS = real["years"]
        FINANCIALS = real["financials"]
        QUARTER_LABELS = real["quarter_labels"]
        QUARTERLY = real["quarterly"]
        report_period = real["report_period"]
        if real["segments"]:
            SEGMENT_LABELS = real["segment_labels"]
            SEGMENTS = real["segments"]
        else:
            # 无分业务构成数据（如港股标的）时清空，避免 fallback 到神华示例数据
            SEGMENT_LABELS = []
            SEGMENTS = []
        VALUATION = real["valuation"]
        GRAHAM = real["graham"]
        RATING = real.get("rating")
        FRAUD = real.get("fraud")
        COMPETITION = real.get("competition")
        BUSINESS_MAP = real.get("business_map")
        CURRENT_POSITION = real.get("current_position")
        ANNUAL_RATES = real.get("annual_rates")
        PIE_DATA = real.get("pie_data")
        SANITY = real.get("sanity")
        OPERATING = real.get("operating_structure")
        if real["company_name"]:
            COMPANY_NAME = real["company_name"]
        COMPANY_CODE = code
        # LLM 生成叙事层（数据先行）。每日刷新跳过（财务数据未变，叙事不变，省 token）
        NARRATIVE = None
        if (not daily) and _HAS_LLM and real.get("narrative_data"):
            _facts = real["narrative_data"]
            NARRATIVE = None if refresh_narrative else _load_cached_narrative(code, _facts)
            if NARRATIVE is None:
                NARRATIVE = generate_narrative(_facts)
                if NARRATIVE:
                    _save_narrative(code, _facts, NARRATIVE)
                print("  叙事层: LLM 重新生成")
            else:
                print("  叙事层: 复用缓存（事实数据未变）")
        # 季度财报解读：抓最新定期报告原文 + 单季财务事实 → DeepSeek（同样按事实哈希缓存）
        QUARTER_REVIEW = None
        if (not daily) and _HAS_QREVIEW and real.get("quarter_review_facts"):
            QUARTER_REVIEW = get_quarter_review(
                code, real["quarter_review_facts"], refresh=refresh_narrative
            )
            print("  季度财报解读: " + ("已生成" if QUARTER_REVIEW else "跳过（无 API key 或生成失败）"))
        data_src = f"真实数据 {code}"
    else:
        report_period = "2026Q2"
        data_src = "示例数据"

    year_range = f"{YEARS[0]}–{YEARS[-1]}"
    quarter_range = f"{QUARTER_LABELS[0]}–{QUARTER_LABELS[-1]}"
    segment_range = f"{SEGMENT_LABELS[0]}–{SEGMENT_LABELS[-1]}" if SEGMENT_LABELS else ""
    # 发布日期 = 股价数据日期（估值面板 quote_date），保证「发布日期」与「最新股价日期」一致；
    # 取不到 quote_date 时回退到生成当天。
    from datetime import date as _date
    quote_date = (VALUATION or {}).get("quote_date")
    publish_date = quote_date.isoformat() if quote_date else _date.today().isoformat()

    industry = (COMPETITION or {}).get("industry") or _narr(["industry"], "行业待接入")
    lynch_type = _narr(["lynch_type"], "待分析")
    graham_badge = build_graham_badge()

    html = (
        TEMPLATE
        .replace("@@CSS@@", CSS)
        .replace("@@TABLE@@", build_table())
        .replace("@@QUARTER_TABLE@@", build_quarter_table())
        .replace("@@SEGMENTS@@", build_segments())
        .replace("@@PIE@@", build_pie())
        .replace("@@STATS_HINT@@", _stats_hint())
        .replace("@@YEAR_RANGE@@", year_range)
        .replace("@@QUARTER_RANGE@@", quarter_range)
        .replace("@@SEGMENT_RANGE@@", segment_range)
        .replace("@@VAL_GRID@@", build_val_grid())
        .replace("@@MARKET_ROW@@", build_market_row())
        .replace("@@PE_CHART@@", build_pe_chart())
        .replace("@@DIV_HISTORY@@", build_div_history())
        .replace("@@VAL_NOTE@@", build_val_note())
        .replace("@@QUARTER_REVIEW@@", build_quarter_review())
        .replace("@@QUARTER_REVIEW_HINT@@", _quarter_review_hint())
        .replace("@@BIZ@@", build_business_model())
        .replace("@@COMPETITION@@", build_competition())
        .replace("@@CURRENT_POSITION@@", build_current_position())
        .replace("@@ANNUAL_RATES@@", build_annual_rates())
        .replace("@@THESIS@@", build_thesis())
        .replace("@@RISKS@@", build_risks())
        .replace("@@VERIFY@@", build_verify())
        .replace("@@FRAUD@@", build_fraud())
        .replace("@@COMPANY_NAME@@", COMPANY_NAME)
        .replace("@@COMPANY_CODE@@", COMPANY_CODE)
        .replace("@@INDUSTRY@@", industry)
        .replace("@@REPORT_PERIOD@@", report_period)
        .replace("@@PUBLISH_DATE@@", publish_date)
        .replace("@@CURRENCY_NOTE@@", CURRENCY_NOTE)
        .replace("@@VAL_CURRENCY_HINT@@", VAL_CURRENCY_HINT)
        .replace("@@LYNCH_TYPE@@", lynch_type)
        .replace("@@GRAHAM_BADGE@@", graham_badge)
    )

    root = Path(__file__).resolve().parent.parent

    # 1. templates/valueline.html（预览/调版式）
    tpl_out = root / "templates" / "valueline.html"
    tpl_out.write_text(html, encoding="utf-8")

    # 2. reports/{报告期}/{code}.html（归档报告）
    report_out = root / "reports" / report_period / f"{code}.html"
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(html, encoding="utf-8")

    print(f"generated:")
    print(f"  预览: {tpl_out}")
    print(f"  归档: {report_out}")
    print(f"  数据来源: {data_src}")
    print(f"  年度: {year_range} ({len(YEARS)} 年), 季度: {quarter_range} ({len(QUARTER_LABELS)} 季)")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "601088"
    daily = "--daily" in sys.argv
    refresh = "--refresh-narrative" in sys.argv
    build(code, daily=daily, refresh_narrative=refresh)
