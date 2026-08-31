"""数据拉取层：封装 AKShare 接口，统一输出标准字段。

数据源策略（架构文档 4.1）：
- 主源：AKShare（东财 / 新浪）
- 备用源：mootdx / 腾讯（后续接入）
"""
from __future__ import annotations

import os

# 环境变量可能有 Veee 代理残留（15236），AKShare 拉国内站点（东财/新浪/百度/腾讯）
# 必须禁用代理直连，否则接口可能超时或返回空数据。
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

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


def fetch_valuation(code: str, period: str = "近十年") -> pd.DataFrame | None:
    """百度估值：总市值 + 市净率（近 N 年，长表结构，两指标独立不 merge）。

    长表列：report_date, value, indicator(market_cap/pb), symbol
    东财行情域名被网络限制时用百度估值兜底；某指标无数据时返回 None。
    """
    try:
        mcap = ak.stock_zh_valuation_baidu(symbol=code, indicator="总市值", period=period)
        pb = ak.stock_zh_valuation_baidu(symbol=code, indicator="市净率", period=period)
    except Exception:
        return None
    if mcap is None or mcap.empty or pb is None or pb.empty:
        return None
    mcap = mcap.copy()
    pb = pb.copy()
    mcap["indicator"] = "market_cap"
    pb["indicator"] = "pb"
    df = pd.concat([mcap, pb], ignore_index=True)
    df = df.rename(columns={"date": "report_date"})
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = code.zfill(6)
    return df


def fetch_quote(code: str) -> pd.DataFrame | None:
    """腾讯行情：公司名、现价、PE(TTM)、PB、总市值、52周高低（实时精确）。

    走 qt.gtimg.cn（腾讯域名，网络受限时东财 push2 的替代）。
    字段：name/price/pe/pb/market_cap/price_52w_high/price_52w_low
    """
    import requests
    em = _em_symbol(code).lower()  # sh601088 / sz600519
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={em}", timeout=8)
        r.raise_for_status()
    except Exception:
        return None
    import re
    m = re.search(r'="([^"]*)"', r.text)
    if not m:
        return None
    f = m.group(1).split("~")
    if len(f) < 49 or not f[1]:
        return None
    def _num(s, default=None):
        try:
            return float(s)
        except (ValueError, TypeError):
            return default
    return pd.DataFrame([{
        "name": f[1],
        "price": _num(f[3]),
        "pe": _num(f[39]),
        "pb": _num(f[46]),
        "market_cap": _num(f[45]),
        "price_52w_high": _num(f[47]),
        "price_52w_low": _num(f[48]),
        "symbol": code.zfill(6),
    }])


def fetch_rating(code: str) -> pd.DataFrame | None:
    """东财盈利预测 + 机构评级（近6个月买入/增持/中性/减持/卖出）。

    数据源 stock_profit_forecast_em（全市场），筛出单只股票后标准化列名。
    返回单行 DataFrame：rating_* 评级分布 + eps_{year} 未来3年预测每股收益。
    """
    try:
        df = ak.stock_profit_forecast_em()
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row = df[df["代码"] == code.zfill(6)]
    if row.empty:
        return None

    eps_cols = [c for c in df.columns if "预测每股收益" in str(c)]
    keep = [
        "代码", "名称", "研报数",
        "机构投资评级(近六个月)-买入", "机构投资评级(近六个月)-增持",
        "机构投资评级(近六个月)-中性", "机构投资评级(近六个月)-减持",
        "机构投资评级(近六个月)-卖出",
    ] + eps_cols
    row = row[keep].copy()

    rename = {
        "代码": "symbol", "名称": "name", "研报数": "rating_total",
        "机构投资评级(近六个月)-买入": "rating_buy",
        "机构投资评级(近六个月)-增持": "rating_overweight",
        "机构投资评级(近六个月)-中性": "rating_neutral",
        "机构投资评级(近六个月)-减持": "rating_underweight",
        "机构投资评级(近六个月)-卖出": "rating_sell",
    }
    for c in eps_cols:
        year = str(c).replace("预测每股收益", "").strip()
        rename[c] = f"eps_{year}"
    row = row.rename(columns=rename)
    row["symbol"] = code.zfill(6)
    return row.reset_index(drop=True)


