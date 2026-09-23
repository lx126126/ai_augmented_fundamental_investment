#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验小红书正文里的每个数字都能在对应图卡上找到出处。

为什么需要
----------
这套内容的铁律是「**正文里的每个数字都能在图卡上逐个对应**」。
但正文是**手写**的、图卡是**生成**的 —— 两边靠肉眼核对必然漏，而漏掉的
数字读者在图上翻不到出处，就成了「无源之数」。所以把这条铁律变成可执行检查。

判据很宽松（只看「这个数字串有没有在图卡 HTML 里出现过」），因为目的是
**发现对不上的数**，不是校验语义。所以：
  - 千分位逗号忽略（`13,597.32` 与 `13597.32` 视为同一个数）
  - Unicode 负号 `−`(U+2212) 与 ASCII `-` 视为同一个符号
  - 图卡 HTML 的**标签属性**也参与匹配（`width:70%` 之类），宁可漏报不误报

用法：
    python scripts/check_caption_numbers.py 正文_纯文本.txt macro.html
    python scripts/check_caption_numbers.py 正文_纯文本.txt macro.html --ignore 2026
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: 数字（含千分位与小数）。也会命中日期里的「2026」「09」，所以用 --ignore 放行。
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(s: str) -> str:
    """统一负号与千分位，便于两边比对。"""
    return s.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in _NUM_RE.finditer(_norm(text)):
        raw = m.group(0)
        out.add(raw)
        out.add(raw.replace(",", ""))       # 去掉千分位后的写法
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="核对正文数字是否都能在图卡上找到")
    ap.add_argument("caption", help="正文纯文本路径")
    ap.add_argument("html", help="图卡 HTML 路径")
    ap.add_argument("--ignore", action="append", default=[],
                    help="放行的数字（可多次指定，如年份 2026）")
    args = ap.parse_args()

    cap = Path(args.caption).read_text(encoding="utf-8")
    html = _norm(Path(args.html).read_text(encoding="utf-8"))
    haystack = numbers_in(html)
    ignore = set(args.ignore)

    missing: list[str] = []
    for token in sorted(numbers_in(cap), key=lambda x: (len(x), x)):
        bare = token.replace(",", "")
        if bare in ignore or len(bare) <= 1:
            continue          # 单字符（0/1/2…）在 HTML 里到处都有，无判别力
        if bare not in haystack and token not in haystack:
            missing.append(token)

    if not missing:
        print(f"✅ 正文数字全部能在图卡上找到出处（逐项 {len(numbers_in(cap))} 个数字串）")
        return 0

    print(f"🔴 有 {len(missing)} 个数字在图卡上找不到出处：")
    for m in missing:
        # 打印它在正文里的上下文，方便人工确认是笔误还是该补进图卡
        for line in cap.splitlines():
            if m in line:
                print(f"   {m:<14} ← {line.strip()[:96]}")
                break
        else:
            print(f"   {m}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
