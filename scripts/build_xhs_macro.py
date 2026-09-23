#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宏观周期看板 → 小红书轮播图（9 张）。

与 `build_xhs_ge.py` 的关系
---------------------------
沿用 `scripts/build_xhs.py` 的**版式层**（`C` 颜色字典 / `_CSS` / `_PAGE` / `_kv` /
`export_png` 的截图参数），**内容层全新**——宏观与个股没有任何字段重叠，
所以不是「改改字段路径」而是重写 9 张。

九张图的结构（自上而下 = 从「水位」到「该关注什么」）
----------------------------------------------------
    1 封面        一句话定位（周期象限）+ 4 个关键数
    2 周期定位     美林时钟 + 增长/通胀的逐项投票（**规则可复核**）
    3 全球水位     中美国债 / 利差 / 汇率 / 政策利率
    4 估值分位     全 A PE·PB + 沪深300 + 官方整体 PE（带历史分位）
    5 股债性价比   巴菲特指标 + ERP 两个口径
    6 中国流动性   M1−M2 剪刀差 / 社融 12M / 两融
    7 增长与通胀   GDP / 用电量 / 社零 / 景气 / CPI / 房价 / 失业率 / 储备
    8 市场与情绪   主要指数 / 波动率 / 铜金比 / 原油 / 市场宽度
    9 数据说明     关键数速览 + 数据源能力边界 + 免责

内容铁律（与个股发布包同一套，逐条都在代码里有对应实现）
--------------------------------------------------------
- 🔴 **零观点、不给买卖建议**：全文只有数值、方向与口径，没有「该买什么」。
- 🔴 **色阶只表示数值变动方向，不表示利好利空**（失业率上行与用电量上行同色，
     含义却相反）—— 每张图的脚注都印这句。
- 🔴 **口径不同的地方必须标注**（中位数法 vs 整体法、月度 vs 季度、累计 vs 单月）。
- 🔴 **每个数字都印出数据日期**（宏观发布时点差异极大：M2 到 8 月、社融只到 4 月）。

用法：
    python scripts/build_xhs_macro.py [--html-only] [-o reports/xhs/macro_发布包]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.build_xhs as bx  # noqa: E402
from src.macro.build import build_payload  # noqa: E402

C = bx.C
PREFIX = "macro"
OUT_DEFAULT = "reports/xhs/macro_发布包"

SLIDE_NAMES = [
    "封面", "周期定位", "全球水位", "估值分位", "股债性价比",
    "中国流动性", "增长与通胀", "市场与情绪", "数据说明",
]
TOTAL = len(SLIDE_NAMES)

# 色阶说明：每张图脚注都带，避免「红色 = 好事」的误读
CALIBER_NOTE = "色阶仅表示该数值相对上期的变动方向（红升绿降），不代表利好或利空"


