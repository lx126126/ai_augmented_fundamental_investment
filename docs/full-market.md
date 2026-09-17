# 全市场覆盖 · 三层架构与数据更新

> 本文说明「全市场 A 股 + 港股」这条链路是怎么跑通的，以及日度/季度更新怎么做。
> 所有数字均为 **2026-09-17 在本机（MacBookAir7,2 / 8GB / macOS 12.7.6）实测**，不是估算。

## 1. 为什么不是「把全市场财报都拉下来」

先把账算清楚，架构才有依据：

| 项目 | 实测值 | 推论 |
|---|---|---|
| 单只 A 股冷启动 `fetch_all` | **63.5s** / 12 张表 / 2386 行 | 5565 只串行 ≈ **98 小时** |
| 单只港股冷启动 `fetch_all_hk` | **4.6s** / 8 张表 | 2803 只串行 ≈ **3.6 小时**（港股很便宜） |
| 单只 raw 磁盘占用 | 约 **150KB** | 全量 8368 只 ≈ **1.2GB**（空间不是问题） |
| 全市场行情一次拉取 | **25.8s** / 8368 只 / 0 失败 | 行情足够便宜，可以全量每日刷 |

结论：**磁盘便宜、行情便宜、财报昂贵**。所以：

```
L0 索引层   全市场 8368 只标的（代码/名称/交易所）     秒级可查   每周构建
L1 行情层   全市场 8368 只行情快照（价/PE/PB/市值）    25.8s      每交易日
L2 财报层   单标的财报 + 一页报告                      拉数 63.5s + 生成约 3 分钟   按需触发 / 每季度全量
```

**「跑通全量市场」= L0 + L1 全量，L2 按需。** 用户查谁才拉谁、算谁，
因此覆盖范围（8368 只）不受"预拉过什么"限制，而成本只与真实查询量成正比。

## 2. 三个入口脚本

### L0 索引：`scripts/build_market_index.py`

```bash
python scripts/build_market_index.py          # 拉取并落盘（约 50s）
python scripts/build_market_index.py --stats  # 只看现状，不联网
```

- 数据源：A 股 `stock_info_a_code_name()`（5565 只 / 17.7s，巨潮）、
  港股 `stock_hk_spot()`（2803 只 / 8.3s，新浪）
- 产物：`data/market/index.parquet` + DuckDB `mart.market_index`
- 频率：**每周一次**足够（新股/改名/退市是低频事件），别放进每日任务

### L1 行情：`scripts/update_spot_all.py`

```bash
python scripts/update_spot_all.py                    # 全市场（25.8s）
python scripts/update_spot_all.py --market HK        # 只港股
python scripts/update_spot_all.py --limit 200        # 试点
python scripts/update_spot_all.py --no-history       # 不写当日归档
```

- 数据源：腾讯 `qt.gtimg.cn` 批量（50 只/请求，A 股 `sh/sz/bj`、港股 `hk` 前缀）
- 产物：`data/market/spot_latest.parquet`（覆盖，永远最新）
  + `data/market/spot_history/spot_YYYY-MM-DD.parquet`（**按日追加**）
- 为什么要两份：`raw/quote` 是覆盖式单行快照，覆写即丢历史；history 目录就是补这个缺口
- **有意不用东财 push2**（`stock_zh_a_spot_em`）：该域名在受限网络下会被链路重置，
  表现为 ProxyError/RemoteDisconnected；腾讯这条路稳定且支持批量

### L2 财报：`scripts/update_financials.py`

```bash
python scripts/update_financials.py --scope missing        # 默认：只补缺表的
python scripts/update_financials.py --scope watchlist      # 只观察池
python scripts/update_financials.py --codes 600519,00700   # 指定标的
python scripts/update_financials.py --scope all --workers 4 --skip-done   # 全市场（跑一夜）
python scripts/update_financials.py --scope all --dry-run  # 先看计划与预估耗时
```

- 落 `data/raw/{code}/*.parquet`，摘要写 `data/logs/financials_YYYY-MM-DD.json`
- 单只失败不影响整体；退出码 1 表示失败率 > 20%
- 全量建议 `--workers 3~4`（再高容易被数据源限流）

## 3. 产品服务：`web/server.py`

```bash
# 本机访问
python -m uvicorn web.server:app --host 127.0.0.1 --port 8000

# 手机同一 WiFi 访问（先在「系统设置 → 网络 → WiFi」查本机内网 IP）
python -m uvicorn web.server:app --host 0.0.0.0 --port 8000
# 手机浏览器打开 http://<内网IP>:8000
```

