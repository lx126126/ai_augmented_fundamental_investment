# 报告链路全图 · 旧标的更新 / 新标的生成

> 2026-09-18 实测梳理（潇姐提问：*"帮我生成旧标的报告更新和新标的报告生成的所有链路（包括数据和工作流）"*）。
> 本文只写**运行时链路**（谁触发 → 读什么 → 写什么 → 什么时候失败）；
> 模块职责见 `docs/code-map.md`，数据字段口径见 `docs/data-map.md`，
> 全市场覆盖见 `docs/full-market.md`，手机页见 `docs/mobile-web.md`。

---

## 0. 结论先行

**两条链路的分叉点只有一个判据**：`data/raw/{code}/` 下三张关键表
（`profit_sheet` / `balance_sheet` / `cash_flow`）是否齐全 —— 即 `web/server.py::_has_data()`。

| | 不齐 → **链路 A：新标的生成** | 齐全 → **链路 B：旧标的更新** |
|---|---|---|
| 触发 | 手机上搜代码/名称（按需） | launchd 定时 / 手动脚本 |
| 首次耗时 | **A 股 ~63.5s + 报告 2~4 分钟**（含 LLM） | **~6.5s/只**（不调 LLM） |
| 会调 LLM 吗 | 会（叙事层 + 季度解读，各 1 次） | 默认**不调**（复用缓存） |
| 报告产物 | `reports/{期}/{code}.html` + `templates/valueline.html` | 同上（覆盖写） |
| 副作用 | 自动入跟踪池 + 异步重建首页与对比表 | 无（池子不变） |
| 幂等性 | 报告新鲜则直接 `cached` 返回 | 天然幂等（覆盖写） |

三条最容易踩的事实：

1. **「每日更新」只覆盖跟踪池，不覆盖全市场。** 全市场行情 `update_spot_all.py` 与全市场索引
   `build_market_index.py` **都没有挂 launchd**（`~/Library/LaunchAgents/` 里只有
   `com.fqf.daily-refresh.plist` 一个作业）。文档里出现过「索引每周、行情每日」的说法，
   那是**建议频率**，不是现状。
2. **日更会覆盖写全部归档报告**，包括只有 LLM 才能生成的板块。所以日更链路**必须复用缓存**，
   不能「跳过 LLM」了事 —— 这正是本次修掉的第 1 个断点（§8）。
3. **报告渲染器只有一套**：`build_valueline.build(code, daily=...)`。两条链路的差别只在
   `daily` 开关和取数步骤，不存在「两套渲染逻辑各自漂移」的风险。

---

## 1. 数据地层（写操作只有这三层）

| 层 | 覆盖 | 落盘产物 | 命令 | 建议频率 | 实测耗时 | 挂调度？ |
|---|---|---|---|---|---|---|
| **L0 索引** | 全市场 **8368 只**（SH 2320 / SZ 2901 / BJ 344 / HK 2803） | `data/market/` 索引 parquet + `mart.market_index` | `python scripts/build_market_index.py` | 每周 | A 股 17.7s + 港股 8.3s | ❌ 手动 |
| **L1 行情** | 全市场 8368 只 | `data/market/spot_latest.parquet` + `spot_history/spot_YYYY-MM-DD.parquet` | `python scripts/update_spot_all.py` | 每交易日 | 25.8s / 0 失败 | ❌ 手动 |
| **L1' 行情（池内）** | 跟踪池 11 只 | `data/raw/{code}/quote.parquet`（覆盖）<br>`data/market/{code}_quote.parquet`（**按日追加**）<br>`valuation.parquet` / `rating.parquet`（A 股） | `scripts/daily_refresh.py`（launchd 调） | 每交易日 16:30 | 11 只约 1 分钟 | ✅ **唯一自动化** |
| **L2 财报** | 按需 / 跟踪池 / 全市场 | `data/raw/{code}/*.parquet`（9~12 张表） | `python scripts/update_financials.py --scope missing\|watchlist\|all` | 每季 | A 股 63.5s / 港股 4.6s / 只 | ❌ 手动 |

> 现状数据量：`data/raw` **13 个标的 / 2.3MB**（每只 ~180KB）；`data/market` 22 个文件；
> `reports/` 28MB（13 份报告）。全市场财报**不预拉**（8368 只串行 ≈ 100 小时），
> 全量 raw 预估约 1.2GB。

