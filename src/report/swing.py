# -*- coding: utf-8 -*-
"""「主要变动指标」候选榜：把当期变动最大的报表科目客观排出来，交给 LLM 做异动归因。

为什么替掉原先的「现金流异动归因」：

那份板块的证据链是 `mda_extract.build_ocf_attribution()` —— 它只解析**现金流量表**，
而科目级明细能不能抽到，完全取决于该标的定期报告 PDF 的版式。实测跟踪池 11 只标的
里只有 2 只抽到（茅台有值、神华整段是 0 与 None），其余 9 只在报告里只能挂一句
「本期现金流归因数据未取到」再加三百字免责声明 —— 也就是说那个板块在设计上就是给
茅台定制的，对其他标的只产生噪音。

而「当期变化最大的是什么」每个标的都不一样：银行看信用减值与吸收存款，煤企看折旧
与资本开支，家电看合同负债与销售费用，白酒看合同负债与销售费用率。所以这里不再预设
该讲哪个指标，而是**客观排榜**：把三大报表里变动金额最大的科目按分组配额挑出来，
让 LLM 从中挑 2-3 个做归因。

口径（三条都必须显式写进给 LLM 的事实里，否则它会把累计当单季、把时点当流量）：
1. 利润表 / 现金流量表列的是**年初至今累计**（中报即 H1），与去年同期的累计比；
2. 资产负债表列的是**季末时点**值，与去年同月末比；
3. 金额一律亿元（raw 层存的是元，这里换算）。

榜单只保证「这些科目动得大」，**不保证动得大就值得解释** —— 挑哪几个、怎么归因，
是 LLM 的事。事实层不替它下结论，这是本模块与叙事层之间的分工线。
"""
from __future__ import annotations

import pandas as pd

# --------------------------------------------------------------------------- #
# 候选科目
# --------------------------------------------------------------------------- #
# (表, 列名, 显示名, 分组)。显示名与 `scripts/gen_raw_schema.py` 的 COL_DOC 逐字一致
# —— 同一列在报告正文、schema 注释、这里出现三种中文写法的话，读者对不上。
#
# 🔴 有意排除的三类科目，别「顺手加回来」：
#   ① 总量科目（资产总计 / 负债合计 / 归母权益）—— 它们是上游明细的加总，异动必然
#      已被明细科目解释；而它们的绝对变动金额最大，进榜必然霸榜把配额吃光（银行尤其）。
#   ② 派生利润科目（营业利润 / 利润总额 / 净利润）—— 同上，是加总结果不是原因。
#   ③ 营业总收入 / 归母净利润 —— 它们是「季度数据表现」那一段的主角，在榜里会挤掉
#      真正有信息量的费用、减值、合同负债。
# ⚠️ 唯一的例外是「扣非归母净利润」：它同样是派生合计，但它与归母净利润的差额
#    = 非经常性损益，是「利润为什么变了」最常用的一个入口，因此保留。要删它之前先想清楚
#    这个入口让谁补上 —— 候选榜里已经没有归母净利润了。
_CANDIDATES: tuple[tuple[str, str, str, str], ...] = (
    # —— 损益（年初至今累计）——
    ("profit_sheet", "operating_cost", "营业成本", "损益"),
    ("profit_sheet", "operate_tax_add", "税金及附加", "损益"),
    ("profit_sheet", "sell_expense", "销售费用", "损益"),
    ("profit_sheet", "admin_expense", "管理费用", "损益"),
    ("profit_sheet", "research_expense", "研发费用", "损益"),
    ("profit_sheet", "finance_expense", "财务费用", "损益"),
    ("profit_sheet", "invest_income", "投资收益", "损益"),
    ("profit_sheet", "asset_impairment_loss", "资产减值损失", "损益"),
    ("profit_sheet", "credit_impairment_loss", "信用减值损失", "损益"),
    ("profit_sheet", "deduct_net_profit", "扣非归母净利润", "损益"),
    ("profit_sheet", "minority_interest", "少数股东损益", "损益"),
    # —— 现金流量（年初至今累计）——
    ("cash_flow", "ocf", "经营活动产生的现金流量净额", "现金流"),
    ("cash_flow", "icf", "投资活动产生的现金流量净额", "现金流"),
    ("cash_flow", "financing_cash_flow", "筹资活动产生的现金流量净额", "现金流"),
    ("cash_flow", "depreciation", "固定资产折旧", "现金流"),
    ("cash_flow", "capital_expenditure",
     "购建固定资产、无形资产和其他长期资产支付的现金（资本开支）", "现金流"),
    # —— 资产负债（季末时点）——
    ("balance_sheet", "monetary_funds", "货币资金", "资产负债"),
    ("balance_sheet", "inventory", "存货", "资产负债"),
    ("balance_sheet", "accounts_receivable", "应收账款", "资产负债"),
    ("balance_sheet", "notes_receivable", "应收票据", "资产负债"),
    ("balance_sheet", "contract_liabilities", "合同负债", "资产负债"),
    ("balance_sheet", "accounts_payable", "应付账款", "资产负债"),
    ("balance_sheet", "borrowings", "借款合计（短期 + 长期）", "资产负债"),
    ("balance_sheet", "construction_in_progress", "在建工程", "资产负债"),
    ("balance_sheet", "goodwill", "商誉", "资产负债"),
    ("balance_sheet", "lend_fund", "发放贷款及垫款（银行专用）", "资产负债"),
    ("balance_sheet", "accept_deposit", "吸收存款（银行专用）", "资产负债"),
)

