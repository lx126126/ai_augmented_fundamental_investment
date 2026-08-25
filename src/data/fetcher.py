"""数据拉取层：封装 AKShare 接口，统一输出标准字段。

数据源策略（架构文档 4.1）：
- 主源：AKShare（东财 / 新浪）
- 备用源：mootdx / 腾讯（后续接入）
"""
from __future__ import annotations

import akshare as ak
import pandas as pd

from .fields import (
    FINANCIAL_INDICATOR_MAP,
    PROFIT_SHEET_MAP,
    BALANCE_SHEET_MAP,
    CASH_FLOW_MAP,
    DIVIDEND_MAP,
    SEGMENT_MAP,
)


def _em_symbol(code: str) -> str:
    """纯数字代码 → 东财接口带交易所前缀的代码。"""
    code = code.zfill(6)
    if code.startswith(("6", "9")):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return code


def _remap(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """按映射重命名列，只保留映射中存在的列。"""
    cols = {k: v for k, v in mapping.items() if k in df.columns}
    return df[list(cols.keys())].rename(columns=cols)


def fetch_financial_indicator(code: str, start_year: str = "2005") -> pd.DataFrame:
    """财务指标（比率型）：毛利率/净利率/ROE/负债率/增速/现金流背离等。

    覆盖 2005 至今（含上市前招股书披露），报告期为季度。
    """
    raw = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
    df = _remap(raw, FINANCIAL_INDICATOR_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    return df


def fetch_profit_sheet(code: str) -> pd.DataFrame:
    """利润表（绝对额）：营业收入/净利润等。"""
    raw = ak.stock_profit_sheet_by_report_em(symbol=_em_symbol(code))
    df = _remap(raw, PROFIT_SHEET_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    return df


def fetch_balance_sheet(code: str) -> pd.DataFrame:
    """资产负债表（时点值）：总资产/负债/货币资金/存货/应收/商誉等。"""
    raw = ak.stock_balance_sheet_by_report_em(symbol=_em_symbol(code))
    df = _remap(raw, BALANCE_SHEET_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    return df


def fetch_cash_flow(code: str) -> pd.DataFrame:
    """现金流表：经营/投资/筹资现金流净额。"""
    raw = ak.stock_cash_flow_sheet_by_report_em(symbol=_em_symbol(code))
    df = _remap(raw, CASH_FLOW_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    return df


def fetch_dividend(code: str) -> pd.DataFrame:
    """分红送配：每10股派息、股息率、总股本（普通股数量）。"""
    raw = ak.stock_fhps_detail_em(symbol=code)
    df = _remap(raw, DIVIDEND_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    df["dividend_yield_pct"] = df["dividend_yield"] * 100  # 小数 → 百分比
    return df


def fetch_segments(code: str) -> pd.DataFrame:
    """主营构成（分业务收入/毛利率），东财口径，半年度披露。"""
    raw = ak.stock_zygc_em(symbol=_em_symbol(code))
    df = _remap(raw, SEGMENT_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    return df


def fetch_all(code: str, start_year: str = "2005") -> dict[str, pd.DataFrame]:
    """一次拉取全部六张表，返回 {表名: DataFrame}。"""
    return {
        "financial_indicator": fetch_financial_indicator(code, start_year),
        "profit_sheet": fetch_profit_sheet(code),
        "balance_sheet": fetch_balance_sheet(code),
        "cash_flow": fetch_cash_flow(code),
        "dividend": fetch_dividend(code),
        "segments": fetch_segments(code),
    }
