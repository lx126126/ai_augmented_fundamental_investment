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


# --------------------------------------------------------------------------- #
# 业务逻辑体检（勾稽关系 + 异常比率）
# --------------------------------------------------------------------------- #
# check_financial_tables 只做「结构/空值/正负」断言，查不出「数字看着就不对」的问题
# （如分红比例 300%、资产≠负债+权益）。这一层补的是会计勾稽与常识边界，
# 定位为「告警」而非阻断——异常也可能是真实的（如亏损年份净利率为负）。

_IDENTITY_TOL_PCT = 0.5      # 资产 = 负债 + 权益 容差
_PROFIT_TOL_PCT = 1.0        # 净利润 = 归母 + 少数股东损益 容差
# 营收/净利同比绝对值上限。定 300%：高成长股真实增速可达 2~3 倍（泡泡玛特 2024 净利 +189%），
# 设太低会天天误报；单位/口径错误通常是整百整万倍（100 倍 = 10000%），300% 仍足以拦下。
_MAX_ABS_YOY_PCT = 300.0

# 低基数豁免：去年同期 < 序列峰值 _LOW_BASE_RATIO 时不判同比异常
# （腾讯 2002 营收 +436%、泡泡玛特 2018 净利 +6243% 都是小基数起飞，非数据错误）。
# ⚠️ 与 _MAX_ABS_YOY_PCT **必须自洽**：同比 >X% 意味着基数 < 峰值的 1/(1+X/100)，
# 若 _LOW_BASE_RATIO ≥ 该值，则所有超阈值的同比都被豁免，检查形同虚设。
# 当前 1/(1+300/100) = 25% > 15% ✓ 检查有效。
_LOW_BASE_RATIO = 0.15


def check_annual_sanity(annual: pd.DataFrame, code: str = "") -> CheckResult:
    """年度宽表（cleaner.build_annual_financials 输出，单位亿元）业务合理性体检。

    检查项：
    1. 会计恒等式：资产合计 = 负债合计 + 股东权益合计（**必须用含少数股东的
       total_equity_all**，用归母 total_equity 会虚增偏差约 13%）
    2. 利润勾稽：净利润 ≈ 归母净利润 + 少数股东损益
    3. 常识边界：分红比例/资产负债率/净利率 ∈ [0,100]、总资产与营收为正
    4. 同比异常：|营收同比|、|净利润同比| 超过 200%（多为单位或口径错误）
    """
    res = CheckResult(table=f"sanity:{code or 'annual'}")
    if annual is None or annual.empty:
        res.add("非空", False, "年度宽表为空")
        return res

    def _col(name):
        return annual[name] if name in annual.columns else None

    years = [int(d.year) for d in annual["report_date"].tolist()]
    ta, tl, te_all = _col("total_assets"), _col("total_liabilities"), _col("total_equity_all")

    # 1. 会计恒等式
    if ta is not None and tl is not None and te_all is not None:
        bad = []
        for i, y in enumerate(years):
            a, l, e = ta.iloc[i], tl.iloc[i], te_all.iloc[i]
            if any(pd.isna(v) for v in (a, l, e)) or not a:
                continue
            dev = abs(a - (l + e)) / abs(a) * 100
            if dev > _IDENTITY_TOL_PCT:
                bad.append(f"{y}年偏差{dev:.1f}%")
        res.add("会计恒等式 资产=负债+权益", not bad, "；".join(bad[:5]) or f"{len(years)}年全部吻合")

    # 2. 利润勾稽
    np_, npp, mi = _col("net_profit"), _col("net_profit_parent"), _col("minority_interest")
    if np_ is not None and npp is not None and mi is not None:
        bad = []
        for i, y in enumerate(years):
            a, b, c = np_.iloc[i], npp.iloc[i], mi.iloc[i]
            if pd.isna(a) or pd.isna(b) or not a:
                continue
            dev = abs(a - (b + (c if not pd.isna(c) else 0.0))) / abs(a) * 100
            if dev > _PROFIT_TOL_PCT:
                bad.append(f"{y}年偏差{dev:.1f}%")
        res.add("利润勾稽 净利润=归母+少数股东损益", not bad, "；".join(bad[:5]) or f"{len(years)}年全部吻合")

    # 3. 常识边界
    bounds = {
        "dividend_payout_pct": (0.0, 150.0),   # 分红比例：超额分红存在但罕见，留到 150
        "debt_ratio_pct": (0.0, 100.0),        # 资产负债率
    }
    for col, (lo, hi) in bounds.items():
        s = _col(col)
        if s is None:
            continue
        bad = [f"{y}年{v:.1f}" for y, v in zip(years, s)
               if not pd.isna(v) and not (lo <= v <= hi)]
        res.add(f"{col} ∈ [{lo},{hi}]", not bad, "；".join(bad[:5]) or "全部在合理区间")

    for col in ("total_assets", "revenue"):
        s = _col(col)
        if s is None:
            continue
        bad = [f"{y}年{v}" for y, v in zip(years, s) if not pd.isna(v) and v <= 0]
        res.add(f"{col} 为正", not bad, "；".join(bad[:5]) or "全部为正")

    # 4. 同比异常（带**低基数豁免**）
    for yoy_col, val_col, label in (("revenue_yoy_pct", "revenue", "营收同比"),
                                    ("net_profit_yoy_pct", "net_profit", "净利润同比")):
        s, v = _col(yoy_col), _col(val_col)
        if s is None:
            continue
        vmax = v.abs().max() if v is not None else None
        bad = []
        for i, (y, yoy) in enumerate(zip(years, s)):
            if pd.isna(yoy) or abs(yoy) <= _MAX_ABS_YOY_PCT:
                continue
            # 低基数豁免：去年同期绝对值不足该序列峰值的 20% → 高增长属正常
            # （腾讯 2002 营收 +436%、泡泡玛特 2018 净利 +6243% 都是小基数起飞，非数据错误）
            if v is not None and vmax and i > 0:
                base = abs(v.iloc[i - 1]) if not pd.isna(v.iloc[i - 1]) else None
                if base is not None and base < _LOW_BASE_RATIO * vmax:
                    continue
            bad.append(f"{y}年{yoy:.0f}%")
        res.add(f"|{label}| ≤ {_MAX_ABS_YOY_PCT:.0f}%（低基数豁免）", not bad,
                "；".join(bad[:5]) or "无异常波动")

    return res


