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
