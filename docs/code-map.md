# 代码实况地图

> 对 `architecture.md`（v2.0，2026-09-02 定稿）的**实况核对**，不是替代品。
> architecture.md 写「设计意图」，本文件写「代码现在到底是什么样」。
> 生成日期：2026-09-16 · **全表复核：2026-09-18** · 核对方式：AST 扫描 + 实际导入 + 数仓直连查询

## 0. 规模总览

| 项 | 实测（2026-09-21） | 09-16 快照 |
|---|---|---|
| Python 文件 | **54 个**（`src/` 33 + `scripts/` 21），其中 6 个 `__init__.py` → 逻辑模块 **48 个** | 43（29+14）→ 37 |
| 代码行数 | **19,056 行**（`src/` 9,789 + `scripts/` 9,267） | 14,957（8,462+6,495） |
| 文档 | **3,135 行**（README 242 + `docs/` 14 篇 2,893，含本文件） | 2,616（190+2,426，13 篇） |
| 测试 | **30 个** `test_*.py`（另有 `conftest.py`），**378 passed**（2026-09-21 实测） | 21 个 / 178 passed |
| 数仓 | `data/warehouse/fqf.duckdb`，**15 张表**（raw 11 + mart 4） | 14 张（raw 11 + mart 3） |
| 落盘标的 | **14 只**（在池 12 + 已移出 2） | 8 只（池 6） |
| 报告归档 | `reports/` **整目录不入库**（2026-09-18 定）。本地快照 **81 个文件**：html **17** / png 54 / pdf 3 / md 3 / txt 2 / 无后缀 2 | 口径不同，见下注 |
| └ 其中 html | 2025Q4 **1** / 2026Q1 **1** / 2026Q2 **13** / xhs **2**（单只 deck + AH 发布包） | — |

> ⚠️ **「报告归档」这两行不是同一口径**：09-16 那一版数的是**目录内文件总数**（含 PNG/PDF），
> 现在拆成「全目录文件数」+「其中 html」。html 才是「一份报告」的单位
> —— 与 `src/report/artifacts.py` 的判据一致（它还要排除 `reports/xhs/` 下的 deck）。
>
> ⚠️ **这一行现在只是本地快照，别当指标用**：`reports/` 已整目录出库（见 `.gitignore` 内注释），
> 报告是「按需生成」的产物，数字随时会变。要可比的指标看上面「落盘标的」那一行。

**复核命令**（改完代码重跑，不要手工估 —— 本表初版的「模块数 / 代码行数 / 文档行数」
三格就是手抄错的：写成 44 个 / 14,771 行 / 2,351 行，实测为 43 / 14,957 / 2,616）：

```bash
find src scripts -name '*.py' -not -path '*__pycache__*' | wc -l             # 54
find src -name '*.py' -not -path '*__pycache__*' -exec cat {} + | wc -l      # 9789
find scripts -name '*.py' -not -path '*__pycache__*' -exec cat {} + | wc -l  # 9267
cat README.md docs/*.md | wc -l                                             # 3135 ← 含本文件，改完本文件要重跑
ls tests/test_*.py | wc -l                                                  # 30
ls -d data/raw/*/ | wc -l                                                   # 14
find reports -type f | wc -l                                                # 81（仅本地快照，不入库）
```

---

## 1. 模块清单（按调用顺序，非目录顺序）

### 1.1 数据拉取层 `src/data/`

| 模块 | 行数 | 职责 | 关键入口 |
|---|---|---|---|
| `fetcher.py` | 1014 | 封装 AKShare，多源回退（东财→腾讯→新浪），统一字段 | `fetch_all` / `fetch_all_hk` |
| `market_index.py` | 210 | **L0 全市场索引**（A 8368 只 / 港 2803 只）—— 代码/名称/行业/交易所 | `load_index` / `a_share_exchange` |
| `market_snapshot.py` | 185 | 每日行情快照（现价/PE/PB/市值/52周/评级），追加历史 + 覆盖最新 | `snapshot_all` |
| `watchlist_store.py` | 477 | **跟踪池唯一读写入口**（`watchlist.json` 单一真源）；软删/恢复、Lynch 归类归一化 | `codes` / `stocks` / `add` / `remove` / `upsert_from_report` |
| `fields.py` | 322 | AKShare 原始字段名 → 标准 snake_case 映射表 | 数据字典 |
| `retry.py` | 95 | 指数退避 + 抖动（`_is_retryable` 判定可重试异常） | `retry` |
| `listing_group.py` | 57 | A+H 同法人配对，跨上市地字段回填的凭据 | `sibling_code` |
| `storage.py` | 46 | DataFrame → parquet 落盘 | `save_all` / `missing_tables` |

