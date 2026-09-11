# -*- coding: utf-8 -*-
"""季度财报解读：抓最新定期报告原文 + 单季财务事实 → DeepSeek 生成三段解读。

铁律与叙事层一致（见 llm.py）：LLM 只负责「把数据讲成投研语言」，不编数字。
本模块在此基础上多一条硬约束——**管理层的观点与战略必须来自报告原文**，
取不到原文就明说「本期披露未包含管理层讨论章节」，不允许由模型自行推测管理层想法。
这条不做，LLM 会稳定地编出一段「管理层表示将持续优化渠道结构」的漂亮话，
在任何一份财报里都读起来成立，也就没有任何信息量。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "quarterly_review"
_DISCLOSURE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "disclosure"

# 管理层讨论与分析的起始 / 结束关键词。半年报与年报用「管理层讨论与分析」，
# 部分公司用「经营情况讨论与分析」；结束标志统一取「重要事项」（半年报/年报都有）。
_MDD_START_KW = ("管理层讨论与分析", "经营情况讨论与分析")
_MDD_END_KW = "重要事项"
_MDD_MAX_CHARS = 9000


def _hash(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(blob.encode("utf-8")).hexdigest()[:16]


def extract_mdd_text(pdf_path: Path, max_chars: int = _MDD_MAX_CHARS) -> str:
    """从定期报告 PDF 抽取「管理层讨论与分析」正文（取不到返回空串）。

    页序扫描而不是全书拼接：拼接会把财务报表附注一起塞进去，token 烧在附注上，
    真正要读的管理层表述反而被稀释。
    """
    try:
        import pymupdf as fitz  # 新包名（旧名 fitz 会打 deprecation warning）
    except Exception:
        try:
            import fitz
        except Exception:
            return ""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return ""
    try:
        pages = [p.get_text() for p in doc]
        if not pages:
            return ""
        start = None
        for i, t in enumerate(pages):
            # 跳过封面/目录页：目录里也会出现章节名，从那里开始会抽到一堆省略号点
            if i >= 3 and any(kw in t for kw in _MDD_START_KW):
                start = i
                break
        if start is None:
            return ""
        end = None
        for j in range(start + 1, len(pages)):
            if _MDD_END_KW in pages[j]:
                end = j
                break
        end = end if end is not None else min(start + 12, len(pages) - 1)
        text = "\n".join(pages[start:end + 1])
        text = re.sub(r"[ \t\u3000]+", " ", text)
        text = re.sub(r"\n{2,}", "\n", text)

        # 起始页通常还带着上一节（非经常性损益表）的尾巴，从章节标题处切一刀；
        # 结束页同理，切在下一节标题「…重要事项」之前，避免把承诺表整段喂给模型。
        hit = None
        for kw in _MDD_START_KW:
            i = text.find(kw)
            if i != -1 and (hit is None or i < hit):
                hit = i
        if hit:
            text = text[hit:]
        m = re.search(r"\n\s*(第[一二三四五六七八九十]+节\s*)?重要事项", text)
        if m and m.start() > 200:
            text = text[:m.start()]
        return text.strip()[:max_chars]
    finally:
        doc.close()


def fetch_latest_report(code: str) -> dict:
    """抓最新一期定期报告 PDF，返回 {meta, text}；任一步失败都优雅降级。

    返回 text 为空串表示「没拿到原文」，调用方据此让 LLM 明确写出「未获取到管理层表述」，
    而不是让模型自由发挥。
    """
    from src.validation.cninfo import download_report_pdf, query_periodic_reports

    out = {"meta": None, "text": "", "error": None}
    try:
        reports = query_periodic_reports(code)
    except Exception as e:
        out["error"] = f"查询巨潮公告失败：{e}"
        return out
    if not reports:
        out["error"] = "未查到定期报告公告"
        return out

    meta = reports[0]
    out["meta"] = {
        "title": meta["title"],
        "kind": meta.get("kind"),
        "year": meta.get("year"),
    }
    try:
        pdf = download_report_pdf(code, meta, _DISCLOSURE_DIR / code)
    except Exception as e:
        out["error"] = f"下载定期报告 PDF 失败：{e}"
        return out
    out["text"] = extract_mdd_text(pdf)
    if not out["text"]:
        out["error"] = f"{meta.get('kind', '定期报告')}未包含可抽取的「管理层讨论与分析」章节"
    return out


def _build_prompt(facts: dict, mdd_text: str, report_meta: dict | None) -> str:
    meta_txt = ""
    if report_meta:
        meta_txt = f"{report_meta.get('title') or ''}"
    body = json.dumps(facts, ensure_ascii=False, indent=1)
    src = mdd_text.strip() if mdd_text.strip() else "（未获取到报告原文）"
    return f"""下面是某 A 股上市公司的最新季度财务事实，以及其定期报告原文摘录。

=== 季度财务事实（JSON）===
{body}

=== 定期报告原文摘录：{meta_txt or "（无）"} ===
{src}

请输出以下 JSON（不要输出 JSON 之外的任何内容，全部用中文）：

