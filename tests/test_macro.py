# -*- coding: utf-8 -*-
"""宏观数据层单测：期间归一化、序列规范化、派生算法、周期定位。

🔴 这些测试**不联网** —— 全部用构造数据。理由是宏观接口的可用性会变
   （本次实测就有两个接口「默认参数写死成历史区间」），把测试绑在网上
   会让 CI 变成「网络状况报告」。真正需要验证网络的部分放在构建脚本里。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.macro import build as B_src  # noqa: E402
from src.macro import derive as D  # noqa: E402
from src.macro import indicators as I  # noqa: E402
from src.macro import source as S  # noqa: E402
from src.macro.clock import QUADRANT_DEF, Vote, locate  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import build_macro as B  # noqa: E402


# --------------------------------------------------------------------------- #
# 期间归一化
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw, want", [
    ("2026年08月份", "2026-08"),
    ("2026年第1-2季度", "2026Q2"),      # 累计口径：1-2 季度 = 上半年 → 取后者
    ("2026年第2季度", "2026Q2"),
    ("2026年第1季度", "2026Q1"),
    ("2026年第1-3季度", "2026Q3"),
    ("2026-09-22", "2026-09-22"),
    ("2026-9-2", "2026-09-02"),
    ("202608", "2026-08"),
    ("201501", "2015-01"),
    ("2026.8", "2026-08"),
    ("2003.12", "2003-12"),
    ("20260922", "2026-09-22"),
    ("", ""),
    ("nan", ""),
    ("None", ""),
    ("-", ""),
])
def test_norm_period(raw, want):
    assert S.norm_period(raw) == want


def test_norm_period_handles_datetime_with_time():
    """pandas 读出的 datetime 带时间部分 —— 不剥掉会污染期间键。

    实测来源：`ak.futures_foreign_hist` 的 date 列被读成
    `2026-09-23 00:00:00`，首版正则带 `$` 锚 → 匹配失败 → 期间字符串带时间，
    与其它日频指标的 `2026-09-23` 形态不一致（排序、去重、对齐都会错）。
    """
    assert S.norm_period("2026-09-23 00:00:00") == "2026-09-23"
    assert S.norm_period(pd.Timestamp("2026-09-23")) == "2026-09-23"
    assert S.norm_period("2026-09-23T15:00:00") == "2026-09-23"


def test_norm_period_is_sortable():
    """归一后的字符串必须满足「字典序 == 时间序」（下游全靠排序取上一期）。"""
    raw = ["2026年08月份", "2026年07月份", "2025年12月份", "2026年01月份"]
    got = sorted(S.norm_period(x) for x in raw)
    assert got == ["2025-12", "2026-01", "2026-07", "2026-08"]


# --------------------------------------------------------------------------- #
# to_series：排序 / 去 NaN / 去重
# --------------------------------------------------------------------------- #

def test_to_series_sorts_ascending_when_source_is_descending():
    """🔴 `macro_china_gdp` 原始是**降序**。不排序时「上一期」会取到十几年前。"""
    df = pd.DataFrame({
        "季度": ["2026年第1-2季度", "2026年第1季度", "2025年第1-4季度"],
        "国内生产总值-同比增长": ["4.7", "5.0", "5.2"],
    })
    s = S.to_series(df, "季度", "国内生产总值-同比增长")
    assert list(s.index) == ["2025Q4", "2026Q1", "2026Q2"]
    assert s.iloc[-1] == 4.7
    assert s.iloc[-2] == 5.0


def test_to_series_drops_trailing_nan_period():
    """🔴 `macro_china_stock_market_cap` 的当月行全列 NaN —— 必须剔除，
    否则「最新值」是 NaN 且全链路不报错。"""
    df = pd.DataFrame({
        "数据日期": ["2026年09月份", "2026年08月份", "2026年07月份"],
        "市价总值-上海": [float("nan"), "706966.48", "673143.48"],
    })
    s = S.to_series(df, "数据日期", "市价总值-上海")
    assert s.index[-1] == "2026-08"
    assert s.iloc[-1] == pytest.approx(706966.48)


def test_to_series_keeps_last_duplicate():
    df = pd.DataFrame({"月份": ["2026年08月份", "2026年08月份"], "值": ["1", "2"]})
    s = S.to_series(df, "月份", "值")
    assert len(s) == 1 and s.iloc[0] == 2


def test_to_series_raises_on_missing_column():
    df = pd.DataFrame({"月份": ["2026年08月份"], "值": ["1"]})
    with pytest.raises(KeyError):
        S.to_series(df, "月份", "不存在的列")


def test_to_series_raises_when_all_invalid():
    df = pd.DataFrame({"月份": ["坏日期"], "值": ["abc"]})
    with pytest.raises(ValueError):
        S.to_series(df, "月份", "值")


def test_to_series_raises_on_empty():
    with pytest.raises(ValueError):
        S.to_series(pd.DataFrame(), "月份", "值")


def test_to_series_strips_thousands_separator():
    df = pd.DataFrame({"日期": ["2026-09-22"], "值": ["1,234.5"]})
    assert S.to_series(df, "日期", "值").iloc[0] == pytest.approx(1234.5)


# --------------------------------------------------------------------------- #
# 变化量：三种口径
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("latest, prev, unit, want_val, want_unit", [
    (1.6829, 1.6791, "bp", 0.38, "bp"),        # 收益率 → 基点
    (0.8, 0.5, "pct", 0.3, "pct"),             # 占比指标 → 百分点
    (-3.4, -3.7, "pct", 0.3, "pct"),           # 负值的百分点差也是正的（收窄）
    (100.0, 80.0, "%", 25.0, "%"),             # 水平值 → 相对变化率
    (88.97, 89.86, "%", -0.99, "%"),
])
def test_change_calibers(latest, prev, unit, want_val, want_unit):
    val, u = D.change(latest, prev, unit)
    assert u == want_unit
    assert val == pytest.approx(want_val, abs=1e-3)


@pytest.mark.parametrize("latest, prev", [
    (-1.0, 5.0),      # 由正转负
    (5.0, -1.0),      # 由负转正
])
def test_change_refuses_cross_zero(latest, prev):
    """跨零点不给百分比 —— 比值无意义，必须改述「由负转正」这类中性事实。"""
    val, u = D.change(latest, prev, "%")
    assert val is None and u == ""


def test_change_refuses_zero_base():
    assert D.change(5.0, 0.0, "%") == (None, "")
    assert D.change(5.0, None, "%") == (None, "")


def test_change_negative_same_sign_uses_abs_base():
    """负值同号：分母取 |上期|，结果符号 = 数值增减方向。"""
    val, u = D.change(-1.8, -3.6, "%")     # 从 −3.6 到 −1.8（值变大）
    assert u == "%" and val == pytest.approx(50.0)


# --------------------------------------------------------------------------- #
# 派生：滚动 4 季 GDP（巴菲特指标最容易错的一步）
# --------------------------------------------------------------------------- #

def test_rolling_four_quarter_cumulative():
    """季度**累计**口径 → 滚动 4 季 = 今年累计 + 去年全年 − 去年同期累计。"""
    s = pd.Series({
        "2024Q1": 90.0, "2024Q2": 200.0, "2024Q3": 310.0, "2024Q4": 460.0,
        "2025Q1": 100.0, "2025Q2": 220.0, "2025Q3": 340.0, "2025Q4": 500.0,
        "2026Q1": 110.0, "2026Q2": 240.0,
    }).sort_index()
    out = D.rolling_four_quarter(s)
    # Q4 的性质：当年 Q4 累计 = 全年，去年同期累计也 = 去年全年 → 滚动 4 季 = 当年全年
    assert out["2025Q4"] == pytest.approx(500.0)
    assert out["2026Q1"] == pytest.approx(110 + 500 - 100)
    assert out["2026Q2"] == pytest.approx(240 + 500 - 220)
    # 首年（2024）没有上一年数据 → 不出数
    assert "2024Q4" not in out.index


def test_rolling_four_quarter_skips_when_prev_year_missing():
    """去年数据缺失 → 该期**不出数**（宁缺毋错），而不是猜一个值。"""
    s = pd.Series({"2026Q1": 110.0, "2026Q2": 240.0}).sort_index()
    with pytest.raises(ValueError):
        D.rolling_four_quarter(s)


def test_rolling_four_quarter_differs_from_naive_shift_sum():
    """反例守护：累计口径下，4 期滑动求和**不等于**滚动 4 季。"""
    s = pd.Series({
        "2024Q1": 90.0, "2024Q2": 200.0, "2024Q3": 310.0, "2024Q4": 460.0,
        "2025Q1": 100.0, "2025Q2": 220.0, "2025Q3": 340.0, "2025Q4": 500.0,
        "2026Q1": 110.0, "2026Q2": 240.0,
    }).sort_index()
    correct = D.rolling_four_quarter(s)["2026Q2"]
    naive = s.iloc[-4:].sum()          # 2025Q3+Q4+2026Q1+Q2 = 340+500+110+240
    assert correct == pytest.approx(240 + 500 - 220)
    assert correct != pytest.approx(naive)


def test_quarter_end_month():
    assert I._quarter_end_month("2026Q1") == "2026-03"
    assert I._quarter_end_month("2026Q2") == "2026-06"
    assert I._quarter_end_month("2025Q4") == "2025-12"
    assert I._quarter_end_month("脏数据") == ""


# --------------------------------------------------------------------------- #
# 派生：对齐与比值
# --------------------------------------------------------------------------- #

def test_align_diff_uses_inner_join():
    """🔴 两条序列频率/日历不同，必须按**共同期间**相减。

    反例：M1 到 8 月、M2 到 7 月时各自取最新值相减 → 拿 8 月减 7 月，静默错值。
    """
    a = pd.Series({"2026-06": 6.0, "2026-07": 7.0, "2026-08": 8.0})
    b = pd.Series({"2026-06": 1.0, "2026-07": 2.0})
    out = D.align_diff(a, b)
    assert list(out.index) == ["2026-06", "2026-07"]
    assert list(out.values) == [5.0, 5.0]


def test_align_diff_raises_without_overlap():
    a = pd.Series({"2026-06": 1.0})
    b = pd.Series({"2020-01": 1.0})
    with pytest.raises(ValueError):
        D.align_diff(a, b)


def test_align_ratio_drops_zero_denominator():
    a = pd.Series({"2026-06": 10.0, "2026-07": 20.0})
    b = pd.Series({"2026-06": 0.0, "2026-07": 5.0})
    out = D.align_ratio(a, b)
    assert list(out.index) == ["2026-07"]
    assert out.iloc[0] == pytest.approx(4.0)


def test_erp_formula():
    """ERP = 1/PE − 10Y 国债（%）。"""
    pe = pd.Series({"2026-09-21": 40.0, "2026-09-22": 50.0})
    bond = pd.Series({"2026-09-21": 1.8, "2026-09-22": 1.7})
    out = D.erp(pe, bond)
    assert out["2026-09-21"] == pytest.approx(1 / 40 * 100 - 1.8)
    assert out["2026-09-22"] == pytest.approx(1 / 50 * 100 - 1.7)


def test_erp_requires_overlap():
    with pytest.raises(ValueError):
        D.erp(pd.Series({"2026-01": 10.0}), pd.Series({"2020-01": 1.0}))


# --------------------------------------------------------------------------- #
# 视图：分位与迷你走势
# --------------------------------------------------------------------------- #

def test_pct_rank_direction():
    """分位越高 = 越偏历史高位（对 PE 是「越贵」，对 CPI 是「通胀越高」）。

    用 `<=` 计数（含自身），所以历史最大值分位是 100%、最小值是 1/N。
    """
    s = pd.Series([float(i) for i in range(100)])
    assert D.pct_rank(s) == pytest.approx(1.0)
    assert D.pct_rank(s, value=0.0) == pytest.approx(0.01)
    assert D.pct_rank(s, value=49.0) == pytest.approx(0.5)


def test_pct_rank_needs_enough_history():
    assert D.pct_rank(pd.Series([1.0, 2.0, 3.0])) is None


def test_view_carries_period_and_prev():
    s = pd.Series({f"2026-0{i}": float(i) for i in range(1, 10)}).sort_index()
    v = D.view("k", s, freq="月", unit="pct")
    assert v.ok
    assert v.period == "2026-09"
    assert v.prev_period == "2026-08"
    assert v.latest == 9.0 and v.prev == 8.0
    assert v.chg == pytest.approx(1.0)
    assert v.n_obs == 9


def test_view_error_path():
    v = D.view("k", None, freq="月", unit="pct", error="ConnectionError: boom")
    assert not v.ok
    assert "boom" in v.error
    assert math.isnan(v.latest)


def test_view_rank_override_wins():
    """全 A 估值走接口自带的近 10 年分位（窗口长于序列本身）。"""
    s = pd.Series([float(i) for i in range(100)])
    v = D.view("a_pe_median", s, freq="日", unit="%", rank_override=0.697, period_override="2026-09-22")
    assert v.rank == pytest.approx(0.697)
    assert v.period == "2026-09-22"


# --------------------------------------------------------------------------- #
# 周期定位
# --------------------------------------------------------------------------- #

def _sv(value, prev, unit="pct"):
    s = pd.Series({"2026-07": float(prev), "2026-08": float(value)})
    return D.view("k", s, freq="月", unit=unit)


@pytest.mark.parametrize("g_up, i_up, want", [
    (True, True, "过热"),
    (True, False, "复苏"),
    (False, True, "滞胀"),
    (False, False, "衰退"),
])
def test_locate_quadrants(g_up, i_up, want):
    g = [("a", "A", _sv(6.0 if g_up else 4.0, 5.0)),
         ("b", "B", _sv(6.0 if g_up else 4.0, 5.0))]
    i = [("c", "C", _sv(3.0 if i_up else 1.0, 2.0)),
         ("d", "D", _sv(3.0 if i_up else 1.0, 2.0))]
    res = locate(g, i)
    assert res.quadrant == want
    assert res.definition == QUADRANT_DEF[want]
    assert res.growth_score == (2 if g_up else -2)


def test_locate_reports_divergence_not_a_quadrant():
    """净票为 0 时**不允许**硬塞象限，也不允许沉默 —— 如实报「方向分歧」。"""
    g = [("a", "A", _sv(6.0, 5.0)), ("b", "B", _sv(4.0, 5.0))]
    i = [("c", "C", _sv(3.0, 2.0)), ("d", "D", _sv(1.0, 2.0))]
    res = locate(g, i)
    assert res.quadrant == "方向分歧"
    assert res.definition == ""
    assert "互相抵消" in res.note
    assert res.growth_score == 0 and res.inflation_score == 0


def test_locate_abstains_silently_on_broken_view():
    """取数失败的指标**弃权**，不当作「持平」计入。"""
    broken = D.view("x", None, freq="月", unit="pct", error="boom")
    g = [("a", "A", _sv(6.0, 5.0)), ("x", "X", broken)]
    i = [("c", "C", _sv(3.0, 2.0))]
    res = locate(g, i)
    assert res.quadrant == "过热"          # 只按有效票定位
    assert res.growth_score == 1
    abstained = [v for v in res.growth_votes if v.abstain]
    assert len(abstained) == 1 and abstained[0].mark == "—"


def test_vote_mark_for_cross_zero_is_abstain():
    sv = D.view("k", pd.Series({"2026-07": -1.0, "2026-08": 5.0}), freq="月", unit="%")
    from src.macro.clock import _vote
    v = _vote("k", "K", sv)
    assert v.abstain and v.mark == "—"


# --------------------------------------------------------------------------- #
# 指标定义表完整性
# --------------------------------------------------------------------------- #

def test_indicator_keys_unique():
    keys = [ind.key for ind in I.INDICATORS]
    assert len(keys) == len(set(keys)), f"重复 key：{_dupes(keys)}"


def test_indicator_groups_valid():
    for ind in I.INDICATORS:
        assert ind.group in I.GROUP_ORDER, f"{ind.key} 的 group 非法：{ind.group}"
        assert ind.group in I.GROUP_TITLE and ind.group in I.GROUP_SUB


def test_indicator_clock_side_valid():
    for ind in I.INDICATORS:
        assert ind.clock in ("", "growth", "inflation"), f"{ind.key} 的 clock 非法"


def test_indicator_chg_unit_valid():
    for ind in I.INDICATORS:
        assert ind.chg_unit in ("bp", "pct", "%"), f"{ind.key} 的 chg_unit 非法"


def test_indicator_freq_valid():
    for ind in I.INDICATORS:
        assert ind.freq in ("日", "月", "季"), f"{ind.key} 的 freq 非法"


def test_every_indicator_has_source_and_caliber():
    """口径与数据源不允许留空 —— 页面上每一条都要能追溯到接口。"""
    for ind in I.INDICATORS:
        assert ind.source.strip(), f"{ind.key} 缺数据源"
        assert ind.caliber.strip(), f"{ind.key} 缺口径说明"


def test_all_groups_are_populated():
    for g in I.GROUP_ORDER:
        assert I.by_group(g), f"分组 {g} 没有任何指标"


def test_clock_has_both_sides():
    """周期定位两侧都必须有指标，否则象限永远无法判定。"""
    assert I.clock_side("growth"), "增长侧没有指标"
    assert I.clock_side("inflation"), "通胀侧没有指标"


def test_snapshot_indicators_declared():
    """快照类指标必须显式标 snapshot（无历史序列，不参与分位与走势）。"""
    snaps = {ind.key for ind in I.INDICATORS if ind.snapshot}
    assert snaps == {"sse_pe", "vhsi"}


# --------------------------------------------------------------------------- #
# 展示文案清洁度
# --------------------------------------------------------------------------- #

#: 展示字段里**绝不允许**出现的标记语法。看板与图卡都是纯 HTML，渲染端不做
#: markdown 解析 —— 写了 `**重点**` 就会在页面上原样显示星号。
#: 实测漏过 3 处（`clock` 的分歧提示 + 两条 `caliber`），全是在 HTML 产出后
#: 肉眼翻页面才发现的：这类错误**任何一层都不会报错**，所以固化成断言。
_MD_MARKERS = ("**",)


def test_indicator_display_text_has_no_markdown():
    bad = []
    for ind in I.INDICATORS:
        for field in ("name", "short", "unit", "caliber", "note"):
            v = getattr(ind, field) or ""
            for m in _MD_MARKERS:
                if m in v:
                    bad.append(f"{ind.key}.{field} 含 {m!r}：{v[:70]}")
    assert not bad, "以下展示字段含标记语法，会原样显示在页面上：\n" + "\n".join(bad)


def test_clock_note_has_no_markdown():
    """分歧提示会原样进入页面与图卡，同样不许带标记语法。"""
    g = [("a", "A", _sv(6.0, 5.0)), ("b", "B", _sv(4.0, 5.0))]
    i = [("c", "C", _sv(3.0, 2.0)), ("d", "D", _sv(1.0, 2.0))]
    note = locate(g, i).note
    assert note, "分歧时必须给出说明文案（沉默不可接受）"
    for m in _MD_MARKERS:
        assert m not in note, f"分歧提示含 {m!r}：{note}"


def test_data_gap_text_has_no_markdown():
    """能力边界清单（`build.DATA_GAPS`，三元组）也是纯文本进 HTML。"""
    for title, reason, plan in B_src.DATA_GAPS:
        for field, v in (("title", title), ("reason", reason), ("plan", plan)):
            for m in _MD_MARKERS:
                assert m not in v, f"DATA_GAPS[{title}].{field} 含 {m!r}：{v[:70]}"


# --------------------------------------------------------------------------- #
# 渲染层小工具
# --------------------------------------------------------------------------- #

def test_spark_path_requires_two_points():
    """迷你走势：不足 2 点返回空串（渲染端据此不画）。"""
    assert B_src._spark_path([]) == ""
    assert B_src._spark_path([1.0]) == ""
    p = B_src._spark_path([1.0, 3.0, 2.0])
    assert p.startswith("M") and "L" in p
    pts = p.replace("M", "").split(" L")
    assert pts[0] == "0.0,26.0"      # 最小值 → 落到下边界
    assert pts[1] == "52.0,0.0"      # 最大值 → 落到上边界
    assert pts[2] == "104.0,13.0"    # 中间值 → 落在中间
    assert len(p.split("L")) - 1 == 2


def test_staleness_flags_old_data():
    """数据过期自检：滞后超窗口要显式提示（本次实测抓到两个接口静默返回历史数据）。"""
    from src.macro.build import _period_end_date, _staleness
    assert _staleness("", "月") == ""
    assert _period_end_date("2026-08", "月") == pd.Timestamp("2026-08-31")
    assert _period_end_date("2026Q2", "季") == pd.Timestamp("2026-06-30")
    assert _period_end_date("2026-09-22", "日") == pd.Timestamp("2026-09-22")
    # 选一个明确晚于容忍窗口的期间
    old = (pd.Timestamp.today() - pd.Timedelta(days=400)).strftime("%Y-%m")
    assert "滞后" in _staleness(old, "月")


def _min_payload() -> dict:
    """最小可用 payload —— 看板与图卡两套渲染端共用。"""
    item = {
        "key": "k", "name": "测试指标", "short": "测试", "unit": "%", "group": "增长",
        "freq": "月", "source": "test", "note": "", "snapshot": False,
        "latest": 1.0, "latest_text": "1.00", "period": "2026-08",
        "prev": 0.5, "prev_period": "2026-07", "chg": 0.5, "chg_unit": "pct",
        "chg_text": "+0.50pct", "chg_dir": "up", "rank": 0.5, "rank_pct": "50%",
        "spark": [0.1, 0.5, 1.0], "spark_path": "M0,26 L52,13 L104,0",
        "spark_dir": "up", "n_obs": 3, "error": "", "stale": "",
    }
    payload = {
        "generated_at": "2026-09-23 12:00",
        "clock": {
            "quadrant": "滞胀", "definition": "增长回落、通胀回升",
            "basis": "增长侧 2 项净得 -2（下行）· 通胀侧 1 项净得 +1（上行）",
            "note": "", "growth_score": -2, "inflation_score": 1,
            "growth_dir": "下行", "inflation_dir": "上行",
            "growth_votes": [{"key": "a", "name": "A", "period": "2026-08",
                              "latest": 1.0, "prev": 2.0, "direction": -1,
                              "abstain": False, "mark": "↓"}],
            "inflation_votes": [{"key": "b", "name": "B", "period": "2026-08",
                                 "latest": 2.0, "prev": 1.0, "direction": 1,
                                 "abstain": False, "mark": "↑"}],
        },
        "groups": [{"key": "增长", "title": "T", "sub": "S", "items": [item]}],
        "errors": [],
        "calibers": [{"key": "k", "name": "测试指标", "freq": "月", "source": "test",
                      "caliber": "测试口径", "note": "", "period": "2026-08",
                      "unit": "%", "n_obs": 3}],
        "data_gaps": [{"title": "缺口", "reason": "原因", "plan": "计划"}],
        "stats": {"total": 1, "ok": 1, "failed": 0},
    }
    return payload


def test_render_smoke():
    """渲染烟雾测试：用最小 payload 跑一遍，确保模板占位符全部被替换。"""
    html = B.render(_min_payload())
    assert "@@" not in html, "模板占位符未被完全替换"
    assert "__CSS__" not in html
    assert "滞胀" in html and "测试指标" in html
    # 当前象限那一格被高亮（其余三格不带 on）
    import re
    m = re.search(r'<div class="q (on)?"><div class="qn">滞胀</div>', html)
    assert m and m.group(1) == "on", "当前象限未被高亮"
    assert html.count('<div class="q on">') == 1, "只应高亮一个象限"


# --------------------------------------------------------------------------- #
# 输出层的「印刷错误」（印刷层不解析任何标记，也不该漏出裸符号）
# --------------------------------------------------------------------------- #

def test_xhs_html_has_no_stray_dollar():
    """🔴 f-string 里写 `${...}` 只会得到字面 `$`。

    实测漏过一次：封面印成「下面 **$8** 张图」。这类错误**任何一层都不会报错**，
    只有把图放大看才会发现 —— 而图已经发出去了。
    """
    import build_xhs_macro as X
    html = X.build_html(_min_payload())
    assert "$" not in html, "图卡 HTML 出现多余 `$`（f-string 里的 `${...}` 不是占位符）"


def test_no_markdown_in_xhs_html():
    """图卡 HTML 同样不解析 markdown，星号会原样印出。"""
    import build_xhs_macro as X
    assert "**" not in X.build_html(_min_payload())


def test_clock_axis_labels_are_unambiguous():
    """🔴 2×2 网格的真实语义是「行 = 增长、列 = 通胀」。

    所以**行标签不能像列标签那样左右分置** —— 分置会被读成「列」，与网格相反
    （实测漏过一次：上排写「增长 ↑ | 增长 ↓」，读者会以为左列是增长回升）。
    """
    import build_xhs_macro as X
    xhs = X.build_html(_min_payload())
    assert "上排" in xhs and "下排" in xhs, "图卡：行标签未写明上排/下排"
    assert "左列" in xhs and "右列" in xhs, "图卡：列标签未写明左列/右列"

    dash = B.render(_min_payload())
    assert "上排：增长" in dash, "看板：行标签未写明上排/下排"
    assert "左列：通胀" in dash, "看板：列标签未写明左列/右列"


def test_fmt_num_keeps_one_decimal_for_index_values():
    """无单位的是指数类：109.5 不能抹成 110。

    景气指数每月只动 0.2，抹成整数就把「唯一在动的信息」吃掉了 ——
    比照「家数」：那是真整数，仍用 0 位小数。
    """
    assert B_src._fmt_num(109.5, "") == "109.5"
    assert B_src._fmt_num(13.99, "") == "13.99"
    assert B_src._fmt_num(0.156, "") == "0.156"      # 铜金比，小值要 3 位
    assert B_src._fmt_num(491.0, "家") == "491"      # 家数是真的整数


def test_fmt_num_percent_is_two_decimals():
    """百分比类不许掉进「量级」兜底的 3 位小数（CPI 会写成 0.800%）。"""
    assert B_src._fmt_num(0.8, "%") == "0.80"
    assert B_src._fmt_num(0.4, "%") == "0.40"
    assert B_src._fmt_num(4.1, "%") == "4.10"
    assert B_src._fmt_num(39.3467, "%") == "39.35"   # 注：Python 是「五取偶」，别用 x.xx5 当样例
    # 单位是 pct（百分点）的走量级兜底，信用利差这类需要 3 位小数
    assert B_src._fmt_num(0.206, "pct") == "0.206"
    assert B_src._fmt_num(-3.4, "pct") == "-3.40"


def test_clock_vote_value_reuses_item_text():
    """图 2 的投票值必须与其它图上的写法逐字一致。

    同一个指标会出现在多张图上（企业景气在「周期定位」与「增长与通胀」都出现），
    两处各自格式化必然出现 `109.50` vs `109.5` 这种对不上的写法。
    """
    import build_xhs_macro as X
    payload = _min_payload()
    payload["groups"][0]["items"][0]["latest_text"] = "109.5"
    payload["clock"]["growth_votes"][0].update({"key": "k", "latest": 109.5})
    html = X.build_html(payload)
    assert "109.50" not in html, "图 2 又把原始值重新格式化了一遍"
    assert "109.5" in html


def test_thin_sample_percentile_is_labelled():
    """观测数太少时分位必须标出样本量。

    `csi300_pe` 这类中证官方源一次只回传约 20 个交易日：20 点算出的分位
    与 6177 点算出的分位在页面上长得一模一样，不标就会当成「历史分位」用。
    """
    import build_xhs_macro as X
    thin = dict(_min_payload()["groups"][0]["items"][0], rank=0.5, n_obs=20)
    assert "20" in X._rank_label(thin), "图卡：薄样本未标出观测数"
    fat = dict(thin, n_obs=6177)
    assert "6177" not in X._rank_label(fat), "图卡：长样本不必标观测数"
    assert "仅" in B._rank_html(thin), "看板：薄样本未标出观测数"
    assert "仅" not in B._rank_html(fat), "看板：长样本不必标观测数"


def _dupes(seq):
    seen, out = set(), []
    for x in seq:
        if x in seen and x not in out:
            out.append(x)
        seen.add(x)
    return out