# --------------------------------------------------------------------------- #
# 行情快照校验（日更 DAG 用）：价格/估值合理性，轻量、不阻断（仅告警）
# --------------------------------------------------------------------------- #
# 估值合理范围：PE/PB 恒为正；PE 上限 200（超过多为接口脏值或亏损股误报）、
# PB 上限 30（金融/高杠杆行业也可能较高，留足余量）。这些是「明显异常」阈值，
# 用于抓接口返回脏价/错位，不做投资判断。
_QUOTE_POSITIVE = ["price", "market_cap"]
_QUOTE_RANGE = {"pe": (0.0, 200.0), "pb": (0.0, 30.0)}


def check_quote(q: pd.DataFrame, code: str | None = None) -> CheckResult:
    """对行情快照单行做合理性校验（日更 DAG 的轻量 gate）。

    与 check_financial_tables 的区别：行情是「高频、错了影响小」的数据，
    故本校验定位为「明显异常识别 + 告警」，不阻断整条流水线（调用方自行决定
    是否 raise）。校验三类：

    1. 结构：非空、含 price/market_cap 列（防接口空表/字段错位）；
    2. 价格合理性：现价落在 [52周低, 52周高] 区间外则视为异常（抓脏价）；
       区间允许 ±5% 容差（52 周高低是历史极值，现价可能因除权/复权略超出）；
    3. 估值合理性：PE/PB 落在合理范围（抓接口返回的 -1/999 等脏值）。

    Args:
        q: fetch_quote 返回的单行 DataFrame（含 price/pe/pb/market_cap/
           price_52w_high/price_52w_low）。
        code: 标的代码（仅用于日志展示）。

    Returns:
        CheckResult，.ok=False 表示存在明显异常（调用方决定是否告警/阻断）。
    """
    label = f"quote:{code}" if code else "quote"
    res = CheckResult(table=label)

    # 1) 结构断言
    if q is None or q.empty:
        res.add("non_empty", False, "行情快照为空")
        return res
    res.add("non_empty", True, f"{len(q)} 行")
    res = _merge(res, check_frame(
        q, label, min_rows=1,
        required_cols=["price", "market_cap"],
        positive_cols=_QUOTE_POSITIVE,
    ))

    # 2) 价格合理性：现价 vs 52 周区间（±5% 容差）
    if all(c in q.columns for c in ("price", "price_52w_high", "price_52w_low")):
        price = pd.to_numeric(q["price"], errors="coerce")
        high = pd.to_numeric(q["price_52w_high"], errors="coerce")
        low = pd.to_numeric(q["price_52w_low"], errors="coerce")
        # 只对同时有 price 和高低的行校验
        valid = price.notna() & high.notna() & low.notna() & (high > 0) & (low > 0)
        if valid.any():
            p = price[valid].iloc[0]
            h = high[valid].iloc[0]
            l = low[valid].iloc[0]
            lo_ok = p >= l * 0.95
            hi_ok = p <= h * 1.05
            if lo_ok and hi_ok:
                res.add("price_in_52w_range", True, f"现价 {p} ∈ [{l}, {h}]")
            else:
                res.add("price_in_52w_range", False,
                        f"现价 {p} 超出 52 周区间 [{l}, {h}]（±5% 容差）")
        else:
            res.add("price_in_52w_range", True, "52 周高低缺失，跳过")

    # 3) 估值合理性：PE/PB 落在合理范围
    for col, (lo, hi) in _QUOTE_RANGE.items():
        if col not in q.columns:
            res.add(f"range:{col}", True, "列缺失，跳过")
            continue
        s = pd.to_numeric(q[col], errors="coerce").dropna()
        if s.empty:
            res.add(f"range:{col}", True, "值缺失，跳过（港股可能无 PE）")
            continue
        v = s.iloc[0]
        if lo < v < hi:
            res.add(f"range:{col}", True, f"{v} ∈ ({lo}, {hi})")
        else:
            res.add(f"range:{col}", False, f"{v} 超出合理范围 ({lo}, {hi})，疑似脏值")

    return res


def _merge(a: CheckResult, b: CheckResult) -> CheckResult:
    """合并两个 CheckResult（用于 check_quote 内部复用 check_frame）。"""
    a.checks.extend(b.checks)
    a.passed += b.passed
    a.failed += b.failed
    return a
