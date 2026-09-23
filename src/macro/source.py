# -*- coding: utf-8 -*-
"""宏观数据源封装：AKShare 接口 → 标准化时间序列。

统一约定（三条，本模块所有函数必须遵守）
----------------------------------------
1. **返回 `pd.Series`**，index 为归一化期间字符串（`2026-08` / `2026Q2` / `2026-09-22`），
   且**按期间升序**排列、值为 float。

   ⚠️ 各接口原始排序方向**不一致**，必须显式排序：实测
   `macro_china_gdp` 是**降序**（head 是最新季），
   `macro_china_society_electricity` 是**升序**（head 是 2003 年）。
   若默认升序，GDP 的「上一期」会取到 2006 年，且**不报错**。

2. **末尾的 NaN 期必须剔除**。`macro_china_stock_market_cap` 的当月行
   全列 NaN（月度数据当月未结束），`macro_china_fx_gold` 早期的黄金储备也是 NaN。
   不剔除则「最新值」= NaN，全链路静默产出空指标。

3. **失败抛异常**，由 `build.py` 决定降级策略。本层不做 try-except 吞异常 ——
   静默返回空序列会让「接口挂了」和「这个指标本来就没数据」长得一模一样。

口径提醒（会原样展示在页面上，别在这里做「加工」）
--------------------------------------------------
- 金额单位基本是**原样**（亿元 / 万美元 / 万盎司），换算是展示层的事。
- 「同比」类字段直接取接口给的值，**不自己换算**（接口口径与统计局一致）。
"""
from __future__ import annotations

import functools
import re
from datetime import date, timedelta

import pandas as pd

from ..data import fetcher as _fetcher  # noqa: F401  ⚠️ 必须先导入：它负责配置网络出口
from ..data.retry import retry
import akshare as ak

# --------------------------------------------------------------------------- #
# 单次构建内的接口缓存
# --------------------------------------------------------------------------- #
# 同一次构建会重复命中同一接口（如 `stock_a_ttm_lyr` 被「中位 PE / 均值 PE / 近 10 年分位」
# 三处调用，`bond_zh_us_rate` 被中美国债四处调用）。不缓存则一次构建多花十几秒，
# 且**多源口的数据版本可能不一致**（同一接口两次调用返回不同快照）。
#
# ⚠️ 有意**不用** `functools.lru_cache`：那是进程级的，会让「同一进程内连续构建两次」
#    拿到第一次的陈旧数据（服务模式下就是永久陈旧），且失效条件不可见。
#    这里用显式 dict + `clear_cache()`，由 `build.build_payload()` 在每次构建开头清空。
_FETCH_CACHE: dict[tuple, object] = {}


def clear_cache() -> None:
    """清空接口缓存。**每次构建开头必须调用**（`build_payload` 已代为调用）。"""
    _FETCH_CACHE.clear()


def cached(fn):
    """把 fetch 函数的结果在一次构建内缓存（异常不缓存，交由 `retry` 处理）。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        if key not in _FETCH_CACHE:
            _FETCH_CACHE[key] = fn(*args, **kwargs)
        return _FETCH_CACHE[key]
    return wrapper


#: 单接口墙钟上限（秒）。取值考虑：正常接口实测 P95 约 4s，
#: 最慢的 `macro_china_shrzgm` / `stock_zh_hk_*` 约 10s —— 30s 不会误杀，但能挡住挂死。
DEFAULT_TIMEOUT_S = 30.0


def timeout(seconds: float = DEFAULT_TIMEOUT_S):
    """给接口调用加**墙钟超时**，超时抛 `TimeoutError`（可被 `retry` 重试）。

    🔴 为什么必须有：akshare 多个接口的内部 timeout 默认 `None` = **无限等待**，
       网络异常时连接会被静默挂死（既不返回也不报错）。
       实测教训：本模块首版没有超时保护，一次构建**卡住 6 分钟无任何输出**
       —— 与 `fetcher._call_with_timeout` 文档里记录的「日 K 卡死 9 分钟」同类。
       没有超时的代价不是「慢」，而是「永远不结束且看不出卡在哪」。

    实现复用 `fetcher._call_with_timeout`（守护线程 + join 超时）：
    超时后线程被放弃，不影响进程退出。
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = _fetcher._call_with_timeout(fn, seconds, *args, **kwargs)
            if result is _fetcher._TIMEOUT:
                raise TimeoutError(f"{fn.__name__} 超过 {seconds:g}s 未返回（接口挂死）")
            return result
        return wrapper
    return decorator

