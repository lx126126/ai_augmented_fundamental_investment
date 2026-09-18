# -*- coding: utf-8 -*-
"""跟踪池单一真源（`src/data/watchlist_store.py`）的单元测试。

全部 hermetic：把模块级的 `WATCHLIST_PATH` 指向 tmp 文件，不碰真实 watchlist.json、
不发网络请求。

背景（为什么值得单独测）：这个模块是为了消除「跟踪池三处硬编码必然漂移」而引入的
唯一来源。一旦它自己出错，错的是**全站**（首页卡片、对比表、日更标的清单都读它）。
本次已踩过两个真实 bug，都补了回归：
  ① 色值判重只看 `s.get("color")` → 旧条目没这个字段 → 新标的全部拿到 PALETTE[0] 撞色
  ② `lynch` 用精确词匹配 → 数据源的行业名带后缀（"银行Ⅱ"/"油气开采Ⅱ"）→ 全落"未分类"
"""
from __future__ import annotations

import json
import re

import pytest

from src.data import watchlist_store as wl


@pytest.fixture()
def tmp_watchlist(tmp_path, monkeypatch):
    """把单例路径指到 tmp，返回 (模块, 路径)。"""
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps({
        "version": 1,
        "rules": {"max_size": 8},
        "stocks": [
            {"code": "601088.SH", "name": "中国神华", "industry": "煤炭开采"},
            {"code": "00700.HK", "name": "腾讯控股", "industry": "互联网"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(wl, "WATCHLIST_PATH", p)
    return wl, p


# --------------------------------------------------------------------------- #
# 代码归一
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("00700", True), ("00700.HK", True), ("09992", True),
    ("601088", False), ("600519.SH", False), ("920000", False),
    ("000651", False),   # ⚠️ 深市 A 股也是 0 开头，只靠长度 5 区分港股
    ("300061", False),
])
def test_is_hk(raw, expected):
    assert wl.is_hk(raw) is expected


@pytest.mark.parametrize("raw,expected", [
    ("601088", "601088"), ("601088.SH", "601088"), ("651", "000651"),
    ("00700.HK", "00700"), ("9999", "009999"),   # 4 位 A 股码补 6 位，不是港股
])
def test_bare(raw, expected):
    assert wl.bare(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("601088", "601088.SH"), ("000651", "000651.SZ"),
    ("00700", "00700.HK"), ("09992", "09992.HK"),
    ("920000", "920000.BJ"),   # 北交所特例：不能按首字符 9 归沪市
])
def test_with_exchange(raw, expected):
    assert wl.with_exchange(raw) == expected


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #

def test_stocks_backfills_bare_and_color(tmp_watchlist):
    _, _ = tmp_watchlist
    ss = wl.stocks()
    assert [s["bare"] for s in ss] == ["601088", "00700"]
    assert all(s["color"].startswith("#") for s in ss)
    assert len({s["color"] for s in ss}) == 2, "两条目的色值必须不同"


def test_codes_keeps_json_order(tmp_watchlist):
    assert wl.codes() == ["601088", "00700"]


def test_max_size_from_rules(tmp_watchlist):
    assert wl.max_size() == 8


def test_missing_file_is_empty_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "WATCHLIST_PATH", tmp_path / "nope.json")
    assert wl.codes() == []
    assert wl.max_size() == wl.DEFAULT_MAX_SIZE


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #

def test_add_then_idempotent(tmp_watchlist, monkeypatch):
    _, p = tmp_watchlist
    monkeypatch.setattr(wl, "_display_name", lambda c: "测试标的")
    assert wl.add("600887") is True
    assert wl.add("600887") is False, "重复入池必须是幂等的"
    assert wl.codes() == ["601088", "00700", "600887"]
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["stocks"][-1]["code"] == "600887.SH"
    assert saved["stocks"][-1]["source"] == "report"
    assert "updated" in saved


def test_add_does_not_clobber_manual_fields(tmp_watchlist, monkeypatch):
    """已在池里的标的，人工填的 name/industry 不能被覆盖。"""
    monkeypatch.setattr(wl, "_display_name", lambda c: "别覆盖我")
    assert wl.add("601088") is False
    assert wl.get("601088")["name"] == "中国神华"


def test_add_color_unique_regression(tmp_watchlist, monkeypatch):
    """回归 bug①：旧条目没有 color 字段时，新条目不能撞色。"""
    _, _ = tmp_watchlist
    monkeypatch.setattr(wl, "_display_name", lambda c: "测试标的")
    for c in ("600887", "600938", "00883", "300061"):
        wl.add(c)
    colors = [s["color"] for s in wl.stocks()]
    assert len(colors) == len(set(colors)), f"色值重复：{colors}"


def test_add_rejects_degenerate_code(tmp_watchlist):
    assert wl.add("") is False
    assert wl.add("000") is False   # 全 0 不是合法代码，别写进池子


def test_ensure_codes_returns_only_new(tmp_watchlist, monkeypatch):
    monkeypatch.setattr(wl, "_display_name", lambda c: "测试标的")
    assert wl.ensure_codes(["601088", "600887"]) == ["600887"]


def test_prune(tmp_watchlist):
    assert wl.prune(["00700"]) == 1
    assert wl.codes() == ["601088"]
    assert wl.prune(["999999"]) == 0


