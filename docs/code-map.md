# 代码实况地图

> 对 `architecture.md`（v2.0，2026-09-02 定稿）的**实况核对**，不是替代品。
> architecture.md 写「设计意图」，本文件写「代码现在到底是什么样」。
> 生成日期：2026-09-16 · 核对方式：AST 扫描 + 实际导入 + 数仓直连查询

## 0. 规模总览

| 项 | 实测（2026-09-16） |
|---|---|
| Python 文件 | **43 个**（`src/` 29 + `scripts/` 14），其中 6 个是 `__init__.py` → 逻辑模块 **37 个** |
| 代码行数 | **14,957 行**（`src/` 8,462 + `scripts/` 6,495） |
| 文档 | **2,616 行**（README 190 + `docs/` 13 篇 2,426，含本文件） |
| 测试 | 21 个 `test_*.py`（另有 `conftest.py`），**178 passed**（2026-09-15 实测） |
| 数仓 | `data/warehouse/fqf.duckdb`，**14 张表**（raw 11 + mart 3） |
| 落盘标的 | **8 只**（跟踪池只登记了 6 只 — 见 §4） |
| 报告归档 | 2025Q4（4）/ 2026Q1（1）/ 2026Q2（17）/ xhs（29 文件 + 2 个发布包） |

**复核命令**（改完代码重跑，不要手工估 —— 本表初版的「模块数 / 代码行数 / 文档行数」
三格就是手抄错的：写成 44 个 / 14,771 行 / 2,351 行，实测为 43 / 14,957 / 2,616）：

```bash
find src scripts -name '*.py' -not -path '*__pycache__*' | wc -l             # 43
find src -name '*.py' -not -path '*__pycache__*' -exec cat {} + | wc -l      # 8462
find scripts -name '*.py' -not -path '*__pycache__*' -exec cat {} + | wc -l  # 6495
cat README.md docs/*.md | wc -l                                             # 2616
ls tests/test_*.py | wc -l                                                  # 21
```

---

## 1. 模块清单（按调用顺序，非目录顺序）

### 1.1 数据拉取层 `src/data/`

| 模块 | 行数 | 职责 | 关键入口 |
|---|---|---|---|
| `fetcher.py` | 867 | 封装 AKShare，多源回退（东财→腾讯→新浪），统一字段 | `fetch_all` / `fetch_all_hk` |
| `retry.py` | 95 | 指数退避 + 抖动（`_is_retryable` 判定可重试异常） | `retry` |
| `fields.py` | 322 | AKShare 原始字段名 → 标准 snake_case 映射表 | 数据字典 |
| `storage.py` | 46 | DataFrame → parquet 落盘 | `save_all` / `missing_tables` |
| `market_snapshot.py` | 175 | 每日行情快照（现价/PE/PB/市值/52周/评级），追加历史 + 覆盖最新 | `snapshot_all` |
| `listing_group.py` | 57 | A+H 同法人配对，跨上市地字段回填的凭据 | `sibling_code` |

### 1.2 清洗适配层 `src/data/`

| 模块 | 行数 | 职责 | 关键入口 |
|---|---|---|---|
| `cleaner.py` | 775 | 宽表构建、单位换算（元→亿）、补算派生指标、前复权 | `build_annual_financials` / `build_quarter_financials` / `build_valuation` |
| `adapter.py` | 1324 | **最大的数据模块**。宽表 → 模板渲染结构，串起校验/分析/报告 | `build_template_data` |
| `quality.py` | 404 | 抓取结果通用断言（跨表勾稽、比率边界、同比异常） | `validate_all` |
| `warehouse.py` | 249 | parquet → DuckDB，raw/mart 双层 schema | `build_warehouse` |

### 1.3 校验层 `src/validation/`

| 模块 | 行数 | 职责 |
|---|---|---|
| `cninfo.py` | 216 | 巨潮年报公告查询 + PDF 下载 |
| `pdf_parser.py` | 624 | 年报 PDF 表格抽取（主要会计数据 / 三表） |
| `validator.py` | 411 | 接口数据 vs PDF 金标准逐字段比对，容差 <0.1% |
| `whitelist.py` | 70 | 已验证字段白名单 |

### 1.4 分析与报告层

| 模块 | 行数 | 职责 |
|---|---|---|
| `analysis/fraud.py` | 228 | Beneish M-Score + 现金流背离 + 应收/存货异常 + 审计意见 |
| `review/lynch.py` | 84 | 林奇六类分类 → 该看的指标映射（纯逻辑，无网络） |
| `report/llm.py` | 643 | DeepSeek 叙事层（商业模式/逻辑/风险）+ 市场多空 + 操作建议 + 验证计划 |
| `report/mda_extract.py` | 943 | 定期报告原文抽取（分产品/渠道/地区收入）—— **第二大模块** |
| `report/quarterly_review.py` | 240 | 最新定期报告 + 单季事实 → 三段季度解读（按事实哈希缓存） |
| `report/perspectives.py` | 155 | 视角加载器（见 §5.2 的同名陷阱） |