def fetch_profile(code: str) -> pd.DataFrame | None:
    """巨潮公司概况：主营业务 / 经营范围（业务版图的客观文字支撑）。

    数据源 stock_profile_cninfo（巨潮），单行，提取主营业务一句话 + 经营范围。
    用于「业务版图」块：一句话说清公司靠什么赚钱，配分业务收入占比。
    """
    try:
        raw = ak.stock_profile_cninfo(symbol=code.zfill(6))
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    r = raw.iloc[0]
    return pd.DataFrame([{
        "symbol": code.zfill(6),
        "main_business": r.get("主营业务"),
        "business_scope": r.get("经营范围"),
    }])


def fetch_competition(code: str, report_date: str = "20251231") -> pd.DataFrame | None:
    """竞争地位：东财业绩报表（全市场营收 + 申万行业）→ 标的所在行业全部公司。

    数据源 stock_yjbb_em，一次返回全市场约 1.1 万只 A 股的营收/净利/所处行业。
    返回标的所在行业的全部公司（多行，营收降序），列：
      symbol / name / industry / revenue_yi / net_profit_yi / report_date
    adapter 据此计算行业排名、营收份额、同行对比。
    """
    try:
        df = ak.stock_yjbb_em(date=report_date)
    except Exception:
        return None
    if df is None or df.empty:
        return None

    code = code.zfill(6)
    df = df.copy()
    df["symbol"] = df["股票代码"].astype(str).str.zfill(6)
    # 仅保留 A 股（沪 6 / 深 0·3 / 京 4·8），剔除新三板 87 等
    df = df[df["symbol"].str[0].isin(["6", "0", "3", "4", "8"])]

    self_row = df[df["symbol"] == code]
    if self_row.empty:
        return None
    industry = self_row.iloc[0]["所处行业"]
    peers = df[df["所处行业"] == industry].copy()

    out = pd.DataFrame({
        "symbol": peers["symbol"],
        "name": peers["股票简称"],
        "industry": peers["所处行业"],
        "revenue_yi": peers["营业总收入-营业总收入"] / 1e8,
        "net_profit_yi": peers["净利润-净利润"] / 1e8,
        "report_date": pd.to_datetime(report_date, format="%Y%m%d"),
    })
    out = out.sort_values("revenue_yi", ascending=False, na_position="last").reset_index(drop=True)
    return out


def fetch_all(code: str, start_year: str = "2005") -> dict[str, pd.DataFrame]:
    """一次拉取全部表，返回 {表名: DataFrame}。"""
    data = {
        "financial_indicator": fetch_financial_indicator(code, start_year),
        "profit_sheet": fetch_profit_sheet(code),
        "balance_sheet": fetch_balance_sheet(code),
        "cash_flow": fetch_cash_flow(code),
        "dividend": fetch_dividend(code),
        "segments": fetch_segments(code),
    }
    val = fetch_valuation(code)
    if val is not None:
        data["valuation"] = val
    quote = fetch_quote(code)
    if quote is not None:
        data["quote"] = quote
    rating = fetch_rating(code)
    if rating is not None:
        data["rating"] = rating
    profile = fetch_profile(code)
    if profile is not None:
        data["profile"] = profile
    # 竞争地位（用利润表最新年报期对齐，保证与年度财务数据同口径）
    ps = data.get("profit_sheet")
    if ps is not None and not ps.empty:
        annual_dates = ps[ps["report_date"].dt.month == 12]["report_date"]
        if not annual_dates.empty:
            comp = fetch_competition(code, annual_dates.max().strftime("%Y%m%d"))
            if comp is not None:
                data["competition"] = comp
    return data
