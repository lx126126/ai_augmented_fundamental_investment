# -*- coding: utf-8 -*-
"""「主要变动指标」候选榜（`src/report/swing.py`）与它在渲染层的接线。

这块替换掉了原先的「现金流异动归因」，所以要守住两件不同的事：

1. **事实层**（本文件前半）：榜单必须是客观算出来的 —— 单位（元→亿元）、同比基数、
   分组配额、缺列与缺去年同期的容错，任何一条错了报告里的数字都不会报错，只会错。
2. **渲染层**（后半）：现金流那套「总额 vs 剔除财务公司科目后」的对照条，
   只在经营现金流自己进了榜时才出现；没进榜时它必须**完全消失**，
   否则就又回到「每个标的都挂一个现金流板块、多数标的只能写数据未取到」的老样子。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.report.swing import build  # noqa: E402

YI = 1e8  # raw 层的金额单位是元


# --------------------------------------------------------------------------- #
# 构造合成报表
# --------------------------------------------------------------------------- #
def _frame(records):
    """[(报告日, {列: 值}), ...] → DataFrame（缺的列自然变成 NaN）。"""
    rows = []
    for d, vals in records:
        r = {"report_date": pd.Timestamp(d)}
        r.update(vals)
        rows.append(r)
    return pd.DataFrame(rows)


def _raw(cur: dict | None = None, prev: dict | None = None,
         bs_cur: dict | None = None, bs_prev: dict | None = None,
         cf_cur: dict | None = None, cf_prev: dict | None = None):
    """默认给一组「去年同期齐全」的三张表，测试只覆盖自己关心的一列。"""
    base_ps = {"revenue": 1000 * YI}
    base_bs = {"monetary_funds": 100 * YI, "inventory": 100 * YI}
    base_cf = {"ocf": 100 * YI}
    return {
        "profit_sheet": _frame([
            ("2025-06-30", {**base_ps, **(prev or {})}),
            ("2026-06-30", {**base_ps, **(cur or {})}),
        ]),
        "balance_sheet": _frame([
            ("2025-06-30", {**base_bs, **(bs_prev or {})}),
            ("2026-06-30", {**base_bs, **(bs_cur or {})}),
        ]),
        "cash_flow": _frame([
            ("2025-06-30", {**base_cf, **(cf_prev or {})}),
            ("2026-06-30", {**base_cf, **(cf_cur or {})}),
        ]),
    }


def _names(out):
    return [e["名称"] for e in out["候选"]]


# --------------------------------------------------------------------------- #
# 单位与口径
# --------------------------------------------------------------------------- #
def test_amounts_are_yi_not_yuan():
    """raw 存的是元，榜单必须换算成亿元 —— 差 1e8 倍的错误不会报错，只会离谱。"""
    out = build(_raw(cur={"sell_expense": 30 * YI}, prev={"sell_expense": 10 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "销售费用")
    assert e["本期_亿元"] == 30.0
    assert e["上期_亿元"] == 10.0
    assert e["变动_亿元"] == 20.0


def test_yoy_and_share_of_revenue():
    """含财务公司的标的营收口径易混，榜单里直接给出「占营业总收入」好让 LLM 判重要性。"""
    out = build(_raw(cur={"sell_expense": 30 * YI}, prev={"sell_expense": 10 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "销售费用")
    assert e["同比_pct"] == 200.0
    assert e["占营业总收入_pct"] == 3.0


def test_labels_and_caliber_stated():
    """累计 vs 时点必须写在事实里 —— 不写的话 LLM 会把 H1 累计当单季说。"""
    out = build(_raw(cur={"sell_expense": 30 * YI}, prev={"sell_expense": 10 * YI},
                     bs_cur={"inventory": 300 * YI}, bs_prev={"inventory": 100 * YI}))
    assert out["报告期"] == "2026H1"
    assert out["上期"] == "2025H1"
    assert "年初至今累计" in out["口径说明"]
    assert "季末时点" in out["口径说明"]
    group_caliber = {e["分组"]: e["口径"] for e in out["候选"]}
    assert group_caliber["损益"] == "年初至今累计"
    assert group_caliber["资产负债"] == "季末时点"


def test_report_period_uses_month_not_fiscal_guess():
    """3/6/9/12 月分别标 Q1 / H1 / Q3 / 年报；港股只有 6 月与 12 月也走同一条。"""
    raw = _raw()
    raw["profit_sheet"] = _frame([
        ("2025-12-31", {"revenue": 1000 * YI}),
        ("2026-12-31", {"revenue": 1100 * YI}),
    ])
    assert build(raw)["报告期"] == "2026年报"


def test_bank_without_revenue_column_falls_back_to_operating_revenue():
    """银行 raw 里没有 revenue 列（只有 operating_revenue），地板要能算出来而不是 0。"""
    raw = _raw()
    raw["profit_sheet"] = _frame([
        ("2025-06-30", {"operating_revenue": 2000 * YI, "sell_expense": 10 * YI}),
        ("2026-06-30", {"operating_revenue": 2200 * YI, "sell_expense": 30 * YI}),
    ])
    out = build(raw)
    assert "销售费用" in _names(out)


# --------------------------------------------------------------------------- #
# 入榜门槛
# --------------------------------------------------------------------------- #
def test_below_floor_is_excluded():
    """变动不到地板的小科目不进榜：它们靠百分比能挤进前几名，但对报表毫无影响。"""
    out = build(_raw(bs_cur={"inventory": 100.5 * YI}, bs_prev={"inventory": 100 * YI}))
    assert "存货" not in _names(out)


def test_small_yoy_is_excluded():
    """金额过线但同比不到 10% 的，不算「异动」。"""
    out = build(_raw(cur={"admin_expense": 105 * YI}, prev={"admin_expense": 100 * YI}))
    assert "管理费用" not in _names(out)


def test_negative_prior_uses_abs_denominator():
    """上期与本期**同号且都为负** → 同比的分母取 |上期|，而不是把百分比置空。

    `cur / prev - 1` 在上期为负时给出的是**方向相反**的数字：格力 2026H1 投资活动
    现金流从 -342.7 亿收窄到 -27.5 亿，该式给 **-92.0%**（读者读成「净额下降 92%」），
    而事实是**流出收窄 92%**。改用 |上期| 后得 +92.0%，与「变动 +315.2 亿」同向。
    """
    out = build(_raw(cf_cur={"icf": -27.5 * YI}, cf_prev={"icf": -342.7 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "投资活动产生的现金流量净额")
    assert e["变动_亿元"] == 315.2
    assert e["同比_pct"] == 92.0, "上期为负时应按 |上期| 给百分比，而不是留空"
    assert e["同比口径"] == "上期为负"
    assert out["特殊同比科目"] == ["投资活动产生的现金流量净额"]


def test_positive_prior_formula_is_identical_to_old_one():
    """上期为正时 |上期| ≡ 上期 → 新式与旧式 `cur/prev - 1` 逐字恒等。

    这条是整次改动的安全边界：只有「上期为负」的科目允许变，其余必须一个数都不动。
    """
    for cur, prev, want in [(30.0, 10.0, 200.0), (9.0, 10.0, -10.0)]:
        out = build(_raw(cur={"sell_expense": cur * YI},
                         prev={"sell_expense": prev * YI}))
        e = next(x for x in out["候选"] if x["名称"] == "销售费用")
        assert e["同比_pct"] == want
        assert "同比口径" not in e, "上期为正的行不该被打上特殊口径标记"


def test_yoy_sign_always_matches_delta_sign():
    """同号时，同比的符号必须与同一行「变动」列同向 —— 这是改动要保住的核心不变量。

    旧式在上期为负时会违反它（变动 +315.2 却印出 -92.0%），那种自相矛盾的一行
    比留空更糟：读者不知道该信哪一列。
    ⚠️ 跨零的行不给百分比（见 `test_crossing_zero_drops_pct`），所以这里只覆盖同号。
    """
    cases = [(-27.5, -342.7), (-400.0, -342.7), (-100.0, -50.0), (30.0, 10.0), (9.0, 10.0)]
    for cur, prev in cases:
        out = build(_raw(cf_cur={"icf": cur * YI}, cf_prev={"icf": prev * YI}))
        e = next(x for x in out["候选"] if x["名称"] == "投资活动产生的现金流量净额")
        d = e["变动_亿元"]
        y = e["同比_pct"]
        assert y is not None, f"{prev} → {cur} 不该留空"
        assert (d > 0) == (y > 0) or d == y == 0, \
            f"变动 {d:+.2f} 与同比 {y:+.1f}% 符号相反"


def test_crossing_zero_drops_pct():
    """上期与本期**符号相反**（跨越零点）→ 不给百分比。

    两个原因叠加：① 分母过小会让比值失真到不可读 —— 实测茅台 2026H1 投资活动
    现金流 -3.10 亿 → +253.1 亿，Δ/|上期| = **+8265.9%**，这个数字没人会信，
    还会连累读者怀疑整张表；② 跨零处「增长/下降」本就没有意义。
    """
    out = build(_raw(cf_cur={"icf": 253.1 * YI}, cf_prev={"icf": -3.1 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "投资活动产生的现金流量净额")
    assert e["变动_亿元"] == 256.2
    assert e["同比_pct"] is None, "跨零不该给百分比（会算出 8265.9% 这种失真比值）"
    assert e["同比口径"] == "由负转正"
    assert "投资活动产生的现金流量净额" in out["特殊同比科目"]


def test_crossing_zero_the_other_way():
    """由正转负同理 —— 表述必须中性（「由正转负」而不是「转负」这种带好坏倾向的词）。"""
    out = build(_raw(cf_cur={"icf": -50.0 * YI}, cf_prev={"icf": 100.0 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "投资活动产生的现金流量净额")
    assert e["同比_pct"] is None
    assert e["同比口径"] == "由正转负"


def test_finance_expense_net_income_base_crosses_zero():
    """财务费用上期是净收益（负数）、本期转为净支出 → 跨越零点，不给百分比。"""
    out = build(_raw(cur={"finance_expense": 8 * YI}, prev={"finance_expense": -5 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "财务费用")
    assert e["变动_亿元"] == 13.0
    assert e["同比_pct"] is None
    assert e["同比口径"] == "由负转正"


def test_tiny_negative_prior_base_still_drops_pct():
    """上期规模**小于地板**时仍不给百分比（哪怕不跨零）。

    否则会算出「本期 5 亿 / 上期 -0.5 亿 → +1100%」这种量级失真的数字 ——
    与「上期 0.09 亿」那类小基数是同一个坑，只是符号不同。
    """
    out = build(_raw(cf_cur={"icf": -5 * YI}, cf_prev={"icf": -0.5 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "投资活动产生的现金流量净额")
    assert e["同比_pct"] is None
    assert e["变动_亿元"] == -4.5
    assert "同比口径" not in e
    assert out["特殊同比科目"] == []


def test_tiny_prior_base_drops_pct():
    """上期基数远小于地板时不给百分比。

    实测踩过：投资活动现金流上期 0.09 亿、本期 -9.89 亿 → 算出「同比 -11088.9%」，
    铺在报告里只会让人不敢信整张表。变动金额本身已经把问题说清楚了。
    """
    out = build(_raw(cf_cur={"icf": -9.89 * YI}, cf_prev={"icf": 0.09 * YI}))
    e = next(x for x in out["候选"] if x["名称"] == "投资活动产生的现金流量净额")
    assert e["同比_pct"] is None
    assert e["变动_亿元"] == -9.98


def test_missing_prior_row_is_excluded():
    """缺去年同期可比数 → 不进榜（没有对照就没有「异动」可言）。"""
    raw = _raw()
    raw["profit_sheet"] = _frame([("2026-06-30", {"revenue": 1000 * YI,
                                                  "sell_expense": 30 * YI})])
    out = build(raw)
    assert "销售费用" not in _names(out)


def test_missing_column_is_silent():
    """银行没有销售费用这一列、港股报表只有十几个字段 → 静默跳过，不能抛。"""
    out = build(_raw())
    assert all(e["名称"] != "销售费用" for e in out["候选"])


# --------------------------------------------------------------------------- #
# 分组与配额
# --------------------------------------------------------------------------- #
def test_group_quota_keeps_each_statement_visible():
    """分组配额是这张榜的核心设计：整体排序会让资产负债表科目（银行动辄几百亿）
    把损益与现金流科目全部挤掉，而「利润为什么变了」只能从损益科目上读出来。"""
    out = build(_raw(
        cur={"sell_expense": 30 * YI, "admin_expense": 25 * YI,
             "research_expense": 20 * YI, "finance_expense": 18 * YI,
             "invest_income": 15 * YI, "operate_tax_add": 12 * YI},
        prev={"sell_expense": 10 * YI, "admin_expense": 10 * YI,
              "research_expense": 10 * YI, "finance_expense": 10 * YI,
              "invest_income": 10 * YI, "operate_tax_add": 10 * YI},
        bs_cur={"monetary_funds": 900 * YI, "inventory": 300 * YI,
                "accounts_payable": 200 * YI, "goodwill": 150 * YI, "borrowings": 120 * YI},
        bs_prev={"monetary_funds": 100 * YI, "inventory": 100 * YI,
                 "accounts_payable": 100 * YI, "goodwill": 100 * YI, "borrowings": 100 * YI},
        cf_cur={"ocf": 500 * YI, "icf": 400 * YI, "financing_cash_flow": 300 * YI,
                "depreciation": 200 * YI},
        cf_prev={"ocf": 100 * YI, "icf": 100 * YI, "financing_cash_flow": 100 * YI,
                 "depreciation": 100 * YI},
    ))
    got = _names(out)
    # 损益组 6 个候选只留变动最大的 4 个
    assert [n for n in got if n in {
        "销售费用", "管理费用", "研发费用", "财务费用", "投资收益", "税金及附加",
    }] == ["销售费用", "管理费用", "研发费用", "财务费用"]
    # 现金流组配额 3
    assert sum(1 for n in got if n in {
        "经营活动产生的现金流量净额", "投资活动产生的现金流量净额",
        "筹资活动产生的现金流量净额", "固定资产折旧",
    }) == 3
    # 资产负债组配额 4
    assert sum(1 for n in got if n in {
        "货币资金", "存货", "应付账款", "商誉", "借款合计（短期 + 长期）",
    }) == 4


def test_groups_are_emitted_in_fixed_order():
    """固定成 损益 → 现金流 → 资产负债，否则每次构建表格顺序都在换。"""
    out = build(_raw(
        cur={"sell_expense": 30 * YI}, prev={"sell_expense": 10 * YI},
        bs_cur={"inventory": 300 * YI}, bs_prev={"inventory": 100 * YI},
        cf_cur={"ocf": 400 * YI}, cf_prev={"ocf": 100 * YI},
    ))
    groups = [e["分组"] for e in out["候选"]]
    assert groups == sorted(groups, key=["损益", "现金流", "资产负债"].index)


def test_group_sorted_by_abs_delta_desc():
    """组内按「变动金额绝对值」降序 —— 序号即重要性，渲染表格直接照抄。"""
    out = build(_raw(
        cur={"sell_expense": 30 * YI, "admin_expense": 60 * YI},
        prev={"sell_expense": 10 * YI, "admin_expense": 10 * YI},
    ))
    loss = [e["名称"] for e in out["候选"] if e["分组"] == "损益"]
    assert loss == ["管理费用", "销售费用"]


def test_derived_and_headline_columns_never_enter():
    """总量/派生科目（总资产、营业利润、营业总收入、归母净利润）有意不入榜：
    它们是加总结果，进榜必然霸榜把配额吃光。"""
    out = build(_raw(
        cur={"revenue": 2000 * YI, "operating_profit": 500 * YI},
        prev={"revenue": 1000 * YI, "operating_profit": 100 * YI},
        bs_cur={"total_assets": 9000 * YI}, bs_prev={"total_assets": 1000 * YI},
    ))
    for banned in ("营业总收入", "归母净利润", "营业利润", "资产总计"):
        assert banned not in _names(out)


# --------------------------------------------------------------------------- #
# 渲染层接线：现金流附注只在经营现金流入榜时出现
# --------------------------------------------------------------------------- #
def _bv():
    return importlib.import_module("build_valueline")


OCF = "经营活动产生的现金流量净额"


def _render(ocf_in_grade: bool, ocf_facts: dict | None):
    bv = _bv()
    bv.SWING_FACTS = {
        "报告期": "2026H1", "上期": "2025H1",
        "经营现金流是否入榜": ocf_in_grade,
        "候选": [{
            "名称": OCF if ocf_in_grade else "存货", "分组": "现金流",
            "口径": "年初至今累计", "本期_亿元": 706.91, "上期_亿元": 131.19,
            "变动_亿元": 575.72, "同比_pct": 438.8,
        }],
        "口径说明": "口径", "门槛说明": "门槛", "未入榜说明": "说明",
    }
    bv.OPERATING = {"现金流归因": ocf_facts} if ocf_facts is not None else None
    html = bv._swing_block({"swing": "正文"})
    bv.SWING_FACTS = None
    bv.OPERATING = None
    return html


def test_annex_renders_when_ocf_in_grade():
    html = _render(True, {
        "期间": "2026H1",
        "经营活动产生的现金流量净额_亿元": {"本期": 706.91, "上期": 131.19},
        "净额同比_pct": 438.8,
        "主要变动科目": [{"科目": "存放中央银行和同业款项净增加额", "本期_亿元": -218.92,
                          "上期_亿元": 254.11, "变动_亿元": -473.06,
                          "是否财务公司科目": True}],
        "剔除财务公司科目后": {"经营性现金净额_亿元": 379.72, "上期_亿元": 372.6,
                              "同比_pct": 1.9},
    })
    assert "主要变动指标归因" in html
    assert "剔除财务公司科目后" in html
    assert "财务公司" in html


def test_annex_hidden_when_ocf_not_in_grade():
    """现金流没异动的标的（伊利 ocf 同比 +0.7%）不该出现这个附注块 ——
    这正是这次改动要消灭的「每只都挂一个现金流板块」。"""
    html = _render(False, {
        "期间": "2026H1",
        "经营活动产生的现金流量净额_亿元": {"本期": 97.59, "上期": 96.9},
        "净额同比_pct": 0.7,
        "主要变动科目": [],
        "剔除财务公司科目后": None,
    })
    assert "主要变动指标归因" in html
    assert "cf-note" not in html
    assert "剔除财务公司科目后" not in html


def test_annex_hidden_when_no_swing_facts():
    """候选榜整个取不到时（老数据/异常）也不能凭空长出一个现金流板块。"""
    bv = _bv()
    bv.SWING_FACTS = None
    bv.OPERATING = {"现金流归因": {"期间": "2026H1",
                                   "经营活动产生的现金流量净额_亿元": {"本期": 100.0}}}
    html = bv._swing_block({"swing": "正文"})
    bv.OPERATING = None
    assert "cf-note" not in html


def test_empty_swing_text_is_visible_not_silent():
    """LLM 漏了 swing 键时，表格照渲染、正文显示「—」——
    整块静默消失比一个占位符危险得多（没有任何症状，只会以为「这段本来就没有」）。"""
    bv = _bv()
    bv.SWING_FACTS = {"经营现金流是否入榜": False, "候选": [{
        "名称": "存货", "分组": "资产负债", "本期_亿元": 300.0,
        "上期_亿元": 100.0, "变动_亿元": 200.0, "同比_pct": 200.0,
    }]}
    html = bv._swing_block({})
    bv.SWING_FACTS = None
    assert "主要变动指标归因" in html
    assert "存货" in html
    assert "—" in html


def test_old_schema_text_falls_back_and_is_labelled():
    """过渡期：旧结构缓存里那段叫 `cashflow`，必须认，并在脚注说明「沿用上一版结构」。

    这条兜的是「改版当天 + 还没重跑完整构建」的窗口 —— 日更刻意容忍旧结构，
    渲染端就得接得住，否则前面那个容忍白做。
    """
    bv = _bv()
    bv.SWING_FACTS = {"经营现金流是否入榜": False, "候选": []}
    html = bv._swing_block({"cashflow": "上一版写的现金流归因", "_schema_stale": True})
    bv.SWING_FACTS = None
    assert "上一版写的现金流归因" in html
    assert "沿用上一版结构" in html


def test_render_does_not_crash_on_none_values():
    """adapter 抽不到数时写的是显式 None —— 渲染绝不能抛（抛了就是整份报告不落盘）。"""
    bv = _bv()
    bv.SWING_FACTS = {"经营现金流是否入榜": True, "候选": [{
        "名称": "存货", "分组": "资产负债", "本期_亿元": None,
        "上期_亿元": None, "变动_亿元": None, "同比_pct": None,
    }]}
    bv.OPERATING = {"现金流归因": {"期间": "2026H1",
                                   "经营活动产生的现金流量净额_亿元": {"本期": None}}}
    html = bv._swing_block({"swing": "正文"})
    bv.SWING_FACTS = None
    bv.OPERATING = None
    assert "主要变动指标归因" in html
    assert "None" not in html


def _swing_facts(entry_extra: dict | None, neg_list: list[str]):
    base = {
        "报告期": "2026H1", "上期": "2025H1", "经营现金流是否入榜": False,
        "候选": [{
            "名称": "投资活动产生的现金流量净额", "分组": "现金流",
            "口径": "年初至今累计", "本期_亿元": -27.52, "上期_亿元": -342.75,
            "变动_亿元": 315.23, "同比_pct": 92.0, **(entry_extra or {}),
        }],
        "特殊同比科目": neg_list,
        "口径说明": "口径", "门槛说明": "门槛", "未入榜说明": "说明",
    }
    return base


def test_swing_block_notes_negative_base_caliber():
    """上期为负的行必须在脚注里点明符号含义。

    不点明的话，报告上会出现「本期 -27.5 / 上期 -342.7 / 变动 +315.2 / 同比 +92.0%」
    这样一行 —— 读者把它读成「投资现金流同比大增 92%」，而它是**流出收窄** 92%。
    数字没算错，但缺了这句，整行的含义就是反的。
    """
    bv = _bv()
    bv.SWING_FACTS = _swing_facts({"同比口径": "上期为负"},
                                  ["投资活动产生的现金流量净额"])
    html = bv._swing_block({"swing": "正文"})
    bv.SWING_FACTS = None
    assert "+92.0%" in html
    assert "上期为净流出" in html
    assert "投资活动产生的现金流量净额" in html


def test_swing_block_omits_caliber_note_when_all_positive():
    """榜上没有特殊口径行时**不**加这句 —— 每只标的都挂一句与它无关的口径说明，
    等于训练读者跳过脚注，真需要读的那次也不会读了。"""
    bv = _bv()
    bv.SWING_FACTS = _swing_facts(None, [])
    html = bv._swing_block({"swing": "正文"})
    bv.SWING_FACTS = None
    assert "+92.0%" in html
    assert "上期为净流出" not in html


def test_swing_block_renders_crossing_zero_as_words_not_number():
    """跨越零点在表里写成「由负转正」，不能印成 +8265.9% 那种失真比值，也不留「—」。

    留空读者会以为没数据；给百分比会被那个数吓到 —— 两者都不是事实的正确形态。
    """
    bv = _bv()
    bv.SWING_FACTS = _swing_facts({"同比_pct": None, "同比口径": "由负转正"},
                                  ["投资活动产生的现金流量净额"])
    html = bv._swing_block({"swing": "正文"})
    bv.SWING_FACTS = None
    assert "由负转正" in html
    assert "跨越零点" in html
    assert "8265" not in html


# --------------------------------------------------------------------------- #
# 缓存结构版本
# --------------------------------------------------------------------------- #
def test_schema_version_bumped_for_new_output_key():
    """输出键从 cashflow 改成了 swing —— 缓存 payload 的 schema 必须跟着抬。

    不抬的话日更链路（`cache_only=True`，只看「缓存文件在不在」）会把旧结构的缓存
    原样返回，渲染端按 `swing` 取值取到空串 —— 段落静默消失，零报错、日志也正常。
    """
    from src.report import quarterly_review as qr
    assert qr._SCHEMA >= 2
    src = (ROOT / "src" / "report" / "quarterly_review.py").read_text(encoding="utf-8")
    assert '"swing"' in src and '"cashflow"' not in src, \
        "prompt 的输出键应已全面改为 swing，不能两套键并存"


def test_prompt_key_and_renderer_key_are_the_same_string():
    """prompt 里承诺的输出键，必须与渲染端读的键**逐字相同**。

    这是纯约定、编译器管不着：prompt 写 `swing`、渲染端读 `cashflow`（或反过来），
    结果是段落整块消失 —— 不报错、不抛异常、体检也看不出（内容"有"，只是键对不上）。
    """
    bv_src = (ROOT / "scripts" / "build_valueline.py").read_text(encoding="utf-8")
    assert 'review.get("swing")' in bv_src, \
        "渲染端读的 key 与 prompt 输出的 key 不一致 —— 板块会静默消失"