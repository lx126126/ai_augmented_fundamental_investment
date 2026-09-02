"""解析官方年报 PDF 的「主要会计数据」表，提取金标准财务指标。

用 pymupdf 的 find_tables 识别表格结构（比正则更可靠）。
「主要会计数据」表含近三年对比（重述后/重述前），支持提取多年份。
字段名、单位因公司而异，故用别名表 + 单位识别通用化。
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # pymupdf

# 标准字段 → 年报里的字段名别名（不同公司披露口径不同）
PDF_FIELD_ALIASES = {
    "operating_revenue": ["营业收入", "营业总收入"],
    "net_profit_parent": [
        "归属于本公司股东的净利润", "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润", "归属于母公司所有者的净利润",
    ],
    "ocf": ["经营活动产生的现金流量净额", "经营活动现金流量净额"],
    "total_equity": [
        "归属于本公司股东的净资产", "归属于上市公司股东的净资产",
        "归属于母公司股东的净资产", "归属于母公司所有者权益",
    ],
    "total_assets": ["资产总计", "总资产", "资产总额"],
    "total_liabilities": ["负债合计", "负债总计", "负债总额"],
    "share_capital": ["期末总股本", "总股本", "股本"],
}

# 「主要会计数据」表列结构因公司而异（有无重述、列数不同），故动态从表头识别

# 单位 → 换算到「元」的乘数
_UNIT_MULTIPLIER = {
    "元": 1,
    "万元": 1e4,
    "百万元": 1e6,
    "亿元": 1e8,
}


def _clean(s: str) -> str:
    """去空白与换行，用于字段名匹配。"""
    return str(s).replace("\n", "").replace(" ", "").replace("\u3000", "")


def _parse_number(s: str) -> float | None:
    """解析数字（含千分位），如 '294,916' → 294916.0。"""
    s = _clean(s).replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _match_field(name: str) -> str | None:
    """字段名 → 标准字段，精确匹配别名（自动去掉「（元）」「（百万元）」等括号后缀）。"""
    name = _clean(name)
    name = re.sub(r"[（(][^）)]*[）)]", "", name)  # 去「（元）」等后缀
    for field, aliases in PDF_FIELD_ALIASES.items():
        if name in aliases:
            return field
    return None


def _detect_unit(page_text: str) -> str:
    """从页面文本识别金额单位，默认「元」。

    兼容「单位：百万元」「金额单位：人民币百万元」等格式。
    """
    for unit in ("百万元", "万元", "亿元", "元"):
        if f"单位：{unit}" in page_text or f"单位: {unit}" in page_text:
            return unit
    # 宽松：金额单位：人民币百万元 / 单位：人民币百万元
    m = re.search(r"单位[:：]\s*[^，。\n]*?(百万元|万元|亿元|元)", page_text)
    if m:
        return m.group(1)
    return "元"


def _find_main_table(doc) -> tuple[list, str] | None:
    """定位「主要会计数据」表（含「营业收入」行），返回 (表格数据, 单位)。"""
    for page in doc:
        page_text = page.get_text()
        if "主要会计数据" not in page_text:
            continue
        for t in page.find_tables().tables:
            data = t.extract()
            if not data:
                continue
            # 表格任意单元格含「营业收入」即认为是「主要会计数据」表
            flat = [_clean(str(c)) for row in data for c in row]
            if any("营业收入" in c for c in flat):
                return data, _detect_unit(page_text)
    return None


def _extract_year_cols(table: list) -> dict[int, dict]:
    """动态识别「主要会计数据」表的年份列，返回 {年份: {"restated": 列, "original": 列}}。

    列结构因公司而异：有重述时前两年分「重述后/重述前」两列（相邻），无重述时每年一列。
    """
    header = table[0]
    year_cols: dict[int, int] = {}
    for ci, cell in enumerate(header):
        m = re.search(r"(20\d{2})", _clean(str(cell)))
        if m:
            year = int(m.group(1))
            if year not in year_cols:
                year_cols[year] = ci

    # 判断是否有重述（表头第二行含「重述前」）
    has_restate = len(table) > 1 and any("重述前" in _clean(str(c)) for c in table[1])
    latest_year = max(year_cols.keys()) if year_cols else None

    result: dict[int, dict] = {}
    for year, ci in sorted(year_cols.items()):
        if has_restate and year != latest_year:
            result[year] = {"restated": ci, "original": ci + 1}
        else:
            result[year] = {"restated": ci, "original": ci}
    return result


def parse_financials_by_year(pdf_path: str | Path) -> dict[int, dict[str, dict]]:
    """解析「主要会计数据」表，返回 {年份: {字段: {"restated": 元, "original": 元}}}。

    覆盖近三年；字段名/单位/列结构按公司自适应。所有金额已统一换算到「元」。
    """
    doc = fitz.open(str(pdf_path))
    found = _find_main_table(doc)
    doc.close()
    if not found:
        return {}
    table, unit = found
    mult = _UNIT_MULTIPLIER.get(unit, 1)

    year_cols = _extract_year_cols(table)
    result: dict[int, dict[str, dict]] = {}

    for row in table:
        # 指标名可能不在第一列（如格力列0为空边框），扫描整行找能匹配字段的单元格
        field = None
        for cell in row:
            f = _match_field(_clean(str(cell)))
            if f:
                field = f
                break
        if field is None:
            continue
        for year, cols in year_cols.items():
            restated = _parse_number(row[cols["restated"]]) if len(row) > cols["restated"] else None
            original = _parse_number(row[cols["original"]]) if len(row) > cols["original"] else None
            if restated is not None:
                restated *= mult
            if original is not None:
                original *= mult
            if restated is not None or original is not None:
                result.setdefault(year, {})[field] = {
                    "restated": restated,
                    "original": original,
                }
    return result


def parse_key_financials(pdf_path: str | Path) -> dict:
    """解析「主要会计数据」表最新年份，返回 {字段: 值(元，重述后)}。"""
    by_year = parse_financials_by_year(pdf_path)
    if by_year:
        latest_year = max(by_year.keys())
        return {f: v["restated"] for f, v in by_year[latest_year].items()}
    # find_tables 失败时，用文本正则兜底（只提取最新年）
    return _parse_by_text(pdf_path)


def _parse_by_text(pdf_path: str | Path) -> dict:
    """文本正则兜底：find_tables 对不规则表格（跨行单元格/列错位）失效时，
    从「主要会计数据」页纯文本提取最新年关键指标。

    字段名容忍跨行空格，返回 {字段: 值(元)}。
    """
    doc = fitz.open(str(pdf_path))
    page_text = ""
    for page in doc:
        txt = page.get_text()
        if "主要会计数据" in txt:
            page_text = txt
            break
    doc.close()
    if not page_text:
        return {}

    unit = _detect_unit(page_text)
    mult = _UNIT_MULTIPLIER.get(unit, 1)
    flat = page_text.replace("\n", " ").replace("\u3000", " ")

    # (字段, 正则)：字段名容忍跨行，后跟「（单位）」和最新年数值
    num = r"(\d[\d,]*\.?\d*)"
    unit_suffix = r"(?:[（(][^）)]*[）)])?"  # 全角/半角括号
    patterns = [
        ("operating_revenue", r"营业收入" + unit_suffix + r"\s*" + num),
        ("net_profit_parent", r"归属于[^0-9]{0,12}?的?净利润" + unit_suffix + r"\s*" + num),
        ("ocf", r"经营活动[^0-9]{0,15}?现金\s*流量净额" + unit_suffix + r"\s*" + num),
        ("total_equity", r"归属于[^0-9]{0,12}?的?净资产" + unit_suffix + r"\s*" + num),
        ("total_assets", r"总资产" + unit_suffix + r"\s*" + num),
        ("total_liabilities", r"负债合?计" + unit_suffix + r"\s*" + num),
        ("share_capital", r"总股本" + unit_suffix + r"\s*" + num),
    ]
    result: dict[str, float] = {}
    for field, pat in patterns:
        m = re.search(pat, flat)
        if m:
            try:
                result[field] = float(m.group(1).replace(",", "")) * mult
            except ValueError:
                continue
    return result


# 合并资产负债表字段 → 标准字段名（与 cleaner 输出列名对齐）
BALANCE_SHEET_ITEMS: dict[str, str] = {
    "货币资金": "monetary_funds",
    "应收账款": "accounts_receivable",
    "存货": "inventory",
    "流动资产合计": "current_assets",
    "固定资产": "fixed_assets",
    "资产总计": "total_assets",
    "短期借款": "short_term_loan",
    "应付账款": "accounts_payable",
    "一年内到期的非流动负债": "noncurrent_liab_1y",
    "流动负债合计": "current_liabilities",
    "长期借款": "long_term_loan",
    "应付债券": "bond_payable",
    "租赁负债": "lease_liabilities",
    "长期应付款": "long_payable",
    "负债合计": "total_liabilities",
    "未分配利润": "retained_profit",
    "归属于母公司股东权益合计": "total_equity",
    "少数股东权益": "minority_equity",
    "股东权益合计": "total_equity_all",
    "归属于母公司所有者权益合计": "total_equity",
    "归属于母公司股东的净资产": "total_equity",
    # 无「合计」后缀的变体（茅台等）
    "归属于母公司所有者权益": "total_equity",
    "归属于母公司股东权益": "total_equity",
    "所有者权益合计": "total_equity_all",
}


def _match_bs_field(name: str) -> str | None:
    """合并资产负债表字段名 → 标准字段（宽松匹配：去括号后缀、去空格）。"""
    name = _clean(name)
    name = re.sub(r"[（(][^）)]*[）)]", "", name)
    if name in BALANCE_SHEET_ITEMS:
        return BALANCE_SHEET_ITEMS[name]
    compact = name.replace(" ", "")
    for k, v in BALANCE_SHEET_ITEMS.items():
        if k.replace(" ", "") == compact:
            return v
    return None


def _parse_balance_sheet_lines(pages_text: list[str]) -> dict[str, float]:
    """文本行扫描解析合并资产负债表（有边框/无边框通用）。

    结构规律：「字段名 → 附注编号 → 本期值 → 上期值」。附注编号有两种写法：
    汉字前缀（神华「五、1」）或纯数字（茅台「28」）。跳过附注编号后，
    取字段名后的第一个有效数值（本期/最新年）。

    字段名用 _match_bs_field 宽松匹配（去括号后缀、去空格）。
    """
    full = "\n".join(pages_text)
    # 合并括号内换行断开的字段名（如「所有者权益（或股东权\n益）合计」）
    full = re.sub(r"（[^（）\n]*\n[^（）]*）", lambda m: m.group(0).replace("\n", ""), full)
    unit = _detect_unit(full)
    mult = _UNIT_MULTIPLIER.get(unit, 1)

    lines = [ln.strip() for ln in full.split("\n")]
    result: dict[str, float] = {}
    for i, ln in enumerate(lines):
        field = _match_bs_field(ln)
        if field is None:
            continue
        nums: list[float] = []
        note_skipped = False  # 字段名后第一个小整数视为附注编号，只跳一次
        for j in range(i + 1, min(i + 12, len(lines))):
            cell = lines[j].replace(",", "").replace("\u200a", "").strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", cell):
                v = float(cell)
                # 纯数字附注编号（1-99 整数）：字段名后第一个小整数，跳过
                if not note_skipped and v == int(v) and 1 <= v <= 99:
                    note_skipped = True
                    continue
                nums.append(v)
                if len(nums) == 2:
                    break
            elif cell in ("", "-", "-*", "—"):
                continue
            elif re.fullmatch(r"[五四三二一0-9]+、\d*", cell) or re.fullmatch(r"五、\d+", cell):
                continue  # 汉字附注编号
            else:
                # 遇到下一个字段名则停止（该字段本期值未披露）
                if _match_bs_field(cell):
                    break
                # 纯中文行（未在映射表中的会计科目名，如「长期应付职工薪酬」「预计负债」）
                # 也是字段名，说明当前字段未披露，停止扫描避免误取下一个字段的值
                if re.fullmatch(r"[\u4e00-\u9fa5（）：:、，,]+", cell):
                    break
        if nums:
            result[field] = nums[0] * mult
    return result


def parse_balance_sheet(pdf_path: str | Path) -> dict[str, float]:
    """解析「合并资产负债表」主表，返回 {标准字段: 最新年值(元)}。

    用于多源交叉校验的金标准：第三方接口（东财/新浪同源）在「同一控制下企业合并
    追溯重述」等特殊情形下可能抓取错误，官方年报 PDF 的合并资产负债表是唯一权威源。

    解析策略：文本行扫描（「字段名 → 附注 → 本期值 → 上期值」），对合并资产负债表
    的有边框/无边框排版均适用；不依赖 find_tables（跨页分段时表头丢失、母公司表
    混入会导致错位）。
    """
    doc = fitz.open(str(pdf_path))

    # 定位主表页范围：从「合并资产负债表」标题页开始，到合并表总计行结束。
    # 结束判定两条路径：
    #  1. 「负债和…权益总计」（含跨行变体「负债和所有者权益（或…）总计」）出现 → 该页即结束；
    #  2. 「母公司资产负债表」标题出现 → 合并表已结束，但部分公司（如茅台）合并表
    #     总计行（少数股东权益/所有者权益合计）与该标题同页，故只保留标题前文本。
    in_table = False
    pages_text: list[str] = []
    for page in doc:
        txt = page.get_text()
        if not in_table and "合并资产负债表" in txt and "货币资金" in txt and "流动资产" in txt:
            in_table = True
        if not in_table:
            continue
        # 母公司资产负债表 = 合并表硬结束：切分标题前的合并表总计行，丢弃母公司表
        if "母公司资产负债表" in txt:
            head = txt.split("母公司资产负债表")[0]
            if head.strip():
                pages_text.append(head)
            break
        pages_text.append(txt)
        if ("负债和股东权益总计" in txt or "负债和所有者权益总计" in txt
                or "负债和所有者权益（或" in txt or "负债和股东权益（或" in txt):
            break
    doc.close()

    if not pages_text:
        return {}
    return _parse_balance_sheet_lines(pages_text)


# ---------------------------------------------------------------------------
# 利润表 / 现金流量表字段映射（对齐 cleaner 输出的 profit_sheet / cash_flow 列名）
# ---------------------------------------------------------------------------

# 利润表字段 → 标准字段（东财 profit_sheet 列名）
INCOME_STATEMENT_ITEMS: dict[str, str] = {
    "营业收入": "operating_revenue",
    "营业总收入": "operating_revenue",
    "营业成本": "operating_cost",
    "营业总成本": "operating_cost",
    "营业利润": "operating_profit",
    "利润总额": "total_profit",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "net_profit_parent",
    "归属于上市公司股东的净利润": "net_profit_parent",
    "归属于母公司所有者的净利润": "net_profit_parent",
    "销售费用": "sell_expense",
    "管理费用": "admin_expense",
    "财务费用": "interest_expense",
    "所得税费用": "income_tax",
    "利息费用": "interest_expense",
}

# 现金流量表字段 → 标准字段（东财 cash_flow 列名）
CASH_FLOW_ITEMS: dict[str, str] = {
    "经营活动产生的现金流量净额": "ocf",
    "经营活动现金流量净额": "ocf",
    "购建固定资产、无形资产和其他长期资产支付的现金": "capital_expenditure",
    "购建固定资产、无形资产和其他长期资产所支付的现金": "capital_expenditure",
    "固定资产折旧": "depreciation",
    "投资活动产生的现金流量净额": "icf",
    "筹资活动产生的现金流量净额": "fcf",
}


def _match_item(name: str, mapping: dict[str, str]) -> str | None:
    """会计科目名 → 标准字段（宽松匹配：去括号后缀、去空格、去换行、去序号前缀）。

    年报利润表/现金流表的科目名常带前缀：「一、营业收入」「减：营业成本」
    「二、营业利润」「加：其他收益」等，须剥离序号（一、二、三…/1、2、3…）
    和「加：/减：/其中：」等前缀后再匹配。
    """
    name = _clean(name)
    name = re.sub(r"[（(][^）)]*[）)]", "", name)
    # 剥离「一、」「1、」「减：」「加：」「其中：」等前缀
    name = re.sub(r"^[一二三四五六七八九十0-9]+[、.．]", "", name)
    name = re.sub(r"^(加|减|其中|其中：[^：]*|其中：)[：:]?", "", name)
    name = name.strip()
    if name in mapping:
        return mapping[name]
    compact = name.replace(" ", "")
    for k, v in mapping.items():
        if k.replace(" ", "") == compact:
            return v
    return None


def _parse_statement_lines(pages_text: list[str], mapping: dict[str, str],
                           stop_markers: tuple[str, ...]) -> dict[str, float]:
    """通用主表文本行扫描解析（利润表 / 现金流量表）。

    与资产负债表解析同构：字段名 → 附注编号 → 本期值（取字段名后第一个有效数值）。
    注意利润表/现金流量表存在负数（费用为负、现金流可为负），故数值正则须容忍负号，
    且不能把「附注编号小整数」误判——费用类科目本期值可能本身就是小负数。
    """
    full = "\n".join(pages_text)
    full = re.sub(r"（[^（）\n]*\n[^（）]*）", lambda m: m.group(0).replace("\n", ""), full)
    unit = _detect_unit(full)
    mult = _UNIT_MULTIPLIER.get(unit, 1)

    # 合并字段名内部断行：相邻两行都是「纯中文（含序号/冒号等，无数字）」时拼接。
    # 年报字段名常因排版断成两行（如茅台「筹资活动产生的现金流\n量净额」），
    # 断点两侧均为中文、无数字，拼接后可被 _match_item 精确匹配。
    lines = [ln.strip() for ln in full.split("\n")]
    merged_lines: list[str] = []
    _zh_re = re.compile(r"^[\u4e00-\u9fa5（）()：:、，,一二三四五六七八九十加减其中\-—\.\s]+$")
    i = 0
    while i < len(lines):
        ln = lines[i]
        # 当前行是纯中文（字段名片段）且下一行也是纯中文 → 拼接
        if ln and _zh_re.match(ln) and i + 1 < len(lines) and _zh_re.match(lines[i + 1]):
            merged_lines.append(ln + lines[i + 1])
            i += 2
            continue
        merged_lines.append(ln)
        i += 1
    lines = merged_lines
    result: dict[str, float] = {}
    for i, ln in enumerate(lines):
        field = _match_item(ln, mapping)
        if field is None:
            continue
        nums: list[float] = []
        note_skipped = False
        for j in range(i + 1, min(i + 12, len(lines))):
            cell = lines[j].replace(",", "").replace("\u200a", "").strip()
            # 括号负值：(4) → -4
            neg = False
            if re.fullmatch(r"\(\d+(\.\d+)?\)", cell):
                neg = True
                cell = cell[1:-1]
            if re.fullmatch(r"-?\d+(\.\d+)?", cell):
                v = float(cell)
                if neg:
                    v = -v
                # 附注编号：字段名后第一个「正整数」且 <=99，跳过（费用科目本期值可为负，不影响）
                if not note_skipped and v > 0 and v == int(v) and 1 <= v <= 99:
                    note_skipped = True
                    continue
                nums.append(v)
                if len(nums) == 1:
                    break
            elif cell in ("", "-", "-*", "—"):
                continue
            elif re.fullmatch(r"[五四三二一0-9]+、\d*", cell) or re.fullmatch(r"五、\d+", cell):
                continue
            else:
                if _match_item(cell, mapping):
                    break
                if re.fullmatch(r"[\u4e00-\u9fa5（）：:、，,]+", cell):
                    break
        if nums:
            val = nums[0] * mult
            # 资本开支本质是「支出金额」，接口统一存正数；年报 PDF 若用括号负值
            # 表示现金流出（如神华「(48,398)」），须取绝对值对齐接口口径。
            if field == "capital_expenditure":
                val = abs(val)
            result[field] = val
    return result


def _extract_statement_pages(doc, title: str, stop_titles: tuple[str, ...],
                             end_markers: tuple[str, ...]) -> list[str]:
    """定位主表页范围：从标题页开始，到结束标记（总计行）或下一张表标题为止。"""
    in_table = False
    pages_text: list[str] = []
    for page in doc:
        txt = page.get_text()
        if not in_table and title in txt:
            in_table = True
        if not in_table:
            continue
        # 下一张表标题（如「合并现金流量表」「母公司利润表」）= 硬结束
        stopped = False
        for st in stop_titles:
            if st in txt and st != title:
                head = txt.split(st)[0]
                if head.strip():
                    pages_text.append(head)
                stopped = True
                break
        if stopped:
            break
        pages_text.append(txt)
        for m in end_markers:
            if m in txt:
                return pages_text
    return pages_text


def parse_income_statement(pdf_path: str | Path) -> dict[str, float]:
    """解析「合并利润表」主表，返回 {标准字段: 最新年值(元)}。

    字段对齐 cleaner 输出的 profit_sheet 列（operating_revenue/operating_cost/
    operating_profit/total_profit/net_profit/net_profit_parent/sell_expense/
    admin_expense/interest_expense/income_tax）。
    """
    doc = fitz.open(str(pdf_path))
    pages_text = _extract_statement_pages(
        doc, "合并利润表",
        stop_titles=("母公司利润表", "合并现金流量表", "合并资产负债表"),
        end_markers=("五、合并财务报表项目注释", "七、合并财务报表项目注释", "基本每股收益"),
    )
    doc.close()
    if not pages_text:
        return {}
    return _parse_statement_lines(pages_text, INCOME_STATEMENT_ITEMS, ())


def parse_cash_flow_statement(pdf_path: str | Path) -> dict[str, float]:
    """解析「合并现金流量表」主表，返回 {标准字段: 最新年值(元)}。

    字段对齐 cleaner 输出的 cash_flow 列（ocf/icf/fcf/capital_expenditure/depreciation）。
    """
    doc = fitz.open(str(pdf_path))
    pages_text = _extract_statement_pages(
        doc, "合并现金流量表",
        stop_titles=("母公司现金流量表", "合并利润表", "合并资产负债表"),
        end_markers=("五、合并财务报表项目注释", "七、合并财务报表项目注释", "汇率变动对现金及现金等价物的影响"),
    )
    doc.close()
    if not pages_text:
        return {}
    return _parse_statement_lines(pages_text, CASH_FLOW_ITEMS, ())
