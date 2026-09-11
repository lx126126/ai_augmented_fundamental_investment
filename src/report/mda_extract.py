"""从定期报告原文抽取「经营结构」（分产品/渠道/地区 收入·占比·毛利率·同比）与「年度经营计划」。

为什么必须回原文，而不是继续用现成接口：

1. 东财 `stock_zygc_em` 只提供「产品 / 地区」两个切面，**没有销售渠道**。而渠道结构对白酒
   公司是最关键的一刀——茅台 2026H1 直销收入占比 57.3%（上年同期 44.8%），直销毛利率比
   批发代理高约 6.7 个百分点，渠道结构一移动，整体利润率就被它决定。缺了这一刀等于没看。
2. 接口不给「分切面的收入同比」和「毛利率同比」，只能自己拿两期原文算。
3. 半年报的「销售情况」表以「万元」披露且不含成本（拿不到分切面毛利率），要回上一份年报取
   毛利率——两个口径必须分别标注，否则读者会把年报毛利率当成年报以外的口径。

所以本模块做三件事：解析最新定期报告（收入/占比/同比）、解析上一份年报（毛利率）、
解析年报的经营计划（年度目标）。原文抽不出来时一律返回 None 优雅降级，绝不猜。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_DISCLOSURE_DIR = Path("data/cache/disclosure")
_CACHE_DIR = Path("data/cache/operating_structure")

# 解析口径版本：改动本模块的抽取逻辑时必须 +1，否则历史缓存会继续命中旧结果。
# v2：新增「现金流归因」（经营活动现金流同比科目级归因 + 剔除财务公司科目口径）。
# v3：产销量补齐「茅台酒 / 系列酒」拆分（来源年报「产品情况」表，与合计行互校）
#     与库存拆分（成品酒 / 半成品基酒）。
# v4：产销量「口径」文案改为独立的基酒口径说明（缓存键只含报告标题，改文案
#     同样会让缓存与代码不一致，因此也必须 +1）。
_EXTRACT_VERSION = 4

# 必须允许负号：年报「收入比上年增减（%）」列大量为负数（如其他系列酒 -9.76、批发代理
# -12.05），不含负号会让这两行只凑到 3 个数值而被当成「解析不完整」整行丢弃——
# 表现为分产品只剩茅台酒、分渠道只剩直销，且占比栏静默变成 100%。
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _read_text(pdf_path: Path) -> str:
    """PDF → 全文（保留换行，行内多余空白压掉）。"""
    try:
        import pymupdf as fitz
    except Exception:  # pragma: no cover
        try:
            import fitz  # type: ignore
        except Exception:
            return ""
    try:
        with fitz.open(str(pdf_path)) as doc:
            return "\n".join(pg.get_text() for pg in doc)
    except Exception:
        return ""


def _lines(text: str) -> list[str]:
    out = []
    for ln in text.replace("\u3000", " ").split("\n"):
        ln = " ".join(ln.split())
        if ln:
            out.append(ln)
    return out


def _n(s: str) -> float:
    return float(str(s).replace(",", ""))


def _c(v, nd=2):
    """四舍五入并清掉 -0.0。"""
    if v is None:
        return None
    r = round(float(v), nd)
    return 0.0 if r == 0 else r


def _norm_name(name: str) -> str:
    """行名归一化：年报写「其他系列酒」、半年报写「系列酒」，同比要能对上。"""
    s = re.sub(r"\s+", "", str(name))
    for pre in ("其他", "其中：", "其中:"):
        if s.startswith(pre) and len(s) > len(pre) + 1:
            s = s[len(pre):]
    return s


def _unit_divisor(line: str) -> float:
    """「单位：万元」→ 换算成「亿元」的除数。"""
    if "万元" in line:
        return 1e4
    if "亿元" in line:
        return 1.0
    if "元" in line:
        return 1e8
    return 1e4


def _period_label(kind: str | None, year: int | None) -> str:
    if not year:
        return str(kind or "")
    return {
        "年报": f"{year}A",
        "半年报": f"{year}H1",
        "一季报": f"{year}Q1",
        "三季报": f"{year}Q3",
    }.get(kind or "", f"{year} {kind or ''}".strip())


def _reconcile(groups: list[list[float]], tol: float = 0.01) -> bool:
    """三切面必须勾稽到同一个「酒类收入」合计，否则说明解析错位，宁可不要数据。

    茅台报表里「按产品档次」「按销售渠道」「按地区」三行合计相等，这是免费的自校验。
    """
    sums = [sum(g) for g in groups if g]
    if len(sums) < 2:
        return True
    base = sums[0]
    if base <= 0:
        return False
    return all(abs(s - base) / base <= tol for s in sums)


# --------------------------------------------------------------------------- #
# 半年报 / 季报：3、销售情况（万元，无成本）
# --------------------------------------------------------------------------- #
# 该表列头固定为「按产品档次 / 按销售渠道 / 按地区」，行序也固定，按序对齐即可。
_SALES_LAYOUT = [
    ("产品", "茅台酒"),
    ("产品", "系列酒"),
    ("渠道", "直销"),
    ("渠道", "批发代理"),
    ("地区", "国内"),
    ("地区", "国外"),
]
_SALES_NOISE = {
    "项目", "单位：万元", "币种：人民币", "按产品档次", "按销售渠道", "按地区",
    "报告期主要", "业务收入", "报告期主要业务收入",
}


def parse_sales_situation(text: str) -> dict | None:
    """解析半年报/季报「销售情况」表：产品档次 × 销售渠道 × 地区 三切面收入（不含成本）。"""
    m = re.search(r"销售情况", text)
    if not m:
        return None
    seg = text[m.end():]
    note_txt = ""
    note = re.search(r"注[：:]", seg)
    if note:
        note_txt = seg[note.start(): note.start() + 260]
        seg = seg[: note.start()]

    tokens = _lines(seg)
    labels = [t for t in tokens if not _NUM_RE.match(t) and t not in _SALES_NOISE]
    nums = [_n(t) for t in tokens if _NUM_RE.match(t)]

    # 标签顺序必须与固定列头一致；不一致说明版式变了，直接放弃（另加勾稽校验兜底）
    expected = [name for _, name in _SALES_LAYOUT]
    if labels[: len(expected)] != expected or len(nums) < len(expected):
        return None
    vals = nums[: len(expected)]

    groups: dict[str, list[float]] = {}
    idx = 0
    for cut, name in _SALES_LAYOUT:
        groups.setdefault(cut, []).append(vals[idx])
        idx += 1

    if not _reconcile([groups.get("产品", []), groups.get("渠道", []), groups.get("地区", [])]):
        return None

    div = _unit_divisor(next((t for t in tokens if "单位" in t), "单位：万元"))

    def _rows(cut: str) -> list[dict]:
        return [
            {"名称": name, "收入_亿元": _c(raw / div)}
            for (c, name), raw in zip(_SALES_LAYOUT, vals) if c == cut
        ]

    imoutai = None
    mm = re.search(r"i\s*茅台[^0-9]{0,60}?([\d,]+\.\d+)", note_txt or "")
    if mm:
        imoutai = _c(_n(mm.group(1)) / div)

    return {
        "口径": "半年报「销售情况」表（含渠道切面；该表不含成本，故无分切面毛利率）",
        "单位": "万元",
        "切片": {cut: _rows(cut) for cut in ("产品", "渠道", "地区")},
        "i茅台_亿元": imoutai,
    }


# --------------------------------------------------------------------------- #
# 年报：(1) 主营业务分行业、分产品、分地区、分销售模式情况（元，含成本与毛利率）
# --------------------------------------------------------------------------- #
_MB_SECTIONS = {"行业": "分行业", "产品": "分产品", "地区": "分地区", "销售模式": "分销售模式"}
_MB_NOISE = {
    "分行业", "分产品", "分地区", "分销售模式",
    "营业收入", "营业成本", "毛利率（%）", "毛利率(%)",
    "营业收入比上", "年增减（%）", "营业成本比上", "毛利率比上年增", "减（%）",
    "单位：元", "币种：人民币", "单位：元币种：人民币", "项目",
    "主营业务分行业情况", "主营业务分产品情况",
    "主营业务分地区情况", "主营业务分销售模式情况",
}
_MB_STOP = re.compile(r"产销量情况分析表|成本分析表|主要销售客户|重大采购合同|子公司股权变动")


def parse_main_business(text: str) -> dict | None:
    """解析年报「主营业务分行业/产品/地区/销售模式情况」：收入、成本、毛利率、收入·成本同比。

    行首判定用「该行的下一行是数字」——列头（营业收入/营业成本/毛利率（%）/…）后面跟的
    都是文字，天然被排除，不需要维护一份易腐的列头白名单。
    """
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"主营业务分(行业|产品|地区|销售模式)情况", text)]
    if not marks:
        return None

    cuts: dict[str, list[dict]] = {}
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(text), pos + 6000)
        seg = text[pos:end]
        stop = _MB_STOP.search(seg)
        if stop:
            seg = seg[: stop.start()]

        toks = _lines(seg)
        rows: list[dict] = []
        seen: set[str] = set()
        for j in range(len(toks) - 1):
            head = toks[j]
            if head in _MB_NOISE or re.search(r"\d", head) or len(head) > 18:
                continue
            if not _NUM_RE.match(toks[j + 1]):
                continue
            if _norm_name(head) in seen:
                continue
            vals: list[float] = []
            k = j + 1
            while k < len(toks) and len(vals) < 6 and _NUM_RE.match(toks[k]):
                vals.append(_n(toks[k]))
                k += 1
            if len(vals) < 5:
                continue
            seen.add(_norm_name(head))
            rows.append({
                "名称": head,
                "收入_亿元": _c(vals[0] / 1e8),
                "成本_亿元": _c(vals[1] / 1e8),
                "毛利率_pct": _c(vals[2]),
                "收入同比_pct": _c(vals[3]),
                "成本同比_pct": _c(vals[4]),
            })
        if not rows:
            continue
        total = sum(r["收入_亿元"] for r in rows if r["收入_亿元"])
        for r in rows:
            r["占比_pct"] = _c(r["收入_亿元"] / total * 100, 1) if total else None
        cuts[_MB_SECTIONS.get(name, name)] = rows

    if not cuts:
        return None
    return {
        "口径": "年报「主营业务分行业/分产品/分地区/分销售模式情况」（含成本与毛利率，单位：元）",
        "单位": "元",
        "切片": cuts,
    }


# --------------------------------------------------------------------------- #
# 经销商情况 / 产销量 / 经营计划
# --------------------------------------------------------------------------- #
def parse_dealers(text: str) -> dict | None:
    """经销商情况：报告期末数量 + 增减。渠道改革的直接观察指标。

    两道防线，因为「国内 / 国外」这两个词在年报里到处都是（分地区收入表、分地区销量表
    都拿它当行名）：
    1. 只认**第一次**出现，后续同名行不再覆盖；
    2. 数量级校验（0 < 期末 ≤ 20000 家）。不校验时茅台年报会解析出「国内经销商 1639 亿家」
       ——那是分地区表的收入被当成家数，错得离谱却依然能渲染出来。
    """
    m = re.search(r"经销商情况", text)
    if not m:
        return None
    seg = text[m.end():]
    stop = re.search(r"注[：:]|非主营业务|资产、负债情况|情况说明", seg)
    if stop:
        seg = seg[: stop.start()]

    toks = _lines(seg)
    out: dict[str, dict] = {}
    for j, ln in enumerate(toks):
        if ln not in ("国内", "国外") or ln in out:
            continue
        vals: list[float] = []
        k = j + 1
        while k < len(toks) and len(vals) < 3 and _NUM_RE.match(toks[k]):
            vals.append(_n(toks[k]))
            k += 1
        if not vals or not 0 < vals[0] <= 20000:
            continue
        d = {"期末_家": int(vals[0])}
        if len(vals) > 1:
            d["增加_家"] = int(vals[1])
        if len(vals) > 2:
            d["减少_家"] = int(vals[2])
        out[ln] = d
    return out or None


def parse_output_sales(text: str) -> list[dict] | None:
    """产销量情况分析表：生产量/销售量/库存量（吨）及同比。"""
    m = re.search(r"产销量情况分析表", text)
    if not m:
        return None
    seg = text[m.end(): m.end() + 1800]
    stop = _MB_STOP.search(seg)
    if stop and stop.start() > 20:
        seg = seg[: stop.start()]
    toks = _lines(seg)
    rows: list[dict] = []
    for j in range(len(toks) - 2):
        head = toks[j]
        if head in _MB_NOISE or re.search(r"\d", head) or len(head) > 18:
            continue
        k = j + 1
        unit = None
        if not _NUM_RE.match(toks[k]):
            # 单位列（吨/件/瓶）夹在名称与数字之间
            unit = toks[k]
            k += 1
        vals: list[float] = []
        while k < len(toks) and len(vals) < 6 and _NUM_RE.match(toks[k]):
            vals.append(_n(toks[k]))
            k += 1
        if len(vals) < 5:
            continue
        rows.append({
            "产品": head, "单位": unit,
            "生产量": _c(vals[0]), "销售量": _c(vals[1]), "库存量": _c(vals[2]),
            "生产量同比_pct": _c(vals[3]), "销售量同比_pct": _c(vals[4]),
        })
        break  # 该表通常只有「酒类」一行汇总
    return rows or None


def parse_product_lines(text: str) -> list[dict] | None:
    """年报「4、产品情况」表：分产品档次的产量·销量·收入（茅台酒 / 系列酒）。

    为什么必须解析这张表：上面那张「产销量情况分析表」只有一行「酒类」合计
    （2025 年报：生产量 116,123.73 吨），直接拿合计数当产量展示，读者会读成
    「茅台酒一年产 11.6 万吨」——而实际是茅台酒 58,473.16 吨 + 系列酒 57,650.57 吨，
    两者几乎各占一半。合计数把最有信息量的那一刀砍掉了，且错得毫无征兆。

    pymupdf 的行序里，产量·销量·收入三组数字紧跟行名，之后才是下一档次的
    代表品牌名（如「贵州茅台酒」「茅台王子酒、茅」），所以只吃紧邻的连续数字，
    不用维护列头白名单。数字列只有 6 个而非 7 个：年报「产销率」列为空，
    文本中根本不出现（年报注释亦说明「茅台酒基酒产销率不能精准计算」）。
    遍历所有「4、产品情况」出现位置（目录页也可能命中），取第一个能解析出
    完整两档的窗口——命中目录时窗口里没有表格，自然落空并继续往后找。
    """
    for m in re.finditer(r"4\s*、\s*产品情况", text):
        rows = _product_rows(text[m.end(): m.end() + 1500])
        if len(rows) >= 2:
            return rows
    return None


def _product_rows(seg: str) -> list[dict]:
    stop = re.search(r"原料采购|产品档次划分标准", seg)
    if stop and stop.start() > 20:
        seg = seg[: stop.start()]
    toks = _lines(seg)
    rows: list[dict] = []
    for j, head in enumerate(toks):
        # 精确相等而非包含：年报里「贵州茅台酒」「茅台酒制酒车间」到处都是，
        # 用 in 判定会把产能表、公司全称都吃进来。
        name = head.strip()
        if name not in ("茅台酒", "其他系列酒", "系列酒"):
            continue
        k = j + 1
        vals: list[float] = []
        while k < len(toks) and len(vals) < 6 and _NUM_RE.match(toks[k]):
            vals.append(_n(toks[k]))
            k += 1
        if len(vals) != 6:
            continue
        prod, prod_yoy, sale, sale_yoy, rev_wan, rev_yoy = vals
        # 量级防线：产量/销量（吨）与收入（万元）都远大于这些下界，
        # 越界基本说明数字列被错位读取（如把「万元」当「元」、或吃到相邻表）。
        if not (1000 < prod < 500000 and 1000 < sale < 500000 and rev_wan > 10000):
            continue
        rows.append({
            "档次": name,
            "产量": _c(prod), "产量同比_pct": _c(prod_yoy),
            "销量": _c(sale), "销量同比_pct": _c(sale_yoy),
            "收入_亿元": _c(rev_wan / 10000),
            "收入同比_pct": _c(rev_yoy),
        })
    # 必须两档齐全才采用（由 parse_product_lines 判定）：只解析出一档时无法区分
    # 「表格改版」与「漏读一行」，而单档展示会把「茅台酒 + 系列酒」静默读成「茅台酒」。
    return rows


def parse_inventory_split(text: str) -> dict | None:
    """年报「产品期末库存量」：成品酒 / 半成品酒（含基础酒），单位吨。

    合计数（2025 年报 339,977.86 吨）单独看像「囤了两年卖不动的货」，拆开才知道
    92.7% 是按规定必须存够年份的半成品基酒，已包装的成品酒只有 2.5 万吨。
    """
    for m in re.finditer(r"产品期末库存量", text):
        run: list[float] = []
        for t in _lines(text[m.end(): m.end() + 300]):
            if _NUM_RE.match(t):
                run.append(_n(t))
            elif run:
                break      # 数字 run 结束（行名与「单位：吨」都不含纯数字行）
        if len(run) < 2:
            continue
        fin, semi = run[0], run[1]
        if not (0 < fin < semi < 1_000_000):   # 成品酒必然小于半成品酒
            continue
        return {"成品酒": _c(fin), "半成品酒": _c(semi), "单位": "吨"}
    return None


def _sum_matches(parts: list[float], total, tol: float = 0.01) -> bool:
    """分项之和是否等于合计行（相对容差）。

    同一组数在年报里出现两次——「产销量情况分析表」（合计）与「产品情况」（分项），
    正好互为校验：对不上说明两处至少有一处读错了，此时宁可不出拆分。
    """
    if not total or not parts or len(parts) < 2:
        return False
    try:
        return abs(sum(float(p) for p in parts) / float(total) - 1) <= tol
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def parse_business_plan(text: str, year: int | None = None) -> dict | None:
    """年报「经营计划」：主题 + 重点工作条目 + 量化目标（有则取，无则明确写「未披露」）。

    茅台这类公司常常只给方向、不给量化营收指引。此时必须**明确标注未披露**，
    而不是让 LLM 顺手编一个「公司预计营收增长 10%」——那是零成本的事实性错误。
    """
    m = re.search(r"经营计划", text)
    if not m:
        return None
    seg = text[m.end(): m.end() + 12000]

    theme = None
    tm = re.search(r"紧紧围绕[“\"]([^”\"]{4,60})[”\"]主题", seg)
    if tm:
        # 原文因排版会把「消费者 / 为中心」拆行，去掉空白而不是留一个假空格
        theme = re.sub(r"\s+", "", tm.group(1))

    # 只取「重点抓好以下…」到下一章节标题之间的正文。不设这个边界，会把「可能面对的风险」
    # 里的「一是宏观经济风险；二是安全风险」当成年度重点——实测真的混进来过，
    # 而且是那种读起来完全通顺、只有回原文才发现张冠李戴的错。
    block = None
    mb = re.search(r"重点抓好以下", seg)
    if mb:
        tail = seg[mb.end():]
        stop = re.search(r"[（(][四五六七八九][）)]|可能面对的风险|未来发展的展望", tail)
        block = tail[: stop.start()] if stop else tail[:4000]

    items: list[str] = []
    for raw in re.findall(r"[一二三四五六七八九十]是([^。]{4,40})。", block or ""):
        s = " ".join(raw.split())[:24]
        if "《" in s or "；" in s:
            continue
        items.append(s)
    items = items[:8]  # 年报惯例是「八方面工作」

    quant = None
    for mm in re.finditer(r"[^。\n]{0,80}(?:营业总收入|营业收入|净利润)[^。\n]{0,40}(?:增长|目标|力争)[^。\n]{0,40}", seg):
        s = " ".join(mm.group(0).split())
        if re.search(r"\d+\s*[%％]|\d+\s*亿元|左右", s):
            quant = s
            break

    if not items and not theme:
        return None
    return {"年度": year, "主题": theme, "要点": items, "量化目标": quant}


# --------------------------------------------------------------------------- #
# 合并现金流量表（经营活动部分）——用于「现金流同比为什么变了」的归因
# --------------------------------------------------------------------------- #
# 财政部《一般企业财务报表格式》的经营活动行项目，顺序即报表顺序。
#
# 为什么按顺序扫描而不是「逐行正则匹配行名」：
# 报表里大量行本期/上期都是空白（保险公司、证券公司专用行），空白行不产生任何数字。
# 逐行独立匹配时，空行的行名会被当成有数字的那一行，把**下一行**的数字错配到自己名下——
# 表现为「向中央银行借款净增加额 = 7383004563.78」这种数字对但名字错的结果，
# 比解析失败更危险，因为它看起来是合理的。顺序扫描天然免疫：前一行匹配到的行名后面
# 紧跟着的不是数字，就说明这一行是空的。
_CF_OPERATING_ITEMS = [
    ("流入", "销售商品、提供劳务收到的现金"),
    ("流入", "客户存款和同业存放款项净增加额"),
    ("流入", "向中央银行借款净增加额"),
    ("流入", "向其他金融机构拆入资金净增加额"),
    ("流入", "收到原保险合同保费取得的现金"),
    ("流入", "收到再保业务现金净额"),
    ("流入", "保户储金及投资款净增加额"),
    ("流入", "收取利息、手续费及佣金的现金"),
    ("流入", "拆入资金净增加额"),
    ("流入", "回购业务资金净增加额"),
    ("流入", "代理买卖证券收到的现金净额"),
    ("流入", "收到的税费返还"),
    ("流入", "收到其他与经营活动有关的现金"),
    ("流入", "经营活动现金流入小计"),
    ("流出", "购买商品、接受劳务支付的现金"),
    ("流出", "客户贷款及垫款净增加额"),
    ("流出", "存放中央银行和同业款项净增加额"),
    ("流出", "支付原保险合同赔付款项的现金"),
    ("流出", "拆出资金净增加额"),
    ("流出", "支付利息、手续费及佣金的现金"),
    ("流出", "支付保单红利的现金"),
    ("流出", "支付给职工以及为职工支付的现金"),
    ("流出", "支付的各项税费"),
    ("流出", "支付其他与经营活动有关的现金"),
    ("流出", "经营活动现金流出小计"),
    ("净额", "经营活动产生的现金流量净额"),
]

# 同一行项目在实务中的不同写法。茅台半年报把标准行名「支付给职工**以及**为职工支付的现金」
# 印成「支付给职工及为职工支付的现金」（少一个「以」），按标准名匹配会整行漏掉——
# 而这一行是 100 亿量级的科目，漏掉会让「经营现金流出小计」的自校验失败、整段作废。
_CF_ALIASES = {
    "支付给职工以及为职工支付的现金": ("支付给职工及为职工支付的现金",),
    "购买商品、接受劳务支付的现金": ("购买商品、接收劳务支付的现金",),
    "经营活动产生的现金流量净额": ("经营活动产生的现金流量净额",),
}

# 财务公司（茅台集团财务有限公司并表）带来的经营性收支科目。
# 这些科目不是「卖酒收钱」，而是吸收成员单位存款、向央行/同业缴存与拆放资金的来回搬动，
# 方向一变就能把经营现金流净额做出几十亿甚至几百亿的波动，且与主业景气度无关。
# 归因时必须单独拎出来算「剔除后」的口径，否则会把资金摆布说成主业改善。
_CF_FINANCE_ITEMS = {
    "客户存款和同业存放款项净增加额",
    "客户贷款及垫款净增加额",
    "存放中央银行和同业款项净增加额",
    "拆出资金净增加额",
    "收取利息、手续费及佣金的现金",
    "支付利息、手续费及佣金的现金",
}

_CF_SEC_START = re.compile(r"一、\s*经营活动产生的现金流量")
_CF_SEC_END = re.compile(r"二、\s*投资活动产生的现金流量")
# 页眉页脚（PDF 分页插进表格中间）：公司名+报告名、以及「34 / 110」式页码
_CF_ARTIFACT = re.compile(r"(有限公司|股份公司|股份有限公司)\s*\d{0,4}\s*年?\s*(半年度|年度|第?[一二三四]季度)报告$")
_CF_PAGENO = re.compile(r"^\d+\s*/\s*\d+$")
# 附注列（如「59（1）」「57(1)」）：夹在行名与数字之间，必须先剔除才能判断「行名后面紧邻数字」
_CF_NOTEONLY = re.compile(r"^\d+\s*[（(]\s*\d*\s*[）)]$")


def _cf_section(text: str) -> str:
    """切出「一、经营活动产生的现金流量」到「二、投资活动」之间的合并报表片段。"""
    m = _CF_SEC_START.search(text)
    if not m:
        return ""
    seg = text[m.end():]
    e = _CF_SEC_END.search(seg)
    return seg[: e.start()] if e else seg[:6000]


def parse_cash_flow_operating(text: str) -> dict | None:
    """解析合并现金流量表「经营活动」段：{行名: (本期, 上期)}，单位统一为亿元。

    返回 None 表示版式不认识（宁可没有数据，也不给错位的行名-数字配对）。
    """
    seg = _cf_section(text)
    if not seg:
        return None

    # 打平成 token 流：正常行名合并成一串文本 token，数字单独成 token。
    toks: list[tuple[str, str, float | None]] = []
    for ln in _lines(seg):
        if _CF_PAGENO.match(ln) or _CF_ARTIFACT.search(ln) or _CF_NOTEONLY.match(ln):
            continue
        if _NUM_RE.match(ln):
            toks.append(("num", ln, _n(ln)))
        else:
            toks.append(("txt", re.sub(r"\s+", "", ln), None))
    if not toks:
        return None

    rows: dict[str, tuple[float, float | None]] = {}
    i = 0
    for _kind, name in _CF_OPERATING_ITEMS:
        spellings = (name,) + _CF_ALIASES.get(name, ())
        # 从当前位置向后找第一个含该行名的连续文本块
        found_tok = None
        j = i
        while j < len(toks):
            if toks[j][0] == "num":
                j += 1
                continue
            blob = ""
            k = j
            while k < len(toks) and toks[k][0] == "txt":
                blob += toks[k][1]
                k += 1
            for sp in spellings:
                idx = blob.find(sp)
                if idx == -1:
                    continue
                # 行名必须**结束在**本块末端，即它后面不能再跟别的行名——
                # 否则匹配到的是被拼进同一块的前一个行名的一部分
                # （「拆入资金净增加额」是「向其他金融机构拆入资金净增加额」的后缀）。
                if idx + len(sp) == len(blob):
                    found_tok = k - 1
                break
            if found_tok is not None:
                break
            j = k
        if found_tok is None:
            continue

        vals: list[float] = []
        k = found_tok + 1
        while k < len(toks) and toks[k][0] == "num" and len(vals) < 2:
            vals.append(toks[k][2])  # type: ignore[arg-type]
            k += 1
        if not vals:
            i = found_tok + 1
            continue
        rows[name] = (vals[0] / 1e8, (vals[1] / 1e8) if len(vals) > 1 else None)
        i = k

    if not rows:
        return None

    # 自校验：流入/流出小计必须等于各自明细加总（差额容忍 0.5%，PDF 抽数字偶有舍入差异）
    for kind, subtotal in (("流入", "经营活动现金流入小计"), ("流出", "经营活动现金流出小计")):
        if subtotal not in rows:
            continue
        detail = [
            v[0] for nm, v in rows.items()
            if nm != subtotal and any(kd == kind and n == nm for kd, n in _CF_OPERATING_ITEMS)
        ]
        if not detail:
            continue
        calc, shown = sum(detail), rows[subtotal][0]
        if shown and abs(calc - shown) / max(abs(shown), 1e-9) > 0.005:
            return None  # 明细与小计对不上 → 抽错了行，整段作废

    return rows


def build_ocf_attribution(text: str, period: str | None = None,
                          prior_period: str | None = None) -> dict | None:
    """经营现金流同比归因：找出主要变动科目，并给出「剔除财务公司科目后」的主业口径。

    单独看「经营现金流同比 +438.8%」会得出「主业回款大幅改善」的结论，而实际原因
    可能只是财务公司把存放在央行/同业的资金收回来了——那是资产负债表上的搬动，
    不是卖酒赚来的钱。所以这里必须同时给出两个口径，并指出差额由哪几个科目造成。
    """
    rows = parse_cash_flow_operating(text)
    if not rows:
        return None

    def _pair(name: str) -> tuple[float | None, float | None]:
        v = rows.get(name)
        if not v:
            return None, None
        return _c(v[0]), _c(v[1]) if v[1] is not None else None

    def _yoy(cur, prev):
        if cur is None or prev is None or not prev:
            return None
        return _c((cur / prev - 1) * 100, 1)

    fin_in_cur = fin_out_cur = 0.0
    fin_in_prev = fin_out_prev = 0.0
    has_prior = any(v[1] is not None for v in rows.values())
    # 必须按「流入侧 / 流出侧」分别归集再分别扣减。
    # 把两侧科目混成一个和数再同时从流入、流出里减掉，等于两侧各减一次同一个数、
    # 净额恰好互相抵消（实测原写法算出「剔除后 706.91 亿、同比 +438.8%」——
    # 与未剔除完全一样，等于这个功能根本没生效，且结果看起来毫无异常）。
    for name in _CF_FINANCE_ITEMS:
        kind = next((kd for kd, nm in _CF_OPERATING_ITEMS if nm == name), None)
        cur, prev = _pair(name)
        if cur is not None:
            if kind == "流入":
                fin_in_cur += cur
            else:
                fin_out_cur += cur
        if prev is not None:
            if kind == "流入":
                fin_in_prev += prev
            else:
                fin_out_prev += prev

    in_cur, in_prev = _pair("经营活动现金流入小计")
    out_cur, out_prev = _pair("经营活动现金流出小计")
    net_cur, net_prev = _pair("经营活动产生的现金流量净额")

    # 主业口径 = 总流入/总流出 里扣掉财务公司科目
    adj_in_cur = _c(in_cur - fin_in_cur) if in_cur is not None else None
    adj_out_cur = _c(out_cur - fin_out_cur) if out_cur is not None else None
    adj_cur = (_c(adj_in_cur - adj_out_cur)
               if adj_in_cur is not None and adj_out_cur is not None else None)
    adj_prev = None
    if has_prior and in_prev is not None and out_prev is not None:
        adj_prev = _c((in_prev - fin_in_prev) - (out_prev - fin_out_prev))

    # 主要变动科目：本期 vs 上期，按变动绝对值排序（只在上期数据存在时才有意义）
    swings = []
    if has_prior:
        for name, (cur, prev) in rows.items():
            if name.endswith("小计") or name.endswith("净额"):
                continue
            if cur is None or prev is None:
                continue
            diff = cur - prev
            if abs(diff) < 5:  # 5 亿元以下不进榜，避免噪音淹没信号
                continue
            swings.append({
                "科目": name,
                "本期_亿元": _c(cur),
                "上期_亿元": _c(prev),
                "变动_亿元": _c(diff),
                "是否财务公司科目": name in _CF_FINANCE_ITEMS,
            })
        swings.sort(key=lambda x: -abs(x["变动_亿元"]))

    return {
        "期间": period,
        "上期": prior_period,
        "经营活动现金流入小计_亿元": {"本期": in_cur, "上期": in_prev},
        "经营活动现金流出小计_亿元": {"本期": out_cur, "上期": out_prev},
        "经营活动产生的现金流量净额_亿元": {"本期": net_cur, "上期": net_prev},
        "净额同比_pct": _yoy(net_cur, net_prev),
        "主要变动科目": swings[:5],
        "剔除财务公司科目后": {
            "剔除的科目": sorted(n for n in _CF_FINANCE_ITEMS if n in rows),
            "经营性现金净额_亿元": adj_cur,
            "上期_亿元": adj_prev,
            "同比_pct": _yoy(adj_cur, adj_prev),
            "说明": "财务公司（吸收存款/缴存央行/同业拆放）科目对主业景气度无指示意义，"
                    "剔除后才能看「卖酒收钱」本身的变化",
        } if adj_cur is not None else None,
    }


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #
def extract(pdf_path: Path, plan_year: int | None = None, period: str | None = None,
            prior_period: str | None = None) -> dict:
    """自动判别报告类型并抽取（年报优先用含毛利率的主营业务表）。

    plan_year：年报「经营计划」讲的是**下一年度**的工作，传年报年份+1 才是计划所属年度。
    period / prior_period：现金流量表归因用的期间标签（如 "2026H1" / "2025H1"），
    只在能确定报告期时才传，否则归因结果不写期间、容易张冠李戴。
    """
    text = _read_text(pdf_path)
    if not text:
        return {}
    out: dict = {}
    mb = parse_main_business(text)
    if mb:
        out["经营结构"] = mb
    else:
        ss = parse_sales_situation(text)
        if ss:
            out["经营结构"] = ss
    dealers = parse_dealers(text)
    if dealers:
        out["经销商"] = dealers
    prod = parse_output_sales(text)
    if prod:
        # 「产销量情况分析表」只有酒类合计，「产品情况」表才有茅台酒/系列酒拆分。
        # 两者用合计-分项互校，任一环节对不上就退回只给合计（绝不展示错拆分）。
        lines = parse_product_lines(text)
        if lines and _sum_matches([r["产量"] for r in lines], prod[0].get("生产量")) \
                and _sum_matches([r["销量"] for r in lines], prod[0].get("销售量")):
            prod[0]["拆分"] = lines
        inv = parse_inventory_split(text)
        if inv and _sum_matches(
                [inv["成品酒"], inv["半成品酒"]], prod[0].get("库存量")):
            prod[0]["库存拆分"] = inv
        prod[0]["口径"] = ("生产量为当年基酒产量（含茅台酒与系列酒基酒），"
                          "不是成品酒出库量")
        out["产销量"] = prod
    plan = parse_business_plan(text, year=plan_year)
    if plan:
        out["经营计划"] = plan
    # 经营现金流同比归因（拆出财务公司科目，还原主业回款口径）
    ocf = build_ocf_attribution(text, period=period, prior_period=prior_period)
    if ocf:
        out["现金流归因"] = ocf
    return out


def _hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def build_operating_structure(code: str, refresh: bool = False) -> dict | None:
    """组装「经营结构」：最新定期报告（收入/占比/同比）+ 上一份年报（毛利率/年度计划）。

    - 最新报告是年报时，毛利率与收入同源，无需外挂；
    - 最新报告是半年报/季报时，「销售情况」表没有成本，毛利率回上一份年报取，
      并在结果里写明「毛利率口径」，避免把年报毛利率误读成当期毛利率。
    """
    from src.validation.cninfo import download_report_pdf, query_periodic_reports

    try:
        reports = query_periodic_reports(code)
    except Exception:
        return None
    if not reports:
        return None

    latest = reports[0]
    period = _period_label(latest.get("kind"), latest.get("year"))
    prior = next(
        (r for r in reports
         if r.get("kind") == latest.get("kind") and r.get("year") == (latest.get("year") or 0) - 1),
        None,
    )
    annual = next((r for r in reports if r.get("kind") == "年报"), None)

    key = {
        "code": code,
        # 抽取口径版本号：只靠报告标题做缓存键的话，解析逻辑升级后旧缓存会一直命中，
        # 新增字段（如「现金流归因」）永远进不了报告，且现象是「静默无数据」极难察觉。
        "v": _EXTRACT_VERSION,
        "latest": latest.get("title"),
        "prior": (prior or {}).get("title"),
        "annual": (annual or {}).get("title"),
    }
    cache_path = _CACHE_DIR / f"{code}.json"
    if not refresh and cache_path.exists():
        try:
            obj = json.loads(cache_path.read_text(encoding="utf-8"))
            if obj.get("_key") == key:
                return obj.get("data")
        except Exception:
            pass

    outdir = _DISCLOSURE_DIR / code

    def _grab(rep: dict | None) -> dict:
        if not rep:
            return {}
        try:
            plan_year = (rep.get("year") or 0) + 1 if rep.get("kind") == "年报" else None
            # 现金流量表「本期/上期」两列就是同比口径：本期 = 报告期，上期 = 去年同期
            per = _period_label(rep.get("kind"), rep.get("year"))
            pri = _period_label(rep.get("kind"), (rep.get("year") or 0) - 1) if rep.get("year") else None
            return extract(download_report_pdf(code, rep, outdir),
                           plan_year=plan_year, period=per, prior_period=pri)
        except Exception:
            return {}

    latest_data = _grab(latest)
    prior_data = _grab(prior)
    annual_data = _grab(annual)

    struct = latest_data.get("经营结构") or annual_data.get("经营结构")
    if not struct:
        return None

    is_annual = latest.get("kind") == "年报"
    margin_src = None
    margin_cut: dict[str, list[dict]] = {}
    if not is_annual and annual_data.get("经营结构", {}).get("切片"):
        margin_cut = annual_data["经营结构"]["切片"]
        margin_src = f"{annual.get('year')} 年报"

    prior_cut = (prior_data.get("经营结构") or {}).get("切片") or {}

    # 上年同期收入同比（原文本期表不含同比；年报主表自带同比，优先用自带的）
    prior_rev: dict[str, float] = {}
    for rows in prior_cut.values():
        for r in rows:
            if r.get("收入_亿元"):
                prior_rev[_norm_name(r["名称"])] = r["收入_亿元"]
    margin_by_name: dict[str, dict] = {}
    for rows in margin_cut.values():
        for r in rows:
            margin_by_name[_norm_name(r["名称"])] = r

    cuts_out: dict[str, list[dict]] = {}
    for cut, rows in struct.get("切片", {}).items():
        total = sum(r.get("收入_亿元") or 0 for r in rows)
        new_rows = []
        for r in rows:
            nm = _norm_name(r["名称"])
            rev = r.get("收入_亿元")
            yoy = r.get("收入同比_pct")
            if yoy is None and rev is not None and prior_rev.get(nm):
                yoy = _c((rev / prior_rev[nm] - 1) * 100, 1)
            mg = r.get("毛利率_pct")
            if mg is None and margin_by_name.get(nm):
                mg = margin_by_name[nm].get("毛利率_pct")
            new_rows.append({
                "名称": r["名称"],
                "收入_亿元": rev,
                "占比_pct": _c(rev / total * 100, 1) if (rev is not None and total) else None,
                "收入同比_pct": yoy,
                "毛利率_pct": mg,
            })
        cuts_out[cut] = new_rows

    data = {
        "期间": period,
        "报告": latest.get("title"),
        "口径": struct.get("口径"),
        "毛利率口径": margin_src,
        "切片": cuts_out,
        "i茅台_亿元": (latest_data.get("经营结构") or {}).get("i茅台_亿元"),
        "经销商": latest_data.get("经销商") or annual_data.get("经销商"),
        "产销量": latest_data.get("产销量") or annual_data.get("产销量"),
        # 产销量只披露在年报（半年报没有这张表）。而「期间」可能是 2026H1，
        # 不标注来源年份的话，读者会把 2025 年报的量读成当期量。
        "产销量口径": (
            f"{latest.get('year')} 年报" if latest_data.get("产销量")
            else (f"{annual.get('year')} 年报" if annual_data.get("产销量") else None)
        ),
        # 现金流归因优先用最新定期报告（半年报/季报有同比两列，年报也有），
        # 最新报告里抽不到（版式不认识）才退回上一份报告。
        "现金流归因": (latest_data.get("现金流归因") or prior_data.get("现金流归因")
                       or annual_data.get("现金流归因")),
        "年度经营计划": annual_data.get("经营计划"),
    }
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"_key": key, "data": data}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except Exception:
        pass
    return data
