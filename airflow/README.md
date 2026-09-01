# ValueLine 一页研报 · Airflow 生产级 ETL 编排

把「拉取 → 交叉校验 → 造假检测 → 渲染 → 导出」的脚本流水线，编排为可调度、可告警、血缘可追踪的 Airflow DAG。

## 目录结构

```
airflow/
├── docker-compose.yaml       # LocalExecutor + Postgres 本地部署
├── Dockerfile                # 基于 apache/airflow + 项目依赖
├── requirements.txt          # akshare / pandas / pyarrow / pymupdf / requests
├── dags/
│   └── valueline_pipeline.py # DAG：fetch → validate → build → export
├── plugins/                  # （空，预留自定义 operator / hook）
└── logs/                     # 运行日志（gitignore）
```

## 快速上手

### 1. 安装 Docker Desktop

> 本机若尚未安装 Docker，先装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows 版默认启用 WSL2 后端），装完启动 Docker 引擎。

### 2. 拉起 Airflow

```bash
cd airflow
docker compose up -d --build
```

首次 `--build` 会基于官方镜像安装项目依赖，耗时取决于网络（akshare 依赖较多）。

### 3. 访问 Web UI

- 地址：http://localhost:8080
- 账号：`admin` / `admin`

### 4. 触发 DAG

1. Web UI 找到 `valueline_pipeline`，点 **▶ Trigger DAG**
2. 默认跑跟踪池首选标的 `601088`；切换标的传参数：`{"code": "600519"}`
3. 观察 Graph 视图的 task 依赖与执行状态

## 数据流与血缘

| 阶段 | 产出物 | 说明 |
|---|---|---|
| fetch | `data/raw/{code}/*.parquet` | 原始财报（AKShare/东财/巨潮/腾讯行情） |
| validate | `data/validation/{code}_{year}_reconcile.json` | PDF 金标准交叉校验覆盖记录 |
| build | `templates/valueline.html` + `reports/{期}/{code}.html` | 一页研报 |
| export | `reports/{期}/{code}.png` / `.pdf` | 高清长图 / A4 PDF（可选） |

## 数据质量 gate（validate 阶段）

- **官方年报 PDF 金标准交叉校验**：东财/新浪接口同源，在「同一控制下企业合并追溯重述」等场景会抓错（神华 2025 总资产接口报 9038 亿 vs 官方 6278 亿）；用巨潮年报 PDF 合并资产负债表逐字段覆盖，差异 >1% 即修正。
- **Beneish M-Score 造假检测** + 现金流背离 + 应收增速背离。
- **审计意见非标一票否决**：保留/无法表示/否定 → 直接判高风险。
- 高风险 → **抛异常阻断下游 build + 触发告警**（`on_failure_callback`）。

## 告警

- 日志告警：任何 task 失败都会记录到 scheduler/webserver 日志。
- Webhook 推送（可选）：配置 `AIRFLOW_ALERT_WEBHOOK` 环境变量后，失败时推送钉钉/飞书文本消息。

## 常见问题

- **容器内访问国内数据源失败**：`docker-compose.yaml` 已清空代理环境变量（`NO_PROXY=*`），确保东财/新浪/巨潮/腾讯直连。若仍失败，检查宿主机是否强制代理。
- **export 阶段跳过**：基础镜像未装 playwright/chromium（镜像更轻）。需要导出 PNG/PDF 时，取消 `Dockerfile` 底部注释并重新 `--build`。
- **切换标的**：Trigger DAG 时传 `{"code": "600519"}`，或改 `dags/valueline_pipeline.py` 里的 `DEFAULT_CODE`。
