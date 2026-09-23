# -*- coding: utf-8 -*-
"""周期定位：把多个指标的**方向**汇总成「增长 × 通胀」四象限。

设计原则（三条，决定了这里为什么这么写）
----------------------------------------
1. **规则透明、可复算**。象限是「若干指标本期相对上期的方向取净和」的机械输出，
   不引入权重、不引入阈值调参。页面上会**列出每一票**（指标名 / 本期 / 上期 / 方向），
   读者可以自己复核，也可以不同意。

2. **不做资产推荐**。象限在页面上只呈现其**宏观定义**（增长与通胀的方向组合），
   不写「该买什么」。理由：本项目投研内容的一贯铁律是「只做数据呈现、不给买卖建议」，
   而「某象限历史表现好」极易被读成建议。

3. **方向分歧要如实说**。净和为 0 时**不允许**硬塞进某个象限，也不允许沉默 ——
   输出「方向分歧」并列出互相抵消的票。永远为真的结论会训练人忽略结论。

🔴 一个容易搞混的点：这里的「方向」是**同比的变化方向**（二阶），不是同比本身的符号。
   CPI 同比 +0.8% 是正通胀，但若上期是 +0.5%，则通胀在**回升**（方向 +1）。
   美林时钟关心的是「改善还是恶化」，所以取二阶方向。页面上必须写清这一点。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 象限 → 宏观定义（仅方向组合，不含任何资产表述）
QUADRANT_DEF = {
    "复苏": "增长回升、通胀回落",
    "过热": "增长回升、通胀回升",
    "滞胀": "增长回落、通胀回升",
    "衰退": "增长回落、通胀回落",
}

QUADRANT_ORDER = ("复苏", "过热", "滞胀", "衰退")


@dataclass(frozen=True)
class Vote:
    """一个指标的一张票。"""

    key: str
    name: str
    period: str
    latest: float
    prev: float | None
    direction: int          # +1 上行 / -1 下行 / 0 持平或弃权
    abstain: bool = False   # 因数据缺失/跨零点无法判定方向

    @property
    def mark(self) -> str:
        if self.abstain:
            return "—"
        return {1: "↑", -1: "↓", 0: "→"}[self.direction]


@dataclass
class ClockResult:
    """周期定位结果。"""

    quadrant: str                       # 复苏 / 过热 / 滞胀 / 衰退 / 方向分歧
    growth_score: int                   # 增长侧净票（正 = 上行）
    inflation_score: int                # 通胀侧净票
    growth_votes: list[Vote] = field(default_factory=list)
    inflation_votes: list[Vote] = field(default_factory=list)
    note: str = ""

    @property
    def growth_dir(self) -> str:
        return _dir_word(self.growth_score)

    @property
    def inflation_dir(self) -> str:
        return _dir_word(self.inflation_score)

    @property
    def definition(self) -> str:
        return QUADRANT_DEF.get(self.quadrant, "")

    def basis(self) -> str:
        """一句话说明判定依据（给页面副标题用）。"""
        return (f"增长侧 {len(self.growth_votes)} 项净得 {self.growth_score:+d}"
                f"（{self.growth_dir}）· 通胀侧 {len(self.inflation_votes)} 项净得 "
                f"{self.inflation_score:+d}（{self.inflation_dir}）")


def _dir_word(score: int) -> str:
    if score > 0:
        return "上行"
    if score < 0:
        return "下行"
    return "持平"


def _vote(key: str, name: str, sv) -> Vote:
    """由 SeriesView 生成一张票。

    ⚠️ 用 `sv.chg`（已按口径算好的变化量）而不是 `latest - prev` 的原始差：
       chg 为 None 时（上期为 0、或跨零点）该票**弃权**，方向记 0 并标 abstain。
       直接把 None 当 0 会让「无法判定」与「确实持平」混为一谈。
    """
    if sv is None or not sv.ok or sv.prev is None:
        return Vote(key=key, name=name, period="", latest=float("nan"),
                    prev=None, direction=0, abstain=True)
    if sv.chg is None:
        return Vote(key=key, name=name, period=sv.period, latest=sv.latest,
                    prev=sv.prev, direction=0, abstain=True)
    d = 1 if sv.chg > 0 else (-1 if sv.chg < 0 else 0)
    return Vote(key=key, name=name, period=sv.period, latest=sv.latest,
                prev=sv.prev, direction=d)


def locate(growth: list[tuple[str, str, object]],
           inflation: list[tuple[str, str, object]]) -> ClockResult:
    """按增长侧与通胀侧指标的方向定位象限。

    Args:
        growth / inflation: `(key, 展示名, SeriesView)` 三元组列表。

    净票 = 所有非弃权票的方向之和。**两端都非零**才能定象限；
    任一侧净票为 0 → 「方向分歧」，如实说明是哪一侧抵消了。
    """
    g_votes = [_vote(k, n, sv) for k, n, sv in growth]
    i_votes = [_vote(k, n, sv) for k, n, sv in inflation]

    g_score = sum(v.direction for v in g_votes if not v.abstain)
    i_score = sum(v.direction for v in i_votes if not v.abstain)

    if g_score == 0 or i_score == 0:
        which = []
        if g_score == 0:
            which.append("增长侧")
        if i_score == 0:
            which.append("通胀侧")
        # ⚠️ 文案不含任何标记语法（`**` / markdown）：它会原样进入 HTML 与图卡，
        #    渲染端不会做 markdown 解析 —— 写了就成了页面上可见的星号。
        note = ("、".join(which) + "指标方向互相抵消，无法定位象限。"
                "此时不应按任一象限解读 —— 指标本身在给出矛盾信号。")
        return ClockResult(quadrant="方向分歧", growth_score=g_score,
                           inflation_score=i_score, growth_votes=g_votes,
                           inflation_votes=i_votes, note=note)

    if g_score > 0:
        quadrant = "过热" if i_score > 0 else "复苏"
    else:
        quadrant = "滞胀" if i_score > 0 else "衰退"

    return ClockResult(quadrant=quadrant, growth_score=g_score,
                       inflation_score=i_score, growth_votes=g_votes,
                       inflation_votes=i_votes)