### 1.2 清洗适配层 `src/data/`

| 模块 | 行数 | 职责 | 关键入口 |
|---|---|---|---|
| `cleaner.py` | 775 | 宽表构建、单位换算（元→亿）、补算派生指标、前复权 | `build_annual_financials` / `build_quarter_financials` / `build_valuation` |
| `adapter.py` | 1328 | **最大的数据模块**。宽表 → 模板渲染结构，串起校验/分析/报告；也是 reconcile 覆盖记录的**应用端**（`_apply_corrections`，阈值同源 `src/plausibility.py`） | `build_template_data` |
| `quality.py` | 404 | 抓取结果通用断言（跨表勾稽、比率边界、同比异常） | `validate_all` |
| `warehouse.py` | 249 | parquet → DuckDB，raw/mart 双层 schema | `build_warehouse` |

### 1.3 校验层 `src/validation/`

| 模块 | 行数 | 职责 |
|---|---|---|
| `cninfo.py` | 216 | 巨潮年报公告查询 + PDF 下载 |
| `pdf_parser.py` | 635 | 年报 PDF 表格抽取（主要会计数据 / 三表）—— **单位识别是它的高危区**，见 `plausibility.py` |
| `validator.py` | 418 | 接口数据 vs PDF 金标准逐字段比对，容差 <0.1% |
| `whitelist.py` | 70 | 已验证字段白名单 |

**跨层共享**

| 模块 | 行数 | 职责 |
|---|---|---|
| `src/plausibility.py` | 49 | PDF 值 vs 接口值的可信度判据（100 倍以上判为解析错，不覆盖）。生产端 `validator` 与消费端 `data/adapter` **同源**；刻意放顶层、零依赖，避免把 pymupdf 拖进报告链路 |

### 1.4 分析与报告层

| 模块 | 行数 | 职责 |
|---|---|---|
| `report/mda_extract.py` | 943 | 定期报告原文抽取（分产品/渠道/地区收入）—— **第二大模块** |
| `report/llm.py` | 645 | DeepSeek 叙事层（商业模式/逻辑/风险）+ 市场多空 + 操作建议 + 验证计划 |
| `report/swing.py` | 267 | **「主要变动指标」候选榜**：三大报表变动最大的科目（分组配额 + 三重入榜判据），供季报解读做异动归因 |
| `report/quarterly_review.py` | 271 | 最新定期报告 + 单季事实 → 季度解读四段 + watch（按事实哈希缓存，`cache_only` 只读缓存；**输出结构有 `_SCHEMA` 版本**） |
| `analysis/fraud.py` | 228 | Beneish M-Score + 现金流背离 + 应收/存货异常 + 审计意见 |
| `review/lynch.py` | 203 | 林奇六类分类 → 该看的指标映射；**`CANONICAL_TYPES` 六个规范值 + `normalize()` 同义写法归一**（全仓唯一口径） |
| `report/artifacts.py` | 159 | **「什么算一份报告」的唯一定义**（报告期目录 + 取最新期）；含占位符体检的 `PLACEHOLDERS` |
| `report/perspectives.py` | 155 | 视角加载器（见 §5.2 的同名陷阱） |

### 1.5 脚本层 `scripts/`

按用途分组（行数 = 2026-09-18 实测）。

**报告主链**

| 脚本 | 行数 | 用途 |
|---|---|---|
| `build_valueline.py` | **2396** | 报告生成主入口（最大文件）；`--daily` 走缓存复用、非日更模式才调 LLM |
| `build_web_index.py` | 658 | 手机网页版首页：**搜索框**（防抖搜索 + 就地生成 + 进度轮询）→ **已生成报告列表** → **「横向对比」按钮** |
| `build_watchlist.py` | 440 | **跟踪池横向对比表**；带行缓存（指纹 = raw mtime + 5 个展示字段），移动端可横滑、首列冻结 |
| `check_placeholders.py` | 58 | **占位符体检**（退出码 1 = 有 LLM 内容退化为占位符）；日更收尾也会调它 |
| `export.py` | 75 | Playwright 导图（PNG @2x / PDF） |

