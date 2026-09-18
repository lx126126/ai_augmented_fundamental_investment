# -*- coding: utf-8 -*-
"""报告产物定位（`src/report/artifacts.py`）的单元测试。

这个模块要守的是**「什么算一份报告」只能有一个定义** —— 之前有三份，且口径互不相同：

| 位置 | 原算法 | 后果 |
|---|---|---|
| `web/server.py::_find_report` | 取 mtime 最大 | 会把 `reports/xhs/600519.html`（小红书九宫格发布包）当报告 |
| `web/server.py::health` | `glob("*/*.html")` 计数 | 报 14，而首页只列 11 |
| `build_web_index._scan_reports` | 跳过 xhs + 按报告期 | 唯一正确的那份 |

全部 hermetic：把 `REPORTS_DIR` 与跟踪池路径指到 tmp，不碰真实 reports/、不发请求。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from src.data import watchlist_store as wl
from src.report import artifacts

ROOT = Path(__file__).resolve().parent.parent


def _touch_reports(reports_dir: Path, spec: dict[str, list[str]],
                   mtimes: dict[str, float] | None = None) -> None:
    """按 `{报告期: [代码, …]}` 造报告文件；`mtimes` 可选指定 `'期/代码' → mtime`。"""
    for period, codes in spec.items():
        d = reports_dir / period
        d.mkdir(parents=True, exist_ok=True)
        for code in codes:
            p = d / f"{code}.html"
            p.write_text("<html></html>", encoding="utf-8")
            if mtimes and f"{period}/{code}" in mtimes:
                t = mtimes[f"{period}/{code}"]
                os.utime(p, (t, t))


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    """把 REPORTS_DIR 指到 tmp，返回那个目录。"""
    d = tmp_path / "reports"
    d.mkdir()
    monkeypatch.setattr(artifacts, "REPORTS_DIR", d)
    return d


@pytest.fixture()
def pool(tmp_path, monkeypatch):
    """把跟踪池指到 tmp（默认池内只有 601088 一只，有报告）。"""
    p = tmp_path / "watchlist.json"
    _write_pool(p, active=["601088.SH"], removed=[])
    monkeypatch.setattr(wl, "WATCHLIST_PATH", p)
    return p


def _write_pool(path: Path, active: list[str], removed: list[str]) -> None:
    stocks = [{"code": c, "name": c, "industry": "测试", "lynch": "周期型",
               "status": "active", "color": "#378ADD"} for c in active]
    stocks += [{"code": c, "name": c, "industry": "测试", "lynch": "周期型",
                "status": "removed", "color": "#E24B4A",
                "removed_at": "2026-09-18"} for c in removed]
    path.write_text(json.dumps({"version": 1, "rules": {}, "stocks": stocks},
                               ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 判据 ①②：只认报告期目录，且取最新一期
# --------------------------------------------------------------------------- #

def test_xhs_deck_is_not_a_report(reports):
    """`reports/xhs/*.html` 是小红书发布包，绝不能算报告。

    这是**真实存在的坑**：`reports/xhs/600519.html` 是 9 张图卡拼的 deck。
    旧的 `_find_report` 取 mtime 最大，而发布包刚重建过就会更新 mtime ——
    于是 `/report/600519` 会返回一张九宫格卡片页。
    """
    _touch_reports(reports, {"xhs": ["600519"], "2026Q2": ["600519"]})
    assert artifacts.scan()["600519"].parent.name == "2026Q2"
    assert len(artifacts.scan()) == 1, "xhs 下的文件不能进候选"


def test_newest_period_wins_even_if_its_file_is_older(reports):
    """同一标的多个报告期 → 取**最新报告期**，与 mtime 无关。

    重跑一次旧期（mtime 更新）不该让它抢镜 —— 旧实现按 mtime 取最大，会抢。
    """
    _touch_reports(reports, {"2026Q1": ["601088"], "2026Q2": ["601088"]},
                   mtimes={"2026Q1/601088": 2_000_000_000, "2026Q2/601088": 1_000_000_000})
    assert artifacts.scan()["601088"].parent.name == "2026Q2"


def test_find_normalizes_suffix_and_short_code(reports):
    """`601088.SH` / `601088` / 港股 5 位补齐 都找得到（服务端路由不再自己算候选）。"""
    _touch_reports(reports, {"2026Q2": ["601088", "00700"]})
    assert artifacts.find("601088.SH").name == "601088.html"
    assert artifacts.find("601088").name == "601088.html"
    assert artifacts.find("700").name == "00700.html"
    assert artifacts.find("999999") is None


# --------------------------------------------------------------------------- #
# 判据 ③：池内 / 池外 / 已移出 三态对账
# --------------------------------------------------------------------------- #

def test_inventory_splits_three_states(reports, pool):
    """首页卡片数 = in_pool + orphan；orphan 是**异常态**（入池链路可能断了）。"""
    _touch_reports(reports, {"2026Q2": ["601088", "600519", "300061"]})
    _write_pool(pool, active=["601088.SH", "600520.SH"], removed=["300061.SZ"])

    inv = artifacts.inventory()
    assert inv["in_pool"] == ["601088"]          # 池内且有报告
    assert inv["missing"] == ["600520"]          # 在池但还没报告
    assert inv["orphan"] == ["600519"]           # 有报告但不在池 → 异常
    assert inv["removed"] == ["300061"]          # 刻意移出，报告仍在磁盘
    assert inv["shown_count"] == 2               # 首页实际列出 2 张卡


def test_inventory_not_fooled_by_xhs(reports, pool):
    """`on_disk` 是「报告文件数」，不含发布包 —— `/api/health` 的对外口径就是它。"""
    _touch_reports(reports, {"xhs": ["600519"], "2026Q2": ["601088"]})
    inv = artifacts.inventory()
    assert len(inv["on_disk"]) == 1
    assert inv["shown_count"] == 1


# --------------------------------------------------------------------------- #
# 防漂移：别再造第二份实现
# --------------------------------------------------------------------------- #

def _code_only(path: Path) -> str:
    """源码去掉注释与文档字符串 —— 只在**代码**里找违规，注释里提一句不算。

    （第一版直接扫原文，结果被自己的说明文字「原先用 `glob("*/*.html")`」绊倒。）
    """
    src = re.sub(r'""".*?"""', "", path.read_text(encoding="utf-8"), flags=re.S)
    src = re.sub(r"'''.*?'''", "", src, flags=re.S)
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


def test_no_second_report_scanner_in_server_or_index():
    """`web/server.py` 与 `build_web_index.py` 都不能自己 glob 报告文件。

    口径分家的成本是「两个数字对不上、且会静默返回错文件」（见模块 docstring）。
    这条断言让「顺手就地写一段 glob」变成红色失败。
    """
    for rel in ("web/server.py", "scripts/build_web_index.py"):
        code = _code_only(ROOT / rel)
        assert "artifacts" in code, f"{rel} 未使用 artifacts（报告判据的唯一实现）"
        assert 'glob(f"*/' not in code, f"{rel} 又自己 glob 报告文件了"
        assert 'glob("*/*.html")' not in code, f"{rel} 又按文件个数算报告数了"
        assert "REPORTS_DIR.glob" not in code, f"{rel} 又自己扫 reports/ 了"
