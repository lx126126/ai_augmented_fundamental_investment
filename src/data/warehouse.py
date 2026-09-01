"""数仓落库层：raw parquet → DuckDB（schema 化）。

分层设计（对齐资深数据工程师 JD 的「数仓 schema 化」）：
  raw   —— 原始 parquet 直接挂载（东财口径，零加工，不做 ETL）
  mart  —— 报告指标宽表（复用 cleaner 的 build_* 宽表，统一「亿元」口径）

表清单：
  raw.{table}                —— 11 张原始表，跨股票 UNION（symbol 列区分）
  mart.annual_financials     —— 年度财务宽表（全历史 + 派生指标）
  mart.quarter_financials    —— 季度财务宽表（近 8 季，单季口径）
  mart.segments              —— 分业务收入构成（近 2 年，长表）

设计原则：
  1. raw 层零加工，DuckDB 直接 read_parquet 挂视图，保证「原始数据可追溯」。
  2. mart 层复用 cleaner.build_*（不重造宽表逻辑），DuckDB 只做持久化 + schema 治理。
  3. 表名/列名显式声明，FastAPI 阶段可直接 SELECT 而不必每次重算。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .adapter import load_raw
from .cleaner import (
    build_annual_financials,
    build_quarter_financials,
)

# 项目根
ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "warehouse" / "fqf.duckdb"
RAW_DIR = ROOT / "data" / "raw"

# 单只股票的全部原始表（与 adapter.load_raw 保持一致）
RAW_TABLES = [
    "financial_indicator", "profit_sheet", "balance_sheet", "cash_flow",
    "dividend", "segments", "valuation", "quote", "rating", "competition", "profile",
]


def _connect(db_path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """连接 DuckDB（文件不存在则自动创建）。"""
    p = Path(db_path) if db_path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    return con


def list_codes() -> list[str]:
    """扫描 data/raw 下所有已拉取的股票代码。"""
    if not RAW_DIR.exists():
        return []
    return sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir() and p.name.isdigit())


def _normalize_datetime(df: pd.DataFrame) -> pd.DataFrame:
    """把 DataFrame 里所有 datetime 列统一 cast 为 datetime64[ns]。

    根因：东财不同接口写出的 parquet，report_date 有的存成 datetime64[us]、
    有的 datetime64[ns]（如 competition 表：格力是 [us]，茅台/神华/交行是 [ns]），
    跨股票 pd.concat 时精度冲突报 ValueError（array size 11 vs 19）。
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].astype("datetime64[ns]")
    return df


def load_raw_layer(con: duckdb.DuckDBPyConnection, codes: list[str] | None = None) -> list[str]:
    """挂载 raw 层：把每只股票的 parquet 注册为 raw.{table} 视图（跨股票 UNION）。

    返回已挂载的表名列表。DuckDB 的 read_parquet 支持 glob，但为保留 symbol 列
    （部分表 parquet 内已含 symbol，部分依赖目录名），这里用显式 read_parquet + UNION。
    """
    codes = codes or list_codes()
    if not codes:
        return []

    mounted = []
    for table in RAW_TABLES:
        frames: list[pd.DataFrame] = []
        for code in codes:
            p = RAW_DIR / code / f"{table}.parquet"
            if not p.exists():
                continue
            df = pd.read_parquet(p)
            df = _normalize_datetime(df)
            if "symbol" not in df.columns:
                df = df.copy()
                df.insert(0, "symbol", code)
            frames.append(df)
        if not frames:
            continue
        full = pd.concat(frames, ignore_index=True, join="outer")
        con.register(f"raw_{table}", full)  # 注册为 raw_{table}（DuckDB 视图）
        # 同时写入持久表 raw.{table}，便于跨连接查询
        con.execute(f"CREATE OR REPLACE TABLE raw.{table} AS SELECT * FROM raw_{table}")
        mounted.append(table)
    return mounted


def _build_mart_annual(codes: list[str]) -> pd.DataFrame:
    """mart.annual_financials：全历史年度宽表（跨股票 UNION）。"""
    frames = []
    for code in codes:
        raw = load_raw(code)
        if not {"financial_indicator", "profit_sheet", "balance_sheet", "cash_flow"}.issubset(raw):
            continue
        annual = build_annual_financials(raw)
        annual = annual.copy()
        if "symbol" not in annual.columns:
            annual.insert(0, "symbol", code)
        frames.append(annual)
    return pd.concat(frames, ignore_index=True, join="outer") if frames else pd.DataFrame()


