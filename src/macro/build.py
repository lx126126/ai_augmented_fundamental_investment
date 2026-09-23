# -*- coding: utf-8 -*-
"""编排：拉取 → 派生 → 定位 → 产出渲染用 payload。

三条不可动的规矩
----------------
1. **单个指标失败不阻断整体**，但必须**显式记账**：失败的指标会产出一个 `error` 非空的
   视图，页面显示「数据缺失（原因）」，并进入 payload 的 `errors` 列表。
   绝不允许「悄悄跳过」—— 本项目吃过静默缺失的亏（北交所 920xxx 少 344 只、零报错）。

2. **每个指标都带自己的数据日期**。宏观数据的发布时点差异极大（M2 到 8 月、
   社融只到 4 月、中证估值只到 8-27、行情到 9-22），混排而不标日期 = 误导。

3. **色阶只表示变动方向，不表示利好利空**。`chg_dir` 只给 up/down/flat 三种取值，
   具体「涨是好事还是坏事」由读者判断 —— 对「失业率上升」和「用电量上升」，
   同一个箭头方向含义完全相反。
"""
from __future__ import annotations

import time
from datetime import datetime

import pandas as pd

from . import derive as D
from . import indicators as I
from . import source as S
from .clock import ClockResult, locate

#: 单次构建的墙钟预算（秒）。超出后**剩余指标不再拉取**，直接标记为「超出预算未拉取」。
#:
#: 为什么需要这层兜底：单接口已有 30s 超时（`source.timeout`），但 47 个接口若集体异常，
#: 最坏仍是 47 × (30s × 2 次尝试) ≈ 47 分钟 —— 周更任务卡在那里没人会发现。
#: 宁可少几个指标并把原因印在页面上，也不要一个「永远不结束」的定时任务。
BUILD_BUDGET_S = 300.0

# 数据源能力边界 —— B① 决策：暂缺的部分**显式标注**，不做「反正也没人看」的沉默省略
DATA_GAPS: tuple[tuple[str, str, str], ...] = (
    ("美国宏观（CPI / 失业率 / 非农 / 核心 PCE）",
     "AKShare 金十源实测停更于 2025-09，滞后约 12 个月",
     "二期接入 FRED 等海外官方源"),
    ("中国 PPI / PMI",
     "同上（仅有金十源），且 AKShare 无官方源替代接口",
     "二期补源；「PPI − CPI 剪刀差」因此暂缺"),
    ("美元指数 DXY",
     "AKShare 无可用接口（东财源在受限网络下被链路重置）",
     "二期接外盘行情；本期用人民币汇率 + 美债利率间接观察"),
    ("美股总市值 / 美股巴菲特指标",
     "无接口 —— 只有 A 股有官方总市值序列",
     "只做 A 股版，且不与美股横向比较"),
    ("VIX（美股恐慌指数）",
     "AKShare 无该接口；新浪美股源不含波动率指数",
     "以 VHSI（恒指波幅）与 50ETF / 300ETF 波指替代"),
)


def _fmt_num(v: float | None, unit: str = "") -> str:
    """按量级格式化数值（渲染端直接用，不在模板里写格式化逻辑）。"""
    if v is None or pd.isna(v):
        return "—"
    av = abs(v)
    # 先按**单位**分派（单位决定这个数该怎么写），没有特例再按量级兜底。
    if unit in ("倍", "点"):
        return f"{v:,.2f}"
    if unit == "家":
        return f"{v:,.2f}" if av < 100 else f"{v:,.0f}"
    if unit == "%":
        # 百分比类统一 2 位小数。若走下面的「量级」兜底，CPI 0.8% 会写成
        # `0.800%`（小数点后 3 位），社零 0.4% 写成 `0.400%` —— 读数不成体统。
        return f"{v:,.2f}"
    if unit == "":
        # 无单位的都是**指数**（企业景气 / 波指 / 铜金比）。大值只留 1 位小数：
        # 景气指数 109.5 若四舍五入成 110，就把「环比 +0.2」这个**唯一在动的
        # 信息**抹掉了 —— 这类指数的月度变动量级本来就只在小数点后一位。
        if av < 1:
            return f"{v:,.3f}"
        return f"{v:,.2f}" if av < 100 else f"{v:,.1f}"
    if av >= 100000:
        return f"{v:,.0f}"
    if av >= 100:
        return f"{v:,.2f}"
    if av >= 1:
        return f"{v:,.2f}"
    return f"{v:,.3f}"