**数据与调度**

| 脚本 | 行数 | 用途 |
|---|---|---|
| `update_financials.py` | 223 | **L2 财报按需补抓**（`--scope missing\|watchlist\|all`） |
| `update_spot_all.py` | 159 | **L1 全市场行情日更**（腾讯批量 50 只/请求，8368 只约 26 秒） |
| `daily_refresh.py` | 264 | **每日行情刷新（launchd 入口）**；配套 `install_launchd.command` + `daily_refresh.sh` + `com.fqf.daily-refresh.plist` |
| `check_refresh.py` | 308 | **数据更新证据链自检**：launchd 状态 / 闸门 / 日志 / 文件 mtime / 按日归档价格序列 / 报告是否重刷 —— 六层由软到硬 |
| `build_market_index.py` | 78 | **L0 全市场索引重建**（每周一次） ⚠️ **未挂调度，目前靠手动** |
| `fetch_stock.py` | 90 | 数据拉取 CLI 入口 |
| `backup.py` | 141 | 日记 + 工作记忆备份 |

**跟踪池与内容**

| 脚本 | 行数 | 用途 |
|---|---|---|
| `watchlist.py` | 345 | 跟踪池 CLI（`list`/`add`/`remove`/`restore`/`sync-lynch`），动作后重建派生页面 |
| `build_xhs.py` | 1313 | 茅台版小红书 9 图；`_lynch_label()` 是林奇分类的展示口径（与报告徽章同源） |
| `build_xhs_oil.py` | 987 | 海油版小红书 9 图（含口径自检） |
| `journal.py` | 382 | 投研日记（私有层，gitignore） |

**工具与一次性**

| 脚本 | 行数 | 用途 |
|---|---|---|
| `gen_raw_schema.py` | 596 | raw 层 schema 自动生成（`sql/schema_raw.sql`，勿手改） |
| `backtest_band.py` | 324 | 腾讯中线波段策略回测 |
| `inspect_raw.py` | 170 | 数据结构查看（Code Review 辅助） |
| `_audit_snapshot.py` | 110 | 易错字段核对摘要 |
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
                  │        ├→ src.data.cleaner.*                 （宽表 / 估值 / 分业务）
                  │        ├→ src.data.quality.validate_all      （勾稽体检 → sanity）
                  │        ├→ src.analysis.fraud.fraud_check     （M-Score 等）
                  │        ├→ src.report.mda_extract.extract     （经营结构，缓存）
                  │        ├→ src.report.llm.generate_narrative  （叙事层：含林奇分类判定）
                  │        │       daily=True → **复用缓存**（ignore_hash=True，不调 LLM）
                  │        └→ src.report.quarterly_review.get_or_generate
                  │                daily=True → **只读缓存**（cache_only=True，不联网）
                  ├→ 渲染（数十个 build_xxx() 从全局变量读数据）
                  ├→ templates/valueline.html  +  reports/{期}/{code}.html
                  └→ build_valueline._sync_watchlist()          ← 只在**真实数据**分支调
                       └→ src.data.watchlist_store.upsert_from_report()
                            （跟踪池回写：行业 + Lynch **规范值** + 注解）

【C】导出        python scripts/export.py
                  └→ Playwright → reports/{期}/{code}.png / .pdf

【D】数仓        python -c "...warehouse.build_warehouse()"
                  └→ data/warehouse/fqf.duckdb（raw 11 表 + mart 4 表）

【E】服务        uvicorn src.api.main:app
                  └→ src.api.query → DuckDB mart 层（7 端点，只读）