### 1.5 脚本层 `scripts/`

| 脚本 | 行数 | 用途 |
|---|---|---|
| `build_valueline.py` | **2241** | 报告生成主入口（最大文件） |
| `build_xhs.py` | 1293 | 茅台版小红书 9 图 |
| `build_xhs_oil.py` | 986 | 海油版小红书 9 图（含口径自检） |
| `journal.py` | 382 | 投研日记（私有层，gitignore） |
| `backtest_band.py` | 324 | 腾讯中线波段策略回测 |
| `build_watchlist.py` | 417 | **跟踪池横向对比表**；读 `watchlist_store`、带行缓存（只重算数据变动过的标的）、移动端可横滑 |
| `daily_refresh.py` | 186 | **每日行情刷新（launchd 入口）**；配套三件套：`install_launchd.command`（安装器）+ `daily_refresh.sh`（适配层）+ `com.fqf.daily-refresh.plist`（声明式配置） |
| `check_refresh.py` | 298 | **数据更新证据链自检**：launchd 状态 / 闸门 / 日志 / 文件 mtime / 按日归档价格序列 / 报告是否重刷 —— 六层由软到硬 |
| `inspect_raw.py` | 170 | 数据结构查看（Code Review 辅助） |
| `build_web_index.py` | 300 | 手机网页版首页；卡片含**现价 / 涨跌幅（红涨绿跌）/ 数据日期**，顶部显示「数据更新于」 |
| `backup.py` | 141 | 日记 + 工作记忆备份 |
| `fetch_stock.py` | 90 | **数据拉取 CLI 入口** |
| `export.py` | 68 | Playwright 导图（PNG @2x / PDF） |
| `_audit_snapshot.py` | 110 | 6 标的易错字段核对摘要 |
| `_sample_data.py` | 67 | 无数据时的版式降级样例 |

---

## 2. 端到端数据链（实测调用路径）

```
【A】拉取        python scripts/fetch_stock.py 600938 [--incremental]
                  └→ src.data.fetcher.fetch_all / fetch_all_hk
                      └→ src.data.retry.retry（指数退避）
                          └→ src.data.storage.save_all
                              └→ data/raw/{code}/*.parquet     ← 落盘

【B】构建        python scripts/build_valueline.py 600938 [--daily] [--refresh-narrative]
                  ├→ build_valueline._reconcile(code)          ← 跳过条件：--daily
                  │    └→ src.validation.cninfo 下载年报 PDF
                  │        └→ src.validation.pdf_parser 抽表
                  │            └→ src.validation.validator 比对（容差 <0.1%）
                  ├→ build_valueline._load_real_data(code)
                  │    └→ src.data.adapter.build_template_data
                  │        ├→ src.data.cleaner.*                （宽表 / 估值 / 分业务）
                  │        ├→ src.data.quality.validate_all     （勾稽体检 → sanity）
                  │        ├→ src.analysis.fraud.fraud_check    （M-Score 等）
                  │        ├→ src.review.lynch.classify         （六类分类）
                  │        ├→ src.report.mda_extract.extract    （经营结构，缓存）
                  │        ├→ src.report.llm.generate_narrative （叙事层）  ← 跳过：--daily
                  │        └→ src.report.quarterly_review.get_or_generate  ← 跳过：--daily
                  ├→ 渲染（数十个 build_xxx() 从全局变量读数据）
                  └→ templates/valueline.html  +  reports/{期}/{code}.html

【C】导出        python scripts/export.py
                  └→ Playwright → reports/{期}/{code}.png / .pdf

【D】数仓        python -c "...warehouse.build_warehouse()"
                  └→ data/warehouse/fqf.duckdb（raw 11 表 + mart 3 表）

【E】服务        uvicorn src.api.main:app
                  └→ src.api.query → DuckDB mart 层（7 端点，只读）
```

### 缓存机制（三层，都靠哈希）

| 层 | 缓存位置 | 键 | 失效方式 |
|---|---|---|---|
| 抽取层 | `data/cache/` | facts-hash + mdd-hash | 改抽取逻辑须递增 `_EXTRACT_VERSION` |
| 叙事层 | `data/cache/`（LLM） | facts-hash（**含估值与市值**） | 行情一变即失效重新生成 LLM |
| 季度解读 | `data/cache/quarterly_review/` | facts-hash | `--refresh-narrative` 强制刷新 |

> ⚠️ 叙事层哈希含行情 → **每次刷新股价都会重新调用 LLM**。这是设计行为，
> 但也意味着「只想更新股价」时应该用 `--daily`（跳过 LLM），否则会白烧 token。

