#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投资日记生成：journal/{code}/{YYYY-MM}.md（内部私有层，gitignore）。

用法：
    python scripts/journal.py 601088            # 生成当日日记（已存在则跳过）
    python scripts/journal.py 601088 --force    # 覆盖重建
    python scripts/journal.py 09992             # 港股标的同样支持

定位：任何时间可跑的一份「轻量决策辅助」，固定栏目帮你在读一页报告后落到结论。
栏目：基本面快照 → 公司类型(林奇) → 主要看什么 → 有没有风险 → 现在贵不贵
     → 市场在交易什么(多空，第三方视角) → AI 操作建议(AI 生成，非本人操作)
     → 重点关注 → 操作/决策心理(待填)

铁律：日记是操作层，但「市场多空」「公司类型」「AI 操作建议」等由 AI 生成的内容
属「第三方视角，非本人观点/非本人操作」；操作/决策心理由本人填写。
本脚本只生成框架 + 客观数据 + AI 草稿。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.adapter import build_template_data
from src.review.lynch import classify, metrics_for
from src.report.llm import generate_market_view, generate_narrative, generate_action_advice

JOURNAL_DIR = Path(__file__).resolve().parent.parent / "journal"


def _is_hk(code: str) -> bool:
    """判断是否港股标的（带 .HK 后缀，或 0 开头 5 位码）。"""
    c = str(code).upper()
    if c.endswith(".HK"):
        return True
    bare = c.split(".")[0]
    return bare.startswith("0") and len(bare) == 5


def _norm_code(code: str) -> str:
    """代码规范化：港股剥 .HK 后缀留 5 位码，A 股 zfill 到 6 位。"""
    c = str(code).upper()
    if _is_hk(c):
        return c.split(".")[0]
    return c.zfill(6)


def _fmt(v, digits=1, suffix=""):
    if v is None:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _snapshot(real: dict) -> str:
    """基本面快照（真实数据，非操作建议）。"""
    val = real.get("valuation") or {}
    narr = real.get("narrative_data") or {}
    latest = narr.get("latest") or {}
    year = narr.get("latest_year", "—")

    lines = ["## 基本面快照（真实数据）"]
    price = val.get("price_now")
    pe = val.get("pe")
    pb = val.get("pb")
    lines.append(f"- 现价 {_fmt(price, 2)} 元 | PE(TTM) {_fmt(pe, 1)} | PB {_fmt(pb, 2)}")
    dy = val.get("dividend_yield")
    lines.append(f"- 股息率 {_fmt(dy, 1, '%')}")
    lo, hi = val.get("price_low"), val.get("price_high")
    if lo is not None and hi is not None:
        lines.append(f"- 52 周区间 {_fmt(lo, 2)} ~ {_fmt(hi, 2)} 元")

    rev = latest.get("revenue")
    np_ = latest.get("net_profit")
    roe = latest.get("roe")
    debt = latest.get("debt_ratio")
    lines.append(
        f"- 最新年报（{year}）：营收 {_fmt(rev, 1)} 亿 | 归母净利 {_fmt(np_, 1)} 亿 "
        f"| ROE {_fmt(roe, 1, '%')} | 负债率 {_fmt(debt, 1, '%')}"
    )
    pe_p = val.get("pe_pctile")
    pb_p = val.get("pb_pctile")
    lines.append(f"- 估值分位：PE 近10年 {_fmt(pe_p, 0, '%')} / PB 近10年 {_fmt(pb_p, 0, '%')}")
    return "\n".join(lines)


def _type_section(lynch_type: str) -> str:
    """公司类型 + 这类公司主要看什么。"""
    if not lynch_type:
        return "## 公司类型\n- （待分析，可运行报告生成拿林奇分类）\n"
    cat = classify(lynch_type)
    lines = [f"## 公司类型（彼得林奇六类）", f"- {lynch_type}"]
    metrics = metrics_for(lynch_type)
    if metrics:
        lines.append("")
        lines.append("这类公司主要看：")
        lines += [f"- {m}" for m in metrics]
    return "\n".join(lines)


def _risk_section(real: dict, narrative: dict) -> str:
    """风险：造假检测 + LLM 风险。"""
    lines = ["## 有没有风险"]

    fraud = real.get("fraud") or {}
    overall = fraud.get("overall_risk", "")
    audit = fraud.get("audit_opinion", "")
    audit_level = fraud.get("audit_level", "")
    flags = fraud.get("flags", []) or []
    mscore = (fraud.get("mscore") or {}).get("mscore")

    risk_label = {"low": "低", "medium": "中", "high": "高"}.get(overall, overall or "未知")
    mscore_txt = f"（Beneish M-Score {mscore:.2f}）" if mscore is not None else ""
    lines.append(f"- 财务造假检测：综合风险【{risk_label}】{mscore_txt}")
    if audit:
        lines.append(f"- 审计意见：{audit}（{'标准' if audit_level == 'clean' else audit_level}）")
    if flags:
        lines.append(f"- ⚠ 风险信号：{'、'.join(flags)}")

    risks = narrative.get("risks", []) if narrative else []
    if risks:
        lines.append("")
        lines.append("主要风险：")
        lines += [f"- {r}" for r in risks]
    elif not (fraud or flags):
        lines.append("- （暂无可识别风险）")
    return "\n".join(lines)


