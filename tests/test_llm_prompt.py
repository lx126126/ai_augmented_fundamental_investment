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


# ---------------------------------------------------------------------------
# 验证计划的时间锚点：必须是「已披露的最新报告期（含季报/中报）」，而不是最新年报年份
# ---------------------------------------------------------------------------

def _verif_prompt(**over):
    from src.report.llm import _build_verification_prompt
    return _build_verification_prompt(_data(**over), [
        {"id": "graham", "name": "格雷厄姆", "verdict": "可关注", "edge": "PE 分位低", "concern": "增长失速"},
    ])


def test_verification_anchor_uses_latest_disclosed_quarter():
    """给了 latest_period（如 2026Q2）时，锚点必须用它，且明确它不是未来时点。

    事故（2026-09 茅台日记）：锚点只写「已披露的最新报告期：2025 年（年报）」，
    而数据其实已到 2026Q2。模型据此把「2026 中报」当成待验证的未来时点写进
    「下次验证触发点」——那份中报当期早已披露，验证点永远无法验证，
    而「可证伪」正是这个板块存在的意义。
    """
    p = _verif_prompt(latest_period="2026Q2")
    assert "2026Q2" in p
    # 不能再把「最新年报 = 已披露的最新报告期」混为一谈
    assert "已披露的最新报告期：2025 年报" not in p
    assert "最新年报为 2025 年" in p   # 年报年份仍如实出现（作为补充信息）


def test_verification_anchor_falls_back_without_latest_period():
    """没有 latest_period（老调用点）时退回「{年份} 年报」，不抛异常。"""
    p = _verif_prompt()
    assert "已披露的最新报告期：2025 年报" in p


def test_verification_anchor_ignores_sanitized_na():
    """latest_period 为空时 sanitize 会写成 'N/A'，不能被当成有效报告期印出来。"""
    p = _verif_prompt(latest_period=None)
    assert "已披露的最新报告期：N/A" not in p
    assert "已披露的最新报告期：2025 年报" in p
