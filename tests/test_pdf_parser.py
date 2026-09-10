# -*- coding: utf-8 -*-
"""集成测试：PDF 金标准解析（利润表 / 现金流量表 / 资产负债表）。

用已下载的官方年报 PDF（data/validation/）验证三表解析正确性。
PDF 属于数据资产（.gitignore 排除），测试在 PDF 缺失时自动跳过。
"""
from pathlib import Path

import pandas as pd
import pytest

from src.validation.pdf_parser import (
    parse_balance_sheet,
    parse_cash_flow_statement,
    parse_income_statement,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# (code, 年报年份, 表, parser, parquet 表名)
# 用「主要会计数据」已覆盖的字段做交叉印证：PDF 解析值必须与接口值一致（<0.1%）
# ⚠️ 重述年例外：同一控制下企业合并+追溯重述（如神华 2025 收购杭锦能源）会导致
#   接口值系统性超差（总资产 9038 亿 vs 官方 6277 亿），此时「PDF==接口」不成立，
#   改用硬编码官方值（_GOLDEN_VALUES）验证解析器正确性。
_CASES = [
    ("601088", 2025, parse_income_statement, "profit_sheet"),
    ("601088", 2025, parse_cash_flow_statement, "cash_flow"),
    ("601088", 2025, parse_balance_sheet, "balance_sheet"),
    ("600519", 2025, parse_income_statement, "profit_sheet"),
    ("600519", 2025, parse_cash_flow_statement, "cash_flow"),
    ("000651", 2025, parse_income_statement, "profit_sheet"),
    ("000651", 2025, parse_cash_flow_statement, "cash_flow"),
]

# 重述年的官方金标准值（来自巨潮官方年报，人肉确认，单位：元）
# 仅用于接口值系统性超差时验证「PDF 解析器解析出的值 == 官方正确值」。
_GOLDEN_VALUES = {
    ("601088", 2025, "balance_sheet"): {
        "total_assets": 627761000000.0,       # 总资产 6277.61 亿
        "total_liabilities": 146310000000.0,  # 总负债 1463.10 亿
        "total_equity_all": 481451000000.0,   # 股东权益合计 4814.51 亿
        "short_term_loan": 409000000.0,       # 短期借款 4.09 亿
    },
}


def _pdf_path(code: str, year: int) -> Path:
    return DATA_DIR / "validation" / f"{code}_{year}年报.pdf"


@pytest.mark.parametrize("code,year,parser,table", _CASES)
def test_pdf_parse_matches_api(code, year, parser, table):
    """PDF 金标准解析值必须与接口 parquet 值一致（<0.1%，容差放宽到 1% 兜底）。

    重述年例外：接口值系统性超差时（同一控制下合并追溯重述），改用硬编码官方值
    验证解析器正确性——此时失败不代表解析错，而是接口错（校验体系要抓的正是这个）。
    """
    pdf = _pdf_path(code, year)
    if not pdf.exists():
        pytest.skip(f"无年报 PDF: {pdf.name}")

    golden = parser(pdf)
    assert golden, f"{code} {table} 解析结果为空"

    # 重述年：用硬编码官方值验证解析器，不依赖（已被污染的）接口值
    golden_ref = _GOLDEN_VALUES.get((code, year, table))
    if golden_ref:
        mismatched = []
        for field, expected in golden_ref.items():
            pdf_val = golden.get(field)
            if pdf_val is None:
                mismatched.append((field, "缺失", expected / 1e8, None))
                continue
            diff = abs(pdf_val - expected) / expected * 100 if expected else 0
            if diff > 1.0:
                mismatched.append((field, pdf_val / 1e8, expected / 1e8, diff))
        assert not mismatched, f"{code} {year} {table} 解析与官方值不一致: {mismatched}"
        return

    parquet = DATA_DIR / "raw" / code / f"{table}.parquet"
    if not parquet.exists():
        pytest.skip(f"无接口数据: {parquet.name}")

    df = pd.read_parquet(parquet)
    d = pd.to_datetime(df["report_date"])
    mask = (d.dt.year == year) & (d.dt.month == 12)
    if not mask.any():
        pytest.skip(f"无 {year} 年报行")
    row = df[mask].iloc[0]

    mismatched = []
    for field, pdf_val in golden.items():
        if field not in df.columns or pdf_val is None:
            continue
        api_val = row[field]
        if pd.isna(api_val):
            continue
        if pdf_val == 0 and api_val == 0:
            continue
        diff = abs(api_val - pdf_val) / abs(pdf_val) * 100 if pdf_val else 0
        if diff > 1.0:  # 容差 1%（金标准本应 <0.1%，放宽防四舍五入误判）
            mismatched.append((field, api_val / 1e8, pdf_val / 1e8, diff))

    assert not mismatched, f"{code} {year} {table} 解析与接口不一致: {mismatched}"


def test_income_statement_extracts_core_fields():
    """利润表应提取核心字段（营业收入/营业成本/净利润等），而非仅费用类。"""
    pdf = _pdf_path("601088", 2025)
    if not pdf.exists():
        pytest.skip("无神华 2025 年报 PDF")
    golden = parse_income_statement(pdf)
    for field in ("operating_revenue", "operating_cost", "net_profit_parent",
                  "total_profit", "income_tax"):
        assert field in golden, f"利润表缺核心字段 {field}"


def test_cash_flow_extracts_ocf_and_capex():
    """现金流量表应提取经营现金流净额 + 资本开支。"""
    pdf = _pdf_path("601088", 2025)
    if not pdf.exists():
        pytest.skip("无神华 2025 年报 PDF")
    golden = parse_cash_flow_statement(pdf)
    assert "ocf" in golden
    assert "capital_expenditure" in golden
    # 资本开支是「支出金额」，接口口径为正数，PDF 解析须取绝对值
    assert golden["capital_expenditure"] > 0


# ---------------------------------------------------------------------------
# 单位识别（_detect_unit）：银行年报用括号夹注「（除另有标明外，人民币百万元）」，
# 页面里没有「单位」二字；且「百万元」含「万元」，贪婪匹配会先命中「万元」→ 差 100 倍。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("单位：人民币百万元", "百万元"),
    ("金额单位: 人民币百万元", "百万元"),
    ("（除另有标明外，人民币百万元）", "百万元"),   # 交行 601328 年报实际写法
    ("(人民币百万元)", "百万元"),
    ("人民币百万元", "百万元"),
    ("单位：万元", "万元"),
    ("（人民币元）", "元"),
    ("单位：元", "元"),
    ("无任何单位标注", "元"),
])
def test_detect_unit(text, expected):
    from src.validation.pdf_parser import _detect_unit
    assert _detect_unit(text) == expected


