# -*- coding: utf-8 -*-
"""网络调用重试：指数退避 + 抖动，供数据拉取层复用。

背景：fetcher 原本裸调 AKShare / requests，网络抖动或接口瞬时限流会导致整次
拉取失败且无重试。本模块提供一个 `retry` 装饰器，用最小侵入方式给各 fetch
函数补上「临时性错误自动重试」，同时不吞掉真正的业务错误（如参数错、数据为空）。

设计取舍：
- 只对「可重试异常」退避重试（网络类：ConnectionError / Timeout / 接口限流），
  对「不可重试」异常（数据为空、字段缺失、参数错）立即失败，避免空转浪费。
- 指数退避 + 随机抖动（full jitter），避免多任务同时重试造成 thundering herd。
- 默认不改变原函数签名与返回语义：重试耗尽后抛出最后一次异常，与裸调用一致。
"""
from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from typing import TypeVar

import pandas as pd

T = TypeVar("T")

# 默认：重试 3 次，退避基数 1s，上限 8s，全抖动
DEFAULT_RETRIES = 3
DEFAULT_BASE = 1.0
DEFAULT_CAP = 8.0

# 可重试异常：网络/超时/限流/连接类。数据为空(ValueError/KeyError)不在此列。
_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,  # 含 requests 的 ConnectionError/SSLError 等底层 socket 异常
)


def _is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试：网络类 → 重试；数据/参数类 → 立即失败。"""
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    # requests 的异常继承自 RequestException，本质是 OSError 子类，已覆盖。
    # 兼容第三方库抛出的「网络」语义异常（字符串兜底，避免漏判）。
    msg = str(exc).lower()
    for kw in ("timed out", "timeout", "connection", "reset", "refused",
               "temporarily", "too many", "rate limit", "429", "503", "502"):
        if kw in msg:
            return True
    return False


def _backoff(attempt: int, base: float, cap: float) -> float:
    """指数退避 + full jitter：sleep = random(0, min(cap, base * 2**attempt))。"""
    return random.uniform(0, min(cap, base * (2 ** attempt)))


def retry(
    retries: int = DEFAULT_RETRIES,
    base: float = DEFAULT_BASE,
    cap: float = DEFAULT_CAP,
    on_error: Callable[[int, BaseException], None] | None = None,
):
    """给网络调用函数加指数退避重试的装饰器。

    Args:
        retries: 最大重试次数（不含首次调用）。
        base: 退避基数（秒），第 n 次重试前 sleep 上限 = base * 2**n。
        cap: 单次退避上限（秒）。
        on_error: 可选回调，签名为 (attempt, exc)，用于日志/告警。

    返回语义与原函数一致：成功返回结果，重试耗尽抛出最后一次异常。
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - 需要捕获一切以判断是否可重试
                    if not _is_retryable(exc) or attempt >= retries:
                        raise
                    attempt += 1
                    delay = _backoff(attempt, base, cap)
                    if on_error is not None:
                        on_error(attempt, exc)
                    time.sleep(delay)
        return wrapper
    return decorator


def empty_or_none(df: pd.DataFrame | None) -> bool:
    """判断数据是否为空/None，供幂等与兜底判断复用。"""
    return df is None or (isinstance(df, pd.DataFrame) and df.empty)
