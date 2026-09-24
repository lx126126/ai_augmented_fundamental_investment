# -*- coding: utf-8 -*-
"""减值科目的「新旧准则双字段」合并（`fields.IMPAIRMENT_FIELDS` + `fetcher._merge_impairment`）。

背景（2026-09-24 实测）：东财在 2018 年新金融工具准则实施后把减值字段换了后缀，
两个字段的时间区间互补，且**符号约定相反**：

    ASSET_IMPAIRMENT_LOSS     2006-06-30 ~ 2018-03-31（48 期）  正数 = 损失
    ASSET_IMPAIRMENT_INCOME   2018-06-30 起（33 期）            负数 = 损失（"损失以-号填列"）
    CREDIT_IMPAIRMENT_LOSS    从来没填过（0 期）
    CREDIT_IMPAIRMENT_INCOME  2019-03-31 起（30 期）

只映射 LOSS 版 → 资产减值损失自 2018Q2 起整列为空 → `swing` 候选榜配了这个科目
却永远取不到值 → 报告「主要变动指标归因」拿不到「利润为什么下滑」最常用的那一个入口
（伊利 2026H1 资产减值 24.6 亿、含澳优商誉减值 15.5 亿，正是利润下滑主因）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.fetcher import _merge_impairment
from src.data.fields import IMPAIRMENT_FIELDS, PROFIT_SHEET_MAP


def test_new_field_is_negated_when_backfilling():
    """新准则字段「损失以"-"号填列」→ 取负，与旧字段统一成「正数 = 损失」。

    不取负的话，同一条时间序列会在 2018 年中途反向：伊利 2018Q1 = +0.18 亿、
    2018Q2 = -0.40 亿，读起来像「由损失转收益」。
    """
    raw = pd.DataFrame({
        "ASSET_IMPAIRMENT_LOSS": [18_025_765.82, None],
        "ASSET_IMPAIRMENT_INCOME": [None, -2_455_875_173.02],
    })
    df = pd.DataFrame({"asset_impairment_loss": [18_025_765.82, None]})
    out = _merge_impairment(df, raw)
    # 旧准则期保持原样（已是正数 = 损失）
    assert out["asset_impairment_loss"].iloc[0] == pytest.approx(18_025_765.82)
    # 新准则期取负 → 与上一期同向，恢复可比
    assert out["asset_impairment_loss"].iloc[1] == pytest.approx(2_455_875_173.02)


def test_existing_value_is_not_overwritten():
    """两个字段的时间区间互补，已由旧字段填上的期不得被新字段覆盖。"""
    raw = pd.DataFrame({
        "ASSET_IMPAIRMENT_LOSS": [111.0],
        "ASSET_IMPAIRMENT_INCOME": [-999.0],
    })
    df = pd.DataFrame({"asset_impairment_loss": [111.0]})
    out = _merge_impairment(df, raw)
    assert out["asset_impairment_loss"].iloc[0] == 111.0


def test_credit_impairment_only_exists_in_new_field():
    """信用减值损失是新准则才有的科目：旧字段 0 期有值，只能靠新字段。"""
    raw = pd.DataFrame({
        "CREDIT_IMPAIRMENT_LOSS": [None],
        "CREDIT_IMPAIRMENT_INCOME": [-4_906_718.67],
    })
    df = pd.DataFrame({"credit_impairment_loss": [None]})
    out = _merge_impairment(df, raw)
    assert out["credit_impairment_loss"].iloc[0] == pytest.approx(4_906_718.67)


def test_missing_columns_are_skipped():
    """港股/银行等报表没有这些列时静默跳过 —— 绝不允许为「补全」而反推一个数。"""
    raw = pd.DataFrame({"OTHER": [1.0]})
    df = pd.DataFrame({"asset_impairment_loss": [None]})
    out = _merge_impairment(df, raw)
    assert out["asset_impairment_loss"].isna().all()
    # 标准列本身不存在时也不能抛异常
    _merge_impairment(pd.DataFrame({"revenue": [1.0]}), raw)


def test_income_field_is_not_in_profit_sheet_map():
    """🔴 新字段**不能**写进 `PROFIT_SHEET_MAP`。

    `_remap` 是 `df[cols].rename(columns=cols)`，两个键映射到同一个标准名会产出
    **两列同名列**，后一列静默覆盖前一列 —— 不报错，只是 2018 年后仍然全空，
    与修复前的现象一模一样。这条断言防的就是「顺手加回来」。
    """
    assert "ASSET_IMPAIRMENT_INCOME" not in PROFIT_SHEET_MAP
    assert "CREDIT_IMPAIRMENT_INCOME" not in PROFIT_SHEET_MAP
    for std, (old_col, _new_col) in IMPAIRMENT_FIELDS.items():
        assert PROFIT_SHEET_MAP.get(old_col) == std, f"{old_col} 应仍映射到 {std}"


def test_real_parquet_has_impairment_after_2018():
    """落盘数据必须已带上 2018 年后的减值值 —— 抽取修了但 raw 没重跑的话，
    报告链路（直读 parquet）仍然拿不到数。

    ⚠️ raw 是数据资产、不入库，且重跑要联网；没重跑时跳过（而不是判失败），
    重跑之后这条就转为真正的回归断言。"""
    p = Path(__file__).resolve().parent.parent / "data" / "raw" / "600887" / "profit_sheet.parquet"
    if not p.exists():
        pytest.skip("raw parquet 不在本地")
    df = pd.read_parquet(p)
    assert "asset_impairment_loss" in df.columns, "profit_sheet 缺 asset_impairment_loss 列"
    late = df[df["report_date"] >= pd.Timestamp("2019-01-01")]
    if late["asset_impairment_loss"].isna().all():
        pytest.skip("该标的 raw 尚未重跑抽取（重跑后本测试转为断言）")
    assert late["asset_impairment_loss"].notna().any()

    # 🔴 锚点判据：2026H1 合并利润表原文「资产减值损失（损失以"-"号填列）」
    #    = −2,455,875,173.02 → 归一后应为 **正** 24.5588 亿。
    #    这道锚点同时挡两种错法：漏取负 → −24.56 亿；重复取负 → 也是负数。
    anchor = df[df["report_date"] == pd.Timestamp("2026-06-30")]
    if not anchor.empty and pd.notna(anchor["asset_impairment_loss"].iloc[0]):
        assert anchor["asset_impairment_loss"].iloc[0] == pytest.approx(
            2_455_875_173.02, rel=1e-6), "与半年报原文对不上 —— 符号或取值错了"

    # 允许**小额**负值：资产减值净转回是真实业务（实测 2019 年三期 −2.5 万 ~ −17.2 万）。
    # 但绝不允许亿级负值 —— 那意味着符号整体反了，损失被当成收益。
    vals = late["asset_impairment_loss"].dropna()
    big_neg = vals[vals < -1e8]
    assert big_neg.empty, f"出现亿级负值，符号疑似整体反向：{big_neg.tolist()[:5]}"
