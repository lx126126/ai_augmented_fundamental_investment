# -*- coding: utf-8 -*-
"""财务造假检测：Beneish M-Score + 现金流背离 + 应收/存货异常。

护城河之二：为投资者避坑。基于年度财务数据计算，阈值与口径在报告透明标注。

Beneish M-Score 8 因子模型（Beneish 1999，操纵利润检测）：
  M = -4.84 + 0.92·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
        + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI
  判读：M > -1.78 → 可能操纵利润（高风险）。
"""
from __future__ import annotations

import pandas as pd

MSCORE_THRESHOLD = -1.78


def _g(row, col, annual):
    """取字段值，缺失/NaN → None。"""
    if col not in annual.columns:
        return None
    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    return float(v)


def _div(a, b):
    """安全除法，None 或分母 0 → None。"""
    if a is None or b is None or b == 0:
        return None
    return a / b


def _net_profit(row, annual):
    """净利润（优先总额，缺失回退归母）。"""
    return _g(row, "net_profit", annual) or _g(row, "net_profit_parent", annual)


def compute_mscore(annual: pd.DataFrame) -> dict | None:
    """Beneish M-Score（8 因子），基于最近两年年报。"""
    if annual is None or len(annual) < 2:
        return None
    t = annual.iloc[-1]
    p = annual.iloc[-2]

    # 1. DSRI：应收账款周转指数
    dsri = _div(
        _div(_g(t, "accounts_receivable", annual), _g(t, "operating_revenue", annual)),
        _div(_g(p, "accounts_receivable", annual), _g(p, "operating_revenue", annual)),
    )
    # 2. GMI：毛利率指数
    gmi = _div(_g(p, "gross_margin_pct", annual), _g(t, "gross_margin_pct", annual))
    # 3. AQI：资产质量指数
    def _aq(row):
        ca, fa, ta = (_g(row, "current_assets", annual),
                      _g(row, "fixed_assets", annual),
                      _g(row, "total_assets", annual))
        if None in (ca, fa, ta) or ta == 0:
            return None
        return 1 - (ca + fa) / ta
    aqi = _div(_aq(t), _aq(p))
    # 4. SGI：销售增长指数
    sgi = _div(_g(t, "operating_revenue", annual), _g(p, "operating_revenue", annual))
    # 5. DEPI：折旧指数
    def _depr(row):
        dep, fa = _g(row, "depreciation", annual), _g(row, "fixed_assets", annual)
        if dep is None or fa is None:
            return None
        denom = dep + fa
        return dep / denom if denom else None
    depi = _div(_depr(p), _depr(t))
    # 6. SGAI：销售管理费用指数
    def _sga(row):
        se = _g(row, "sell_expense", annual)
        ae = _g(row, "admin_expense", annual)
        rev = _g(row, "operating_revenue", annual)
        if rev is None or rev == 0:
            return None
        return ((se or 0) + (ae or 0)) / rev
    sgai = _div(_sga(t), _sga(p))
    # 7. TATA：应计项/总资产
    def _tata(row):
        ni = _net_profit(row, annual)
        ocf = _g(row, "ocf", annual)
        ta = _g(row, "total_assets", annual)
        if None in (ni, ocf, ta) or ta == 0:
            return None
        return (ni - ocf) / ta
    tata_t = _tata(t)
    # 8. LVGI：杠杆指数
    def _lev(row):
        tl = _g(row, "total_liabilities", annual)
        ta = _g(row, "total_assets", annual)
        if None in (tl, ta) or ta == 0:
            return None
        return tl / ta
    lvgi = _div(_lev(t), _lev(p))

    def _safe(x, neutral=1.0):
        return x if x is not None else neutral

    m = (-4.84 + 0.92 * _safe(dsri) + 0.528 * _safe(gmi) + 0.404 * _safe(aqi)
         + 0.892 * _safe(sgi) + 0.115 * _safe(depi) - 0.172 * _safe(sgai)
         + 4.679 * _safe(tata_t, neutral=0.0) - 0.327 * _safe(lvgi))

    return {
        "mscore": m,
        "risk": "high" if m > MSCORE_THRESHOLD else "low",
        "threshold": MSCORE_THRESHOLD,
        "factors": {"dsri": dsri, "gmi": gmi, "aqi": aqi, "sgi": sgi,
                    "depi": depi, "sgai": sgai, "tata": tata_t, "lvgi": lvgi},
    }


