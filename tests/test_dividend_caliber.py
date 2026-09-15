# -*- coding: utf-8 -*-
"""回归测试：分红与股数口径（红筹股本、港股多次派息）。

这两个 bug 的共同点是**静默错数** —— 不报错、不崩溃、测试全绿、报告照出，
只是数字是错的，而且错得"看着很合理"：股息率 5.34%、分红比例 70.5% 都落在
正常区间里，肉眼无法判断。真值只能对年报（海油 2025 年报披露分红比例 45.0%）
才能发现，所以必须把口径钉死在测试里。

两个事故（2026-09，中国海油 A+H 双线报告首次引入红筹结构时暴露）：
  1. `dividend_total = 每股股息 × share_capital` 把资产负债表的「股本」科目
     当成股数 —— 红筹结构下两者不等（751.8 亿 vs 475.3 亿股），分红总额虚高 1.58 倍。
     该 bug 还会连累 52 周股价换算（被压到真实值的六成多）。
  2. 港股同一年度「中期分配 + 年度分配」两笔被 `drop_duplicates` 去掉一笔，
     末期股息整笔丢失，股息率被腰斩。该 bug 影响**所有**港股标的。

估值面板「分位」措辞的守护在 `test_valuation_pctile_scope.py`。
"""
from unittest import mock

import pandas as pd
import pytest

import src.data.fetcher as ft
from src.data.cleaner import build_annual_financials


def _mk(df: pd.DataFrame, symbol: str = "600938") -> pd.DataFrame:
    df = df.copy()
    df["symbol"] = symbol
    df["report_date"] = pd.to_datetime(df["report_date"])
    return df


def _data(dividend: pd.DataFrame, share_capital_yuan: float = 751.8e8) -> dict:
    """最小可用输入（金额单位「元」，由 _to_yi 换算成亿元）。

    数值取中国海油 2025 年度的量级：归母净利 1220 亿、资产 10000 亿、负债 2670 亿；
    「股本」科目 751.8 亿（面值口径，≠ 股数 475.3 亿股）。
    """
    return {
        "profit_sheet": _mk(pd.DataFrame({
            "report_date": ["2025-12-31"],
            "operating_revenue": [4000e8],
            "net_profit_parent": [1220e8],
        })),
        "cash_flow": _mk(pd.DataFrame({
            "report_date": ["2025-12-31"],
            "ocf": [2000e8],
        })),
        "balance_sheet": _mk(pd.DataFrame({
            "report_date": ["2025-12-31"],
            "total_assets": [10000e8],
            "total_liabilities": [2670e8],
            "total_equity": [7330e8],
            "share_capital": [share_capital_yuan],
        })),
        "financial_indicator": _mk(pd.DataFrame({
            "report_date": ["2025-12-31"],
            "net_margin_pct": [30.0],
        })),
        "dividend": dividend,
    }


# ---------------------------------------------------------------------------
# 1. 红筹股本：分红总额必须用真实股数，不能用「股本」科目
# ---------------------------------------------------------------------------

def test_red_chip_dividend_total_uses_real_share_count():
    """分红总额 = 每股股息 × 真实股数（total_shares），而非「股本」科目。

    若回归成 share_capital：1.1455 × 751.8 = 861 亿，分红比例 70.6% —— 与年报
    披露的 45.0% 差出 25pp，且 861 亿 > 当年归母净利 1220 亿的 70%，属于
    「看着像高分红股、实际是算错」的典型静默错数。
    """
    div = _mk(pd.DataFrame({
        "report_date": ["2025-12-31"],
        "dividend_per_10": [11.455],        # 每 10 股派 11.455 元 → 每股 1.1455 元
        "total_shares": [475.3e8],          # 东财「总股本」，单位「股」（真实股数）
        "dividend_yield_pct": [3.38],
    }))

    annual = build_annual_financials(_data(div))

    # 股数取真实值，不是「股本」科目的 751.8
    assert annual["shares_yi"].iloc[0] == pytest.approx(475.3, abs=0.05)

    # 分红总额 1.1455 × 475.3 ≈ 544.5 亿元（错口径会是 861 亿）
    assert annual["dividend_total"].iloc[0] == pytest.approx(544.5, abs=0.5)

    # 分红比例 544.5 / 1220 ≈ 44.6%，与年报披露的 45.0% 吻合
    assert annual["dividend_payout_pct"].iloc[0] == pytest.approx(44.6, abs=0.2)


def test_dividend_total_not_inflated_by_share_capital():
    """守住「1.58 倍虚高」这条线：错口径会让分红比例落在 70% 量级。"""
    div = _mk(pd.DataFrame({
        "report_date": ["2025-12-31"],
        "dividend_per_10": [11.455],
        "total_shares": [475.3e8],
    }))
    annual = build_annual_financials(_data(div))
    payout = annual["dividend_payout_pct"].iloc[0]
    # 真实 ~44.6%，误用 share_capital 为 ~70.6% —— 40% 是两种口径的分水岭
    assert 40.0 < payout < 50.0, f"分红比例 {payout:.1f}% 不在真实口径区间，疑似又用了「股本」科目"


