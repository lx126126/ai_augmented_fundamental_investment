# -*- coding: utf-8 -*-
"""口径回归：「营收」统一为**营业总收入**（`revenue`），不得与营业收入（`operating_revenue`）混用。

背景（2026-09，贵州茅台一页报告 + 投资日记）：
项目里「营收」长期有两个来源 —— `revenue`（营业总收入，利润表第一行）与
`operating_revenue`（利润表「其中：营业收入」）。8 个已覆盖标的里 4 个两者完全相等
（600938 / 601088 / 601328 / 09992），所以混用一直不显形；但茅台 2025 相差 32.1 亿
（1720.5 vs 1688.4，差额＝财务公司利息收入）。一旦「标签写 A、取数取 B」，
同一份报告里同一个数字就有两个名字，读者（以及 LLM 叙事层）无从判断该信哪个。

实测本轮修掉 5 处错挂：
  1. `perspectives.py` 把 `revenue` 标成「营业收入」（喂给 LLM，会直接写出错标签）
  2. `_audit_snapshot.py` 反过来用 `operating_revenue` 标「营收」
  3. `api/query.py` 别名 `revenue_yi` 取的是 `operating_revenue`
  4. `build_xhs.py` 单季卡「营业收入」的值配「营业总收入同比」的同比（同卡自相矛盾）
  5. `fraud._receivable_divergence` 的「营收增速」取 `operating_revenue`

定案（2026-09）：
  * **展示口径**：「营收」= 营业总收入 = `revenue`；文字一律写全称「营业总收入」
  * **比率口径**：毛利率 / 净利率 / Beneish M-Score 的收入项仍取 `operating_revenue`
    （会计上营业成本与营业收入配对），属**有意保留**的第二个口径，不是遗漏

本文件把这两条同时钉死 —— 既防「标签与取数不一致」，也防后来者「顺手统一」把
比率口径也改掉。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# scripts/ 里的模块互相 import（build_valueline → _sample_data），必须把该目录本身入 path
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _scripts_mod(name: str):
    return importlib.import_module(name)


def _facts() -> dict:
    """茅台口径的事实包：两口径刻意不等，便于断言取的是哪一列。"""
    return {
        "name": "贵州茅台",
        "code": "600519",
        "main_business": "白酒",
        "latest_year": "2025",
        "latest": {
            "revenue": 1720.5,            # 营业总收入
            "operating_revenue": 1688.4,  # 营业收入（若被误取，断言会立刻失败）
            "net_profit": 823.2,
            "roe": 33.6,
            "debt_ratio": 16.4,
            "gross_margin": 91.2,
            "net_margin": 48.8,
            "ocf": 897.6,
        },
        "recent": [{"year": 2025, "revenue": 1720.5, "profit": 823.2}],
        "segments": [],
        "competition": {"industry": "白酒", "rank": 1, "peers_count": 20,
                        "share_pct": 47.55, "revenue_yi": 1720.5, "report_year": 2025,
                        "top_peers": [{"name": "五粮液", "revenue_yi": 832.7}]},
        "valuation": {"pe": 19.6, "pb": 6.34, "pe_pctile": 1, "pb_pctile": 7},
    }


# ---------------------------------------------------------------------------
# 1. 喂给 LLM 的事实包：标签与取数必须同口径
# ---------------------------------------------------------------------------

def test_perspective_prompt_labels_revenue_as_total_revenue():
    """视角 prompt 的「关键指标」必须写营业总收入，且取 revenue 列。"""
    from src.report.perspectives import build_perspective_prompt, load_perspective

    p = build_perspective_prompt(_facts(), load_perspective("graham"))
    assert "营业总收入：1720.5 亿元" in p
    assert "营业收入：1720.5" not in p          # 曾经把 revenue 标成「营业收入」
    assert "2025年营业总收入1720.5亿" in p
    assert "营业总收入行业第 1/20 家" in p


def test_main_prompt_declares_single_caliber():
    """主报告 prompt 必须显式声明口径，并禁止 LLM 改写成「营业收入」。"""
    from src.report.llm import _build_prompt

    p = _build_prompt(_facts())
    assert "营业总收入：1720.5 亿元" in p
    assert "营业收入：1720.5" not in p
    assert "「营收」一律指【营业总收入】" in p


# ---------------------------------------------------------------------------
# 2. 造假检测：展示用「营业总收入」，模型因子仍用「营业收入」
# ---------------------------------------------------------------------------

def test_fraud_revenue_prefers_total_revenue():
    """`_revenue` 优先取 revenue（营业总收入）。"""
    from src.analysis.fraud import _revenue

    annual = pd.DataFrame({"revenue": [1720.5], "operating_revenue": [1688.4]})
    assert _revenue(annual.iloc[-1], annual) == pytest.approx(1720.5)


def test_fraud_revenue_falls_back_to_operating_revenue():
    """缺 revenue 列（个别数据源）时退回 operating_revenue，不返回 None。"""
    from src.analysis.fraud import _revenue

    annual = pd.DataFrame({"operating_revenue": [1688.4]})
    assert _revenue(annual.iloc[-1], annual) == pytest.approx(1688.4)


def test_receivable_divergence_uses_total_revenue():
    """「应收增速 vs 营业总收入增速」这行必须用营业总收入算增速。

    构造：应收 +10%、营业总收入 +10%、营业收入 +0%。
    取错列时 rev_yoy 会变成 0、gap 变成 +10 → 误报「背离」。
    """
    from src.analysis.fraud import _receivable_divergence

    annual = pd.DataFrame({
        "accounts_receivable": [100.0, 110.0],
        "revenue": [1720.5, 1892.55],        # +10.0%
        "operating_revenue": [1688.4, 1688.4],  # +0.0%（诱饵）
    })
    r = _receivable_divergence(annual)
    assert r["rev_yoy"] == pytest.approx(10.0, abs=0.05)
    assert r["gap"] == pytest.approx(0.0, abs=0.05)
    assert r["warning"] is False


def test_mscore_keeps_operating_revenue_as_sales():
    """Beneish M-Score 的收入项**有意**用营业收入，别「顺手统一」成营业总收入。

    做法：同一份数据，只改 revenue 列，M-Score 必须不变。
    """
    from src.analysis.fraud import compute_mscore

    base = {
        "accounts_receivable": [100.0, 120.0],
        "current_assets": [500.0, 560.0],
        "fixed_assets": [300.0, 320.0],
        "total_assets": [1000.0, 1100.0],
        "total_liabilities": [400.0, 430.0],
        "operating_revenue": [900.0, 1000.0],
        "net_profit": [90.0, 100.0],
        "net_profit_parent": [85.0, 95.0],
        "ocf": [80.0, 88.0],
        "gross_margin_pct": [40.0, 39.0],
        "sell_expense": [30.0, 33.0],
        "admin_expense": [20.0, 22.0],
        "depreciation": [15.0, 16.0],
    }
    a = compute_mscore(pd.DataFrame({**base, "revenue": [950.0, 1050.0]}))
    b = compute_mscore(pd.DataFrame({**base, "revenue": [1.0, 2.0]}))
    assert a is not None and b is not None
    assert a["mscore"] == pytest.approx(b["mscore"])


# ---------------------------------------------------------------------------
# 3. 财报表格：第二行标明是「其中」明细，不是并列的第二口径
# ---------------------------------------------------------------------------

def test_annual_spec_marks_operating_revenue_as_subitem():
    from src.data.adapter import ANNUAL_SPEC, QUARTER_SPEC

    annual = {row[1]: row[2] for row in ANNUAL_SPEC if row[1]}
    assert annual.get("营业总收入（亿元）") == "revenue"
    assert annual.get("其中：营业收入（亿元）") == "operating_revenue"

    quarter = {row[1]: row[2] for row in QUARTER_SPEC if row[1]}
    assert quarter.get("营业总收入（亿元）") == "revenue"
    assert quarter.get("其中：营业收入（亿元）") == "operating_revenue"


# ---------------------------------------------------------------------------
# 4. 季度事实包：键名自带口径，不再出现裸「营收」
# ---------------------------------------------------------------------------

def _quarter_frame() -> pd.DataFrame:
    """两年各两季（Q1/Q2），供累计同比计算。"""
    return pd.DataFrame({
        "report_date": pd.to_datetime(["2025-03-31", "2025-06-30",
                                       "2026-03-31", "2026-06-30"]),
        "revenue": [500.0, 514.0, 510.0, 376.0],
        "operating_revenue": [490.0, 504.0, 500.0, 368.0],
        "net_profit_parent": [250.0, 257.0, 245.0, 173.0],
        "gross_margin_pct": [91.0, 90.4, 90.0, 89.3],
        "net_margin_pct": [50.0, 50.0, 48.0, 46.0],
        "ocf": [100.0, 43.0, 110.0, 438.0],
        "revenue_yoy_pct": [9.0, 7.0, 2.0, -5.2],
        "net_profit_parent_yoy_pct": [8.0, 6.0, -2.0, -6.9],
        "ocf_yoy_pct": [10.0, 12.0, 10.0, 915.8],
    })


def test_quarter_facts_keys_carry_caliber_explicitly():
    from src.data.adapter import _build_quarter_review_facts

    annual = pd.DataFrame({
        "report_date": pd.to_datetime(["2025-12-31"]),
        "revenue": [1720.5],
        "operating_revenue": [1688.4],
        "net_profit_parent": [823.2],
    })
    f = _build_quarter_review_facts(_quarter_frame(), annual)
    assert f is not None

    single = f["单季"]
    # 键名自带口径（旧名 `单季营收同比_pct` 是裸「营收」，已废除）
    assert single["单季营业总收入_亿元"] == pytest.approx(376.0)
    assert single["单季营业总收入同比_pct"] == pytest.approx(-5.2)
    assert "单季营收同比_pct" not in single
    assert "单季营收环比_pct" not in single

    ytd = f["年初至今累计"]
    assert ytd["累计营业总收入_亿元"] == pytest.approx(886.0)   # 2026: 510 + 376
    # 2025 同期 500 + 514 = 1014 → 886/1014 - 1 = -12.6%
    assert ytd["累计营业总收入同比_pct"] == pytest.approx(-12.6, abs=0.05)

    trend_last = f["单季走势序列"][-1]
    assert trend_last["营业总收入_亿元"] == pytest.approx(376.0)
    assert "营收_亿元" not in trend_last
    assert "营收同比_pct" not in trend_last


def test_narrative_recent_uses_total_revenue():
    """narrative_data.recent 的 revenue 取自宽表 revenue（营业总收入）。"""
    from src.data.adapter import _build_narrative_data

    annual = pd.DataFrame({
        "report_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
        "revenue": [1741.4, 1720.5],
        "operating_revenue": [1709.0, 1688.4],
        "net_profit_parent": [862.3, 823.2],
    })
    nd = _build_narrative_data(annual, [], {}, "贵州茅台", "600519", None)
    assert nd["recent"][-1]["revenue"] == pytest.approx(1720.5)
    assert nd["latest"]["revenue"] == pytest.approx(1720.5)


# ---------------------------------------------------------------------------
# 5. 渲染层：读者看得到的标签一律写全称
# ---------------------------------------------------------------------------

def test_competition_block_labels_total_revenue():
    bv = _scripts_mod("build_valueline")
    saved = bv.COMPETITION
    try:
        bv.COMPETITION = {
            "industry": "白酒", "report_year": 2025, "rank": 1, "peers_count": 20,
            "share_pct": 47.55, "revenue_yi": 1720.5,
            "top_peers": [{"name": "五粮液", "revenue_yi": 832.7}],
        }
        html = bv.build_competition()
    finally:
        bv.COMPETITION = saved

    assert "营业总收入口径：东财业绩报表" in html
    assert "营业总收入排名" in html
    assert "营业总收入份额" in html
    assert ">营收<" not in html


def test_journal_snapshot_labels_total_revenue():
    j = _scripts_mod("journal")
    md = j._snapshot({
        "valuation": {"price_now": 1275.16, "pe": 19.6, "pb": 6.34, "dividend_yield": 4.0},
        "narrative_data": {"latest_year": 2025,
                           "latest": {"revenue": 1720.5, "net_profit": 823.2,
                                      "roe": 33.6, "debt_ratio": 16.4}},
    })
    assert "营业总收入 1720.5 亿" in md
    assert "营收 1720.5" not in md


def test_xhs_quarter_card_is_single_caliber():
    """小红书单季卡：标题、数值、同比必须同口径（曾出现「营业收入」配「营业总收入同比」）。"""
    src = (ROOT / "scripts" / "build_xhs.py").read_text(encoding="utf-8")
    assert "单季营业总收入_亿元" in src
    assert "单季营业总收入同比_pct" in src
    assert "单季营业收入_亿元" not in src      # 该卡不再取营业收入的数