def _cashflow_divergence(annual: pd.DataFrame) -> dict | None:
    """现金流背离：近3年 经营现金流/净利润（净现比）。"""
    if annual is None or len(annual) < 1:
        return None
    ratios = []
    for _, r in annual.tail(3).iterrows():
        ni = _net_profit(r, annual)
        ocf = _g(r, "ocf", annual)
        ratios.append(ocf / ni if (ni not in (None, 0) and ocf is not None) else None)
    low_years = sum(1 for x in ratios if x is not None and x < 0.5)
    return {"ratios": ratios, "low_years": low_years, "warning": low_years >= 2}


def _receivable_divergence(annual: pd.DataFrame) -> dict | None:
    """应收增速 vs 营收增速（背离提示激进确认收入）。"""
    if annual is None or len(annual) < 2:
        return None
    t, p = annual.iloc[-1], annual.iloc[-2]
    ar_t, ar_p = _g(t, "accounts_receivable", annual), _g(p, "accounts_receivable", annual)
    rev_t, rev_p = _g(t, "operating_revenue", annual), _g(p, "operating_revenue", annual)
    if None in (ar_t, ar_p, rev_t, rev_p) or ar_p == 0 or rev_p == 0:
        return None
    ar_yoy = (ar_t / ar_p - 1) * 100
    rev_yoy = (rev_t / rev_p - 1) * 100
    gap = ar_yoy - rev_yoy
    return {"ar_yoy": ar_yoy, "rev_yoy": rev_yoy, "gap": gap, "warning": gap > 10}


def _audit_risk(opinion) -> dict:
    """审计意见风险判定（东财 OPINION_TYPE，仅年报有值）。

    分级：
      clean —— 标准无保留意见（正常）
      watch —— 带强调事项段 / 持续经营重大不确定性段的无保留意见（提示）
      high  —— 保留意见 / 无法表示意见 / 否定意见（非标，重大红旗）
    """
    if opinion is None:
        return {"opinion": None, "level": None}
    if isinstance(opinion, float) and pd.isna(opinion):
        return {"opinion": None, "level": None}
    op = str(opinion).strip()
    if not op or op.lower() == "nan":
        return {"opinion": None, "level": None}

    if op == "标准无保留意见":
        level = "clean"
    elif "无保留" in op:
        level = "watch"   # 带强调事项段 / 持续经营重大不确定性段的无保留意见
    else:
        level = "high"    # 保留 / 无法表示 / 否定意见
    return {"opinion": op, "level": level}


def fraud_check(annual: pd.DataFrame) -> dict:
    """综合造假检测：M-Score + 现金流背离 + 应收异常 + 审计意见 → 风险评级。"""
    mscore = compute_mscore(annual)
    cashflow = _cashflow_divergence(annual)
    receivable = _receivable_divergence(annual)

    # 审计意见（最新年报，来自资产负债表 OPINION_TYPE）
    audit = _audit_risk(
        annual["audit_opinion"].iloc[-1] if "audit_opinion" in annual.columns else None
    )

    flags = []
    if mscore and mscore["risk"] == "high":
        flags.append("M-Score 超阈值")
    if cashflow and cashflow["warning"]:
        flags.append("现金流背离")
    if receivable and receivable["warning"]:
        flags.append("应收增速背离")
    if audit["level"] == "high":
        flags.append("非标审计意见")
    elif audit["level"] == "watch":
        flags.append("审计意见含强调事项")

    # 非标审计意见（保留/无法表示/否定）一票否决 → 高风险
    if audit["level"] == "high":
        overall = "high"
    elif len(flags) >= 2:
        overall = "high"
    elif len(flags) == 1:
        overall = "medium"
    else:
        overall = "low"

    return {
        "mscore": mscore,
        "cashflow": cashflow,
        "receivable": receivable,
        "audit_opinion": audit["opinion"],
        "audit_level": audit["level"],
        "overall_risk": overall,
        "flags": flags,
    }
