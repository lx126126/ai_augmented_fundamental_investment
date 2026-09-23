# -*- coding: utf-8 -*-
"""`scripts/backfill_snapshot.py` 测试（纯函数 + monkeypatch，不联网）。

为什么要有这些测试：这个脚本是「补漏掉的那一天」的唯一正路，而它一开始是作为
一次性脚本躺在 /tmp 里的 —— 一次性脚本没有回归防线，改坏了要等到某天数据错了才发现。
其中 `last_completed_session` / `resolve_target` 决定「回填哪一天」，错了就会把
**别的交易日**的价格写成目标日的收盘价，且零报错。
"""
import sys
from argparse import Namespace
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import backfill_snapshot as bs  # noqa: E402

SER = pd.Series(
    [10.0, 11.0, 12.0, 13.0],
    index=pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"]),
)


def _args(**kw) -> Namespace:
    base = {"date": None, "auto": True, "codes": None, "dry_run": True, "force": False}
    base.update(kw)
    return Namespace(**base)


# ---------------------------------------------------------------- 交易日解析


def test_is_a_share():
    assert bs.is_a_share("601088") is True
    assert bs.is_a_share("300750") is True
    assert bs.is_a_share("00700") is False  # 港股 5 位
    assert bs.is_a_share("09992") is False


def test_last_completed_session_before_close_is_yesterday():
    # 09:11（开盘前）→ 最近已收盘的是昨天
    now = pd.Timestamp("2026-09-23 09:11")
    assert bs.last_completed_session(now) == pd.Timestamp("2026-09-22")


def test_last_completed_session_during_session_is_yesterday():
    # 盘中 10:30 同样还没收盘
    assert bs.last_completed_session(pd.Timestamp("2026-09-23 10:30")) == pd.Timestamp("2026-09-22")


def test_last_completed_session_after_close_is_today():
    # 16:30（plist 的触发时刻）A 股与港股都已收盘
    assert bs.last_completed_session(pd.Timestamp("2026-09-23 16:30")) == pd.Timestamp("2026-09-23")
    # 边界：正好 16:00 算已收盘
    assert bs.last_completed_session(pd.Timestamp("2026-09-23 16:00")) == pd.Timestamp("2026-09-23")
    # 15:59 还不算
    assert bs.last_completed_session(pd.Timestamp("2026-09-23 15:59")) == pd.Timestamp("2026-09-22")


def test_resolve_target_auto_stops_at_last_available_bar():
    """盘前 auto：K 里最新的那根就是昨天（今天那根要么没有、要么是未收盘的盘中 bar）。"""
    now = pd.Timestamp("2026-09-23 09:11")
    assert bs.resolve_target(SER, _args(), now) == pd.Timestamp("2026-09-22")


def test_resolve_target_auto_ignores_todays_partial_bar_before_close():
    """K 里若已混进今天那根盘中 bar，盘前 auto 必须把它排除。"""
    ser = pd.concat([SER, pd.Series([99.0], index=pd.to_datetime(["2026-09-23"]))])
    now = pd.Timestamp("2026-09-23 09:11")
    assert bs.resolve_target(ser, _args(), now) == pd.Timestamp("2026-09-22")


def test_resolve_target_auto_after_close_uses_today():
    ser = pd.concat([SER, pd.Series([99.0], index=pd.to_datetime(["2026-09-23"]))])
    now = pd.Timestamp("2026-09-23 16:30")
    assert bs.resolve_target(ser, _args(), now) == pd.Timestamp("2026-09-23")


def test_resolve_target_skips_holiday_gap_without_a_calendar():
    """节假日不维护日历：K 里没有的日期自动落到上一根（09-21 → 09-18）。"""
    ser = pd.Series([10.0, 11.0], index=pd.to_datetime(["2026-09-17", "2026-09-18"]))
    now = pd.Timestamp("2026-09-22 16:30")
    assert bs.resolve_target(ser, _args(), now) == pd.Timestamp("2026-09-18")


def test_resolve_target_explicit_date_missing_raises():
    with pytest.raises(RuntimeError, match="日 K 里没有"):
        bs.resolve_target(SER, _args(date="2026-09-19", auto=False), pd.Timestamp("2026-09-23 16:30"))


# ---------------------------------------------------------------- 写盘


def _quote_frame(price=10.0, pe=20.0, date="2026-09-18", intraday=None) -> pd.DataFrame:
    return pd.DataFrame([{
        "name": "测试标的", "symbol": "00700", "price": price, "pe": pe,
        "pb": 1.5, "market_cap": 1000.0, "change": 0.0, "change_pct": 0.0,
        "report_date": pd.Timestamp(date), "is_intraday": intraday,
        "price_52w_high": 20.0, "price_52w_low": 5.0,
    }])


