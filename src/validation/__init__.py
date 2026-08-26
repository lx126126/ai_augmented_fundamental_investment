"""数据验证模块：官方年报 PDF（金标准）vs 接口数据。"""

from .validator import format_report, validate

__all__ = ["validate", "format_report"]
