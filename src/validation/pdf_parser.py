"""解析官方年报 PDF 的「主要会计数据」表，提取金标准财务指标。

用 pymupdf 的 find_tables 识别表格结构（比正则更可靠）。
「主要会计数据」表含近三年对比（重述后/重述前），支持提取多年份。
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # pymupdf

# PDF 指标名 → 标准字段（与 cleaner/adapter 的宽表字段对齐）
PDF_TO_FIELD = {
    "营业收入": "operating_revenue",
    "归属于本公司股东的净利润": "net_profit_parent",
    "经营活动产生的现金流量净额": "ocf",
    "归属于本公司股东的净资产": "total_equity",
    "资产总计": "total_assets",
    "负债合计": "total_liabilities",
    "期末总股本": "share_capital",
}

# 「主要会计数据」表的列索引：(年份标题列, 重述后列, 重述前列)
# 最新年无重述（restated=original=自身）；前两年有重述前后两套口径
_YEAR_COL_CONFIG = [
    (1, 1, 1),   # 最新年：无重述
    (2, 2, 3),   # 前年：列2=重述后，列3=重述前
    (5, 5, 6),   # 大前年：列5=重述后，列6=重述前
]


def _clean(s: str) -> str:
    """去空白与换行，用于指标名匹配。"""
    return str(s).replace("\n", "").replace(" ", "").replace("\u3000", "")


def _parse_number(s: str) -> float | None:
    """解析带千分位的数字，如 '294,916' → 294916.0。"""
    s = _clean(s)
    if not s:
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _find_main_table(doc) -> list | None:
    """定位「主要会计数据」表（含「营业收入」和「资产总计」行），返回表格数据。"""
    for page in doc:
        for t in page.find_tables().tables:
            data = t.extract()
            if not data:
                continue
            first_col = [_clean(r[0]) if r else "" for r in data]
            has_revenue = any("营业收入" in c for c in first_col)
            has_assets = any("资产总计" in c for c in first_col)
            if has_revenue and has_assets:
                return data
    return None


def _extract_years(table: list) -> list[int | None]:
    """从表头行提取近三年年份（重述后列的标题）。"""
    if not table:
        return [None, None, None]
    header = table[0]
    years = []
    for title_col, _restated, _original in _YEAR_COL_CONFIG:
        cell = _clean(header[title_col]) if len(header) > title_col else ""
        m = re.search(r"(\d{4})", cell)
        years.append(int(m.group(1)) if m else None)
    return years


def parse_financials_by_year(pdf_path: str | Path) -> dict[int, dict[str, dict]]:
    """解析「主要会计数据」表，返回 {年份: {字段: {"restated": 值, "original": 值}}}。

    覆盖近三年；同一控制下合并会追溯重述，故同时保留「重述后/重述前」两套口径，
    由调用方按接口数据口径匹配。金额原始单位「百万元」，股本「百万股」。
    """
    doc = fitz.open(str(pdf_path))
    table = _find_main_table(doc)
    doc.close()
    if not table:
        return {}

    years = _extract_years(table)
    result: dict[int, dict[str, dict]] = {}

    for row in table:
        if not row or not row[0]:
            continue
        name = _clean(row[0])
        for pdf_name, field in PDF_TO_FIELD.items():
            if name == pdf_name or name.startswith(pdf_name):
                for (title_col, restated_col, original_col), year in zip(_YEAR_COL_CONFIG, years):
                    if year is None:
                        continue
                    restated = _parse_number(row[restated_col]) if len(row) > restated_col else None
                    original = _parse_number(row[original_col]) if len(row) > original_col else None
                    if restated is not None or original is not None:
                        result.setdefault(year, {})[field] = {
                            "restated": restated,
                            "original": original,
                        }
                break
    return result


def parse_key_financials(pdf_path: str | Path) -> dict:
    """解析「主要会计数据」表最新年份，返回 {字段: 值(原始单位，重述后)}。"""
    by_year = parse_financials_by_year(pdf_path)
    if not by_year:
        return {}
    latest_year = max(by_year.keys())
    return {f: v["restated"] for f, v in by_year[latest_year].items()}


def to_yuan(value: float, field: str) -> float:
    """统一转成原始单位：金额「百万元→元」、股本「百万股→股」（均为 ×1e6）。"""
    return value * 1e6
