# -*- coding: utf-8 -*-
"""林奇分类值的**规范枚举**回归测试。

背景（为什么值得单独测）
------------------------
2026-09-18 实测：跟踪池 11 只里，同一个林奇类别出现了三种写法 ——

| 标的 | 池子里的值 |
|---|---|
| 00700 腾讯 | `稳健成长` |
| 600519 茅台 / 600036 招行 / 600887 伊利 / 00300 美的 | `稳健成长型` |
| 600900 长电（已移出） | `稳健增长型 · 收息` |

根因是 `src/report/llm.py` 的 prompt 写「**如** 周期型/稳健成长/…，**可加简短后缀**」
—— 「如」= 举例不是枚举，「可加后缀」= 允许自由发挥，输出必然不稳。
对比表把这几个排一起时，同一类别看起来像三个类别。

修法两层：
1. **源头**：prompt 冻结成 6 个值逐字照抄，注解改走独立字段 `lynch_note`
2. **兜底**：`normalize()` 在**渲染时**把存量自由写法收成规范值 —— 不重调 LLM、不花钱

这里测的就是第 2 层，以及「别再长出第二张同义词表」这条纪律。
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.data import watchlist_store as wl            # noqa: E402
from src.review import lynch as lt                    # noqa: E402

#: 2026-09-18 在真实跟踪池 / 叙事缓存里**实际观测到**的写法，一条都不许漏。
OBSERVED = [
    "周期型",
    "周期型（高股息现金牛）",
    "周期型（高股息资源龙头）",
    "周期型 · 资源/周期",
    "稳健成长",
    "稳健成长型",
    "稳健增长型 · 收息",
    "稳定增长型 · 品牌消费",
    "稳定增长型 · 轻资产",
    "快速成长型",
    "缓慢增长型（高股息现金牛）",
    "缓慢增长型（大盘国有银行）",
    "困境反转（尚未验证）",
    "资产隐蔽",
    "资产富余",
]


# --------------------------------------------------------------------------- #
# 归一化：所有观测到的写法都要落进 6 个规范值
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", OBSERVED)
def test_every_observed_writing_normalizes_into_enum(raw):
    """真实世界里出现过的每一种写法，都必须归一化到 `CANONICAL_TYPES` 之一。"""
    assert lt.normalize(raw) in lt.CANONICAL_TYPES, f"{raw!r} 归一化失败"


@pytest.mark.parametrize("raw,expected", [
    ("稳健成长型", "稳健成长"),
    ("稳健增长型 · 收息", "稳健成长"),        # 「增长」也要吃下
    ("稳定增长型 · 品牌消费", "稳健成长"),
    ("快速成长型", "快速成长"),
    ("快速增长", "快速成长"),
    ("周期型（高股息现金牛）", "周期型"),
    ("资产隐蔽", "资产富余"),                  # 同类的第三种译名
    ("隐蔽资产", "资产富余"),
    ("红利股", "缓慢增长"),
])
def test_writing_variants_map_to_canonical(raw, expected):
    assert lt.normalize(raw) == expected


def test_longer_keyword_wins():
    """最长优先：`稳健成长` 不能被裸 `成长` 抢先判成 `快速成长`。

    这就是别名表必须按长度降序排列的原因（`_ALIAS_SORTED`）。
    """
    assert lt.normalize("稳健成长") == "稳健成长"
    assert lt.normalize("稳健成长型") == "稳健成长"
    assert lt.normalize("缓慢增长") == "缓慢增长"


def test_unknown_returns_empty_not_a_guess():
    """识别不出**返回空串**，绝不猜。

    尤其是 `高速增长`：如果别名表里登记了裸「增长」，它会被静默判成「缓慢增长」
    —— 那正是本项目最忌的静默错数。所以裸「增长」刻意**不登记**。
    """
    for raw in ("", None, "待分析", "待归类", "玄学服务", "某种未知类型", "高速增长"):
        assert lt.normalize(raw) == "", f"{raw!r} 不该被归一化出值"


# --------------------------------------------------------------------------- #
# split_note：分类与注解的拆分
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,body,note", [
    ("周期型（高股息现金牛）", "周期型", "高股息现金牛"),
    ("稳健增长型 · 收息", "稳健增长型", "收息"),
    ("周期型", "周期型", ""),
    ("稳健成长", "稳健成长", ""),
    ("", "", ""),
    ("周期型（煤炭）· 高股息", "周期型", "煤炭 · 高股息"),   # 两种注解并存
    ("周期型(", "周期型(", ""),                              # 括号不闭合：原样返回，不抛
])
def test_split_note(raw, body, note):
    assert lt.split_note(raw) == (body, note)


def test_split_note_order_is_readable():
    """两种注解并存时按阅读顺序输出（括号限定词在前、`·` 补充在后）。"""
    _, note = lt.split_note("周期型（煤炭）· 高股息")
    assert note == "煤炭 · 高股息"


# --------------------------------------------------------------------------- #
# 兼容：classify() 的老契约不能被这次重构改掉（journal.py 依赖它）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("周期型（资源+一体化）", "周期型"),
    ("快速增长", "快速成长"),
    ("红利股", "缓慢增长"),
    ("资产富余", "资产隐蔽"),        # ← 指标表的键名是「资产隐蔽」，这里做一次显式翻译
    ("资产隐蔽", "资产隐蔽"),
    ("某种未知类型", "某种未知类型"),  # 识别不出 → 返回原文（老契约）
    ("", ""),
])
def test_classify_contract_unchanged(raw, expected):
    assert lt.classify(raw) == expected


def test_metrics_for_matches_canonical_types():
    """6 个规范值都要能取到指标清单，且不能落到兜底（兜底=没配上）。"""
    fallback = lt.metrics_for("玄学型")
    for t in lt.CANONICAL_TYPES:
        m = lt.metrics_for(t)
        assert m, f"{t} 没有指标清单"
        assert m != fallback or t == "稳健成长", f"{t} 落到了兜底清单"


# --------------------------------------------------------------------------- #
# 单一真源纪律：别长出第二张同义词表
# --------------------------------------------------------------------------- #

def test_placeholder_set_is_not_duplicated():
    """`watchlist_store` 的占位符集合必须是**同一个对象**（别名引用），不是抄一份。

    抄一份 = 改一处漏一处，这正是 Lynch 词表原先漂移的原因之一。
    """
    assert wl._LYNCH_PLACEHOLDERS is lt.LYNCH_PLACEHOLDERS


def _code_only(txt: str) -> str:
    """剥掉三引号字符串与 `#` 注释，只留可执行代码。

    ⚠️ 必须剥：本项目大量把「举例」「踩坑记录」写在 docstring 里，
    直接扫原文会被自己的注释命中（这个坑 test_placeholders 已经踩过一次）。
    """
    import re as _re

    txt = _re.sub(r'"""(?:.|\n)*?"""', "", txt)
    txt = _re.sub(r"'''(?:.|\n)*?'''", "", txt)
    return "\n".join(l for l in txt.splitlines() if not l.strip().startswith("#"))


def test_no_second_synonym_table_anywhere():
    """全仓只允许 `src/review/lynch.py` 定义「同义写法 → 规范值」这一张映射表。

    查的是**映射的形状**（`"稳健增长型": "稳健成长"`）而不是关键词本身 ——
    `watchlist_store._LYNCH_RULES` 里也出现「稳健增长型」等字样，但那是
    「行业名 → 猜测值」的规则表，是另一回事（且它的输出会走同一个漏斗归一化，
    见 `test_guesser_labels_go_through_the_funnel`）。
    """
    import re as _re

    shape = _re.compile(
        r'["\'](?:[^"\']*?(?:稳健增长|稳定增长|快速增长|资产隐蔽|隐蔽资产|红利)[^"\']*?)["\']'
        r'\s*:\s*["\'](?:' + "|".join(lt.CANONICAL_TYPES) + r')["\']'
    )
    hits = []
    for p in sorted((ROOT / "src").rglob("*.py")):
        if p.name == "lynch.py":
            continue
        for i, line in enumerate(_code_only(p.read_text(encoding="utf-8")).splitlines(), 1):
            if shape.search(line):
                hits.append(f"{p.relative_to(ROOT)}: {line.strip()[:90]}")
    assert not hits, "又出现了第二张同义词表：\n" + "\n".join(hits)


def test_guesser_labels_go_through_the_funnel():
    """关键字猜测器（`_LYNCH_RULES`）的每个标签都要能被归一化 —— 它是「还没报告」的占位值。

    这条替代了「扫源码找同义词表」那种粗糙做法：不禁止标签里出现「稳健增长型」，
    但要求它**必须**能被 `normalize()` 收成规范值，否则池子里又会冒出第二种写法。
    """
    labels = {label for _keys, label in wl._LYNCH_RULES}
    assert labels, "猜测规则表是空的？"
    for label in sorted(labels):
        assert lt.normalize(label) in lt.CANONICAL_TYPES, f"猜测标签 {label!r} 归一化失败"


# --------------------------------------------------------------------------- #
# 落库端：池子里的 lynch 只允许是规范值
# --------------------------------------------------------------------------- #

def test_store_normalizes_and_splits_note(monkeypatch, tmp_path):
    """`watchlist_store._normalize_lynch()` 是最后一道闸。"""
    assert wl._normalize_lynch("周期型（高股息现金牛）") == ("周期型", "高股息现金牛")
    assert wl._normalize_lynch("稳健增长型 · 收息") == ("稳健成长", "收息")
    assert wl._normalize_lynch("待归类") == ("待归类", "")     # 占位值原样返回，由调用方过滤
    assert wl._normalize_lynch("") == ("", "")


def test_store_add_splits_guessed_value(monkeypatch, tmp_path):
    """`add()` 走的关键字猜测也要过同一个漏斗（「还没生成过报告」的标的）。"""
    pool = tmp_path / "watchlist.json"
    pool.write_text('{"version": 1, "stocks": []}', encoding="utf-8")
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pool)
    monkeypatch.setattr(wl, "_display_name", lambda code: "测试标的")
    monkeypatch.setattr(wl, "_guess_industry", lambda code: "银行")

    assert wl.add("601328") is True
    s = wl.get("601328")
    assert s["lynch"] in lt.CANONICAL_TYPES, s["lynch"]
    assert s["lynch"] == "稳健成长"
    assert s["lynch_note"] == "收息"      # 关键字规则的注解拆出来了


def test_store_add_keeps_unrecognized_value_visible(monkeypatch, tmp_path):
    """认不出的猜测值**保留原文**（界面上看得出来），而不是伪装成某个类别。"""
    pool = tmp_path / "watchlist.json"
    pool.write_text('{"version": 1, "stocks": []}', encoding="utf-8")
    monkeypatch.setattr(wl, "WATCHLIST_PATH", pool)
    monkeypatch.setattr(wl, "_display_name", lambda code: "测试标的")
    monkeypatch.setattr(wl, "_guess_industry", lambda code: "玄学服务")

    wl.add("601328")
    s = wl.get("601328")
    assert s["lynch"] == "待归类"          # classify_lynch 的兜底原样落库
    assert s["lynch_note"] == ""


# --------------------------------------------------------------------------- #
# 源头：prompt 必须冻结枚举（防止有人把「如…可加后缀」改回去）
# --------------------------------------------------------------------------- #

def test_prompt_freezes_the_enum():
    """`llm.py` 的 prompt 必须逐个列出 6 个值，且不再出现「可加后缀」这类放行措辞。

    这条是**源码级**断言：行为测试测不到「LLM 会不会自由发挥」，
    只能守住 prompt 文本这个唯一的源头。
    """
    txt = (ROOT / "src" / "report" / "llm.py").read_text(encoding="utf-8")
    line = next((l for l in txt.splitlines() if '"lynch_type"' in l), "")
    assert line, "llm.py 里找不到 lynch_type 的 prompt 定义"
    for t in lt.CANONICAL_TYPES:
        assert t in line, f"prompt 没列出规范值 {t}"
    assert "可加简短后缀" not in line, "prompt 又允许自由加后缀了"
    assert '"lynch_note"' in txt, "注解字段 lynch_note 不在 prompt 里"
    # 反过来也要防：断言那句「必须原样输出」的约束还在
    assert "原样" in line or "必须" in line


# --------------------------------------------------------------------------- #
# 渲染端：build_valueline._lynch_fields 的三元组语义
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def bv():
    """加载 `scripts/build_valueline.py`（模块级会连带 akshare，缺依赖则 skip）。"""
    try:
        return importlib.import_module("build_valueline")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"build_valueline 无法导入：{type(e).__name__}: {e}")


def test_lynch_fields_three_tuple(bv):
    """`(显示值, 规范值, 注解)` —— 显示值给徽章，规范值给跟踪池。"""
    assert bv._lynch_fields("周期型（高股息现金牛）") == ("周期型", "周期型", "高股息现金牛")
    assert bv._lynch_fields("稳健成长型") == ("稳健成长", "稳健成长", "")
    # LLM 显式给了 lynch_note 时优先用它
    assert bv._lynch_fields("周期型（旧注解）", "高股息现金牛") == ("周期型", "周期型", "高股息现金牛")
    # 归一化失败：显示值退回原文（报告如实反映 LLM 说了什么），规范值为空 → 不回写池子
    display, canon, note = bv._lynch_fields("玄学型 · 备注")
    assert display == "玄学型" and canon == "" and note == "备注"


def test_lynch_fields_is_quiet_for_placeholders(bv):
    """「待分析」这类是链路未就位，不是分类异常 —— 不该打告警（否则日志天天刷）。"""
    assert bv._lynch_fields("待分析") == ("待分析", "", "")
    assert bv._lynch_fields("") == ("", "", "")


def test_lynch_fields_warns_when_unrecognized(bv, capsys):
    """认不出时**必须出声** —— 静默降级是本项目反复踩的坑。"""
    bv._lynch_fields("玄学型")
    out = capsys.readouterr().out
    assert "无法归一化" in out