def _valuation_section(real: dict) -> str:
    """现在贵不贵：估值 + 分位 + 格雷厄姆体检 + 52周位置。"""
    val = real.get("valuation") or {}
    graham = real.get("graham") or {}
    lines = ["## 现在贵不贵"]

    pe, pb = val.get("pe"), val.get("pb")
    pe_p, pb_p = val.get("pe_pctile"), val.get("pb_pctile")
    dy = val.get("dividend_yield")

    lines.append(f"- PE {_fmt(pe, 1)}（近10年分位 {_fmt(pe_p, 0, '%')}）| PB {_fmt(pb, 2)}（近10年分位 {_fmt(pb_p, 0, '%')}）")
    lines.append(f"- 股息率 {_fmt(dy, 1, '%')}")

    # 52 周位置
    price = val.get("price_now")
    lo, hi = val.get("price_low"), val.get("price_high")
    if price is not None and lo is not None and hi is not None and hi > lo:
        pos = (price - lo) / (hi - lo) * 100
        lines.append(f"- 现价处于 52 周区间 {_fmt(pos, 0, '%')} 分位（{_fmt(lo, 2)}~{_fmt(hi, 2)} 元）")

    # 格雷厄姆体检
    debt = graham.get("debt_ratio")
    cur = graham.get("current_ratio")
    net_cash = graham.get("net_cash")
    stable = graham.get("profit_stable")
    g_lines = []
    if debt is not None:
        g_lines.append(f"负债率 {_fmt(debt, 1, '%')}")
    if cur is not None:
        g_lines.append(f"流动比率 {_fmt(cur, 2)}")
    if net_cash is not None:
        g_lines.append(f"净现金 {_fmt(net_cash, 1)} 亿")
    if stable is not None:
        g_lines.append("盈利稳定" if stable else "盈利波动")
    if g_lines:
        lines.append(f"- 格雷厄姆体检：{' / '.join(g_lines)}")
    return "\n".join(lines)


def _market_section(real: dict) -> str:
    """市场在交易什么（多空，第三方视角，非本人观点）。"""
    narrative = real.get("narrative_data") or {}
    lines = ["## 市场在交易什么（第三方视角，非本人观点）"]
    mv = generate_market_view(narrative) if narrative else None
    if mv:
        if mv.get("bull_case"):
            lines.append(f"- 多头在交易：{mv['bull_case']}")
        if mv.get("bear_case"):
            lines.append(f"- 空头在交易：{mv['bear_case']}")
        if mv.get("watch_points"):
            lines.append("")
            lines.append("重点关注：")
            lines += [f"- {w}" for w in mv["watch_points"]]
    else:
        lines.append("- （多空视角待 AI 生成，需配置 DEEPSEEK_API_KEY）")
    return "\n".join(lines)


def _action_section(real: dict, lynch_type: str) -> str:
    """AI 操作建议（AI 生成，非本人操作，非荐股，仅作决策参考）。"""
    narrative = real.get("narrative_data") or {}
    lines = ["## AI 操作建议（AI 生成，非本人操作 · 非荐股，仅供决策参考）"]
    if not narrative:
        lines.append("- （无数据，无法生成）")
        return "\n".join(lines)
    # 把林奇分类补进 data，供 prompt 使用
    data = dict(narrative)
    if lynch_type:
        data["lynch_type"] = lynch_type
    adv = generate_action_advice(data)
    if adv:
        if adv.get("stance"):
            lines.append(f"- 一句话结论：{adv['stance']}")
        if adv.get("trigger_buy"):
            lines.append(f"- 买入/加仓触发：{adv['trigger_buy']}")
        if adv.get("trigger_sell"):
            lines.append(f"- 卖出/减仓触发：{adv['trigger_sell']}")
        if adv.get("position_hint"):
            lines.append(f"- 仓位方向：{adv['position_hint']}")
        if adv.get("risk_reminder"):
            lines.append(f"- 风险警示：{adv['risk_reminder']}")
        if adv.get("next_check"):
            lines.append(f"- 下次复核：{adv['next_check']}")
    else:
        lines.append("- （待 AI 生成，需配置 DEEPSEEK_API_KEY）")
    return "\n".join(lines)


_TEMPLATE_TAIL = """
## 操作
- （待填：日期 + 方向 + 价格 + 仓位，未成交也记）

## 决策心理
- （待填：为什么这个价/这个时点，在犹豫什么）

## 事后
- （复盘时补：这个决定对不对，为什么）
"""


def generate(code: str, force: bool = False) -> Path:
    """生成当日投资日记，已存在则跳过（除非 force）。返回文件路径。"""
    code = _norm_code(code)
    real = build_template_data(code)
    name = real.get("company_name") or code
    narrative = real.get("narrative_data") or {}
    ym = date.today().strftime("%Y-%m")

    path = JOURNAL_DIR / code / f"{ym}.md"
    if path.exists() and not force:
        print(f"跳过（已存在）: {path}")
        return path

    # 拿林奇分类（复用 generate_narrative 的 lynch_type；失败则空）
    lynch_type = ""
    if narrative:
        narr = generate_narrative(narrative)
        if narr:
            lynch_type = narr.get("lynch_type", "")

    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        _snapshot(real),
        _type_section(lynch_type),
        _risk_section(real, narrative),
        _valuation_section(real),
        _market_section(real),
        _action_section(real, lynch_type),
    ]
    content = f"# {ym} {name}\n\n" + "\n\n".join(b.rstrip("\n") for b in blocks) + "\n" + _TEMPLATE_TAIL
    path.write_text(content, encoding="utf-8")
    print(f"已生成: {path}")
    return path


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    code = args[0] if args else "601088"
    generate(code, force)


if __name__ == "__main__":
    main()
