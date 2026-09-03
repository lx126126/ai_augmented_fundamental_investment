"""数据质量校验层：抓取结果的通用断言，供 Airflow 管道 gate 复用。

这是「生产级数据管道」与「能跑的脚本」的分水岭——在 fetcher 与 cleaner 之间
插入一道质量 gate，对每一张表做结构/空值/数值/一致性断言，失败即阻断下游，
保证「脏数据不出数仓、不出报告」。

设计原则：
- 纯函数式、零依赖额外服务，输入 DataFrame 输出 CheckResult；
- 每条规则可独立开关，方便按标的/表类型定制；
- 与 src/analysis/fraud.py（造假检测）正交：fraud 查「财务是否造假」，
  本模块查「数据是否完整、数值是否合理」，二者共同构成质量门禁。

用法：
    from src.data.quality import check_frame, CheckResult
    res = check_frame(df, table="profit_sheet", min_rows=5)
    if not res.ok:
        raise ValueError(res.summary())   # 在 Airflow 中触发 on_failure_callback 告警
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd


@dataclass
class CheckResult:
    """单表质量校验结果。"""

    table: str
    checks: list[dict] = field(default_factory=list)
    passed: int = 0
    failed: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if ok:
            self.passed += 1
        else:
            self.failed += 1

    def summary(self) -> str:
        lines = [f"[quality:{self.table}] {self.passed} 通过 / {self.failed} 失败"]
        for c in self.checks:
            if not c["ok"]:
                lines.append(f"  ✗ {c['check']}: {c['detail']}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 通用断言（可复用）
# --------------------------------------------------------------------------- #
def check_frame(
    df: pd.DataFrame,
    table: str,
    min_rows: int = 1,
    required_cols: Iterable[str] = (),
    positive_cols: Iterable[str] = (),
    nonnull_cols: Iterable[str] = (),
    max_null_ratio: float = 0.2,
) -> CheckResult:
    """对单张 DataFrame 做一组标准质量断言，返回 CheckResult。

    Args:
        df: 待校验表。
        table: 表名（仅用于日志）。
        min_rows: 最低行数（防接口返回空表，如带后缀 code 导致东财空表的历史 bug）。
        required_cols: 必须存在的列。
        positive_cols: 必须恒为正数（且无 NaN）的列，如营收/净利/净资产。
        nonnull_cols: 非空率必须 ≥ (1 - max_null_ratio) 的列。
        max_null_ratio: nonnull_cols 允许的最大空值比例（默认 20%）。
    """
    res = CheckResult(table=table)

    # 1) 非空 + 行数下限
    if df is None or df.empty:
        res.add("non_empty", False, "DataFrame 为空")
        return res
    res.add("non_empty", True, f"{len(df)} 行")
    if len(df) < min_rows:
        res.add("min_rows", False, f"仅 {len(df)} 行，低于门槛 {min_rows}")
    else:
        res.add("min_rows", True, f"{len(df)} 行 ≥ {min_rows}")

    # 2) 必需列存在
    for col in required_cols:
        res.add(f"col:{col}", col in df.columns, "存在" if col in df.columns else "缺失")

    # 3) 正数列：无 NaN 且 > 0
    for col in positive_cols:
        if col not in df.columns:
            res.add(f"positive:{col}", False, "列缺失，无法校验正数")
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        bad = s.isna().sum() + (s <= 0).sum()
        if bad > 0:
            res.add(f"positive:{col}", False, f"{bad} 行非正数或空值")
        else:
            res.add(f"positive:{col}", True, "全部为正")

    # 4) 非空率
    for col in nonnull_cols:
        if col not in df.columns:
            res.add(f"nonnull:{col}", False, "列缺失")
            continue
        ratio = df[col].isna().mean()
        if ratio > max_null_ratio:
            res.add(f"nonnull:{col}", False, f"空值率 {ratio:.1%} > {max_null_ratio:.0%}")
        else:
            res.add(f"nonnull:{col}", True, f"空值率 {ratio:.1%}")

    return res


# --------------------------------------------------------------------------- #
# 财务三表专用校验（按表定制 required/positive/nonnull 列）
# --------------------------------------------------------------------------- #
# 字段名与 src/data/adapter.py load_raw() 输出的标准列对齐（英文列名）
_BALANCE_REQUIRED = ["report_date", "total_assets", "total_liabilities", "total_equity"]
_BALANCE_POSITIVE = ["total_assets", "total_liabilities", "total_equity"]

_CASHFLOW_REQUIRED = ["report_date", "ocf"]


def check_financial_tables(raw: dict[str, pd.DataFrame]) -> list[CheckResult]:
    """对 fetch 产出的财务三表（+ 财务指标）批量做质量校验。

    Args:
        raw: adapter.load_raw() 返回的 dict，键为表名（financial_indicator /
             profit_sheet / balance_sheet / cash_flow），值为 DataFrame。

    Returns:
        每个表的 CheckResult 列表；调用方检查每个 .ok 决定是否阻断。
    """
    results: list[CheckResult] = []

    # 资产负债表：总资产 > 0，且资产=负债+权益（会计恒等式，容差 1%）
    if "balance_sheet" in raw:
        df = raw["balance_sheet"]
        r = check_frame(
            df, "balance_sheet", min_rows=5,
            required_cols=_BALANCE_REQUIRED, positive_cols=_BALANCE_POSITIVE,
        )
        # 会计勾稽：|总资产 - (总负债 + 全部股东权益)| / 总资产 < 1%
        # 注意：必须用「全部股东权益」（含少数股东）total_equity_all，
        #       不能用归母权益 total_equity——否则少数股东权益会漏算，虚增「偏差」。
        equity_col = "total_equity_all" if "total_equity_all" in df.columns else "total_equity"
        if all(c in df.columns for c in ("total_assets", "total_liabilities")) and equity_col in df.columns:
            a = pd.to_numeric(df["total_assets"], errors="coerce")
            l = pd.to_numeric(df["total_liabilities"], errors="coerce")
            e = pd.to_numeric(df[equity_col], errors="coerce")
            diff = (a - (l + e)).abs() / a.abs()
            worst = diff.max()
            r.add("balance_identity", worst < 0.01, f"最大偏差 {worst:.2%}（用 {equity_col}）")
        results.append(r)

    # 利润表：营业收入 > 0，净利润列存在
    # 注意：银行等金融机构利润表可能无 revenue 列，用 operating_revenue（营业收入）兜底。
    if "profit_sheet" in raw:
        pdf = raw["profit_sheet"]
        rev_col = "revenue" if "revenue" in pdf.columns else "operating_revenue"
        r = check_frame(
            pdf, "profit_sheet", min_rows=5,
            required_cols=["report_date", rev_col, "net_profit"],
            positive_cols=[rev_col],
        )
        results.append(r)

    # 现金流量表：经营活动现金流列存在
    if "cash_flow" in raw:
        r = check_frame(
            raw["cash_flow"], "cash_flow", min_rows=5,
            required_cols=_CASHFLOW_REQUIRED,
            nonnull_cols=_CASHFLOW_REQUIRED[1:],
        )
        results.append(r)

    # 财务指标：ROE/毛利率等比率列存在即可（不强求正数，ROE 可为负）
    if "financial_indicator" in raw:
        r = check_frame(
            raw["financial_indicator"], "financial_indicator", min_rows=5,
            required_cols=["report_date"],
        )
        results.append(r)

    return results


def validate_all(raw: dict[str, pd.DataFrame]) -> CheckResult:
    """聚合多表校验：任一表失败即整体失败，返回合并的 CheckResult。"""
    merged = CheckResult(table="all")
    for r in check_financial_tables(raw):
        merged.checks.extend(r.checks)
        merged.passed += r.passed
        merged.failed += r.failed
    return merged
