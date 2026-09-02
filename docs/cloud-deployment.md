# 云部署方案设计

> fqf 从「本地个人工具」到「生产级云原生数据管道」的迁移设计。
> 版本：v1.0（2026-09-02）
> 定位：**设计文档**（证明「懂云原生数据管道怎么落地」），非实际部署记录。本地链路已真实跑通（Docker + Airflow + DuckDB + FastAPI），本文档描述其规模化、服务化、托管化路径。

---

## 1. 目标

把当前「单机可跑」的投研数据管道，迁移为云上**托管、可伸缩、可观测**的生产级架构，对齐资深数据工程师 JD 的「云（GCP / AWS managed）」能力要求。

设计原则：

1. **不重造**：本地已跑通的 ETL / 数仓 / API 逻辑全部复用，只替换「部署载体」。
2. **优先托管**：能用 managed 服务的不用自建（Composer 替代自建 Airflow、BigQuery 替代自建数仓）。
3. **可逆**：每一层都有「本地 → 云」的一一映射，迁移可增量、可回退。
4. **成本可控**：个人投研场景数据量小，全部选「按需 + 免费层」档位。

---

## 2. 现状盘点（本地架构）

```
┌─────────────────────────────────────────────────────────────┐
│  本地（当前已跑通，零改动）                                    │
│                                                             │
│  Docker Compose                                              │
│   ├─ postgres (airflow 元数据库)                             │
│   ├─ airflow-webserver + scheduler (LocalExecutor)           │
│   │      └─ 五阶段 DAG：fetch→validate→build→warehouse→export│
│   ├─ 挂载 ../ → /opt/airflow/project（共享项目目录）          │
│   └─ 数据落盘：data/raw/*.parquet + data/warehouse/fqf.duckdb│
│                                                             │
│  FastAPI (uvicorn) ──只读──→ DuckDB (mart 层)               │
│                                                             │
│  数据源：AKShare / 东财 / 巨潮 / 腾讯（国内站点，需直连）      │
└─────────────────────────────────────────────────────────────┘
```

关键约束（迁移时必守）：

| 约束 | 说明 | 云上对应 |
|------|------|---------|
| 数据源在国内 | 东财/巨潮/腾讯接口需直连，**不能走境外代理**（本地已清 NO_PROXY） | 云函数/Composer 需部署在**亚太区**，且不挂全局代理 |
| 密钥不进库 | DeepSeek key 在 `.env`，不入 git | Secret Manager |
| parquet / duckdb 不提交 | `.gitignore` 已排除 | Cloud Storage（对象存储） |
| 质量 gate 阻断下游 | validate 高风险即抛异常 | 同一逻辑，Composer 任务失败自动重试 + 告警 |

---

## 3. 组件映射：本地 → 云

以 **GCP** 为主方案（Composer 是 Airflow 官方托管形态，迁移路径最短），AWS 列为等价替代。

| 本地组件 | GCP 服务 | AWS 等价 | 说明 |
|---------|---------|---------|------|
| Airflow（LocalExecutor） | **Cloud Composer 2** | MWAA | 托管 Airflow，DAG 原样搬入，调度/重试/告警托管 |
| postgres（Airflow 元库） | Composer 自带 Cloud SQL | MWAA 自带 RDS | 随 Composer 托管，无需自建 |
| DuckDB 数仓（fqf.duckdb） | **BigQuery** | Redshift / Athena | mart 层 schema 化迁移，SQL 兼容度高 |
| parquet 原始数据（data/raw） | **Cloud Storage** | S3 | 对象存储，parquet 原生支持，BigQuery 可直接外部表读取 |
| FastAPI 查询 API | **Cloud Run** | Fargate / Lambda | 无服务器容器，按请求计费，自动伸缩 |
| 报告 HTML/PNG/PDF（reports/） | Cloud Storage | S3 | 静态产物归档，可接 CDN |
| DeepSeek / 数据源密钥 | **Secret Manager** | Secrets Manager | 密钥集中管理 + 版本 + 审计 |
| 失败告警 Webhook（钉钉/飞书） | Composer 告警 + Cloud Monitoring | CloudWatch + SNS | 任务失败 → 邮件/Webhook 推送 |
| 定时触发（每日 2 点） | Composer schedule | MWAA schedule | DAG 自带 cron，无需改 |

> **关键判断**：DuckDB → BigQuery 是唯一「逻辑等价但需改 SQL」的迁移点，其余全是「换托管载体、业务代码不动」。

---

## 4. 目标云架构

```
                         ┌─────────────────────────────┐
                         │       Secret Manager        │
                         │  (DeepSeek key / 数据源凭证) │
                         └──────────────┬──────────────┘
                                        │ 注入
┌──────────────────┐           ┌───────▼────────┐
│  数据源（国内）   │◄─直连─────│ Cloud Composer 2 │
│ AKShare/东财/巨潮 │           │  (托管 Airflow)  │
│  /腾讯（亚太区）  │           │  fetch→validate │
└──────────────────┘           │  →build→warehouse│
                               │  →export        │
                               └──┬──────────┬───┘
                                  │写        │写
                         ┌────────▼──┐   ┌───▼────────────┐
                         │ Cloud     │   │  BigQuery      │
                         │ Storage   │   │  (mart 层)     │
                         │ (parquet  │   │  annual/quarter│
                         │  + 报告)  │   │  /segments     │
                         └───────────┘   └───┬────────────┘
                                             │ 只读查询
                                      ┌──────▼──────┐
                                      │  Cloud Run  │
                                      │  (FastAPI)  │
                                      │  7 个端点   │
                                      └─────────────┘
```

