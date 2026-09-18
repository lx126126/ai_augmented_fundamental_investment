# -*- coding: utf-8 -*-
"""跟踪池单一真源：读 / 写 `watchlist/watchlist.json`。

为什么需要这个模块
------------------
原先「跟踪池」被硬编码在**三处**，必然漂移。2026-09-17 实测：

| 来源 | 标的数 | 内容 |
|---|---|---|
| `watchlist/watchlist.json` | 6 | 神华/茅台/格力/交行/腾讯/泡泡玛特 |
| `scripts/build_web_index.py` 的 `STOCK_META` | 8 | 多了招行/兴业/成都银行，**缺腾讯** |
| `scripts/build_watchlist.py` 的 `STOCK_META` | 6 | 与 json 同内容但代码重复一份 |
| 实际已有报告 | **11** | 另有海油 A+H（600938/00883）、伊利（600887）、300061 |

后果很具体：**手机首页列 6 张卡片，对比表列 6 只，而报告实际有 11 份** ——
生成过的报告在网页上"消失"了。本模块把清单收敛为唯一来源。

入池规则（2026-09-17 定，潇姐）
------------------------------
**生成过报告即入池**，不设硬上限。`rules.max_size` 只作页面提示
（「名义上限 8 只 · 当前 N 只」），不阻断写入。

用法
----
    from src.data import watchlist_store as wl
    wl.codes()                       # ['601088', '600519', ...]
    wl.stocks()                      # 完整条目列表（含 name/industry/color）
    wl.add('600887', name='伊利股份')  # 幂等；新增返回 True，已存在返回 False
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WATCHLIST_PATH = ROOT / "watchlist" / "watchlist.json"

#: 色点调色板 —— 卡片/表格的左侧圆点。按入池顺序循环取，避免手工维护颜色。
PALETTE = (
    "#378ADD", "#E24B4A", "#BA7517", "#888780", "#00A4FF",
    "#F08BB0", "#5B8FF9", "#3D9A5B", "#9A6BBF", "#0F9B8E",
    "#D4763A", "#7A6FF0",
)

#: 跟踪池名义上限（`rules.max_size`）；仅用于页面提示，不阻断入池。
DEFAULT_MAX_SIZE = 8

#: Lynch 归类规则（关键字 → 归类），按顺序**首个命中即用**。
#: 用关键字而非精确匹配：数据源给的行业名带后缀（"银行Ⅱ"）、带细分
#: （"油气开采Ⅱ"、"饮料乳品"），精确表会全落空、界面上一排"未分类"。
_LYNCH_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("银行", "保险", "证券", "高速", "港口", "铁路", "公用", "电力", "水务"),
     "稳健增长型 · 收息"),
    (("煤炭", "石油", "天然气", "油气", "有色", "钢铁", "化工", "水泥", "建材",
      "航运", "房地产", "建筑"), "周期型 · 资源/周期"),
    (("白酒", "饮料", "乳品", "食品", "调味", "啤酒", "家电", "纺织", "服装"),
     "稳定增长型 · 品牌消费"),
    (("互联网", "软件", "传媒", "广告", "游戏", "云", "电商"),
     "稳定增长型 · 轻资产"),
    (("半导体", "电子", "电池", "光伏", "新能源", "军工", "机械", "汽车",
      "医药", "生物", "医疗器械", "通信"), "快速增长型"),
)

#: 一条都命中不了时的兜底 —— 写"待归类"而不是"未分类"，读起来像"该补一下"而不是"系统坏了"
_LYNCH_FALLBACK = "待归类"


def classify_lynch(industry: str, name: str = "") -> str:
    """按行业名（+名称）推 Lynch 归类。命中不了返回 `待归类`。"""
    hay = f"{industry or ''}{name or ''}"
    for keys, label in _LYNCH_RULES:
        if any(k in hay for k in keys):
            return label
    return _LYNCH_FALLBACK


# --------------------------------------------------------------------------- #
# 代码归一
# --------------------------------------------------------------------------- #

def is_hk(code: str) -> bool:
    """港股判定：剥后缀后是 0 开头的 5 位码。

    ⚠️ 不能只看"0 开头"：深市 A 股（000651）也是 0 开头，靠**长度 5**区分。
    与 `scripts/daily_refresh.py` / `fetch_stock.py` 保持同一口径。
    """
    c = str(code).strip().upper().split(".")[0]
    return c.startswith("0") and len(c) == 5


def bare(code: str) -> str:
    """剥掉交易所后缀并补齐位数：`'601088.SH'` / `'601088'` → `'601088'`。"""
    c = str(code).strip().upper().split(".")[0]
    if is_hk(c):
        return c.zfill(5)
    return c.zfill(6)


def with_exchange(code: str) -> str:
    """`'601088'` → `'601088.SH'`；`'00700'` → `'00700.HK'`；已带后缀则归一。

    沪/深/北的判定**复用 `market_index.a_share_exchange()`**，不在这里重写一份 ——
    920xxx 归北交所这条特例（误判会静默少 344 只）只能有一个实现。
    """
    c = bare(code)
    if is_hk(c):
        return f"{c}.HK"
    from .market_index import a_share_exchange  # 延迟导入：market_index 会连带拉起 akshare
    return f"{c}.{a_share_exchange(c)}"


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #

def _read_raw() -> dict:
    if not WATCHLIST_PATH.exists():
        return {"version": 1, "rules": {"max_size": DEFAULT_MAX_SIZE}, "stocks": []}
    try:
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "rules": {"max_size": DEFAULT_MAX_SIZE}, "stocks": []}


def stocks(include_removed: bool = False) -> list[dict]:
    """跟踪池条目（保持 json 里的顺序 = 页面上的人工排序）。

    每条都补齐 `bare`（无后缀代码）与 `color`（色点），调用方无需自己算。
    默认**不含** `status == "removed"` 的条目（见 `remove()` 的软删说明）。
    """
    out: list[dict] = []
    # ⚠️ enumerate 在下标过滤前算：`color` 的兜底 `PALETTE[i % len]` 依赖**原始位置**，
    #    若先过滤再取下标，一次移出会让后面所有未落盘颜色的标的换色。
    for i, s in enumerate(_read_raw().get("stocks", [])):
        code = str(s.get("code", "")).strip()
        if not code:
            continue
        item = dict(s)
        item.setdefault("status", "active")
        if item["status"] == "removed" and not include_removed:
            continue
        item["bare"] = bare(code)
        item["code"] = with_exchange(item["bare"]) if "." not in code else code
        item["color"] = s.get("color") or PALETTE[i % len(PALETTE)]
        item.setdefault("industry", "未分类")
        item.setdefault("name", item["bare"])
        out.append(item)
    return out


def codes() -> list[str]:
    """跟踪池代码（无后缀），保持 json 顺序。**不含已移出的**。"""
    return [s["bare"] for s in stocks()]


def removed_codes() -> set[str]:
    """已移出跟踪池的代码集合（软删留痕）。

    存在的理由：`build_web_index` 对「有报告但不在池里」有兜底卡片（防入池链路断），
    而**刻意移出**的标的必须排除在外 —— 否则「移出池」在首页上不生效（卡片仍在，
    只是名字退化成代码）。两者靠 `status` 区分，不能靠"在不在池里"。
    """
    return {s["bare"] for s in stocks(include_removed=True) if s.get("status") == "removed"}


def max_size() -> int:
    return int(_read_raw().get("rules", {}).get("max_size", DEFAULT_MAX_SIZE) or DEFAULT_MAX_SIZE)


def get(code: str, include_removed: bool = False) -> dict | None:
    c = bare(code)
    for s in stocks(include_removed=include_removed):
        if s["bare"] == c:
            return s
    return None


def color_map() -> dict[str, str]:
    """{无后缀代码: 色值}，供渲染层取色。"""
    return {s["bare"]: s["color"] for s in stocks()}


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #

def _display_name(code: str) -> str:
    """没给名称时从全市场索引查（索引缺失就退回代码本身，不抛错）。"""
    try:
        from .market_index import resolve
        hit = resolve(code)
        if hit:
            return str(hit.get("name") or code)
    except Exception:
        pass
    return code


def _guess_industry(code: str) -> str:
    """没给行业时自行推断，优先用报告同源的 `competition.parquet`。

    全市场索引只有代码/简称/市场，**没有行业**；`competition.parquet`（东财同业对比）
    里的 `industry` 与报告「行业排名」板块同源，拿它最不容易出现两处口径打架。
    再退一步才写"未分类" —— 宁可显示未分类，也不猜一个错的行业进去。
    """
    try:
        import pandas as pd
        f = ROOT / "data" / "raw" / bare(code) / "competition.parquet"
        if f.exists():
            df = pd.read_parquet(f, columns=["industry"])
            vals = [str(v).strip() for v in df["industry"].dropna().unique() if str(v).strip()]
            if vals:
                return vals[0]
    except Exception:
        pass
    return "未分类"


def _append(code_full: str, name: str, industry: str, lynch: str,
            source: str = "report") -> None:
    data = _read_raw()
    raw_stocks = data.setdefault("stocks", [])
    # 色值落盘而不是靠下标现算：否则 prune/重排会让**后面所有标的换色**。
    # ⚠️ 判重必须用 stocks() 的「有效色」（含下标兜底）—— 旧条目可能没有 color 字段，
    #    只看 s.get("color") 会得到空集合，新标的全部拿到 PALETTE[0] 撞色（已踩一次）。
    used = {s["color"] for s in stocks()}
    color = next((c for c in PALETTE if c not in used), PALETTE[len(raw_stocks) % len(PALETTE)])
    # 顺手把缺失的 color 回填进已有条目，让颜色从此稳定（重排/增删都不再换色）
    for rs in raw_stocks:
        rs.setdefault("color", color_map().get(bare(rs.get("code", "")), color))
    raw_stocks.append({
        "code": code_full,
        "name": name,
        "industry": industry,
        "lynch": lynch,
        "status": "active",
        "since": date.today().strftime("%Y-%m"),
        "color": color,
        "source": source,
    })
    data["updated"] = date.today().isoformat()
    data.setdefault("version", 1)
    WATCHLIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def add(code: str, name: str | None = None, industry: str | None = None,
        lynch: str | None = None, source: str = "report") -> bool:
    """把标的加入跟踪池。**幂等** —— 已存在返回 False，不重复写、不覆盖人工填的字段。

    名称/行业缺省时自动从全市场索引查（索引不可用就退回代码，不阻断入池）。
    """
    c = bare(code)
    if not c or re.fullmatch(r"0+", c):
        return False
    existing = get(c, include_removed=True)
    if existing is not None:
        # 已移出的标的重新入池 → **恢复原条目**，不追加新条目
        # （否则 json 里同名两条：active 一条 + removed 一条，留痕重复且难清理）。
        if existing.get("status") == "removed":
            return restore(c)
        return False

    name = name or _display_name(c)
    industry = industry or _guess_industry(c)
    lynch = lynch or classify_lynch(industry, name)
    _append(with_exchange(c), name, industry, lynch, source=source)
    return True


def remove(code: str, reason: str = "") -> dict | None:
    """移出跟踪池 —— **软删**：`status` 置 `removed`，保留名称/行业/颜色。

    为什么软删而不是删条目：`build_web_index` 对「有报告但不在池里」有兜底卡片
    （防入池链路静默断掉）。硬删会让**刻意移出**和**链路故障**长得一模一样，
    兜底卡片于是照旧显示被移出的标的（2026-09-18 实测：移出 600900 后首页仍有卡片，
    且名称退化成裸代码）。留一个 `status` 就能把两者分开。

    硬删（彻底清掉条目）用 `prune()`。返回被移出的条目，不存在或已移出返回 None。
    """
    c = bare(code)
    data = _read_raw()
    for s in data.get("stocks", []):
        if bare(s.get("code", "")) != c:
            continue
        if s.get("status") == "removed":
            return None
        s["status"] = "removed"
        s["removed_at"] = date.today().isoformat()
        if reason:
            s["removed_reason"] = reason
        data["updated"] = date.today().isoformat()
        WATCHLIST_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return dict(s, bare=c)
    return None


def restore(code: str) -> bool:
    """把已移出的标的重新纳入跟踪池（清掉 `status`/`removed_at`/`removed_reason`）。"""
    c = bare(code)
    data = _read_raw()
    for s in data.get("stocks", []):
        if bare(s.get("code", "")) != c or s.get("status") != "removed":
            continue
        s["status"] = "active"
        for k in ("removed_at", "removed_reason"):
            s.pop(k, None)
        data["updated"] = date.today().isoformat()
        WATCHLIST_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return True
    return False


def ensure_codes(codes_in: list[str]) -> list[str]:
    """批量补入池，返回**本次新增**的代码列表（供日志打印）。"""
    added = []
    for c in codes_in:
        if add(c):
            added.append(bare(c))
    return added


def prune(codes_in: list[str]) -> int:
    """移除指定标的（人工清理用）。返回移除条数。"""
    c_set = {bare(c) for c in codes_in}
    data = _read_raw()
    before = len(data.get("stocks", []))
    data["stocks"] = [s for s in data.get("stocks", []) if bare(s.get("code", "")) not in c_set]
    removed = before - len(data["stocks"])
    if removed:
        data["updated"] = date.today().isoformat()
        WATCHLIST_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return removed
