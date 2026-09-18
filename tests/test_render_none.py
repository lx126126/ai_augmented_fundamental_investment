# -*- coding: utf-8 -*-
"""渲染层对「显式 None」的健壮性。

根因（2026-09-18 实测）：`.get(key, 0)` **只吃「key 不存在」，吃不掉「key 存在但值为 None」**
—— 而 adapter 抽不到数据时写的正是显式 None。于是

    f'{(d or {}).get("本期", 0):,.1f}'   # d["本期"] 是 None → None 被拿去格式化

抛 `TypeError: unsupported format string passed to NoneType.__format__`，
**整份报告渲染失败、不落盘**（不是降级，是彻底没有产物）。

这个 bug 被**占位符掩盖了很久**：内容为空时那段代码根本不执行。补齐内容才暴露。
→ `docs/report-chains.md` F10；skill `cn-finance-data-hardening` 坑 41。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 脚本互相 import（build_valueline → _sample_data），必须把 scripts/ 本身也放进 path。
# 脚本作为入口运行时 sys.path[0] 自动是它所在目录，被 import 时不会。
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _yi():
    """取 `build_valueline._yi`。"""
    return importlib.import_module("build_valueline")._yi


# --------------------------------------------------------------------------- #
# 🔴 核心：None 必须不崩
# --------------------------------------------------------------------------- #

def test_none_does_not_crash():
    """这正是 601088 报告整份没生成的原因（TypeError → 不落盘）。"""
    assert _yi()(None) == "—"


def test_do_not_pretend_none_is_zero():
    """设计取舍：None → `—`，**不是** `0.0`。

    显示 0.0 会被读成「本期就是零」这个事实 —— 那是编造一个不存在的数据点。
    """
    assert _yi()(None) != "0.0"
    assert _yi()(None) == "—"


def test_non_numeric_does_not_crash():
    """上游偶尔给字符串 / 容器；同样不能崩（崩了就是整份产物没有）。"""
    assert _yi()("abc") == "—"
    assert _yi()({}) == "—"
    assert _yi()([]) == "—"


# --------------------------------------------------------------------------- #
# 正常值不能被这次加固改坏
# --------------------------------------------------------------------------- #

def test_formats_with_thousands_separator():
    assert _yi()(1234.5) == "1,234.5"
    assert _yi()(12345678.9) == "12,345,678.9"


def test_zero_is_a_real_value_not_missing():
    """0 是有效数据点，必须显示成 `0.0` —— 与 None 区分开。"""
    assert _yi()(0) == "0.0"
    assert _yi()(0) != "—"


def test_negative_keeps_sign_and_one_decimal():
    assert _yi()(-12.34) == "-12.3"
    assert _yi()(-0.05) == "-0.1"


def test_numeric_string_is_parsed():
    """接口/JSON 给字符串数字时要能转，别一律当缺失。"""
    assert _yi()("1234.5") == "1,234.5"


def test_custom_default_for_callers_that_need_another_word():
    assert _yi()(None, default="无") == "无"
