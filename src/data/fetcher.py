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
    HK_PROFIT_SHEET_MAP,
    HK_BALANCE_SHEET_MAP,
    HK_CASH_FLOW_MAP,
    HK_FINANCIAL_INDICATOR_MAP,
    HK_DIVIDEND_MAP,
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


def fetch_quote(code: str, market: str | None = None) -> pd.DataFrame | None:
    """腾讯行情：公司名、现价、PE(TTM)、PB、总市值、52周高低（实时精确）。

    走 qt.gtimg.cn（腾讯域名，网络受限时东财 push2 的替代）。
    字段：name/price/pe/pb/market_cap/price_52w_high/price_52w_low
    market：可选交易所前缀（如港股传 "hk"），默认按 A 股规则推断。
    """
    import requests
    if market:
        em = f"{market}{code.zfill(5) if market == 'hk' else code.zfill(6)}".lower()
    else:
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
    # 腾讯行情字段布局：A 股与港股不同（港股 f[46] 是英文名而非 PB）。
    # A 股: f[39]=PE(TTM), f[46]=PB, f[45]=总市值, f[47]=52周高, f[48]=52周低
    # 港股: f[57]=PE(TTM), f[58]=PB, f[45]=总市值, f[48]=52周高, f[49]=52周低
    if market == "hk":
        pe_idx, pb_idx, high_idx, low_idx = 57, 58, 48, 49
        div_yield_idx = 47  # 港股 f[47]=股息率(%)，与 A 股 f[47]=52周高不同
    else:
        pe_idx, pb_idx, high_idx, low_idx = 39, 46, 47, 48
        div_yield_idx = None  # A 股股息率由分红接口 dividend_yield_pct 提供
    row = {
        "name": f[1],
        "price": _num(f[3]),
        "pe": _num(f[pe_idx]),
        "pb": _num(f[pb_idx]),
        "market_cap": _num(f[45]),
        "price_52w_high": _num(f[high_idx]),
        "price_52w_low": _num(f[low_idx]),
        "symbol": code.zfill(6),
    }
    if div_yield_idx is not None:
        row["dividend_yield"] = _num(f[div_yield_idx])
    return pd.DataFrame([row])


def _hk_code(code: str) -> str:
    """港股代码规范化：剥 .HK 后缀，去前导零（东财港股接口要 5 位纯数字，如 09992）。"""
    code = str(code).upper().replace(".HK", "").strip()
    code = code.zfill(5)
    return code


def _hk_report_to_wide(symbol: str, mapping: dict, code: str) -> pd.DataFrame:
    """港股三表长表 → 宽表（标准字段名）。

    长表每行一个字段（STD_ITEM_CODE + AMOUNT），按 STD_ITEM_CODE 映射后
    pivot 成「每报告期一行、每字段一列」的宽表，单位人民币元（东财港股接口原生人民币）。
    用 indicator="报告期" 拉半年度（6-30 中期 + 12-31 年报），港股无 Q1/Q3 季报。
    """
    hk = _hk_code(code)
    df = ak.stock_financial_hk_report_em(stock=hk, symbol=symbol, indicator="报告期")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df["STD_ITEM_CODE"].isin(mapping.keys())].copy()
    df["field"] = df["STD_ITEM_CODE"].map(mapping)
    df["report_date"] = pd.to_datetime(df["REPORT_DATE"])
    wide = df.pivot_table(index="report_date", columns="field", values="AMOUNT", aggfunc="first")
    wide = wide.reset_index()
    wide["symbol"] = hk
    wide["report_date"] = wide["report_date"].astype("datetime64[us]")
    return wide


def fetch_hk_profit_sheet(code: str) -> pd.DataFrame:
    """港股利润表（长表→宽表，人民币元，标准字段名）。"""
    return _hk_report_to_wide("利润表", HK_PROFIT_SHEET_MAP, code)


def fetch_hk_balance_sheet(code: str) -> pd.DataFrame:
    """港股资产负债表（长表→宽表，人民币元，标准字段名）。"""
    return _hk_report_to_wide("资产负债表", HK_BALANCE_SHEET_MAP, code)


def fetch_hk_cash_flow(code: str) -> pd.DataFrame:
    """港股现金流量表（长表→宽表，人民币元，标准字段名）。"""
    wide = _hk_report_to_wide("现金流量表", HK_CASH_FLOW_MAP, code)
    # 资本开支：港股接口「购建固定资产」为流出，接口返回正数，取绝对值对齐 A 股口径
    if not wide.empty and "capital_expenditure" in wide.columns:
        wide["capital_expenditure"] = wide["capital_expenditure"].abs()
    return wide


def fetch_hk_financial_indicator(code: str) -> pd.DataFrame:
    """港股财务指标（宽表，英文列名 → 标准字段名）。"""
    hk = _hk_code(code)
    raw = ak.stock_financial_hk_analysis_indicator_em(symbol=hk, indicator="报告期")
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = _remap(raw, HK_FINANCIAL_INDICATOR_MAP).copy()
    df["report_date"] = pd.to_datetime(df["report_date"]).astype("datetime64[us]")
    df["symbol"] = hk
    return df


