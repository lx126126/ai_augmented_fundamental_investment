# -*- coding: utf-8 -*-
"""LLM 叙事层生成：基于真实财务数据，用 DeepSeek 生成商业模式/投资逻辑/风险等。

铁律：LLM 只负责「把数据讲成投研语言」，不编数字——prompt 里明确要求
所有结论必须基于提供的数据，不得虚构财务数字。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests


def _load_config() -> tuple[str, str, str] | None:
    """从 .env 或环境变量读 API 配置。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not key:
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                elif line.startswith("DEEPSEEK_MODEL="):
                    model = line.split("=", 1)[1].strip()
    return (key, model, base) if key else None


def _build_prompt(data: dict) -> str:
    """把财务数据摘要转成 prompt（数据先行，约束 LLM 不编数）。"""
    seg_text = "\n".join(
        f"  - {s['name']}: 收入占比 {s.get('revenue_pct', 'N/A')}%, 利润率 {s.get('margin', 'N/A')}%"
        for s in data.get("segments", [])
    ) or "  （无分业务数据）"

    recent = ", ".join(
        f"{item.get('year', '')}年营收{item.get('revenue', 'N/A')}亿/净利{item.get('profit', 'N/A')}亿"
        for item in data.get("recent", [])
    ) or "（无历史数据）"

    comp = data.get("competition") or {}
    comp_text = ""
    if comp:
        top5 = ", ".join(
            f"{p.get('name', '')} {p.get('revenue_yi', 'N/A')}亿" for p in comp.get("top_peers", [])
        )
        comp_text = (
            f"所属申万行业：{comp.get('industry', 'N/A')}（{comp.get('report_year', '')} 年报）\n"
            f"营收排名：行业第 {comp.get('rank', 'N/A')} / {comp.get('peers_count', 'N/A')} 家\n"
            f"营收份额：{comp.get('share_pct', 'N/A')}%（行业营收总额 {comp.get('industry_revenue', 'N/A')} 亿元）\n"
            f"行业营收前5：{top5}"
        )
    else:
        comp_text = "（无行业竞争地位数据）"

    return f"""你是资深 A 股基本面分析师，遵循格雷厄姆（安全边际/财务稳健）+ 彼得林奇（六类公司）方法论。

以下是【真实财务数据】，你的任务是基于这些数据生成投研报告的文字部分。
**铁律：所有结论必须严格基于以下数据，严禁虚构任何财务数字、行业排名、市场份额。**
**口径提示：分业务中的「利润率」是数据源口径（金融业为利差率/利润率、制造业为毛利率），请勿擅自改写成「毛利率」或臆断具体口径。**

=== 公司基本信息 ===
公司名：{data.get('name', '')}
代码：{data.get('code', '')}
主营业务：{data.get('main_business', 'N/A')}

=== 最新年报关键指标（{data.get('latest_year', '')} 年）===
营业收入：{data.get('latest', {}).get('revenue', 'N/A')} 亿元
归母净利润：{data.get('latest', {}).get('net_profit', 'N/A')} 亿元
毛利率：{data.get('latest', {}).get('gross_margin', 'N/A')}%
净利率：{data.get('latest', {}).get('net_margin', 'N/A')}%
ROE：{data.get('latest', {}).get('roe', 'N/A')}%
资产负债率：{data.get('latest', {}).get('debt_ratio', 'N/A')}%
经营现金流净额：{data.get('latest', {}).get('ocf', 'N/A')} 亿元
分红比例：{data.get('dividend_payout', 'N/A')}%
股息率：{data.get('dividend_yield', 'N/A')}%

=== 近5年营收/净利趋势 ===
{recent}

=== 分业务收入构成（最新报告期）===
{seg_text}

=== 行业竞争地位（客观数据，来自东财业绩报表）===
{comp_text}

=== 估值 ===
PE：{(data.get('valuation') or {}).get('pe', 'N/A')}
PB：{(data.get('valuation') or {}).get('pb', 'N/A')}
PE 近10年分位：{(data.get('valuation') or {}).get('pe_pctile', 'N/A')}%
PB 近10年分位：{(data.get('valuation') or {}).get('pb_pctile', 'N/A')}%

请输出以下 JSON（不要输出任何 JSON 之外的内容，所有文字用中文）：

{{
  "industry": "所属申万行业（2-6字，优先用上述客观行业名）",
  "lynch_type": "彼得林奇六类之一（如 周期型/稳健成长/缓慢增长/快速成长/困境反转/资产富余），可加简短后缀",
  "graham_badge": "格雷厄姆质量评级：高/中/低，括号注明关键依据（如 低负债·净现金）",
  "business_model": {{
    "revenue_source": "盈利来源：靠什么赚钱（一句话，基于分业务数据）",
    "profit_structure": "盈利结构：利润主要来自哪块业务、占比如何",
    "moat": "护城河：竞争壁垒（如资源禀赋/品牌/规模/牌照/一体化，可结合上述行业排名）"
  }},
  "thesis": ["投资逻辑1（基于数据）", "投资逻辑2", "投资逻辑3"],
  "risks": ["风险1", "风险2", "风险3"]
}}

要求：
1. thesis 给 3 条，risks 给 3 条，每条 20-40 字，具体、可证伪，不要空话套话。
2. business_model 三个字段各 30-60 字，紧扣分业务数据与行业排名。
3. 不要出现"根据数据""综上"等套话，直接给结论。"""


