# -*- coding: utf-8 -*-
"""多投资人视角层测试：视角定义加载 + prompt 生成（纯逻辑，无网络）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.report.perspectives import (
    list_perspectives,
    load_perspective,
    build_perspective_prompt,
)


def test_list_perspectives_nonempty():
    ids = [p["id"] for p in list_perspectives()]
    assert "graham" in ids
    assert "lynch" in ids
    # 每个视角都有必备字段
    for p in list_perspectives():
        assert p.get("name")
        assert p.get("core_belief")
        assert p.get("focus_dimensions")
        assert p.get("core_questions")
        assert p.get("tone")


def test_load_perspective_graham():
    p = load_perspective("graham")
    assert p["name"] == "格雷厄姆"
    assert "安全边际" in p["core_belief"]
    assert p.get("source_books")


def test_load_perspective_unknown():
    assert load_perspective("not_exist") is None


def test_build_perspective_prompt_injects_persona():
    data = {
        "name": "腾讯控股",
        "code": "00700",
        "main_business": "互联网",
        "latest_year": "2025",
        "latest": {"revenue": 6374.0, "net_profit": 1927.1, "roe": 21.1, "debt_ratio": 39.1},
        "recent": [],
        "segments": [],
        "competition": {},
        "valuation": {"pe": 20.9, "pb": 3.05, "pe_pctile": 17, "pb_pctile": 2},
    }
    p = load_perspective("graham")
    prompt = build_perspective_prompt(data, p)
    # 视角人格注入
    assert "格雷厄姆" in prompt
    assert "安全边际" in prompt
    # 数据注入
    assert "腾讯控股" in prompt
    assert "6374.0" in prompt
    # 视角核心问题注入
    assert "核心问题" in prompt
    # 铁律：不编数
    assert "严禁编造" in prompt
