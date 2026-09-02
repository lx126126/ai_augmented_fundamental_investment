"""投研决策辅助：林奇六类公司 → 该看什么核心指标的映射。

轻量投资日记（journal/）用此模块自动推导「这类公司该盯哪些数」。
"""

from .lynch import classify, metrics_for

__all__ = ["classify", "metrics_for"]
