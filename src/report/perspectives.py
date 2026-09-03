# -*- coding: utf-8 -*-
"""多投资人视角层：把「投资人风格」做成可插拔的结构化定义，驱动多版本投研日记。

设计理念（潇姐 2026-09-03 定）：
- 同一份客观数据，按不同投资人的方法论输出不同版本的投研日记。
- 每个视角 = 一个 JSON 定义（关注维度 / 核心问题 / 话术风格 / 书单溯源），
  而非散落的 prompt 字符串。
- 「读书 → 蒸馏成视角定义文件 → 生成该风格的日记」是一条可复利的流水线。

铁律（与 llm.py 一致）：LLM 只把数据翻译成投研语言，不编数字；
所有视角输出都属「第三方方法视角」，非本人观点、非荐股。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PERSPECTIVE_DIR = Path(__file__).resolve().parent / "perspectives"

# 内置视角 id → 文件名
_BUILTIN = ("graham", "lynch", "buffett", "fisher")


def load_perspective(perspective_id: str) -> dict | None:
    """按 id 加载视角定义；找不到返回 None。"""
    path = PERSPECTIVE_DIR / f"{perspective_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[perspectives] 加载视角 {perspective_id} 失败: {e}")
        return None


def list_perspectives() -> list[dict]:
    """列出所有可用视角定义（按目录扫描，含用户后续新增的蒸馏视角）。"""
    out = []
    if not PERSPECTIVE_DIR.exists():
        return out
    for p in sorted(PERSPECTIVE_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_file"] = p.stem
            out.append(d)
        except Exception:
            continue
    return out


def perspective_names() -> list[str]:
    """返回可用视角 id 列表。"""
    return [p["id"] for p in list_perspectives()]


def _fmt_list(items: list[str] | None, bullet: str = "- ") -> str:
    if not items:
        return "（无）"
    return "\n".join(f"{bullet}{x}" for x in items)


def build_perspective_prompt(data: dict, p: dict) -> str:
    """基于视角定义，把客观数据转成「该投资人视角」的投研日记生成 prompt。

    data：adapter 产出的 narrative_data 结构（含 name/code/latest/recent/segments/competition/valuation 等）。
    p：视角定义 dict（load_perspective 的返回）。
    """
    seg_text = "\n".join(
        f"  - {s['name']}: 收入占比 {s.get('revenue_pct', 'N/A')}%, 利润率 {s.get('margin', 'N/A')}%"
        for s in data.get("segments", [])
    ) or "  （无分业务数据）"

    recent = ", ".join(
        f"{item.get('year', '')}年营收{item.get('revenue', 'N/A')}亿/净利{item.get('profit', 'N/A')}亿"
        for item in data.get("recent", [])
    ) or "（无历史数据）"

    comp = data.get("competition") or {}
    if comp:
        comp_text = (
            f"所属行业：{comp.get('industry', 'N/A')}，"
            f"营收行业第 {comp.get('rank', 'N/A')}/{comp.get('peers_count', 'N/A')} 家，"
            f"营收份额 {comp.get('share_pct', 'N/A')}%"
        )
    else:
        comp_text = "（无行业竞争地位数据）"

    val = data.get("valuation") or {}

    return f"""你是价值投资流派中的【{p.get('name', '')}】，其方法论源自 {p.get('full_name', '')}。
参考书目：{', '.join(p.get('source_books', [])) or '（无）'}。
核心信条：{p.get('core_belief', '')}

请完全站在【{p.get('name', '')}】的立场与话术风格上，基于下方【真实财务数据】写一份投研笔记。
**铁律：只能基于下方数据推演，严禁编造任何数据之外的数字、事件、传闻、目标价。**

=== 公司 ===
公司名：{data.get('name', '')}
代码：{data.get('code', '')}
主营业务：{data.get('main_business', 'N/A')}

=== 最新年报（{data.get('latest_year', '')} 年）关键指标 ===
营业收入：{data.get('latest', {}).get('revenue', 'N/A')} 亿元
归母净利润：{data.get('latest', {}).get('net_profit', 'N/A')} 亿元
毛利率：{data.get('latest', {}).get('gross_margin', 'N/A')}%
净利率：{data.get('latest', {}).get('net_margin', 'N/A')}%
ROE：{data.get('latest', {}).get('roe', 'N/A')}%
资产负债率：{data.get('latest', {}).get('debt_ratio', 'N/A')}%
经营现金流净额：{data.get('latest', {}).get('ocf', 'N/A')} 亿元
股息率：{data.get('dividend_yield', 'N/A')}%
分红比例：{data.get('dividend_payout', 'N/A')}%

=== 近5年营收/净利趋势 ===
{recent}

=== 分业务收入构成（最新报告期）===
{seg_text}

=== 行业竞争地位 ===
{comp_text}

=== 估值 ===
PE：{val.get('pe', 'N/A')}（近10年分位 {val.get('pe_pctile', 'N/A')}%）
PB：{val.get('pb', 'N/A')}（近10年分位 {val.get('pb_pctile', 'N/A')}%）

=== 你要紧扣的视角 ===
关注维度：
{_fmt_list(p.get('focus_dimensions'))}

核心问题（逐条回答）：
{_fmt_list(p.get('core_questions'))}

该看的指标：
{_fmt_list(p.get('metrics_to_watch'))}

话术风格：{p.get('tone', '')}

请输出以下 JSON（不要输出 JSON 之外的内容，所有文字用中文）：

{{
  "thesis": ["该视角下的核心判断1（20-40字，具体可证伪）", "核心判断2", "核心判断3"],
  "edge": "该视角下最看重的一个信号/数据点（一句话，30字内，说明为什么这是关键）",
  "concern": "该视角下最担忧的一个风险点（一句话，30字内）",
  "verdict": "一句话总结该视角对这家公司的态度（20字内，如「安全边际充足，可关注」/「成长消化估值，逢低分批」）"
}}

要求：
1. 四个字段都要紧扣【{p.get('name', '')}】的方法论，不要用通用套话，要体现这个投资人的独特关注点。
2. 每条具体、可证伪，严禁编造数据之外的数字。
3. 这是「第三方方法视角」的推演，非本人观点、非荐股。"""
