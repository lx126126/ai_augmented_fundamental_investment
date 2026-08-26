"""解析官方年报 PDF 的「主要会计数据」表，提取金标准财务指标。

用 pymupdf 的 find_tables 识别表格结构（比正则更可靠），
从「主要会计数据」表提取关键指标（营收/净利/现金流/净资产/总资产/总负债/总股本）。
"""

from __future__ import annotations

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
    "期末总股本": "total_shares",
}

# 字段单位说明：金额类原始单位是「百万元」，股本原始单位是「百万股」
_SHARE_FIELDS = {"total_shares"}


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


def parse_key_financials(pdf_path: str | Path) -> dict:
    """解析年报 PDF 的「主要会计数据」表，返回 {字段: 值(原始单位)}。

    金额字段返回「百万元」，股本返回「百万股」；未找到的字段缺省。
    """
    doc = fitz.open(str(pdf_path))
    result: dict[str, float] = {}

    for page in doc:
        tables = page.find_tables()
        for t in tables.tables:
            data = t.extract()
            if not data:
                continue
            # 找「主要会计数据」表：含「营业收入」行 且 含「期末总股本」或「资产总计」
            first_col = [_clean(r[0]) if r else "" for r in data]
            if not any("营业收入" in c for c in first_col):
                continue
            # 该表第一列为指标名，第二列为最新年度（2025年）值
            for row in data:
                if not row or not row[0]:
                    continue
                name = _clean(row[0])
                # 精确匹配指标名（PDF 里可能带括号注释，取行首核心名）
                for pdf_name, field in PDF_TO_FIELD.items():
                    if name == pdf_name or name.startswith(pdf_name):
                        val = _parse_number(row[1]) if len(row) > 1 else None
                        if val is not None and field not in result:
                            result[field] = val
                        break
    doc.close()
    return result


def to_yuan(value: float, field: str) -> float:
    """统一转成「元」（金额）或「股」（股本）的原始单位。"""
    if field in _SHARE_FIELDS:
        return value * 1e6  # 百万股 → 股
    return value * 1e6  # 百万元 → 元
