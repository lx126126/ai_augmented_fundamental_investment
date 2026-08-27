#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投研日记模板生成：journal/{code}/{YYYY-MM}.md（内部操作层，gitignore）。

用法：
    python scripts/journal.py 601088            # 生成当月日记（已存在则跳过）
    python scripts/journal.py 601088 --force    # 覆盖重建

铁律：日记是操作层（买卖/仓位/成本/决策心理），本脚本只生成
「基本面快照（真实数据）+ 三栏空模板」，操作必须由本人填写。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.adapter import build_template_data
from src.review.ledger import load_latest

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"


def _fmt(v, digits=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _snapshot(real: dict) -> str:
    """从真实数据拼基本面快照（非操作建议）。"""
    val = real.get("valuation") or {}
    narr = real.get("narrative_data") or {}
    latest = narr.get("latest") or {}
    year = narr.get("latest_year", "—")

    lines = ["## 基本面快照（真实数据，非操作建议）"]
    price = val.get("price_now")
    pe = val.get("pe")
    pb = val.get("pb")
    lines.append(
        f"- 现价 {_fmt(price, 2)} 元 | PE(TTM) {_fmt(pe, 1)} | PB {_fmt(pb, 2)}"
    )
    dy = val.get("dividend_yield")
    lines.append(f"- 股息率 {_fmt(dy, 1, '%')}")
    lo, hi = val.get("price_low"), val.get("price_high")
    if lo is not None and hi is not None:
        lines.append(f"- 52 周区间 {_fmt(lo, 2)} ~ {_fmt(hi, 2)} 元")

    rev = latest.get("revenue")
    np_ = latest.get("net_profit")
    roe = latest.get("roe")
    debt = latest.get("debt_ratio")
    lines.append(f"- 最新年报（{year}）：营收 {_fmt(rev, 1)} 亿 | 归母净利 {_fmt(np_, 1)} 亿 | ROE {_fmt(roe, 1, '%')} | 负债率 {_fmt(debt, 1, '%')}")

    pe_p = val.get("pe_pctile")
    pb_p = val.get("pb_pctile")
    lines.append(f"- 估值分位：PE 近10年 {_fmt(pe_p, 0, '%')} / PB 近10年 {_fmt(pb_p, 0, '%')}")
    return "\n".join(lines)


def _hypotheses_section(code: str) -> str:
    """从假设台账读本季假设，生成「假设复盘」栏（下季验证用）。"""
    ledger = load_latest(code)
    hyps = (ledger or {}).get("hypotheses", [])
    lines = ["## 假设复盘（本季假设，下季逐条验证）"]
    if not hyps:
        lines.append("- （暂无假设，可运行 scripts/review.py 生成）")
    else:
        for h in hyps:
            line = f"- {h.get('statement', '')}"
            if h.get("metric"):
                line += f" 【{h['metric']}】"
            if h.get("basis"):
                line += f"（依据：{h['basis']}）"
            lines.append(line)
    return "\n".join(lines) + "\n"


_TEMPLATE_TAIL = """
## 操作
- （待填：日期 + 方向 + 价格 + 仓位，未成交也记）

## 决策心理
- （待填：为什么这个价/这个时点，在犹豫什么）

## 事后
- （复盘时补：这个决定对不对，为什么）
"""


def generate(code: str, force: bool = False) -> Path:
    """生成当月日记，已存在则跳过（除非 force）。返回文件路径。"""
    code = code.zfill(6)
    real = build_template_data(code)
    name = real.get("company_name") or code
    ym = date.today().strftime("%Y-%m")

    path = JOURNAL_DIR / code / f"{ym}.md"
    if path.exists() and not force:
        print(f"跳过（已存在）: {path}")
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"# {ym} {name}\n\n" + _snapshot(real) + "\n\n" + _hypotheses_section(code) + _TEMPLATE_TAIL
    path.write_text(content, encoding="utf-8")
    print(f"已生成: {path}")
    return path


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    code = args[0] if args else "601088"
    generate(code, force)


if __name__ == "__main__":
    main()