# ---------------------------------------------------------------------------
# 2. 无 total_shares 时回退 share_capital（港股路径；其 share_capital 本就等于股数）
# ---------------------------------------------------------------------------

def test_shares_fallback_to_share_capital_without_total_shares():
    """港股（及老接口）没有 total_shares 列时，仍能用 share_capital 兜底。

    港股的 share_capital 由「归母净利 ÷ 每股基本盈利」反推，本就等于股数
    （见 fetcher 港股股本反推），故此处回退是安全的 —— 这条测试防的是
    「为了修红筹而把港股一起改坏」。
    """
    div = _mk(pd.DataFrame({
        "report_date": ["2025-12-31"],
        "dividend_per_share": [1.28],       # 港股已是「元/股」口径
        # 刻意不给 total_shares 列
    }), symbol="00883")
    data = _data(div, share_capital_yuan=475.3e8)
    for _k in ("profit_sheet", "cash_flow", "balance_sheet", "financial_indicator"):
        data[_k]["symbol"] = "00883"

    annual = build_annual_financials(data)
    assert annual["shares_yi"].iloc[0] == pytest.approx(475.3, abs=0.05)
    # 1.28 × 475.3 ≈ 608.4 亿元
    assert annual["dividend_total"].iloc[0] == pytest.approx(608.4, abs=1.0)


# ---------------------------------------------------------------------------
# 3. 港股同一年度「中期 + 末期」两笔必须加总，不能去重
# ---------------------------------------------------------------------------

def test_hk_dividend_sums_interim_and_final():
    """同一财政年度的中期分配与年度分配要相加（海油 2025：0.73 + 0.55 = 1.28 港元）。

    原实现 `drop_duplicates(subset=["symbol","report_date"], keep="last")` 会把末期
    整笔丢掉 → 每股只剩 0.73，股息率从 5.25% 掉到 3.0%。该 bug 影响**所有**港股标的。
    """
    raw = pd.DataFrame({
        "最新公告日期": ["2025-08-30", "2026-03-28"],
        "财政年度": ["2025", "2025"],       # 同一财政年度两笔
        "分红方案": ["每股派港币0.73元", "每股派港币0.55元"],
        "分配类型": ["中期分配", "年度分配"],
    })

    with mock.patch.object(ft.ak, "stock_hk_dividend_payout_em", return_value=raw):
        df = ft.fetch_hk_dividend("00883")

    # 归一到一行、值等于两笔之和
    assert len(df) == 1
    assert df["dividend_per_share"].iloc[0] == pytest.approx(1.28)
    assert df["report_date"].iloc[0] == pd.Timestamp("2025-12-31")


def test_hk_dividend_different_years_stay_separate():
    """加总只发生在同一财政年度内部，跨年度不能串（防 groupby 键写错）。"""
    raw = pd.DataFrame({
        "最新公告日期": ["2024-08-30", "2025-08-30"],
        "财政年度": ["2024", "2025"],
        "分红方案": ["每股派港币0.74元", "每股派港币0.73元"],
        "分配类型": ["中期分配", "中期分配"],
    })

    with mock.patch.object(ft.ak, "stock_hk_dividend_payout_em", return_value=raw):
        df = ft.fetch_hk_dividend("00883")

    assert len(df) == 2
    got = dict(zip(df["report_date"].dt.year, df["dividend_per_share"]))
    assert got[2024] == pytest.approx(0.74)
    assert got[2025] == pytest.approx(0.73)


def test_dividend_yield_realigns_to_latest_market_cap():
    """股息率的分子与分母必须来自同一时点。

    事故现场（2026-09-15 行情刷新）：市值被腾讯快照覆盖成 15,898.77 亿，
    而股息率仍按百度估值序列最后一行的 16,117 亿算，得到 3.38% ——
    与同一次刷新后的股价 33.45 元对不上（应为 3.42%）。这类不一致肉眼
    看不出来（3.38% 与 3.42% 都落在正常区间），只能靠测试钉住。
    """
    from src.data.adapter import align_dividend_yield

    v = {"dividend_total": 544.1847, "market_cap": 15898.77,
         "dividend_yield": 3.3764}      # 旧值：分母是前一日的 16,117 亿
    align_dividend_yield(v)
    assert v["dividend_yield"] == pytest.approx(3.4228, abs=1e-3)


def test_dividend_yield_untouched_without_components():
    """缺分子或分母时不动原值（港股无分红接口时 dividend_total 缺失）。"""
    from src.data.adapter import align_dividend_yield

    for v in ({"market_cap": 100.0, "dividend_yield": 5.0},
              {"dividend_total": 10.0, "dividend_yield": 5.0},
              {"dividend_yield": 5.0}):
        align_dividend_yield(v)
        assert v["dividend_yield"] == 5.0