def _build_mart_quarter(codes: list[str]) -> pd.DataFrame:
    """mart.quarter_financials：近 8 季单季宽表（跨股票 UNION）。"""
    frames = []
    for code in codes:
        raw = load_raw(code)
        if not {"financial_indicator", "profit_sheet", "balance_sheet", "cash_flow"}.issubset(raw):
            continue
        quarter = build_quarter_financials(raw)
        quarter = quarter.copy()
        if "symbol" not in quarter.columns:
            quarter.insert(0, "symbol", code)
        frames.append(quarter)
    return pd.concat(frames, ignore_index=True, join="outer") if frames else pd.DataFrame()


def _build_mart_segments(codes: list[str]) -> pd.DataFrame:
    """mart.segments：分业务收入构成长表（近 2 年，跨股票 UNION）。

    拍平为长表：symbol, report_date, segment_name, category_type, revenue_yi, margin_pct。
    """
    rows = []
    for code in codes:
        raw = load_raw(code)
        seg = raw.get("segments")
        if seg is None or seg.empty:
            continue
        # 直接对原始 segments 拍平（保留真实日期），复用 cleaner 的清理逻辑，
        # 不调 build_segments（其返回的是聚合好的 (labels, [(条线,收入,利润率)]) 而非长表）。
        from .cleaner import _clean_segment_name
        cand = seg[seg["category_type"].isin(["按产品分类", "按行业分类"])].copy()
        cand["clean"] = cand["segment_name"].map(_clean_segment_name)
        cand = cand[cand["clean"].notna()]
        # 选条线更丰富的分类口径
        best_type, best_n = None, 0
        for ct in ("按产品分类", "按行业分类"):
            n = cand[cand["category_type"] == ct]["clean"].nunique()
            if n > best_n:
                best_n, best_type = n, ct
        cand = cand[cand["category_type"] == best_type]
        latest = cand["report_date"].max()
        cutoff = latest - pd.DateOffset(years=2)
        cand = cand[cand["report_date"] >= cutoff]
        for _, r in cand.iterrows():
            rows.append({
                "symbol": code,
                "report_date": r["report_date"],
                "segment_name": r["clean"],
                "category_type": best_type,
                "revenue_yi": r["segment_revenue"] / 1e8 if pd.notna(r["segment_revenue"]) else None,
                "margin_pct": r["segment_margin"] * 100 if pd.notna(r["segment_margin"]) else None,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_mart_layer(con: duckdb.DuckDBPyConnection, codes: list[str] | None = None) -> list[str]:
    """构建 mart 层：复用 cleaner 宽表，落库为 mart.* 表。"""
    codes = codes or list_codes()
    tables = []

    annual = _build_mart_annual(codes)
    if not annual.empty:
        con.register("_annual", annual)
        con.execute("CREATE OR REPLACE TABLE mart.annual_financials AS SELECT * FROM _annual")
        tables.append("annual_financials")

    quarter = _build_mart_quarter(codes)
    if not quarter.empty:
        con.register("_quarter", quarter)
        con.execute("CREATE OR REPLACE TABLE mart.quarter_financials AS SELECT * FROM _quarter")
        tables.append("quarter_financials")

    segments = _build_mart_segments(codes)
    if not segments.empty:
        con.register("_segments", segments)
        con.execute("CREATE OR REPLACE TABLE mart.segments AS SELECT * FROM _segments")
        tables.append("segments")

    return tables


def init_schemas(con: duckdb.DuckDBPyConnection) -> None:
    """创建 raw / mart 两个 schema（幂等）。"""
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE SCHEMA IF NOT EXISTS mart")


def build_warehouse(codes: list[str] | None = None, db_path: Path | str | None = None) -> dict:
    """一键构建数仓：raw 挂载 + mart 落库。

    返回 summary 字典，含各层表名与行数，便于验证。
    """
    con = _connect(db_path)
    init_schemas(con)
    codes = codes or list_codes()

    raw_tables = load_raw_layer(con, codes)
    mart_tables = build_mart_layer(con, codes)

    summary = {"db_path": str(DB_PATH if not db_path else db_path), "codes": codes,
               "raw": {}, "mart": {}}
    for t in raw_tables:
        try:
            summary["raw"][t] = con.execute(f"SELECT COUNT(*) FROM raw.{t}").fetchone()[0]
        except Exception:
            summary["raw"][t] = None
    for t in mart_tables:
        try:
            summary["mart"][t] = con.execute(f"SELECT COUNT(*) FROM mart.{t}").fetchone()[0]
        except Exception:
            summary["mart"][t] = None

    con.close()
    return summary


if __name__ == "__main__":
    import json
    result = build_warehouse()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
