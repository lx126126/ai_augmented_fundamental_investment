# -*- coding: utf-8 -*-
"""单元测试：PDF 值 vs 接口值的可信度判据（`src/plausibility.py`）。

这层判据的作用是拦住「PDF 解析错、却把正确的接口值覆盖掉」。真实事故（2026-09-18）：
美的 000333 利润表单位是「千元」，而 `_UNIT_MULTIPLIER` 漏登记千元 →
销售费用被解析成 0.4289 亿（真值 428.9149 亿）→ 判为「接口错误」覆盖进报告。

判据历史上踩过两个坑，本文件把它们钉住：
① 门槛写成百分比、值取 `100_000`，而 1000 倍对应的百分比是 **99,900** —— 差一点点，
   恰好把唯一要防的那一类整类漏掉；
② 百分比判据**不对称**（分母是 PDF），「PDF 偏大 1000 倍」只算出 100%，方向一反就放行。
"""
import pytest

from src.plausibility import IMPLAUSIBLE_DIFF_PCT, IMPLAUSIBLE_RATIO, is_implausible


def test_thresholds_are_derived_from_each_other():
    """百分比阈值必须由倍率**派生** —— 两个数字各写一份必然漂移。"""
    assert IMPLAUSIBLE_RATIO == 100
    assert IMPLAUSIBLE_DIFF_PCT == (IMPLAUSIBLE_RATIO - 1) * 100 == 9900


@pytest.mark.parametrize("factor,expected", [
    (1.0, False),        # 完全一致
    (1.01, False),       # 1% 容差内
    (1.25, False),       # 神华应收账款 165.66 vs 132.25
    (2.05, False),       # 神华总负债
    (31.0, False),       # 神华短期借款 131.18 vs 4.09 —— 作者认可的极端重述差异
    (99.0, False),       # 贴近门槛内侧
    (100.0, True),       # 门槛本身：100 倍即判不可信
    (101.0, True),
    (1000.0, True),      # 千元/元 错位 —— 本次事故的真实倍数
    (10000.0, True),     # 百万元/元 错位
    (999999.0, True),    # 交行历史上算出的倍数
])
def test_ratio_symmetric_in_both_directions(factor, expected):
    """判据必须对「PDF 偏小」与「PDF 偏大」**同样**成立（对称）。

    旧的百分比判据只有「PDF 偏小」方向会算出大百分比，「偏大」方向只算出 ~100%，
    同一个数量级的解析错误方向一反就放行。
    """
    base = 100.0
    assert is_implausible(base * factor, base) is expected, f"PDF 偏小 {factor} 倍"
    assert is_implausible(base, base * factor) is expected, f"PDF 偏大 {factor} 倍"


@pytest.mark.parametrize("api,pdf", [
    (0.0, 0.0),          # 两侧都是 0 → 无可比性，不算异常
    (0.0, 100.0),        # 一侧为 0 另一侧非 0 → 判为解析失败
    (100.0, 0.0),
    (-100.0, 0.0),       # 符号不影响量级判定（费用类科目常为负）
])
def test_zero_edge_cases(api, pdf):
    expected = (api == 0.0) != (pdf == 0.0)
    assert is_implausible(api, pdf) is expected


def test_negative_values_use_absolute_magnitude():
    """负值（费用/现金流流出）与正值同量级时不应被判为异常。"""
    assert is_implausible(-428.9149e8, 428.9149e8) is False
    assert is_implausible(-0.4289149e8, 428.9149e8) is True


def _code_only(src: str) -> str:
    """只留可执行代码行 —— 注释里会引用历史魔数（`1e6` / `100_000`）做说明，
    直接对整段源码做子串断言会把「解释」误判成「又写回来了」。"""
    return "\n".join(
        ln for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    )


def test_adapter_and_validator_share_one_threshold():
    """生产端与消费端必须同源 —— 历史上分别是 100_000 与裸魔数 1e6，量级都对不上。"""
    import inspect

    from src.data import adapter
    from src.validation import validator

    assert validator.IMPLAUSIBLE_RATIO is IMPLAUSIBLE_RATIO
    assert validator.is_implausible is is_implausible
    code = _code_only(inspect.getsource(adapter._apply_corrections))
    assert "IMPLAUSIBLE_DIFF_PCT" in code, "adapter 未使用共享阈值"
    assert "1e6" not in code, "adapter 又写回了裸魔数 1e6"


def test_plausibility_module_has_no_heavy_deps():
    """本模块被报告链路与 web 服务间接引用，必须零重依赖（尤其不能拖 pymupdf）。"""
    src = __import__("pathlib").Path("src/plausibility.py").read_text(encoding="utf-8")
    head = src.split('"""', 2)[-1]  # 跳过模块 docstring
    for bad in ("pymupdf", "fitz", "requests", "pandas", "numpy"):
        assert f"import {bad}" not in head, f"plausibility.py 不应 import {bad}"
