# fqf 查询 API（FastAPI + DuckDB）

对齐资深数据工程师 JD 的「后端 FastAPI 查询 API」缺口。

## 数据流定位

```
AKShare/东财抓取 → 清洗宽表(cleaner) → DuckDB 数仓(warehouse, raw/mart) → 本 API 只读查询
                                                                              ↑
                                                        FastAPI 直接 SELECT mart 层，不重算
```

API 只读 `mart` 层（报告指标宽表），不触碰 raw 层、不重算宽表。DuckDB 连接用 `read_only=True`，防止误写数仓。

## 启动

```bash
# 方式一：直接跑
python -m src.api.main          # 等价于 uvicorn --host 127.0.0.1 --port 8000

# 方式二：uvicorn 显式
uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

> 前置：需先构建数仓 `python -m src.data.warehouse`（否则报 503，提示数仓文件不存在）。

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 健康检查 + 端点清单 |
| GET | `/stocks` | 全部已入库股票（symbol + 最新年度核心指标） |
| GET | `/stocks/{code}` | 单股年度财务全历史（倒序，`?limit=` 控制条数） |
| GET | `/stocks/{code}/quarters` | 单股季度财务（近 8 季） |
| GET | `/stocks/{code}/segments` | 单股分业务收入构成 |
| GET | `/stocks/{code}/metrics/{metric}` | 单股某指标历史序列（趋势图用） |
| GET | `/compare?metric={m}[&year={y}]` | 跨股对比，指定指标降序 |

## 示例

```bash
curl "http://127.0.0.1:8000/stocks"
curl "http://127.0.0.1:8000/stocks/601088?limit=5"
curl "http://127.0.0.1:8000/stocks/601088/quarters"
curl "http://127.0.0.1:8000/stocks/601088/segments"
curl "http://127.0.0.1:8000/stocks/601088/metrics/roe_pct"
curl "http://127.0.0.1:8000/compare?metric=roe_pct"
curl "http://127.0.0.1:8000/compare?metric=operating_revenue&year=2025"
```

## 安全设计

- **SQL 注入防护**：所有 `symbol` 用 `?` 参数化占位；`metric` 走白名单（`compare_stocks` / `get_metric_history` 内定义 `allowed` 集合），非法指标返回 400。
- **只读连接**：`duckdb.connect(..., read_only=True)`。
- **错误语义**：503 数仓未构建 / 404 股票或数据不存在 / 400 参数非法。

## 白名单指标

`operating_revenue` `net_profit_parent` `net_margin_pct` `gross_margin_pct` `roe_pct` `debt_ratio_pct` `rotc` `dividend_yield_pct` `total_assets` `total_equity` `current_ratio` `ocf` `working_capital`