def _extract_hk_dividend(text) -> float | None:
    """从港股分红方案文本提取每股股息（元/股），优先人民币口径。

    分红方案形如「每股派人民币0.8146元(相当于港币0.8881元)」。
    """
    import re
    s = str(text)
    # 优先人民币：每股派人民币X.XX元
    m = re.search(r"人民币\s*([0-9.]+)\s*元", s)
    if m:
        return float(m.group(1))
    # 其次港币：港币X.XX元
    m = re.search(r"港币\s*([0-9.]+)\s*元", s)
    if m:
        return float(m.group(1))
    # 兜底：每股派X.XX元（无币种，按人民币）
    m = re.search(r"每股派?[^0-9]*([0-9.]+)\s*元", s)
    if m:
        return float(m.group(1))
    return None


def fetch_hk_dividend(code: str) -> pd.DataFrame | None:
    """港股分红（每股股息），从分红方案文本正则提取人民币口径。

    分红方案形如「每股派人民币0.8146元(相当于港币0.8881元)」，
    优先提取「人民币」口径（港股股息多以人民币计价），其次「港币」。
    每股股息（元/股），与 A 股 dividend_per_share 口径一致。
    """
    hk = _hk_code(code)
    try:
        raw = ak.stock_hk_dividend_payout_em(symbol=hk)
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    df = _remap(raw, HK_DIVIDEND_MAP).copy()

    df["dividend_per_share"] = df["dividend_text"].map(_extract_hk_dividend)
    df["dividend_per_share"] = pd.to_numeric(df["dividend_per_share"], errors="coerce")
    # 财政年度 → 报告期（归一到 12-31，与 A 股年度口径一致）
    df["report_date"] = pd.to_datetime(
        df["fiscal_year"].astype(str) + "-12-31", errors="coerce"
    ).astype("datetime64[us]")
    df["symbol"] = hk
    # 仅保留年度分配 + 有股息值的行；剔除冗余列（announce_date 为字符串对象类型，且无业务价值）
    df = df[df["dividend_per_share"].notna()].copy()
    df = df.drop(columns=["dividend_text", "announce_date", "fiscal_year", "dividend_type"], errors="ignore")
    df = df[["symbol", "report_date", "dividend_per_share"]].drop_duplicates(
        subset=["symbol", "report_date"], keep="last"
    )
    return df


def fetch_hk_valuation(code: str, period: str = "近十年") -> pd.DataFrame | None:
    """港股百度估值：总市值 + 市净率（长表，date/value 两列，与 A 股结构一致）。"""
    hk = _hk_code(code)
    try:
        mcap = ak.stock_hk_valuation_baidu(symbol=hk, indicator="总市值", period=period)
        pb = ak.stock_hk_valuation_baidu(symbol=hk, indicator="市净率", period=period)
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
    df["symbol"] = hk
    return df


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


def fetch_hk_rating(code: str) -> pd.DataFrame | None:
    """港股机构评级：经济通券商逐条评级 → 聚合为五档分布 + 预测 EPS + 目标价。

    数据源 stock_hk_profit_forecast_et（etnet 经济通，港股券商预测），
    逐条含 财政年度/纯利/每股盈利/每股派息/证券商/评级/目标价/更新日期。
    港股评级体系（买入/增持/优于大市/跑赢行业/中性/持有/减持/沽出）映射到
    与 A 股 fetch_rating 一致的五档（rating_buy/overweight/neutral/underweight/sell），
    使 adapter._build_rating 直接复用。
    """
    try:
        df = ak.stock_hk_profit_forecast_et(symbol=_hk_code(code), indicator="盈利预测概览")
    except Exception:
        return None
    if df is None or df.empty or "评级" not in df.columns:
        return None

    df = df.copy()
    # 剔除无评级样本（"--" 表示券商未给评级）
    r = df["评级"].astype(str).str.strip()
    df = df[r != "--"]
    if df.empty:
        return None

    # 港股评级 → 标准五档（港股「优于大市/跑赢行业」≈ A 股「增持」）
    def _bucket(v: str) -> str:
        v = str(v).strip()
        if v in ("买入", "强烈买入"):
            return "buy"
        if v in ("增持", "优于大市", "跑赢行业", "跑赢大市"):
            return "overweight"
        if v in ("持有", "中性", "与大市同步"):
            return "neutral"
        if v == "减持":
            return "underweight"
        if v in ("沽出", "卖出", "强烈卖出"):
            return "sell"
        return None

    df["bucket"] = df["评级"].map(_bucket)
    df = df[df["bucket"].notna()]
    if df.empty:
        return None

    counts = df["bucket"].value_counts().to_dict()

    # 预测 EPS：按财政年度分组取均值（每股盈利单位「分」，即 0.01 元 → 转元）
    eps_forecast = []
    if "每股盈利" in df.columns and "财政年度" in df.columns:
        eps = pd.to_numeric(df["每股盈利"], errors="coerce")
        for year, grp in df.groupby("财政年度", sort=True):
            e = eps[grp.index].mean()
            if pd.notna(e):
                eps_forecast.append({"year": str(year), "eps": round(float(e) / 100, 3)})

    target_price = None
    if "目标价" in df.columns:
        tp = pd.to_numeric(df["目标价"], errors="coerce")
        if tp.notna().any():
            target_price = round(float(tp.mean()), 2)

    row = {
        "symbol": _hk_code(code),
        "name": "",
        "rating_total": int(len(df)),
        "rating_buy": int(counts.get("buy", 0)),
        "rating_overweight": int(counts.get("overweight", 0)),
        "rating_neutral": int(counts.get("neutral", 0)),
        "rating_underweight": int(counts.get("underweight", 0)),
        "rating_sell": int(counts.get("sell", 0)),
        "target_price": target_price,
    }
    for e in eps_forecast:
        row[f"eps_{e['year']}"] = e["eps"]

    return pd.DataFrame([row])


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


