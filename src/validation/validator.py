"""数据验证：官方年报 PDF（金标准）vs 接口数据，容差 <0.1%。

流程：下载年报 PDF → 解析「主要会计数据」→ 读接口数据 → 逐项对比 → 校验记录。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .cninfo import download_annual_report
from .pdf_parser import PDF_TO_FIELD, parse_key_financials, to_yuan

# 金额字段在接口数据里的单位是「亿元」，股本字段是「股」
_SHARE_FIELDS = {"total_shares"}
TOLERANCE_PCT = 0.1  # 容差 0.1%

_FIELD_LABEL = {
    "operating_revenue": "营业收入",
    "net_profit_parent": "归母净利润",
    "ocf": "经营现金流净额",
    "total_equity": "归母净资产",
    "total_assets": "总资产",
    "total_liabilities": "总负债",
    "total_shares": "总股本",
}


def _load_api_annual(code: str, year: int) -> dict | None:
    """读接口数据（parquet → cleaner 宽表），取指定年份行。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src.data.adapter import load_raw
    from src.data.cleaner import build_annual_financials

    raw = load_raw(code)
    if not raw or "profit_sheet" not in raw:
        return None
    annual = build_annual_financials(raw)
    row = annual[annual["report_date"].dt.year == year]
    if row.empty:
        return None
    return row.iloc[-1].to_dict()


def _to_base(field: str, api_value: float) -> float:
    """接口值 → 原始单位（元 / 股），与 PDF 的 to_yuan 对齐。"""
    if field in _SHARE_FIELDS:
        return api_value  # 已经是「股」
    return api_value * 1e8  # 亿元 → 元


def validate(code: str, year: int, out_dir: str | Path = "data/validation") -> dict:
    """对比指定股票的某年年报官方数据与接口数据，返回校验记录。"""
    # 1. 下载 + 解析官方年报 PDF
    pdf_path = download_annual_report(code, year, Path(out_dir))
    golden = parse_key_financials(pdf_path)

    # 2. 读接口数据
    api = _load_api_annual(code, year)

    # 3. 逐项对比
    items = []
    passed = 0
    for field in PDF_TO_FIELD.values():
        g = golden.get(field)
        if g is None:
            items.append({"field": field, "label": _FIELD_LABEL[field],
                          "status": "缺失", "golden": None, "api": None, "diff_pct": None})
            continue
        if api is None or field not in api or api[field] is None:
            items.append({"field": field, "label": _FIELD_LABEL[field],
                          "status": "接口缺失", "golden": g, "api": None, "diff_pct": None})
            continue

        g_base = to_yuan(g, field)
        a_base = _to_base(field, api[field])
        diff_pct = abs(g_base - a_base) / g_base * 100 if g_base else None
        status = "一致" if (diff_pct is not None and diff_pct < TOLERANCE_PCT) else "差异"
        if status == "一致":
            passed += 1
        items.append({
            "field": field, "label": _FIELD_LABEL[field], "status": status,
            "golden": g, "api": api[field], "diff_pct": diff_pct,
        })

    return {
        "code": code,
        "year": year,
        "pdf_path": str(pdf_path),
        "passed": passed,
        "total": len(items),
        "items": items,
    }


def format_report(result: dict) -> str:
    """校验记录 → 可读文本。"""
    lines = [
        f"数据校验：{result['code']} {result['year']}年报",
        f"金标准：巨潮官方年报 PDF（{result['pdf_path']}）",
        f"通过 {result['passed']}/{result['total']} 项（容差 <{TOLERANCE_PCT}%）",
        "",
    ]
    for it in result["items"]:
        label = it["label"]
        if it["status"] == "一致":
            lines.append(f"  ✓ {label}: 官方 {it['golden']:,.2f} vs 接口 {it['api']:,.2f}（差 {it['diff_pct']:.4f}%）")
        elif it["status"] == "差异":
            lines.append(f"  ✗ {label}: 官方 {it['golden']:,.2f} vs 接口 {it['api']:,.2f}（差 {it['diff_pct']:.2f}%）")
        else:
            lines.append(f"  - {label}: {it['status']}")
    return "\n".join(lines)
