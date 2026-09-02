# -*- coding: utf-8 -*-
"""林奇六类 → 估值指标映射测试（纯逻辑，无网络依赖）。"""
from src.review.lynch import classify, metrics_for


def test_classify_periodic():
    assert classify("周期型（资源+一体化）") == "周期型"
    assert classify("周期型") == "周期型"


def test_classify_growth():
    assert classify("快速成长") == "快速成长"
    assert classify("快速增长") == "快速成长"
    assert classify("稳健成长") == "稳健成长"


def test_classify_slow_and_turnaround():
    assert classify("缓慢增长") == "缓慢增长"
    assert classify("红利股") == "缓慢增长"
    assert classify("困境反转") == "困境反转"
    assert classify("资产隐蔽") == "资产隐蔽"


def test_classify_unknown_returns_original():
    assert classify("某种未知类型") == "某种未知类型"
    assert classify("") == ""


def test_metrics_for_periodic():
    m = metrics_for("周期型")
    assert any("PB" in x for x in m)
    assert any("股息" in x for x in m)


def test_metrics_for_growth():
    m = metrics_for("快速成长")
    assert any("增速" in x for x in m)
    assert any("毛利率" in x for x in m)


def test_metrics_for_unknown_falls_back():
    # 识别不出时回退到稳健成长的通用清单
    m = metrics_for("某种未知类型")
    assert len(m) > 0
