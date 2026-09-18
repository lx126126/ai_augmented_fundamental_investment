#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报告完整性体检：池内标的的报告里，LLM 生成的内容是否退化成了占位符。

为什么需要
----------
日更的 LLM 内容一律走「只读缓存」（见 `build_valueline.build` 的 daily 分支），
所以**没有缓存的标的永远不会被补上** —— 报告里会一直挂着「待生成」，
而这条链路零报错、日志干净、文件大小正常。

2026-09-18 实测：11 只池内标的里有 5 只的「季度财报解读」是占位符，
没有任何地方会为此报警。详见 `docs/report-chains.md` F9。

本脚本只**报告**，不修复 —— 补内容要跑一次**不带** `--daily` 的完整构建。

用法
----
    python scripts/check_placeholders.py

退出码：0 = 全部完整；1 = 存在占位符（可直接用于 `&&` 串联或 CI）。

⚠️ 日更链路里调用同一判据时**不应挡闸门** —— 理由与派生展示产物相同：
重跑成本很低，挡了会让当天整批标的重拉一遍（见 `daily_refresh` 模块 docstring）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.report.artifacts import PLACEHOLDERS, audit_placeholders  # noqa: E402


def main() -> int:
    bad = audit_placeholders()
    if not bad:
        print("✓ 池内报告内容完整，无占位符")
        return 0

    print(f"⚠️ {len(bad)} 份报告含占位符（LLM 内容缺失）：\n")
    for code, blocks in sorted(bad.items()):
        print(f"  {code}: {' / '.join(blocks)}")

    print("\n检测依据（占位符特征串，唯一真源：src/report/artifacts.py）")
    for name, text in PLACEHOLDERS.items():
        print(f"  {name}: {text}")

    print("\n常见原因：该标的从未跑过不带 --daily 的构建 → LLM 缓存从未落地；")
    print("          而日更只复用缓存（cache_only=True），所以永远不会被补上。")
    print("\n修法（一次一只，脚本读 sys.argv[1]）：")
    print("  python scripts/build_valueline.py <代码>")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
