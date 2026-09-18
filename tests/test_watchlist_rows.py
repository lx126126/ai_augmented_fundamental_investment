# -*- coding: utf-8 -*-
"""`scripts/build_watchlist.py` 行缓存指纹的回归测试。

背景（为什么值得单独测）
------------------------
对比表每一行都要跑一遍 `build_template_data`（实测约 14 秒/只），所以做了行缓存。
缓存键 = `_data_fingerprint(code)`。

它原先**只**看 `data/raw/{code}/*.parquet` 的最大 mtime —— 可是行数据里还带着
`industry` / `lynch` / `color` 三个字段，来源是 `watchlist/watchlist.json`，
跟 raw parquet 一点关系都没有。

2026-09-18 实测踩到：把全池 Lynch 分类对齐成报告口径（只改了 json、没动 raw），
重建对比表时 **11 只全部命中缓存**，表里还是旧的「稳健增长型 · 收息」——
改了等于没改，且零报错。

修法：指纹 = `raw mtime + 该标的自身四个展示字段的 hash`。只取该标的自己的字段
（不是整个 json 的 mtime），所以改一只不会让全池重算。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def bw():
    """按路径加载 `scripts/build_watchlist.py`（它不是包，没法直接 import）。

    ⚠️ 模块级会 `from src.data.adapter import build_template_data`（连带 pandas 等），
    环境缺依赖时直接 skip —— 这是缓存指纹的测试，不该因为数据层依赖缺失而报红。
    """
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec = importlib.util.spec_from_file_location(
            "build_watchlist_under_test", ROOT / "scripts" / "build_watchlist.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"build_watchlist 无法导入：{type(e).__name__}: {e}")
    return mod


def _write_pool(path: Path, lynch: str, industry: str = "煤炭开采") -> None:
    path.write_text(json.dumps({
        "version": 1, "rules": {"max_size": 8},
        "stocks": [
            {"code": "601088.SH", "name": "中国神华", "industry": industry,
             "lynch": lynch, "color": "#378ADD"},
            {"code": "600519.SH", "name": "贵州茅台", "industry": "白酒Ⅱ",
             "lynch": "稳健成长型", "color": "#E24B4A"},
        ],
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def env(bw, tmp_path, monkeypatch):
    """把跟踪池与 raw 目录都指到 tmp；返回 (builder, watchlist_path, raw_dir)。"""
    pool = tmp_path / "watchlist.json"
    raw = tmp_path / "raw"
    (raw / "601088").mkdir(parents=True)
    (raw / "601088" / "profit_sheet.parquet").write_bytes(b"x" * 300)
    (raw / "600519").mkdir(parents=True)
    (raw / "600519" / "profit_sheet.parquet").write_bytes(b"x" * 300)

    _write_pool(pool, lynch="稳健增长型 · 收息")
    monkeypatch.setattr(bw.wl, "WATCHLIST_PATH", pool)
    monkeypatch.setattr(bw, "RAW_DIR", raw)
    return bw, pool, raw


def test_fingerprint_changes_when_pool_field_changes(env):
    """改 json 里的 `lynch`（raw 一个字都没动）→ 指纹必须变，否则缓存会盖住改动。"""
    bw, pool, _ = env
    before = bw._data_fingerprint("601088")
    _write_pool(pool, lynch="周期型（高股息现金牛）")
    after = bw._data_fingerprint("601088")
    assert before != after


def test_fingerprint_changes_when_industry_changes(env):
    """`industry` 同理 —— 它也是从 json 来的展示字段。"""
    bw, pool, _ = env
    before = bw._data_fingerprint("601088")
    _write_pool(pool, lynch="稳健增长型 · 收息", industry="煤炭开采Ⅱ")
    assert bw._data_fingerprint("601088") != before


def test_fingerprint_ignores_other_codes(env):
    """改 A 的字段**不该**让 B 的指纹也变 —— 否则改一次要重算全池（约 2.6 分钟）。"""
    bw, pool, _ = env
    other_before = bw._data_fingerprint("600519")
    _write_pool(pool, lynch="周期型（高股息现金牛）")   # 只动 601088
    assert bw._data_fingerprint("600519") == other_before


def test_fingerprint_stable_when_nothing_changes(env):
    """什么都不改 → 指纹稳定（否则每次重建都全量重算，缓存形同虚设）。"""
    bw, _, _ = env
    assert bw._data_fingerprint("601088") == bw._data_fingerprint("601088")


def test_fingerprint_changes_when_raw_changes(env):
    """raw parquet 变了（日更）→ 指纹必须变，这是缓存最原始的那一半职责。"""
    import os
    import time

    bw, _, raw = env
    before = bw._data_fingerprint("601088")
    f = raw / "601088" / "profit_sheet.parquet"
    time.sleep(0.01)
    os.utime(f, (f.stat().st_atime + 5, f.stat().st_mtime + 5))
    assert bw._data_fingerprint("601088") != before
