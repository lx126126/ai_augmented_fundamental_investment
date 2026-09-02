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
_CASES = [
    ("601088", 2025, parse_income_statement, "profit_sheet"),
    ("601088", 2025, parse_cash_flow_statement, "cash_flow"),
    ("601088", 2025, parse_balance_sheet, "balance_sheet"),
    ("600519", 2025, parse_income_statement, "profit_sheet"),
    ("600519", 2025, parse_cash_flow_statement, "cash_flow"),
    ("000651", 2025, parse_income_statement, "profit_sheet"),
    ("000651", 2025, parse_cash_flow_statement, "cash_flow"),
]


def _pdf_path(code: str, year: int) -> Path:
    return DATA_DIR / "validation" / f"{code}_{year}年报.pdf"


@pytest.mark.parametrize("code,year,parser,table", _CASES)
def test_pdf_parse_matches_api(code, year, parser, table):
    """PDF 金标准解析值必须与接口 parquet 值一致（<0.1%，容差放宽到 1% 兜底）。"""
    pdf = _pdf_path(code, year)
    if not pdf.exists():
        pytest.skip(f"无年报 PDF: {pdf.name}")

    golden = parser(pdf)
    assert golden, f"{code} {table} 解析结果为空"

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
