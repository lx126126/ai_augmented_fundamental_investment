"""数据适配层：把 cleaner 输出的宽表，转成模板渲染所需的结构。

模板结构（build_valueline.py 消费）：
- YEARS / FINANCIALS：年度表，FINANCIALS 每项为 (分组 或 None, 指标名, [格式化字符串])
- QUARTER_LABELS / QUARTERLY：季度表，同上
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .cleaner import build_annual_financials, build_quarter_financials, build_segments


# 业务条线颜色（与模板 SEGMENTS 一致）
SEGMENT_COLORS = {
    "煤炭": "#378ADD",
    "发电": "#E24B4A",
    "运输": "#BA7517",
    "煤化工": "#888780",
    "其他": "#6b7280",
}


# ---------------------------------------------------------------------------
# 字段映射：(分组, 模板指标名, 宽表字段名, 小数位)
# ---------------------------------------------------------------------------
ANNUAL_SPEC = [
    ("利润表", None, None, None),
    (None, "营业收入（亿元）", "operating_revenue", 1),
    (None, "归母净利润（亿元）", "net_profit_parent", 1),
    (None, "毛利率 %", "gross_margin_pct", 1),
    (None, "净利率 %", "net_margin_pct", 1),
    (None, "经营现金流净额（亿元）", "ocf", 1),
    (None, "ROE（摊薄）%", "roe_pct", 1),
    ("资产负债表", None, None, None),
    (None, "总资产（亿元）", "total_assets", 1),
    (None, "总负债（亿元）", "total_liabilities", 1),
    (None, "净资产（归母）（亿元）", "total_equity", 1),
    (None, "货币资金（亿元）", "monetary_funds", 1),
    (None, "存货（亿元）", "inventory", 1),
    (None, "应收账款（亿元）", "accounts_receivable", 1),
    (None, "有息负债（亿元）", "interest_bearing_debt", 1),
    (None, "商誉（亿元）", "goodwill", 1),
    ("股本结构", None, None, None),
    (None, "普通股数量（亿股）", "total_shares_yi", 2),
    (None, "优先股数量（亿股）", "preferred_shares_yi", 2),
    ("股东回报", None, None, None),
    (None, "分红比例 %", "dividend_payout_pct", 1),
    (None, "股息率 %", "dividend_yield_pct", 1),
]

QUARTER_SPEC = [
    ("利润表（单季）", None, None, None),
    (None, "营业收入（亿元）", "operating_revenue", 1),
    (None, "归母净利润（亿元）", "net_profit_parent", 1),
    (None, "毛利率 %", "gross_margin_pct", 1),
    (None, "净利率 %", "net_margin_pct", 1),
    (None, "经营现金流净额（亿元）", "ocf", 1),
    (None, "ROE（单季）%", "roe_pct", 1),
    ("资产负债表（季末）", None, None, None),
    (None, "总资产（亿元）", "total_assets", 1),
    (None, "总负债（亿元）", "total_liabilities", 1),
    (None, "净资产（归母）（亿元）", "total_equity", 1),
    (None, "货币资金（亿元）", "monetary_funds", 1),
    (None, "存货（亿元）", "inventory", 1),
    (None, "应收账款（亿元）", "accounts_receivable", 1),
    (None, "有息负债（亿元）", "interest_bearing_debt", 1),
    (None, "商誉（亿元）", "goodwill", 1),
    ("股本结构（季末）", None, None, None),
    (None, "普通股数量（亿股）", "total_shares_yi", 2),
    (None, "优先股数量（亿股）", "preferred_shares_yi", 2),
]


def _clean(v):
    """NaN / inf → None。"""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _fmt(v, digits=1) -> str:
    """数值 → 展示字符串（None → —）。"""
    if v is None:
        return "—"
    return f"{float(v):.{digits}f}"


def _extract(df: pd.DataFrame, spec) -> list[tuple]:
    """按 spec 从宽表提取模板结构（值已格式化为字符串）。"""
    rows = []
    for group, name, field, digits in spec:
        if group:
            rows.append((group, None, None))
            continue
        if field in df.columns:
            vals = [_fmt(_clean(v), digits) for v in df[field].tolist()]
        else:
            vals = ["—"] * len(df)
        rows.append((None, name, vals))
    return rows


def _q_label(dt) -> str:
    """report_date → 季度标签，如 2024-09-30 → 24Q3。"""
    y = str(dt.year)[2:]
    q = (dt.month - 1) // 3 + 1
    return f"{y}Q{q}"


def load_raw(code: str) -> dict[str, pd.DataFrame]:
    """读 parquet 原始数据（含分业务构成，共 6 张表）。"""
    d = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / code
    tables = ["financial_indicator", "profit_sheet", "balance_sheet", "cash_flow", "dividend", "segments"]
    out = {}
    for t in tables:
        p = d / f"{t}.parquet"
        if p.exists():
            out[t] = pd.read_parquet(p)
    return out


def build_template_data(code: str) -> dict:
    """读 parquet → 输出模板所需的全部数据结构。

    返回 keys:
      years, financials, quarter_labels, quarterly
    """
    raw = load_raw(code)
    required = {"financial_indicator", "profit_sheet", "balance_sheet", "cash_flow"}
    if not required.issubset(raw.keys()):
        missing = required - raw.keys()
        raise FileNotFoundError(f"{code} 缺 parquet 表: {missing}，请先运行 scripts/fetch_stock.py {code}")

    annual = build_annual_financials(raw)
    quarter = build_quarter_financials(raw)

    # 股本转亿股 + 优先股默认 0
    latest_shares = None
    if "total_shares" in annual.columns:
        latest_shares = annual["total_shares"].iloc[-1]

    annual = annual.copy()
    if "total_shares" in annual.columns:
        annual["total_shares_yi"] = annual["total_shares"] / 1e8
    annual["preferred_shares_yi"] = 0.0  # A 股极少发优先股，默认 0

    quarter = quarter.copy()
    if latest_shares is not None:
        quarter["total_shares_yi"] = latest_shares / 1e8  # 股本变动不频繁，用最新值近似
    quarter["preferred_shares_yi"] = 0.0

    years = [d.year for d in annual["report_date"].tolist()]
    financials = _extract(annual, ANNUAL_SPEC)

    quarter_labels = [_q_label(d) for d in quarter["report_date"].tolist()]
    quarterly = _extract(quarter, QUARTER_SPEC)

    # 报告期 = 最新季度，如 2026Q1（用于 reports/ 归档目录）
    latest_q = quarter["report_date"].iloc[-1]
    report_period = f"{latest_q.year}Q{(latest_q.month - 1) // 3 + 1}"

    # 分业务收入构成（半年度，可选）
    segment_labels = None
    segments = None
    if "segments" in raw:
        segment_labels, seg_result = build_segments(raw["segments"])
        segments = [
            (name, SEGMENT_COLORS.get(name, "#6b7280"),
             [_clean(v) for v in revs], [_clean(v) for v in margins])
            for name, revs, margins in seg_result
        ]

    return {
        "years": years,
        "financials": financials,
        "quarter_labels": quarter_labels,
        "quarterly": quarterly,
        "report_period": report_period,
        "segment_labels": segment_labels,
        "segments": segments,
    }