# 每组配额。分组而不是整体排序，是因为三大报表的规模量级差着数量级：
# 银行一张资产负债表的科目变动动辄几百亿，整体排序会让损益与现金流科目**全部消失**，
# 而「利润为什么变了」恰恰只能从损益科目上读出来。
_QUOTA = {"损益": 4, "现金流": 3, "资产负债": 4}
_GROUP_ORDER = ("损益", "现金流", "资产负债")

# 入榜判据。三条必须同时存在于设计里，只留一条都会漏掉一整类标的：
#
# ① 绝对重大（变动 ≥ 参考规模的 1%）：大基数科目**即使增速温和，绝对额也已经改变了
#    一张表的量级**。2026H1 交行吸收存款 +7440 亿、同比只有 +8.1% —— 只按百分比判，
#    银行、公用事业、超大市值公司最重要的科目会被系统性地挡在榜外。
# ② 相对异动（|同比| ≥ 10% 且金额过地板）：抓「小科目大弹性」，如合同负债 -42%。
# ③ 地板（参考规模 0.1% 再兜 1 亿元）：纯挡噪音 —— 「其他应收款从 0.5 亿涨到 3 亿」
#    同比 +500%，但对报表毫无影响，只靠百分比它稳压真正重要的科目。
#
# 参考规模按分组取：损益与现金流取营业总收入，资产负债取资产总计。
# 拿营业收入当资产负债表科目的分母会荒谬（交行吸收存款 7440 亿 / 营收 1423 亿 = 523%）。
_FLOOR_RATIO = 0.001
_FLOOR_ABS_YI = 1.0
_MATERIAL_RATIO = 0.01
_YOY_MIN_PCT = 10.0

# 「现金流附注」的触发科目：这一条入榜，报告才渲染「总额 vs 剔除财务公司科目后」
# 那条对照条与科目表。理由见 build_valueline._swing_block。
_OCF_NAME = "经营活动产生的现金流量净额"


def _q_label(d: pd.Timestamp) -> str:
    """报告期标签（与 build_operating_structure 的期间写法一致：2026H1 / 2026年报）。"""
    y, m = d.year, d.month
    if m == 3:
        return f"{y}Q1"
    if m == 6:
        return f"{y}H1"
    if m == 9:
        return f"{y}Q3"
    return f"{y}年报"


