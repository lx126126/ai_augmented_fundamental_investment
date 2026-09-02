# -*- coding: utf-8 -*-
"""生成手机网页版首页 web/index.html。

从 reports/ 归档目录扫描最新报告期 + 每只标的的报告，配合内置的名称/行业映射，
动态生成首页卡片列表。首页永远反映真实数据状态，避免手写静态卡片过期。

用法：
    python scripts/build_web_index.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
WEB_DIR = ROOT / "web"

# 跟踪池名称/行业映射（手维护，6 只以内；新标的加一行即可）
STOCK_META = {
    "601088": ("中国神华", "煤炭开采 · 周期型稳健收息", "#378ADD"),
    "600519": ("贵州茅台", "白酒 · 稳健增长型", "#E24B4A"),
    "000651": ("格力电器", "家电 · 稳健增长型收息", "#BA7517"),
    "601328": ("交通银行", "银行 · 稳健增长型收息", "#888780"),
    "600036": ("招商银行", "银行 · 稳健增长型收息", "#5B8FF9"),
    "601166": ("兴业银行", "银行 · 稳健增长型", "#3D9A5B"),
    "601838": ("成都银行", "银行 · 稳健增长型", "#9A6BBF"),
    "09992": ("泡泡玛特", "潮玩消费 · 快速增长型", "#F08BB0"),
}

# 报告期排序键：2026Q2 -> (2026, 2)
def _period_key(p: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})Q([1-4])", p)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (0, 0)


def _scan_reports() -> dict[str, str]:
    """扫描 reports/，返回 {code: 最新报告期相对路径}。"""
    result: dict[str, str] = {}
    if not REPORTS_DIR.exists():
        return result
    for period_dir in sorted(REPORTS_DIR.iterdir(), key=lambda d: _period_key(d.name), reverse=True):
        if not period_dir.is_dir():
            continue
        for html in period_dir.glob("*.html"):
            code = html.stem
            if code not in result:
                result[code] = html.relative_to(ROOT).as_posix()
    return result


def build_cards(reports: dict[str, str]) -> str:
    """生成首页卡片 HTML。按 STOCK_META 顺序（跟踪池顺序）输出。"""
    cards: list[str] = []
    # 先按跟踪池顺序输出已有的报告
    for code, (name, industry, color) in STOCK_META.items():
        if code not in reports:
            continue
        path = reports[code]
        cards.append(
            f'    <a class="stock-card" href="../{path}">\n'
            f'      <div class="head"><span class="name">{name}</span>'
            f'<span class="code">{code}</span></div>\n'
            f'      <div class="meta"><span class="dot" style="background:{color}"></span>{industry}</div>\n'
            f'      <span class="arrow">›</span>\n'
            f'    </a>'
        )
    return "\n".join(cards)


def main() -> None:
    reports = _scan_reports()
    cards = build_cards(reports)
    if not cards:
        cards = '    <div class="empty" style="text-align:center;color:#8a97a6;padding:40px 0;">暂无报告，请先运行 build 生成</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>投研排雷 · ValueLine 一页报告</title>
<style>
:root {{
  --ink: #1a2330; --muted: #5c6b7a; --faint: #8a97a6;
  --line: #dde3ea; --accent: #0f3d6e; --accent-2: #14508c; --bg-soft: #f5f7fa;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color: var(--ink); background: #e9edf1; -webkit-font-smoothing: antialiased; min-height: 100vh;
}}
.wrap {{ max-width: 640px; margin: 0 auto; padding: 28px 16px 48px; }}
.top {{ padding: 8px 0 20px; }}
.top h1 {{ font-size: 24px; font-weight: 700; color: var(--accent); letter-spacing: 1px; }}
.top p {{ font-size: 13px; color: var(--muted); margin-top: 8px; line-height: 1.7; }}
.top .pill {{ display: inline-block; margin-top: 12px; font-size: 11px; color: var(--muted); background: var(--bg-soft); border: 1px solid var(--line); border-radius: 4px; padding: 3px 10px; }}
.stock-list {{ display: flex; flex-direction: column; gap: 12px; }}
.stock-card {{
  display: block; text-decoration: none; color: inherit;
  background: #fff; border-radius: 10px; padding: 16px 18px;
  box-shadow: 0 2px 10px rgba(15,61,110,0.07);
  transition: transform .08s ease;
}}
.stock-card:active {{ transform: scale(0.99); }}
.stock-card .head {{ display: flex; justify-content: space-between; align-items: baseline; }}
.stock-card .name {{ font-size: 17px; font-weight: 700; color: var(--ink); }}
.stock-card .code {{ font-size: 12px; color: var(--faint); font-variant-numeric: tabular-nums; }}
.stock-card .meta {{ font-size: 12px; color: var(--muted); margin-top: 6px; }}
.stock-card .meta .dot {{ display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }}
.stock-card .arrow {{ float: right; color: var(--faint); font-size: 18px; margin-top: -20px; }}
.foot {{ text-align: center; font-size: 11px; color: var(--faint); margin-top: 32px; line-height: 1.8; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <h1>投研排雷</h1>
    <p>纯客观数据工具 · ValueLine 一页报告<br>数据加工 + 指标 + 风险检测，帮自己做投资决策前先排雷。</p>
    <span class="pill">第三方机构观点 · 非本人建议 · 数据可校验</span>
  </div>

  <div class="stock-list">
{cards}
  </div>

  <div class="foot">
    数据来源：AKShare / 东方财富 / 巨潮年报（官方 PDF 金标准交叉校验）<br>
    本页仅供个人研究，不含任何操作建议。
  </div>
</div>
</body>
</html>
"""
    WEB_DIR.mkdir(exist_ok=True)
    out = WEB_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"[web] 首页已生成：{out.relative_to(ROOT)}（{len(reports)} 只标的报告）")
    for code, path in reports.items():
        name = STOCK_META.get(code, (code,))[0]
        print(f"  - {code} {name}: {path}")


if __name__ == "__main__":
    main()
