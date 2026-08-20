#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ValueLine 一页研报导出脚本：HTML -> PNG 长图 / PDF。

依赖安装（首次）：
    pip install playwright
    playwright install chromium

用法：
    python scripts/export.py templates/valueline.html -o reports/2026Q2 -f png pdf

说明：
    - PNG 为高清长图（device_scale_factor=2），可直接用于小红书 / 小程序素材。
    - PDF 按 A4 打印，适合归档与分享。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def export(html_path: Path, out_dir: Path, formats: list[str], width: int = 794) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright，请先执行：pip install playwright && playwright install chromium")

    if not html_path.exists():
        sys.exit(f"文件不存在：{html_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = html_path.stem
    uri = html_path.resolve().as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": 1200},
            device_scale_factor=2,
        )
        page.goto(uri)
        page.wait_for_timeout(300)

        if "png" in formats:
            page.screenshot(path=str(out_dir / f"{stem}.png"), full_page=True)
        if "pdf" in formats:
            page.pdf(path=str(out_dir / f"{stem}.pdf"), format="A4", print_background=True)

        browser.close()

    print(f"导出完成：{out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="导出 ValueLine 一页研报")
    ap.add_argument("html", help="HTML 模板路径")
    ap.add_argument("-o", "--out", default="reports", help="输出目录（默认 reports）")
    ap.add_argument(
        "-f", "--formats", nargs="+", default=["png", "pdf"],
        choices=["png", "pdf"], help="导出格式（默认 png pdf）",
    )
    args = ap.parse_args()
    export(Path(args.html), Path(args.out), args.formats)


if __name__ == "__main__":
    main()
