# -*- coding: utf-8 -*-
"""宏观指标定义表 —— 看板与小红书内容的**唯一真源**。

为什么合成一张表
----------------
本项目踩过「同一业务口径两套实现」的坑（memory：watchlist 的 PE 在两处各算一遍，
界面上同一标的显示成两个值）。宏观指标的展示名、单位、数据源、口径说明、变化量口径
如果散在渲染端，一定会漂移 —— 所以这里集中定义，渲染端只做「按 group 分组、按 key 取数」。

新增指标的唯一正确姿势
----------------------
在本文件加一条 `Indicator`，并在 `GROUP_ORDER` 对应的分组里出现。渲染端**不需要改**。

三个字段最容易被写错，说明如下
------------------------------
- `chg_unit`：变化量口径（`bp` 收益率基点 / `pct` 百分点 / `%` 相对变化率）。见 `derive` 模块。
- `clock`：是否参与周期定位（`growth` 增长侧 / `inflation` 通胀侧 / `""` 不参与）。
  参与判定的指标必须**频率与口径可比**（都是同比或都是指数），混入点位类指标会污染象限。
- `note`：页面上必须显示的额外提示（如「该接口滞后 5 个月」）。数据有缺口时**不许静默展示**。

🔴 单位一律保持**数据源原样**，换算（元→亿元、盎司→吨）在渲染端做且必须写明。
   本项目已有教训：raw 层量纲不统一是历史包袱，宏观层不要重蹈覆辙 ——
   所以这里的 `unit` 字段记的就是接口给的原生单位。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from . import derive as D
from . import source as S

# 分组（渲染端按此顺序排列）
G_WATER = "水位"
G_VAL = "估值"
G_GROWTH = "增长"
G_MARKET = "情绪"
GROUP_ORDER = (G_WATER, G_VAL, G_GROWTH, G_MARKET)

GROUP_TITLE = {
    G_WATER: "全球水位 · 利率与汇率",
    G_VAL: "估值与位置 · A 股贵不贵",
    G_GROWTH: "周期与增长 · 中国基本面",
    G_MARKET: "市场与情绪 · 行情与风险偏好",
}

GROUP_SUB = {
    G_WATER: "别的指标告诉你「贵不贵」，利率和汇率告诉你「钱会不会走」",
    G_VAL: "绝对水平没有意义，一律看历史分位",
    G_GROWTH: "领先指标（M1、社融）看方向，滞后指标（GDP）只作确认",
    G_MARKET: "行情、波动率与杠杆资金",
}


@dataclass(frozen=True)
class Indicator:
    """一条宏观指标的定义。"""

    key: str
    name: str
    unit: str
    group: str
    freq: str                       # 日 / 月 / 季
    source: str                     # AKShare 接口名（可追溯）
    caliber: str                    # 口径说明（展示在页面底部的数据说明里）
    loader: Callable[[], pd.Series]
    chg_unit: str = "%"             # bp / pct / %
    clock: str = ""                 # "" / growth / inflation
    short: str = ""                 # 图卡上用的极短名
    note: str = ""                  # 必须显示的额外提示
    snapshot: bool = False          # 单点快照（无历史序列，不参与分位与走势）

    @property
    def label(self) -> str:
        return self.short or self.name


# --------------------------------------------------------------------------- #
# 小工具：把零散接口的取数逻辑包成 loader
# --------------------------------------------------------------------------- #

def _col(df_name: str):
    """`lambda df: df[col]` 的语法糖，避免定义处满屏 lambda。"""
    return lambda df: df[df_name]


def _single_point(value: float, period: str) -> pd.Series:
    """构造单点序列（快照类指标用）。"""
    return pd.Series([float(value)], index=[period])


# --------------------------------------------------------------------------- #
# ① 全球水位 · 利率与汇率
# --------------------------------------------------------------------------- #

def _cn_us_10y() -> tuple[pd.Series, pd.Series]:
    return S.cn_us_10y()


def _cn_us_2y() -> tuple[pd.Series, pd.Series]:
    return S.cn_us_2y()


def _load_cn_10y() -> pd.Series:
    return S.cn_us_10y()[0]


def _load_us_10y() -> pd.Series:
    return S.cn_us_10y()[1]


def _load_us_2y() -> pd.Series:
    return S.cn_us_2y()[1]


def _load_cn_us_spread() -> pd.Series:
    """中美 10Y 利差 = 美国 − 中国（pct）。

    ⚠️ 用 `align_diff` 而不是各自取最新值相减：两者交易日历不同（美国假日 A 股开市），
       不对齐会出现「拿美国的 9/22 减中国的 9/21」这种错值，且不报错。
    """
    cn, us = _cn_us_10y()
    return D.align_diff(us, cn)


def _load_credit_spread() -> pd.Series:
    """信用利差 = 中债 AAA 银行债 10Y − 国债券 10Y（pct）。

    企业融资环境的温度计：走阔 = 信用风险溢价上升 / 银行间流动性收紧。
    """
    aaa = S.china_yield_curve("AAA", "10年")
    gov = S.china_yield_curve("国债", "10年")
    return D.align_diff(aaa, gov)


def _load_lpr(tenor: str) -> pd.Series:
    df = S.fetch_lpr()
    return S.to_series(df, "TRADE_DATE", tenor)


# --------------------------------------------------------------------------- #
# ② 估值与位置
# --------------------------------------------------------------------------- #

def _load_a_pe(field: str) -> pd.Series:
    df = S.fetch_a_ttm_lyr()
    return S.to_series(df, "date", field)


def _load_a_pb(field: str) -> pd.Series:
    df = S.fetch_a_all_pb()
    return S.to_series(df, "date", field)


def a_pe_median_rank() -> tuple[float | None, str]:
    """全 A 中位 PE(TTM) 的**接口自带近 10 年分位**（0~1）与其日期。

    ⚠️ 这是**有意保留的口径特例**：接口只在最后一行给分位，且该分位基于**近 10 年**全历史；
       而 `derive.pct_rank` 算的是「本次拉取窗口（近 1 年）内的分位」。
       两者窗口不同，页面上分别标注 —— 不要为了「统一」而丢掉信息量更大的 10 年分位。
    """
    df = S.fetch_a_ttm_lyr()
    if df.empty:
        return None, ""
    last = df.iloc[-1]
    r = S._to_float(last.get("quantileInRecent10YearsMiddlePeTtm"))
    if pd.isna(r):
        return None, ""
    return float(r), S.norm_period(last.get("date"))


def a_pb_median_rank() -> tuple[float | None, str]:
    """全 A 中位 PB 的接口自带近 10 年分位。"""
    df = S.fetch_a_all_pb()
    if df.empty:
        return None, ""
    last = df.iloc[-1]
    r = S._to_float(last.get("quantileInRecent10YearsMiddlePB"))
    if pd.isna(r):
        return None, ""
    return float(r), S.norm_period(last.get("date"))


def _load_sse_pe() -> pd.Series:
    """上交所股票平均市盈率（单点快照）。"""
    df = S.fetch_sse_summary()
    hit = df[df["项目"].astype(str).str.contains("平均市盈率", regex=False)]
    if hit.empty:
        raise ValueError("上交所概况未找到「平均市盈率」")
    period = S.norm_period(df[df["项目"].astype(str).str.contains("报告时间", regex=False)].iloc[0]["股票"])
    return _single_point(S._to_float(hit.iloc[0]["股票"]), period or "")


def _load_sse_market_cap() -> pd.Series:
    """上交所总市值（单点快照，亿元）。"""
    df = S.fetch_sse_summary()
    hit = df[df["项目"].astype(str) == "总市值"]
    if hit.empty:
        raise ValueError("上交所概况未找到「总市值」")
    period = S.norm_period(df[df["项目"].astype(str).str.contains("报告时间", regex=False)].iloc[0]["股票"])
    return _single_point(S._to_float(hit.iloc[0]["股票"]), period or "")


def _load_csi300_pe() -> pd.Series:
    df = S.fetch_csindex_value("000300")
    return S.to_series(df, "日期", "市盈率1")


def _load_csi300_div() -> pd.Series:
    df = S.fetch_csindex_value("000300")
    return S.to_series(df, "日期", "股息率1")


def _load_erp_median() -> pd.Series:
    """ERP（中位数法）= 1 / 全A中位PE − 10Y 国债。"""
    pe = _load_a_pe("middlePETTM")
    return D.erp(pe, _load_cn_10y())


def _load_erp_csi300() -> pd.Series:
    """ERP（整体法）= 1 / 沪深300 PE − 10Y 国债。"""
    return D.erp(_load_csi300_pe(), _load_cn_10y())


def _load_market_cap() -> pd.Series:
    """沪深两市合计市价总值（月度，亿元）。"""
    df = S.fetch_market_cap()
    sh = S.to_series(df, "数据日期", "市价总值-上海")
    sz = S.to_series(df, "数据日期", "市价总值-深圳")
    return (sh.add(sz, fill_value=0)).sort_index()


def _load_gdp_ttm() -> pd.Series:
    """滚动 4 季 GDP（亿元）—— 巴菲特指标的分母。"""
    return D.rolling_four_quarter(_load_gdp_abs())


def _load_buffett() -> pd.Series:
    """巴菲特指标 = 沪深总市值 / 滚动 4 季 GDP（%）。

    ⚠️ 分子（月度）与分母（季度）频率不同，`align_ratio` 走 inner join：
       只有双方都有数的期间才出值。所以最新点通常是「上一个已结束的季度末」。

    🔴 跨市场不可比：本指标只在 A 股内部做时间序列比较，不与美股并列。
    """
    mv = _load_market_cap()
    gdp = _load_gdp_ttm()
    # 市值月度 → 只保留与季度末重合的期间：把季度期间 'YYYYQn' 展开成该季末月份 'YYYY-MM'
    q_to_m = {q: _quarter_end_month(q) for q in gdp.index}
    gdp_m = pd.Series({q_to_m[q]: v for q, v in gdp.items() if q_to_m.get(q)}).sort_index()
    return D.align_ratio(mv, gdp_m, scale=100.0)


def _quarter_end_month(quarter: str) -> str:
    """'2026Q2' → '2026-06'（该季度最后一个月）。"""
    if "Q" not in quarter:
        return ""
    y, q = quarter.split("Q")
    try:
        return f"{y}-{int(q) * 3:02d}"
    except ValueError:
        return ""


# --------------------------------------------------------------------------- #
# ③ 周期与增长（中国）
# --------------------------------------------------------------------------- #

def _load_gdp_abs() -> pd.Series:
    df = S.fetch_china_gdp()
    return S.to_series(df, "季度", "国内生产总值-绝对值")


def _load_gdp_yoy() -> pd.Series:
    df = S.fetch_china_gdp()
    return S.to_series(df, "季度", "国内生产总值-同比增长")


def _load_cpi_yoy() -> pd.Series:
    df = S.fetch_china_cpi()
    return S.to_series(df, "月份", "全国-同比增长")


def _load_cpi_mom() -> pd.Series:
    df = S.fetch_china_cpi()
    return S.to_series(df, "月份", "全国-环比增长")


def _load_money(field: str) -> pd.Series:
    df = S.fetch_china_money_supply()
    return S.to_series(df, "月份", field)


def _load_m1_m2_gap() -> pd.Series:
    """M1 − M2 剪刀差（pct）。

    中国市场最经典的流动性指标：M1 = 企业活期（随时能动），M2 = M1 + 定期。
    剪刀差**收窄/转正 = 资金活化 = 经济回暖**。当前长期为负（定期化）。

    ⚠️ 用 `align_diff` 对齐后相减，不用各自最新值 —— 偶有一方多一期。
    """
    return D.align_diff(_load_money("货币(M1)-同比增长"), _load_money("货币和准货币(M2)-同比增长"))


def _load_shrzgm() -> pd.Series:
    """社会融资规模**滚动 12 个月增量合计**（亿元）。

    🔴 有意**不展示单月增量**：社融单月值季节性极强（1 月天量、4 月低谷 ——
       实测 2026-03 为 5.22 万亿、2026-04 骤降至 0.62 万亿，环比「−88%」），
       用相对变化率展示单月值会把季节性误读成信用收缩。
       滚动 12 个月合计是宏观分析的标准做法，平滑掉季节性后方向才可读。
    """
    df = S.fetch_china_shrzgm()
    s = S.to_series(df, "月份", "社会融资规模增量")
    if len(s) < 12:
        raise ValueError(f"社融序列仅 {len(s)} 期，不足以算滚动 12 个月")
    return s.rolling(12).sum().dropna()


def _load_electricity_yoy() -> pd.Series:
    df = S.fetch_china_electricity()
    return S.to_series(df, "统计时间", "全社会用电量同比")


def _load_boom_index() -> pd.Series:
    df = S.fetch_china_boom_index()
    return S.to_series(df, "季度", "企业景气指数-指数")


def _load_retail_yoy() -> pd.Series:
    df = S.fetch_china_retail()
    return S.to_series(df, "月份", "同比增长")


def _load_unemployment() -> pd.Series:
    """全国城镇调查失业率（%）—— 长表按 item 精确筛出「全国」口径。

    ⚠️ 该接口是长表（date / item / value），且 item 含结尾空格，必须 strip 后精确匹配；
       用 `contains('失业率')` 会同时命中「25-59 岁」「本地户籍」「外来户籍」等多条。
    """
    df = S.fetch_china_unemployment()
    items = df["item"].astype(str).str.strip()
    hit = df[items == "全国城镇调查失业率"]
    if hit.empty:
        raise ValueError("失业率长表未找到「全国城镇调查失业率」")
    return S.to_series(hit, "date", "value")


def _load_house_price() -> pd.Series:
    """北京、上海新建商品住宅价格指数同比的均值（%）。

    🔴 **口径必须如实写「京沪」**：AKShare 1.18.94 的 `macro_china_new_house_price`
       实测**只回传北京、上海两市**（376 行 ÷ 188 个月 = 每期 2 城），不是文档暗示的 70 城。
       名字与实际不符比缺数据更糟 —— 所以指标名叫「京沪住宅价格同比」，不叫「70 城」。

    ⚠️ 取值为**上年同月 = 100 的指数**（97.7 表示同比 −2.3%），
       这里统一换算成**同比百分比**（指数 − 100），与其它同比类指标口径一致。
    """
    df = S.fetch_china_house_price()
    # ⚠️ 该接口的「日期」带日（2026-08-01），而其它月度指标归一后是「2026-08」。
    #    不截断到月会让同一批月度指标出现两种期间写法，排序/去重/对齐全部受影响。
    df = df.assign(_p=df["日期"].map(lambda d: S.norm_period(d)[:7]))
    df["_v"] = df["新建商品住宅价格指数-同比"].map(S._to_float)
    df = df[(df["_p"] != "") & df["_v"].notna()]
    if df.empty:
        raise ValueError("房价指数全部无效")
    return (df.groupby("_p")["_v"].mean().sort_index() - 100.0)


def _load_fx_reserves() -> pd.Series:
    df = S.fetch_china_fx_gold()
    return S.to_series(df, "月份", "国家外汇储备-数值")


def _load_gold_reserves() -> pd.Series:
    df = S.fetch_china_fx_gold()
    return S.to_series(df, "月份", "黄金储备-数值")


# --------------------------------------------------------------------------- #
# ④ 市场与情绪
# --------------------------------------------------------------------------- #

def _load_index_us(symbol: str) -> pd.Series:
    df = S.fetch_index_us(symbol)
    return S.to_series(df, "date", "close")


def _load_index_cn(symbol: str) -> pd.Series:
    df = S.fetch_index_cn(symbol)
    return S.to_series(df, "date", "close")


def _load_index_hk(symbol: str) -> pd.Series:
    df = S.fetch_index_hk(symbol)
    return S.to_series(df, "date", "close")


def _load_vhsi() -> pd.Series:
    """恒指波幅指数（单点快照）。

    ⚠️ 只能拿当期值：AKShare 无 VHSI 历史序列接口 → 该指标不参与分位与走势。
    """
    df = S.fetch_hk_index_spot()
    hit = df[df["代码"].astype(str).str.upper() == "VHSI"]
    if hit.empty:
        raise ValueError("港股指数快照未找到 VHSI")
    return _single_point(S._to_float(hit.iloc[0]["最新价"]), pd.Timestamp.today().strftime("%Y-%m-%d"))


def _load_qvix(fetcher) -> pd.Series:
    df = fetcher()
    return S.to_series(df, "date", "close")


def _load_breadth_net() -> pd.Series:
    """市场宽度：20 日新高家数 − 20 日新低家数（家）。

    净值比两个绝对数更能反映「赚钱效应」：牛市里高低同增时，净值为正。
    """
    df = S.fetch_high_low_stat()
    hi = S.to_series(df, "date", "high20")
    lo = S.to_series(df, "date", "low20")
    return D.align_diff(hi, lo)


def _load_margin() -> pd.Series:
    """沪市融资融券余额（亿元）。

    ⚠️ 接口原生单位是**元**（实测 1.36e12），这里除以 1e8 转亿元 ——
       转换写在这层而不是渲染端，并记进 `caliber`，避免两处各转一次。
    """
    df = S.fetch_margin_sh()
    return S.to_series(df, "日期", "融资融券余额") / 1e8


def _load_futures(symbol: str) -> pd.Series:
    df = S.fetch_foreign_futures(symbol)
    return S.to_series(df, "date", "close")


def _load_copper_gold() -> pd.Series:
    """铜金比 = 铜价 / 金价。

    经济景气的高频市场代理（比 PMI 实时）：铜代表工业需求，金代表避险。
    比值上行 = 市场在定价「增长 > 避险」。

    ⚠️ 两者交易时段/假期不同，`align_ratio` 走 inner join 只取共同交易日。
    """
    return D.align_ratio(_load_futures("HG"), _load_futures("GC"))


def _load_oil_yoy() -> pd.Series:
    """WTI 原油**同比**（%）—— 参与通胀侧定位。

    ⚠️ 这条序列是「日频价格」→「按年同比」的派生。做法是按日期对齐一年前的值：
       原油是全球通胀最直接的输入项，用绝对价格参与时钟判定没有可比性。
    """
    return _yoy_by_date(S.fetch_foreign_futures("CL"), "date", "close")


def _yoy_by_date(df: pd.DataFrame, date_col: str, value_col: str) -> pd.Series:
    """日频序列 → 同比（%）：与**去年的同一天**比（按日历年对齐）。

    ⚠️ 不能用 `shift(365)`：A 股/外盘的交易日不是连续日历年，
       365 个交易日前是 1.4 年前。这里用日期索引做「找去年最近的一个可用日期」，
       容忍 ±7 天误差（节假日错位）—— 原油同比是宏观量级判断，不要求逐日精确。
    """
    s = S.to_series(df, date_col, value_col)
    idx = pd.to_datetime(s.index, errors="coerce")
    good = s[~idx.isna()].copy()
    good.index = idx[~idx.isna()]
    good = good.sort_index()

    out: dict[str, float] = {}
    for ts, v in good.items():
        target = ts - pd.DateOffset(years=1)
        window = good.loc[target - pd.Timedelta(days=7): target + pd.Timedelta(days=7)]
        if window.empty:
            continue
        base = float(window.iloc[0])
        if base == 0 or (base < 0) != (v < 0):
            continue
        out[ts.strftime("%Y-%m-%d")] = (v / base - 1) * 100.0
    if not out:
        raise ValueError("同比：没有任何日期能找到去年的对照值")
    return pd.Series(out).sort_index()


# --------------------------------------------------------------------------- #
# 指标表
# --------------------------------------------------------------------------- #

INDICATORS: tuple[Indicator, ...] = (
    # ---------------- ① 水位 ----------------
    Indicator(
        key="cn_10y", name="中国 10 年期国债收益率", short="中国 10Y", unit="%",
        group=G_WATER, freq="日", source="ak.bond_zh_us_rate",
        caliber="中债国债到期收益率，日频，单位 %",
        loader=_load_cn_10y, chg_unit="bp",
    ),
    Indicator(
        key="us_10y", name="美国 10 年期国债收益率", short="美国 10Y", unit="%",
        group=G_WATER, freq="日", source="ak.bond_zh_us_rate",
        caliber="美债 10 年期收益率，日频，单位 %（全球资产定价的锚）",
        loader=_load_us_10y, chg_unit="bp",
    ),
    Indicator(
        key="cn_us_spread", name="中美 10 年期利差（美 − 中）", short="中美利差", unit="pct",
        group=G_WATER, freq="日", source="派生：ak.bond_zh_us_rate",
        caliber="美国 10Y − 中国 10Y，按共同交易日对齐；负值 = 中国利率更低",
        loader=_load_cn_us_spread, chg_unit="bp",
    ),
    Indicator(
        key="us_10y_2y", name="美债 10Y − 2Y 利差", short="美债 10Y-2Y", unit="pct",
        group=G_WATER, freq="日", source="ak.bond_zh_us_rate",
        caliber="期限利差；倒挂（< 0）是过去 50 年最可靠的衰退领先指标，领先 12–18 个月",
        loader=lambda: S.to_series(S.fetch_bond_zh_us_rate(), "日期", "美国国债收益率10年-2年"),
        chg_unit="bp",
    ),
    Indicator(
        key="credit_spread", name="中债 AAA 银行债 10Y − 国债 10Y", short="信用利差", unit="pct",
        group=G_WATER, freq="日", source="ak.bond_china_yield",
        caliber="中债商业银行普通债收益率曲线(AAA) 10Y − 中债国债券收益率曲线 10Y；走阔 = 信用溢价上升",
        loader=_load_credit_spread, chg_unit="bp",
    ),
    Indicator(
        key="shibor_3m", name="3 月期 SHIBOR", short="SHIBOR 3M", unit="%",
        group=G_WATER, freq="日", source="ak.rate_interbank",
        caliber="上海银行间同业拆放利率 3 月期，日频，单位 %",
        loader=lambda: S.to_series(S.fetch_shibor(), "报告日", "利率"), chg_unit="bp",
    ),
    Indicator(
        key="lpr_1y", name="LPR 1 年期", short="LPR 1Y", unit="%",
        group=G_WATER, freq="月", source="ak.macro_china_lpr",
        caliber="贷款市场报价利率（全国银行间同业拆借中心），月度报价", loader=lambda: _load_lpr("LPR1Y"),
        chg_unit="bp",
    ),
    Indicator(
        key="lpr_5y", name="LPR 5 年期以上", short="LPR 5Y", unit="%",
        group=G_WATER, freq="月", source="ak.macro_china_lpr",
        caliber="5 年期以上 LPR，房贷利率的定价基准，月度报价", loader=lambda: _load_lpr("LPR5Y"),
        chg_unit="bp",
    ),
    Indicator(
        key="usd_cny", name="美元兑人民币（央行中间价）", short="USD/CNY", unit="元/100美元",
        group=G_WATER, freq="日", source="ak.currency_boc_sina(美元)",
        caliber="中国银行外汇牌价「央行中间价」，每 100 美元折人民币元；回传近约 400 日",
        loader=lambda: S.boc_fx_mid("美元"), chg_unit="%",
    ),
    Indicator(
        key="usd_jpy", name="美元兑日元（由中间价折算）", short="USD/JPY", unit="日元/美元",
        group=G_WATER, freq="日", source="派生：ak.currency_boc_sina(美元/日元)",
        caliber="由美元、日元的央行中间价交叉折算（美元中间价 ÷ 日元中间价 × 100）",
        loader=lambda: _load_usd_jpy(), chg_unit="%",
        note="日元急升是全球套息交易去杠杆的经典信号；本值由两组中间价折算，非直接报价",
    ),
    Indicator(
        key="eur_cny", name="欧元兑人民币（央行中间价）", short="EUR/CNY", unit="元/100欧元",
        group=G_WATER, freq="日", source="ak.currency_boc_sina(欧元)",
        caliber="每 100 欧元折人民币元；回传近约 400 日",
        loader=lambda: S.boc_fx_mid("欧元"), chg_unit="%",
    ),
    # ---------------- ② 估值 ----------------
    Indicator(
        key="a_pe_median", name="全 A 市盈率中位数（TTM）", short="全A PE中位", unit="倍",
        group=G_VAL, freq="日", source="ak.stock_a_ttm_lyr",
        caliber="全部 A 股 PE(TTM) 的中位数（剔除亏损股的极端值影响）；接口仅回传近 1 年日频",
        loader=lambda: _load_a_pe("middlePETTM"),
        note="分位取接口自带的近 10 年分位（窗口长于本序列）",
    ),
    Indicator(
        key="a_pe_avg", name="全 A 市盈率平均数（TTM）", short="全A PE均值", unit="倍",
        group=G_VAL, freq="日", source="ak.stock_a_ttm_lyr",
        caliber="平均法口径，受高价股/微利股拉高，与中位数法差异即为「口径差」的直观体现",
        loader=lambda: _load_a_pe("averagePETTM"),
    ),
    Indicator(
        key="a_pb_median", name="全 A 市净率中位数", short="全A PB中位", unit="倍",
        group=G_VAL, freq="日", source="ak.stock_a_all_pb",
        caliber="全部 A 股 PB 的中位数；接口仅回传近 1 年日频",
        loader=lambda: _load_a_pb("middlePB"),
        note="分位取接口自带的近 10 年分位",
    ),
    Indicator(
        key="csi300_pe", name="沪深 300 市盈率", short="沪深300 PE", unit="倍",
        group=G_VAL, freq="日", source="ak.stock_zh_index_value_csindex(000300)",
        caliber="中证指数公司官方口径「市盈率1」（整体法，剔除亏损）；更新节奏慢于行情，以数据日期为准",
        loader=_load_csi300_pe, note="中证官方源更新节奏慢，请看数据日期",
    ),
    Indicator(
        key="csi300_div", name="沪深 300 股息率", short="沪深300 股息率", unit="%",
        group=G_VAL, freq="日", source="ak.stock_zh_index_value_csindex(000300)",
        caliber="中证指数公司官方口径「股息率1」，近 12 个月分红 / 当前市值",
        loader=_load_csi300_div, chg_unit="pct",
    ),
    Indicator(
        key="sse_pe", name="上交所股票平均市盈率", short="上交所整体PE", unit="倍",
        group=G_VAL, freq="日", source="ak.stock_sse_summary",
        caliber="上交所官方公布的股票平均市盈率（整体法）；该接口为「当日快照」，无历史序列",
        loader=_load_sse_pe, snapshot=True,
    ),
    Indicator(
        key="erp_median", name="股权风险溢价 ERP（中位数法）", short="ERP 中位法", unit="%",
        group=G_VAL, freq="日", source="派生：1 / 全A中位PE − 10Y国债",
        caliber="ERP = 1/PE − 10 年期国债收益率（A 股圈称「格雷厄姆指数」的倒数形态）；越高 = 股票相对债券越便宜",
        loader=_load_erp_median, chg_unit="pct",
    ),
    Indicator(
        key="erp_csi300", name="股权风险溢价 ERP（沪深 300）", short="ERP 沪深300", unit="%",
        group=G_VAL, freq="日", source="派生：1 / 沪深300PE − 10Y国债",
        caliber="与中位数法口径的差异来自 PE 口径本身；受中证源滞后影响",
        loader=_load_erp_csi300, chg_unit="pct", note="受中证官方源滞后影响",
    ),
    Indicator(
        key="buffett_cn", name="巴菲特指标（A 股证券化率）", short="巴菲特指标", unit="%",
        group=G_VAL, freq="季", source="派生：ak.macro_china_stock_market_cap ÷ ak.macro_china_gdp",
        caliber="沪深两市总市值 ÷ 滚动 4 季 GDP；滚 4 季 = 今年累计 + 去年全年 − 去年同期累计",
        loader=_load_buffett, chg_unit="pct",
        note="🔴 跨市场不可比：各国融资结构不同，本指标只与自身历史比较",
    ),
    # ---------------- ③ 增长 ----------------
    Indicator(
        key="m1_yoy", name="M1 同比", short="M1 同比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_money_supply",
        caliber="狭义货币供应量同比；M1 含企业活期存款，是资金活化程度的直接体现",
        loader=lambda: _load_money("货币(M1)-同比增长"), chg_unit="pct", clock="growth",
    ),
    Indicator(
        key="m2_yoy", name="M2 同比", short="M2 同比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_money_supply",
        caliber="广义货币供应量同比",
        loader=lambda: _load_money("货币和准货币(M2)-同比增长"), chg_unit="pct",
    ),
    Indicator(
        key="m1_m2_gap", name="M1 − M2 剪刀差", short="M1-M2 剪刀差", unit="pct",
        group=G_GROWTH, freq="月", source="派生：ak.macro_china_money_supply",
        caliber="M1 同比 − M2 同比，单位百分点；为负 = 资金定期化（企业不扩产）；向零收窄 = 资金活化",
        loader=_load_m1_m2_gap, chg_unit="pct", clock="growth",
    ),
    Indicator(
        key="gdp_yoy", name="GDP 同比", short="GDP 同比", unit="%",
        group=G_GROWTH, freq="季", source="ak.macro_china_gdp",
        caliber="国内生产总值不变价同比（季度累计口径）；滞后指标，仅作确认",
        loader=_load_gdp_yoy, chg_unit="pct", clock="growth", note="季度滞后指标",
    ),
    Indicator(
        key="electricity_yoy", name="全社会用电量同比", short="用电量同比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_society_electricity",
        caliber="全社会用电量同比（「克强指数」核心，统计口径难修饰）",
        loader=_load_electricity_yoy, chg_unit="pct", clock="growth",
    ),
    Indicator(
        key="retail_yoy", name="社会消费品零售总额同比", short="社零同比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_consumer_goods_retail",
        caliber="当月社零同比；国内需求侧的月度同步指标",
        loader=_load_retail_yoy, chg_unit="pct", clock="growth",
    ),
    Indicator(
        key="boom_index", name="企业景气指数", short="企业景气", unit="",
        group=G_GROWTH, freq="季", source="ak.macro_china_enterprise_boom_index",
        caliber="央行 5000 户工业企业调查，>100 为景气区间；季度",
        loader=_load_boom_index, chg_unit="pct", clock="growth",
        note="该接口的「企业家信心指数」系列自 2024 起已停更，仅「企业景气指数」仍在更新",
    ),
    Indicator(
        key="shrzgm", name="社会融资规模（滚动 12 个月合计）", short="社融 12M 合计", unit="亿元",
        group=G_GROWTH, freq="月", source="ak.macro_china_shrzgm",
        caliber="近 12 个月社会融资规模增量合计（非单月值，滚动口径以平滑季节性）；中国信用周期的核心指标",
        loader=_load_shrzgm, clock="",
        note="🔴 该接口实测滞后约 5 个月；展示为滚动 12 个月合计而非单月增量（单月季节性极强）",
    ),
    Indicator(
        key="cpi_yoy", name="CPI 同比", short="CPI 同比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_cpi",
        caliber="居民消费价格指数同比（上年同月 = 100 换算），单位 %",
        loader=_load_cpi_yoy, chg_unit="pct", clock="inflation",
    ),
    Indicator(
        key="cpi_mom", name="CPI 环比", short="CPI 环比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_cpi",
        caliber="CPI 环比（上月 = 100 换算）",
        loader=_load_cpi_mom, chg_unit="pct",
    ),
    Indicator(
        key="unemployment", name="城镇调查失业率", short="调查失业率", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_urban_unemployment",
        caliber="全国城镇调查失业率（16–24 岁等分口径为另一组数据，未纳入）",
        loader=_load_unemployment, chg_unit="pct",
    ),
    Indicator(
        key="house_price_70", name="京沪新建住宅价格同比", short="京沪房价同比", unit="%",
        group=G_GROWTH, freq="月", source="ak.macro_china_new_house_price",
        caliber="北京、上海两市新建商品住宅价格指数同比的均值（已由「上年同月=100」换算为 %）",
        loader=_load_house_price, chg_unit="pct",
        note="🔴 该接口实测仅回传京沪两市（非 70 城），口径以「京沪」为准",
    ),
    Indicator(
        key="fx_reserves", name="外汇储备", short="外汇储备", unit="亿美元",
        group=G_GROWTH, freq="月", source="ak.macro_china_fx_gold",
        caliber="国家外汇储备余额，单位亿美元",
        loader=_load_fx_reserves,
    ),
    Indicator(
        key="gold_reserves", name="黄金储备", short="黄金储备", unit="万盎司",
        group=G_GROWTH, freq="月", source="ak.macro_china_fx_gold",
        caliber="官方黄金储备，单位万盎司；央行购金是近年重要的宏观叙事",
        loader=_load_gold_reserves,
    ),
    # ---------------- ④ 市场与情绪 ----------------
    Indicator(
        key="idx_sh", name="上证指数", short="上证指数", unit="点",
        group=G_MARKET, freq="日", source="ak.stock_zh_index_daily(sh000001)",
        caliber="上证综合指数收盘价",
        loader=lambda: _load_index_cn("sh000001"),
    ),
    Indicator(
        key="idx_sz", name="深证成指", short="深证成指", unit="点",
        group=G_MARKET, freq="日", source="ak.stock_zh_index_daily(sz399001)",
        caliber="深证成份指数收盘价",
        loader=lambda: _load_index_cn("sz399001"),
    ),
    Indicator(
        key="idx_csi300", name="沪深 300", short="沪深300", unit="点",
        group=G_MARKET, freq="日", source="ak.stock_zh_index_daily(sz399300)",
        caliber="沪深 300 指数收盘价",
        loader=lambda: _load_index_cn("sz399300"),
    ),
    Indicator(
        key="idx_hsi", name="恒生指数", short="恒生指数", unit="点",
        group=G_MARKET, freq="日", source="ak.stock_hk_index_daily_sina(HSI)",
        caliber="恒生指数收盘价",
        loader=lambda: _load_index_hk("HSI"),
    ),
    Indicator(
        key="idx_spx", name="标普 500", short="标普500", unit="点",
        group=G_MARKET, freq="日", source="ak.index_us_stock_sina(.INX)",
        caliber="标普 500 指数收盘价（新浪美股源）",
        loader=lambda: _load_index_us(".INX"),
    ),
    Indicator(
        key="oil_wti", name="WTI 原油", short="WTI 原油", unit="美元/桶",
        group=G_MARKET, freq="日", source="ak.futures_foreign_hist(CL)",
        caliber="纽约轻质原油期货连续合约收盘价",
        loader=lambda: _load_futures("CL"),
    ),
    Indicator(
        key="oil_yoy", name="WTI 原油同比", short="原油同比", unit="%",
        group=G_MARKET, freq="日", source="派生：ak.futures_foreign_hist(CL)",
        caliber="WTI 原油价格与上年同期比（按日历日期对齐，容忍 ±7 天节假日错位）",
        loader=_load_oil_yoy, chg_unit="pct", clock="inflation",
    ),
    Indicator(
        key="copper_gold", name="铜金比", short="铜金比", unit="",
        group=G_MARKET, freq="日", source="派生：ak.futures_foreign_hist(HG) ÷ (GC)",
        caliber="COMEX 铜价 ÷ 金价；上行 = 市场定价「工业需求 > 避险需求」，是比 PMI 更实时的景气代理",
        loader=_load_copper_gold,
    ),
    Indicator(
        key="qvix_50", name="50ETF 期权隐含波动率", short="50ETF 波指", unit="",
        group=G_MARKET, freq="日", source="ak.index_option_50etf_qvix",
        caliber="上证 50ETF 期权隐含波动率（中国版「恐慌指数」），日频",
        loader=lambda: _load_qvix(S.fetch_qvix_50etf),
    ),
    Indicator(
        key="qvix_300", name="300ETF 期权隐含波动率", short="300ETF 波指", unit="",
        group=G_MARKET, freq="日", source="ak.index_option_300etf_qvix",
        caliber="沪深 300ETF 期权隐含波动率；该接口早期数值为 NaN，仅近年有效",
        loader=lambda: _load_qvix(S.fetch_qvix_300etf),
    ),
    Indicator(
        key="vhsi", name="恒指波幅指数", short="VHSI", unit="",
        group=G_MARKET, freq="日", source="ak.stock_hk_index_spot_sina",
        caliber="恒生指数波幅指数（港股版恐慌指数）；该接口为快照，无历史序列",
        loader=_load_vhsi, snapshot=True,
    ),
    Indicator(
        key="breadth_net", name="20 日新高 − 新低家数", short="新高-新低", unit="家",
        group=G_MARKET, freq="日", source="派生：ak.stock_a_high_low_statistics",
        caliber="创 20 日新高的 A 股家数 − 创 20 日新低的家数；正 = 赚钱效应扩散",
        loader=_load_breadth_net,
    ),
    Indicator(
        key="margin_balance", name="沪市融资融券余额", short="两融余额", unit="亿元",
        group=G_MARKET, freq="日", source="ak.macro_china_market_margin_sh",
        caliber="沪市融资融券余额合计（接口原生单位为元，已 ÷1e8 转亿元）",
        loader=_load_margin,
    ),
)

BY_KEY: dict[str, Indicator] = {ind.key: ind for ind in INDICATORS}


def by_group(group: str) -> list[Indicator]:
    """取某分组下的全部指标（保持定义顺序）。"""
    return [ind for ind in INDICATORS if ind.group == group]


def clock_side(side: str) -> list[Indicator]:
    """取参与周期定位某一侧的指标（growth / inflation）。"""
    return [ind for ind in INDICATORS if ind.clock == side]


# --------------------------------------------------------------------------- #
# usd_jpy：由两组中间价交叉折算
# --------------------------------------------------------------------------- #

def _load_usd_jpy() -> pd.Series:
    """美元兑日元 = 美元中间价 ÷ 日元中间价。

    ⚠️ 中行牌的折算价是「每 100 外币折人民币元」，但**两者量纲相同**，
       相除时那个 100 自动抵消，不能再乘：
       美元 674.68（元/100 美元）÷ 日元 4.2754（元/100 日元）= 157.8
       （首版多乘了一个 100，算出 15,780 日元/美元的荒唐值 ——
       量纲不一致是这类交叉汇率最常见的错法，所以这里把算式写死在注释里）
    """
    usd = S.boc_fx_mid("美元")
    jpy = S.boc_fx_mid("日元")
    joined = pd.concat([usd.rename("usd"), jpy.rename("jpy")], axis=1, join="inner").dropna()
    joined = joined[joined["jpy"] != 0]
    if joined.empty:
        raise ValueError("美元/日元中间价没有共同日期")
    return (joined["usd"] / joined["jpy"]).sort_index()
