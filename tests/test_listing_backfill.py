"""跨上市地（A+H 同一法人）公司级字段回填的边界测试。

背景与规则见 src/data/listing_group.py 模块头部。这里只锁四条边界：
1. 未登记配对 → 不动作
2. 目标为空 → 回填，且来源可追溯
3. **目标已有值 → 绝不覆盖**（两套接口同名指标定义不同，覆盖会让同页自相矛盾）
4. 期间不一致 → 宁缺勿错，跳过并在来源映射里写明原因

⚠️ `data/` 目录不入库，所以全部用合成 DataFrame，不依赖真实 parquet。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.data.adapter import _build_graham, backfill_company_fields
from src.data.listing_group import sibling_code
from src.analysis.fraud import fraud_check


# ---------------------------------------------------------------------------
# sibling_code：配对表的双向解析
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,expect", [
    ("00883", "600938"),
    ("00883.HK", "600938"),
    ("600938", "00883"),
    ("600938.SH", "00883"),
    ("601088", None),      # 未登记配对
    ("00700", None),
])
def test_sibling_code(code, expect):
    assert sibling_code(code) == expect


# ---------------------------------------------------------------------------
# 合成年报宽表
# ---------------------------------------------------------------------------
def _annual(years, *, audit_opinion=None, interest_bearing_debt=None) -> pd.DataFrame:
    """造一份「宽表形态」的年报（列名与 build_annual_financials 输出对齐，只给用到的）。"""
    rows = []
    for y in years:
        row = {
            "report_date": pd.Timestamp(f"{y}-12-31"),
            "net_profit_parent": 100.0,
            "monetary_funds": 1000.0,
            "total_assets": 5000.0,
            "total_liabilities": 2000.0,
            "ocf": 120.0,
            "operating_revenue": 2000.0,
            "accounts_receivable": 200.0,
            "current_assets": 1500.0,
            "fixed_assets": 2500.0,
            "depreciation": 100.0,
            "sell_expense": 30.0,
            "admin_expense": 50.0,
            "gross_margin_pct": 40.0,
            "debt_ratio_pct": 40.0,
            "current_ratio": 1.5,
        }
        if audit_opinion is not None:
            row["audit_opinion"] = audit_opinion
        if interest_bearing_debt is not None:
            row["interest_bearing_debt"] = interest_bearing_debt
        rows.append(row)
    return pd.DataFrame(rows).sort_values("report_date").reset_index(drop=True)


@pytest.fixture
def target():          # 港股形态：无借款科目 → 净现金算不出；无审计意见列
    return _annual([2024, 2025])


@pytest.fixture
def sibling():         # A 股形态：借款科目与审计意见都有
    return _annual([2024, 2025], audit_opinion="标准无保留意见",
                   interest_bearing_debt=300.0)


def test_target_alone_has_no_net_cash_and_no_audit(target):
    """先确认前提：不打回填时，港股形态的目标数据确实两样都缺。"""
    assert _build_graham(target)["net_cash"] is None
    assert fraud_check(target)["audit_opinion"] is None


def test_backfill_fills_blanks_and_records_source(target, sibling):
    graham, fraud = _build_graham(target), fraud_check(target)
    src = backfill_company_fields(target, sibling, graham, fraud, "600938")

    assert graham["net_cash"] == 1000.0 - 300.0
    assert fraud["audit_opinion"] == "标准无保留意见"
    assert fraud["audit_level"] == "clean"
    assert src == {"net_cash": "600938", "audit_opinion": "600938"}


def test_backfill_never_overwrites_existing_values(target, sibling):
    """关键回归：目标已有值时必须原样保留，哪怕配对代码的值不同。

    实测 `operating_revenue` 两边差 2.3%、ROE 差 1.06pp —— 一旦按列覆盖，
    同一份报告里就会出现「一个指标两个值」。
    """
    graham = {"net_cash": 999.0}
    fraud = {"audit_opinion": "带强调事项段的无保留意见", "audit_level": "watch"}
    src = backfill_company_fields(target, sibling, graham, fraud, "600938")

    assert graham["net_cash"] == 999.0
    assert fraud["audit_opinion"] == "带强调事项段的无保留意见"
    assert fraud["audit_level"] == "watch"
    assert src == {}


def test_backfill_skips_when_periods_mismatch(target):
    """期间不一致 → 不填，且原因可追溯（宁缺勿错，不拼不同年份的数字）。"""
    stale = _annual([2022, 2023], audit_opinion="标准无保留意见",
                    interest_bearing_debt=300.0)
    graham, fraud = _build_graham(target), fraud_check(target)
    src = backfill_company_fields(target, stale, graham, fraud, "600938")

    assert graham["net_cash"] is None
    assert fraud["audit_opinion"] is None
    assert "_skipped" in src and "2023" in src["_skipped"]


def test_backfill_noop_without_sibling(target):
    graham, fraud = _build_graham(target), fraud_check(target)
    assert backfill_company_fields(target, None, graham, fraud, "600938") == {}
    assert graham["net_cash"] is None
    assert fraud["audit_opinion"] is None
