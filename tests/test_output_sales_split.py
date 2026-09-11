# -*- coding: utf-8 -*-
"""产销量拆分的解析守卫。

背景（一次真实误读）：展示层一度只取「产销量情况分析表」的酒类合计
（2025 年报：生产量 116,123.73 吨 / 销售量 85,104.14 吨），读者会直接读成
「茅台酒一年产 11.6 万吨、卖 8.5 万吨」。而年报「产品情况」表写得很清楚：
茅台酒 58,473.16 吨 + 系列酒 57,650.57 吨，几乎各占一半。

这类错误不会报错、不会崩、数字还来自原文，只能靠测试钉死三条底线：
  1. 拆分要能解析出来，并与合计行勾稽（合计 = 分项之和）；
  2. 只解析出一档时整体丢弃——单档展示会把「茅台酒 + 系列酒」静默读成「茅台酒」；
  3. 产能表里的「茅台酒制酒车间 / 系列酒制酒车间」不能被当成产品行。
"""
from pathlib import Path

import pytest

from src.report.mda_extract import (
    _product_rows,
    _sum_matches,
    extract,
    parse_inventory_split,
    parse_output_sales,
    parse_product_lines,
)

# 年报「产销量情况分析表」：只有一行「酒类」合计（真实原文，2025 年报 page 10）
_OUTPUT_TABLE = """产销量情况分析表
√适用□不适用
主要产品
单位
生产量
销售量
库存量
生产量比
上年增减
（%）
销售量比
上年增减
（%）
库存量比
上年增减
（%）
酒类
吨
116,123.73
85,104.14
339,977.86
11.25
2.13
9.67
(3). 重大采购合同、重大销售合同的履行情况
"""

# 年报「4、产品情况」：茅台酒 / 系列酒的产量·销量·收入（真实原文，page 16）
# 注意「产销率」列在年报里为空，文本中不出现，因此每行只有 6 个数字
_PRODUCT_TABLE = """4、产品情况
√适用
□不适用
单位：万元
币种：人民币
产品档次
产量
（吨）
同比
（%）
销量
（吨）
同比（%）
产销
率(%)
销售
收入
同比
（%）
主要代表品牌
茅台酒
58,473.16
3.91
46,750.66
0.73
14,649,990.65
0.39
贵州茅台酒
其他系列酒
57,650.57
19.82
38,353.48
3.88
2,227,467.87
-9.76
茅台王子酒、茅
台1935 酒、汉
酱酒、赖茅酒
注：（1）为保证公司可持续发展，每年需留存一定量的基酒。
产品档次划分标准
√适用
□不适用
按产品品质划分。
5、原料采购情况
"""

# 年报「产品期末库存量」（真实原文，page 16）
_INVENTORY_TABLE = """3、产品期末库存量
√适用
□不适用
单位：吨
成品酒
半成品酒（含基础酒）
24,770.35
315,207.51
注：成品酒为公司已包装的库存商品（含酱香系列酒）。
"""

# 年报「2、产能状况」：车间名里带「茅台酒 / 系列酒」，是最容易被误吃的段落
_CAPACITY_TABLE = """2、产能状况
现有产能
√适用
□不适用
主要工厂名称
设计产能
实际产能
茅台酒制酒车间
46,395.00
58,473.16
系列酒制酒车间
59,400.00
57,650.57
"""


# --------------------------------------------------------------------------- #
# 拆分解析
# --------------------------------------------------------------------------- #
def test_parses_both_tiers_with_correct_values():
    rows = parse_product_lines(_PRODUCT_TABLE)
    assert [r["档次"] for r in rows] == ["茅台酒", "其他系列酒"]
    maotai, series = rows
    assert maotai["产量"] == 58473.16
    assert maotai["销量"] == 46750.66
    assert maotai["收入_亿元"] == 1465.0          # 14,649,990.65 万元
    assert maotai["收入同比_pct"] == 0.39
    assert series["产量"] == 57650.57
    assert series["销量"] == 38353.48
    assert series["收入同比_pct"] == -9.76        # 负号必须保留


def test_single_tier_is_discarded():
    """只解析出一档时返回 None：单档会把两档之和静默读成第一档。"""
    only_maotai = _PRODUCT_TABLE.replace(
        """其他系列酒
57,650.57
19.82
38,353.48
3.88
2,227,467.87
-9.76
""", "")
    assert parse_product_lines(only_maotai) is None


