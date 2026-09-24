# -*- coding: utf-8 -*-
"""归因链路的「变动原因说明」通道（`quarterly_review`）。

问题的形态（2026-09-24 实测 600887 2026 半年报）：
报告页上写着「报告原文未给出具体归因，只能从数据侧观察，无法判断成因」——
看起来像模型能力不足，实际是**输入里根本没有原因**：

- `extract_mdd_text` 抽的是「管理层讨论与分析」章节，但受 `_MDD_MAX_CHARS = 9000`
  截断。该标的 MDD 是第 3–34 页（≈3 万字），9000 字符只读到第 7–8 页；
- 而「财务报表相关科目变动分析表」+ 三条现金流量净额「变动原因说明」在第 **17** 页，
  「资产、负债情况分析」的逐条原因在第 **18–20** 页 —— **全在截断之外**。

所以原因必须**单独抽一段**（`extract_swing_reasons_text`），不能指望 MDD 顺带覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.report import quarterly_review as qr
from src.report.quarterly_review import (
    _MDD_MAX_CHARS,
    _build_prompt,
    extract_mdd_text,
    extract_swing_reasons_text,
)

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "cache" / "disclosure" / "600887" / "600887_2026半年报.pdf"


# --------------------------------------------------------------------------- #
# 抽取器本身（需要本地 PDF；PDF 是数据资产、不入库，缺失时跳过）
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not PDF.exists(), reason="半年报 PDF 不在本地")
def test_extract_gets_cash_flow_reasons():
    t = extract_swing_reasons_text(PDF)
    assert "变动原因说明" in t
    # 三条现金流量净额的原因说明都应在
    assert "销售商品的现金流入增加" in t
    assert "大额存单及定期存款" in t
    assert "支付的股利减少" in t


@pytest.mark.skipif(not PDF.exists(), reason="半年报 PDF 不在本地")
def test_extract_gets_balance_sheet_reasons():
    """资产、负债情况分析的逐条原因 —— 商誉减值这类原因只在权益侧，
    损益候选榜上不会有对应科目，但它是「利润为什么下滑」的关键线索。"""
    t = extract_swing_reasons_text(PDF)
    assert "商誉减少原因" in t
    assert "澳优" in t


@pytest.mark.skipif(not PDF.exists(), reason="半年报 PDF 不在本地")
def test_extract_drops_headers_and_table_numbers():
    """页眉公司名碎片与表格数字行都要清掉：前者会让模型以为正文，后者白占 token
    （金额已在 facts 里，且带着更严格的口径标注）。"""
    t = extract_swing_reasons_text(PDF)
    assert "内蒙古伊利实业集团\n" not in t      # 页眉公司名碎片
    assert "2,455,875" not in t                # 表格单元格金额
    assert "√适用" not in t                     # 表单勾选标记（每段都重复，无信息量）


@pytest.mark.skipif(not PDF.exists(), reason="半年报 PDF 不在本地")
def test_mdd_excerpt_still_misses_the_reasons():
    """🔴 本次修复的**前提事实**：MDD 摘录拿不到「变动原因说明」。

    这条测试是在给两件事上保险：① 若哪天调大 `_MDD_MAX_CHARS` 覆盖到了这张表，
    它会失败 —— 提醒可以合并两条通道、不必再单独抽；② 若有人「优化」成
    从 MDD 里找原因，也会失败。现状是被截断，所以必须单独抽。
    """
    m = extract_mdd_text(PDF)
    assert len(m) <= _MDD_MAX_CHARS
    assert "变动原因说明" not in m


# --------------------------------------------------------------------------- #
# prompt 集成（纯逻辑，不依赖 PDF）
# --------------------------------------------------------------------------- #
def test_prompt_carries_reasons_section():
    p = _build_prompt({"k": 1}, "管理层讨论正文", {"title": "某某 2026 半年报"},
                      "经营活动产生的现金流量净额变动原因说明：主要是销售收现增加。")
    assert "各科目变动原因说明" in p
    assert "主要是销售收现增加" in p


def test_prompt_marks_missing_reasons_explicitly():
    """取不到原文时必须**显式标记**，让模型明说「无法判断成因」而不是自己编。"""
    p = _build_prompt({}, "", None, "")
    assert "未取到" in p
    assert "不得编造成因" in p


def test_prompt_forbids_cross_attribution():
    """不得把 A 科目的原因安到 B 科目头上 —— 这是引入原文后**新增**的风险，
    原文没写就只讲金额与方向。"""
    p = _build_prompt({}, "x", None, "y")
    assert "不得把 A 科目的原因安到 B 科目头上" in p


def test_schema_version_bumped_for_reasons_input():
    """swing 段的语义变了（从「模型推」改成「引用原文」），缓存结构版本必须跟着抬。"""
    assert qr._SCHEMA >= 3
    src = (ROOT / "src" / "report" / "quarterly_review.py").read_text(encoding="utf-8")
    assert "reasons_hash" in src, "「变动原因说明」必须进缓存键，否则抽取器升级后旧解读不失效"


# --------------------------------------------------------------------------- #
# 顺带修的口径：饼图不该把减值算进「营业总成本」
# --------------------------------------------------------------------------- #
def test_pie_groups_exclude_impairment_from_operating_cost():
    """2019 年新金融工具准则起，减值科目**移出了「二、营业总成本」项下**。

    实测伊利 2026H1：营业总成本 551.34 亿 = 营业成本+税金及附加+销售+管理+研发+财务，
    **不含**资产减值 24.56 亿（2026 半年报合并利润表）。

    留在饼图里的后果：正项加总 > 大类总额 → 占比分母偏大、每一项占比都被稀释，
    而饼图仍正常渲染、看不出错。这两行在修复减值字段前一直是空值（从未生效），
    字段一补上就会立刻失真。
    """
    from src.data.adapter import PIE_GROUPS

    items = next(items for title, _field, items in PIE_GROUPS if title == "营业总成本")
    fields = {f for _name, f in items}
    assert "asset_impairment_loss" not in fields
    assert "credit_impairment_loss" not in fields