# --------------------------------------------------------------------------- #
# 样式：复用 build_xhs 的版式，追加宏观专用组件
# --------------------------------------------------------------------------- #
_EXTRA_CSS = f"""
  .slide {{ width:390px; }}
  .unit {{ font-size:11px; color:{C['faint']}; margin-left:3px; font-weight:400; }}
  .sub2 {{ font-size:11px; color:{C['faint']}; margin:2px 0 0; line-height:1.6; }}

  /* 大数字块 */
  .big4 {{ display:grid; grid-template-columns:1fr 1fr; gap:9px; margin-top:6px; }}
  .b4 {{ background:{C['card']}; border:1px solid {C['line']}; border-radius:9px;
         padding:10px 11px; }}
  .b4 .k {{ font-size:11px; color:{C['muted']}; line-height:1.3; }}
  .b4 .v {{ font-size:19px; font-weight:800; margin-top:3px; letter-spacing:-.3px; }}
  .b4 .m {{ font-size:10px; color:{C['faint']}; margin-top:3px; }}

  /* 分位条 */
  .pbar {{ height:5px; background:#eaeef4; border-radius:3px; position:relative;
           margin-top:5px; overflow:hidden; }}
  .pbar i {{ position:absolute; left:0; top:0; bottom:0; border-radius:3px;
             background:{C['accent2']}; opacity:.72; }}
  .plab {{ display:flex; justify-content:space-between; font-size:10px;
           color:{C['faint']}; margin-top:3px; }}

  /* 美林时钟 */
  .clk {{ display:grid; grid-template-columns:1fr 1fr; gap:7px; margin-top:10px; }}
  .cq {{ border:1px solid {C['line']}; border-radius:9px; padding:11px 12px;
         background:{C['card']}; }}
  .cq .n {{ font-size:15px; font-weight:700; color:{C['muted']}; }}
  .cq .d {{ font-size:10px; color:{C['faint']}; margin-top:3px; }}
  .cq.on {{ background:linear-gradient(135deg,{C['accent']},{C['accent2']});
            border-color:{C['accent']}; }}
  .cq.on .n {{ color:#fff; }}
  .cq.on .d {{ color:rgba(255,255,255,.85); }}
  .axis {{ display:flex; justify-content:space-between; font-size:9px;
           color:{C['faint']}; letter-spacing:.6px; margin:5px 2px; }}

  /* 投票 */
  .vt {{ margin-top:9px; }}
  .vt-row {{ display:flex; align-items:baseline; gap:6px; font-size:11.5px;
             padding:4px 0; border-bottom:1px solid {C['line']}; }}
  .vt-row:last-child {{ border-bottom:0; }}
  .vt-m {{ flex:0 0 13px; font-weight:800; text-align:center; }}
  .vt-m.up {{ color:{C['up']}; }} .vt-m.dn {{ color:{C['down']}; }}
  .vt-m.flat {{ color:{C['faint']}; }}
  .vt-n {{ flex:1; min-width:0; color:{C['muted']}; }}
  .vt-v {{ font-variant-numeric:tabular-nums; }}

  /* 段落与提示 */
  .warnbox {{ background:#fff6e8; border:1px solid #f0cfa0; border-radius:8px;
              padding:9px 11px; font-size:10.5px; color:#8a5a00; line-height:1.7;
              margin-top:9px; }}
  .notebox {{ background:{C['soft']}; border-radius:8px; padding:9px 11px;
              font-size:10.5px; color:{C['muted']}; line-height:1.75; margin-top:9px; }}
  .gap {{ font-size:10.5px; color:{C['muted']}; line-height:1.7; padding:6px 0;
          border-bottom:1px dashed {C['line']}; }}
  .gap:last-child {{ border-bottom:0; }}
  .gap b {{ color:{C['ink']}; }}
  .gap .r {{ color:#8a5a00; }}

  /* 迷你走势 */
  .sp {{ display:block; width:100%; height:22px; margin-top:5px; overflow:visible; }}
  .sp path {{ fill:none; stroke-width:1.4; stroke-linejoin:round; }}
"""


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _slide(idx: int, title: str, subtitle: str, body: str, foot: str = "") -> str:
    """本套图自己的卡片骨架（品牌名与页码分母与个股发布包不同）。"""
    return (
        '<section class="slide">'
        '<div class="hd">'
        '<span class="brand">宏观周期看板</span>'
        f'<span class="pager">{idx} / {TOTAL}</span>'
        "</div>"
        f'<div class="ttl">{bx._esc(title)}'
        + (f'<span class="sub">{bx._esc(subtitle)}</span>' if subtitle else "")
        + "</div>"
        f'<div class="body">{body}</div>'
        + (f'<div class="foot">{foot}</div>' if foot else "")
        + "</section>"
    )


def _v(it: dict) -> str:
    """值 + 单位。"""
    return f'{bx._esc(it["latest_text"])}<span class="unit">{bx._esc(it["unit"])}</span>'


def _chg(it: dict) -> str:
    """变动量，按 chg_dir 上红下绿。"""
    d = it.get("chg_dir", "flat")
    cls = {"up": "upv", "down": "dnv"}.get(d, "flat")
    return f'<span class="{cls}">{bx._esc(it["chg_text"])}</span>'


