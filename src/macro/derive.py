# -*- coding: utf-8 -*-
"""指标派生：把一条时间序列压成渲染端要的「视图」。

为什么要有这一层
----------------
渲染端（HTML / 小红书）需要的东西是固定的六件：**当前值、数据日期、上一期值、
变化量、历史分位、迷你走势**。让每个指标各自去算这六件，必然漂移 ——
本项目已经有过「同一口径两套实现」的教训（memory：`watchlist` 的 PE 两处各算一遍）。

所以：**所有指标都被抽象成「一条按期间升序的 Series」，本层统一压成 `SeriesView`**。
指标之间只差「原始序列怎么来」（`source.py`）与「展示名/单位/口径」（`indicators.py`）。

🔴 变化量的三种口径（`chg_unit`）—— 弄错会把「0.3 个百分点」说成「0.3%」
------------------------------------------------------------------------
| 指标类型 | 口径 | 例 |
|---|---|---|
| 收益率（%） | **bp**（基点，1bp = 0.01%） | 国债 10Y 1.6829% → 次期差 0.38bp |
| 本身就是百分比/增速（%） | **pct**（百分点差） | CPI 同比 0.5% → 0.8%，差 +0.3pct |
| 点位 / 金额 / 家数 | **%**（相对变化率） | 恒指 25087 → 涨 +1.2% |

用相对变化率去描述 CPI 会得到「+60%」这种荒谬结果（0.5→0.8），
用百分点差去描述股价会得到「+0.01」这种没有意义的值。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# 迷你走势的取点数（按发布频率给不同窗口：日频看近 3 个月，月频看近 3 年）
_SPARK_WINDOW = {"日": 60, "月": 36, "季": 16}


def pct_rank(series: pd.Series, value: float | None = None) -> float | None:
    """当前值在**本序列自身历史窗口**中的分位（0~1，越高 = 越偏历史高位）。

    ⚠️ 这与 `stock_a_ttm_lyr` 接口自带的 `quantileInRecent10Years*` 是**两套口径**：
       接口给的是「全 A 估值在其全历史/近 10 年中的分位」（窗口由接口方定义），
       本函数给的是「这条序列在本次拉取窗口内的分位」（窗口 = 接口回传长度）。
       两者不能混用，页面上必须标清各自窗口。
    """
    if series is None or len(series) < 10:
        return None
    v = series.iloc[-1] if value is None else value
    if v is None or pd.isna(v):
        return None
    valid = series.dropna()
    if len(valid) < 10:
        return None
    return float((valid <= v).mean())


def change(latest: float | None, prev: float | None, unit: str) -> tuple[float | None, str]:
    """按 `chg_unit` 口径算变化量，返回 (数值, 单位)。

    相对变化率（`unit == "%"`）的两条自我保护，两条都是**踩过的坑**：
    - 上期为 0 → 不给（除零）
    - 上期与本期**跨零点**（异号）→ 不给（比值无意义）。例：某指标 −3.6 亿 → +0.5 亿，
      按 |上期| 算得 −86%，符号会误导；正确做法是改述「由负转正」这类中性事实，
      由渲染端处理 —— 本函数只负责**不给错的数**。
    """
    if latest is None or prev is None:
        return None, ""
    if pd.isna(latest) or pd.isna(prev):
        return None, ""

    d = latest - prev
    if unit == "bp":
        return d * 100.0, "bp"
    if unit == "pct":
        return d, "pct"

    if prev == 0 or (prev < 0) != (latest < 0):
        return None, ""
    return d / abs(prev) * 100.0, "%"


@dataclass
class SeriesView:
    """一条指标序列的展示视图 —— 渲染端要的一切都在这里。"""

    key: str
    latest: float
    period: str            # 最新数据期间（归一化字符串，如 '2026-08'）
    prev: float | None = None
    prev_period: str = ""
    chg: float | None = None
    chg_unit: str = ""
    rank: float | None = None      # 本序列窗口内分位 0~1
    spark: list[float] = field(default_factory=list)
    n_obs: int = 0
    error: str = ""                # 非空 = 该指标拉取失败，渲染端显示「数据缺失」

    @property
    def ok(self) -> bool:
        return not self.error


def view(key: str, series: pd.Series | None, *,
         freq: str = "月", unit: str = "%", error: str = "",
         rank_override: float | None = None, period_override: str = "") -> SeriesView:
    """Series → SeriesView。

    Args:
        freq: 发布频率（日/月/季），决定迷你走势取几个点。
        unit: 变化量口径（`bp` / `pct` / `%`），见模块 docstring。
        error: 拉取失败时填错误摘要，此时只产出一个「数据缺失」占位视图。
        rank_override: 用接口自带的分位覆盖自算分位（全 A 估值走这条）。
        period_override: 覆盖展示用期间（接口自带分位时，其日期可能与该序列不同步）。
    """
    if error or series is None or len(series) == 0:
        return SeriesView(key=key, latest=float("nan"), period=period_override,
                          n_obs=0, error=error or "无数据")

    s = series.dropna()
    if s.empty:
        return SeriesView(key=key, latest=float("nan"), period=period_override,
                          n_obs=0, error=error or "无数据")

    latest = float(s.iloc[-1])
    period = period_override or str(s.index[-1])
    prev = float(s.iloc[-2]) if len(s) >= 2 else None
    prev_period = str(s.index[-2]) if len(s) >= 2 else ""
    chg, chg_unit = change(latest, prev, unit)

    window = _SPARK_WINDOW.get(freq, 36)
    tail = s.iloc[-window:]

    return SeriesView(
        key=key,
        latest=latest,
        period=period,
        prev=prev,
        prev_period=prev_period,
        chg=chg,
        chg_unit=chg_unit,
        rank=rank_override if rank_override is not None else pct_rank(s),
        spark=[float(x) for x in tail],
        n_obs=int(len(s)),
    )


# --------------------------------------------------------------------------- #
# 派生指标：由若干原始序列算出「新序列」
# --------------------------------------------------------------------------- #
# 这些函数的共同要求：**返回一条按期间升序的 Series**，好让 view() 统一处理。
# 关键在于「两个序列怎么对齐」—— 频率不同的两条序列相减，必须取**共同期间**，
# 否则会拿 A 的 8 月减 B 的 6 月，静默得出错值。


def align_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    """两条序列按**共同期间**对齐后相减（a − b）。

    ⚠️ 有意用 inner join 而不是 reindex+ffill：
       M1 同比（月度）与 M2 同比（月度）本应同期，但接口偶有一方多/少一期；
       GDP 同比（季度）与 CPI 同比（月度）频率不同，强行相减无意义。
       inner join 保证「只在两方都有数的期间出数」，拿不到就不出 —— 宁缺毋错。
    """
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    if joined.empty:
        raise ValueError("两条序列没有共同期间")
    return (joined["a"] - joined["b"]).sort_index()


def align_ratio(a: pd.Series, b: pd.Series, scale: float = 1.0) -> pd.Series:
    """两条序列按共同期间对齐后相除（a / b × scale）。

    用于「铜金比」「总市值 / GDP」这类比值指标。分母为 0/NaN 的期间直接丢弃。
    """
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    joined = joined[joined["b"] != 0]
    if joined.empty:
        raise ValueError("两条序列没有可用的共同期间")
    return (joined["a"] / joined["b"] * scale).sort_index()


def rolling_four_quarter(gdp_abs: pd.Series) -> pd.Series:
    """季度**累计** GDP 绝对值 → 滚动 4 季（TTM）合计。

    🔴 这是巴菲特指标最容易错的一步。`macro_china_gdp` 的绝对值是**年内累计**口径
       （`2026年第1-2季度` = 上半年合计），不是单季值。所以：

           滚动4季(T) = 今年累计(T) + 去年全年 − 去年同期累计

       例：2026Q2 滚动 4 季 = 2026H1 + 2025FY − 2025H1

    ⚠️ 陷阱：把累计当单季直接 ×4，或在 Q1 上做 4 期滑动求和 ——
       前者在 Q4 恰好对（Q4 累计 = 全年）而在 Q1 错 4 倍，后者完全无意义。
       这也是为什么本函数必须**自己按 (年, 季) 定位去年同期**，不能靠 shift。

    ⚠️ 落在 2026Q2（累计半年）与 2025Q4（累计全年）的期间字符串字典序
       `2026Q2` > `2025Q4` 成立，所以按字符串排序取「上一个 Q4」是安全的。
    """
    if gdp_abs is None or gdp_abs.empty:
        raise ValueError("GDP 绝对值序列为空")

    s = gdp_abs.dropna().sort_index()
    periods = list(s.index)
    out: dict[str, float] = {}

    for p in periods:
        if "Q" not in p:
            continue
        year_s, q_s = p.split("Q")
        try:
            year, quarter = int(year_s), int(q_s)
        except ValueError:
            continue

        ytd = float(s[p])                             # 今年累计
        prev_fy_key = f"{year - 1}Q4"                 # 去年全年（累计到 Q4）
        last_year_ytd_key = f"{year - 1}Q{quarter}"   # 去年同期累计
        if prev_fy_key not in s.index or last_year_ytd_key not in s.index:
            continue                                  # 缺去年数据 → 不出数（宁缺毋错）

        out[p] = ytd + float(s[prev_fy_key]) - float(s[last_year_ytd_key])

    if not out:
        raise ValueError("GDP 滚动 4 季：没有任何期间满足「去年全年 + 去年同期」齐全")
    return pd.Series(out).sort_index()


def erp(pe: pd.Series, bond_10y: pd.Series) -> pd.Series:
    """股权风险溢价 ERP = 1/PE − 10Y 国债收益率（%）。

    即 A 股圈说的「格雷厄姆指数」的倒数形态，衡量**股债性价比**。
    比巴菲特指标更可操作：可直接比较、直接对应「偏股还是偏债」的配置问题。

    ⚠️ PE 与国债的频率/期间不同（PE 日频、国债日频，但节假日不同），
       走 `inner` 对齐 —— 只在两者都有值的交易日出数。
    """
    if pe is None or pe.empty:
        raise ValueError("PE 序列为空")
    inv = (1.0 / pe.dropna()) * 100.0
    joined = pd.concat([inv.rename("e"), bond_10y.rename("b")], axis=1, join="inner").dropna()
    if joined.empty:
        raise ValueError("PE 与国债收益率没有共同期间")
    return (joined["e"] - joined["b"]).sort_index()
