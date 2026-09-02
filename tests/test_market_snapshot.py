"""market_snapshot 模块测试：港股/市场后缀判定（纯函数，无网络依赖）。"""
import sys
from pathlib import Path

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
