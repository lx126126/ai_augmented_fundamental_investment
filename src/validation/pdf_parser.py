"""解析官方年报 PDF 的「主要会计数据」表，提取金标准财务指标。

用 pymupdf 的 find_tables 识别表格结构（比正则更可靠）。
「主要会计数据」表含近三年对比（重述后/重述前），支持提取多年份。
字段名、单位因公司而异，故用别名表 + 单位识别通用化。
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # pymupdf

# 标准字段 → 年报里的字段名别名（不同公司披露口径不同）
PDF_FIELD_ALIASES = {
    "operating_revenue": ["营业收入", "营业总收入"],
    "net_profit_parent": [
        "归属于本公司股东的净利润", "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润", "归属于母公司所有者的净利润",
    ],
    "ocf": ["经营活动产生的现金流量净额", "经营活动现金流量净额"],
    "total_equity": [
        "归属于本公司股东的净资产", "归属于上市公司股东的净资产",
        "归属于母公司股东的净资产", "归属于母公司所有者权益",
    ],
    "total_assets": ["资产总计", "总资产", "资产总额"],
    "total_liabilities": ["负债合计", "负债总计", "负债总额"],
    "share_capital": ["期末总股本", "总股本", "股本"],
}

# 「主要会计数据」表列结构因公司而异（有无重述、列数不同），故动态从表头识别

# 单位 → 换算到「元」的乘数
_UNIT_MULTIPLIER = {
    "元": 1,
    "万元": 1e4,
    "百万元": 1e6,
    "亿元": 1e8,
}


def _clean(s: str) -> str:
    """去空白与换行，用于字段名匹配。"""
    return str(s).replace("\n", "").replace(" ", "").replace("\u3000", "")


def _parse_number(s: str) -> float | None:
    """解析数字（含千分位），如 '294,916' → 294916.0。"""
    s = _clean(s).replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _match_field(name: str) -> str | None:
    """字段名 → 标准字段，精确匹配别名（自动去掉「（元）」「（百万元）」等括号后缀）。"""
    name = _clean(name)
    name = re.sub(r"[（(][^）)]*[）)]", "", name)  # 去「（元）」等后缀
    for field, aliases in PDF_FIELD_ALIASES.items():
        if name in aliases:
            return field
    return None


def _detect_unit(page_text: str) -> str:
    """从页面文本识别金额单位，默认「元」。"""
    for unit in ("百万元", "万元", "亿元", "元"):
        if f"单位：{unit}" in page_text or f"单位: {unit}" in page_text:
            return unit
    return "元"


def _find_main_table(doc) -> tuple[list, str] | None:
    """定位「主要会计数据」表（含「营业收入」行），返回 (表格数据, 单位)。"""
    for page in doc:
        page_text = page.get_text()
        if "主要会计数据" not in page_text:
            continue
        for t in page.find_tables().tables:
            data = t.extract()
            if not data:
                continue
            # 表格任意单元格含「营业收入」即认为是「主要会计数据」表
            flat = [_clean(str(c)) for row in data for c in row]
            if any("营业收入" in c for c in flat):
                return data, _detect_unit(page_text)
    return None


def _extract_year_cols(table: list) -> dict[int, dict]:
    """动态识别「主要会计数据」表的年份列，返回 {年份: {"restated": 列, "original": 列}}。

    列结构因公司而异：有重述时前两年分「重述后/重述前」两列（相邻），无重述时每年一列。
    """
    header = table[0]
    year_cols: dict[int, int] = {}
    for ci, cell in enumerate(header):
        m = re.search(r"(20\d{2})", _clean(str(cell)))
        if m:
            year = int(m.group(1))
            if year not in year_cols:
                year_cols[year] = ci

    # 判断是否有重述（表头第二行含「重述前」）
    has_restate = len(table) > 1 and any("重述前" in _clean(str(c)) for c in table[1])
    latest_year = max(year_cols.keys()) if year_cols else None

    result: dict[int, dict] = {}
    for year, ci in sorted(year_cols.items()):
        if has_restate and year != latest_year:
            result[year] = {"restated": ci, "original": ci + 1}
        else:
            result[year] = {"restated": ci, "original": ci}
    return result


def parse_financials_by_year(pdf_path: str | Path) -> dict[int, dict[str, dict]]:
    """解析「主要会计数据」表，返回 {年份: {字段: {"restated": 元, "original": 元}}}。

    覆盖近三年；字段名/单位/列结构按公司自适应。所有金额已统一换算到「元」。
    """
    doc = fitz.open(str(pdf_path))
    found = _find_main_table(doc)
    doc.close()
    if not found:
        return {}
    table, unit = found
    mult = _UNIT_MULTIPLIER.get(unit, 1)

    year_cols = _extract_year_cols(table)
    result: dict[int, dict[str, dict]] = {}

    for row in table:
        # 指标名可能不在第一列（如格力列0为空边框），扫描整行找能匹配字段的单元格
        field = None
        for cell in row:
            f = _match_field(_clean(str(cell)))
            if f:
                field = f
                break
        if field is None:
            continue
        for year, cols in year_cols.items():
            restated = _parse_number(row[cols["restated"]]) if len(row) > cols["restated"] else None
            original = _parse_number(row[cols["original"]]) if len(row) > cols["original"] else None
            if restated is not None:
                restated *= mult
            if original is not None:
                original *= mult
            if restated is not None or original is not None:
                result.setdefault(year, {})[field] = {
                    "restated": restated,
                    "original": original,
                }
    return result


def parse_key_financials(pdf_path: str | Path) -> dict:
    """解析「主要会计数据」表最新年份，返回 {字段: 值(元，重述后)}。"""
    by_year = parse_financials_by_year(pdf_path)
    if by_year:
        latest_year = max(by_year.keys())
        return {f: v["restated"] for f, v in by_year[latest_year].items()}
    # find_tables 失败时，用文本正则兜底（只提取最新年）
    return _parse_by_text(pdf_path)


def _parse_by_text(pdf_path: str | Path) -> dict:
    """文本正则兜底：find_tables 对不规则表格（跨行单元格/列错位）失效时，
    从「主要会计数据」页纯文本提取最新年关键指标。

    字段名容忍跨行空格，返回 {字段: 值(元)}。
    """
    doc = fitz.open(str(pdf_path))
    page_text = ""
    for page in doc:
        txt = page.get_text()
        if "主要会计数据" in txt:
            page_text = txt
            break
    doc.close()
    if not page_text:
        return {}

    unit = _detect_unit(page_text)
    mult = _UNIT_MULTIPLIER.get(unit, 1)
    flat = page_text.replace("\n", " ").replace("\u3000", " ")

    # (字段, 正则)：字段名容忍跨行，后跟「（单位）」和最新年数值
    num = r"(\d[\d,]*\.?\d*)"
    unit_suffix = r"(?:[（(][^）)]*[）)])?"  # 全角/半角括号
    patterns = [
        ("operating_revenue", r"营业收入" + unit_suffix + r"\s*" + num),
        ("net_profit_parent", r"归属于[^0-9]{0,12}?的?净利润" + unit_suffix + r"\s*" + num),
        ("ocf", r"经营活动[^0-9]{0,15}?现金\s*流量净额" + unit_suffix + r"\s*" + num),
        ("total_equity", r"归属于[^0-9]{0,12}?的?净资产" + unit_suffix + r"\s*" + num),
        ("total_assets", r"总资产" + unit_suffix + r"\s*" + num),
        ("total_liabilities", r"负债合?计" + unit_suffix + r"\s*" + num),
        ("share_capital", r"总股本" + unit_suffix + r"\s*" + num),
    ]
    result: dict[str, float] = {}
    for field, pat in patterns:
        m = re.search(pat, flat)
        if m:
            try:
                result[field] = float(m.group(1).replace(",", "")) * mult
            except ValueError:
                continue
    return result
