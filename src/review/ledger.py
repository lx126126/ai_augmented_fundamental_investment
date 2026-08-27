# -*- coding: utf-8 -*-
"""假设台账：可证伪判断的读写与生命周期管理。

文件布局：
    watchlist/reviews/{code}/{period}.json

一条假设的生命周期：
    {period} 写 hypothesis（可证伪判断）→ 下一期填 review（verdict/actual/why）→ 归因沉淀。

schema：
    {
      "code": "601088", "name": "中国神华", "period": "2026Q2", "created": "2026-08-27",
      "hypotheses": [
        {"id": "2026Q2-1", "statement": "...", "metric": "...", "confidence": "中高", "basis": "..."}
      ],
      "reviews": [
        {"id": "2026Q2-1", "verdict": "refuted", "actual": "...", "why": "..."}
      ]
    }
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_REVIEWS_DIR = Path(__file__).resolve().parent.parent.parent / "watchlist" / "reviews"

VERDICTS = ("verified", "refuted", "partial")
VERDICT_LABEL = {"verified": "验证", "refuted": "打脸", "partial": "部分验证"}


def period_path(code: str, period: str) -> Path:
    """某股票某报告期的台账文件路径。"""
    return _REVIEWS_DIR / code / f"{period}.json"


def new_ledger(code: str, name: str, period: str) -> dict:
    """新建一份空台账（冷启动用）。"""
    return {
        "code": code,
        "name": name,
        "period": period,
        "created": date.today().isoformat(),
        "hypotheses": [],
        "reviews": [],
    }


def save_period(data: dict) -> Path:
    """保存台账（data 含 code/period 字段）。"""
    code = data["code"]
    period = data["period"]
    p = period_path(code, period)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_period(code: str, period: str) -> dict | None:
    """读某期台账，不存在返回 None。"""
    p = period_path(code, period)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_periods(code: str) -> list[str]:
    """该股票已记录的全部报告期（按字典序，如 2026Q2 < 2026Q3）。"""
    d = _REVIEWS_DIR / code
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.json"))


def latest_period(code: str) -> str | None:
    """该股票最新已记录的报告期。"""
    periods = list_periods(code)
    return periods[-1] if periods else None


def load_latest(code: str) -> dict | None:
    """读最新一期台账。"""
    p = latest_period(code)
    return load_period(code, p) if p else None


def current_period() -> str:
    """当前所属季度，如 2026Q3。"""
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"{today.year}Q{q}"


def prev_period(period: str) -> str:
    """上一季度，如 2026Q3 → 2026Q2，2026Q1 → 2025Q4。"""
    year, q = int(period[:4]), int(period[5])
    if q == 1:
        return f"{year - 1}Q4"
    return f"{year}Q{q - 1}"
