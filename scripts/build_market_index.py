#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建全市场标的索引（A 股 + 港股）→ parquet + DuckDB mart.market_index。

这是「全市场可查询」的地基：索引只存代码/名称/交易所（几百 KB），
不存任何财务数据。用户查谁，才拉谁的财报（按需），磁盘与时间因此可控。

用法：
    python scripts/build_market_index.py            # 拉取并落盘
    python scripts/build_market_index.py --dry-run  # 只拉取，不写 parquet / 不写库
    python scripts/build_market_index.py --stats    # 只看现有索引概况，不联网

建议频率：**每周一次**足矣（新股上市 / 改名 / 退市是低频事件）。
不适合放进每日任务 —— 索引稳定，天天拉是浪费外部请求。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import market_index  # noqa: E402


def write_duckdb(df) -> str | None:
    """把索引挂进 DuckDB 的 mart.market_index（供 SQL 侧 join 用）。"""
    try:
        import duckdb
        from src.data.warehouse import DB_PATH

        if not DB_PATH.exists():
            return f"跳过（数仓不存在：{DB_PATH}）"
        con = duckdb.connect(str(DB_PATH))
        try:
            con.execute("CREATE SCHEMA IF NOT EXISTS mart")
            con.register("_idx", df)
            con.execute("CREATE OR REPLACE TABLE mart.market_index AS SELECT * FROM _idx")
            n = con.execute("SELECT COUNT(*) FROM mart.market_index").fetchone()[0]
        finally:
            con.close()
        return f"mart.market_index 已写入 {n} 行"
    except Exception as e:
        return f"跳过（{type(e).__name__}: {e}）"


def main() -> int:
    ap = argparse.ArgumentParser(description="构建全市场标的索引")
    ap.add_argument("--dry-run", action="store_true", help="只拉取，不落盘")
    ap.add_argument("--stats", action="store_true", help="只看现有索引概况，不联网")
    args = ap.parse_args()

    if args.stats:
        if not market_index.INDEX_PATH.exists():
            print(f"✗ 索引不存在：{market_index.INDEX_PATH}")
            return 1
        df = market_index.load_index()
        s = market_index.stats(df)
        print(json.dumps(s, ensure_ascii=False, indent=2))
        print("\n样例（各市场前 5 只）：")
        for mkt, g in df.groupby("market"):
            print(f"  {mkt}: " + ", ".join(f"{r.symbol} {r['name']}" for _, r in g.head(5).iterrows()))
        return 0

    df = market_index.build_index(save=not args.dry_run)
    if args.dry_run:
        print("[dry-run] 未落盘")
        return 0

    print("[duckdb] " + str(write_duckdb(df)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