def _date(it: dict) -> str:
    return f'<span class="unit">{bx._esc(it["period"] or "—")}</span>'


def _kv_it(rows: list[tuple[dict, str, str]]) -> str:
    """(指标, 备注, 额外HTML) → 键值块。"""
    out = '<div class="kv">'
    for it, note, extra in rows:
        out += (
            '<div class="kv-row">'
            f'<div class="kv-k">{bx._esc(it["short"])}{_date(it)}</div>'
            f'<div class="kv-v">{_v(it)} {_chg(it)}</div>'
            + (f'<div class="kv-note">{note}{extra}</div>' if (note or extra) else "")
            + "</div>"
        )
    return out + "</div>"


def _rank_label(it: dict, base: str = "本序列窗口分位") -> str:
    """分位标签：观测太少时把样本量印出来。

    🔴 为什么必须印：`csi300_pe` 这类中证官方源一次只回传约 20 个交易日，
    拿 20 个点算出来的分位与用 6177 个点算出来的分位在画面上长得一模一样。
    不标样本量，读者会把「20 点分位」当成「历史分位」用。
    """
    n = it.get("n_obs") or 0
    return f"{base}（仅 {n} 个观测）" if 0 < n < 60 else base


def _pbar(rank: float | None, label: str = "历史分位") -> str:
    if rank is None:
        return ""
    pct = max(0.0, min(1.0, float(rank))) * 100.0
    return (f'<div class="pbar"><i style="width:{pct:.0f}%"></i></div>'
            f'<div class="plab"><span>{bx._esc(label)}</span><span>{pct:.0f}%</span></div>')


def _spark(it: dict) -> str:
    p = it.get("spark_path") or ""
    if not p:
        return ""
    color = {"up": C["up"], "down": C["down"]}.get(it.get("spark_dir"), C["faint"])
    return (f'<svg class="sp" viewBox="0 0 104 26" preserveAspectRatio="none">'
            f'<path d="{p}" stroke="{color}"/></svg>')


def _foot(extra: str = "") -> str:
    tail = f"{extra}<br>" if extra else ""
    return tail + CALIBER_NOTE


def _idx_lookup(payload: dict) -> dict[str, dict]:
    return {it["key"]: it for g in payload["groups"] for it in g["items"]}


def _get(idx: dict[str, dict], key: str) -> dict:
    """取指标；缺失时给一个占位 dict，避免 KeyError 中断整套图。"""
    return idx.get(key) or {
        "key": key, "short": key, "unit": "", "latest_text": "—", "period": "",
        "chg_text": "—", "chg_dir": "flat", "rank": None, "spark_path": "",
        "spark_dir": "flat", "note": "", "error": "未找到该指标",
    }


# --------------------------------------------------------------------------- #
# 图 1 · 封面
# --------------------------------------------------------------------------- #
def slide_cover(payload: dict, idx: dict[str, dict]) -> str:
    c = payload["clock"]
    quad = c["quadrant"]
    quad_cls = "diverge" if quad == "方向分歧" else ""

    keys = ["a_pe_median", "buffett_cn", "cn_us_spread", "margin_balance"]
    big = ""
    for k in keys:
        it = _get(idx, k)
        big += (f'<div class="b4"><div class="k">{bx._esc(it["short"])}</div>'
                f'<div class="v">{bx._esc(it["latest_text"])}'
                f'<span class="unit">{bx._esc(it["unit"])}</span></div>'
                f'<div class="m">数据日期 {bx._esc(it["period"] or "—")} · '
                f'{bx._esc(it["chg_text"])}</div></div>')

    body = (
        f'<div style="margin-top:8px;font-size:13px;color:{C["muted"]};line-height:1.7">'
        f'下面 {TOTAL - 1} 张图，用公开数据回答一个问题：'
        f'<b style="color:{C["ink"]}">现在整个市场处在周期的什么位置</b>。</div>'
        f'<div class="big4">{big}</div>'
        f'<div class="notebox">判定规则：取若干方向独立的指标，各自与上一期比较，'
        f'方向取净和，落到「增长 × 通胀」四象限；对全部参与指标<b>等权计票</b>，'
        f'未做加权。第 2 张会逐项列出每一票，可自行复核。</div>'
        f'<div class="notebox" style="background:#fff;border:1px solid {C["line"]}">'
        f'构建于 {bx._esc(payload["generated_at"])}　'
        f'共 {payload["stats"]["total"]} 个指标（成功 {payload["stats"]["ok"]}）<br>'
        f'数据来源：AKShare（东财 / 新浪 / 中债 / 中证指数 / 央行等公开接口）</div>'
    )
    return _slide(1, quad if not quad_cls else "方向分歧",
                  (c["definition"] if c["definition"] else "增长与通胀指标互相抵消"), body,
                  "本页为公开数据的结构化呈现，不含任何投资建议。")