**分层的原因**：磁盘便宜、行情便宜、财报昂贵。L0/L1 可以全量，L2 必须按需。

---

## 2. 链路 A：新标的生成（按需，手机上一次点完）

```
用户输入 "美的集团"
   │
   ├─① market_index.resolve(q)            查 L0 索引：命中唯一 → 继续；多命中 → 返回
   │                                      ambiguous 让用户选（**不猜**，避免查 A 生成 B）
   ├─② _has_data(code)?                   三表 + 目录检查
   │      否 → _fetch_financials()        fetch_all / fetch_all_hk（@retry 指数退避）
   │            └→ save_all()             data/raw/{code}/*.parquet    ← 落盘 9~12 张表
   │            └→ 仍不齐 → 抛错（**绝不降级成示例数据**，见 failure 面 F1）
   ├─③ build_valueline.build(code)        渲染报告
   │       ├→ _reconcile()                官方年报 PDF 金标准交叉校验（下载+抽表+比对）
   │       ├→ _load_real_data()           adapter.build_template_data（cleaner/quality/fraud/lynch）
   │       ├→ 叙事层                      LLM 生成 or 缓存命中
   │       ├→ 季度财报解读                LLM 生成 or 缓存命中
   │       └→ 写 templates/valueline.html + reports/{期}/{code}.html
   ├─④ _sync_watchlist()                  按**报告口径**回写 Lynch/行业/名称（upsert 幂等）
   ├─⑤ upsert_from_report()              服务端再兜一次（防 ④ 被 try 吞掉后静默不入池）
   └─⑥ _schedule_derived_rebuild()       异步重建：build_web_index.py → build_watchlist.py
                                         （合并连点，不阻塞报告队列）
```

| 步骤 | 触发形式 | 输入 | 产物 | 耗时 | 失败面 |
|---|---|---|---|---|---|
| ① 搜索 | `GET /api/search` `POST /api/report` | 代码或名称 | 候选 / job_id | 秒级 | 索引缺失 → 503 |
| ② 取财报 | `_fetch_financials()` | 代码 + 市场 | `data/raw/{code}/*.parquet` | A股 63.5s / 港股 4.6s | 见 F1/F2 |
| ③ 渲染 | `build_valueline.build()` | raw parquet | 2 个 HTML | 2~4 分钟 | LLM 无 key → 占位；PDF 校验失败 → 跳过 |
| ④⑤ 入池 | `watchlist_store.upsert_from_report()` | 报告口径字段 | `watchlist/watchlist.json` | 毫秒 | 失败只打印，不影响报告 |
| ⑥ 重建网页 | `_DERIVED_STEPS` | watchlist + reports | `web/index.html`、`web/watchlist.html` | 首页 2s / 对比表 14s×N | 失败只记状态，不影响报告 |

**实测样本（2026-09-18 11:10，美的集团 00300.HK）**：报告 54KB、raw 落 9 张表、
叙事层完整、Lynch「稳健成长型」按报告口径写进池子、首页卡片 11→12 张。

**为什么 ③ 排在 ④ 前面**：报告是交付物，跟踪池是记账。记账失败不能把「报告已生成」
变成失败，所以 ④ 被 try 包着、只打印。⑤ 是对它为幂等的二次兜底。

---

## 3. 链路 B：旧标的更新（三条子链路，频率不同）

### B1 · 每日（唯一自动化）：`com.fqf.daily-refresh`

```
launchd 16:30（周一~周五，Weekday 1-5，另有 RunAtLoad 在登录/唤醒时补跑）
  └→ bash scripts/daily_refresh.sh
       ├─ 闸门：data/logs/.last_success == 今天 → 直接跳过（--force 可强跑）
       └→ python scripts/daily_refresh.py
            ├─ 读跟踪池 watchlist_store.codes()   ← **排除 status=removed**（见 §8 断点 3）
            └─ 逐只：
                 ├─ snapshot_all()        quote/valuation/rating → raw 覆盖 + market 按日追加
                 ├─ build_valueline.build(code, daily=True)   ← 复用缓存，不调 LLM
                 │    └→ 覆盖写 reports/{期}/{code}.html 与 templates/valueline.html
                 └─ 全部结束后（**顺序执行，非逐只**）：
                      build_web_index.py    → web/index.html
                      build_watchlist.py    → web/watchlist.html
```