def test_bank_annual_report_parsed_in_millions():
    """交行 2025 年报：单位「百万元」，解析值必须与接口值同量级（亿元口径）。"""
    pdf = DATA_DIR / "validation" / "601328_2025年报.pdf"
    if not pdf.exists():
        pytest.skip("缺交行年报 PDF（数据资产，.gitignore 排除）")
    from src.validation.pdf_parser import parse_key_financials
    g = parse_key_financials(pdf)
    assert g.get("operating_revenue"), "未解析出营业收入"
    # 265,071 百万元 = 2650.71 亿元（若单位误判为万元则只有 26.5 亿）
    assert g["operating_revenue"] == pytest.approx(2650.71e8, rel=0.01)
    assert g["total_assets"] == pytest.approx(155483.88e8, rel=0.01)


# ---------------------------------------------------------------------------
# 单位识别（_detect_unit）：银行年报用括号夹注「（除另有标明外，人民币百万元）」，
# 页面里没有「单位」二字；且「百万元」含「万元」，贪婪匹配会先命中「万元」→ 差 100 倍。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("单位：人民币百万元", "百万元"),
    ("金额单位: 人民币百万元", "百万元"),
    ("（除另有标明外，人民币百万元）", "百万元"),   # 交行 601328 年报实际写法
    ("(人民币百万元)", "百万元"),
    ("人民币百万元", "百万元"),
    ("单位：万元", "万元"),
    ("（人民币元）", "元"),
    ("单位：元", "元"),
    ("无任何单位标注", "元"),
])
def test_detect_unit(text, expected):
    from src.validation.pdf_parser import _detect_unit
    assert _detect_unit(text) == expected


def test_bank_annual_report_parsed_in_millions():
    """交行 2025 年报：单位「百万元」，解析值必须与接口值同量级（亿元口径）。"""
    pdf = DATA_DIR / "validation" / "601328_2025年报.pdf"
    if not pdf.exists():
        pytest.skip("缺交行年报 PDF（数据资产，.gitignore 排除）")
    from src.validation.pdf_parser import parse_key_financials
    g = parse_key_financials(pdf)
    assert g.get("operating_revenue"), "未解析出营业收入"
    # 265,071 百万元 = 2650.71 亿元（若单位误判为万元则只有 26.5 亿）
    assert g["operating_revenue"] == pytest.approx(2650.71e8, rel=0.01)
    assert g["total_assets"] == pytest.approx(155483.88e8, rel=0.01)