---

## 3. 实况与 architecture.md 的偏差

| # | architecture.md 说 | 实况 | 判定 |
|---|---|---|---|
| 1 | Roadmap P3「剩：季度更新引擎」 | `src/report/quarterly_review.py`（240 行）**已实现并接入**，在 `build_valueline.py:2159` 调用，带哈希缓存 | ❌ **已过期**，实际已完成 |
| 2 | §5「估值分位标注『近 10 年』」 | A 股次新股（如 600938 上市 4.4 年）实际序列不足 10 年，报告措辞与序列长度不符 | 🔴 已知待修 |
| 3 | §4.4「DuckDB raw/mart 双层」 | 实测 raw 11 + mart 4 = **15 表**（2026-09-17 新增 `mart.market_index` 8368 行）✅ | ✅ 一致 |
| 4 | §8「FastAPI 7 端点」 | 实测 7 个（`root`/`list_stocks`/`get_stock`/`get_quarters`/`get_segments`/`get_metric`/`compare`）✅ | ✅ 一致 |
| 5 | §6.5「`src/report/perspectives/{id}.json`」 | JSON 确实在 `perspectives/` 目录（4 个）；但**同目录还有一个同名 `perspectives.py`** | ✅ 能用，但见 §5.2 |

---

## 4. ✅ 标的清单已收敛为单一真源（2026-09-17 修复）

**修复前**（记录在此，因为它是一类必犯的错）：跟踪池被硬编码在**三处**，必然漂移。

| 来源 | 标的 | 数量 | 差异 |
|---|---|---|---|
| `watchlist/watchlist.json` | 神华 / 茅台 / 格力 / 交行 / 腾讯 / 泡泡玛特 | **6** | 基准（更新于 2026-09-03） |
| `scripts/build_web_index.py` 的 `STOCK_META` | 神华 / 茅台 / 格力 / 交行 / 招行 / 兴业 / 成都银行 / 泡泡玛特 | **8** | ❌ 缺腾讯；多三家银行 |
| `scripts/build_watchlist.py` 的 `STOCK_META` | 与 json 同内容 | **6** | ❌ 代码重复一份，改一处必漏另一处 |
| 实际已有报告 | 上列 + 海油 A+H（600938 / 00883）+ 伊利 600887 + 300061 | **11** | ❌ 5 只有报告却不在任何清单里 |

**修复**：新增 `src/data/watchlist_store.py` 作为**唯一读写入口**
（`codes()` / `stocks()` / `add()` / `prune()`），
`build_web_index.py`、`build_watchlist.py`、`daily_refresh.py`、`web/server.py`
全部改读它 —— 三处 `STOCK_META` 已全部删除。

**入池规则**（2026-09-17 定）：**生成过报告即入池**，不设硬上限。
`rules.max_size = 8` 降级为页面提示（「跟踪池 11 只 · 已超名义上限 8 只」），不阻断写入。
落地点两处：`web/server.py`（网页按需生成后 `add()`）与 `daily_refresh.py`（读同一清单）。
海油 A+H、伊利、300061 已按此规则补入，当前 **11 只**。

⚠️ 新增/移除标的时**只需改 `watchlist/watchlist.json`**；但 `.gitignore` 里
`reports/**/*.html` 的白名单需同步增删一行（报告默认不入库，只留跟踪池的）。

**后果**：

- **海油（600938 + 00883）做了完整报告 + PDF + PNG + 小红书两轮内容，却从未进跟踪池** →
  `web/index.html` 与 `web/watchlist.html` 里搜不到任何海油痕迹（实测命中 0 次）
- `STOCK_META` 里的招行/兴业/成都银行**无对应报告文件**，是历史残留
- 三处清单各自手维护，没有单一数据源

**建议**：`watchlist.json` 作为唯一数据源，`build_web_index.STOCK_META` 改为读它
（名称/行业/配色可保留在 watchlist 里扩字段）。

### 报告与导图完整度不一致

`reports/2026Q2/` 有 7 只标的的 HTML，但导图只覆盖 4 只：

| 标的 | HTML | PNG | PDF |
|---|---|---|---|
| 神华 601088 | ✅ | ✅（+2 张 zoom） | — |
| 茅台 600519 | ✅ | ✅ | ✅ |
| 海油 A 600938 | ✅ | ✅ | ✅ |
| 海油 H 00883 | ✅ | ✅ | ✅ |
| 格力 000651 | ✅ | ❌ | — |
| 腾讯 00700 | ✅ | ❌ | — |
| 交行 601328 | ✅ | ❌ | — |

泡泡玛特 09992 在跟踪池与 `data/raw/` 里，但 `reports/2026Q2/` 无其报告。

---

## 5. 架构层面的三个观察

