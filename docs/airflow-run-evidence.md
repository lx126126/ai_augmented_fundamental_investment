# Airflow 数据管道运行证据（作品集可引用）

> 记录一次完整的 Airflow 生产级数据管道真实运行，用于简历/作品集佐证「Airflow 编排」「DuckDB 数仓 schema 化」能力。
> 运行日期：2026-09-03（本地 Docker Desktop + Airflow 2.9.3）

## 一、运行环境

| 项 | 值 |
|---|---|
| 编排 | Apache Airflow 2.9.3（Docker Compose：postgres + scheduler + webserver） |
| 镜像 | `apache/airflow:2.9.3-python3.11` + 项目依赖（AKShare 1.18.x / pandas / pyarrow / duckdb / pymupdf / requests） |
| 数仓 | DuckDB（文件型，`data/warehouse/fqf.duckdb`） |
| 项目挂载 | `../:/opt/airflow/project`（源码实时挂载，改代码无需重建镜像） |

## 二、两个 DAG

### 1. `valueline_pipeline`（财报季 ETL，手动触发）

跟踪池 6 只标的的全链路：`fetch → validate → build → export → warehouse → web_index`。

- 6 只标的：格力 `000651.SZ`、腾讯 `00700.HK`、泡泡玛特 `09992.HK`、茅台 `600519.SH`、神华 `601088.SH`、交行 `601328.SH`
- 任务拓扑：`start → [每只 fetch → validate → build → export] → warehouse → web_index`
- 共 26 个 task，全部 `success`

**最新一次运行**（`run_id=manual__2026-09-03T06:35:12+00:00`）：

| 阶段 | 结果 |
|---|---|
| fetch × 6 | ✅ 全部成功（A股走 `fetch_all`，港股走 `fetch_all_hk`） |
| validate × 6 | ✅ 全部成功（资产负债表勾稽校验） |
| build × 6 | ✅ 全部成功（渲染 ValueLine 报告） |
| export × 6 | ✅ 全部成功（PNG 长图导出） |
| warehouse | ✅ 成功（写入 DuckDB 数仓） |
| web_index | ✅ 成功（手机网页版首页刷新） |
| **总耗时** | **3 分 15 秒** |

### 2. `market_daily`（日更行情，定时调度）

每日拉取跟踪池行情快照（现价/PE/PB/市值/52周区间），写入 `market_snapshot`。

- 最新运行 `success`（1 分 42 秒）
- 调度：`@daily`

## 三、数仓 schema（DuckDB）

`data/warehouse/fqf.duckdb` 分两层 14 张表：

```
raw 层（11 张，原始数据落地）
  ├── financial_indicator   # 财务指标（比率型）
  ├── profit_sheet          # 利润表
  ├── balance_sheet         # 资产负债表
  ├── cash_flow             # 现金流量表
  ├── dividend              # 分红
  ├── segments              # 分业务收入构成
  ├── valuation             # 估值（PE/PB/分位）
  ├── quote                 # 行情（现价/市值/52周）
  ├── rating                # 机构评级
  ├── competition           # 行业竞争地位
  └── profile               # 公司概况

mart 层（3 张，加工后宽表）
  ├── annual_financials     # 年度财务宽表
  ├── quarter_financials    # 季度财务宽表
  └── segments              # 分业务汇总
```

## 四、本次运行修复的关键 bug

**问题**：A 股 4 只（格力/茅台/神华/交行）fetch 全部 `KeyError: 'report_date'` 失败。

**根因**：DAG 把带市场后缀的 code（如 `601088.SH`）传给 `fetch_all`，而 `fetch_financial_indicator` 直接用它调 `ak.stock_financial_analysis_indicator(symbol=code)`，东财接口对带后缀 symbol 返回**空 DataFrame（0行0列）**，导致 `_remap` 后无 `report_date` 列。

**修复**：新增 `_bare_code()` 工具函数（剥 `.SH/.SZ/.HK` 后缀），在 `fetch_all` 入口 + `fetch_financial_indicator` + `_em_symbol` 三处统一防御性规范化。

**验证**：容器内 `fetch_financial_indicator('601088.SH')` 从空表恢复为正常返回 81 行 32 列，`report_date` 列存在。修复后 `valueline_pipeline` 全链路成功。

## 五、证据产物清单

| 产物 | 路径 | 说明 |
|---|---|---|
| 数仓文件 | `data/warehouse/fqf.duckdb` | 14 表 schema 化落地 |
| 手机网页首页 | `web/index.html` | 6 只标的入口页 |
| 跟踪池对比表 | `web/watchlist.html` | 横向决策对比 |
| 报告归档 | `reports/2026Q2/` | 各标的 ValueLine 报告 |
| DAG 定义 | `airflow/dags/valueline_pipeline.py`、`market_daily.py` | 编排代码 |
| 容器编排 | `airflow/docker-compose.yaml`、`Dockerfile` | 可复现环境 |
