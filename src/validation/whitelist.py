"""字段白名单：已验证通过（官方年报 PDF 金标准）的字段，及其来源与验证状态。

白名单的作用（见 docs/data-validation.md）：
1. 记录「信任基线」——哪些字段已用官方披露验过、可放心用
2. 日常只监控白名单字段的「新数据 + 字段映射变更」
3. 字段映射变更检测：若东财接口改名/改口径，白名单字段会比对失败，触发告警
"""

from __future__ import annotations

# 已验证通过的字段（2026-08-26，神华 2025 年报，容差 <0.1%）
WHITELIST: dict[str, dict] = {
    "operating_revenue": {
        "label": "营业收入",
        "source": "东财利润表 OPERATE_INCOME",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
    "net_profit_parent": {
        "label": "归母净利润",
        "source": "东财利润表 PARENT_NETPROFIT",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
    "ocf": {
        "label": "经营现金流净额",
        "source": "东财现金流表 NETCASH_OPERATE",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
    "total_assets": {
        "label": "总资产",
        "source": "东财资产负债表 TOTAL_ASSETS",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
    "total_liabilities": {
        "label": "总负债",
        "source": "东财资产负债表 TOTAL_LIABILITIES",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
    "total_equity": {
        "label": "归母净资产",
        "source": "东财资产负债表 TOTAL_PARENT_EQUITY",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
    "share_capital": {
        "label": "总股本",
        "source": "东财资产负债表 SHARE_CAPITAL",
        "verified_on": "2026-08-26",
        "status": "passed",
    },
}

# 弃用字段（已知数据源 bug，勿再使用）
DEPRECATED: dict[str, dict] = {
    "total_shares": {
        "label": "总股本（分红接口）",
        "reason": "stock_fhps_detail_em 的「总股本」字段 2025 年报跳涨到 216.89 亿股（官方 198.69），数据源错误",
        "replaced_by": "share_capital",
        "deprecated_on": "2026-08-26",
    },
}


def passed_fields() -> list[str]:
    """返回已验证通过的字段名列表。"""
    return [f for f, meta in WHITELIST.items() if meta.get("status") == "passed"]
