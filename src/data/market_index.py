"""全市场标的索引：A 股 + 港股全量代码/名称 → 可搜索的「标的路由表」。

为什么需要它（定位）
--------------------
raw / mart 层是**已有**标的的数据。没有索引表时，产品只能查「已预拉过财报的标的」——
想覆盖全市场，就只能把 8000 只标的的财报全拉下来（实测单只 A 股 63.5s，全量约 140 小时）。

本模块把「能查什么」和「已经算了什么」解耦：
    索引（全市场 8368 只，秒级可查） → 用户查谁 → 才拉谁、才算谁
这样磁盘（全量 raw 仅约 1.2GB）与时间都可控，而查询体验不受标的范围限制。

数据源（2026-09-17 实测）
------------------------
    A 股：ak.stock_info_a_code_name()   5565 只 / 17.7s（巨潮，代码 + 简称）
    港股：ak.stock_hk_spot()            2803 只 /  8.3s（新浪，代码 + 中英文名）

⚠️ 有意**不用** `stock_zh_a_spot_em` / `stock_hk_spot_em` 取列表：那两个走东财
push2 域名，在沙箱/CI 等受限网络下会被链路重置（实测 ProxyError / RemoteDisconnected）；
且它们是行情接口，用行情接口取静态列表属于杀鸡用牛刀，还会把「列表不可用」
和「行情不可用」两类故障搅在一起。

代码规范（与项目既有约定一致，别改）
------------------------------------
    A 股：6 位纯数字（000651 / 600519 / 830799）
    港股：5 位纯数字（00700 / 09992）
与 `adapter._norm_code` 完全一致 —— 目录名、parquet 文件名、报告归档名都用这个形态。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from . import fetcher as _fetcher  # noqa: F401  ⚠️ 必须先导入，它负责配置网络出口
import akshare as ak

ROOT = Path(__file__).resolve().parent.parent.parent
MARKET_DIR = ROOT / "data" / "market"
INDEX_PATH = MARKET_DIR / "index.parquet"

# A 股代码首字符 → 交易所。6/9 沪市，0/2/3 深市，4/8 北交所
_A_SHARE_EXCHANGE = {
    "6": "SH", "9": "SH",
    "0": "SZ", "2": "SZ", "3": "SZ",
    "4": "BJ", "8": "BJ",
}


def a_share_exchange(symbol: str) -> str:
    """A 股代码 → 交易所（SH / SZ / BJ）。

    ⚠️ 有一条不能按首字符走的特例：**920xxx 是北交所**（2023 年起启用的新代码段），
    不能因为首字符是 '9' 就归到沪市。实测 2026-09-17：`sh920000` / `sz920000`
    都取不到行情，`bj920000` 正常（安徽凤凰 13.62）。首字符 '9' 里只有 900xxx
    才是沪市 B 股。
    误判的代价不是报错而是**静默缺失** —— 全市场行情会少 344 只而没有任何提示。
    """
    s = str(symbol).strip().zfill(6)
    if s[:2] == "92":
        return "BJ"
    if s[:1] == "9":
        return "SH"  # 900xxx 沪市 B 股
    return _A_SHARE_EXCHANGE.get(s[:1], "SZ")


# --------------------------------------------------------------------------- #
# 拉取
# --------------------------------------------------------------------------- #

def fetch_a_share_index() -> pd.DataFrame:
    """A 股全量标的（代码 + 简称 + 交易所）。"""
    raw = ak.stock_info_a_code_name()
    out = pd.DataFrame({
        "symbol": raw["code"].astype(str).str.strip().str.zfill(6),
        "name": raw["name"].astype(str).str.strip(),
    })
    out["market"] = out["symbol"].map(a_share_exchange)
    return out[["symbol", "market", "name"]]


def fetch_hk_index() -> pd.DataFrame:
    """港股全量标的（代码 + 中文名 + 英文名）。"""
    raw = ak.stock_hk_spot()
    code_col = "代码" if "代码" in raw.columns else "code"
    name_col = "中文名称" if "中文名称" in raw.columns else "name"
    en_col = "英文名称" if "英文名称" in raw.columns else None

    out = pd.DataFrame({
        "symbol": raw[code_col].astype(str).str.strip().str.zfill(5),
        "name": raw[name_col].astype(str).str.strip(),
    })
    out["market"] = "HK"
    if en_col:
        out["name_en"] = raw[en_col].astype(str).str.strip()
    return out


def build_index(save: bool = True) -> pd.DataFrame:
    """拉全市场索引（A 股 + 港股）并落盘。

    单一市场失败不阻断另一市场：只报错、保留已拉到的部分。只有两个都失败才抛异常
    —— 全市场索引的价值在于「有总比没有好」，A 股拿不到不该让港股也白跑。
    """
    parts: list[pd.DataFrame] = []
    for label, fn in (("A 股", fetch_a_share_index), ("港股", fetch_hk_index)):
        try:
            d = fn()
            print(f"[market_index] {label}: {len(d)} 只")
            parts.append(d)
        except Exception as e:
            print(f"[market_index] {label} 拉取失败：{type(e).__name__}: {e}")

    if not parts:
        raise RuntimeError("全市场索引拉取全部失败（A 股与港股都拿不到）")

    df = pd.concat(parts, ignore_index=True)
    # 同一 symbol 在同一市场内去重（源接口偶有重复行）
    df = (df.drop_duplicates(subset=["symbol", "market"])
            .sort_values(["market", "symbol"])
            .reset_index(drop=True))
    df["updated"] = datetime.now().strftime("%Y-%m-%d")
    df["name_en"] = df.get("name_en", pd.Series([""] * len(df), index=df.index)).fillna("")

    if save:
        MARKET_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(INDEX_PATH, index=False)
        n_a = int((df["market"] != "HK").sum())
        n_hk = int((df["market"] == "HK").sum())
        print(f"[market_index] 已落盘 {INDEX_PATH}")
        print(f"[market_index] 合计 {len(df)} 只（A 股 {n_a} / 港股 {n_hk}）")
    return df


def load_index() -> pd.DataFrame:
    """读索引；不存在则现场构建（首次使用会慢十几秒）。"""
    if not INDEX_PATH.exists():
        print("[market_index] 索引不存在，现场构建…")
        return build_index()
    return pd.read_parquet(INDEX_PATH)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #

def _norm_query(q: str) -> str:
    """查询串归一化：去空格、转大写、剥掉 .SH/.SZ/.HK/.BJ 后缀。"""
    return (q or "").strip().replace(" ", "").upper().split(".")[0]


def search(query: str, limit: int = 20, index: pd.DataFrame | None = None) -> list[dict]:
    """按代码或名称搜索标的，返回候选列表（按匹配强度排序）。

    匹配优先级：代码精确 > 代码前缀 > 名称精确/包含。
    纯数字按代码匹配（A 股补到 6 位、港股补到 5 位）；含非数字的按名称匹配。
    """
    df = load_index() if index is None else index
    q = _norm_query(query)
    if not q:
        return []

    if q.isdigit():
        # ① 精确（含补位后的精确，覆盖用户少打前导零的情况）
        exact = df[df["symbol"].isin({q, q.zfill(6), q.zfill(5)})]
        if not exact.empty:
            return exact.head(limit).to_dict(orient="records")
        # ② 前缀
        pfx = df[df["symbol"].str.startswith(q, na=False)]
        return pfx.head(limit).to_dict(orient="records")

    names = df["name"].astype(str).str.replace(" ", "", regex=False).str.upper()
    names_en = df.get("name_en", pd.Series([""] * len(df), index=df.index))
    names_en = names_en.astype(str).str.replace(" ", "", regex=False).str.upper()

    # ③ 名称精确（中文或英文）
    exact = df[(names == q) | (names_en == q)]
    if not exact.empty:
        return exact.head(limit).to_dict(orient="records")
    # ④ 名称包含
    hit = df[names.str.contains(q, regex=False, na=False)
             | names_en.str.contains(q, regex=False, na=False)]
    return hit.head(limit).to_dict(orient="records")


def resolve(query: str, index: pd.DataFrame | None = None) -> dict | None:
    """把用户输入解析成**唯一**标的；无命中或多命中都返回 None。

    有意不做「多命中取第一个」：那会让用户输入「银行」时静默生成了某家银行的报告。
    调用方拿到 None 后应展示 `search()` 的候选让用户选。
    """
    hits = search(query, limit=5, index=index)
    if len(hits) == 1:
        return hits[0]
    # 代码精确命中也视为唯一（此时 search 的精确分支只会返回 1 条）
    if hits and hits[0]["symbol"] == _norm_query(query).zfill(len(hits[0]["symbol"])):
        return hits[0]
    return None


def stats(index: pd.DataFrame | None = None) -> dict:
    """索引概况（给服务层做状态展示）。"""
    df = load_index() if index is None else index
    by_market = df.groupby("market").size().to_dict()
    return {
        "total": int(len(df)),
        "by_market": {k: int(v) for k, v in by_market.items()},
        "updated": str(df["updated"].iloc[0]) if "updated" in df.columns and len(df) else "",
    }
