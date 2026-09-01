"""FastAPI 查询 API：数仓 mart 层的只读查询服务。

对齐资深数据工程师 JD 的「后端 FastAPI 查询 API」缺口：
  数据流：AKShare/东财抓取 → 清洗宽表 → DuckDB 数仓（raw/mart）→ 本 API 只读查询。

端点：
  GET /                健康检查 + 服务说明
  GET /stocks          全部已入库股票（symbol + 最新核心指标）
  GET /stocks/{code}   单股年度财务全历史
  GET /stocks/{code}/quarters    单股季度财务
  GET /stocks/{code}/segments    单股分业务收入构成
  GET /stocks/{code}/metrics/{metric}  单股某指标历史序列
  GET /compare?metric=roe_pct[&year=2025]  跨股对比

启动：
  python -m src.api.main
  # 或 uvicorn src.api.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from . import query

app = FastAPI(
    title="fqf 基本面投研查询 API",
    description="A 股 ValueLine 一页研报的数据查询服务，基于 DuckDB 数仓 mart 层。",
    version="0.1.0",
)


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #

@app.get("/")
def root() -> dict:
    return {
        "service": "fqf 基本面投研查询 API",
        "version": "0.1.0",
        "data_layer": "DuckDB mart（报告指标宽表）",
        "endpoints": [
            "/stocks",
            "/stocks/{code}",
            "/stocks/{code}/quarters",
            "/stocks/{code}/segments",
            "/stocks/{code}/metrics/{metric}",
            "/compare",
        ],
    }


@app.get("/stocks")
def list_stocks() -> list[dict]:
    """全部已入库股票 + 最新年度核心指标。"""
    try:
        return query.list_stocks()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/stocks/{code}")
def get_stock(code: str, limit: int = Query(25, ge=1, le=50)) -> list[dict]:
    """单股年度财务全历史（倒序）。"""
    try:
        rows = query.get_annual_history(code, limit=limit)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not rows:
        raise HTTPException(status_code=404, detail=f"股票 {code} 不存在于数仓")
    return rows


@app.get("/stocks/{code}/quarters")
def get_quarters(code: str) -> list[dict]:
    """单股季度财务（近 8 季）。"""
    try:
        rows = query.get_quarter_history(code)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not rows:
        raise HTTPException(status_code=404, detail=f"股票 {code} 无季度数据")
    return rows


@app.get("/stocks/{code}/segments")
def get_segments(code: str) -> list[dict]:
    """单股分业务收入构成。"""
    try:
        rows = query.get_segments(code)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not rows:
        raise HTTPException(status_code=404, detail=f"股票 {code} 无分业务数据")
    return rows


@app.get("/stocks/{code}/metrics/{metric}")
def get_metric(code: str, metric: str) -> list[dict]:
    """单股某指标历史序列（趋势图用）。"""
    try:
        rows = query.get_metric_history(code, metric)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not rows:
        raise HTTPException(status_code=404, detail=f"股票 {code} 指标 {metric} 无数据")
    return rows


@app.get("/compare")
def compare(
    metric: str = Query(..., description="指标名，如 roe_pct / net_margin_pct / operating_revenue"),
    year: int | None = Query(None, description="指定年报年份（如 2025），缺省取各股最新期"),
) -> list[dict]:
    """跨股对比：指定指标降序排列。"""
    try:
        rows = query.compare_stocks(metric, year=year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return rows


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8000, reload=True)