def generate_narrative(data: dict) -> dict | None:
    """调用 DeepSeek，生成叙事层 JSON。失败返回 None。"""
    cfg = _load_config()
    if not cfg:
        print("[llm] 未配置 DEEPSEEK_API_KEY，跳过叙事层生成")
        return None
    key, model, base = cfg
    prompt = _build_prompt(data)
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是资深 A 股基本面分析师，只输出 JSON，不输出任何其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 2000,
                "temperature": 0.3,
            },
            timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"[llm] 生成叙事层失败: {e}")
        return None


def _build_market_view_prompt(data: dict) -> str:
    """把财务/估值数据转成「市场在交易什么（多空）」生成 prompt。"""
    seg_text = "\n".join(
        f"  - {s['name']}: 收入占比 {s.get('revenue_pct', 'N/A')}%, 利润率 {s.get('margin', 'N/A')}%"
        for s in data.get("segments", [])
    ) or "  （无分业务数据）"

    recent = ", ".join(
        f"{item.get('year', '')}年营收{item.get('revenue', 'N/A')}亿/净利{item.get('profit', 'N/A')}亿"
        for item in data.get("recent", [])
    ) or "（无历史数据）"

    comp = data.get("competition") or {}
    comp_text = ""
    if comp:
        comp_text = (
            f"行业第 {comp.get('rank', 'N/A')}/{comp.get('peers_count', 'N/A')} 家，"
            f"营收份额 {comp.get('share_pct', 'N/A')}%"
        )
    else:
        comp_text = "（无行业竞争地位数据）"

    val = data.get("valuation") or {}

    return f"""你是资深 A 股基本面分析师，遵循格雷厄姆 + 彼得林奇方法论。

以下是【真实财务数据】。请分析「当前价格下，市场多头和空头分别在交易什么」——
即为什么现在市场给这个价，看多的人在赌什么、看空的人在担心什么。
**铁律：只能基于下方数据推演，严禁编造数据之外的任何数字、事件、传闻、目标价。**

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

=== 近5年营收/净利趋势 ===
{recent}

=== 分业务收入构成 ===
{seg_text}

=== 行业竞争地位 ===
{comp_text}

=== 估值 ===
PE：{val.get('pe', 'N/A')}（近10年分位 {val.get('pe_pctile', 'N/A')}%）
PB：{val.get('pb', 'N/A')}（近10年分位 {val.get('pb_pctile', 'N/A')}%）
股息率：{data.get('dividend_yield', 'N/A')}%
分红比例：{data.get('dividend_payout', 'N/A')}%

请输出以下 JSON（不要输出 JSON 之外的内容，所有文字用中文）：

{{
  "bull_case": "多头在交易什么：当前价格下看多方押注的核心逻辑（1-2句，50字内，紧扣估值/成长/分红数据）",
  "bear_case": "空头在交易什么：当前价格下看空方担心的核心风险（1-2句，50字内，紧扣估值分位/增速/现金流数据）",
  "watch_points": ["重点关注1", "重点关注2", "重点关注3"]
}}

要求：
1. bull_case 和 bear_case 各 1-2 句，具体、有数据支撑，不要空话。
2. watch_points 给 3 条，每条 15-25 字，是「下季度该盯哪些数据/事件」可操作清单。
3. 严禁编造数据之外的任何数字、目标价、事件。"""


def generate_market_view(data: dict) -> dict | None:
    """基于数据生成「市场多空视角 + 重点关注」草稿（第三方视角，非本人观点）。失败返回 None。"""
    cfg = _load_config()
    if not cfg:
        print("[llm] 未配置 DEEPSEEK_API_KEY，跳过多空视角生成")
        return None
    key, model, base = cfg
    prompt = _build_market_view_prompt(data)
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是资深 A 股基本面分析师，只输出 JSON，不输出任何其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 800,
                "temperature": 0.3,
            },
            timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"[llm] 生成多空视角失败: {e}")
        return None


def generate_perspective_view(data: dict, perspective_id: str) -> dict | None:
    """基于某投资人视角（perspectives/*.json）生成投研笔记。失败返回 None。

    与 generate_narrative 的区别：这里不输出「客观公司定性」，而是
    完全站在某个投资人的方法论立场上推演判断，属「第三方方法视角」。
    """
    from .perspectives import load_perspective, build_perspective_prompt

    p = load_perspective(perspective_id)
    if not p:
        print(f"[llm] 未知视角: {perspective_id}")
        return None
    cfg = _load_config()
    if not cfg:
        print(f"[llm] 未配置 DEEPSEEK_API_KEY，跳过视角[{perspective_id}]生成")
        return None
    key, model, base = cfg
    prompt = build_perspective_prompt(data, p)
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": f"你是价值投资流派中的{p.get('name')}，只输出 JSON，不输出任何其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 1000,
                "temperature": 0.3,
            },
            timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"[llm] 生成视角[{perspective_id}]失败: {e}")
        return None