数据流与本地**完全一致**：抓取 → 质量 gate（PDF 金标准交叉校验 + 造假检测）→ 渲染 → 数仓落库 → API 只读查询。变的只是「谁在跑、数据存在哪」。

---

## 5. 分阶段迁移路径

### 阶段 0：现状（已完成 ✅）
本地 Docker Compose 跑通五阶段 DAG + DuckDB 数仓 + FastAPI API。这是迁移的**基线**，所有后续阶段都是「把某个本地组件替换成托管服务」。

### 阶段 1：数据湖上云（最低成本起步，半小时）
- **做**：`data/raw/*.parquet` 改为写入 Cloud Storage（或先双写：本地 + GCS 各一份）。
- **收益**：原始数据脱离「单机磁盘」，可追溯、可共享、可被 BigQuery 直接读。
- **改动**：`storage.save_all()` 增加 GCS sink（`gcsfs` 库），业务逻辑零改动。

### 阶段 2：数仓迁 BigQuery
- **做**：DuckDB 的 `mart.*` 三张宽表 → BigQuery 对应 schema 化表（见 §6 DDL）。
- **收益**：SQL 分析能力大幅增强（列式存储 + 大规模扫描），且可与公开数据集 join。
- **改动**：`warehouse.py` 增加 BigQuery sink；`query.py` 的 SQL 方言微调（`max_by` → `ARRAY_AGG(... LIMIT 1)` 等）。

### 阶段 3：编排迁 Composer
- **做**：`airflow/dags/valueline_pipeline.py` 原样部署到 Cloud Composer 2（亚太区）。
- **收益**：调度/重试/告警/监控全部托管，不再需要本地 Docker 常驻。
- **改动**：几乎为零——DAG 是纯 Python + PythonOperator，Composer 直接认。

### 阶段 4：API 上 Cloud Run
- **做**：FastAPI 打包成容器镜像，部署 Cloud Run（内存 ≥ 512MB，因 DuckDB 查询在内存加载）。
- **收益**：只读查询服务 7×24 在线，按请求计费，无流量时缩容到 0。
- **改动**：`main.py` 零改动，加一份 `Dockerfile` + `cloudbuild.yaml`。

### 阶段 5：可观测 + 密钥治理
- **做**：Secret Manager 接密钥；Composer 失败告警 → 钉钉/飞书 Webhook；Cloud Monitoring 看板。
- **收益**：补上「质量 + 告警 + 血缘」的可观测闭环（本地 `notify_failure` 已实现，云上只是换推送通道）。

> **迁移策略**：阶段 1 → 5 可**增量推进、每步独立可验证**，不必一次性切换。任一阶段都保留「本地兜底」，云上失败可秒回本地。

---

## 6. BigQuery 数仓 DDL（mart 层迁移）

DuckDB 的 mart 三表 → BigQuery schema 化 DDL。**分区 + 聚簇**是 BigQuery 相比 DuckDB 的增量收益：

```sql
-- mart.annual_financials：年度财务宽表
CREATE TABLE IF NOT EXISTS `fqf.mart.annual_financials` (
  symbol            STRING    NOT NULL,   -- 股票代码
  report_date       DATE      NOT NULL,   -- 年报期末
  operating_revenue FLOAT64,              -- 营业收入（亿元）
  net_profit_parent FLOAT64,              -- 归母净利润（亿元）
  net_margin_pct    FLOAT64,              -- 净利率 %
  roe_pct           FLOAT64,              -- ROE %
  gross_margin_pct  FLOAT64,              -- 毛利率 %
  debt_ratio_pct    FLOAT64,              -- 资产负债率 %
  total_assets      FLOAT64,              -- 总资产（亿元）
  total_equity      FLOAT64,              -- 归母净资产（亿元）
  ocf               FLOAT64,              -- 经营现金流（亿元）
  rotc              FLOAT64,              -- 资本回报率 %
  dividend_yield_pct FLOAT64,             -- 股息率 %
  working_capital   FLOAT64,              -- 营运资本（亿元）
  -- ... 其余派生指标列同 DuckDB 宽表（此处省略，全量见 warehouse.py 的 build_annual_financials）
)
PARTITION BY DATE_TRUNC(report_date, YEAR)   -- 按年报年份分区
CLUSTER BY symbol;                           -- 按股票聚簇，单股查询走聚簇裁剪

-- mart.quarter_financials：季度财务宽表（近 8 季）
CREATE TABLE IF NOT EXISTS `fqf.mart.quarter_financials` (
  symbol      STRING NOT NULL,
  report_date DATE   NOT NULL,
  -- ... 16 个单季指标列
)
PARTITION BY DATE_TRUNC(report_date, QUARTER)
CLUSTER BY symbol;

-- mart.segments：分业务收入构成（长表）
CREATE TABLE IF NOT EXISTS `fqf.mart.segments` (
  symbol        STRING  NOT NULL,
  report_date   DATE    NOT NULL,
  segment_name  STRING  NOT NULL,
  category_type STRING,          -- 按产品 / 按行业
  revenue_yi    FLOAT64,          -- 条线收入（亿元）
  margin_pct    FLOAT64           -- 条线利润率 %
)
PARTITION BY DATE_TRUNC(report_date, YEAR)
CLUSTER BY symbol, segment_name;
```

