"""复盘层：假设台账（可证伪判断）的读写与生命周期管理。

公开版展示「上季假设 → 本季实际 → 验证/打脸」闭环；
投研日记（操作层）单独存放于 journal/，绝不进公开版。
"""

from .ledger import (
    VERDICTS,
    VERDICT_LABEL,
    load_latest,
    load_period,
    latest_period,
    new_ledger,
    save_period,
)

__all__ = [
    "VERDICTS", "VERDICT_LABEL",
    "new_ledger", "save_period", "load_period", "load_latest", "latest_period",
]