- **闸门语义**：只有「全批无失败」才写 `.last_success`；派生产物（两个网页）失败**不计入失败**，
  避免为了重刷 2 秒的首页而重拉 20 分钟的数据。
- 当前状态：`runs = 2`，`last exit code = 0`，闸门 = **2026-09-17**（今天 16:30 会跑第三次）。
- 港股口径：百度估值 / 东财评级无港股接口，只刷 `quote`（腾讯），估值板块靠市值重算。

### B2 · 只重建报告（不拉数据）

```bash
for c in 601088 600519 000651; do python scripts/build_valueline.py $c --daily; done
```
用途：修了渲染逻辑或叙事缓存后，把全池报告刷成新版。实测 **12 只 / 78 秒 ≈ 6.5s/只**。
⚠️ 它会把 `templates/valueline.html` 覆盖成**最后一只**的样式 —— 想保持模板是哪个标的，
把那只放循环最后（HEAD 里是**中国海油 600938**）。

### B3 · 每周：全市场索引（手动）

```bash
python scripts/build_market_index.py            # 拉取 + 落 parquet + 写 mart.market_index
python scripts/build_market_index.py --stats    # 只看现状，不联网
```

### B4 · 每季：财报（手动）

```bash
python scripts/update_financials.py --scope watchlist        # 只更跟踪池
python scripts/update_financials.py --scope missing          # 补缺表的
python scripts/update_financials.py --scope all --workers 4  # 全市场，跑一夜
```
⚠️ **不会重建 DuckDB 数仓**：`load_raw_layer()` 跨全部标的 concat 后物化，本机 8GB 必 OOM。
报告链路直接读 parquet，与数仓解耦。

### B5 · 每日：全市场行情（手动，未挂调度）

```bash
python scripts/update_spot_all.py --workers 4     # 8368 只 / 25.8s
```
用**腾讯批量 50 只/请求**而非东财 `push2`（受限网络下后者会被链路重置）。

---

## 4. 两条链路差异对账（一表看清）

| 维度 | A 新标的生成 | B1 日更 | B2 报告重建 |
|---|---|---|---|
| 谁触发 | 人（手机搜索） | launchd 16:30 | 人 |
| 标的范围 | 1 只 | 跟踪池全部 | 指定/全池 |
| 读 L2 财报 | 缺则拉（63.5s/4.6s） | 不拉 | 不拉 |
| 读 L1 行情 | 不拉（用已有 quote） | 拉（覆盖+追加） | 不拉 |
| LLM 叙事 | 生成或缓存命中 | **复用缓存** | **复用缓存** |
| LLM 季度解读 | 生成或缓存命中 | **复用缓存** | **复用缓存** |
| PDF 金标准校验 | 做 | 跳过 | 跳过 |
| 写报告 | ✅ 两份 | ✅ 两份（覆盖） | ✅ 两份（覆盖） |
| 改跟踪池 | ✅ 自动入池 | ❌ | ✅ 按报告口径回写字段 |
| 重建网页 | ✅（异步） | ✅（顺序） | ❌ |
| 失败是否影响别的步骤 | 报告失败则整单失败 | 行情失败 → 挡闸门；报告失败 → 记 failed | 单只失败不影响其他 |

---

## 5. 缓存与指纹 —— 「改了到底有没有生效」

| 缓存 | 位置 | 键 | 失效条件 | 谁读 |
|---|---|---|---|---|
| 叙事层 | `data/cache/narrative/{code}.json` | facts-hash（**含估值与市值**） | 行情一动即失效 | `_load_cached_narrative()` |
| 季度解读 | `data/cache/quarterly_review/{code}.json` | facts-hash + mdd-hash | 原文/财务事实变，或 >3 天 | `get_or_generate()` |
| 经营结构抽取 | `data/cache/operating_structure/` | 抽取逻辑版本 `_EXTRACT_VERSION` | 改抽取逻辑须递增版本号 | `mda_extract` |
| 公告原文 | `data/cache/disclosure/`、`cninfo_org/` | 报告期 | 新报告披露 | PDF 校验 / 季报解读 |
| 对比表行 | `data/cache/watchlist_rows.json` | **raw parquet mtime + 本标的 4 个池字段 hash** | 数据变 **或** `industry/lynch/color/name` 变 | `build_watchlist._data_fingerprint()` |
| 报告新鲜度 | 无缓存文件，比对 mtime | `report.mtime ≥ 最新 raw.mtime` | 数据更新即重算 | `server._report_fresh()` |