def _yi(v) -> float | None:
    """元 → 亿元（保留 2 位小数）。NaN / None → None（**不写成 0**）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return round(f / 1e8, 2)


def _judge(cur: float | None, prev: float | None,
           floor: float, material: float) -> tuple[float | None, bool]:
    """→（同比 pct 或 None，是否够格入榜）。判据见文件顶部 `_FLOOR_RATIO` 那段。

    🔴 分母一律取 **|上期|**，「同比」= 变动额 ÷ 上期规模：

    - 上期为正时该式与 `cur / prev - 1` **逐字恒等** → 绝大多数科目行为不变；
    - 上期为负时必须换（这就是本函数唯一的语义分叉）：`cur / prev - 1` 会给出
      **方向相反**的数字。格力 2026H1 投资活动现金流由 -342.7 亿收窄到 -27.5 亿，
      `cur / prev - 1` = **-92.0%** —— 读者会读成「净额下降 92%」，而事实是流出
      收窄了 92%。改用 |上期| 后得 **+92.0%**，**符号与同一行的「变动」列一致**
      （正 = 净额改善），不会再出现「变动 +315.2 亿、同比 -92.0%」自相矛盾的一行。
      ⚠️ 上期为负时它不是严格意义的「同比」，所以事实里带 `同比口径` 标记，
      渲染端据此加口径注（见 `build()` 的「负基数同比科目」）。

    上期**规模过小**（|上期| ≤ 地板，含 0 与极小的正数）时仍不判同比：会算出
    「同比 -11088.9%」这种让人不敢信整张表的数字（实测来自上期 0.09 亿 / 本期 -9.89 亿）。
    此时变动金额本身就是异动信号，过地板即入榜。
    """
    if prev is None:
        return None, False
    delta = cur - prev
    if abs(prev) <= floor:
        return None, True
    yoy = round(delta / abs(prev) * 100, 1)
    return yoy, (abs(yoy) >= _YOY_MIN_PCT or abs(delta) >= material)


def _locate(raw: dict, table: str) -> pd.DataFrame | None:
    df = (raw or {}).get(table)
    if df is None or getattr(df, "empty", True) or "report_date" not in df.columns:
        return None
    return df.sort_values("report_date").reset_index(drop=True)


def _row_at(df: pd.DataFrame | None, d) -> pd.Series | None:
    """取某报告日的行。同一天出现多行（不同 report_type 的同名报表）时取非空字段最多的那行
    —— 直接 iloc[-1] 可能拿到一行大半是 NaN 的版本。"""
    if df is None:
        return None
    sub = df[df["report_date"] == d]
    if sub.empty:
        return None
    if len(sub) == 1:
        return sub.iloc[0]
    return sub.loc[sub.notna().sum(axis=1).idxmax()]


def _pick(row: pd.Series | None, col: str):
    if row is None or col not in row.index:
        return None
    return row[col]


def build(raw: dict) -> dict | None:
    """产出「主要变动指标」候选榜。无可用报表时返回 None（调用方据此不渲染这一段）。

    返回值只有事实与阈值，没有一句结论 —— 结论归 LLM（见本模块头部的分工线）。
    """
    ps = _locate(raw, "profit_sheet")
    if ps is None:
        return None
    latest_date = ps["report_date"].max()
    prior_date = latest_date - pd.DateOffset(years=1)

    tables = {t: _locate(raw, t) for t in ("profit_sheet", "balance_sheet", "cash_flow")}
    latest_rows = {t: _row_at(df, latest_date) for t, df in tables.items()}
    prior_rows = {t: _row_at(df, prior_date) for t, df in tables.items()}

    # 参考规模：损益 / 现金流取营业总收入，资产负债取资产总计。
    # 营业总收入：raw 里银行等金融股没有 revenue 列（只有 operating_revenue）→ 兜底
    rev_cur = None
    for col in ("revenue", "operating_revenue"):
        rev_cur = _yi(_pick(latest_rows.get("profit_sheet"), col))
        if rev_cur is not None:
            break
    asset_cur = _yi(_pick(latest_rows.get("balance_sheet"), "total_assets"))

    def _scale(group: str) -> float:
        """该分组的参考规模。资产总计缺失（部分港股）时退回营业总收入。"""
        if group == "资产负债" and asset_cur:
            return asset_cur
        return rev_cur or 0.0

    entries: list[dict] = []
    for table, col, name, group in _CANDIDATES:
        df = tables.get(table)
        if df is None or col not in df.columns:
            continue   # 该标的没有这一列（银行无销售费用、港股现金流表只有四行）→ 静默跳过
        cur = _yi(_pick(latest_rows.get(table), col))
        prev = _yi(_pick(prior_rows.get(table), col))
        if cur is None or prev is None:
            continue   # 缺去年同期的科目一律不进榜：没有对照就没有「异动」可言
        floor = max(_FLOOR_ABS_YI, _FLOOR_RATIO * _scale(group))
        material = _MATERIAL_RATIO * _scale(group)
        if abs(cur - prev) < floor:
            continue
        yoy, ok = _judge(cur, prev, floor, material)
        if not ok:
            continue
        e = {
            "名称": name,
            "分组": group,
            "口径": "年初至今累计" if group in ("损益", "现金流") else "季末时点",
            "本期_亿元": cur,
            "上期_亿元": prev,
            "变动_亿元": round(cur - prev, 2),
            "同比_pct": yoy,
        }
        if prev < 0 and yoy is not None:
            if cur > 0:
                # 跨零（上期净流出、本期净流入）：**不给百分比**。
                # 两个原因叠加：① 分母太小会让比值失真到不可读 —— 实测茅台 2026H1
                # 投资活动现金流 -3.10 亿 → +253.1 亿，Δ/|上期| = **+8265.9%**，
                # 这个数字没人会信，且会连累读者怀疑整张表；② 跨零处「增长/下降」
                # 本就没有意义。事实一句话就说清了：由净流出转为净流入。
                # 表述必须是中性的（由负转正），不能写「转正」—— 对财务费用这类科目
                # 「转正」听起来像好事，而这张表不做利好利空判断（见渲染端脚注）。
                e["同比_pct"] = None
                e["同比口径"] = "由负转正"
            else:
                e["同比口径"] = "上期为负"
        elif prev > 0 and cur < 0:
            e["同比_pct"] = None
            e["同比口径"] = "由正转负"
        if group == "损益" and rev_cur:
            e["占营业总收入_pct"] = round(cur / rev_cur * 100, 1)
        entries.append(e)

    # 组内按 |变动金额| 降序取配额；再按固定分组顺序重排，让报告里的表格稳定成
    # 「损益 → 现金流 → 资产负债」三块，而不是每次构建换一个顺序。
    picked: list[dict] = []
    for group in _GROUP_ORDER:
        rows = [e for e in entries if e["分组"] == group]
        rows.sort(key=lambda x: -abs(x["变动_亿元"]))
        picked.extend(rows[:_QUOTA[group]])

    # 同比口径「特殊」的科目：上期为负（分母取 |上期|）与跨越零点（不给百分比）。
    # 渲染端据此决定脚注要不要出现，LLM 也据此知道哪几行的百分比不能按「增速」读。
    special = [e["名称"] for e in picked if e.get("同比口径")]

    return {
        "报告期": _q_label(latest_date),
        "上期": _q_label(prior_date),
        "时点日": latest_date.date().isoformat(),
        "口径说明": (
            "「损益」「现金流」组为年初至今累计口径（本期累计 vs 去年同期累计）；"
            "「资产负债」组为季末时点口径（本期末 vs 去年同期末）；金额单位亿元；"
            "「同比」的分母一律取 |上期|"
        ),
        # 只给 LLM 看，渲染端不读（报告的脚注由 `特殊同比科目` 动态生成，
        # 见 build_valueline._swing_block）。上期为负时同比的符号是「方向」而不是
        # 「增速」，模型很容易顺手写成「同比 -92.0%，净额下降 92%」——那是反的。
        "特殊同比说明": (
            "「同比」的分母是 |上期|，因此上期为负的科目其正号表示净额改善、负号表示恶化，"
            "与同一行的变动金额同向；描述方向必须写成「流出收窄 / 流出扩大 / 由净流入转为净流出」"
            "这类说法，**不得写成「同比 -92% ＝ 下降 92%」**（旧式 cur/prev-1 才会给出那个反向数字）。"
            "跨越零点（上期与本期符号相反）的科目**不给百分比** —— 分母过小会算出失真比值"
            "（实测茅台投资活动现金流 -3.10 亿 → +253.1 亿 = +8265.9%），"
            "此时只做事实描述，不要自行补算百分比。下列科目属于这两类："
            + ("、".join(special) if special else "（本期无此类科目）")
        ),
        "门槛说明": (
            "入榜条件（满足其一即可）：① 变动金额 ≥ 参考规模的 1% —— 参考规模在损益与"
            "现金流组取营业总收入、在资产负债组取资产总计，这一条专门捞「增速温和但绝对额"
            "巨大」的科目（如银行吸收存款同比只 +8%、金额却动了上千亿）；"
            f"② 同比绝对值 ≥ {_YOY_MIN_PCT:.0f}% 且变动金额 ≥ 地板。"
            "地板 = max(1 亿元, 参考规模的 0.1%)，用于挡掉「其他应收款从 0.5 亿涨到 3 亿」"
            "这类靠百分比挤进榜、对报表毫无影响的噪音。|上期| ≤ 地板（基数过小）时不判同比，"
            "只看变动金额。每组按变动金额取前几名，组内序号即重要性排序"
        ),
        # 这句必须给：不给的话 LLM 会稳定地写出「其余科目未出现异常变动」——
        # 而榜上没有的科目，真实原因可能是该标的报表压根没这一栏（港股只有十几个字段）、
        # 缺去年同期数、或变动幅度在门槛之下。三者与「没有变化」是四件不同的事。
        "未入榜说明": (
            "榜上未出现的科目，原因只可能是：该标的报表没有这一栏、缺去年同期可比数、"
            "或变动未达上面的门槛。不得据此断言「其他科目无变化」"
        ),
        "候选": picked,
        "特殊同比科目": special,
        "经营现金流是否入榜": any(e["名称"] == _OCF_NAME for e in picked),
    }
