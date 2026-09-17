#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日行情刷新：拉行情快照 → 轻量重刷报告（跳过 PDF 校验与 LLM 叙事）。

为什么用 launchd 而不是容器化调度平台
--------------------------------------
本机（MacBookAir7,2 / 8GB 内存 / 双核）跑不了「元数据库 + 常驻 scheduler +
webserver」那一整套（需 3~4GB 常驻内存）。而本场景的负载是「每天跑一次、
一个人看」—— 平台能力用不上，开销全额承担。

故直接由 macOS 原生 launchd 在每交易日 16:30 拉起本脚本，三个步骤：

    market_snapshot.snapshot_all       → 拉行情/估值/评级快照
    build_valueline.build(daily=True)  → 轻量重刷估值板块
    build_web_index.py                 → 重生成网页首页（子进程调用）

口径差异（重要）
----------------
- **A 股**：`snapshot_all` 更新 quote + valuation（百度估值）+ rating 三张表；
- **港股**：百度估值无港股接口（`snapshot_valuation` 对非 A 股直接返回 None），
  只更新 quote（腾讯快照）。但报告里港股的市值/PE/PB 正是由 quote 的市值驱动、
  PE/PB 随之重算 —— 所以只刷 quote 已足以刷新估值板块。

为什么必须带 `--daily`
----------------------
`build_valueline.build()` 的叙事层缓存键是 facts-hash，而 **facts 里含估值与市值**：
行情一变哈希即变，缓存自动失效 → 会重新调用 LLM（烧 token）。
`--daily` 会跳过 PDF 校验与 LLM，只重刷估值/市场板块。**每日刷新一律用 --daily。**

用法
----
    python scripts/daily_refresh.py                  # 跟踪池全部标的
    python scripts/daily_refresh.py --codes 600938,00883
    python scripts/daily_refresh.py --dry-run        # 只列计划，不拉数
    python scripts/daily_refresh.py --no-build       # 只拉行情，不重刷报告
    python scripts/daily_refresh.py --no-web         # 不刷手机网页版首页

退出码：0 = 全部成功；1 = 存在失败（供 launchd / 告警判定）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# scripts/ 也要进 path：build_valueline.py 里是裸导入 `from _sample_data import ...`
sys.path.insert(0, str(SCRIPTS))

WATCHLIST = ROOT / "watchlist" / "watchlist.json"
RAW_DIR = ROOT / "data" / "raw"


def _is_hk(code: str) -> bool:
    """港股判定：剥后缀后 0 开头的 5 位码（与 fetch_stock.py 同口径）。"""
    c = str(code).upper().split(".")[0]
    return c.startswith("0") and len(c) == 5


def _store_code(code: str) -> str:
    """归一为 parquet 目录名：A 股 zfill(6)，港股保留 5 位。"""
    c = str(code).upper().split(".")[0]
    return c if _is_hk(c) else c.zfill(6)


def load_codes() -> list[str]:
    """读跟踪池；文件缺失或为空时回退扫描 data/raw 已落盘标的。"""
    if WATCHLIST.exists():
        try:
            data = json.loads(WATCHLIST.read_text(encoding="utf-8"))
            codes = [s["code"] for s in data.get("stocks", []) if s.get("code")]
            if codes:
                return codes
        except Exception as e:
            print(f"[warn] 读跟踪池失败，回退扫描 data/raw：{e}")
    if RAW_DIR.exists():
        return sorted(p.name for p in RAW_DIR.iterdir() if p.is_dir())
    return []


def _parse_args(argv: list[str]) -> dict:
    codes = None
    for i, a in enumerate(argv):
        if a == "--codes" and i + 1 < len(argv):
            codes = [c.strip() for c in argv[i + 1].split(",") if c.strip()]
    return {
        "dry_run": "--dry-run" in argv,
        "do_build": "--no-build" not in argv,
        "do_web": "--no-web" not in argv,
        "codes": codes,
    }


def main() -> int:
    opt = _parse_args(sys.argv[1:])
    codes = opt["codes"] or load_codes()

    if not codes:
        print("❌ 没有可刷新的标的（跟踪池为空且 data/raw 不存在）")
        return 1

    print(f"每日行情刷新 · {len(codes)} 只标的"
          f"{'（dry-run，不实际拉取）' if opt['dry_run'] else ''}")
    for c in codes:
        st = _store_code(c)
        print(f"  - {c}  →  {st}  [{'港股' if _is_hk(st) else 'A 股'}]")
    if opt["dry_run"]:
        print("\n计划：")
        print("  1) market_snapshot.snapshot_all  → 追加行情历史 + 覆盖最新表")
        if opt["do_build"]:
            print("  2) build_valueline.build(daily=True)  → 重刷估值/市场板块（不调 LLM）")
        if opt["do_web"] and opt["do_build"]:
            print("  3) build_web_index.py  → 重刷 web/index.html")
        return 0

    snapshot_all = None
    build_report = None
    try:
        # 导入即触发 fetcher._force_direct_connection()（沙箱/代理环境下必须）
        from src.data.market_snapshot import snapshot_all as _sa
        snapshot_all = _sa
    except Exception as e:
        print(f"❌ 导入行情层失败：{e}")
        return 1
    if opt["do_build"]:
        try:
            from build_valueline import build as _b
            build_report = _b
        except Exception as e:
            print(f"⚠️ 导入报告层失败，将跳过重刷报告：{e}")

    failed: list[str] = []
    for code in codes:
        st = _store_code(code)
        hk = _is_hk(st)
        print(f"\n{'=' * 56}\n[{st}] 行情快照（{'港股' if hk else 'A 股'}）")

        try:
            r = snapshot_all(st, market="hk" if hk else None)
            mark = "" if r.get("quote_ok") is not False else "  ⚠️ 行情校验告警"
            print(f"  quote={r.get('quote')}  valuation={r.get('valuation')}"
                  f"  rating={r.get('rating')}{mark}")
            for c in (r.get("quote_checks") or []):
                print(f"    - {c.get('name', '')}: {c.get('detail', '')}")
            if not r.get("quote"):
                failed.append(f"{st}:行情")
        except Exception as e:
            print(f"  ❌ 行情快照失败：{e}")
            traceback.print_exc()
            failed.append(f"{st}:行情")
            continue

        if build_report is not None:
            try:
                print(f"[{st}] 重刷报告（--daily：跳过 PDF 校验与 LLM）")
                build_report(st, daily=True)
            except Exception as e:
                print(f"  ❌ 重刷报告失败：{e}")
                traceback.print_exc()
                failed.append(f"{st}:报告")

    if opt["do_web"] and opt["do_build"]:
        print(f"\n{'=' * 56}\n重刷手机网页版首页")
        try:
            r = subprocess.run([sys.executable, str(SCRIPTS / "build_web_index.py")],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=300)
            print((r.stdout or "").strip()[-800:])
            if r.returncode != 0:
                print(f"  ⚠️ 退出码 {r.returncode}：{(r.stderr or '').strip()[-400:]}")
                failed.append("web_index")
        except Exception as e:
            print(f"  ⚠️ 首页刷新失败（不影响报告）：{e}")
            failed.append("web_index")

    print(f"\n{'=' * 56}")
    if failed:
        print(f"❌ 完成，但有 {len(failed)} 项失败：{', '.join(failed)}")
        return 1
    print("✅ 全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