🔴 **最容易中的一坑**：叙事层哈希含行情，所以「只想更新价格」必须走 `--daily`；
而 `--daily` 一旦**跳过**叙事，报告就会缺板块（这正是 §8 断点 1）。
正确做法是 `--daily` **复用**缓存，与「哈希只看财务事实」等价，但不需要重构哈希、不废掉已有缓存。

---

## 6. 失败面清单（🔴 = 静默失败，优先看）

| # | 场景 | 表现 | 现在的防线 |
|---|---|---|---|
| F1 🔴 | `_load_real_data()` 取数失败 | 命令行走「示例数据」兜底，**产出一份看起来正常的神华样例报告** | 服务端生成前 `_has_data()` 显式校验；`data_src` 写进日志 |
| F2 🔴 | 某个 fetch 偶发失败 | `except Exception: return None` 塌缩成一个 None，字段空/界面「未分类」，日志干净 | `@retry()` 三件套 + 占位值过滤（`_LYNCH_PLACEHOLDERS`） |
| F3 🔴 | 报告目录里混进非报告 HTML | `reports/xhs/600519.html`（九宫格发布包）会被当成报告返回 | 判据收敛到 `src/report/artifacts.py`，只认 `YYYYQn` 报告期目录（§8 断点 2） |
| F4 🔴 | 忘了同步清单 | 新入池标的的报告静默不入库 | 已消除：`reports/` 整目录出库，仓库只留模板 |
| F5 | 行情首拉为空 | `.last_success` 被挡 → 当天整批重跑 | `_QUOTE_RETRY_DELAY=3s` 重试一次 |
| F6 | 派生产物失败 | 页面不更新但闸门照写 | 有意为之：单独重跑成本极低 |
| F7 | 东财重述 | 覆盖式快照**不可回滚**，`backup.py` 以「可再生」为由不备份 `data/raw` | ⚠️ 该理由在重述场景下不成立（已知待办） |
| F8 | `920xxx` 北交所代码 | 按首字符 9 归沪市 → 静默取不到行情（少 344 只） | `market_index.a_share_exchange()` 唯一实现 |
| F9 🔴 | **没有季报解读缓存的标的** | 日更走 `get_quarter_review(..., cache_only=True)` —— **只读缓存，绝不生成**。没有 `data/cache/quarterly_review/{code}.json` 的标的，报告里「季度财报解读」**永远**是占位符，日更一万次也补不上（零报错） | ✅ 2026-09-18 加防线：`artifacts.audit_placeholders()` + `scripts/check_placeholders.py`，日更收尾自动体检（**只告警、不进 failed**）。**判据**：`ls data/cache/quarterly_review/` 有几只，报告里就有几只有内容。**修法**：跑一次不带 `--daily` 的构建 |
| F10 🔴 | **占位符掩盖了渲染路径的 bug** | 内容缺失时那段代码根本不执行，于是「缺内容」把「渲染崩溃」挡住了 —— 两个问题叠在一起，只看得见前一个。一旦补齐内容，`_cashflow_block` 在 `None` 上抛 `TypeError: unsupported format string passed to NoneType` → **整份报告渲染失败、不落盘**，磁盘上留着的还是旧那份 | `_yi()` 统一兜底 None（根因：`.get(k, 0)` **只吃 key 不存在，吃不掉 key 存在但值为 None**，而 adapter 抽不到数据时写的正是显式 None） |

### F9 实测记录（2026-09-18）

`quarterly_review/` 只有 8 个缓存文件 = 报告里有内容的 7 只 + 已移出的 300061，**完全吻合**。
缺的 5 只（601088 / 601328 / 000651 / 00700 / 09992）逐项排查过，**都不是链路故障**：

| 检查项 | 结果 |
|---|---|
| `real["quarter_review_facts"]` | ✓ 有内容（7–9 键），`if` 条件满足 —— **不是数据缺失** |
| `DEEPSEEK_API_KEY`（`.env`） | ✓ 配着（35 字符） |
| 巨潮原文 `data/cache/disclosure/` | 601088 / 601328 / 000651 **有** 3 份 PDF；00700 / 09992 港股无 |
| 手动跑 `get_or_generate('601088', facts)` | ✓ **一次成功**（返回 `data_read / structure / cashflow / management / watch`，`_cached=False`） |

