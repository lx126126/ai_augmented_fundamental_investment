# -*- coding: utf-8 -*-
"""日更链路**不能丢掉只有 LLM 才能生成的板块**（2026-09-18 回归）。

背景（真实事故，静默）：`build(code, daily=True)` 跳过 LLM 是为了省 token、
只刷估值板块 —— 但 `build()` 会**无条件**把整份 HTML 覆盖写回
`reports/{期}/{code}.html`。于是「跳过」等于**擦掉**：2026-09-17 16:30 那次日更，
把全池 11 只归档报告的投资逻辑 / 风险提示洗成了「待 LLM 生成」，
而同日按需生成的长江电力是完整的 —— 文件大小、元素计数、日志，全看不出异常。

两条防线：
  ① 行为测试：`cache_only=True` 只读缓存、绝不联网/调模型（季度解读）；
  ② 静态断言：`build()` 的 daily 分支必须是**复用缓存**，不能退回「跳过」。
第三条（2026-09-21 加）：结构版本（`quarterly_review._SCHEMA`）的界线要划对 ——
**完整构建**遇旧结构必须重新生成，**日更**遇旧结构必须照旧交出（打 `_schema_stale` 标记）。
划反了就会在改版当天把全池报告的季度解读洗成占位符。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.report import quarterly_review as qr

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "scripts" / "build_valueline.py"

FACTS = {"营业总收入": 100.0, "归母净利": 10.0}


@pytest.fixture()
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "quarterly_review"
    d.mkdir()
    monkeypatch.setattr(qr, "_CACHE_DIR", d)
    return d


def _write_cache(path: Path, facts_hash: str) -> dict:
    review = {"summary": "上次生成的解读正文", "highlights": ["一", "二"]}
    path.write_text(json.dumps({
        # schema 必须与当前 `_SCHEMA` 一致 —— 结构版本不符的缓存一律当没有缓存，
        # 否则改了输出键之后日更会把旧结构的缓存原样返回（段落静默消失）。
        "schema": qr._SCHEMA,
        "facts_hash": facts_hash, "mdd_hash": "whatever",
        "review": review, "meta": {"has_mdd": True},
    }, ensure_ascii=False), encoding="utf-8")
    return review


def test_cache_only_tolerates_old_schema_but_marks_it(cache_dir, monkeypatch):
    """结构版本不符时，`cache_only` **仍然要交出旧内容**，只是打上 `_schema_stale`。

    反过来的设计（拒绝返回）看着更"干净"，代价却极大：改一次输出结构，日更就会把全池
    报告的季度解读洗成占位符 —— 要 14 次 LLM 调用才补得回来，而且**零报错**。
    键的兼容由渲染端负责（`build_valueline._swing_block` 退回旧的 `cashflow`）。
    """
    p = cache_dir / "600519.json"
    _write_cache(p, facts_hash="x")
    obj = json.loads(p.read_text(encoding="utf-8"))
    obj["schema"] = qr._SCHEMA - 1
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("cache_only 不该触发生成")

    monkeypatch.setattr(qr, "generate", _boom)
    monkeypatch.setattr(qr, "fetch_latest_report", _boom)
    out = qr.get_or_generate("600519", FACTS, cache_only=True)
    assert out is not None and out["_schema_stale"] is True


def test_full_build_regenerates_on_old_schema(cache_dir, monkeypatch):
    """完整构建（非 cache_only）遇到旧结构缓存必须重新生成 —— 否则新键永远补不上。"""
    p = cache_dir / "600519.json"
    _write_cache(p, facts_hash="x")
    obj = json.loads(p.read_text(encoding="utf-8"))
    obj["schema"] = qr._SCHEMA - 1
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    qr._CACHE_DIR.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(qr, "fetch_latest_report",
                        lambda code: {"meta": {"title": "x"}, "text": "", "error": None})
    monkeypatch.setattr(qr, "generate",
                        lambda facts, mdd, meta: {"swing": "新结构生成的解读"})

    out = qr.get_or_generate("600519", FACTS)
    assert out["swing"] == "新结构生成的解读"
    assert out["_cached"] is False


def test_cache_only_returns_last_review_even_if_hash_differs(cache_dir, monkeypatch):
    """`cache_only=True` 必须在**哈希不匹配**时也把上次的解读交出来。

    日更改的是行情，财务事实没变；哈希里含市值 → 必然不匹配。
    如果这里返回 None，渲染层就会显示占位 —— 正是那次事故的形态。
    """
    _write_cache(cache_dir / "601088.json", facts_hash="老哈希（行情变了所以对不上）")

    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("cache_only 不该触发生成（会联网 + 烧 token）")

    monkeypatch.setattr(qr, "generate", _boom)
    monkeypatch.setattr(qr, "fetch_latest_report", _boom)

    out = qr.get_or_generate("601088", FACTS, cache_only=True)
    assert out is not None
    assert out["summary"] == "上次生成的解读正文"
    assert out["_cached"] is True


def test_cache_only_returns_none_when_no_cache(cache_dir):
    """没缓存就诚实返回 None（渲染成占位），而不是去联网生成。"""
    assert qr.get_or_generate("601088", FACTS, cache_only=True) is None


def test_cache_only_ignores_stale_days(cache_dir, monkeypatch):
    """再老的缓存也给 —— 日更不判断新鲜度（判断权在「财务事实有没有变」）。

    否则标的隔几天没重建，报告里的季度解读就会周期性消失。
    """
    p = cache_dir / "600519.json"
    _write_cache(p, facts_hash="x")
    import os
    import time
    old = time.time() - 30 * 86400
    os.utime(p, (old, old))

    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("不该联网")

    monkeypatch.setattr(qr, "fetch_latest_report", _boom)
    assert qr.get_or_generate("600519", FACTS, cache_only=True) is not None


# --------------------------------------------------------------------------- #
# 静态断言：daily 分支不能退回「跳过」
# --------------------------------------------------------------------------- #

def test_daily_branch_reuses_cache_instead_of_skipping():
    """`build()` 的 daily 分支必须是复用缓存。

    这条守的是「下次有人为了省 token 把 daily 的叙事重新改成 None」——
    那样报告会被静默洗成占位，而所有体检指标都是绿的。
    """
    src = BUILDER.read_text(encoding="utf-8")
    assert "ignore_hash=True" in src, "叙事层 daily 分支没有复用缓存"
    assert "cache_only=True" in src, "季度解读 daily 分支没有只读缓存"
    assert "if (not daily) and _HAS_LLM" not in src, \
        "daily 又变成「跳过 LLM」了 —— 跳过会擦掉叙事（见本文件 docstring）"