# --------------------------------------------------------------------------- #
# 图 2 · 周期定位
# --------------------------------------------------------------------------- #
def slide_clock(payload: dict, idx: dict[str, dict]) -> str:
    c = payload["clock"]
    layout = [("复苏", "增长↑ 通胀↓"), ("过热", "增长↑ 通胀↑"),
              ("衰退", "增长↓ 通胀↓"), ("滞胀", "增长↓ 通胀↑")]
    cells = ""
    for name, d in layout:
        on = " on" if name == c["quadrant"] else ""
        cells += f'<div class="cq{on}"><div class="n">{name}</div><div class="d">{d}</div></div>'

    def votes(vs: list[dict], title: str, score: int, direction: str) -> str:
        rows = ""
        for v in vs:
            cls = {"↑": "up", "↓": "dn"}.get(v["mark"], "flat")
            # 🔴 有意取指标自身的 `latest_text` 而不是重新格式化原始值：
            #    同一个指标会出现在多张图上（企业景气在「周期定位」与「增长与通胀」
            #    都出现），两处各自格式化必然出现 `109.50` vs `109.5` 这种对不上的写法。
            val = "—" if v.get("abstain") else _get(idx, v["key"])["latest_text"]
            rows += (f'<div class="vt-row"><span class="vt-m {cls}">{v["mark"]}</span>'
                     f'<span class="vt-n">{bx._esc(v["name"])}</span>'
                     f'<span class="vt-v">{val}</span></div>')
        return (f'<div class="vt"><div class="kv-k" style="margin-bottom:2px">'
                f'{title}（净 {score:+d} · {bx._esc(direction)}）</div>{rows}</div>')

    body = (
        # 🔴 网格实际语义：**行 = 增长**（上排回升、下排回落）、**列 = 通胀**（左列回落、右列回升）。
        #    行标签必须写成一句话，不能像列标签那样左右分置 —— 分置会被读成「列」，与网格相反。
        f'<div class="axis" style="justify-content:center">'
        f'<span>上排 增长 ↑　·　下排 增长 ↓</span></div>'
        f'<div class="clk">{cells}</div>'
        f'<div class="axis"><span>左列 通胀 ↓</span><span>右列 通胀 ↑</span></div>'
        + votes(c["growth_votes"], "增长侧", c["growth_score"], c["growth_dir"])
        + votes(c["inflation_votes"], "通胀侧", c["inflation_score"], c["inflation_dir"])
        + (f'<div class="warnbox">{bx._esc(c["note"])}</div>' if c["note"] else "")
        + '<div class="notebox">「方向」= 该指标<b>本期相对上一期</b>的变化方向'
          '（CPI 从 0.5% 升到 0.8% 记为通胀上行），不是同比本身的符号。</div>'
    )
    return _slide(2, "周期定位", c["basis"], body,
                  "象限是多指标方向的机械汇总，不构成任何投资建议。")