def fetch_hk_profile(code: str) -> pd.DataFrame | None:
    """港股公司概况：所属行业（恒生分类）+ 公司介绍（竞争地位/业务版图的客观支撑）。

    数据源 stock_hk_company_profile_em（东财 datacenter，港股 F10）。
    港股无「全市场营收排名」接口（东财业绩报表 stock_yjbb_em 是 A 股专用），
    故港股竞争地位降级为「行业定位 + 公司介绍」，不提供行业排名/营收份额。
    """
    try:
        raw = ak.stock_hk_company_profile_em(symbol=_hk_code(code))
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    r = raw.iloc[0]
    return pd.DataFrame([{
        "symbol": _hk_code(code),
        "company_name": r.get("公司名称"),
        "industry": r.get("所属行业"),
        "company_intro": r.get("公司介绍"),
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


# 港股标的：三表 + 财务指标 + 估值 + 分红 + 行情（财报原生人民币，市值/股价原生港元）


def fetch_all_hk(code: str) -> dict[str, pd.DataFrame]:
    """港股标的：三表 + 财务指标 + 估值 + 分红 + 行情。

    财报三表/财务指标原生即为人民币口径（东财直接返回人民币值，不换算）；
    估值/行情（市值/股价）原生为港元口径。与 A 股 fetch_all 输出同一套标准字段名，
    cleaner/adapter/build 全部复用。
    股本不可靠（港股报表股本单位特殊），用 股东应占溢利 / 每股基本盈利 反推。
    """
    code = _hk_code(code)

    ps = fetch_hk_profit_sheet(code)
    bs = fetch_hk_balance_sheet(code)
    cf = fetch_hk_cash_flow(code)
    fi = fetch_hk_financial_indicator(code)

    data: dict[str, pd.DataFrame] = {}

    # 港股财报（东财 stock_financial_hk_report_em + 财务指标）原生即为人民币口径
    # （腾讯/泡泡玛特等以人民币为记账本位币，东财直接返回人民币值），无需任何汇率换算。
    # 市值/股价（估值+行情）原生为港元，保持原样；PE/PB 计算时由 adapter 标注双币种。

    # 股本反推：share_capital = 股东应占溢利 / 每股基本盈利（人民币元）
    # cleaner 会再 ÷1e8 转「亿元/亿股」，此处保持「元」口径对齐 A 股 share_capital
    if not fi.empty and {"net_profit_parent", "eps"}.issubset(fi.columns):
        hp = pd.to_numeric(fi["net_profit_parent"], errors="coerce")
        eps = pd.to_numeric(fi["eps"], errors="coerce")
        shares = (hp / eps.where(eps != 0)).where(eps != 0)  # 股
        fi["share_capital"] = shares  # 元口径（面值1元，故=股数）
        # 合并到 balance_sheet 供 cleaner 使用
        if not bs.empty:
            sc = fi[["report_date", "share_capital"]].copy()
            bs = bs.merge(sc, on="report_date", how="left")

    if not ps.empty:
        data["profit_sheet"] = ps
    if not bs.empty:
        data["balance_sheet"] = bs
    if not cf.empty:
        data["cash_flow"] = cf
    if not fi.empty:
        data["financial_indicator"] = fi

    dv = fetch_hk_dividend(code)
    if dv is not None and not dv.empty:
        # 每股股息已从分红文本提取为人民币口径，无需再换算
        data["dividend"] = dv

    val = fetch_hk_valuation(code)
    if val is not None:
        data["valuation"] = val

    quote = fetch_quote(code, market="hk")
    if quote is not None:
        data["quote"] = quote

    rating = fetch_hk_rating(code)
    if rating is not None:
        data["rating"] = rating

    # 竞争地位（港股降级：行业定位 + 公司介绍，无营收排名）
    hk_profile = fetch_hk_profile(code)
    if hk_profile is not None:
        data["competition"] = hk_profile

    return data