> 附一份 **PostgreSQL 版 DDL**（[`sql/schema_postgres.sql`](../sql/schema_postgres.sql)），用于本地/自建 PG 的等价 schema，证明「schema 设计与关系型数仓兼容」。二者列结构一致，仅方言（分区语法）不同。

---

## 7. 成本估算（个人投研规模）

数据量量级：4-8 只跟踪标的 × 全历史财务，parquet 原始数据 < 100 MB，mart 宽表 < 10 万行。这个规模下：

| 服务 | 档位 | 月成本（估算） |
|------|------|--------------|
| Cloud Storage | 标准存储 + 少量读写 | ≈ ¥0（免费层内） |
| BigQuery | 按查询扫描计费，10 万行级 | ≈ ¥0（免费层 1TB/月） |
| Cloud Run | 无流量缩容到 0，按请求 | ≈ ¥0（免费层内） |
| Cloud Composer 2 | **常驻**，这是唯一持续成本 | ¥1500-2500/月（最小 1 环境） |
| Secret Manager | 少量密钥 | ≈ ¥0 |

> **成本优化决策**：Composer 是唯一「贵」的项（因为要常驻调度器）。个人投研场景有两个替代：
> 1. **Cloud Scheduler + Cloud Run Jobs**（无服务器批处理）——用 `gcloud scheduler` 定时触发 Cloud Run Job 跑 ETL，成本趋近 0，代价是失去 Airflow 的 DAG 可视化/重试 UI。
> 2. **保留本地 Airflow**，只把「数仓 + API + 存储」上云——混合架构，兼顾成本与能力展示。

> 推荐：**面试展示用 Composer（证明懂托管编排），实际自用可切 Scheduler+Jobs（省钱）**。两者 DAG/任务逻辑 100% 复用。

---

## 8. 安全与合规

| 项 | 设计 |
|----|------|
| 密钥 | DeepSeek key、数据源凭证全部进 Secret Manager，运行时不落盘、不入镜像、不入 git |
| 权限 | Composer 服务账号最小权限：仅写 GCS bucket + 写 BigQuery + 读 Secret（IAM 细粒度授权） |
| 网络 | 数据源为国内站点 → Composer / Cloud Run 部署**亚太区**，不挂境外代理；若需访问受限站点，用 VPC + 合规出口 |
| 数据合规 | 原始财报数据为公开数据，无个人隐私；报告「纯客观、零个人观点」，天然规避投资咨询合规风险 |
| 审计 | Secret Manager 版本 + Cloud Audit Logs，密钥访问留痕 |

---

## 9. 关键设计决策（为什么这么选）

1. **DuckDB → BigQuery，而非直接 Postgres**：mart 层是「宽表 + 长表」的分析型负载，列式 + 分区 + 聚簇比行式 OLTP 更合适；且 BigQuery 有免费层，个人规模零成本。
2. **Composer 而非自建 K8s Airflow**：JD 要求「编排 + 质量 + 告警 + 血缘」，Composer 原生覆盖；自建 K8s 运维成本高，个人场景不值得。
3. **Cloud Run 而非 GKE**：查询 API 是无状态只读服务，Cloud Run 的「缩容到 0 + 按请求计费」完美匹配「低频查询」场景，GKE 会常驻空跑烧钱。
4. **质量 gate 不上云也保留**：`validate` 任务的「PDF 金标准交叉校验 + 造假检测非标一票否决」是项目护城河，云上沿用同一逻辑（Composer 任务失败即阻断下游 + 告警），这是「数据可信」的云化延续。
5. **不真部署**：当前阶段目标是「简历作品集的云能力证明」，设计文档 + 可运行 DDL 已足够让面试官看到「懂怎么落地」，真部署的成本/收益比在找到目标岗位前不划算。

---

## 10. 待办（找到目标岗位后按需推进）

- [ ] 阶段 1：GCS 双写 parquet（`gcsfs` sink）
- [ ] 阶段 2：BigQuery mart 表落库 + 外部表读 GCS
- [ ] 阶段 3：DAG 部署 Composer（亚太区）
- [ ] 阶段 4：FastAPI 容器化 + Cloud Run
- [ ] 阶段 5：Secret Manager + Cloud Monitoring 告警闭环
- [ ] 补 `sql/schema_postgres.sql`（PG 等价 DDL，与 BigQuery 列结构一致）
