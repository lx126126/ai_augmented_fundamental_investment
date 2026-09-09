# -*- coding: utf-8 -*-
"""腾讯控股（00700）中线波段策略回测。

严格按 journal/_组合/账户②-波段策略.md 的规则实现：
  - 方向层（估值）：PE 近10年分位 <30% 关注，>70% 警惕；PB 分位交叉验证（双低才做多）
  - 时机层（技术）：股价站上 MA60 + MACD 金叉 → 买入；乖离率 +15% 或 PE 分位 >70% → 止盈；
                   跌破 MA60 且 MACD 死叉，或 −8% 止损 → 卖出
  - 分批：试探 1/3 + 确认 1/3 + 极端加仓 1/3

数据源：
  - 日K：akshare stock_hk_hist（前复权）
  - 市值/PB：data/raw/00700/valuation.parquet（百度，周频）
  - 归母净利：data/raw/00700/profit_sheet.parquet（年报）

输出：完整回测报告（文本 + 交易明细），写回测结果文件。
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import akshare as ak


CODE = "00700"
INIT_CAPITAL = 100_000.0  # 初始资金 10 万
FEE_RATE = 0.0003  # 佣金 + 印花税等，简化按双边 0.03% + 卖出印花税 0.1%

# 回测起始日期（None = 全历史）。技术指标用完整历史热身，仅在 start 之后交易。
# 近3年回测：START_DATE = "2023-09-09"，分位窗口 PCTILE_WINDOW_YEARS = 3
START_DATE = None  # 例："2023-09-09"
PCTILE_WINDOW_YEARS = 10  # PE/PB 滚动分位窗口年数


def _load_daily_kline() -> pd.DataFrame:
    """读本地日K缓存（前复权），无缓存则抓取（绕开代理）。"""
    cache = ROOT / "data" / "00700_daily_kline.csv"
    if cache.exists():
        df = pd.read_csv(cache)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    import os
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)
    raw = ak.stock_hk_hist(symbol=CODE, period="daily", start_date="20160101",
                           end_date="20260909", adjust="qfq")
    raw = raw.rename(columns={"日期": "date", "收盘": "close"})
    raw["date"] = raw["date"].astype(str)
    raw[["date", "close"]].to_csv(cache, index=False)
    df = pd.read_csv(cache)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _load_valuation() -> pd.DataFrame:
    """市值 + PB 周频序列（百度）。"""
    val = pd.read_parquet(ROOT / "data" / "raw" / CODE / "valuation.parquet")
    val["report_date"] = pd.to_datetime(val["report_date"])
    mcap = val[val["indicator"] == "market_cap"][["report_date", "value"]].rename(
        columns={"report_date": "date", "value": "mcap_yi"})
    pb = val[val["indicator"] == "pb"][["report_date", "value"]].rename(
        columns={"report_date": "date", "value": "pb"})
    return mcap.sort_values("date").reset_index(drop=True), pb.sort_values("date").reset_index(drop=True)


def _load_annual_net_profit() -> pd.DataFrame:
    """年报归母净利（亿元）。"""
    ps = pd.read_parquet(ROOT / "data" / "raw" / CODE / "profit_sheet.parquet")
    ps["report_date"] = pd.to_datetime(ps["report_date"])
    ann = ps[ps["report_date"].dt.month == 12][["report_date", "net_profit_parent"]].copy()
    ann["net_profit_yi"] = ann["net_profit_parent"] / 1e8
    return ann.sort_values("report_date").reset_index(drop=True)


def _build_pe_series(daily: pd.DataFrame, mcap: pd.DataFrame,
                     annual_np: pd.DataFrame) -> pd.DataFrame:
    """把周频市值 + 年报净利 forward-fill 到每个交易日，算每日 PE 与 PE 分位。

    复现项目 _pe_pctile 思路：市值日频 + 已披露最近年报净利（merge_asof backward）。
    这里市值是周频，用 forward-fill 填到日频（两个采样日之间市值视为不变）。
    """
    # 市值周频 → forward-fill 到日频
    daily = daily.merge(
        mcap.sort_values("date"), on="date", how="left"
    )
    daily["mcap_yi"] = daily["mcap_yi"].ffill()

    # 年报净利 forward-fill（merge_asof backward：取 <= 该日的最近年报）
    ann = annual_np.sort_values("report_date")
    daily = pd.merge_asof(
        daily.sort_values("date"),
        ann[["report_date", "net_profit_yi"]],
        left_on="date", right_on="report_date", direction="backward",
    ).drop(columns=["report_date"])

    daily["pe"] = daily["mcap_yi"] / daily["net_profit_yi"]
    daily["pe"] = daily["pe"].where(daily["net_profit_yi"] > 0)

    # PB 周频 → ffill
    pb_sorted = _load_valuation()[1]
    daily = daily.merge(pb_sorted.rename(columns={"date": "d2"}), left_on="date", right_on="d2",
                        how="left").drop(columns=["d2"])
    daily["pb"] = daily["pb"].ffill()

    return daily


def _pe_pctile_rolling(pe_series: pd.Series, window_years: int = 10) -> pd.Series:
    """滚动 PE 分位：当前 PE 在过去 window_years 年 PE 序列中的百分位。

    与项目 build_valuation 的「全历史分位」略有差异——回测用滚动窗口，
    避免未来函数（当日分位只能用当日之前的数据算）。
    """
    window = window_years * 250  # 交易日近似
    def pct(x):
        arr = x.dropna()
        if len(arr) < 50:  # 数据太少不给分位
            return np.nan
        cur = arr.iloc[-1]
        return (arr < cur).mean() * 100
    return pe_series.rolling(window, min_periods=50).apply(pct, raw=False)


def _add_technical(df: pd.DataFrame) -> pd.DataFrame:
    """加技术指标：MA60、MACD(12,26,9)、乖离率(相对 MA60)。"""
    close = df["close"]
    df["ma60"] = close.rolling(60).mean()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = (df["dif"] - df["dea"])  # 红柱>0 绿柱<0

    # 金叉/死叉（dif 上穿/下穿 dea）
    df["gold_cross"] = (df["dif"] > df["dea"]) & (df["dif"].shift(1) <= df["dea"].shift(1))
    df["death_cross"] = (df["dif"] < df["dea"]) & (df["dif"].shift(1) >= df["dea"].shift(1))

    # 乖离率（相对 MA60）
    df["bias60"] = (close - df["ma60"]) / df["ma60"] * 100

    return df


def run_backtest() -> dict:
    print("加载数据...")
    daily = _load_daily_kline()
    mcap, pb = _load_valuation()
    ann = _load_annual_net_profit()

    df = _build_pe_series(daily, mcap, ann)
    df = _add_technical(df)

    # 滚动 PE/PB 分位（窗口年数可配置）
    df["pe_pctile"] = _pe_pctile_rolling(df["pe"], window_years=PCTILE_WINDOW_YEARS)
    df["pb_pctile"] = _pe_pctile_rolling(df["pb"], window_years=PCTILE_WINDOW_YEARS)

    # 只保留数据齐全的区间
    df = df.dropna(subset=["ma60", "pe_pctile", "pb_pctile"]).reset_index(drop=True)

    # 若指定回测起始日期，只在此之后交易（技术指标已用完整历史热身）
    if START_DATE is not None:
        start_ts = pd.Timestamp(START_DATE)
        df["tradable"] = df["date"] >= start_ts
    else:
        df["tradable"] = True

    # ---- 事件驱动回测 ----
    cash = INIT_CAPITAL
    shares = 0.0
    cost_basis = 0.0  # 持仓均价
    trades = []  # 交易明细
    position_plan = 0.0  # 计划总仓位（用于分批）
    last_buy_price = 0.0

    # 状态：0=空仓，1=持仓
    state = 0
    batch = 0  # 已买批次

    for i, row in df.iterrows():
        if not row["tradable"]:
            continue
        price = row["close"]
        pe_pct = row["pe_pctile"]
        pb_pct = row["pb_pctile"]
        above_ma = price > row["ma60"]
        bias = row["bias60"]

        # ---- 空仓 → 找买点 ----
        if state == 0 and batch == 0:
            # 买入条件：PE/PB 双低 + 站上 MA60 + MACD 金叉
            cheap = pe_pct < 30 and pb_pct < 30
            if cheap and above_ma and row["gold_cross"]:
                # 第一批 1/3
                amt = INIT_CAPITAL / 3
                qty = amt / price
                fee = amt * FEE_RATE
                cash -= amt + fee
                shares += qty
                cost_basis = price
                state = 1
                batch = 1
                last_buy_price = price
                trades.append({"date": row["date"], "action": "买入(第1批)",
                               "price": round(price, 2), "qty": round(qty, 0),
                               "note": f"PE分位{pe_pct:.0f}% PB分位{pb_pct:.0f}% 金叉"})

        # ---- 持仓 → 加仓 / 止盈 / 止损 ----
        elif state == 1:
            # 第二批确认：回踩 MA60 不破（价格仍在 MA60 上方）+ 红柱放大
            if batch == 1 and above_ma and row["macd_hist"] > 0 and row["macd_hist"] > df["macd_hist"].iloc[max(0, i-1)]:
                amt = INIT_CAPITAL / 3
                qty = amt / price
                fee = amt * FEE_RATE
                cash -= amt + fee
                # 更新成本均价
                total_cost = cost_basis * (shares) + amt
                shares += qty
                cost_basis = total_cost / shares
                batch = 2
                trades.append({"date": row["date"], "action": "买入(第2批)",
                               "price": round(price, 2), "qty": round(qty, 0),
                               "note": "回踩不破 红柱放大"})

            # 止盈：PE 分位 >70% 或 乖离率 +15%
            stop_profit = pe_pct > 70 or bias > 15
            # 止损：跌破 MA60 且死叉，或 -8%
            stop_loss = (not above_ma and row["death_cross"]) or (price < cost_basis * 0.92)

            if stop_profit or stop_loss:
                qty = shares
                amt = qty * price
                fee = amt * FEE_RATE + amt * 0.001  # 卖出印花税 0.1%
                cash += amt - fee
                ret = (price - cost_basis) / cost_basis * 100
                reason = "止盈" if stop_profit else "止损"
                trades.append({"date": row["date"], "action": f"卖出({reason})",
                               "price": round(price, 2), "qty": round(qty, 0),
                               "note": f"收益率{ret:+.1f}%"})
                shares = 0.0
                state = 0
                batch = 0

    # 期末平仓结算
    final_price = df["close"].iloc[-1]
    if shares > 0:
        amt = shares * final_price
        fee = amt * FEE_RATE + amt * 0.001
        cash += amt - fee
        trades.append({"date": df["date"].iloc[-1], "action": "期末平仓",
                       "price": round(final_price, 2), "qty": round(shares, 0),
                       "note": "回测结束强平"})
        shares = 0.0

    final_value = cash
    total_return = (final_value - INIT_CAPITAL) / INIT_CAPITAL * 100

    # 同期买入持有（基准）：从实际交易起始日（首个 tradable 日）起算
    tradable_df = df[df["tradable"]]
    base_start = tradable_df["close"].iloc[0]
    base_ret = (df["close"].iloc[-1] - base_start) / base_start * 100

    return {
        "df": tradable_df, "trades": trades, "final_value": final_value,
        "total_return": total_return, "base_return": base_ret,
        "n_trades": len(trades),
        "years": (tradable_df["date"].iloc[-1] - tradable_df["date"].iloc[0]).days / 365,
    }


def _format_report(r: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("腾讯控股（00700）中线波段策略回测报告")
    lines.append("=" * 70)
    lines.append(f"回测区间：{r['df']['date'].iloc[0].date()} ~ {r['df']['date'].iloc[-1].date()}"
                 f"（{r['years']:.1f} 年）")
    lines.append(f"初始资金：{INIT_CAPITAL:,.0f} 元")
    lines.append("")
    lines.append(f"策略累计收益：{r['total_return']:+.1f}%  →  期末资产 {r['final_value']:,.0f} 元")
    lines.append(f"买入持有基准：{r['base_return']:+.1f}%")
    lines.append(f"超额收益：{r['total_return'] - r['base_return']:+.1f}pp")
    lines.append(f"交易次数：{r['n_trades']} 笔")
    lines.append("")
    lines.append("-" * 70)
    lines.append("交易明细：")
    lines.append("-" * 70)
    for t in r["trades"]:
        lines.append(f"  {t['date'].date()}  {t['action']:<12} @ {t['price']:>8.2f}  "
                     f"{t['qty']:>8.0f} 股   {t['note']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    # 命令行可选：近3年回测 → python scripts/backtest_band.py 3y
    if len(sys.argv) > 1 and sys.argv[1] == "3y":
        START_DATE = "2023-09-09"
        PCTILE_WINDOW_YEARS = 3
        tag = "近3年"
    else:
        tag = "全历史"

    r = run_backtest()
    report = _format_report(r)
    print(report)

    # 落盘
    out = ROOT / "journal" / "_组合" / f"账户②-波段回测-腾讯-{tag}.md"
    header = (
        f"# 账户② 波段策略回测 · 腾讯控股（00700）· {tag}\n\n"
        "> ⚠️ 主观策略回测，已 gitignore，严禁入库。\n"
        f"> 回测规则见《账户②-波段策略.md》框架 v1。分位窗口 {PCTILE_WINDOW_YEARS} 年，"
        f"交易起点 {START_DATE or '全历史'}。**本回测含未来函数风险，仅作框架验证，非实盘依据。**\n\n"
        "```\n" + report + "\n```\n"
    )
    out.write_text(header, encoding="utf-8")
    print(f"\n已写入: {out}")