# --------------------------------------------------------------------------- #
# 期间归一化
# --------------------------------------------------------------------------- #

_QUARTER_RE = re.compile(r"(\d{4})\D*?第\s*(\d+)(?:\s*-\s*(\d+))?\s*季度")
_MONTH_RE = re.compile(r"(\d{4})\D*?(\d{1,2})\s*月")
# ⚠️ 有意**不加 `$` 锚**：pandas 把日期列读成 datetime 后 `str()` 会带时间
#    （实测 `futures_foreign_hist` 的 date 是 `2026-09-23 00:00:00`），
#    加了锚就匹配不上、原样返回带时间的字符串，于是同一批指标里
#    「期间」有的是 `2026-09-23`、有的是 `2026-09-23 00:00:00`，排序与去重都会出错。
_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]|$)")
_COMPACT_DATE_RE = re.compile(r"^(\d{8})$")
_COMPACT_MONTH_RE = re.compile(r"^(\d{6})$")
_DOT_MONTH_RE = re.compile(r"^(\d{4})\.(\d{1,2})$")


def norm_period(value: object) -> str:
    """把各接口五花八门的期间写法归一成**可排序**的规范字符串。

    ⚠️ 归一后的字符串必须保证「字典序 == 时间序」，因为下游一律靠排序取「上一期」。
       `2026-08` / `2026Q2` / `2026-09-22` 三种形态各自都满足该性质。

    已知输入形态（全部来自 2026-09-23 实测）：

    | 原始写法 | 归一 | 出处 |
    |---|---|---|
    | `2026年08月份` | `2026-08` | macro_china_cpi / money_supply / stock_market_cap |
    | `2026年第1-2季度` | `2026Q2` | macro_china_gdp（**累计口径**：1-2 季度 = 上半年） |
    | `2026年第2季度` | `2026Q2` | macro_china_enterprise_boom_index |
    | `2026-09-22` | `2026-09-22` | bond_zh_us_rate / currency_boc_sina |
    | `202608` | `2026-08` | macro_china_urban_unemployment |
    | `201501` | `2015-01` | macro_china_shrzgm |
    | `2026.8` | `2026-08` | macro_china_society_electricity |
    | `20260922` | `2026-09-22` | stock_sse_summary 的「报告时间」 |

    ⚠️ 季度累计写法（`第1-2季度`）取**后者**作为期间编号：它表示「截至 Q2 的累计值」，
    不是 Q1 的数据。GDP 绝对值与同比都是这种累计口径，弄错会让滚动 4 季 GDP 少算。
    """
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "nat", "null", "-"):
        return ""

    m = _QUARTER_RE.search(s)
    if m:
        year, first, last = m.group(1), m.group(2), m.group(3)
        # 累计写法取后者：「第 1-2 季度」= 截至 Q2
        return f"{year}Q{int(last or first)}"

    m = _MONTH_RE.search(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"

    m = _DATE_RE.match(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 8 位纯数字（20260922）必须先于 6 位判断，否则会被截成 2026-09
    m = _COMPACT_DATE_RE.match(s)
    if m:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"

    m = _DOT_MONTH_RE.match(s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"

    m = _COMPACT_MONTH_RE.match(s)
    if m:
        return f"{s[:4]}-{s[4:]}"

    return s


def _to_float(v: object) -> float:
    """转 float，失败或 NaN/None 一律给 NaN（由调用方决定是否剔除）。"""
    if v is None:
        return float("nan")
    try:
        out = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return float("nan")
    return out


def to_series(df: pd.DataFrame, period_col: str, value_col: str) -> pd.Series:
    """DataFrame（期间列 + 数值列）→ 规范 Series。

    做四件事：归一期间 → 转数值 → **剔除无效行** → 按期间升序。
    无效行 = 期间解析为空、数值为 NaN、期间重复（保留最后一条）。

    ⚠️ 保留最后一条重复项是有意的：`macro_china_stock_market_cap` 这类月度表
       偶尔出现「同月两行、后一行更完整」的情况。
    """
    if df is None or df.empty:
        raise ValueError(f"空表：{period_col} / {value_col}")
    for col in (period_col, value_col):
        if col not in df.columns:
            raise KeyError(f"缺少列 {col!r}；实际列：{list(df.columns)}")

    periods = df[period_col].map(norm_period)
    values = df[value_col].map(_to_float)

    out = pd.DataFrame({"p": periods, "v": values})
    out = out[(out["p"] != "") & out["v"].notna()]
    if out.empty:
        raise ValueError(f"全部行都无效：{period_col} / {value_col}")

    out = out.drop_duplicates(subset="p", keep="last").set_index("p")["v"]
    return out.sort_index()  # 按期间字符串升序 == 时间序


# --------------------------------------------------------------------------- #
# 利率 / 债券
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_bond_zh_us_rate() -> pd.DataFrame:
    """中美国债收益率（2/5/10/30Y + 期限利差），日频。"""
    return ak.bond_zh_us_rate()


def cn_us_10y() -> tuple[pd.Series, pd.Series]:
    """中国 10Y 国债、美国 10Y 国债收益率（%）。"""
    df = fetch_bond_zh_us_rate()
    return (to_series(df, "日期", "中国国债收益率10年"),
            to_series(df, "日期", "美国国债收益率10年"))


def cn_us_2y() -> tuple[pd.Series, pd.Series]:
    """中国 2Y、美国 2Y 国债收益率（%）。"""
    df = fetch_bond_zh_us_rate()
    return (to_series(df, "日期", "中国国债收益率2年"),
            to_series(df, "日期", "美国国债收益率2年"))


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_bond_china_yield(days: int = 300) -> pd.DataFrame:
    """中债收益率曲线（国债 / AAA 银行债，多期限），日频。

    ⚠️ 该接口是**长表**：同一日期有多条「曲线名称」，取数时必须先筛曲线。

    🔴 **必须显式传日期**，两个坑叠在一起：
       ① 签名默认值被写死成历史区间（实测 1.18.94 为
          `start_date='20200204', end_date='20210124'`）→ 不传日期会安静地
          返回 **2020–2021 年**的数据（与「汇率接口默认 2023 年」完全同类）。
       ② 接口限制 `end_date - start_date` **必须小于一年**，跨度超限时返回
          **0 行**而不是报错 → 于是「日期传得宽松些」反而变成空表。
          故默认取 300 天，留出余量。
    """
    end = date.today()
    start = end - timedelta(days=days)
    return ak.bond_china_yield(
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )


def china_yield_curve(name_keyword: str, tenor: str = "10年") -> pd.Series:
    """按曲线名关键字 + 期限取一条收益率曲线（%）。"""
    df = fetch_bond_china_yield()
    hit = df[df["曲线名称"].astype(str).str.contains(name_keyword, regex=False)]
    if hit.empty:
        raise ValueError(f"债券曲线未命中：{name_keyword!r}")
    return to_series(hit, "日期", tenor)


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout(50.0)          # ⚠️ 单独放宽：见下方 docstring
def fetch_shibor() -> pd.DataFrame:
    """SHIBOR（上海银行间同业拆放利率）3 月期，日频。

    🔴 **本接口是实现层面的慢接口，耗时波动极大**：它内部按期限**串行**发起 10 次
       请求（进度条 10 项），即使只要一个期限也照跑全部，实测 5～40s 都可能。
       2026-09-23 更出现过卡在第 8 项、**数分钟无返回**的情况 —— 表现为整次构建卡死，
       而日志停在进度条上完全看不出卡在哪。

       超时定 50s（比默认 30s 宽）是因为**按默认值会误杀正常运行**（实测 37s 才返回）；
       超时后降级为「数据缺失」并在页面显式标注 —— 本指标在页面上是补充项，
       缺失不影响主结论（政策利率由 LPR 覆盖、无风险利率由国债覆盖）。
    """
    return ak.rate_interbank(market="上海银行同业拆借市场",
                             symbol="Shibor人民币", indicator="3月")


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_lpr() -> pd.DataFrame:
    """LPR 报价（1Y / 5Y），月度。"""
    return ak.macro_china_lpr()


# --------------------------------------------------------------------------- #
# 汇率
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_boc_fx(symbol: str, days: int = 400) -> pd.DataFrame:
    """中国银行外汇牌价（含**央行中间价**），日频。

    symbol ∈ {美元, 欧元, 日元, 英镑, 港元, ...}

    🔴 **必须显式传日期**：该接口的函数签名把默认值写死成了历史区间
       （实测 1.18.94 为 `start_date='20230304', end_date='20231110'`），
       不传日期时**返回 2023 年的数据且不报错** —— 页面上会安静地展示三年前的汇率。
       这个坑与「北交所 920xxx 静默取空」同类：错了但没有一个地方会喊。
    """
    end = date.today()
    start = end - timedelta(days=days)
    return ak.currency_boc_sina(
        symbol=symbol,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )


def boc_fx_mid(symbol: str) -> pd.Series:
    """取某币种的**央行中间价**序列（元 / 100 外币）。"""
    df = fetch_boc_fx(symbol)
    return to_series(df, "日期", "央行中间价")


# --------------------------------------------------------------------------- #
# 估值（全 A / 指数）
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_a_ttm_lyr() -> pd.DataFrame:
    """全 A 市盈率（TTM / LYR，中位数法 + 平均法）+ **官方历史分位**，日频近 1 年。

    ⚠️ 分位字段只在**最后一行**有值，前面的历史行是 NaN。
       这与「接口只回近 1 年数据、但分位是基于全历史算的」一致 —— 直接用最后一行的分位。
    """
    return ak.stock_a_ttm_lyr()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_a_all_pb() -> pd.DataFrame:
    """全 A 市净率（中位数法 + 等权平均）+ **官方历史分位**，日频近 1 年。"""
    return ak.stock_a_all_pb()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_sse_summary() -> pd.DataFrame:
    """上交所市场概况（总市值 / 平均市盈率 / 上市公司数），**当日快照**（无历史）。

    返回长表：项目 / 股票 / 主板 / 科创板。
    """
    return ak.stock_sse_summary()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_csindex_value(index_code: str) -> pd.DataFrame:
    """中证指数官方估值（市盈率1/2、股息率1/2），日频近 20 个交易日。

    index_code: '000300'(沪深300) / '000905'(中证500)
    ⚠️ 实测该接口**滞后约 1 个月**（2026-09-23 拉到的最新是 08-27），
       展示时必须印出真实数据日期，不能跟当日行情混排。
    """
    return ak.stock_zh_index_value_csindex(symbol=index_code)


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_market_cap() -> pd.DataFrame:
    """沪深两市**月度**市值序列（2008 起），是巴菲特指标的分子来源。

    ⚠️ 当月行全列 NaN（月度数据当月未结束）→ `to_series` 会自动剔除。
    """
    return ak.macro_china_stock_market_cap()


# --------------------------------------------------------------------------- #
# 中国宏观（官方源）
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_gdp() -> pd.DataFrame:
    """中国 GDP（季度**累计**绝对值 + 同比）。绝对值单位：亿元。"""
    return ak.macro_china_gdp()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_cpi() -> pd.DataFrame:
    """中国 CPI（当月 / 同比 / 环比 / 累计），月度。上月=100 的指数形态。"""
    return ak.macro_china_cpi()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_money_supply() -> pd.DataFrame:
    """中国 M0 / M1 / M2（数量 + 同比 + 环比），月度。数量单位：亿元。"""
    return ak.macro_china_money_supply()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_shrzgm() -> pd.DataFrame:
    """社会融资规模增量（分项），月度。

    ⚠️ 实测**滞后数月**（2026-09-23 只到 2026-04）—— 展示要标真实日期。
    """
    return ak.macro_china_shrzgm()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_retail() -> pd.DataFrame:
    """社会消费品零售总额（当月 / 同比 / 环比 / 累计），月度。单位：亿元。"""
    return ak.macro_china_consumer_goods_retail()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_unemployment() -> pd.DataFrame:
    """城镇调查失业率，**长表**（date / item / value），月度。"""
    return ak.macro_china_urban_unemployment()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_electricity() -> pd.DataFrame:
    """全社会用电量（绝对值 + 同比），月度。绝对值单位：万千瓦时。"""
    return ak.macro_china_society_electricity()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_boom_index() -> pd.DataFrame:
    """企业景气指数（季度），含同比/环比。

    ⚠️ 实测「企业家信心指数」系列自 2024 起已停更（全 NaN），只有「企业景气指数」仍更新。
    """
    return ak.macro_china_enterprise_boom_index()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_house_price() -> pd.DataFrame:
    """70 城住宅价格指数（同比/环比/定基），月度长表（日期 × 城市）。"""
    return ak.macro_china_new_house_price()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_china_fx_gold() -> pd.DataFrame:
    """外汇储备（亿美元）+ 黄金储备（万盎司），月度。"""
    return ak.macro_china_fx_gold()


# --------------------------------------------------------------------------- #
# 市场行情：指数
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_index_us(symbol: str) -> pd.DataFrame:
    """新浪美股指数日 K。

    symbol: '.INX'(标普500) / '.DJI'(道指) / '.IXIC'(纳指)
    """
    return ak.index_us_stock_sina(symbol=symbol)


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_index_cn(symbol: str) -> pd.DataFrame:
    """A 股指数日 K（新浪）。

    symbol: 'sh000001'(上证) / 'sz399001'(深证成指) / 'sz399300'(沪深300)
    ⚠️ 用不带前缀的代码（'000001'）会取到**个股**（平安银行），必须带交易所前缀。
    """
    return ak.stock_zh_index_daily(symbol=symbol)


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_index_hk(symbol: str) -> pd.DataFrame:
    """港股指数日 K（新浪）：'HSI'(恒生) / 'HSCEI'(国企) / 'HSTECH'(科技)。"""
    return ak.stock_hk_index_daily_sina(symbol=symbol)


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_hk_index_spot() -> pd.DataFrame:
    """港股指数实时快照（38 个），含 **VHSI 恒指波幅指数**。

    ⚠️ 这是**快照**接口，无历史；VHSI 的历史序列在 AKShare 没有稳定来源，
       所以它只以「当期值」呈现，不参与分位/走势计算。
    """
    return ak.stock_hk_index_spot_sina()


# --------------------------------------------------------------------------- #
# 市场行情：波动率 / 情绪 / 资金
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_qvix_50etf() -> pd.DataFrame:
    """50ETF 期权隐含波动率（中国版「恐慌指数」），日频长历史。"""
    return ak.index_option_50etf_qvix()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_qvix_300etf() -> pd.DataFrame:
    """300ETF 期权隐含波动率，日频长历史。

    ⚠️ 该接口早期（2015 起）数值为 NaN，只有近若干年有效 —— `to_series` 会自动剔除。
    """
    return ak.index_option_300etf_qvix()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_high_low_stat() -> pd.DataFrame:
    """A 股新高/新低家数（20/60/120 日窗口），日频 —— 市场宽度。"""
    return ak.stock_a_high_low_statistics()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_margin_sh() -> pd.DataFrame:
    """沪市融资融券余额（日频）。单位：元。"""
    return ak.macro_china_market_margin_sh()


@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_hsgt_flow() -> pd.DataFrame:
    """沪深港通资金流向汇总（北向/南向），**当日快照**。"""
    return ak.stock_hsgt_fund_flow_summary_em()


# --------------------------------------------------------------------------- #
# 大宗商品
# --------------------------------------------------------------------------- #

@cached
@retry(retries=1, base=2.0, cap=4.0)
@timeout()
def fetch_foreign_futures(symbol: str) -> pd.DataFrame:
    """外盘期货历史日 K。

    symbol: 'CL'(WTI 原油) / 'GC'(黄金) / 'HG'(铜) / 'SI'(白银)
    ⚠️ 该接口返回的 `volume` / `settlement` 等字段恒为 0，只有 OHLC 有效。
    """
    return ak.futures_foreign_hist(symbol=symbol)
