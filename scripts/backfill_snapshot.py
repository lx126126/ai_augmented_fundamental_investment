"""按日 K 回填某个交易日的收盘快照（补「Mac 关机/睡眠导致漏掉的那一天」）。

## 为什么需要它

`snapshot_quote` 走的是腾讯**实时**接口 —— 它只能给「现在」。所以：
- 漏掉的交易日**无法靠重跑日更补回来**：盘后重跑只会拿到今天（或下一个交易日）的价，
  盘中重跑拿到的是盘中价，**都补不出历史某天的收盘价**；
- 反过来，**盘前**跑日更更危险：实时接口此时返回的是**上一交易日收盘价**，而它的时间戳
  是「现在」→ 快照被写成 `report_date=今天` + `is_intraday=False`，报告页印出
  「收盘价 <今天>」而今天根本还没开市（2026-09-23 实测踩到，见 daily_refresh.sh 的收盘前闸门）。

本脚本绕开实时接口，直接取**日 K 的收盘价**（唯一权威源）+ 百度估值重建快照。

## 用法

    # 补最近一个已收盘的交易日（自动跳过周末；K 里没有的日期会自动退到上一根）
    python scripts/backfill_snapshot.py --auto

    # 补指定日期
    python scripts/backfill_snapshot.py --date 2026-09-22

    # 只补某几只 / 先看不动手
    python scripts/backfill_snapshot.py --date 2026-09-22 --codes 000651,00700
    python scripts/backfill_snapshot.py --auto --dry-run

⚠️ 回填之后**只能跑 `build_valueline.py --daily`**（或让 16:30 的日更去跑）。
   不要跑 `daily_refresh.py`：它第一步 `snapshot_all()` 会用实时价覆盖掉刚回填的值。

## 口径（每个字段取各自最权威的源，不做插值）

| 字段 | 来源 | 说明 |
|---|---|---|
| `price` | 日 K 的 `close`（A 股多源回退 / 港股腾讯，均**不复权**） | 与项目「区间用日 K」的既有口径一致 |
| `change` / `change_pct` | 目标日收盘 − **K 里前一根**收盘 | 不假设「前一个交易日 = 昨天」，节假日自动正确 |
| `pb` / `market_cap` | 百度估值目标日那一行 | 缺该行则保留旧值并告警（港股无百度接口） |
| `pe` | 旧快照 `pe × 目标日收盘 ÷ 旧快照价` | **恒等换算**：pe 是 TTM 口径，两次财报之间 eps 不变 → pe ∝ price |
| `report_date` | 目标日 | = 价格所属交易日 |
| `is_intraday` | `False` | 这是收盘价 |
| `price_52w_high/low` | **沿用旧值不动** | 该口径来自腾讯 q= 接口，不夹带口径变更 |
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.fetcher import fetch_hk_valuation, fetch_kline, fetch_valuation  # noqa: E402
from src.data.watchlist_store import codes as watchlist_codes  # noqa: E402

# 收盘时刻（分钟）：A 股 15:00、港股 16:00 → 取 16:00 兼顾两地（与 plist 的 16:30 一致）
CLOSE_MINUTES = 16 * 60
HK_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
HTTP_TIMEOUT = 10


def is_a_share(code: str) -> bool:
    return code.isdigit() and len(code) == 6


def _a_kline(code: str) -> pd.Series:
    """A 股日 K 收盘序列（index = 交易日）。fetch_kline 自带多源回退。"""
    k = fetch_kline(code)
    if k is None or k.empty:
        raise RuntimeError("日 K 拉取失败（多源均不可用）")
    k = k.copy()
    k["report_date"] = pd.to_datetime(k["report_date"]).dt.normalize()
    s = k.dropna(subset=["close"]).set_index("report_date")["close"].astype(float)
    return s.sort_index()


def _hk_kline(code: str, n: int = 30) -> pd.Series:
    """港股日 K（腾讯，不复权）。

    ⚠️ 东财 `ak.stock_hk_hist` 在本机/沙箱里对港股频繁 `RemoteDisconnected`，
    腾讯域名稳定得多（项目里「全市场行情用腾讯批量」也是同一理由）。
    ⚠️ `appstock/app/kline/kline` 的参数**不能带尾随逗号**、也**不能加 `,qfq`**
    （都会返回 `bad params`），且它本身给的就是不复权价 —— 正合我们的口径。
    ⚠️ 必须 `trust_env=False`：本机走 VPN 时 `HTTPS_PROXY` 会让该域名失败。
    """
    s = requests.Session()
    s.trust_env = False
    r = s.get(HK_KLINE_URL, params={"param": f"hk{code},day,,,{n}"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"腾讯港股日 K 返回 code={j.get('code')} msg={j.get('msg')}")
    rows = ((j.get("data") or {}).get(f"hk{code}") or {}).get("day") or []
    if not rows:
        raise RuntimeError("腾讯港股日 K 为空")
    # 行格式：[日期, 开, 收, 高, 低, 量]
    ser = pd.Series({pd.Timestamp(x[0]): float(x[2]) for x in rows})
    return ser.sort_index()


def kline_of(code: str) -> pd.Series:
    return _a_kline(code) if is_a_share(code) else _hk_kline(code)


def last_completed_session(now: pd.Timestamp) -> pd.Timestamp:
    """「最近一个已收盘的交易日」的**上界**：16:00 前算到昨天，之后算到今天。

    只给上界，真正的日期由 K 决定（K 里没有的日期天然不存在 → 自动落到上一根），
    所以节假日不用维护日历。
    """
    cutoff = now.normalize()
    if now.hour * 60 + now.minute < CLOSE_MINUTES:
        cutoff -= pd.Timedelta(days=1)
    return cutoff


def resolve_target(ser: pd.Series, args, now: pd.Timestamp) -> pd.Timestamp | None:
    """决定这只标的要回填哪一天。"""
    if args.date:
        t = pd.Timestamp(args.date).normalize()
        if t not in ser.index:
            raise RuntimeError(f"日 K 里没有 {t.date()}（区间 {ser.index.min().date()}~{ser.index.max().date()}）")
        return t
    cutoff = last_completed_session(now)
    cand = ser[ser.index <= cutoff]
    if cand.empty:
        raise RuntimeError("日 K 里没有可用的已完成交易日")
    return cand.index[-1]


def valuation_row(code: str, target: pd.Timestamp) -> dict | None:
    """百度估值目标日那一行的 pb / market_cap；取不到返回 None（保留旧值）。"""
    try:
        v = fetch_hk_valuation(code) if not is_a_share(code) else fetch_valuation(code)
        if v is None or v.empty:
            return None
        v = v.copy()
        v["report_date"] = pd.to_datetime(v["report_date"]).dt.normalize()
        row = v[v["report_date"] == target]
        if row.empty:
            return None
        d = dict(zip(row["indicator"], row["value"]))
        return d if ("pb" in d and "market_cap" in d) else None
    except Exception:  # noqa: BLE001
        return None


def backfill_one(code: str, args, now: pd.Timestamp) -> tuple[bool, str]:
    raw_q = ROOT / "data" / "raw" / code / "quote.parquet"
    hist_q = ROOT / "data" / "market" / f"{code}_quote.parquet"
    if not raw_q.exists():
        return False, f"缺 {raw_q.relative_to(ROOT)}"

    ser = kline_of(code)
    target = resolve_target(ser, args, now)
    if target is None:
        return False, "无法确定目标日"

    pos = ser.index.get_loc(target)
    if pos == 0:
        return False, f"{target.date()} 是 K 的第一根，取不到前收"
    close = float(ser.iloc[pos])
    prev_close = float(ser.iloc[pos - 1])

    old = pd.read_parquet(raw_q)
    o = old.iloc[0]
    old_date = pd.to_datetime(o.get("report_date")).normalize()
    old_price = float(o["price"])

    # 已经就是这一天且盘中标记正常 → 不必动
    if not args.force and old_date == target and bool(o.get("is_intraday") or False) is False:
        return True, f"已是 {target.date()}，跳过"

    vd = valuation_row(code, target)
    new = old.copy()
    new.loc[0, "price"] = close
    new.loc[0, "pe"] = round(float(o["pe"]) * close / old_price, 2) if old_price else o["pe"]
    if vd is not None:
        new.loc[0, "pb"] = float(vd["pb"])
        new.loc[0, "market_cap"] = float(vd["market_cap"])
    new.loc[0, "change"] = round(close - prev_close, 4)
    new.loc[0, "change_pct"] = round((close / prev_close - 1) * 100, 2)
    new.loc[0, "report_date"] = target
    new.loc[0, "is_intraday"] = False

    chg = float(new.loc[0, "change_pct"])
    if args.dry_run:
        return True, (f"[dry-run] {old_date.date()} → {target.date()}  "
                      f"{old_price:g} → {close:g}  {chg:+.2f}%  "
                      f"pb {'百度' if vd is not None else '沿用'}")

    new.to_parquet(raw_q, index=False)

    if hist_q.exists():
        hist = pd.read_parquet(hist_q)
        hist["report_date"] = pd.to_datetime(hist["report_date"]).dt.normalize()
        # 先剔掉「日期相同但内容不同」的旧行，避免 concat 后留下两行同日期
        hist = hist[hist["report_date"] != target]
        hist = pd.concat([hist, new], ignore_index=True)
        hist["report_date"] = pd.to_datetime(hist["report_date"])
        hist = (hist.sort_values("report_date")
                    .drop_duplicates(subset=[c for c in hist.columns if c != "report_date"],
                                     keep="last"))
        hist.to_parquet(hist_q, index=False)

    # A 股顺带把百度估值序列追到最新，让分位的分母跟上（港股无该接口）
    if is_a_share(code):
        try:
            from src.data.market_snapshot import snapshot_valuation
            snapshot_valuation(code)
        except Exception:  # noqa: BLE001
            pass

    return True, (f"{old_date.date()} → {target.date()}  "
                  f"{old_price:g} → {close:g}  {chg:+.2f}%  "
                  f"pb {'百度' if vd is not None else '沿用旧值'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="按日 K 回填某个交易日的收盘快照")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="目标交易日 YYYY-MM-DD")
    g.add_argument("--auto", action="store_true",
                   help="自动取「最近一个已收盘的交易日」（K 里没有则退到上一根）")
    ap.add_argument("--codes", help="逗号分隔；默认取跟踪池全部 active 标的")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    ap.add_argument("--force", action="store_true", help="即使快照已是目标日也重写")
    args = ap.parse_args()

    codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
             if args.codes else watchlist_codes())
    now = pd.Timestamp.now()

    print(f"回填 {'自动（最近已收盘交易日）' if args.auto else args.date} · {len(codes)} 只"
          f"{' · dry-run' if args.dry_run else ''}")
    print("-" * 84)
    ok, bad = 0, []
    for code in codes:
        try:
            success, msg = backfill_one(code, args, now)
            if success:
                ok += 1
                print(f"  ✓ {code:<8}{msg}")
            else:
                bad.append((code, msg))
                print(f"  ✗ {code:<8}{msg}")
        except Exception as e:  # noqa: BLE001
            bad.append((code, f"{type(e).__name__}: {e}"))
            print(f"  ✗ {code:<8}{type(e).__name__}: {str(e)[:80]}")
    print("-" * 84)
    print(f"成功 {ok} / 失败 {len(bad)}")
    if bad:
        for c, m in bad:
            print(f"  !! {c}: {m}")
    if ok and not args.dry_run:
        print("\n回填完成。下一步只跑构建（不要跑 daily_refresh.py，它会用实时价覆盖）：")
        print(f"  {sys.executable} scripts/build_valueline.py --daily <code>")
        print("  或等 16:30 的日更自动接管")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
