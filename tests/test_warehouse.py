# -*- coding: utf-8 -*-
"""单元测试：warehouse 数仓层（datetime 归一化 + 跨股票 concat + raw/mart 落库）。"""
import pandas as pd
import pytest

from src.data.warehouse import (
    _normalize_datetime,
    list_codes,
    load_raw_layer,
    _connect,
)


def test_normalize_datetime_mixed_precision():
    """us/ns 混合精度 datetime 列，归一化后应统一为 datetime64[ns]。

    pandas 3.0 默认 datetime64[us]；部分旧 parquet 存的是 [ns]。归一化保证
    DuckDB schema 一致（concat 时列 dtype 不因来源而漂移）。
    """
    df_us = pd.DataFrame({
        "report_date": pd.to_datetime(["2024-12-31"]).astype("datetime64[us]"),
        "v": [1],
    })
    df_ns = pd.DataFrame({
        "report_date": pd.to_datetime(["2025-12-31"]).astype("datetime64[ns]"),
        "v": [2],
    })
    # 归一化后统一为 [ns]，concat 成功且 dtype 一致
    a = _normalize_datetime(df_us)
    b = _normalize_datetime(df_ns)
    assert str(a["report_date"].dtype) == "datetime64[ns]"
    assert str(b["report_date"].dtype) == "datetime64[ns]"
    merged = pd.concat([a, b], ignore_index=True, join="outer")
    assert len(merged) == 2
    assert str(merged["report_date"].dtype) == "datetime64[ns]"


def test_normalize_datetime_idempotent():
    """归一化把 pandas 3.0 默认的 [us] cast 为 [ns]（保证 schema 一致）。"""
    df = pd.DataFrame({"report_date": pd.to_datetime(["2024-12-31"])})
    # pandas 3.0 默认 [us]
    assert str(df["report_date"].dtype) == "datetime64[us]"
    out = _normalize_datetime(df)
    assert str(out["report_date"].dtype) == "datetime64[ns]"


def test_list_codes_finds_parquet_dirs(tmp_path, monkeypatch):
    """list_codes 应扫描 data/raw 下的数字目录。"""
    import src.data.warehouse as wh
    monkeypatch.setattr(wh, "RAW_DIR", tmp_path)
    (tmp_path / "601088").mkdir()
    (tmp_path / "600519").mkdir()
    (tmp_path / "not_a_code").mkdir()  # 非数字目录应被排除
    codes = wh.list_codes()
    assert codes == ["600519", "601088"]  # 排序后


def test_raw_layer_mixed_precision_codes(tmp_path, monkeypatch):
    """跨股票 concat 落库：不同股票 datetime 精度不同也能成功（回归测试）。"""
    import src.data.warehouse as wh

    # 构造两只股票的 profit_sheet，report_date 精度故意不同
    for code, dtype in (("000001", "datetime64[us]"), ("000002", "datetime64[ns]")):
        d = tmp_path / code
        d.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            "report_date": pd.to_datetime(["2024-12-31"]).astype(dtype),
            "operating_revenue": [1.0e10],
            "net_profit_parent": [2.0e9],
        })
        df.to_parquet(d / "profit_sheet.parquet", index=False)

    monkeypatch.setattr(wh, "RAW_DIR", tmp_path)
    db = tmp_path / "test.duckdb"
    con = _connect(db)
    wh.init_schemas(con)
    mounted = wh.load_raw_layer(con, ["000001", "000002"])
    assert "profit_sheet" in mounted

    # raw.profit_sheet 应含两只股票 2 行
    n = con.execute("SELECT COUNT(*) FROM raw.profit_sheet").fetchone()[0]
    assert n == 2
    symbols = con.execute("SELECT DISTINCT symbol FROM raw.profit_sheet ORDER BY symbol").fetchall()
    assert [s[0] for s in symbols] == ["000001", "000002"]
    con.close()
