"""DuckDB 查询层：FastAPI 与数仓 mart 层之间的数据访问封装。

设计原则：
  1. 只读 mart 层（报告指标宽表），不触碰 raw 层、不重算宽表。
  2. 每个查询函数返回 pandas.DataFrame 或 list[dict]，FastAPI 层负责序列化。
  3. 连接短生命周期（每次查询开/关），避免长连接占用 DuckDB 文件锁。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

# 项目根 & 数仓路径（与 warehouse.py 保持一致）
ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "warehouse" / "fqf.duckdb"


def _connect() -> duckdb.DuckDBPyConnection:
    """打开只读连接（read_only=True 防止误写数仓）。"""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"数仓文件不存在：{DB_PATH}。请先运行 python -m src.data.warehouse 构建数仓。"
        )
    return duckdb.connect(str(DB_PATH), read_only=True)


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    """执行查询并转成 list[dict]（FastAPI 直接序列化为 JSON）。

    支持 ? 参数化占位（params），防止 SQL 注入。
    """
    df = con.execute(sql, params or []).fetchdf()
    return df.to_dict(orient="records")


# --------------------------------------------------------------------------- #
# 查询函数
# --------------------------------------------------------------------------- #

def list_stocks() -> list[dict]:
    """列出数仓里所有已入库的股票（symbol + 最新年度核心指标）。"""
    con = _connect()
    try:
        sql = """
        SELECT
            a.symbol,
            max_by(a.report_date, a.report_date) AS latest_report_date,
            max_by(a.operating_revenue, a.report_date) AS revenue_yi,
            max_by(a.net_profit_parent, a.report_date) AS net_profit_yi,
            max_by(a.net_margin_pct, a.report_date) AS net_margin_pct,
            max_by(a.roe_pct, a.report_date) AS roe_pct
        FROM mart.annual_financials a
        GROUP BY a.symbol
        ORDER BY a.symbol
        """
        return _rows(con, sql)
    finally:
        con.close()


def get_annual_history(symbol: str, limit: int = 25) -> list[dict]:
    """单股年度财务全历史（倒序，最新在前）。"""
    con = _connect()
    try:
        sql = """
        SELECT *
        FROM mart.annual_financials
        WHERE symbol = ?
        ORDER BY report_date DESC
        LIMIT ?
        """
        return _rows(con, sql, [symbol, limit])
    finally:
        con.close()


def get_quarter_history(symbol: str) -> list[dict]:
    """单股季度财务（近 8 季）。"""
    con = _connect()
    try:
        sql = """
        SELECT *
        FROM mart.quarter_financials
        WHERE symbol = ?
        ORDER BY report_date DESC
        """
        return _rows(con, sql, [symbol])
    finally:
        con.close()


def get_segments(symbol: str) -> list[dict]:
    """单股分业务收入构成（长表）。"""
    con = _connect()
    try:
        sql = """
        SELECT *
        FROM mart.segments
        WHERE symbol = ?
        ORDER BY report_date DESC, revenue_yi DESC
        """
        return _rows(con, sql, [symbol])
    finally:
        con.close()


def compare_stocks(metric: str, year: int | None = None) -> list[dict]:
    """跨股对比：指定指标的最新（或指定年报）值，降序排列。

    仅允许白名单指标，防止 SQL 注入（metric 拼进列名）。
    """
    allowed = {
        "operating_revenue", "net_profit_parent", "net_margin_pct", "roe_pct",
        "debt_ratio_pct", "gross_margin_pct", "rotc", "dividend_yield_pct",
        "total_assets", "total_equity", "current_ratio", "ocf",
    }
    if metric not in allowed:
        raise ValueError(f"指标 {metric} 不在白名单内，允许：{sorted(allowed)}")

    con = _connect()
    try:
        if year:
            sql = f"""
            SELECT symbol, report_date, {metric} AS value
            FROM mart.annual_financials
            WHERE year(report_date) = ? AND month(report_date) = 12
            ORDER BY {metric} DESC
            """
            return _rows(con, sql, [year])
        else:
            sql = f"""
            SELECT symbol,
                   max_by(report_date, report_date) AS report_date,
                   max_by({metric}, report_date) AS value
            FROM mart.annual_financials
            GROUP BY symbol
            ORDER BY value DESC
            """
            return _rows(con, sql)
    finally:
        con.close()


def get_metric_history(symbol: str, metric: str) -> list[dict]:
    """单股某指标的历史序列（用于画趋势图）。"""
    allowed = {
        "operating_revenue", "net_profit_parent", "net_margin_pct", "roe_pct",
        "gross_margin_pct", "debt_ratio_pct", "rotc", "dividend_yield_pct",
        "total_assets", "total_equity", "ocf", "working_capital",
    }
    if metric not in allowed:
        raise ValueError(f"指标 {metric} 不在白名单内，允许：{sorted(allowed)}")

    con = _connect()
    try:
        sql = f"""
        SELECT report_date, {metric} AS value
        FROM mart.annual_financials
        WHERE symbol = ?
        ORDER BY report_date ASC
        """
        return _rows(con, sql, [symbol])
    finally:
        con.close()