# --------------------------------------------------------------------------- #
# Lynch 归类（回归 bug②）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("industry,expected_kw", [
    ("银行", "收息"), ("银行Ⅱ", "收息"), ("保险", "收息"),
    ("煤炭开采", "周期"), ("石油及天然气", "周期"), ("油气开采Ⅱ", "周期"),
    ("白酒", "品牌消费"), ("饮料乳品", "品牌消费"),
    ("互联网", "轻资产"), ("广告营销", "轻资产"),
    ("半导体", "快速增长"), ("电池", "快速增长"),
])
def test_classify_lynch_by_keyword(industry, expected_kw):
    """回归 bug②：带后缀/细分的行业名也必须命中，不能全落"未分类"。"""
    out = wl.classify_lynch(industry)
    assert expected_kw in out, f"{industry} → {out}"
    assert out != "未分类"


def test_classify_lynch_fallback_is_honest():
    assert wl.classify_lynch("玄学服务") == wl._LYNCH_FALLBACK


# --------------------------------------------------------------------------- #
# 移出池（软删）+ 恢复
# --------------------------------------------------------------------------- #

def test_remove_is_soft_delete(tmp_watchlist):
    """移出是软删：条目留在 json 里、status 变 removed，但默认读不到。"""
    _, p = tmp_watchlist
    out = wl.remove("00700", reason="试功能")
    assert out and out["bare"] == "00700"
    assert wl.codes() == ["601088"]                       # 默认读：不含
    assert "00700" in wl.removed_codes()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert len(raw["stocks"]) == 2                        # 条目**没被删掉**
    gone = wl.get("00700", include_removed=True)
    assert gone["status"] == "removed" and gone["removed_reason"] == "试功能"


def test_remove_is_idempotent(tmp_watchlist):
    assert wl.remove("00700") is not None
    assert wl.remove("00700") is None            # 已移出，不重复写
    assert wl.remove("999999") is None           # 不存在


def test_restore_brings_it_back(tmp_watchlist):
    wl.remove("00700")
    assert wl.restore("00700") is True
    assert wl.codes() == ["601088", "00700"]     # 顺序保持 json 原序
    gone = wl.get("00700", include_removed=True)
    assert gone["status"] == "active"
    assert "removed_at" not in gone and "removed_reason" not in gone
    assert wl.restore("00700") is False          # 本就在池中


def test_add_on_removed_restores_instead_of_duplicating(tmp_watchlist):
    """回归：对已移出的标的 `add` 必须恢复原条目，不能追加出第二条同名记录。"""
    _, p = tmp_watchlist
    wl.remove("00700")
    assert wl.add("00700") is True
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert len(raw["stocks"]) == 2, "同一条目被追加了两次"
    assert wl.codes() == ["601088", "00700"]


def test_remove_does_not_shift_other_colors(tmp_watchlist, monkeypatch):
    """回归：`stocks()` 的色值兜底用**原始下标** —— 移出一条不能让后面的标的换色。

    若先按下标过滤再算 `PALETTE[i % len]`，移出第 1 只会让第 2 只的兜底色变化。
    """
    monkeypatch.setattr(wl, "_display_name", lambda c: "测试标的")
    for c in ("600887", "600938"):
        wl.add(c)
    before = {s["bare"]: s["color"] for s in wl.stocks()}
    wl.remove("601088")                            # 移出**第 1 条**
    after = {s["bare"]: s["color"] for s in wl.stocks()}
    for c, col in after.items():
        assert col == before[c], f"{c} 因其他标的被移出而换色：{before[c]} → {col}"


def test_removed_codes_distinguishes_from_never_pooled(tmp_watchlist):
    """`removed_codes()` 只含**刻意移出**的，不含从没入过池的。

    这个区分是首页兜底卡片正确与否的全部依据（见 `build_web_index.build_cards`）。
    """
    wl.remove("00700")
    assert wl.removed_codes() == {"00700"}
    assert "600887" not in wl.removed_codes()      # 从没入过池


# --------------------------------------------------------------------------- #
# 跨文件一致性：.gitignore 白名单（防漂移断言）
# --------------------------------------------------------------------------- #

def test_gitignore_whitelist_matches_watchlist():
    """`.gitignore` 的跟踪池白名单必须与 `watchlist.json` 完全一致。

    为什么用断言而不是"记得手工同步"：`.gitignore` 里 `reports/**/*.html` 是**全忽略**，
    逐只放行靠 `!reports/**/<code>.html` —— 这是一份**人工维护的清单**，
    而跟踪池会因为「生成过报告即入池」随时变长，两者必然漂移。

    2026-09-18 实测后果：新入池的长江电力（600900）漏加白名单 → 报告静默不入库，
    没有任何报错。这条测试把"忘了同步"从静默错误变成红色失败。

    修法：`python scripts/watchlist.py sync-gitignore`
    """
    gi = (wl.ROOT / ".gitignore").read_text(encoding="utf-8")
    listed = set(re.findall(r"^!reports/\*\*/(\d{5,6})\.html$", gi, re.M))
    pool = set(wl.codes())
    assert listed == pool, (
        "`.gitignore` 白名单与跟踪池不一致 —— 跑 `python scripts/watchlist.py sync-gitignore`\n"
        f"  只在白名单里：{sorted(listed - pool)}\n"
        f"  只在跟踪池里：{sorted(pool - listed)}"
    )
