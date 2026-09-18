# -*- coding: utf-8 -*-
"""LLM 内容占位符检测（`artifacts.PLACEHOLDERS` / `audit_placeholders`）的测试。

守两件事：

1. **判据只有一份** —— 占位符特征串定义在 `src/report/artifacts.py`，渲染端
   （`build_valueline.py`）与体检端（`check_placeholders.py` / 日更）都引用它。
   任何一处自己再写一遍字面量就会漂移，而漂移方向**总是漏报** ——
   2026-09-18 就是这么漏掉了「日更只复用缓存 ⇒ 没有缓存的标的永远补不上」
   （`docs/report-chains.md` F9）：报告里挂着占位符，却没有任何地方为此报警。

2. **只报池内标的** —— 已移出标的的报告留在磁盘是有意为之（`reports/` 只改清单
   不删数据），它们的内容占位不该天天报出来，那是会训练人忽略告警的噪音。

全部 hermetic：`REPORTS_DIR` 与跟踪池路径都指到 tmp。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.data import watchlist_store as wl
from src.report import artifacts

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #

def _write_pool(path: Path, active: list[str], removed: list[str]) -> None:
    stocks = [{"code": c, "name": c, "industry": "测试", "lynch": "周期型",
               "status": "active", "color": "#378ADD"} for c in active]
    stocks += [{"code": c, "name": c, "industry": "测试", "lynch": "周期型",
                "status": "removed", "color": "#E24B4A",
                "removed_at": "2026-09-18"} for c in removed]
    path.write_text(json.dumps({"version": 1, "rules": {}, "stocks": stocks},
                               ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """`(reports_dir, pool_path)` —— 都指到 tmp。"""
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(artifacts, "REPORTS_DIR", d)
    p = tmp_path / "watchlist.json"
    _write_pool(p, active=["601088.SH"], removed=[])
    monkeypatch.setattr(wl, "WATCHLIST_PATH", p)
    return d, p


def _report(reports_dir: Path, code: str, blocks: dict[str, str],
            period: str = "2026Q2") -> Path:
    """造一份报告：`blocks` 是 `{占位符名: 内容}` —— 值为空串表示该段缺失。"""
    d = reports_dir / period
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f"<div>{txt}</div>" for txt in blocks.values())
    p = d / f"{code}.html"
    p.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return p


def _complete(code: str) -> dict[str, str]:
    """各段都有真实内容（不含任何占位符特征串）。"""
    return {"商业模式": "以煤炭销售为主，煤电路港航一体化",
            "投资逻辑": "高分红、低负债，防御属性强",
            "风险提示": "煤价下行将显著压制盈利",
            "季度财报解读": "单季营收环比回升，现金流稳健"}


def _code_only(rel: str) -> str:
    """源码剥掉注释与文档字符串 —— 只在**代码**里查违规，注释里提一句不算。"""
    src = re.sub(r'""".*?"""', "", (ROOT / rel).read_text(encoding="utf-8"), flags=re.S)
    src = re.sub(r"'''.*?'''", "", src, flags=re.S)
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


# --------------------------------------------------------------------------- #
# 检测本身
# --------------------------------------------------------------------------- #

def test_all_four_blocks_are_covered():
    """四个 LLM 区块一个都不能漏 —— 漏一个就等于该区块静默降级时无人知晓。"""
    assert set(artifacts.PLACEHOLDERS) == {"商业模式", "投资逻辑", "风险提示", "季度财报解读"}


def test_detects_each_block_individually():
    """逐段注入，逐段检出（不是「有任意一个就报全部占位符名」）。"""
    for name, text in artifacts.PLACEHOLDERS.items():
        html = f"<html>{text}</html>"
        assert artifacts.find_placeholders(html) == [name], f"{name} 未被检出"


def test_complete_report_has_no_placeholder():
    html = "<html>" + "".join(_complete("601088").values()) + "</html>"
    assert artifacts.find_placeholders(html) == []


def test_quarter_review_prefix_matches_longer_sentence():
    """季报解读的特征串是**片段**，必须能命中带说明文字的完整占位文案。

    渲染端写的是「季度财报解读待生成（……说明……）。」—— 说明文字会改，
    所以特征串取「待生成」前的稳定部分。这条测试锁住这个设计。
    """
    long_form = ("<div>季度财报解读待生成（需配置 DEEPSEEK_API_KEY，"
                 "且能访问巨潮资讯网）。</div>")
    assert artifacts.find_placeholders(long_form) == ["季度财报解读"]


# --------------------------------------------------------------------------- #
# audit_placeholders：口径 = 池内标的最新一期
# --------------------------------------------------------------------------- #

def test_audit_flags_incomplete_pool_member(env):
    reports, _ = env
    _report(reports, "601088", {**_complete("601088"), "季度财报解读":
                                artifacts.PLACEHOLDERS["季度财报解读"]})
    assert artifacts.audit_placeholders() == {"601088": ["季度财报解读"]}


def test_audit_silent_when_all_complete(env):
    reports, _ = env
    _report(reports, "601088", _complete("601088"))
    assert artifacts.audit_placeholders() == {}


def test_audit_ignores_non_pool_and_removed(env):
    """池外的、已移出的报告带占位符**不报** —— 否则天天告警，人会开始忽略它。"""
    reports, pool_path = env
    _write_pool(pool_path, active=["601088.SH"], removed=["300061.SZ"])
    _report(reports, "601088", _complete("601088"))            # 池内、完整
    _report(reports, "300061", dict(artifacts.PLACEHOLDERS))   # 已移出、四段全缺
    _report(reports, "600519", {"商业模式": artifacts.PLACEHOLDERS["商业模式"]})  # 池外
    assert artifacts.audit_placeholders() == {}


def test_audit_only_reads_newest_period(env):
    """同一标的多期：只体检最新一期（与首页/对比表同口径）。"""
    reports, _ = env
    _report(reports, "601088", _complete("601088"), period="2026Q1")   # 旧期完整
    _report(reports, "601088", {"商业模式": artifacts.PLACEHOLDERS["商业模式"]},
            period="2026Q2")                                            # 新期缺
    assert artifacts.audit_placeholders() == {"601088": ["商业模式"]}


# --------------------------------------------------------------------------- #
# 防漂移：渲染端与日更都必须引用同一份判据
# --------------------------------------------------------------------------- #

def test_build_valueline_does_not_hardcode_placeholder_text():
    """`build_valueline.py` 不得再写死占位符字面量 —— 必须引用 `artifacts.PLACEHOLDERS`。

    写死 = 与体检端各存一份判据 = 改一处忘一处 = **体检漏报**（正是 F9 的成因）。
    """
    code = _code_only("scripts/build_valueline.py")
    for name, text in artifacts.PLACEHOLDERS.items():
        assert text not in code, (
            f"build_valueline.py 又硬编码了占位符 {name}（{text!r}）—— "
            f"应改为引用 artifacts.PLACEHOLDERS"
        )
    assert "PLACEHOLDERS" in code, "build_valueline.py 未引用 artifacts.PLACEHOLDERS"


def test_daily_refresh_runs_placeholder_audit():
    """日更收尾必须跑体检 —— 否则 F9 类问题仍然没有任何自动信号。"""
    code = _code_only("scripts/daily_refresh.py")
    assert "audit_placeholders" in code, "日更没有调用占位符体检"
