"""数据验证：官方年报 PDF（金标准）vs 接口数据，容差 <0.1%。

流程：下载年报 PDF → 解析「主要会计数据」→ 读接口数据 → 逐项对比 → 校验记录。
支持单年验证（validate）与全历史验证（validate_history，错位采样）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .cninfo import download_annual_report
from .pdf_parser import PDF_FIELD_ALIASES, parse_financials_by_year, parse_key_financials

TOLERANCE_PCT = 0.1  # 容差 0.1%

_FIELD_LABEL = {
    "operating_revenue": "营业收入",
    "net_profit_parent": "归母净利润",
    "ocf": "经营现金流净额",
    "total_equity": "归母净资产",
    "total_assets": "总资产",
    "total_liabilities": "总负债",
    "share_capital": "总股本",
}


def _load_api_annual_all(code: str) -> dict[int, dict]:
    """读接口数据（parquet → cleaner 宽表），返回 {年份: 该年宽表行}。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from src.data.adapter import load_raw
    from src.data.cleaner import build_annual_financials

    raw = load_raw(code)
    if not raw or "profit_sheet" not in raw:
        return {}
    annual = build_annual_financials(raw)
    return {int(r["report_date"].year): r.to_dict() for _, r in annual.iterrows()}


def _to_base(api_value: float) -> float:
    """接口值 → 原始单位（金额「亿元→元」、股本「亿股→股」，均 ×1e8）。"""
    return api_value * 1e8


def _compare(golden: dict, api_row: dict | None) -> tuple[list, int]:
    """对比单年，返回 (items, passed)。

    golden 值可能是标量（单年）或 {"restated": x, "original": y}（多年，重述口径）。
    支持「重述后/重述前」任一匹配 <0.1%（同一控制下合并会追溯重述）。
    """
    items = []
    passed = 0
    for field in PDF_FIELD_ALIASES.keys():
        g = golden.get(field)
        if g is None:
            items.append({"field": field, "label": _FIELD_LABEL[field],
                          "status": "缺失", "golden": None, "api": None, "diff_pct": None})
            continue
        if api_row is None or field not in api_row or api_row[field] is None:
            items.append({"field": field, "label": _FIELD_LABEL[field],
                          "status": "接口缺失", "golden": g, "api": None, "diff_pct": None})
            continue

        a_base = _to_base(api_row[field])
        # 重述口径：取「重述后/重述前」中更接近接口值的一个（golden 已是「元」）
        candidates = (g.get("restated"), g.get("original")) if isinstance(g, dict) else (g,)
        diff_pct = None
        matched_val = None
        for c in candidates:
            if c is None:
                continue
            d = abs(c - a_base) / c * 100 if c else None
            if d is not None and (diff_pct is None or d < diff_pct):
                diff_pct = d
                matched_val = c
        status = "一致" if (diff_pct is not None and diff_pct < TOLERANCE_PCT) else "差异"
        if status == "一致":
            passed += 1
        items.append({
            "field": field, "label": _FIELD_LABEL[field], "status": status,
            "golden": matched_val, "api": api_row[field], "diff_pct": diff_pct,
        })
    return items, passed


def validate(code: str, year: int, out_dir: str | Path = "data/validation") -> dict:
    """对比指定股票的某年年报官方数据与接口数据，返回校验记录。"""
    pdf_path = download_annual_report(code, year, Path(out_dir))
    golden = parse_key_financials(pdf_path)
    api = _load_api_annual_all(code).get(year)
    items, passed = _compare(golden, api)

    return {
        "code": code,
        "year": year,
        "pdf_path": str(pdf_path),
        "passed": passed,
        "total": len(items),
        "items": items,
    }


def validate_history(code: str, out_dir: str | Path = "data/validation") -> dict:
    """全历史校验（错位采样）：每 3 年下载一个年报，覆盖近三年，拼出全历史。

    返回 {code, years, total_checks, passed, failed, per_year}。
    """
    api_annual = _load_api_annual_all(code)
    if not api_annual:
        return {"code": code, "error": "无接口数据"}

    years = sorted(api_annual.keys())
    min_year, max_year = years[0], years[-1]

    # 错位采样：从最新年报开始，每 3 年一个（每个年报覆盖近三年）
    sample_years = list(range(max_year, min_year - 1, -3))

    per_year: dict[int, dict] = {}
    for sy in sample_years:
        try:
            pdf_path = download_annual_report(code, sy, Path(out_dir))
            by_year = parse_financials_by_year(pdf_path)
        except Exception:
            continue
        for year, golden in by_year.items():
            if year in api_annual and year not in per_year:
                items, passed = _compare(golden, api_annual[year])
                per_year[year] = {"passed": passed, "total": len(items), "items": items}

    total_checks = sum(v["total"] for v in per_year.values())
    passed = sum(v["passed"] for v in per_year.values())
    failed = [
        {"year": y, **it}
        for y, v in per_year.items()
        for it in v["items"] if it["status"] not in ("一致",)
    ]

    return {
        "code": code,
        "year_range": f"{min_year}-{max_year}",
        "years_validated": len(per_year),
        "total_checks": total_checks,
        "passed": passed,
        "failed": failed,
        "per_year": per_year,
    }


def format_report(result: dict) -> str:
    """单年校验记录 → 可读文本。"""
    lines = [
        f"数据校验：{result['code']} {result['year']}年报",
        f"金标准：巨潮官方年报 PDF（{result['pdf_path']}）",
        f"通过 {result['passed']}/{result['total']} 项（容差 <{TOLERANCE_PCT}%）",
        "",
    ]
    for it in result["items"]:
        label = it["label"]
        if it["status"] == "一致":
            # golden 是「元」，转「亿元」显示；api 已是「亿元」
            g_yi = it["golden"] / 1e8 if it["golden"] is not None else None
            lines.append(f"  ✓ {label}: 官方 {g_yi:,.2f}亿 vs 接口 {it['api']:,.2f}亿（差 {it['diff_pct']:.4f}%）")
        elif it["status"] == "差异":
            g_yi = it["golden"] / 1e8 if it["golden"] is not None else None
            lines.append(f"  ✗ {label}: 官方 {g_yi:,.2f}亿 vs 接口 {it['api']:,.2f}亿（差 {it['diff_pct']:.2f}%）")
        else:
            lines.append(f"  - {label}: {it['status']}")
    return "\n".join(lines)


def format_history_report(result: dict) -> str:
    """全历史校验记录 → 可读文本。"""
    if "error" in result:
        return f"全历史校验失败：{result['error']}"
    lines = [
        f"全历史校验：{result['code']}（{result['year_range']}，验证 {result['years_validated']} 个年度）",
        f"共 {result['total_checks']} 项对比，通过 {result['passed']} 项，失败 {len(result['failed'])} 项",
        "",
    ]
    if result["failed"]:
        lines.append("失败项：")
        for f in result["failed"]:
            lines.append(f"  ✗ {f['year']} {f['label']}: {f['status']}")
    else:
        lines.append("✓ 全部年份、全部字段一致（容差 <0.1%）。")
    return "\n".join(lines)