### 5.1 全局变量当数据总线

`build_valueline.build()` 的第一行声明了 **21 个 global**：

```python
global YEARS, FINANCIALS, QUARTER_LABELS, QUARTERLY, SEGMENT_LABELS, SEGMENTS,
       VALUATION, GRAHAM, RATING, FRAUD, COMPETITION, BUSINESS_MAP,
       CURRENT_POSITION, ANNUAL_RATES, PIE_DATA, COMPANY_NAME, COMPANY_CODE,
       NARRATIVE, RECONCILE_LOG, SANITY, CURRENCY_NOTE, VAL_CURRENCY_HINT,
       QUARTER_REVIEW, OPERATING, BACKFILL_SRC
```

随后几十个 `build_xxx()` 函数从全局读数据、拼 HTML 字符串。

**代价**：不可并行构建两个标的、无法单测渲染函数、隐式依赖难追踪。
**收益**：省掉了参数穿透，脚本形态下更短。

判断：**单人脚本形态下这是可接受的权衡**，但如果要扩到全市场批量构建，
这里会是第一个瓶颈（`build()` 需要改成接受一个 context 对象）。

### 5.2 同名文件与目录共存（脆弱点）

```
src/report/perspectives.py     ← 155 行，加载器
src/report/perspectives/       ← 4 个 JSON（graham/lynch/buffett/fisher）
```

现在**能正常工作**：Python 导入时模块（`.py`）优先于命名空间包，
`perspectives.py` 里用 `Path(__file__).resolve().parent / "perspectives"` 定位 JSON 目录，
实测 4 个视角全部加载成功。

**但这是脆弱的**：只要有人给 `perspectives/` 加一个 `__init__.py`，
`src.report.perspectives` 立刻变成包导入，`load_perspective` 就找不到了。
建议把 JSON 目录改名（如 `perspective_defs/`）消除歧义。

### 5.3 三层可降级导入

`build_valueline.py` 顶部有三个 `try/except`，各带一个开关：

| 开关 | 导入 | 失败后果 |
|---|---|---|
| `_HAS_DATA` | `src.data.adapter` | 降级为 `_sample_data.py` 示例版式 |
| `_HAS_LLM` | `src.report.llm` | 叙事段留占位 |
| `_HAS_QREVIEW` | `src.report.quarterly_review` | 季度解读留占位 |

**好处**：无数据 / 无 API key / 无网络都能出图，版式开发不被依赖阻塞。
**代价**：失败被静默吞掉，容易「以为渲染了真实数据其实是示例数据」。

⚠️ 建议：降级时应往 HTML 里写一个显眼标识（现在只在 stdout 打印 `数据来源: 示例数据`，
产物上看不出来）。

---

## 6. 待办池（截至 2026-09-17）

### 数据正确性（静默错数，优先级最高）

| # | 问题 | 影响 |
|---|---|---|
| 1 | `net_cash` 用简口径有息负债（漏应付债券等 678 亿）→ 海油虚高 47.8% | 🔴 已发布内容含此错数 |
| 2 | 港股 kline 缺失 → 52 周区间用兜底值，静默错约 12pp | 🔴 P0 |
| 3 | 报告叙事层写「PE 近 10 年分位」，A 股次新股序列实际只有 4.4 年 | 🔴 |
| 4 | Beneish M-Score 缺因子被 `_safe(x, neutral=1.0)` 静默当 1.0（港股 5/8） | 🔴 |
| 5 | 港股 `gross_margin_pct` 77.92% 与 A 股口径 51.5% 差 26pp（潜伏陷阱） | 🟡 |

### 一致性与工程

| # | 问题 | 影响 |
|---|---|---|
| 6 | 导图覆盖不全（7 只报告只有 4 只有 PNG） | 🟡 |
| 7 | `perspectives` 同名共存（§5.2） | 🟢 潜在 |
| 8 | 降级模式无产物标识（§5.3） | 🟢 潜在 |

> ✅ **已销账（2026-09-17）**：原条目 6「三处标的清单漂移（§4）」——
> 已收敛到 `src/data/watchlist_store.py` 单一真源，见 §4。

> ✅ **已销账（2026-09-17）**：原条目 8「launchd 任务未安装」—— 已安装并跑通首次真实执行
> （`launchctl print gui/501/com.fqf.daily-refresh` → `runs = 1`）。安装入口
> `scripts/install_launchd.command`；调度设计见 `data-map.md` §3。

---

## 7. 与 architecture.md 的分工

- **architecture.md** = 设计意图、产品定位、方法论、Roadmap、技术栈选型理由（对外讲架构用）
- **本文件** = 代码实况、实际调用链、模块行数、清单漂移、待办（对内维护用）

两者冲突时**以本文件为准**（architecture.md 是 2026-09-02 的快照）。