```

> ⚠️ **修正一处凭空的依赖**（2026-09-18）：本图原先把 `src.review.lynch.classify`
> 画在 `adapter.build_template_data` 底下，但实测 `adapter.py` / `cleaner.py` **一个字都没提 lynch**
> —— 报告里的林奇分类来自 **LLM 判定**（存在叙事缓存 `data/cache/narrative/{code}.json`），
> 不是本地规则算的。`src/review/lynch.py` 的真实消费方是四处：
> `scripts/build_valueline.py`（归一化 + 徽章）、`src/data/watchlist_store.py`（落库前归一化）、
> `scripts/build_xhs.py`（卡片同一口径）、`scripts/journal.py`（`classify`/`metrics_for`）。

### 缓存机制（三层，都靠哈希）

| 层 | 缓存位置 | 键 | 失效方式 |
|---|---|---|---|
| 抽取层 | `data/cache/` | facts-hash + mdd-hash | 改抽取逻辑须递增 `_EXTRACT_VERSION` |
| 叙事层 | `data/cache/`（LLM） | facts-hash（**含估值与市值**） | 行情一变即失效重新生成 LLM |
| 季度解读 | `data/cache/quarterly_review/` | facts-hash | `--refresh-narrative` 强制刷新 |

> ⚠️ 叙事层哈希含行情 → **每次刷新股价都会让哈希失效**。所以日更 / 只刷估值时必须走
> `--daily`，否则会白烧 token。
> 🔴 但 `--daily` **不能「跳过 LLM」了事**：跳过 = 叙事留空，而 `build()` 会无条件把整份
> HTML 覆盖写回归档 —— 实测 2026-09-17 那次日更把 11 只报告的「投资逻辑 / 风险提示」
> 全洗成了「待 LLM 生成」，零报错。现在的语义是 **`--daily` 复用缓存**
> （叙事 `ignore_hash=True`、季报解读 `cache_only=True`），细节见 `docs/report-chains.md` §8。

---

## 3. 实况与 architecture.md 的偏差

| # | architecture.md 说 | 实况 | 判定 |
|---|---|---|---|
| 1 | Roadmap P3「剩：季度更新引擎」 | `src/report/quarterly_review.py`（254 行）**已实现并接入** —— 调用点是 `build_valueline.build()` 里的 `get_quarter_review(...)`，带事实哈希缓存 + `cache_only` 复用模式 | ❌ **已过期**，实际已完成 |
| 2 | §5「估值分位标注『近 10 年』」 | A 股次新股（如 600938 上市 4.4 年）实际序列不足 10 年，报告措辞与序列长度不符 | 🔴 已知待修 |
| 3 | §4.4「DuckDB raw/mart 双层」 | 实测 raw 11 + mart 4 = **15 表**（2026-09-17 新增 `mart.market_index` 8368 行）✅ | ✅ 一致 |
| 4 | §8「FastAPI 7 端点」 | 实测 7 个（`root`/`list_stocks`/`get_stock`/`get_quarters`/`get_segments`/`get_metric`/`compare`，全在 `src/api/main.py`）✅ | ✅ 一致 |
| 5 | §6.5「`src/report/perspectives/{id}.json`」 | JSON 确实在 `perspectives/` 目录（4 个）；但**同目录还有一个同名 `perspectives.py`** | ✅ 能用，但见 §5.2 |

> ⚠️ **本文件不写行号**。原先第 1 行引用写的是 ``build_valueline.py:2159``，
> 一个无关的改动就让它漂到 2296 —— 行号是**必然过期**的引用形式。
> 要指位置就写函数名（`build_valueline.build()` 里的 `get_quarter_review(...)`），
> 定位交给编辑器的「转到定义」。

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

**入池规则**（2026-09-17 定，2026-09-18 收紧）：**生成过报告即入池，不设上限**。
`rules.max_size = 8` 那一版「仅页面提示」已**删除**（不阻断写入却长期显示「已超名义上限」，
只会训练人忽略告警）；`max_size()` 函数、`rules.max_size` 字段、对比表表头告警与
`/api/watchlist` 响应字段一并去掉，`tests/test_watchlist_store.py::test_no_max_size_anywhere`
防它被加回来。
落地点两处：`web/server.py`（网页按需生成后 `upsert_from_report()`）与
`build_valueline._sync_watchlist()`（报告口径回写，覆盖全部调用方）。

⚠️ 新增/移除标的**只需改 `watchlist/watchlist.json`**（或跑 `scripts/watchlist.py`），
不再需要同步任何清单 —— `reports/` 已于 2026-09-18 **整目录出库**，仓库只留代码与报告模板，
原先那套 `reports/**/*.html` + `!reports/**/<code>.html` 白名单机制已彻底删除。

> 运行时链路（旧标的更新 / 新标的生成、launchd、失败面）见 **`docs/report-chains.md`**。

**后果**：

- **海油（600938 + 00883）做了完整报告 + PDF + PNG + 小红书两轮内容，却从未进跟踪池** →
  `web/index.html` 与 `web/watchlist.html` 里搜不到任何海油痕迹（实测命中 0 次）
- `STOCK_META` 里的招行/兴业/成都银行**无对应报告文件**，是历史残留
- 三处清单各自手维护，没有单一数据源

**建议（✅ 已落地）**：`watchlist.json` 作为唯一数据源，`build_web_index.STOCK_META` 改为读它
（名称/行业/配色可保留在 watchlist 里扩字段）。—— 2026-09-17 已完成，三处 `STOCK_META` 全删。

### 报告与导图完整度不一致（2026-09-18 复核）

`reports/2026Q2/` 有 **13 只**标的的 HTML，但导图只覆盖 **5 只**：

| 标的 | HTML | PNG | PDF |
|---|---|---|---|
| 神华 601088 | ✅ | ✅（+2 张 zoom） | — |
| 茅台 600519 | ✅ | ✅ | ✅ |
| 海油 A 600938 | ✅ | ✅ | ✅ |
| 海油 H 00883 | ✅ | ✅ | ✅ |
| 腾讯 00700 | ✅ | ✅ | — |
| 美的 A 000333 / 美的 H 00300 / 格力 000651 / 交行 601328 | ✅ | ❌ | — |
| 招行 600036 / 伊利 600887 / 长江电力 600900 / 旗天 300061 | ✅ | ❌ | — |

另有 **2025Q4 的 09992 泡泡玛特**（1 html + 3 PNG）—— 它不在 `2026Q2`，因为最新一期报告停在中报
（`artifacts.scan()` 取「最新报告期目录」时会取到它，属正常）。

导图是**手工触发的**（`scripts/export.py`），不在任何调度里 ——
所以「13 只报告只有 5 只有导图」不是 bug，是「没跑就没图」。
但它意味着：**要对外发材料时得先确认目标标的有现成 PNG**，否则得现跑一次。

---

## 5. 架构层面的三个观察

### 5.1 全局变量当数据总线

`build_valueline.build()` 的第一行声明了 **25 个 global**（AST 实测；下面这串就是全部）：

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

## 6. 待办池（截至 2026-09-18）

### 数据正确性（静默错数，优先级最高）

| # | 问题 | 影响 |
|---|---|---|
| 1 | `net_cash` 用简口径有息负债（漏应付债券等 678 亿）→ 海油虚高 47.8% | 🔴 已发布内容含此错数 |
| 2 | 港股 kline 缺失 → 52 周区间用兜底值，静默错约 12pp | 🔴 P0 |
| 3 | 报告叙事层写「PE 近 10 年分位」，A 股次新股序列实际只有 4.4 年 | 🔴 |
| 4 | Beneish M-Score 缺因子被 `_safe(x, neutral=1.0)` 静默当 1.0（港股 5/8） | 🔴 |
| 5 | 港股 `gross_margin_pct` 77.92% 与 A 股口径 51.5% 差 26pp（潜伏陷阱） | 🟡 |
| 6 | **`listing_group` 的声明与实现有缺口**：模块 docstring 说可回填「审计意见、主营构成、公司简介、有息负债/净现金、**行业排名**」，`backfill_company_fields()` 实际只回填 `net_cash` + `audit_opinion` 两项 | 🟡 港股报告缺排名（美的 H 00300 实测） |
| 12 | **000333 报告已被 1000 倍错误覆盖**：利润构成饼图显示「销售费用 0.4 亿 / 管理费用 0.2 亿」（真值 428.9 / 160.9 亿）。**代码三层根因已修（见下方销账）**，但 `reports/2026Q2/000333.html` 与落盘的 `data/validation/000333_2025_reconcile.json` 还是污染版本，需重跑 reconcile + 重渲 | 🔴 产物错数 |

### 一致性与工程

| # | 问题 | 影响 |
|---|---|---|
| 7 | 导图覆盖不全（2026Q2 的 13 只报告只有 5 只有 PNG）—— 导图是手工触发的，不属 bug | 🟡 |
| 8 | `perspectives` 同名共存（§5.2） | 🟢 潜在 |
| 9 | 降级模式无产物标识（§5.3） | 🟢 潜在 |
| 10 | **全市场两层没挂调度**：`launchd` 只挂了 `com.fqf.daily-refresh` 一个作业，L0 索引（`build_market_index.py`）与全市场行情（`update_spot_all.py`）**靠手动** | 🟡 地基靠手动 |
| 11 | `data/raw` 无版本留痕：上游重述会静默改写旧值且不可回滚（`scripts/backup.py` 以「可再生」为由不备份它，该理由在重述场景下不成立） | 🟡 |

> ✅ **已销账（2026-09-17）**：原条目「三处标的清单漂移（§4）」——
> 已收敛到 `src/data/watchlist_store.py` 单一真源，见 §4。

> ✅ **已销账（2026-09-17）**：原条目「launchd 任务未安装」—— 已安装并跑通首次真实执行
> （`launchctl print gui/501/com.fqf.daily-refresh` → `runs = 1`）。安装入口
> `scripts/install_launchd.command`；调度设计见 `data-map.md` §3。

> ✅ **已销账（2026-09-18）**：原条目「Lynch 命名漂移（LLM 输出不稳定）」——
> 分类值已冻结为 `src/review/lynch.py` 的 `CANONICAL_TYPES` 六个规范值，注解走独立字段
> `lynch_note`，prompt 改成逐字照抄、存量缓存由 `normalize()` 在渲染时兜住。
> 回归测试 `tests/test_lynch_canonical.py`（52 条）。
>
> ✅ **已销账（2026-09-18，代码部分）**：美的 000333 的 1000 倍覆盖错 —— 三层根因各自独立、
> 任修一层都能挡住，已全修：
> ① `pdf_parser._UNIT_MULTIPLIER` **漏「千元」键**（美的利润表声明「单位：千元」），且
> `_UNIT_PATTERNS` 的 `[^，。\n]*?` 惰性前缀会逐字符右移、在「千」处不匹配后右移一格命中裸
> 「元」→ **"匹配成功"但单位错**（比匹配失败更隐蔽）；
> ② `validator` 的防呆门槛写成 `IMPLAUSIBLE_DIFF_PCT = 100_000`，而 1000 倍对应的百分比是
> **99,900** —— 只差一点点，恰好整类漏掉；
> ③ `adapter` 消费端用的是裸魔数 `1e6`（10000 倍），比生产端还松。
> 现 ①`千元`+正则收紧（禁跨量词字）；②③ 合并为 `src/plausibility.py` 的**对称倍率判据**（100 倍），
> 生产端与消费端同源。dry 重跑：000333 覆盖记录 **4 条 → 0 条**，601088 的 18 条真实重述差异
> **照常保留**（无回归）。回归测试 `tests/test_plausibility.py` + `test_pdf_parser.py` 千元用例。
> **代码已修，但已生成的产物仍是污染版 → 见待办池 #12。**
>
> ⚠️ **顺带销掉一条错误待办**：原先记的「对比表列式宽表在池子变长后会横向溢出」**是错的** ——
> 实测表结构是「每只一行 × 13 个指标列」，加标的只增行不增列，表格实宽恒定 1080px
> （`min-width` 兜底 1040px，窄屏可横滑、首列已冻结）。**照那条待办去改渲染层会白干。**
> 真正随池子线性恶化的是**页面垂直长度**（每行约 93px）与**重建耗时**（约 14 秒/只）。

### 已定的取舍（记录在此，避免被当成遗留问题反复提出）

| 项 | 决定 | 理由 |
|---|---|---|
| `web/{index,watchlist,query}.html` + `templates/valueline.html` **留在版本库** | **不移除**（2026-09-18 定） | 日更每天都会改前三个 → 工作区**每天必脏**，但出库后 GitHub 上就看不到首页与模板了；她要能看，所以保留这份不方便 |
| `web/query.html` 不删 | **保留文件 + 两个兼容路由**（2026-09-18 定） | 首页已无任何入口指向它（两处搜索入口 = 两份同功能 JS = 必然漂移），留着只为**防旧书签 404**。⚠️ 别按「已删除」处理 —— `server.py` 的模块 docstring 曾这么写，是错的 |

---

## 7. 与 architecture.md 的分工

- **architecture.md** = 设计意图、产品定位、方法论、Roadmap、技术栈选型理由（对外讲架构用）
- **本文件** = 代码实况、实际调用链、模块行数、清单漂移、待办（对内维护用）

两者冲突时**以本文件为准**（architecture.md 是 2026-09-02 的快照）。
