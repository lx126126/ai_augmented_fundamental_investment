# -*- coding: utf-8 -*-
"""回归测试：估值面板的「分位」措辞必须与序列实际跨度一致。

事故（2026-09，中国海油 A 股 600938 首次进入观察池）：
该股 2022-04 上市，估值序列只有 4.4 年，面板却印「近10年分位 91%」。
读者会读成「十年 91% 分位」，而真实含义是「上市以来 91% 分位」——
该股上市初期正值油气高景气、PE 仅 5~7 倍，分母换成真十年，分位会大幅
下移，两个结论指向完全不同的动作。同页的「十年中位数」有同样的问题。

措辞由 `_scope_label()` 按跨度生成，跨度 ≥ 9.5 年才允许说「近10年」。
本条与 `test_report_style.py` 的分工：那边守「样式类有没有定义」，
这边守「措辞有没有撒谎」。
"""
import importlib
import sys
from pathlib import Path

import pandas as pd


def _scope_label(kind: str, v: dict | None):
    """取 scripts/build_valueline.py 的 _scope_label。

    ⚠️ 该脚本里有 `from _sample_data import ...`（同目录模块），所以在测试里
    导入前必须先把 scripts/ 放进 sys.path；只加项目根目录是不够的
    （脚本作为入口运行时 sys.path[0] 自动是脚本所在目录，被 import 时不会）。
    """
    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("build_valueline")._scope_label(kind, v)


def test_pctile_label_switches_for_recent_listing():
    """上市仅 4.4 年时改为「上市以来分位（4.4年）」，年数如实标出。"""
    v = {"val_series_start": pd.Timestamp("2022-04-21"),
         "val_series_end": pd.Timestamp("2026-09-01")}   # 跨度 4.4 年
    assert _scope_label("pctile", v) == "上市以来分位（4.4年）"
    assert _scope_label("median", v) == "区间中位数"
    assert _scope_label("range", v) == "上市以来"


def test_pctile_label_keeps_ten_year_for_full_history():
    """满 10 年（跨度 ≥ 9.5 年）的标的措辞不变，避免误伤老股。"""
    v = {"val_series_start": pd.Timestamp("2016-01-04"),
         "val_series_end": pd.Timestamp("2026-09-01")}   # 跨度 10.7 年
    assert _scope_label("pctile", v) == "近10年分位"
    assert _scope_label("median", v) == "十年中位数"
    assert _scope_label("range", v) == "近十年"


def test_pctile_label_defaults_when_span_unknown():
    """跨度缺失（老调用点不传 v）时退回默认措辞，不抛异常。"""
    assert _scope_label("pctile", {}) == "近10年分位"
    assert _scope_label("median", None) in ("十年中位数", "区间中位数")
