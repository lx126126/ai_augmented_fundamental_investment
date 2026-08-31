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
        f"  - {s['name']}: 收入占比 {s.get('revenue_pct', 'N/A')}%, 毛利率 {s.get('margin', 'N/A')}%"
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

=== 公司基本信息 ===
公司名：{data.get('name', '')}
代码：{data.get('code', '')}

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


def _build_hypotheses_prompt(data: dict) -> str:
    """把财务数据摘要转成「可证伪假设」生成 prompt。"""
    seg_text = "\n".join(
        f"  - {s['name']}: 收入占比 {s.get('revenue_pct', 'N/A')}%, 毛利率 {s.get('margin', 'N/A')}%"
        for s in data.get("segments", [])
    ) or "  （无分业务数据）"

    recent = ", ".join(
        f"{item.get('year', '')}年营收{item.get('revenue', 'N/A')}亿/净利{item.get('profit', 'N/A')}亿"
        for item in data.get("recent", [])
    ) or "（无历史数据）"

    val = data.get("valuation") or {}

    return f"""你是资深 A 股基本面分析师，遵循格雷厄姆（安全边际/财务稳健）+ 彼得林奇（六类公司）方法论。

以下是【真实财务数据】。请基于这些数据生成 2-3 条「可证伪的投研假设」——即复盘层要跟踪的判断。
**铁律：假设必须可证伪、带量化阈值，严禁编造数据之外的任何数字、行业排名、市场份额。**

=== 公司 ===
公司名：{data.get('name', '')}
代码：{data.get('code', '')}

=== 最新年报（{data.get('latest_year', '')} 年）关键指标 ===
营业收入：{data.get('latest', {}).get('revenue', 'N/A')} 亿元
归母净利润：{data.get('latest', {}).get('net_profit', 'N/A')} 亿元
毛利率：{data.get('latest', {}).get('gross_margin', 'N/A')}%
净利率：{data.get('latest', {}).get('net_margin', 'N/A')}%
ROE：{data.get('latest', {}).get('roe', 'N/A')}%
资产负债率：{data.get('latest', {}).get('debt_ratio', 'N/A')}%
经营现金流净额：{data.get('latest', {}).get('ocf', 'N/A')} 亿元

=== 近5年营收/净利趋势 ===
{recent}

=== 分业务收入构成 ===
{seg_text}

=== 估值 ===
PE：{val.get('pe', 'N/A')}（近10年分位 {val.get('pe_pctile', 'N/A')}%）
PB：{val.get('pb', 'N/A')}（近10年分位 {val.get('pb_pctile', 'N/A')}%）
股息率：{data.get('dividend_yield', 'N/A')}%
分红比例：{data.get('dividend_payout', 'N/A')}%

请输出以下 JSON（不要输出 JSON 之外的内容）：

{{
  "hypotheses": [
    {{
      "statement": "可证伪判断（必须带量化阈值或明确方向，如「毛利率守住30%以上」「营收增速转正」）",
      "metric": "用什么指标验证这条判断（如 销售毛利率 / 营业收入同比）",
      "confidence": "高 / 中高 / 中",
      "basis": "判断依据（严格基于上述数据，30字内）"
    }}
  ]
}}

要求：
1. 2-3 条，覆盖不同维度（盈利质量 / 成长性 / 估值 / 现金流），不要重复。
2. statement 必须可证伪：半年后能明确判定对或错，禁止「长期看好」「有护城河」「质地优良」这类永远正确的空话。
3. statement 用陈述句断言（如「毛利率守住30%以上」「营收增速转正」），严禁「能否」「是否」等疑问措辞。
4. 每条 statement 控制在 25 字内，metric 10 字内。
5. 严禁编造数据之外的数字。"""


def generate_hypotheses(data: dict) -> list[dict] | None:
    """基于财务数据生成可证伪假设草稿（供人工改）。失败返回 None。"""
    cfg = _load_config()
    if not cfg:
        print("[llm] 未配置 DEEPSEEK_API_KEY，跳过假设草稿生成")
        return None
    key, model, base = cfg
    prompt = _build_hypotheses_prompt(data)
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
                "max_tokens": 1200,
                "temperature": 0.3,
            },
            timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content).get("hypotheses", [])
    except Exception as e:
        print(f"[llm] 生成假设草稿失败: {e}")
        return None
