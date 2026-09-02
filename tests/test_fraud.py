# -*- coding: utf-8 -*-
"""单元测试：fraud 财务造假检测（M-Score 边界 / 审计意见分级 / 现金流背离）。"""
import pandas as pd

from src.analysis.fraud import (
    MSCORE_THRESHOLD,
    _audit_risk,
    compute_mscore,
    fraud_check,
)


def _annual(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_audit_risk_clean():
    r = _audit_risk("标准无保留意见")
    assert r["level"] == "clean"


def test_audit_risk_watch():
    """带强调事项段的无保留意见 → watch。"""
    r = _audit_risk("带强调事项段的无保留意见")
    assert r["level"] == "watch"


def test_audit_risk_high_non_standard():
    """保留 / 无法表示 / 否定 → high。"""
    for op in ("保留意见", "无法表示意见", "否定意见"):
        assert _audit_risk(op)["level"] == "high"


def test_audit_risk_none():
    assert _audit_risk(None)["level"] is None
    assert _audit_risk(float("nan"))["level"] is None
    assert _audit_risk("")["level"] is None


def test_mscore_requires_two_years():
    """不足两年年报 → 返回 None。"""
    assert compute_mscore(_annual([{"operating_revenue": 1.0}])) is None


def test_fraud_check_non_standard_audit_veto():
    """非标审计意见一票否决 → 高风险（即使其他指标正常）。"""
    annual = _annual([
        {
            "report_date": pd.Timestamp("2023-12-31"),
            "operating_revenue": 100.0, "net_profit": 20.0, "net_profit_parent": 18.0,
            "accounts_receivable": 10.0, "gross_margin_pct": 30.0,
            "current_assets": 50.0, "fixed_assets": 30.0, "total_assets": 100.0,
            "depreciation": 5.0, "sell_expense": 5.0, "admin_expense": 5.0,
            "ocf": 25.0, "total_liabilities": 40.0, "audit_opinion": "标准无保留意见",
        },
        {
            "report_date": pd.Timestamp("2024-12-31"),
            "operating_revenue": 110.0, "net_profit": 22.0, "net_profit_parent": 20.0,
            "accounts_receivable": 11.0, "gross_margin_pct": 31.0,
            "current_assets": 55.0, "fixed_assets": 33.0, "total_assets": 110.0,
            "depreciation": 5.5, "sell_expense": 5.5, "admin_expense": 5.5,
            "ocf": 28.0, "total_liabilities": 44.0, "audit_opinion": "保留意见",
        },
    ])
    result = fraud_check(annual)
    assert result["audit_level"] == "high"
    assert result["overall_risk"] == "high"
    assert "非标审计意见" in result["flags"]


def test_fraud_check_clean_company_low_risk():
    """正常公司（标准无保留 + 现金流健康）→ 低风险。"""
    annual = _annual([
        {
            "report_date": pd.Timestamp("2023-12-31"),
            "operating_revenue": 100.0, "net_profit": 20.0, "net_profit_parent": 18.0,
            "accounts_receivable": 10.0, "gross_margin_pct": 30.0,
            "current_assets": 50.0, "fixed_assets": 30.0, "total_assets": 100.0,
            "depreciation": 5.0, "sell_expense": 5.0, "admin_expense": 5.0,
            "ocf": 25.0, "total_liabilities": 40.0, "audit_opinion": "标准无保留意见",
        },
        {
            "report_date": pd.Timestamp("2024-12-31"),
            "operating_revenue": 110.0, "net_profit": 22.0, "net_profit_parent": 20.0,
            "accounts_receivable": 11.0, "gross_margin_pct": 31.0,
            "current_assets": 55.0, "fixed_assets": 33.0, "total_assets": 110.0,
            "depreciation": 5.5, "sell_expense": 5.5, "admin_expense": 5.5,
            "ocf": 28.0, "total_liabilities": 44.0, "audit_opinion": "标准无保留意见",
        },
    ])
    result = fraud_check(annual)
    assert result["audit_level"] == "clean"
    # 现金流背离：ocf/净利润 > 0.5，无背离
    assert result["cashflow"]["warning"] is False


def test_mscore_threshold_constant():
    assert MSCORE_THRESHOLD == -1.78
