# -*- coding: utf-8 -*-
"""叙事层事实摘要测试：港股股息率回退 + 空值不泄漏（纯逻辑，无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.adapter import _build_narrative_data


def _annual(with_div_yield: bool) -> pd.DataFrame:
    cols = {
        "report_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
        "revenue": [100.0, 120.0],
        "net_profit_parent": [10.0, 12.0],
        "roe_pct": [15.0, 16.0],
        "dividend_payout_pct": [30.0, 32.0],
    }
    if with_div_yield:
        cols["dividend_yield_pct"] = [2.0, 2.4]
    return pd.DataFrame(cols)


def test_dividend_yield_prefers_valuation_panel():
    """面板口径优先：叙事层必须引用读者在估值面板上看到的那个股息率。

    面板的股息率是「最近年度分红总额 ÷ 当前市值」，年报的 dividend_yield_pct 是
    「年内各次派息当时股息率之和」——两者是不同分母，混用会让同一页出现
    「投资逻辑写 4.0%、估值面板写 4.1%」的自相矛盾。
    """
    annual = _annual(with_div_yield=True)
    valuation = {"pe": 12.0, "pb": 2.0, "dividend_yield": 1.8,
                 "pe_pctile": 30.0, "pb_pctile": 40.0}
    d = _build_narrative_data(annual, [], valuation, "测试", "600519")
    assert d["dividend_yield"] == 1.8


def test_dividend_yield_falls_back_to_annual_column():
    """面板取不到（港股无分红总额列）时，回退年报口径，避免 LLM 看到 None 写出「股息率缺失」。"""
    annual = _annual(with_div_yield=True)
    valuation = {"pe": 12.0, "pb": 2.0, "dividend_yield": None,
                 "pe_pctile": 30.0, "pb_pctile": 40.0}
    d = _build_narrative_data(annual, [], valuation, "测试", "09992")
    assert d["dividend_yield"] == 2.4


def test_dividend_yield_falls_back_to_valuation():
    """年报无 dividend_yield_pct 且面板有值时，用面板值。

    不回退会让 LLM 看到「股息率：None%」并在正文写出「股息率缺失」。
    """
    annual = _annual(with_div_yield=False)
    valuation = {"pe": 12.0, "pb": 2.0, "dividend_yield": 1.8,
                 "pe_pctile": 30.0, "pb_pctile": 40.0}
    d = _build_narrative_data(annual, [], valuation, "测试", "09992")
    assert d["dividend_yield"] == 1.8
    assert d["valuation"]["dividend_yield"] == 1.8


def test_dividend_yield_none_when_no_source_at_all():
    annual = _annual(with_div_yield=False)
    d = _build_narrative_data(annual, [], None, "测试", "000001")
    assert d["dividend_yield"] is None
    assert d["valuation"] is None
