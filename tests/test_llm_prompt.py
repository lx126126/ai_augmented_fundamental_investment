# -*- coding: utf-8 -*-
"""LLM 叙事层 prompt 卫生测试：空值不得泄漏进 prompt（纯逻辑，无网络）。

背景：data.get(k, 'N/A') 只在「键不存在」时返回默认值；键存在但值为 None 时
仍返回 None，f-string 会印成「None%」。LLM 读到后会写出「股息率缺失」
「数据未提供」这类句子，把数据缺口变成正文噪声。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.report.llm import _na, _sanitize, _num, _NA_RULE, _build_prompt


def _data(**over):
    base = {
        "name": "测试公司",
        "code": "000001",
        "latest_year": 2025,
        "latest": {
            "revenue": 100.0,
            "net_profit": None,          # 键存在但值为 None（最易踩的情形）
            "gross_margin": float("nan"),
            "net_margin": 10.0,
            "roe": 15.0,
            "debt_ratio": 40.0,
            "ocf": 20.0,
        },
        "recent": [{"year": 2025, "revenue": 100.0, "profit": None}],
        "segments": [{"name": "主业", "revenue_pct": None, "margin": 30.0}],
        "dividend_payout": 30.0,
        "dividend_yield": None,
        "valuation": {"pe": 12.0, "pb": None, "pe_pctile": 30.0, "pb_pctile": None},
        "competition": None,
    }
    base.update(over)
    return base


def test_na_normalizes_empty_values():
    assert _na(None) == "N/A"
    assert _na(float("nan")) == "N/A"
    assert _na("") == "N/A"
    assert _na("  ") == "N/A"
    assert _na("none") == "N/A"
    assert _na("NaN") == "N/A"
    assert _na(0) == "0"          # 0 是有效值，不能被当成空
    assert _na(1.2) == "1.2"


def test_sanitize_recurses_into_containers():
    out = _sanitize({"a": None, "b": {"c": [None, 1, "x"]}})
    assert out == {"a": "N/A", "b": {"c": ["N/A", 1, "x"]}}


def test_num_is_none_safe():
    assert _num(None) is None
    assert _num("N/A") is None
    assert _num(float("nan")) is None
    assert _num(0.5) == 0.5
    assert _num("12.5") == 12.5


def test_prompt_has_no_raw_none_or_nan():
    p = _build_prompt(_data())
    assert "None" not in p
    assert "nan" not in p.lower().replace("finance", "")  # 避免误伤正常英文


def test_prompt_carries_na_rule():
    p = _build_prompt(_data())
    assert "N/A" in p
    assert "禁止" in p and "缺失" in p


def test_prompt_survives_segments_with_null_pct():
    """占比为 None 的分业务不能让 <1.0 比较抛 TypeError。"""
    p = _build_prompt(_data(segments=[{"name": "杂项", "revenue_pct": None, "margin": None}]))
    assert "杂项" in p


def test_market_view_and_action_prompts_are_clean():
    from src.report.llm import _build_market_view_prompt, _build_action_prompt

    for fn in (_build_market_view_prompt, _build_action_prompt):
        p = fn(_data())
        assert "None" not in p
        assert _NA_RULE.strip()[:12] in p
