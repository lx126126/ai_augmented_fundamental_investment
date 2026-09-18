# -*- coding: utf-8 -*-
"""报告产物定位：**「什么算一份报告」在这里定义一次**（2026-09-18 建）。

为什么要单独一个模块
--------------------
同一件事原先在**三处**各写了一遍，口径互不相同 —— 这正是「手机上说 14 份、
首页只列 11 只」那类对不上的根源：

| 位置 | 原算法 | 后果 |
|---|---|---|
| `web/server.py::_find_report` | `reports.glob("*/{code}.html")` 取 **mtime 最大** | 会把 `reports/xhs/600519.html`（小红书九宫格发布包）当成报告返回 |
| `web/server.py::health` | `len(list(reports.glob("*/*.html")))` | 把发布包、已移出标的的报告全算进去 → 数字与首页对不上 |
| `scripts/build_web_index.py::_scan_reports` | 跳过 `xhs` 目录、按**报告期**排序取最新 | 三个里唯一正确的那个 |

`reports/xhs/600519.html` 是**发布包**（9 张图卡拼成的 deck），不是一页报告。
它至今没酿成事故纯属侥幸：`_find_report` 取 mtime 最大，而报告刚生成过、更新些。
下次重建小红书发布包，它的 mtime 就会翻新 —— `/report/600519` 于是返回一张九宫格。

判据（本模块是唯一实现）
------------------------
1. 只认**报告期目录** `reports/{YYYY}Q{n}/`。其他目录（`xhs/` 之类）一律不算报告；
2. 同一标的有多个报告期时取**最新报告期**（不按 mtime —— 重跑一次旧期不该抢镜）；
3. 口径三分：
   - `in_pool`  池内且有报告  → 首页卡片 / 对比表列
   - `orphan`   有报告但不在池、也非刻意移出 → **异常态**（入池链路可能断了）
   - `removed`  刻意移出的标的的报告 → 留在磁盘，但不展示
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = ROOT / "reports"

#: 报告期目录名。**严格全匹配**：`reports/xhs/` 这类非报告期目录因此自动出局，
#: 不需要维护一份「要跳过的目录名」黑名单（黑名单总会被新目录绕过）。
_PERIOD_RE = re.compile(r"^(\d{4})Q([1-4])$")


def period_key(name: str) -> tuple[int, int]:
    """`'2026Q2'` → `(2026, 2)`；非报告期目录返回 `(0, 0)`（排最后）。"""
    m = _PERIOD_RE.match(name or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def period_dirs(newest_first: bool = True) -> list[Path]:
    """报告期目录列表（只含 `YYYYQn` 形态，默认最新在前）。"""
    if not REPORTS_DIR.is_dir():
        return []
    dirs = [d for d in REPORTS_DIR.iterdir() if d.is_dir() and _PERIOD_RE.match(d.name)]
    return sorted(dirs, key=lambda d: period_key(d.name), reverse=newest_first)


def scan() -> dict[str, Path]:
    """`{标的代码: 最新一期的报告文件}` —— 全盘扫描，**不区分是否在跟踪池**。

    顺序敏感：先遍历最新报告期、用 `setdefault` 占位，于是同一标的只留最新那期。
    """
    out: dict[str, Path] = {}
    for d in period_dirs(newest_first=True):
        for html in sorted(d.glob("*.html")):
            out.setdefault(html.stem, html)
    return out


def find(code: str) -> Path | None:
    """找某标的的最新报告文件。不存在返回 None。"""
    c = str(code).strip().upper().split(".")[0]
    files = scan()
    for cand in ([c] if not c.isdigit() else [c, c.zfill(5), c.zfill(6)]):
        if cand in files:
            return files[cand]
    return None


def period_of(path: Path) -> str:
    """报告文件所属报告期（目录名）。"""
    return path.parent.name


def inventory() -> dict:
    """「池内 / 池外 / 已移出」三态对账表 —— 首页、对比表、`/api/health` 都取这一份。

    返回：
        on_disk     {code: Path}         磁盘上全部报告（已排除 xhs 等非报告目录）
        in_pool     [code, …]            池内且有报告（按**跟踪池顺序**，即页面排序）
        orphan      [code, …]            有报告但不在池、也非刻意移出 → 异常态
        missing     [code, …]            在池但还没报告 → 首页不显示，但值得知道
        removed     [code, …]            刻意移出的标的仍留在磁盘上的报告
        shown_count int                  首页/对比表实际展示的报告数 = in_pool + orphan
    """
    from src.data import watchlist_store as wl  # 延迟导入：watchlist_store 会连带拉起 akshare

    on_disk = scan()
    pool = wl.codes()
    removed = wl.removed_codes()

    in_pool = [c for c in pool if c in on_disk]
    orphan = sorted(c for c in on_disk if c not in pool and c not in removed)
    return {
        "on_disk": on_disk,
        "in_pool": in_pool,
        "orphan": orphan,
        "missing": [c for c in pool if c not in on_disk],
        "removed": sorted(c for c in removed if c in on_disk),
        "shown_count": len(in_pool) + len(orphan),
    }
