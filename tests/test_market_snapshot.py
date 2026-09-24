"""market_snapshot 模块测试：港股/市场后缀判定 + 墙钟超时防线（无网络依赖）。

为什么超时也要写测试
--------------------
2026-09-23 16:30 的日更**挂死 17 小时**（某次 akshare 调用被链路静默挂死），
后果是 launchd 认为作业一直在 `running`，**此后每天的定时刷新都不再被拉起** ——
数据静默停在 09-22，页面上、日志里零报错。这类「不报错、只是永远不结束」的 bug
不会出现在任何指标上，只能靠断言守住，所以下面用「假装挂死的函数」直接测行为。
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.market_snapshot import is_hk, _is_a_share


def test_is_hk_true_for_hk_suffix():
    assert is_hk("hk") is True
    assert is_hk("HK") is True


def test_is_hk_false_for_a_share():
    assert is_hk("sh") is False
    assert is_hk("sz") is False
    assert is_hk(None) is False
    assert is_hk("") is False


def test_is_a_share_for_a_share_codes():
    assert _is_a_share("601088") is True  # 沪市
    assert _is_a_share("000651") is True  # 深市主板
    assert _is_a_share("300750") is True  # 创业板
    assert _is_a_share("830799") is True  # 北交所


def test_is_a_share_ambiguous_for_hk_numeric_code():
    # 港股 09992 剥后缀后是 0 开头，会被 _is_a_share 误判为 A 股——
    # 这正是「是否港股必须以后缀为准」的原因（is_hk 前置拦截，_is_a_share 仅粗防呆）。
    assert _is_a_share("09992") is True


# --------------------------------------------------------------------------- #
# 墙钟超时防线
# --------------------------------------------------------------------------- #

def _hang(code, market=None):
    """假装被链路挂死的取数：既不返回也不报错。"""
    time.sleep(10)
    return None


def test_call_with_timeout_raises_timeouterror():
    from src.data.fetcher import call_with_timeout

    with pytest.raises(TimeoutError):
        call_with_timeout(_hang, 0.2, "601088")


def test_call_with_timeout_passes_args_and_kwargs():
    from src.data.fetcher import call_with_timeout

    def _echo(code, market=None):
        return code, market

    assert call_with_timeout(_echo, 5, "00700", market="hk") == ("00700", "hk")


def test_fetch_guarded_returns_none_with_reason(monkeypatch, capsys):
    """超时必须**换成 None + 一行原因**，而不是抛给上层或静默吞掉。"""
    import src.data.market_snapshot as ms

    monkeypatch.setattr(ms, "SNAPSHOT_TIMEOUT_S", 0.2)
    assert ms._fetch_guarded("601088", "行情", _hang, market=None) is None

    out = capsys.readouterr().out
    assert "601088" in out and "超时" in out


def test_fetch_guarded_passes_code_and_kwargs(monkeypatch):
    import src.data.market_snapshot as ms

    seen: dict = {}

    def _fn(code, market=None):
        seen["code"], seen["market"] = code, market
        return "ok"

    monkeypatch.setattr(ms, "SNAPSHOT_TIMEOUT_S", 5)
    assert ms._fetch_guarded("00700", "行情", _fn, market="hk") == "ok"
    assert seen == {"code": "00700", "market": "hk"}


def test_snapshot_quote_gives_up_instead_of_hanging(monkeypatch):
    """核心回归：挂死的行情接口不能让日更无限等待（含首拉 + 重试两次）。"""
    import src.data.market_snapshot as ms

    monkeypatch.setattr(ms, "SNAPSHOT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(ms, "_QUOTE_RETRY_DELAY", 0.01)
    monkeypatch.setattr(ms, "fetch_quote", _hang)

    t0 = time.monotonic()
    assert ms.snapshot_quote("601088") is None
    # 两次调用各 0.2s + 极小重试间隔；无超时保护时这里会等 20s 以上。
    assert time.monotonic() - t0 < 5
