# -*- coding: utf-8 -*-
"""跟踪池单一真源（`src/data/watchlist_store.py`）的单元测试。

全部 hermetic：把模块级的 `WATCHLIST_PATH` 指向 tmp 文件，不碰真实 watchlist.json、
不发网络请求。

背景（为什么值得单独测）：这个模块是为了消除「跟踪池三处硬编码必然漂移」而引入的
唯一来源。一旦它自己出错，错的是**全站**（首页卡片、对比表、日更标的清单都读它）。
本次已踩过两个真实 bug，都补了回归：
  ① 色值判重只看 `s.get("color")` → 旧条目没这个字段 → 新标的全部拿到 PALETTE[0] 撞色
  ② `lynch` 用精确词匹配 → 数据源的行业名带后缀（"银行Ⅱ"/"油气开采Ⅱ"）→ 全落"未分类"
  ③ Lynch 有两套实现（报告里 LLM 判的 / 这里关键字猜的），同一标的显示成两个归类
     → 定「以报告为准」，本模块只负责把 LLM 值写进来（见文件末的报告口径回写测试）
"""
from __future__ import annotations

import json

import pytest

from src.data import watchlist_store as wl


@pytest.fixture()
def tmp_watchlist(tmp_path, monkeypatch):
    """把单例路径指到 tmp，返回 (模块, 路径)。"""
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps({
        "version": 1,
        "rules": {"cadence": "年报/中报季全量更新"},
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


def test_no_max_size_anywhere(tmp_path, monkeypatch):
    """跟踪池**不设上限**（2026-09-18 潇姐拍板）—— 防它被悄悄加回来。

    为什么值得一条断言：`max_size=8` 这个「名义上限」不阻断写入，却让对比表长期挂着
    「已超名义上限 8 只」的告警。永远为真、谁也没打算处理的告警只会训练人忽略告警 ——
    去掉之后必须防止下次有人「顺手加个上限」。这条断言覆盖三处：模块 API、
    缺失文件时的兜底骨架、以及真实配置。
    """
    assert not hasattr(wl, "max_size"), "max_size() 已删除，别加回来"
    assert not hasattr(wl, "DEFAULT_MAX_SIZE")

    monkeypatch.setattr(wl, "WATCHLIST_PATH", tmp_path / "nope.json")
    assert wl._empty() == {"version": 1, "rules": {}, "stocks": []}
    assert "max_size" not in json.dumps(wl._empty())

    real = json.loads((wl.ROOT / "watchlist" / "watchlist.json").read_text(encoding="utf-8"))
    assert "max_size" not in json.dumps(real.get("rules", {})), \
        "真实 watchlist.json 的 rules 里还有 max_size"


def test_missing_file_is_empty_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "WATCHLIST_PATH", tmp_path / "nope.json")
    assert wl.codes() == []
    assert wl.stocks() == []


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
# 报告口径回写（回归 bug③：Lynch 两套口径，定「以报告为准」）
# --------------------------------------------------------------------------- #

def test_upsert_from_report_updates_existing(tmp_watchlist):
    """已在池的条目 → 按报告口径更新 lynch/industry，但**不动记账字段**。"""
    wl, _ = tmp_watchlist
    before = wl.get("601088")
    status = wl.upsert_from_report("601088", name="中国神华", industry="煤炭开采",
                                   lynch="周期型（高股息现金牛）")
    assert status == "updated"
    after = wl.get("601088")
    assert after["lynch"] == "周期型（高股息现金牛）"
    assert after["meta_source"] == "report"          # 留痕：这个值来自报告，不是关键字猜的
    assert after["color"] == before["color"]         # 色点/入池时间属于记账信息
    assert after.get("since") == before.get("since")


def test_upsert_from_report_is_idempotent(tmp_watchlist):
    """同值重复回写 → `unchanged`，且**不重写文件**（日更会天天调它）。"""
    wl, p = tmp_watchlist
    assert wl.upsert_from_report("601088", lynch="周期型") == "updated"
    mtime = p.stat().st_mtime_ns
    assert wl.upsert_from_report("601088", lynch="周期型") == "unchanged"
    assert p.stat().st_mtime_ns == mtime


def test_upsert_from_report_creates_when_absent(tmp_watchlist):
    """从没入过池的标的：报告生成即入池（沿用 2026-09-17 的定规）。"""
    wl, _ = tmp_watchlist
    assert wl.upsert_from_report("600519", name="贵州茅台",
                                 industry="白酒Ⅱ", lynch="稳健成长型") == "added"
    assert "600519" in wl.codes()
    assert wl.get("600519")["lynch"] == "稳健成长型"


def test_upsert_from_report_revives_removed(tmp_watchlist):
    """刻意移出的标的：**重新生成报告 = 用户主动要它** → 恢复入池。"""
    wl, _ = tmp_watchlist
    wl.remove("601088", reason="试试移出")
    assert wl.upsert_from_report("601088", lynch="周期型（高股息现金牛）") == "restored"
    s = wl.get("601088")
    assert s["status"] == "active"
    assert "removed_reason" not in s
    assert s["lynch"] == "周期型（高股息现金牛）"


def test_upsert_from_report_filters_placeholders(tmp_watchlist):
    """占位值不写入 —— `待分析`/`未分类` 不是真实分类，写进去只会让人以为判过了。"""
    wl, _ = tmp_watchlist
    wl.upsert_from_report("601088", lynch="待分析", industry="未分类")
    s = wl.get("601088")
    assert s.get("lynch") != "待分析"          # 被过滤 → 条目保持原样（fixture 里本就没有 lynch）
    assert s.get("industry") != "未分类"       # 原值"煤炭开采"不该被占位值顶掉


def test_upsert_from_report_skips_illegal_code(tmp_watchlist):
    """非法代码返回 `skipped` 而不抛异常 —— 回写失败不该把「报告已生成」变成失败。"""
    wl, _ = tmp_watchlist
    assert wl.upsert_from_report("000000", lynch="周期型") == "skipped"
    assert wl.upsert_from_report("", lynch="周期型") == "skipped"


def test_update_fields_never_revives_removed(tmp_watchlist):
    """`update_fields()` 只回填字段、**不复活**已移出的条目。

    这是 `scripts/watchlist.py sync-lynch` 走的路径：批量对齐历史数据时，
    不该把用户亲手移出的标的悄悄放回来（那要靠 `upsert_from_report`，即重新生成报告）。
    """
    wl, _ = tmp_watchlist
    wl.remove("601088")
    assert wl.update_fields("601088", lynch="周期型", industry="煤炭开采") is False
    s = wl.get("601088", include_removed=True)   # ⚠️ removed 的要显式带 include_removed
    assert s is not None and s["status"] == "removed"
    assert s.get("lynch") != "周期型"


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
# 跨文件不变量：reports/ 整目录不入库
# --------------------------------------------------------------------------- #

def test_reports_dir_fully_ignored():
    """`reports/` 必须整体不入库，且**不允许**任何否定行把它放回来。

    这条断言换掉了旧版的「`.gitignore` 白名单 == 跟踪池」。旧版的前提是
    「拉链式放行」：`reports/**/*.html` 全忽略 + 逐只 `!reports/**/<code>.html` 放行。
    那份放行清单要一直跟着跟踪池同步（池子因「生成过报告即入池」随时变长），
    **忘同步一次，新报告就静默不进库** —— 2026-09-18 实测长江电力（600900）踩到，
    全程零报错、零日志。

    2026-09-18 改为整目录出库：报告只是「本地生成、本地看」的产物，仓库只留代码与模板。
    漂移风险随之消失，但「有人手滑补一行 `!reports/...`」会把这个新不变量破坏掉，
    所以仍然留一条断言守住它。

    例外：`templates/valueline.html`（报告模板）**必须**入库，它不在 reports/ 下，
    另见 `tests/test_report_style.py`。
    """
    gi = (wl.ROOT / ".gitignore").read_text(encoding="utf-8")
    rules = [ln.strip() for ln in gi.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]

    assert "reports/" in rules or "reports/**" in rules, (
        "`.gitignore` 里缺少 `reports/` 规则 —— 生成出来的报告会重新进版本库"
    )

    negated = [r for r in rules if r.startswith("!") and "reports" in r]
    assert not negated, (
        "`.gitignore` 里出现了把 reports/ 放回来的否定行：\n"
        + "\n".join(f"  {x}" for x in negated)
        + "\n>>> 现行约定是 reports/ **整目录**不入库（理由见 .gitignore 内注释）。\n"
        ">>> 若确实要恢复「逐只放行」，请同时改回这条测试，别只改 .gitignore。"
    )
