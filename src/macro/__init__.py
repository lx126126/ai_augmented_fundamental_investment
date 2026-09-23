# -*- coding: utf-8 -*-
"""宏观数据看板：指标定义 / 拉取 / 派生 / 周期定位。

定位（为什么单独成模块，而不并进 src/data）
--------------------------------------------
个股链路（`src/data` + `src/report`）回答「这家公司怎么样」；
本模块回答「现在整个市场处在什么位置」。两者共享同一套网络出口配置与重试约定
（`src.data.fetcher` / `src.data.retry`），但**数据形态完全不同**：

| | 个股链路 | 宏观链路 |
|---|---|---|
| 形态 | 结构化财报表（宽表，按报告期对齐） | 时间序列（每个指标一条，长 10~30 年） |
| 落盘 | `data/raw/{code}/{table}.parquet` | 不落盘（接口每次回传全量历史） |
| 可变性 | 上游会**重述**，需版本留痕 | 官方口径，极少回填 |
| 频率 | 季度 | 日 / 月 / 季 混杂 |

所以不复用 `adapter` / `storage`，只复用 `retry` 与网络出口配置。

模块职责
--------
- `indicators.py` —— 指标定义表（**唯一真源**：名称/单位/口径/数据源/是否参与周期定位）
- `source.py` —— AKShare 接口 → 标准化时间序列（期间归一化 + 排序 + 去 NaN）
- `derive.py` —— 序列 → 展示视图（值/上期/变化量/历史分位/迷你走势），含派生指标算法
- `clock.py` —— 周期定位（增长 × 通胀四象限），规则透明可复算
- `build.py` —— 编排：拉取 → 派生 → 产出渲染 payload（单指标失败降级但不静默）

一句话记住本模块的四条铁律
--------------------------
1. 每个指标**带自己的数据日期**（宏观发布时点差异极大，混排会误导）
2. **变化量有三种口径**（收益率用 bp、占比指标用 pct、其余用 %），不可混用
3. **色阶只表示变动方向，不表示利好利空**（失业率上行与用电量上行的含义相反）
4. **展示文案是纯文本，不是 Markdown** —— 渲染端是 HTML，不做任何解析。
   写 `**重点**` 会在页面上原样显示星号，且**任何一层都不会报错**。
   `tests/test_macro.py` 里有一组断言专门扫这件事（实测漏过 3 处）。
"""
from __future__ import annotations

from .build import DATA_GAPS, build_payload
from .clock import ClockResult, locate
from .indicators import BY_KEY, GROUP_ORDER, INDICATORS, Indicator

__all__ = [
    "build_payload",
    "DATA_GAPS",
    "ClockResult",
    "locate",
    "INDICATORS",
    "BY_KEY",
    "GROUP_ORDER",
    "Indicator",
]