→ **链路完好，纯粹是「从没跑过非日更构建」**。时间线佐证：有内容的 7 只，其
`narrative` 与 `quarterly_review` 缓存的 mtime **只差几秒**（同一次构建的产物）；
缺的 5 只 narrative 全部停在 `09-14 11:06:11`（批量导入留下的），qreview 从未落地。

⚠️ 连带提醒：非日更构建会**校验叙事 hash**，而 hash 含市值/估值 → 行情一动必变 →
所以「补季报解读」的同时**会重新调 LLM 生成叙事层**（每只 2 次调用，不是 1 次）。
只想补季报解读、不想动叙事层的话，当前没有更细的开关。

#### 修复执行记录（2026-09-18 13:41）

5 只逐只补跑非日更构建（脚本读 `sys.argv[1]`，**一次只能一只**）：

| 标的 | 结果 | 耗时 |
|---|---|---|
| 601328 交行 | ✅ | 54.8s |
| 000651 格力 | ✅ | 44.9s |
| 00700 腾讯 | ✅ | 17.1s |
| 09992 泡泡玛特 | ✅ | 17.7s |
| **601088 神华** | 🔴 渲染崩溃（见 F10）→ 修 `_yi()` 后重跑成功 | 26.7s + 重跑 |

5 只的季报解读缓存与叙事缓存**全部落地**。修完 F10 后 `check_placeholders.py` 实测：
`✓ 池内报告内容完整，无占位符`。

⚠️ **成本实测修正**：「每只 2 次 LLM 调用」是**上限**而非常态 —— 601328 / 000651 /
00700 / 09992 那次是「LLM 重新生成」，而 601088 重跑时打印的是「复用缓存（事实数据未变）」
（它上一次崩溃前已把叙事缓存写好）。叙事 hash 只含财务事实时才不变；行情一动仍会重算。

---

## 7. 命令速查（可直接粘贴）

```bash
PY=/Users/lixiao/.workbuddy/binaries/python/envs/fqf/bin/python
cd /Users/lixiao/WorkBuddy/fqf

# —— 看服务 / 看调度
$PY -m uvicorn web.server:app --host 127.0.0.1 --port 8000   # 报告服务（手机用 0.0.0.0）
launchctl print gui/501/com.fqf.daily-refresh                # 日更作业状态
launchctl kickstart -k gui/501/com.fqf.daily-refresh         # 立刻跑一次
cat data/logs/.last_success                                  # 闸门：最后一次全批成功的日期
bash scripts/daily_refresh.sh --force                        # 手动强跑日更
$PY scripts/check_refresh.py                                 # 「怎么证明数据更新过」六层证据链

# —— 生成 / 更新
$PY scripts/build_valueline.py 600938                        # 全量重算一只（含 LLM）
$PY scripts/build_valueline.py 600938 --daily                # 只刷估值/市场，不调 LLM
$PY scripts/update_financials.py --codes 600938              # 补一只的财报
$PY scripts/update_spot_all.py --limit 200                   # 全市场行情试点

# —— 页面与产物
$PY scripts/build_web_index.py && $PY scripts/build_watchlist.py
$PY scripts/export.py templates/valueline.html -o reports/2026Q2 -f png pdf

# —— 跟踪池（单一真源）
$PY scripts/watchlist.py list
$PY scripts/watchlist.py add 600938 / remove 600938 -r "原因" / restore 600938
$PY scripts/watchlist.py sync-lynch                # 全池 Lynch 按报告口径回填

# —— 测试（⚠️ 每次换 --basetemp 名字，复用会误报 11 个 error）
$PY -m pytest tests/ -q --basetemp=.pytest_tmp_$(date +%m%d%H%M)
```

---

## 8. 本次体检修掉的三处静默断点（2026-09-18）

### 断点 1 🔴 日更把叙事层洗成占位符

**现象**：`reports/2026Q2/` 里 11 只归档报告（09-17 16:30 日更产出）的
「投资逻辑 / 风险提示」全是「待 LLM 生成」，「季度财报解读」是「未接入」；
而同日按需生成的长江电力（600900）与今天生成的美的（00300）是完整的。
纯静态检查（文件大小、元素计数、日志）**完全发现不了** —— 零报错。

