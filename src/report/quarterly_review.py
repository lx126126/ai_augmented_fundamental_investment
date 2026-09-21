# -*- coding: utf-8 -*-
"""季度财报解读：抓最新定期报告原文 + 单季财务事实 → DeepSeek 生成四段解读 + watch。

铁律与叙事层一致（见 llm.py）：LLM 只负责「把数据讲成投研语言」，不编数字。
本模块在此基础上多两条硬约束：

1. **管理层的观点与战略必须来自报告原文**，取不到原文就明说「本期披露未包含管理层讨论
   章节」，不允许由模型自行推测管理层想法。这条不做，LLM 会稳定地编出一段「管理层表示
   将持续优化渠道结构」的漂亮话，在任何一份财报里都读起来成立，也就没有任何信息量。
2. **异动归因只能从「主要变动指标」候选榜里挑**（榜单由 `src/report/swing.py` 客观算出）。
   原先那一段固定讲现金流，而现金流科目级明细能不能抽到完全取决于该标的 PDF 的版式 ——
   实测跟踪池 11 只里只有 2 只抽到，其余只能写「本期现金流归因数据未取到」。
   每个标的的变化点本来就不一样，让榜单决定讲什么，而不是让模板决定。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "quarterly_review"
_DISCLOSURE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "disclosure"

# 输出结构版本。**改了输出键或各段语义就必须 +1**，否则两种缓存会并存：
# `cache_only=True`（日更链路）不看 facts_hash、只看「缓存文件在不在」，于是旧结构的
# 缓存会被原样返回，而渲染端按新键取值 —— 结果是段落**静默消失**，不报错、日志也正常。
# v1：data_read / structure / cashflow / management / watch
# v2：cashflow → swing（「现金流异动归因」改成「主要变动指标归因」）
_SCHEMA = 2

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
  "swing": "主要变动指标归因（140-200字）：「主要变动指标.候选」是当期变动最大的科目榜单，不同标的的榜单完全不同。你先判断哪两三个科目最值得解释（判断标准是「解释它，读者才看得懂这家公司这个季度发生了什么」，不是谁的变动金额最大），然后逐个给出变动金额与方向并说明成因。成因必须落在证据上：优先用报告原文的经营表述，其次用同一份事实里其它指标的相互印证（例：合同负债下降 + 销售费用上升 → 渠道投入前置、回款节奏变化），两者都没有就明说「只能从数据侧观察，无法判断成因」。经营现金流若在候选里、且「现金流归因」的总额口径与剔除财务公司口径差异悬殊，必须点出财务公司的搬动因素；若「主要变动指标.候选」为空，写「本期未取到可比的主要变动指标」，不得推测",
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
   「公司未披露量化营业总收入增长目标」，只能归纳质性的重点方向，严禁给出任何百分比或金额目标；
   （口径：本报告「营收」一律指**营业总收入**，与「单季」表头一致，不得写成「营业收入」）
5. 「主要变动指标.候选」是异动归因那一段的**唯一**数字来源，且**只从里面挑**：
   候选条目已经按分组（损益 / 现金流 / 资产负债）排好，「口径」字段写明该条是
   「年初至今累计」还是「季末时点」，两者不可混说；同比为 null 表示上期基数不具可比性
   （亏损转盈利、基数过小），此时只能说变动金额与方向，不得自行推算百分比。
   ⚠️ 营业总收入与归母净利润已被「季度数据表现」那一段讲过，除非它们就是当期最大的
   异动来源，不要在这里重复；
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
        # 1900 而不是原来的 1600：swing 段现在要解释 2-3 个科目而不是 1 条现金流，
        # 段长上限也抬到 200 字。token 给不够时输出被截断 → JSON 解析失败 →
        # 整段解读返回 None，报告退回占位符（且日志只显示「跳过」）。
        max_tokens=1900,
        temperature=0.3,
    )


def get_or_generate(code: str, facts: dict | None, refresh: bool = False,
                    stale_days: float = 3.0, cache_only: bool = False) -> dict | None:
    """带缓存的入口：财务事实或报告原文一变，缓存自动失效。

    缓存键同时包含「财务事实哈希」与「原文哈希」——只哈希财务事实的话，
    中报原文更新了（如更正公告）但财务数据没动时，解读会一直沿用旧原文的结论。

    另外设了 stale_days：命中缓存时不再重抓巨潮（否则每次重建报告都要多打两三个
    网络请求），超过该天数才重新拉一次公告，避免新报告披露后长期不更新。

    `cache_only=True`：**只读缓存，绝不联网、不调模型**（日更链路用）。
    命中条件放宽成「缓存文件存在」—— 不看 facts_hash、不看年龄。理由：
    日更改的是**行情**，财务事实没变，解读文本就该留着；没有缓存才返回 None，
    由渲染层显示占位（诚实地表示「这只还没生成过解读」）。

    ⚠️ 但「放宽」必须**以 schema 版本为界**：非 cache_only 的三个分支都要求缓存里的
    `schema` 等于当前 `_SCHEMA`，否则改了输出结构后完整构建会一直命中旧结构缓存，
    渲染端按新键取值取到空串 —— 段落静默消失，不报错、日志也正常。

    🔴 而 `cache_only` 分支**刻意不校验 schema**。它的契约是「把上次完整构建的产物原样
    交出来，绝不重新生成」；若因结构版本不符就返回 None，日更会把整个板块洗成占位符 ——
    那是要 14 次 LLM 调用才补得回来的静默损坏，比显示一段旧文案严重得多。
    键的兼容交给渲染端（`build_valueline._swing_block` 会退回旧的 `cashflow`），
    并打 `_schema_stale` 标记让渲染端把这件事写在页面上。
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

    if cache_only:
        if not (cached_obj or {}).get("review"):
            return None
        review = dict(cached_obj.get("review") or {})
        review["_meta"] = cached_obj.get("meta") or {}
        review["_schema_stale"] = cached_obj.get("schema") != _SCHEMA
        review["_cached"] = True
        return review or None

    # 结构版本不符 = 这份缓存对当前渲染端不可用，一律当没有缓存 → 重新生成
    if (cached_obj or {}).get("schema") != _SCHEMA:
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
    payload = {"schema": _SCHEMA, "facts_hash": facts_hash, "mdd_hash": _hash(mdd_text)}

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
