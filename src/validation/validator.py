"""数据验证：官方年报 PDF（金标准）vs 接口数据，容差 <0.1%。

流程：下载年报 PDF → 解析「主要会计数据」→ 读接口数据 → 逐项对比 → 校验记录。
支持单年验证（validate）与全历史验证（validate_history，错位采样）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from .cninfo import download_annual_report
from .pdf_parser import (
    PDF_FIELD_ALIASES,
    parse_balance_sheet,
    parse_financials_by_year,
    parse_key_financials,
)

TOLERANCE_PCT = 0.1  # 容差 0.1%
RECONCILE_TOLERANCE_PCT = 1.0  # 覆盖容差 1%（放宽，避免四舍五入误判）

# 合并资产负债表字段 → 中文标签（用于覆盖记录展示）
_BS_LABEL = {
    "monetary_funds": "货币资金", "accounts_receivable": "应收账款", "inventory": "存货",
    "current_assets": "流动资产合计", "fixed_assets": "固定资产", "total_assets": "总资产",
    "short_term_loan": "短期借款", "accounts_payable": "应付账款",
    "noncurrent_liab_1y": "一年内到期非流动负债", "current_liabilities": "流动负债合计",
    "long_term_loan": "长期借款", "bond_payable": "应付债券", "lease_liabilities": "租赁负债",
    "long_payable": "长期应付款", "total_liabilities": "总负债",
    "retained_profit": "未分配利润", "total_equity": "归母净资产",
    "minority_equity": "少数股东权益", "total_equity_all": "股东权益合计",
}

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


def reconcile_balance_sheet(code: str, year: int,
                            data_dir: str | Path = "data/raw",
                            pdf_dir: str | Path = "data/validation") -> list[dict]:
    """用官方年报 PDF 合并资产负债表（金标准）覆盖接口错误字段，写回 parquet。

    背景：东财/新浪等第三方接口同源（同一底层数据供应商），在「同一控制下企业合并
    追溯重述」等特殊情形下会抓取错误——如神华 2025 年总资产被接口报成 9038 亿（官方
    6278 亿）、短期借款 131 亿（官方 4 亿）。官方年报 PDF 的合并资产负债表是唯一权威
    源，本函数逐字段对比，差异超过容差（1%）即用 PDF 值覆盖。

    覆盖后 cleaner 会重新跑（_to_yi + 派生指标重算），故无需重复派生逻辑。

    覆盖记录持久化到 data/validation/{code}_{year}_reconcile.json，供报告「数据校验」区
    展示（parquet 覆盖后再次对比会一致，故记录须落盘保存）。

    返回覆盖记录列表 [{field, label, api_yi, pdf_yi, diff_pct}]。
    """
    import json

    pdf_path = Path(pdf_dir) / f"{code}_{year}年报.pdf"
    if not pdf_path.exists():
        pdf_path = download_annual_report(code, year, Path(pdf_dir))
    golden = parse_balance_sheet(pdf_path)  # {标准字段: 元}
    if not golden:
        return []

    bs_path = Path(data_dir) / code / "balance_sheet.parquet"
    if not bs_path.exists():
        return []
    bs = pd.read_parquet(bs_path)

    d = pd.to_datetime(bs["report_date"])
    mask = (d.dt.year == year) & (d.dt.month == 12)  # 明确筛年报行，避免误取季度行
    if not mask.any():
        return []
    idx = bs[mask].index[0]

    corrections: list[dict] = []
    for field, pdf_val in golden.items():
        if field not in bs.columns or pdf_val is None:
            continue
        api_val = bs.at[idx, field]
        if pd.isna(api_val):
            continue
        if pdf_val == 0 and api_val == 0:
            continue
        diff = abs(api_val - pdf_val) / pdf_val * 100 if pdf_val else 0
        if diff > RECONCILE_TOLERANCE_PCT:
            bs.at[idx, field] = pdf_val
            corrections.append({
                "field": field, "label": _BS_LABEL.get(field, field),
                "api_yi": api_val / 1e8, "pdf_yi": pdf_val / 1e8, "diff_pct": diff,
            })

    if corrections:
        bs.to_parquet(bs_path, index=False)
        log_path = Path(pdf_dir) / f"{code}_{year}_reconcile.json"
        log_path.write_text(json.dumps({"code": code, "year": year, "items": corrections},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    return corrections


def load_reconcile_log(code: str, year: int,
                       pdf_dir: str | Path = "data/validation") -> list[dict]:
    """读取历史数据交叉校验覆盖记录（parquet 已覆盖后，报告据此展示修正项）。"""
    import json

    log_path = Path(pdf_dir) / f"{code}_{year}_reconcile.json"
    if not log_path.exists():
        return []
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
        return data.get("items", [])
    except (json.JSONDecodeError, OSError):
        return []
