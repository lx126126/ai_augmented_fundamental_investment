"""港股数据层测试：代码规范化 + 分红文本提取 + 半年度差分（纯函数，无网络依赖）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.data.fetcher import _hk_code, _extract_hk_dividend


def test_hk_code_strips_suffix():
    assert _hk_code("09992.HK") == "09992"
    assert _hk_code("09992") == "09992"
    assert _hk_code("00700.HK") == "00700"
    assert _hk_code("9992") == "09992"  # 补前导零到 5 位


def test_hk_code_case_insensitive():
    assert _hk_code("09992.hk") == "09992"
    assert _hk_code("00700.HK") == "00700"


def test_fetch_hk_profile_has_company_name():
    """港股 profile 提取「公司名称」字段（quote 行情失败时的公司名兜底源）。"""
    from unittest import mock
    import src.data.fetcher as ft

    fake = mock.Mock()
    fake.iloc = [{"公司名称": "腾讯控股有限公司", "所属行业": "软件服务", "公司介绍": "互联网科技公司"}]
    fake.empty = False

    with mock.patch.object(ft.ak, "stock_hk_company_profile_em", return_value=fake):
        df = ft.fetch_hk_profile("00700")

    assert df is not None
    assert df.iloc[0]["company_name"] == "腾讯控股有限公司"
    assert df.iloc[0]["industry"] == "软件服务"


def test_adapter_norm_code_hk():
    """adapter._norm_code 与 fetcher._hk_code 港股规范化对齐（00700.HK→00700）。"""
    from src.data.adapter import _norm_code
    assert _norm_code("00700.HK") == "00700"
    assert _norm_code("09992.HK") == "09992"
    assert _norm_code("09992") == "09992"


def test_adapter_norm_code_a_share():
    """A 股代码 zfill 6（601088 不变，688 补零）。"""
    from src.data.adapter import _norm_code
    assert _norm_code("601088") == "601088"
    assert _norm_code("688") == "000688"


def test_extract_hk_dividend_rmb_priority():
    # 优先取人民币口径
    assert _extract_hk_dividend("每股派人民币0.8146元(相当于港币0.8881元)") == 0.8146


def test_extract_hk_dividend_hkd_fallback():
    # 无人民币时取港币
    assert _extract_hk_dividend("每股派港币0.5元") == 0.5


def test_extract_hk_dividend_plain():
    # 无币种兜底
    assert _extract_hk_dividend("每股派0.2元") == 0.2


def test_extract_hk_dividend_none():
    assert _extract_hk_dividend("不派息") is None
    assert _extract_hk_dividend("") is None


# ---------------------------------------------------------------------------
# 港股半年度差分：H1 是独立累计起点，H2 = FY − H1（区别于 A 股 Q1-Q4 连续累计）
# ---------------------------------------------------------------------------

def test_to_single_half_yearly_h1_is_start():
    """港股半年报（无 3 月数据）时，6 月是独立累计起点，不差分。"""
    from src.data.cleaner import _to_single
    df = pd.DataFrame({
        "report_date": pd.to_datetime(["2024-12-31", "2025-06-30", "2025-12-31"]),
        "revenue": [200.0, 120.0, 300.0],  # FY24=200, H1=120(独立), FY25=300
    })
    out = _to_single(df, ["revenue"])
    # H1 取累计值本身(120)，H2 = FY25 − H1 = 180
    assert out["revenue"].tolist() == [200.0, 120.0, 180.0]


def test_to_single_a_share_q1_is_start_unchanged():
    """A 股季报（有 3 月数据）时，Q1 仍是起点，Q2 差分，不受港股改动影响。"""
    from src.data.cleaner import _to_single
    df = pd.DataFrame({
        "report_date": pd.to_datetime(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]),
        "revenue": [50.0, 120.0, 210.0, 300.0],  # Q1=50, Q2=70, Q3=90, Q4=90
    })
    out = _to_single(df, ["revenue"])
    assert out["revenue"].tolist() == [50.0, 70.0, 90.0, 90.0]


def test_period_label_half_yearly():
    """港股半年度标签：6 月 → H1，12 月 → H2。"""
    from src.data.adapter import _period_label
    import pandas as pd
    assert _period_label(pd.Timestamp("2025-06-30"), half_yearly=True) == "25H1"
    assert _period_label(pd.Timestamp("2025-12-31"), half_yearly=True) == "25H2"


def test_period_label_quarterly_unchanged():
    """A 股季报标签仍为 Q1-Q4。"""
    from src.data.adapter import _period_label
    import pandas as pd
    assert _period_label(pd.Timestamp("2025-09-30"), half_yearly=False) == "25Q3"
    assert _period_label(pd.Timestamp("2025-06-30"), half_yearly=False) == "25Q2"


# ---------------------------------------------------------------------------
# 港股行情股息率字段：腾讯港股 f[47]=股息率(%)，与 A 股 f[47]=52周高不同
# ---------------------------------------------------------------------------

def test_hk_quote_dividend_yield_field():
    """港股 fetch_quote 从 f[47] 读股息率，而非 A 股的 52 周高字段。"""
    import re
    from unittest import mock
    from src.data.fetcher import fetch_quote

    # 构造港股行情串：f[47]=1.79(股息率)、f[48]=313.475(52周高)、f[49]=137.375(52周低)
    f = [""] * 60
    f[1] = "泡泡玛特"
    f[3] = "154.100"
    f[45] = "2052.27"
    f[47] = "1.79"        # 股息率
    f[48] = "313.475"     # 52周最高
    f[49] = "137.375"     # 52周最低
    f[57] = "13.46"       # PE(TTM)
    f[58] = "7.73"        # PB
    payload = "v_hk09992=\"" + "~".join(f) + "\""

    with mock.patch("requests.get") as mg:
        mg.return_value.text = payload
        mg.return_value.raise_for_status = lambda: None
        q = fetch_quote("09992", market="hk")

    assert q.iloc[0]["dividend_yield"] == 1.79
    assert q.iloc[0]["price_52w_high"] == 313.475
    assert q.iloc[0]["price_52w_low"] == 137.375
    assert q.iloc[0]["pe"] == 13.46
    assert q.iloc[0]["pb"] == 7.73


def test_a_quote_no_dividend_yield_field():
    """A 股 fetch_quote 不返回 dividend_yield（股息率由分红接口提供）。"""
    from unittest import mock
    from src.data.fetcher import fetch_quote

    f = [""] * 50
    f[1] = "中国神华"
    f[3] = "40.00"
    f[39] = "10.0"
    f[45] = "6000.0"
    f[46] = "1.5"
    f[47] = "45.0"
    f[48] = "35.0"
    payload = "v_sh601088=\"" + "~".join(f) + "\""

    with mock.patch("requests.get") as mg:
        mg.return_value.text = payload
        mg.return_value.raise_for_status = lambda: None
        q = fetch_quote("601088")

    assert "dividend_yield" not in q.columns