def generate_action_advice(data: dict) -> dict | None:
    """基于数据生成「AI 操作建议草稿」（私有日记决策参考，非本人操作、非荐股）。失败返回 None。"""
    cfg = _load_config()
    if not cfg:
        print("[llm] 未配置 DEEPSEEK_API_KEY，跳过操作建议生成")
        return None
    key, model, base = cfg
    prompt = _build_action_prompt(data)
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是资深价值投资者，只输出 JSON，不输出任何其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 800,
                "temperature": 0.3,
            },
            timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"[llm] 生成操作建议失败: {e}")
        return None


def _build_action_prompt(data: dict) -> str:
    """把财务/估值/风险数据转成「AI 操作建议草稿」生成 prompt。

    定位：操作建议是私有投研日记的「决策参考」，由 AI 基于客观数据推演，
    只供本人（潇姐）参考拍板，绝不进公开报告。铁律：不编数、不给精确买卖点位
    （只给方向性判断 + 触发条件 + 风险提示），并明确这是 AI 生成、非本人决策。
    """
    val = data.get("valuation") or {}
    comp = data.get("competition") or {}

    comp_text = ""
    if comp:
        comp_text = (
            f"所属行业：{comp.get('industry', 'N/A')}，营收行业第 {comp.get('rank', 'N/A')}"
            f"/{comp.get('peers_count', 'N/A')} 家，营收份额 {comp.get('share_pct', 'N/A')}%"
        )
    else:
        comp_text = "（无行业竞争地位数据）"

    return f"""你是资深价值投资者，遵循格雷厄姆（安全边际）+ 彼得林奇（六类公司）方法论。

以下是【真实财务数据】。请基于这些数据，为「我本人」生成一份投资决策参考草稿——
帮我把「读了一页报告」落到「到底该怎么操作」的思考框架。

**铁律：**
1. 只能基于下方数据推演，严禁编造数据之外的任何数字、事件、目标价。
2. 不给精确买卖点位（如「XX 元买入」），只给方向性判断 + 触发条件 + 风险提示。
3. 你是 AI 辅助，你的结论是「参考」不是「指令」，措辞用「可考虑/需警惕/建议关注」而非命令式。

=== 公司 ===
公司名：{data.get('name', '')}
代码：{data.get('code', '')}
主营业务：{data.get('main_business', 'N/A')}
林奇分类：{data.get('lynch_type', 'N/A')}

=== 最新年报（{data.get('latest_year', '')} 年）关键指标 ===
营业收入：{data.get('latest', {}).get('revenue', 'N/A')} 亿元
归母净利润：{data.get('latest', {}).get('net_profit', 'N/A')} 亿元
毛利率：{data.get('latest', {}).get('gross_margin', 'N/A')}%
净利率：{data.get('latest', {}).get('net_margin', 'N/A')}%
ROE：{data.get('latest', {}).get('roe', 'N/A')}%
资产负债率：{data.get('latest', {}).get('debt_ratio', 'N/A')}%

=== 估值 ===
PE：{val.get('pe', 'N/A')}（近10年分位 {val.get('pe_pctile', 'N/A')}%）
PB：{val.get('pb', 'N/A')}（近10年分位 {val.get('pb_pctile', 'N/A')}%）
股息率：{data.get('dividend_yield', 'N/A')}%

=== 行业竞争地位 ===
{comp_text}

请输出以下 JSON（不要输出 JSON 之外的内容，所有文字用中文）：

{{
  "stance": "一句话结论（如「偏谨慎：估值处于历史高位，等待回调」或「可关注：成长消化估值，逢低分批」），20字内",
  "trigger_buy": "什么条件下可考虑买入/加仓（量化触发条件，如「PE 分位回到 50% 以下且股息率 >5%」）",
  "trigger_sell": "什么条件下可考虑卖出/减仓（量化触发条件）",
  "position_hint": "仓位建议方向（如「轻仓试错」「分批建仓」「观望不追高」，一句话）",
  "risk_reminder": "最需要警惕的风险（1-2句，紧扣数据）",
  "next_check": "下次重点复核哪个数据/信号（一句话）"
}}

要求：
1. 每条 15-40 字，具体、可执行、可证伪，不要空话套话。
2. 触发条件必须带量化阈值（基于给出的 PE/PB/股息率/分位等数据），不要「合理价位」「耐心等待」这类模糊表述。
3. 全程是「参考框架」，帮本人把数据落到决策，不是替本人下单。"""
