#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看数据结构：列名、行数、数据样例（Code Review 辅助工具）。

用法：
    python scripts/inspect_raw.py --list                  # 列出所有已拉取的股票代码
    python scripts/inspect_raw.py 601088                  # 概览：每个 parquet 的行列数 + 列名
    python scripts/inspect_raw.py 601088 -t profit_sheet  # 详细：单表列名 + 数据样例
    python scripts/inspect_raw.py 601088 -n 5             # 样例行数改为 5（默认 3）
    python scripts/inspect_raw.py --mart                  # 查看 DuckDB mart 层三张表

说明：
- raw 层 = data/raw/{code}/*.parquet（一个文件对应一个源接口）
- mart 层 = data/warehouse/fqf.duckdb 里的三张建模结果表
- 宽表（如 30 列的资产负债表）样例会自动「转置」显示，方便纵向阅读
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 终端可能是 GBK，强制 UTF-8 输出避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
DUCKDB_PATH = ROOT / "data" / "warehouse" / "fqf.duckdb"


def list_codes() -> list[str]:
    """列出所有已拉取的股票代码。"""
    if not RAW_DIR.exists():
        return []
    return sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir())


def _print_columns(cols: list[str], per_line: int = 6) -> None:
    """列名分组换行打印，避免一行过长。"""
    for i in range(0, len(cols), per_line):
        chunk = cols[i : i + per_line]
        print("      " + "  ".join(chunk))


def _print_sample(df: pd.DataFrame, n: int) -> None:
    """打印数据样例。宽表自动转置，方便纵向看。"""
    sample = df.head(n)
    if sample.empty:
        print("      （无数据）")
        return
    # 列数多 → 转置显示（行变列），避免横向滚动
    if len(df.columns) > 8:
        t = sample.T
        t.columns = [f"第{i+1}行" for i in range(len(sample))]
        # 值过长时截断，保持可读性
        t = t.map(lambda v: (str(v)[:28] + "…") if len(str(v)) > 28 else v)
        print(t.to_string())
    else:
        print(sample.to_string(index=False))


def inspect_code(code: str, table: str | None, n: int) -> None:
    """查看单只股票的 raw 层。"""
    d = RAW_DIR / code
    if not d.exists():
        print(f"❌ 找不到目录：{d}")
        print(f"   已有的股票：{', '.join(list_codes()) or '（空）'}")
        return

    files = sorted(d.glob("*.parquet"))
    if not files:
        print(f"❌ {d} 下没有 parquet 文件")
        return

    if table:
        files = [f for f in files if f.stem == table]
        if not files:
            print(f"❌ 没有名为 {table} 的表。可用：{[f.stem for f in sorted(d.glob('*.parquet'))]}")
            return

    print(f"\n{'=' * 70}")
    print(f"  raw 层 · {code} · 共 {len(files)} 个 parquet")
    print(f"{'=' * 70}")

    total_rows = 0
    for f in files:
        df = pd.read_parquet(f)
        total_rows += len(df)
        print(f"\n【{f.stem}】 {len(df)} 行 × {len(df.columns)} 列")
        _print_columns(list(df.columns))
        if table:
            print(f"\n  ── 数据样例（前 {n} 行，宽表已转置）──")
            _print_sample(df, n)

    print(f"\n{'-' * 70}")
    print(f"  合计 {len(files)} 张表 / {total_rows} 行")
    if not table:
        print(f"  想看某张表的具体数据：python scripts/inspect_raw.py {code} -t <表名>")


def inspect_mart(n: int) -> None:
    """查看 DuckDB mart 层三张表。"""
    if not DUCKDB_PATH.exists():
        print(f"❌ 找不到数仓文件：{DUCKDB_PATH}")
        return
    import duckdb

    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    tables = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='mart' ORDER BY table_name"
    ).fetchall()

    print(f"\n{'=' * 70}")
    print(f"  mart 层 · DuckDB · {len(tables)} 张表")
    print(f"{'=' * 70}")

    for (name,) in tables:
        cols = con.execute(f"DESCRIBE mart.{name}").fetchdf()
        cnt = con.execute(f"SELECT COUNT(*) FROM mart.{name}").fetchone()[0]
        print(f"\n【{name}】 {cnt} 行 × {len(cols)} 列")
        _print_columns(list(cols["column_name"]))
        print(f"\n  ── 数据样例（前 {n} 行，宽表已转置）──")
        df = con.execute(f"SELECT * FROM mart.{name} LIMIT {n}").fetchdf()
        _print_sample(df, n)

    con.close()
    print(f"\n{'-' * 70}")
    print("  mart 是建模后的宽表/长表（raw 是接口原样落地）")


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    # --list：列出所有股票
    if args[0] == "--list":
        codes = list_codes()
        print("已拉取的股票：" + (", ".join(codes) if codes else "（空）"))
        return

    # --mart：查 DuckDB
    n = 3
    if "-n" in args:
        try:
            n = int(args[args.index("-n") + 1])
        except (IndexError, ValueError):
            pass
    if args[0] == "--mart":
        inspect_mart(n)
        return

    # 其余：按股票代码查 raw
    code = args[0]
    table = None
    if "-t" in args:
        idx = args.index("-t")
        if idx + 1 < len(args):
            table = args[idx + 1]
    inspect_code(code, table, n)


if __name__ == "__main__":
    main()