# --------------------------------------------------------------------------- #
# 图 3 · 全球水位
# --------------------------------------------------------------------------- #
def slide_water(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    body = (
        '<div class="kv-k" style="margin:12px 0 4px">国债收益率</div>'
        + _kv_it([
            (g("cn_10y"), "中国 10 年期国债", ""),
            (g("us_10y"), "美国 10 年期国债（全球资产定价锚）", ""),
            (g("cn_us_spread"), "美国 10Y − 中国 10Y，负值 = 中国利率更低", ""),
            (g("us_10y_2y"), "倒挂（&lt; 0）是历史最可靠的衰退领先指标", ""),
        ])
        + '<div class="kv-k" style="margin:12px 0 4px">信用与货币市场</div>'
        + _kv_it([
            (g("credit_spread"), "AAA 银行债 − 国债券（10Y），走阔 = 信用溢价上升", ""),
            (g("shibor_3m"), "银行间 3 月期拆借利率", ""),
            (g("lpr_1y"), "贷款市场报价利率", ""),
            (g("lpr_5y"), "5 年期以上 LPR（房贷定价基准）", ""),
        ])
        + '<div class="kv-k" style="margin:12px 0 4px">汇率</div>'
        + _kv_it([
            (g("usd_cny"), "央行中间价", ""),
            (g("usd_jpy"), "由美元 / 日元中间价交叉折算", ""),
            (g("eur_cny"), "央行中间价", ""),
        ])
        + '<div class="notebox">🔴 美元指数 DXY 在 AKShare 无可用接口，'
          '本期用<b>人民币汇率 + 美债利率</b>间接观察全球美元流动性。</div>'
    )
    return _slide(3, "全球水位", "别的指标告诉你「贵不贵」，利率与汇率告诉你「钱会不会走」",
                  body, _foot())


# --------------------------------------------------------------------------- #
# 图 4 · 估值分位
# --------------------------------------------------------------------------- #
def slide_valuation(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    pe = g("a_pe_median")
    pb = g("a_pb_median")

    body = (
        f'<div class="big4">'
        f'<div class="b4"><div class="k">全 A 市盈率中位数（TTM）</div>'
        f'<div class="v">{bx._esc(pe["latest_text"])}<span class="unit">倍</span></div>'
        f'<div class="m">数据日期 {bx._esc(pe["period"])} · {bx._esc(pe["chg_text"])}</div>'
        f'{_pbar(pe["rank"], "近 10 年分位")}</div>'
        f'<div class="b4"><div class="k">全 A 市净率中位数</div>'
        f'<div class="v">{bx._esc(pb["latest_text"])}<span class="unit">倍</span></div>'
        f'<div class="m">数据日期 {bx._esc(pb["period"])} · {bx._esc(pb["chg_text"])}</div>'
        f'{_pbar(pb["rank"], "近 10 年分位")}</div>'
        f'</div>'
        + _kv_it([
            (g("a_pe_avg"), "平均法口径，与中位数法的差异即「口径差」", _spark(g("a_pe_avg"))),
            (g("csi300_pe"), "中证指数官方整体法口径",
             _pbar(g("csi300_pe")["rank"], _rank_label(g("csi300_pe")))),
            (g("csi300_div"), "近 12 个月分红 ÷ 当前市值", ""),
            (g("sse_pe"), "上交所官方公布（该接口为当日快照，无历史序列）", ""),
        ])
        + '<div class="notebox">🔴 <b>绝对水平没有意义，只看分位</b>。'
          '这里的「近 10 年分位」来自 AKShare 接口自带的官方分位字段；'
          '其余指标的分位是「本序列回传窗口内」的分位 —— <b>两者窗口不同，不可混用</b>。<br>'
          '「中位数法」（38 倍）与「整体法」（沪深300 约 15 倍）差异极大，'
          '因为中位数法对微利股更敏感，比较时务必看清口径。</div>'
    )
    return _slide(4, "估值分位", "全 A 与沪深 300 的历史位置", body, _foot())


# --------------------------------------------------------------------------- #
# 图 5 · 股债性价比
# --------------------------------------------------------------------------- #
def slide_equity_bond(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    bf = g("buffett_cn")
    body = (
        f'<div class="big4">'
        f'<div class="b4" style="grid-column:1 / -1">'
        f'<div class="k">巴菲特指标（A 股证券化率 = 沪深总市值 ÷ 滚动 4 季 GDP）</div>'
        f'<div class="v">{bx._esc(bf["latest_text"])}<span class="unit">%</span></div>'
        f'<div class="m">数据日期 {bx._esc(bf["period"])} · 较上期 {bx._esc(bf["chg_text"])}'
        f' · 分子为月度市值、分母为季度 GDP</div>'
        f'{_pbar(bf["rank"], "本序列窗口分位")}</div>'
        f'</div>'
        + _kv_it([
            (g("erp_median"), "= 1 ÷ 全 A 中位 PE − 10Y 国债；即 A 股圈说的「格雷厄姆指数」", ""),
            (g("erp_csi300"), "= 1 ÷ 沪深 300 PE − 10Y 国债", ""),
            (g("cn_10y"), "ERP 的分母端 —— 国债越低，ERP 越高", ""),
        ])
        + '<div class="warnbox">🔴 <b>巴菲特指标跨市场不可比</b>：美股常年 200%+、'
          'A 股常年 60–80%，那是融资结构差异（中国靠银行、美国靠股市），'
          '不是「A 股便宜」。<b>本指标只与 A 股自身历史比较</b>，也不要套用'
          '「70–80% 便宜 / 100% 危险」这套基于美股历史的经验阈值。</div>'
        + '<div class="notebox">为什么 ERP 更可操作：它直接回答'
          '「同样一元钱，买股票还是买债券」，且可跨市场比较；'
          '而巴菲特指标的分子分母口径差异使其只能做时间序列比较。</div>'
    )
    return _slide(5, "股债性价比", "巴菲特指标与股权风险溢价", body, _foot())


# --------------------------------------------------------------------------- #
# 图 6 · 中国流动性
# --------------------------------------------------------------------------- #
def slide_liquidity(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    gap = g("m1_m2_gap")
    body = (
        f'<div class="b4" style="margin-top:4px">'
        f'<div class="k">M1 − M2 剪刀差（中国市场最经典的流动性指标）</div>'
        f'<div class="v">{bx._esc(gap["latest_text"])}<span class="unit">pct</span></div>'
        f'<div class="m">数据日期 {bx._esc(gap["period"])} · 较上期 {bx._esc(gap["chg_text"])}'
        f' · 为负 = 资金定期化，向零收窄 = 资金活化</div>'
        f'{_spark(gap)}</div>'
        + _kv_it([
            (g("m1_yoy"), "狭义货币（含企业活期存款）", ""),
            (g("m2_yoy"), "广义货币", ""),
            (g("shrzgm"), "近 12 个月增量合计（非单月值，滚动口径以平滑季节性）", _spark(g("shrzgm"))),
            (g("margin_balance"), "A 股杠杆资金情绪", _spark(g("margin_balance"))),
        ])
        + '<div class="notebox">🔴 社融展示的是<b>滚动 12 个月合计</b>而非单月增量：'
          '单月社融季节性极强（1 月天量、4 月低谷），用环比描述会把季节性误读成信用收缩。'
          '该接口实测滞后约 5 个月，请看数据日期。</div>'
    )
    return _slide(6, "中国流动性", "货币与信用：资金活化到什么程度",
                  body, _foot())


# --------------------------------------------------------------------------- #
# 图 7 · 增长与通胀
# --------------------------------------------------------------------------- #
def slide_growth(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    body = (
        '<div class="kv-k" style="margin:12px 0 4px">增长（领先 → 同步 → 滞后）</div>'
        + _kv_it([
            (g("m1_yoy"), "领先：货币端", ""),
            (g("electricity_yoy"), "同步：「克强指数」核心，统计口径难修饰", ""),
            (g("retail_yoy"), "同步：国内需求侧", ""),
            (g("boom_index"), "央行 5000 户工业企业调查，&gt;100 为景气区间", ""),
            (g("gdp_yoy"), "滞后：季度指标，仅作确认", ""),
        ])
        + '<div class="kv-k" style="margin:12px 0 4px">通胀与居民端</div>'
        + _kv_it([
            (g("cpi_yoy"), "居民消费价格同比", ""),
            (g("cpi_mom"), "环比", ""),
            (g("house_price_70"), "🔴 该接口实测<b>仅回传京沪两市</b>（非 70 城）", ""),
            (g("unemployment"), "全国城镇调查失业率", ""),
        ])
        + '<div class="kv-k" style="margin:12px 0 4px">外部头寸</div>'
        + _kv_it([
            (g("fx_reserves"), "外汇储备余额", ""),
            (g("gold_reserves"), "官方黄金储备（央行购金是近年重要宏观叙事）", ""),
        ])
        + '<div class="notebox">🔴 中国 <b>PPI 与 PMI 本期暂缺</b>：'
          'AKShare 仅有金十数据源，实测停更于 2025-09，且无官方源替代接口。'
          '因此「PPI − CPI 剪刀差」暂无法计算。</div>'
    )
    return _slide(7, "增长与通胀", "中国基本面：哪些在改善，哪些在走弱",
                  body, _foot())


# --------------------------------------------------------------------------- #
# 图 8 · 市场与情绪
# --------------------------------------------------------------------------- #
def slide_market(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    body = (
        '<div class="kv-k" style="margin:12px 0 4px">主要指数</div>'
        + _kv_it([
            (g("idx_sh"), "", _spark(g("idx_sh"))),
            (g("idx_sz"), "", _spark(g("idx_sz"))),
            (g("idx_csi300"), "", _spark(g("idx_csi300"))),
            (g("idx_hsi"), "", _spark(g("idx_hsi"))),
            (g("idx_spx"), "美股（新浪源）", _spark(g("idx_spx"))),
        ])
        + '<div class="kv-k" style="margin:12px 0 4px">波动率（恐慌度）</div>'
        + _kv_it([
            (g("qvix_50"), "中国版「恐慌指数」", ""),
            (g("qvix_300"), "", ""),
            (g("vhsi"), "港股版（该接口为快照，无历史序列）", ""),
        ])
        + '<div class="kv-k" style="margin:12px 0 4px">大宗与市场宽度</div>'
        + _kv_it([
            (g("oil_wti"), "纽约轻质原油连续合约", ""),
            (g("oil_yoy"), "与上年同期比（按日历日期对齐）", ""),
            (g("copper_gold"), "铜 ÷ 金：上行 = 定价「工业需求 &gt; 避险」，比 PMI 更实时的景气代理", _spark(g("copper_gold"))),
            (g("breadth_net"), "创 20 日新高家数 − 新低家数；正 = 赚钱效应扩散", _spark(g("breadth_net"))),
        ])
        + '<div class="notebox">🔴 VIX（美股恐慌指数）在 AKShare 无可用接口，'
          '本期以 VHSI 与 50ETF / 300ETF 波指替代，三者不可直接比较绝对值。</div>'
    )
    return _slide(8, "市场与情绪", "行情、波动率与杠杆资金", body, _foot())


# --------------------------------------------------------------------------- #
# 图 9 · 数据说明
# --------------------------------------------------------------------------- #
def slide_notes(payload: dict, idx: dict[str, dict]) -> str:
    g = lambda k: _get(idx, k)  # noqa: E731
    gaps = ""
    for x in payload["data_gaps"]:
        gaps += (f'<div class="gap"><b>{bx._esc(x["title"])}</b><br>'
                 f'<span class="r">现状：</span>{bx._esc(x["reason"])}<br>'
                 f'<span style="color:{C["faint"]}">计划：{bx._esc(x["plan"])}</span></div>')

    body = (
        '<div class="kv-k" style="margin:12px 0 4px">本期关键数速览</div>'
        + _kv_it([
            (g("a_pe_median"), "全 A 估值中位数", ""),
            (g("cn_10y"), "国内无风险利率", ""),
            (g("m1_m2_gap"), "资金活化程度", ""),
            (g("margin_balance"), "杠杆资金", ""),
        ])
        + '<div class="kv-k" style="margin:14px 0 4px">数据源能力边界（本期未覆盖）</div>'
        + gaps
        + '<div class="notebox" style="margin-top:12px">'
          f'构建于 {bx._esc(payload["generated_at"])} · '
          f'共 {payload["stats"]["total"]} 个指标（成功 {payload["stats"]["ok"]} '
          f'/ 失败 {payload["stats"]["failed"]}）<br>'
          '数据来源：AKShare（东财 / 新浪 / 中债 / 中证指数 / 央行等公开接口）<br>'
          '<b>本内容为公开数据的结构化呈现，不含任何投资建议；'
          '指标方向不代表利好或利空。</b>数据可能存在延迟、缺口或口径差异，'
          '据此决策的风险由使用者自行承担。</div>'
    )
    return _slide(9, "数据说明", "口径、缺口与免责", body, "")


# --------------------------------------------------------------------------- #
# 组装与导出
# --------------------------------------------------------------------------- #
def build_html(payload: dict) -> str:
    idx = _idx_lookup(payload)
    slides = [
        slide_cover(payload, idx),
        slide_clock(payload, idx),
        slide_water(payload, idx),
        slide_valuation(payload, idx),
        slide_equity_bond(payload, idx),
        slide_liquidity(payload, idx),
        slide_growth(payload, idx),
        slide_market(payload, idx),
        slide_notes(payload, idx),
    ]
    return bx._PAGE.format(css=bx._CSS + _EXTRA_CSS, slides="\n".join(slides))


def export_png(html_text: str, out_dir: Path) -> list[Path]:
    """截图（参数与 build_xhs.export_png 一致：390px 宽 × 3 倍缩放）。

    有意自己实现而不是调 `bx.export_png`：那个函数按模块级 `bx.SLIDE_NAMES`
    命名文件，那可是**茅台那套图名**（业务产品 / 渠道模式 / 现金流），
    拿来命名宏观图会得到「macro_02_业务产品.png」这种对不上的文件名。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright，请先执行：pip install playwright && playwright install chromium")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"_xhs_{PREFIX}.html"
    tmp.write_text(html_text, encoding="utf-8")
    uri = tmp.resolve().as_uri()

    out_paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 1400}, device_scale_factor=3)
        page.goto(uri)
        page.wait_for_timeout(400)
        slides = page.locator("section.slide")
        for i in range(slides.count()):
            name = SLIDE_NAMES[i] if i < len(SLIDE_NAMES) else f"p{i + 1}"
            fp = out_dir / f"{PREFIX}_{i + 1:02d}_{name}.png"
            slides.nth(i).screenshot(path=str(fp))
            out_paths.append(fp)
        browser.close()

    tmp.unlink(missing_ok=True)
    return out_paths


def main() -> None:
    ap = argparse.ArgumentParser(description="宏观周期看板 → 小红书轮播图")
    ap.add_argument("-o", "--out", default=OUT_DEFAULT, help=f"输出目录（默认 {OUT_DEFAULT}）")
    ap.add_argument("--html-only", action="store_true", help="只写 HTML，不截图")
    ap.add_argument("--from-json", metavar="PATH",
                    help="用已有 payload 直接出图（跳过拉数，调图卡版式时用）")
    args = ap.parse_args()

    if args.from_json:
        # 🔴 有意支持复用看板那次拉数的 payload：图卡上写的每个数字都必须能在
        #    看板上逐个对上（本套内容的铁律）。各自拉一次数 = 两份快照，
        #    盘中/接口抖动会让两边的数字对不上，而且白跑一遍 90 秒。
        import json
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        print(f"从 {args.from_json} 读取 payload（未拉数）")
    else:
        payload = build_payload(verbose=False)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    html_text = build_html(payload)
    hp = out_dir / f"{PREFIX}.html"
    hp.write_text(html_text, encoding="utf-8")
    print(f"HTML：{hp}")

    c = payload["clock"]
    print(f"周期定位：{c['quadrant']} —— {c['basis']}")
    print(f"指标 {payload['stats']['total']} 个：成功 {payload['stats']['ok']}、"
          f"失败 {payload['stats']['failed']}")

    if args.html_only:
        return
    for fp in export_png(html_text, out_dir):
        print(f"生成：{fp}")


if __name__ == "__main__":
    main()