def _fmt_chg(chg: float | None, chg_unit: str) -> str:
    """变化量文本：`+0.38bp` / `−0.3pct` / `+1.24%`。"""
    if chg is None or pd.isna(chg):
        return "—"
    sign = "+" if chg > 0 else ("−" if chg < 0 else "")
    av = abs(chg)
    if chg_unit == "bp":
        return f"{sign}{av:,.2f}bp"
    if chg_unit == "pct":
        return f"{sign}{av:,.2f}pct"
    return f"{sign}{av:,.2f}%"


def _spark_path(values: list[float], width: float = 104.0, height: float = 26.0) -> str:
    """迷你走势 → SVG path。数据不足 2 点时返回空串（渲染端据此不画）。"""
    if not values or len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = i / (n - 1) * width
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return "M" + " L".join(pts)


def _chg_dir(chg: float | None) -> str:
    """变动方向（仅方向，不含利好利空判断）。"""
    if chg is None or pd.isna(chg):
        return "flat"
    if chg > 0:
        return "up"
    if chg < 0:
        return "down"
    return "flat"


# 判定「数据已过期」的容忍窗口（天）。超过就显式提示，不静默展示。
# 🔴 这不是锦上添花：本次实测就抓到两个接口**默认返回历史区间且不报错**
#    （`currency_boc_sina` 默认 2023 年、`bond_china_yield` 默认 2020–2021 年），
#    以及金十源系列整体停更 12 个月。没有这道自检，页面上就是一个
#    「看起来很正常」的过期数字 —— 本项目吃过太多次静默错数的亏。
_STALE_DAYS = {"日": 12, "月": 95, "季": 200}


def _period_end_date(period: str, freq: str):
    """期间字符串 → 该期间的**结束日**（用于判断数据新鲜度）。"""
    if not period:
        return None
    try:
        if freq == "季" and "Q" in period:
            y, q = period.split("Q")
            return pd.Timestamp(int(y), int(q) * 3, 1) + pd.offsets.MonthEnd(0)
        if freq == "月" and len(period) == 7:
            return pd.Timestamp(period) + pd.offsets.MonthEnd(0)
        ts = pd.to_datetime(period, errors="coerce")
        return None if pd.isna(ts) else ts
    except (ValueError, TypeError):
        return None


def _staleness(period: str, freq: str) -> str:
    """数据新鲜度提示（空串 = 在容忍窗口内）。"""
    end = _period_end_date(period, freq)
    if end is None:
        return ""
    days = (pd.Timestamp.today().normalize() - end.normalize()).days
    if days <= _STALE_DAYS.get(freq, 95):
        return ""
    if freq in ("月", "季"):
        months = round(days / 30.4)
        return f"⚠️ 该数据已滞后约 {months} 个月"
    return f"⚠️ 该数据已滞后 {days} 天"


def _item(ind: I.Indicator, view: D.SeriesView) -> dict:
    """SeriesView → 渲染端 dict。"""
    return {
        "key": ind.key,
        "name": ind.name,
        "short": ind.short or ind.name,
        "unit": ind.unit,
        "group": ind.group,
        "freq": ind.freq,
        "source": ind.source,
        "note": ind.note,
        "snapshot": ind.snapshot,
        "latest": view.latest,
        "latest_text": _fmt_num(view.latest, ind.unit),
        "period": view.period,
        "prev": view.prev,
        "prev_period": view.prev_period,
        "chg": view.chg,
        "chg_unit": view.chg_unit,
        "chg_text": _fmt_chg(view.chg, view.chg_unit),
        "chg_dir": _chg_dir(view.chg),
        "rank": view.rank,
        "rank_pct": (f"{view.rank * 100:.0f}%" if view.rank is not None else ""),
        "spark": view.spark,
        "spark_path": _spark_path(view.spark),
        "spark_dir": _chg_dir((view.spark[-1] - view.spark[0]) if len(view.spark) >= 2 else None),
        "n_obs": view.n_obs,
        "error": view.error,
        "stale": _staleness(view.period, ind.freq) if view.ok else "",
    }


