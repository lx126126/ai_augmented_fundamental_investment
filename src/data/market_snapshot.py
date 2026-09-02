"""每日行情快照：拉取实时行情/估值/评级，落历史快照表 + 覆盖报告用最新表。

与财报季 DAG 的区别：
    - 财报季 DAG 拉三表（利润/资产负债/现金流）+ 分红 + 分业务，低频（季度）；
    - 本模块只拉「行情类」数据（现价/PE/PB/市值/52周/机构评级），高频（每交易日）。

数据流向：
    1. 历史快照表（积累自己的估值历史，摆脱对外部接口的依赖）：
        data/market/{symbol}_quote.parquet     现价/PE/PB/市值/52周 逐日追加
        data/market/{symbol}_valuation.parquet 市值/PB 逐日追加（长表）
    2. 报告用最新表（覆盖，adapter 直接读，保证报告估值板块为当日值）：
        data/raw/{symbol}/quote.parquet
        data/raw/{symbol}/rating.parquet

港股（如 09992.HK）只拉行情（腾讯行情覆盖），估值/评级接口为 A 股专用，
缺失字段留空，不阻塞整条流水线。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .fetcher import fetch_quote, fetch_rating, fetch_valuation

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"
MARKET_DIR = DATA_ROOT / "market"
RAW_DIR = DATA_ROOT / "raw"

# A 股代码前缀（6/0/3/4/8）。注意：港股如 09992 剥后缀后也是 0 开头，
# 与深市 A 股冲突，因此「是否港股」必须以 market 后缀为准（见 is_hk），
# _is_a_share 仅作 snapshot_valuation/rating 内部的粗粒度防呆（由 snapshot_all 的 is_hk 前置拦截港股）。
_A_SHARE_PREFIXES = ("6", "0", "3", "4", "8")


def is_hk(market: str | None) -> bool:
    """根据交易所后缀判断是否为港股标的（港股无百度估值/东财评级 A 股接口）。"""
    return (market or "").upper() == "HK"


def _is_a_share(code: str) -> bool:
    """纯 6 位 A 股代码判定：6/0/3/4/8 开头。"""
    code = code.zfill(6)
    return code[0] in _A_SHARE_PREFIXES


def _append_snapshot(path: Path, df: pd.DataFrame) -> None:
    """追加历史快照（去重：同 symbol + report_date 不重复写）。

    df 需含 report_date 列（日期），追加前按日期去重，保留最新。
    """
    if df is None or df.empty:
        return
    df = df.copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = pd.read_parquet(path)
            # 合并后按 report_date 去重（保留最新）
            combined = pd.concat([old, df], ignore_index=True)
            if "report_date" in combined.columns:
                combined["report_date"] = pd.to_datetime(combined["report_date"])
                combined = combined.sort_values("report_date").drop_duplicates(
                    subset=[c for c in combined.columns if c != "report_date"],
                    keep="last",
                )
            combined.to_parquet(path, index=False)
            return
        except Exception:
            pass  # 读旧表失败则直接覆盖
    df.to_parquet(path, index=False)


def snapshot_quote(code: str, market: str | None = None) -> pd.DataFrame | None:
    """拉腾讯行情单行快照，追加历史 + 覆盖报告用最新表。

    market：可选交易所前缀（港股传 "hk"）。返回带 report_date 的单行 DataFrame
    （用于历史快照），或 None（拉取失败）。
    """
    q = fetch_quote(code, market=market)
    if q is None or q.empty:
        return None

    # 覆盖报告用最新表（adapter 读 data/raw/{code}/quote.parquet）
    raw_path = RAW_DIR / code / "quote.parquet"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    q.to_parquet(raw_path, index=False)

    # 追加历史快照（加 report_date 日期列）
    snap = q.copy()
    snap["report_date"] = pd.Timestamp.now().normalize()
    _append_snapshot(MARKET_DIR / f"{code}_quote.parquet", snap)
    return snap


def snapshot_valuation(code: str) -> pd.DataFrame | None:
    """拉百度估值近十年长表，追加历史（积累更密的市值/PB 历史）。

    港股无此接口，返回 None。追加时与已有历史合并去重。
    """
    if not _is_a_share(code):
        return None
    val = fetch_valuation(code)
    if val is None or val.empty:
        return None

    # 覆盖报告用最新表（adapter 读 data/raw/{code}/valuation.parquet）
    raw_path = RAW_DIR / code / "valuation.parquet"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    val.to_parquet(raw_path, index=False)

    # 追加历史快照
    _append_snapshot(MARKET_DIR / f"{code}_valuation.parquet", val)
    return val


def snapshot_rating(code: str) -> pd.DataFrame | None:
    """拉东财机构评级单行，覆盖报告用最新表。

    评级数据低频（周/月级别），但接口轻量，随每日一起拉无妨。港股返回 None。
    """
    if not _is_a_share(code):
        return None
    r = fetch_rating(code)
    if r is None or r.empty:
        return None
    raw_path = RAW_DIR / code / "rating.parquet"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    r.to_parquet(raw_path, index=False)
    return r


def snapshot_all(code: str, market: str | None = None) -> dict:
    """拉取单只标的全部行情类数据，返回各表状态摘要。

    market：可选交易所前缀（港股传 "hk"，只拉行情不拉估值/评级）。
    """
    result = {"code": code, "quote": False, "valuation": False, "rating": False}

    q = snapshot_quote(code, market=market)
    if q is not None:
        result["quote"] = True

    if not is_hk(market):
        v = snapshot_valuation(code)
        if v is not None:
            result["valuation"] = True

        r = snapshot_rating(code)
        if r is not None:
            result["rating"] = True

    return result