**根因**：`build(daily=True)` 跳过 LLM（本意是省 token、只刷估值），
但 `build()` **无条件**把整份 HTML 覆盖写回归档。跳过 ≠ 保留，等于**擦掉了只有 LLM
才能生成的板块**。

**修复**：`--daily` 改为**复用缓存**而非跳过 ——
`_load_cached_narrative(..., ignore_hash=True)`（日更改的是行情，财务事实没变，
叙事就该留着；哈希含市值不能拿来判断）＋ `get_quarter_review(..., cache_only=True)`
（只读缓存，绝不联网/调模型）。全池 11 只实测重建：**78 秒，12/13 份报告叙事层恢复**
（余下 1 份是已移出的 300061）。

### 断点 2 🔴 「什么算一份报告」有三份口径

| 位置 | 原算法 | 后果 |
|---|---|---|
| `web/server.py::_find_report` | glob `*/{code}.html` 取 **mtime 最大** | 会把 `reports/xhs/600519.html`（九宫格发布包）当报告 |
| `web/server.py::health` | `glob("*/*.html")` 计数 | 报 **14**，而首页只列 11 |
| `build_web_index._scan_reports` | 跳过 xhs + 按**报告期**取最新 | 唯一正确的 |

发布包至今没酿成事故**纯属侥幸**：它的 mtime 恰好比报告旧。下次重建小红书包，
`/report/600519` 就会返回一张九宫格。

**修复**：新增 `src/report/artifacts.py` 作为唯一判据（只认 `YYYYQn` 报告期目录、
取最新报告期、三态对账 `in_pool`/`orphan`/`removed`），服务端与首页脚本全改读它；
`/api/health` 的 `reports` 现在**等于首页卡片数**，并附 `reports_detail` 说明差异原因
（实测 `reports=11, in_pool=11, orphan=0, removed=2, on_disk=13`）。

### 断点 3 🔴 软删之后，日更还在刷已移出的标的

**现象**：09-18 给跟踪池加软删（`status: removed` + `restore`）之后，
`daily_refresh.load_codes()` 与 `update_financials._load_watchlist()` 仍是
「直接读 json 取全部 code」—— 300061 / 600900 移出后**每天继续被拉行情、刷报告**。
页面上看不出、日志里不报错，只是每天多干两份活，「移出池」在数据层从未生效。

**修复**：两处都改走 `watchlist_store.codes()`（内含 removed 过滤）。

---

## 9. 待办（本次未动）

- 🔴 `data/raw` 版本留痕：上游重述会**静默改写历史**且不可回滚（`backup.py` 不备份它的理由
  在重述场景下不成立）。
- 全量财报是否启动（`--scope all --workers 4 --skip-done`，A 股约 12 小时 / 港股约 1 小时）。
- 6 项历史口径待拍板：`net_cash` 口径、港股 kline、PE「近 10 年」措辞（次新股序列不足 10 年）、
  M-Score 中性值、行业排名回填、港股毛利率口径。
- `web/query.html` 仍在仓库里（`server.py` 注释写着"已删除"，实际有文件 + 兼容路由）。
- **🔴 更正（2026-09-18 实测）**：本条原写「对比表是列式宽表，标的一多会横向溢出」——
  **这个判断不成立**，别照它去改渲染层。实测对比表是**每只一行 × 13 个指标列**
  （`build_watchlist.py::_render_rows`，11 只 = 11 行），加标的**只增行、不增列**；
  横向滚动 + 首列冻结**早已实现**（同文件 docstring 第 3 条：`.scroller{overflow-x:auto}`
  + `table{min-width:1040px}` + `th.sticky/td.sticky`），1080 视口下根本不用滑。
  池子不再设上限（2026-09-18 去掉 `max_size=8`）后，真正会**线性恶化**的是另外两件：
  ① **页面垂直长度** —— 实测 11 只 → 页面总高 **1368px**（表格本身 1028px，每行约 **93px**），
     按此线性外推 20 只约 2200px；每行含「名称 / 代码 / 行业 · Lynch」三层小字；
  ② **重建耗时** —— 每只 `build_template_data` 约 14 秒，11 只约 2.6 分钟，20 只约 5 分钟
  （行缓存键 = `data/raw/{code}/*.parquet` 最大 mtime，日更后全部失效、一次性全量重算）。
  要解的是这两个，**不是横向**。
