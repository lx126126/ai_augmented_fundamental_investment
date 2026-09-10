#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据复核快照：提取 6 个标的报告里的关键易错字段，生成核对摘要。

用途：陪潇姐"过数据"时，快速扫一遍易错点（估值分位/总股本/分红/有息负债/总市值/股价日期等），
发现异常再逐项深挖。只读 parquet，不改任何数据。
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402
from src.data.adapter import build_template_data, load_raw  # noqa: E402
from src.data.cleaner import build_annual_financials  # noqa: E402

CODES = [
    ("601088", "中国神华"),
    ("600519", "贵州茅台"),
    ("000651", "格力电器"),
    ("601328", "交通银行"),
    ("00700", "腾讯控股"),
    ("09992", "泡泡玛特"),
]


def _n(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    return v


def _f(v, d=1):
    v = _n(v)
    return "—" if v is None else f"{float(v):.{d}f}"


def main():
    for code, name in CODES:
        print("=" * 92)
        print(f"{name}（{code}）")
        print("-" * 92)
        try:
            td = build_template_data(code)
        except Exception as e:
            print(f"  !! 加载失败: {type(e).__name__}: {e}")
            continue

        raw = load_raw(code)
        annual = build_annual_financials(raw)
        latest = annual.iloc[-1]
        yr = int(latest["report_date"].year)
        rp = td.get("report_period")

        def g(col):
            v = latest.get(col) if col in annual.columns else None
            return _n(v)

        # ---- 估值面板 ----
        val = td.get("valuation") or {}
        print(f"  报告期 {rp} · 最新年报 {yr}")
        print(f"  [估值] 现价 {_f(val.get('price_now'), 2)} | "
              f"52周 {_f(val.get('price_low'), 2)} ~ {_f(val.get('price_high'), 2)} | "
              f"股价日期 {val.get('quote_date')}")
        mcap = _n(val.get("market_cap"))
        mcap_txt = "—" if mcap is None else (f"{mcap/10000:.2f}万亿" if mcap >= 10000 else f"{mcap:.0f}亿")
        print(f"  [估值] PE {_f(val.get('pe'), 1)}x (分位 {_f(val.get('pe_pctile'), 0)}%) | "
              f"PB {_f(val.get('pb'), 2)}x (分位 {_f(val.get('pb_pctile'), 0)}%) | "
              f"股息率 {_f(val.get('dividend_yield'), 1)}% | 总市值 {mcap_txt}")

        # ---- 年度关键财务 ----
        print(f"  [年度] 营收 {_f(g('operating_revenue'), 0)}亿 | "
              f"归母净利 {_f(g('net_profit_parent'), 0)}亿 | "
              f"毛利率 {_f(g('gross_margin_pct'), 1)}% | 净利率 {_f(g('net_margin_pct'), 1)}% | "
              f"ROE {_f(g('roe_pct'), 1)}%")
        print(f"  [资产] 总资产 {_f(g('total_assets'), 0)}亿 | "
              f"总负债 {_f(g('total_liabilities'), 0)}亿 | "
              f"净资产(归母) {_f(g('total_equity'), 0)}亿 | "
              f"有息负债 {_f(g('interest_bearing_debt'), 0)}亿 | 总债务 {_f(g('total_debt'), 0)}亿")
        print(f"  [股本] 总股本 {_f(g('share_capital'), 2)}亿股 | "
              f"每股股息 {_f(g('dividend_per_share'), 2)}元 | "
              f"分红比例 {_f(g('dividend_payout_pct'), 1)}% | 股息率 {_f(g('dividend_yield_pct'), 1)}%")

        # ---- 造假检测 ----
        fr = td.get("fraud") or {}
        ov = fr.get("overall_risk")
        flags = fr.get("flags") or []
        flag_txt = "、" .join(flags) if flags else "无"
        print(f"  [造假] 综合风险 {ov or '—'} | 命中项: {flag_txt}")

        # ---- 数据校验 ----
        # validate 需要 PDF；仅看是否有 reconcile 修正记录
        rec = []
        vdir = ROOT / "data" / "validation"
        for p in sorted(vdir.glob(f"{code}_*_reconcile.json")):
            rec.append(p.name.replace(f"{code}_", "").replace("_reconcile.json", ""))
        print(f"  [校验] reconcile 修正记录: {('、'.join(rec)) if rec else '无'}")

    print("=" * 92)
    print("提示：估值分位/总股本/分红比例/有息负债/总市值/股价日期 为历史高频易错点，重点核。")


if __name__ == "__main__":
    main()