def test_capacity_table_is_not_mistaken_for_products():
    """车间名包含「茅台酒 / 系列酒」，用「包含」判定会把产能表吃成产品行。"""
    assert _product_rows(_CAPACITY_TABLE) == []
    assert parse_product_lines(_CAPACITY_TABLE) is None


def test_toc_hit_does_not_block_later_real_table():
    """目录里也会出现「4、产品情况」，窗口命中目录时必须继续往后找正文表格。"""
    filler = "目录文字占位。" * 200          # 把真实表格推出第一个 1500 字窗口
    text = "4、产品情况 ......... 16\n" + filler + _PRODUCT_TABLE
    rows = parse_product_lines(text)
    assert [r["档次"] for r in rows] == ["茅台酒", "其他系列酒"]


# --------------------------------------------------------------------------- #
# 库存拆分
# --------------------------------------------------------------------------- #
def test_inventory_split_parsed():
    inv = parse_inventory_split(_INVENTORY_TABLE)
    assert inv == {"成品酒": 24770.35, "半成品酒": 315207.51, "单位": "吨"}


def test_inventory_split_rejects_impossible_order():
    """成品酒不可能多于半成品基酒；出现即说明读错了列，宁可不出。"""
    bad = _INVENTORY_TABLE.replace("24,770.35", "400,000.00")
    assert parse_inventory_split(bad) is None


# --------------------------------------------------------------------------- #
# 勾稽
# --------------------------------------------------------------------------- #
def test_sum_matches_reconciles_real_numbers():
    assert _sum_matches([58473.16, 57650.57], 116123.73)
    assert _sum_matches([46750.66, 38353.48], 85104.14)
    assert _sum_matches([24770.35, 315207.51], 339977.86)


def test_sum_matches_rejects_mismatch_and_degenerate_input():
    assert not _sum_matches([58473.16, 57650.57], 999999.99)   # 对不上合计
    assert not _sum_matches([58473.16], 58473.16)              # 只有一项，不构成拆分
    assert not _sum_matches([], 116123.73)
    assert not _sum_matches([1, 2], None)
    assert not _sum_matches([1, 2], 0)


# --------------------------------------------------------------------------- #
# extract 级：拆分挂载与否决
# --------------------------------------------------------------------------- #
def _extract_from_text(monkeypatch, text):
    from src.report import mda_extract as me
    monkeypatch.setattr(me, "_read_text", lambda _p: text)
    return me.extract(Path("dummy.pdf"))


def test_extract_attaches_split_and_inventory_when_reconciled(monkeypatch):
    out = _extract_from_text(
        monkeypatch, _OUTPUT_TABLE + _PRODUCT_TABLE + _INVENTORY_TABLE)
    prod = out["产销量"][0]
    assert [r["档次"] for r in prod["拆分"]] == ["茅台酒", "其他系列酒"]
    assert prod["库存拆分"]["半成品酒"] == 315207.51
    assert "口径" in prod


def test_extract_drops_split_when_totals_do_not_reconcile(monkeypatch):
    """合计行被改动后，分项与合计对不上 → 拆分必须整块丢弃，只留合计。"""
    broken = _OUTPUT_TABLE.replace("116,123.73", "999,999.99")
    out = _extract_from_text(monkeypatch, broken + _PRODUCT_TABLE)
    prod = out["产销量"][0]
    assert prod["生产量"] == 999999.99
    assert "拆分" not in prod


def test_extract_without_product_table_still_reports_totals(monkeypatch):
    """年报版式变化导致拆分解析失败时，仍要给出合计行（优雅降级）。"""
    out = _extract_from_text(monkeypatch, _OUTPUT_TABLE)
    prod = out["产销量"][0]
    assert prod["生产量"] == 116123.73
    assert "拆分" not in prod


# --------------------------------------------------------------------------- #
# 真实年报集成（PDF 属数据资产，缺失时跳过）
# --------------------------------------------------------------------------- #
_REAL_PDF = (Path(__file__).resolve().parent.parent
             / "data/cache/disclosure/600519/600519_2025年报.pdf")


@pytest.mark.skipif(not _REAL_PDF.exists(), reason="缺少茅台年报 PDF")
def test_real_annual_report_split_reconciles():
    out = extract(_REAL_PDF)
    prod = out["产销量"][0]
    rows = prod["拆分"]
    assert len(rows) == 2
    assert _sum_matches([r["产量"] for r in rows], prod["生产量"])
    assert _sum_matches([r["销量"] for r in rows], prod["销售量"])
    assert _sum_matches([prod["库存拆分"]["成品酒"], prod["库存拆分"]["半成品酒"]],
                        prod["库存量"])
