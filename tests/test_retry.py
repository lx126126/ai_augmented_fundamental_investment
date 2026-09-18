# -*- coding: utf-8 -*-
"""单元测试：retry 重试装饰器（可重试判定 / 退避 / 重试语义）。"""
from src.data.retry import _backoff, _is_retryable, retry


def test_retryable_network_errors():
    """网络/超时/连接类异常应判定为可重试。"""
    assert _is_retryable(TimeoutError("timed out"))
    assert _is_retryable(ConnectionError("connection refused"))
    assert _is_retryable(OSError("connection reset by peer"))
    assert _is_retryable(Exception("429 Too Many Requests"))
    assert _is_retryable(Exception("HTTP 503 Service Unavailable"))


def test_non_retryable_data_errors():
    """数据为空 / 字段缺失 / 参数错不应重试（避免空转）。"""
    assert not _is_retryable(ValueError("empty dataframe"))
    assert not _is_retryable(KeyError("missing column"))
    assert not _is_retryable(TypeError("bad argument"))


def test_backoff_within_bounds():
    """指数退避 + 抖动，sleep 值应在 [0, cap] 且不超过 2**attempt。"""
    for attempt in range(5):
        d = _backoff(attempt, base=1.0, cap=8.0)
        assert 0 <= d <= min(8.0, 2 ** attempt)


def test_retry_succeeds_after_transient_failure():
    """前两次网络失败、第三次成功 → 返回结果，共调用 3 次。"""
    calls = {"n": 0}

    @retry(retries=3, base=0.01, cap=0.05)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("temporarily unavailable")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_non_retryable_raises_immediately():
    """不可重试异常应立即抛出，不重试（只调用 1 次）。"""
    calls = {"n": 0}

    @retry(retries=3, base=0.01)
    def bad():
        calls["n"] += 1
        raise ValueError("data empty")

    try:
        bad()
        assert False, "应抛出 ValueError"
    except ValueError:
        pass
    assert calls["n"] == 1


def test_retry_exhausted_raises_last_exception():
    """重试耗尽后抛出最后一次异常，总调用 = 1 + retries。"""
    calls = {"n": 0}

    @retry(retries=2, base=0.01)
    def always_fail():
        calls["n"] += 1
        raise ConnectionError("down")

    try:
        always_fail()
        assert False, "应抛出 ConnectionError"
    except ConnectionError as e:
        assert str(e) == "down"
    assert calls["n"] == 3


# --------------------------------------------------------------------------- #
# 回归：fetch_competition 曾把异常吞在函数内部且没挂 @retry()
# --------------------------------------------------------------------------- #

def _yjbb_fake():
    """东财业绩报表的最小可用替身（列名与真实返回一致）。"""
    import pandas as pd
    return pd.DataFrame({
        "股票代码": ["600900", "600011"],
        "股票简称": ["长江电力", "华能国际"],
        "所处行业": ["电力", "电力"],
        "营业总收入-营业总收入": [8.6e10, 2.3e11],
        "净利润-净利润": [3.45e10, 1.44e10],
    })


def test_fetch_competition_retries_then_succeeds(monkeypatch):
    """网络抖动后应自动重试并成功返回。

    背景（2026-09-18 实测）：这个接口分 24 页拉全市场约 1.1 万行，实测单次 12.7s ~ 82s。
    新入池的 600900 首次生成报告时它失败过一次 —— 而当时既没挂 `@retry()`，
    异常又被函数内的 `except Exception: return None` 挡住（**即使挂了装饰器也看不到异常**），
    于是 `competition.parquet` 静默不落盘，连带三处症状全不报错。
    """
    from src.data import fetcher

    calls = {"n": 0}

    def flaky(date=None, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("temporarily unavailable")
        return _yjbb_fake()

    monkeypatch.setattr(fetcher.ak, "stock_yjbb_em", flaky)
    out = fetcher.fetch_competition("600900", "20251231")
    assert calls["n"] == 2, "首次失败后没有重试"
    assert out is not None and len(out) == 2
    assert out["industry"].iloc[0] == "电力"


def test_fetch_competition_gives_up_with_none(monkeypatch):
    """重试耗尽后**返回 None 而不是抛出** —— competition 是尽力而为：

    同业数据缺失不该拖垮同一只标的的其余 11 张表（`fetch_all` 是串行组装一个 dict）。
    """
    from src.data import fetcher

    def always_fail(date=None, **_kw):
        raise ConnectionError("down")

    monkeypatch.setattr(fetcher.ak, "stock_yjbb_em", always_fail)
    assert fetcher.fetch_competition("600900", "20251231") is None
