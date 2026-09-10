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


def test_dividend_yield_falls_back_to_valuation():
    """年报无 dividend_yield_pct（港股）时，回退到估值面板的行情口径。

    不回退会让 LLM 看到「股息率：None%」并在正文写出「股息率缺失」。
    """
    annual = _annual(with_div_yield=False)
    valuation = {"pe": 12.0, "pb": 2.0, "dividend_yield": 1.8,
                 "pe_pctile": 30.0, "pb_pctile": 40.0}
    d = _build_narrative_data(annual, [], valuation, "测试", "09992")
    assert d["dividend_yield"] == 1.8
    assert d["valuation"]["dividend_yield"] == 1.8


def test_dividend_yield_prefers_annual_column():
    """年报有值时优先用年报口径，不被行情值覆盖。"""
    annual = _annual(with_div_yield=True)
    valuation = {"pe": 12.0, "pb": 2.0, "dividend_yield": 1.8,
                 "pe_pctile": 30.0, "pb_pctile": 40.0}
    d = _build_narrative_data(annual, [], valuation, "测试", "600519")
    assert d["dividend_yield"] == 2.4


def test_dividend_yield_none_when_no_source_at_all():
    annual = _annual(with_div_yield=False)
    d = _build_narrative_data(annual, [], None, "测试", "000001")
    assert d["dividend_yield"] is None
    assert d["valuation"] is None