{{
  "data_read": "季度数据表现（110-170字）：说清单季与年初至今累计的收入/利润/毛利率/现金流同比环比、与「单季走势序列」的关系、最关键的一个变化",
  "structure": "经营结构解读（110-170字）：用「经营结构」里的产品/渠道/地区收入占比与同比，说清增长（或下滑）由哪个切面驱动、结构怎么变了；有毛利率的切面要点出高低差对整体利润率的方向；没有就只讲结构不讲毛利率",
  "cashflow": "现金流异动归因（120-180字）：用「现金流归因」说明经营现金流同比大起大落的**具体原因**，必须落到科目上——先给总额同比，再点出「主要变动科目」里贡献最大的两三个科目及其变动金额与方向，最后给「剔除财务公司科目后」的主业口径同比。若两者方向或幅度差异悬殊，必须明确写出「总额的大幅变动主要由财务公司（吸收存款/缴存央行/同业拆放）科目造成，与白酒主业回款能力关系不大」这类判断；若「现金流归因」为 null，写「本期现金流归因数据未取到」，不得推测原因",
  "management": "管理层观点与战略（90-150字）：优先用「经营计划」的主题与要点，其次用原文中的管理层表述（对经营环境的判断、产能/渠道/价格/费用等口径）。原文与经营计划都为空时写「本期披露未包含管理层讨论章节，仅能从财务数据侧面观察」，不得推测管理层想法",
  "watch": ["投资者需要关注的点 1（25-45字，具体且可验证）", "关注点 2", "关注点 3"]
}}

要求：
1. 所有数字必须来自上面给出的「季度财务事实」，不得自行推算、不得补充未给出的数字；
2. 区分「单季」与「年初至今累计」：中报的 Q2 单季 -x% 与上半年累计 -y% 是两件事，
   不要混用；同理单季环比（Q1 对上年 Q4）含季节性，不要当趋势描述；
3. 「经营结构」里各切面的收入/占比/同比来自最新定期报告，而毛利率来自「毛利率口径」
   标明的另一份报告（通常是上一份年报）。两者报告期不同，只能说毛利率是**那个口径下**的
   水平，不得表述成当期毛利率；
4. 「年度经营计划.量化目标」为 null 表示公司未披露量化营收目标。此时必须明确写
   「公司未披露量化营收增长目标」，只能归纳质性的重点方向，严禁给出任何百分比或金额目标；
5. 现金流那一段是本段解读的重点：很多消费类公司（尤其带财务公司的，如白酒、家电、
   汽车集团的财务公司）经营现金流同比会出现几百个百分点的跳动，而真正的原因只是
   财务公司把同业存放或缴存央行的资金搬了一趟。「现金流归因」里的
   「主要变动科目」已经把每条科目的本期/上期/变动金额列出来了，
   「剔除财务公司科目后」给了还原后的主业口径。叙事必须先把这两层讲清楚，
   不能只念一遍总额同比就下结论；
6. 不要写「综上所述」「根据数据」「值得关注」这类套话，直接给结论；
7. 不给投资建议、目标价、买卖点（本报告遵循完全去操作原则）；
8. watch 给 3 条，聚焦「后续可用什么数据验证或证伪」。
"""


def generate(facts: dict, mdd_text: str, report_meta: dict | None) -> dict | None:
    """调用 DeepSeek 生成季度财报解读。失败返回 None。"""
    from src.report.llm import chat_json

    return chat_json(
        system="你是资深 A 股基本面分析师，只输出 JSON，不输出任何其他内容。",
        prompt=_build_prompt(facts, mdd_text, report_meta),
        max_tokens=1600,
        temperature=0.3,
    )


def get_or_generate(code: str, facts: dict | None, refresh: bool = False,
                    stale_days: float = 3.0) -> dict | None:
    """带缓存的入口：财务事实或报告原文一变，缓存自动失效。

    缓存键同时包含「财务事实哈希」与「原文哈希」——只哈希财务事实的话，
    中报原文更新了（如更正公告）但财务数据没动时，解读会一直沿用旧原文的结论。

    另外设了 stale_days：命中缓存时不再重抓巨潮（否则每次重建报告都要多打两三个
    网络请求），超过该天数才重新拉一次公告，避免新报告披露后长期不更新。
    """
    if not facts:
        return None

    cache_path = _CACHE_DIR / f"{code}.json"
    facts_hash = _hash(facts)

    cached_obj = None
    if cache_path.exists():
        try:
            cached_obj = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cached_obj = None
    if cached_obj and not refresh:
        import time
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400
        if cached_obj.get("facts_hash") == facts_hash and age_days <= stale_days:
            review = dict(cached_obj.get("review") or {})
            review["_meta"] = cached_obj.get("meta") or {}
            review["_cached"] = True
            return review or None

    fetched = fetch_latest_report(code)
    mdd_text = fetched.get("text") or ""
    payload = {"facts_hash": facts_hash, "mdd_hash": _hash(mdd_text)}

    if cached_obj and not refresh:
        if cached_obj.get("facts_hash") == payload["facts_hash"] and \
                cached_obj.get("mdd_hash") == payload["mdd_hash"]:
            review = dict(cached_obj.get("review") or {})
            review["_meta"] = cached_obj.get("meta") or {}
            review["_cached"] = True
            return review or None

    review = generate(facts, mdd_text, fetched.get("meta"))
    if not review:
        return None

    meta = dict(fetched.get("meta") or {})
    meta.update({
        "has_mdd": bool(mdd_text.strip()),
        "mdd_chars": len(mdd_text),
        "note": fetched.get("error"),
    })
    stored = {k: v for k, v in review.items() if not k.startswith("_")}
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({**payload, "review": stored, "meta": meta}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return {**stored, "_meta": meta, "_cached": False}