def _load_one(ind: I.Indicator) -> D.SeriesView:
    """拉取单个指标并压成视图；失败时产出「数据缺失」视图（不抛）。"""
    try:
        series = ind.loader()
    except Exception as e:  # noqa: BLE001 - 单指标失败必须降级，不能拖垮整块看板
        return D.view(ind.key, None, freq=ind.freq, unit=ind.chg_unit,
                      error=f"{type(e).__name__}: {e}")

    rank_override = None
    period_override = ""
    # 全 A 估值：用接口自带的**近 10 年**分位（窗口长于本序列回传的 1 年）
    if ind.key in ("a_pe_median", "a_pb_median"):
        try:
            getter = I.a_pe_median_rank if ind.key == "a_pe_median" else I.a_pb_median_rank
            rank_override, period_override = getter()
        except Exception:  # noqa: BLE001 - 分位拿不到就退回自算，不阻断主值
            rank_override = None
            period_override = ""

    return D.view(ind.key, series, freq=ind.freq, unit=ind.chg_unit,
                  rank_override=rank_override, period_override=period_override)


def _clock_inputs(views: dict[str, D.SeriesView], side: str) -> list[tuple[str, str, D.SeriesView]]:
    """组装某一侧的时钟输入：(key, 展示名, 视图)。"""
    out = []
    for ind in I.clock_side(side):
        sv = views.get(ind.key)
        if sv is not None:
            out.append((ind.key, ind.short or ind.name, sv))
    return out


def build_payload(*, verbose: bool = True, budget_s: float = BUILD_BUDGET_S) -> dict:
    """拉全部指标 → 产出渲染 payload。

    Args:
        verbose: 逐条打印取数结果（用 `flush=True`，重定向到文件时也能实时看到进度）。
        budget_s: 墙钟预算，超出后剩余指标标记为未拉取（见 `BUILD_BUDGET_S`）。
    """
    S.clear_cache()
    views: dict[str, D.SeriesView] = {}
    items: dict[str, dict] = {}
    deadline = time.monotonic() + budget_s

    for ind in I.INDICATORS:
        if time.monotonic() > deadline:
            sv = D.view(ind.key, None, freq=ind.freq, unit=ind.chg_unit,
                        error=f"超出本次构建预算（{budget_s:g}s），未拉取")
        else:
            sv = _load_one(ind)
        views[ind.key] = sv
        items[ind.key] = _item(ind, sv)
        if verbose:
            if sv.ok:
                chg = "—" if sv.chg is None else f"{sv.chg:+.3f}"
                print(f"[macro] ✓ {ind.key:<16} {sv.latest:>14,.3f} @ {sv.period:<12} "
                      f"({sv.chg_unit or '-'} {chg})", flush=True)
            else:
                print(f"[macro] ✗ {ind.key:<16} {sv.error}", flush=True)

    clock: ClockResult = locate(_clock_inputs(views, "growth"),
                                _clock_inputs(views, "inflation"))

    groups = []
    for g in I.GROUP_ORDER:
        inds = I.by_group(g)
        groups.append({
            "key": g,
            "title": I.GROUP_TITLE[g],
            "sub": I.GROUP_SUB[g],
            "items": [items[i.key] for i in inds],
        })

    errors = [{"key": ind.key, "name": ind.short or ind.name,
               "error": views[ind.key].error}
              for ind in I.INDICATORS if not views[ind.key].ok]

    calibers = [{
        "key": ind.key, "name": ind.name, "freq": ind.freq,
        "source": ind.source, "caliber": ind.caliber, "note": ind.note,
        "period": views[ind.key].period, "unit": ind.unit,
        "n_obs": views[ind.key].n_obs,
    } for ind in I.INDICATORS]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "clock": {
            "quadrant": clock.quadrant,
            "definition": clock.definition,
            "basis": clock.basis(),
            "note": clock.note,
            "growth_score": clock.growth_score,
            "inflation_score": clock.inflation_score,
            "growth_dir": clock.growth_dir,
            "inflation_dir": clock.inflation_dir,
            "growth_votes": [vars(v) | {"mark": v.mark} for v in clock.growth_votes],
            "inflation_votes": [vars(v) | {"mark": v.mark} for v in clock.inflation_votes],
        },
        "groups": groups,
        "errors": errors,
        "calibers": calibers,
        "data_gaps": [{"title": t, "reason": r, "plan": p} for t, r, p in DATA_GAPS],
        "stats": {
            "total": len(I.INDICATORS),
            "ok": sum(1 for ind in I.INDICATORS if views[ind.key].ok),
            "failed": len(errors),
        },
    }