@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ROOT", tmp_path)
    monkeypatch.setattr(bs, "kline_of", lambda code: SER)
    monkeypatch.setattr(bs, "valuation_row", lambda code, target: {"pb": 1.8, "market_cap": 1234.0})
    (tmp_path / "data" / "raw" / "00700").mkdir(parents=True)
    (tmp_path / "data" / "market").mkdir(parents=True)
    return tmp_path


def _write(fp: Path, df: pd.DataFrame) -> None:
    df.to_parquet(fp, index=False)


def test_backfill_writes_target_date_price_and_change(fake_root):
    raw_q = fake_root / "data" / "raw" / "00700" / "quote.parquet"
    _write(raw_q, _quote_frame(price=10.0, pe=20.0, date="2026-09-18"))

    ok, _ = bs.backfill_one("00700", _args(date="2026-09-22", auto=False, dry_run=False),
                            pd.Timestamp("2026-09-23 16:30"))
    assert ok is True

    got = pd.read_parquet(raw_q).iloc[0]
    assert pd.Timestamp(got["report_date"]).normalize() == pd.Timestamp("2026-09-22")
    assert got["price"] == 13.0                      # K 的 09-22 收盘
    assert got["is_intraday"] is False or got["is_intraday"] == False  # noqa: E712
    # 09-22 收盘 13.0 vs K 里前一根 09-21 的 12.0 → +8.33%
    assert got["change_pct"] == pytest.approx(8.33, abs=0.01)
    assert got["change"] == pytest.approx(1.0)
    # pe 恒等换算：20.0 × 13/10 = 26.0
    assert got["pe"] == pytest.approx(26.0)
    # 估值取自百度目标日那一行
    assert got["pb"] == pytest.approx(1.8)
    assert got["market_cap"] == pytest.approx(1234.0)
    # 52 周高低沿用旧值（不夹带口径变更）
    assert got["price_52w_high"] == pytest.approx(20.0)


def test_backfill_prev_close_uses_k_position_not_calendar(fake_root):
    """前收取 K 里的**前一交易日**，而不是「昨天」——节假日才不会算错。"""
    ser = pd.Series([10.0, 12.0], index=pd.to_datetime(["2026-09-17", "2026-09-22"]))
    bs.kline_of = lambda code: ser  # 覆盖 fixture 的桩
    raw_q = fake_root / "data" / "raw" / "00700" / "quote.parquet"
    _write(raw_q, _quote_frame(price=10.0, date="2026-09-17"))

    ok, _ = bs.backfill_one("00700", _args(date="2026-09-22", auto=False, dry_run=False),
                            pd.Timestamp("2026-09-23 16:30"))
    assert ok is True
    got = pd.read_parquet(raw_q).iloc[0]
    # 前收 10.0（09-17），不是「昨天」09-21
    assert got["change_pct"] == pytest.approx((12.0 / 10.0 - 1) * 100, abs=0.01)


def test_backfill_skips_when_already_at_target(fake_root):
    raw_q = fake_root / "data" / "raw" / "00700" / "quote.parquet"
    _write(raw_q, _quote_frame(price=13.0, date="2026-09-22", intraday=False))

    ok, msg = bs.backfill_one("00700", _args(date="2026-09-22", auto=False, dry_run=False),
                              pd.Timestamp("2026-09-23 16:30"))
    assert ok is True and "跳过" in msg
    # 未被改写
    assert pd.read_parquet(raw_q).iloc[0]["price"] == 13.0


def test_dry_run_does_not_touch_disk(fake_root):
    raw_q = fake_root / "data" / "raw" / "00700" / "quote.parquet"
    _write(raw_q, _quote_frame(price=10.0, date="2026-09-18"))

    ok, msg = bs.backfill_one("00700", _args(date="2026-09-22", auto=False, dry_run=True),
                              pd.Timestamp("2026-09-23 16:30"))
    assert ok is True and "[dry-run]" in msg
    assert pd.read_parquet(raw_q).iloc[0]["price"] == 10.0


def test_first_bar_has_no_prev_close(fake_root):
    """K 只有一根时取不到前收 → 明确失败，不能拿 0 或自己当分母。"""
    bs.kline_of = lambda code: pd.Series([10.0], index=pd.to_datetime(["2026-09-22"]))
    raw_q = fake_root / "data" / "raw" / "00700" / "quote.parquet"
    _write(raw_q, _quote_frame())

    ok, msg = bs.backfill_one("00700", _args(date="2026-09-22", auto=False, dry_run=False),
                              pd.Timestamp("2026-09-23 16:30"))
    assert ok is False and "第一根" in msg
