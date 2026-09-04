"""ELT 同步：DuckDB 数仓 mart 层 → PostgreSQL。

对齐资深数据工程师 JD 的「数仓 schema 化（Snowflake/BigQuery/PG）」缺口：
  把 DuckDB 单文件数仓的 mart 层（3 张报告指标宽表）同步到 PostgreSQL，
  证明「schema 设计与关系型数据库完全兼容、可落地」。

分层原则：
  DuckDB = 轻量开发/单机分析（raw + mart 都在本地文件）
  PG     = 生产级关系型数仓（mart 层可被多服务、SQL 客户端、BI 查询）

用法：
  1. 启动 PG（Docker）：
     docker compose -f airflow/docker-compose.pg.yaml up -d
  2. 建 schema（首次）：
     psql -h 127.0.0.1 -U fqf -d fqf -f sql/schema_postgres.sql
     （或本脚本自动执行 --init-schema）
  3. 同步数据：
     python -m src.data.elt_pg --init-schema --truncate

依赖：
  psycopg2-binary（PG 驱动）、duckdb
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DUCKDB_PATH = ROOT / "data" / "warehouse" / "fqf.duckdb"
SCHEMA_SQL = ROOT / "sql" / "schema_postgres.sql"

# mart 层 3 张表（同步顺序：annual → quarter → segments）
MART_TABLES = [
    "annual_financials",
    "quarter_financials",
    "segments",
]

# 每张表的列（与 DuckDB DESCRIBE 实际列、schema_postgres.sql 声明完全一致）
TABLE_COLUMNS = {
    "annual_financials": [
        "symbol", "report_date", "operating_revenue", "net_profit", "net_profit_parent",
        "gross_margin_pct", "sell_expense", "admin_expense", "income_tax", "interest_expense",
        "total_profit", "ocf", "depreciation", "capital_expenditure", "amortize_intangible",
        "amortize_lpe", "depre_invest_realestate", "depre_oilgas_bio", "amortize_useright",
        "total_assets", "total_liabilities", "total_equity", "total_equity_all",
        "current_assets", "monetary_funds", "inventory", "accounts_receivable",
        "interest_bearing_debt", "goodwill", "share_capital", "preferred_shares",
        "audit_opinion", "current_liabilities", "accounts_payable", "other_current_assets",
        "other_current_liabilities", "noncurrent_liab_1y", "retained_profit", "bond_payable",
        "long_payable", "lease_liabilities", "short_bond_payable", "noncurrent_liabilities",
        "long_term_debt", "total_debt", "net_margin_pct", "roe_pct", "roe_weighted_pct",
        "debt_ratio_pct", "revenue_yoy_pct", "net_profit_yoy_pct", "ocf_to_profit_pct",
        "current_ratio", "quick_ratio", "dividend_per_share", "dividend_yield_pct",
        "dividend_total", "dividend_payout_pct", "working_capital",
        "depreciation_amortization", "income_tax_rate", "retained_to_equity", "rotc",
    ],
    "quarter_financials": [
        "symbol", "report_date", "operating_revenue", "net_profit_parent", "gross_margin_pct",
        "net_margin_pct", "ocf", "total_assets", "total_liabilities", "total_equity",
        "monetary_funds", "inventory", "accounts_receivable", "interest_bearing_debt",
        "goodwill", "roe_pct",
    ],
    "segments": [
        "symbol", "report_date", "segment_name", "category_type", "revenue_yi", "margin_pct",
    ],
}


def _pg_connect():
    """连接 PG（参数从环境变量读，默认对齐 docker-compose.pg.yaml）。"""
    import os
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("PG_PORT", "5432")),
        dbname=os.environ.get("PG_DB", "fqf"),
        user=os.environ.get("PG_USER", "fqf"),
        password=os.environ.get("PG_PASSWORD", "fqf123456"),
    )


def _read_mart_table(table: str) -> pd.DataFrame:
    """从 DuckDB 读 mart 层一张表（只读连接）。"""
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        cols = TABLE_COLUMNS[table]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        df = con.execute(
            f'SELECT {col_sql} FROM mart.{table} ORDER BY symbol, report_date'
        ).fetchdf()
        return df
    finally:
        con.close()


def _to_pg_type(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame 类型规整，保证能无损写入 PG：
    - report_date → datetime64[ns]（PG DATE/TIMESTAMP 兼容）
    - 数值列 → float（PG NUMERIC 兼容，NaN → None）
    - 文本列 → str（PG VARCHAR/TEXT 兼容，NaN → None）
    """
    df = df.copy()
    for col in df.columns:
        if col == "report_date":
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].astype("float64").where(pd.notna(df[col]), None)
        else:
            df[col] = df[col].astype("object").where(pd.notna(df[col]), None)
    return df


def init_schema() -> None:
    """在 PG 里执行 schema_postgres.sql（建 raw/mart schema + 3 张 mart 表）。"""
    import psycopg2
    conn = _pg_connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print(f"[elt] 已执行 schema：{SCHEMA_SQL.name}")
    finally:
        conn.close()


def sync_table(conn, table: str, truncate: bool = False) -> int:
    """同步一张表：先（可选）truncate，再批量 insert。返回写入行数。"""
    import psycopg2
    from psycopg2.extras import execute_values

    df = _read_mart_table(table)
    if df.empty:
        print(f"[elt] {table}: 0 行（跳过）")
        return 0
    df = _to_pg_type(df)

    cols = TABLE_COLUMNS[table]
    col_sql = ", ".join(f'"{c}"' for c in cols)

    with conn.cursor() as cur:
        if truncate:
            cur.execute(f'TRUNCATE mart.{table}')
        execute_values(
            cur,
            f'INSERT INTO mart.{table} ({col_sql}) VALUES %s',
            df[cols].itertuples(index=False, name=None),
            page_size=1000,
        )
    return len(df)


def sync_all(truncate: bool = False, init: bool = False) -> dict:
    """完整同步：可选建 schema → 同步 3 张表 → 返回行数统计。"""
    import psycopg2
    if init:
        init_schema()

    conn = _pg_connect()
    summary = {}
    try:
        for table in MART_TABLES:
            try:
                n = sync_table(conn, table, truncate=truncate)
                summary[table] = n
                print(f"[elt] {table}: {n} 行")
            except Exception as e:
                summary[table] = f"失败: {e}"
                print(f"[elt] {table}: 失败 {e}")
    except Exception:
        conn.rollback()
        raise
    else:
        # psycopg2 默认非 autocommit：不显式 commit，close() 会隐式 rollback，
        # 导致所有 INSERT 被丢弃（三表 0 行的根因）。必须在 close 前 commit。
        conn.commit()
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description="DuckDB mart → PostgreSQL ELT 同步")
    parser.add_argument("--init-schema", action="store_true", help="先执行 schema_postgres.sql")
    parser.add_argument("--truncate", action="store_true", help="同步前 truncate 目标表（幂等重跑）")
    parser.add_argument("--no-truncate", action="store_true", help="不清空，追加写入")
    args = parser.parse_args()

    truncate = args.truncate and not args.no_truncate
    result = sync_all(truncate=truncate, init=args.init_schema)
    print(json.dumps(result, ensure_ascii=False, indent=2))