| 端点 | 说明 |
|---|---|
| `GET /` | 查询首页（`web/query.html`） |
| `GET /api/search?q=茅台` | 全市场搜索（名称包含 / 代码前缀），带 `has_data` / `has_report` 状态 |
| `POST /api/report` `{"query":"600519"}` | 提交生成任务，返回 `job_id` |
| `GET /api/report/status/{job_id}` | 轮询进度（fetching → building → done/error） |
| `GET /report/{code}` | 查看报告 HTML |
| `GET /api/reports` | 已生成报告清单 |
| `GET /api/health` | 服务与索引状态 |

设计要点：

- **生成串行**（线程池 workers=1）：`build_valueline.build()` 会写固定的
  `templates/valueline.html`，并发会互相踩踏；串行也天然避免数据源限流。
- **前端轮询而不是长连接**：手机浏览器长连接会超时。
  实测全新标的（招商银行 600036）全流程 **约 4 分钟**：
  拉财报 1 分钟 → LLM 叙事 2-3 分钟 → 年报 PDF 交叉校验与出图。
  第二次查询命中缓存 **0.46 秒**。所以前端文案写的是「约 3-5 分钟」而不是乐观数字。
- 🔴 **绝不生成假报告**：`build_valueline._load_real_data()` 在取数失败时会
  **静默降级成中国神华的示例数据**（`data_src = "示例数据"`）并照常输出一份看起来
  正常的报告。命令行里这是兜底，产品里等于给用户看别家公司的财报。
  `web/server.py` 因此在生成前显式用 `_has_data()` 校验，缺就先拉、拉不到就报错。

## 4. 🔴 全量数据与 DuckDB 数仓的边界（务必知道）

**`update_financials.py` 默认不重建 DuckDB 数仓。** 原因：

`warehouse.load_raw_layer()` 会把每张表**跨全部标的 `pd.concat` 后物化**。
11 张表 × 8000 只 —— 仅 `balance_sheet` 就是约 300 万行 × 87 列，本机 8GB 内存撑不住，会 OOM。

分工因此是：

| 存储 | 放什么 | 谁在用 |
|---|---|---|
| `data/raw/**` | 全市场原冷数据（parquet，按目录组织） | **报告链路**（adapter → cleaner → build_valueline）直接读 |
| `data/warehouse/fqf.duckdb` | 只放需要 SQL 分析的标的（观察池 + 常查的） | `src/api/` 查询 API、`mart.*` 宽表 |

报告链路**不经过 DuckDB**，所以"全量数据"和"数仓规模"是解耦的 ——
这是全量覆盖能在 8GB 机器上跑通的关键。

## 5. 网络出口（沙箱 / CI / 云端必读）

`src/data/fetcher.py` 默认**强制直连**（置空 `urllib` 的代理取值函数）——
因为本机能直连数据源，而环境里常残留 Veee 代理设置，走代理反而超时。

如果所在环境**直连不通、必须经代理出网**（沙箱 / CI / 容器 / 海外云），设：

```bash
export FQF_HTTP_PROXY=http://127.0.0.1:15236     # 换成你的代理地址
```

### 已知边界（2026-09-17 实测，别踩重复的坑）

- 沙箱里**东财 push2 域名**（`*.push2.eastmoney.com`，全市场行情 clist 接口）
  即使配了代理也不稳定（多线程分页时 `ProxyError: RemoteDisconnected`）。
  这也是 L1 行情走**腾讯批量**而不是东财 spot 的另一个原因。
- `stock_info_a_code_name` / `stock_hk_spot` / 财报接口 / 腾讯行情在沙箱直连下**可用**。
- ⚠️ **`920xxx` 是北交所新代码段**，不能按首字符 `9` 归到沪市：
  `sh920000`/`sz920000` 取不到行情（静默空），`bj920000` 正常。
  踩过的后果：全市场行情**静默少 344 只**且没有任何报错。
  已修在 `market_index.a_share_exchange()` 与 `fetcher._em_symbol()`。

## 6. 建议的日常节奏

| 频率 | 动作 | 命令 |
|---|---|---|
| 每交易日收盘后 | 全市场行情快照 | `python scripts/update_spot_all.py` |
| 每周 | 重建标的索引 | `python scripts/build_market_index.py` |
| 每季度（财报季后） | 补全市场财报 | `python scripts/update_financials.py --scope all --workers 4 --skip-done` |
| 随时 | 查一只股票出报告 | 打开 `http://<内网IP>:8000` 或 `POST /api/report` |

本机（8GB / 无 Docker）用 `launchd` 调度，见 `scripts/com.fqf.daily-refresh.plist`；
Airflow DAG 保留在 `airflow/` 作为可迁移到服务器的版本。
