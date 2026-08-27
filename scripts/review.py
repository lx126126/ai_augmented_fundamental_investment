#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘层录入：生成假设台账草稿（AI 草稿 + 上季待复盘模板），人工改后由 build 渲染。

用法：
    python scripts/review.py 601088            # 生成本季假设草稿（period 默认当前季度）
    python scripts/review.py 601088 2026Q2     # 指定报告期

流程：
    1. 读股票数据（adapter）
    2. 若有上季台账，把上季假设摊成「待复盘」模板（verdict/actual/why 留空）
    3. AI 基于财务数据生成 2-3 条可证伪假设草稿
    4. 组装台账存到 watchlist/reviews/{code}/{period}.json

人工改完 JSON 后，跑 build_valueline.py 即把复盘渲染进报告「季度复盘」区。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.review.ledger import current_period, load_period, new_ledger, prev_period, save_period
from src.report.llm import generate_hypotheses
from src.data.adapter import build_template_data

VERDICT_HINT = "verified / refuted / partial"


def _build_draft(code: str, period: str) -> tuple[dict | None, str]:
    """生成台账草稿，返回 (ledger, 保存路径)。失败返回 (None, "")。"""
    try:
        real = build_template_data(code)
    except Exception as e:
        print(f"[review] 读数据失败: {e}")
        return None, ""
    name = real.get("company_name") or code
    narrative = real.get("narrative_data")

    ledger = new_ledger(code, name, period)

    # 上季假设 → 待复盘模板
    prev = load_period(code, prev_period(period))
    if prev:
        ledger["reviews"] = [
            {"id": h["id"], "verdict": "", "actual": "", "why": ""}
            for h in prev.get("hypotheses", [])
        ]

    # AI 生成假设草稿
    if narrative:
        drafts = generate_hypotheses(narrative)
        if drafts:
            ledger["hypotheses"] = [
                {"id": f"{period}-{i + 1}", **d}
                for i, d in enumerate(drafts)
            ]

    path = save_period(ledger)
    return ledger, str(path)


def main() -> None:
    code = (sys.argv[1] if len(sys.argv) > 1 else "601088").zfill(6)
    period = sys.argv[2] if len(sys.argv) > 2 else current_period()

    ledger, path = _build_draft(code, period)
    if ledger is None:
        return

    n_h = len(ledger["hypotheses"])
    n_r = len(ledger["reviews"])
    print(f"已生成台账草稿: {path}")
    print(f"  本季假设 {n_h} 条（AI 草稿，请人工改/删/补）")
    if n_r:
        print(f"  待复盘 {n_r} 条（上季假设，请填 verdict[{VERDICT_HINT}]/actual/why）")
    else:
        print("  冷启动：无上季假设，本季为第一期（下季度才有复盘）")
    print(f"\n下一步：人工改 JSON → python scripts/build_valueline.py {code}")


if __name__ == "__main__":
    main()
