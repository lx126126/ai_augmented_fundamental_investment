#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日行情刷新：拉行情快照 → 轻量重刷报告（跳过 PDF 校验与 LLM 叙事）。

为什么用 launchd 而不是容器化调度平台
--------------------------------------
本机（MacBookAir7,2 / 8GB 内存 / 双核）跑不了「元数据库 + 常驻 scheduler +
webserver」那一整套（需 3~4GB 常驻内存）。而本场景的负载是「每天跑一次、
一个人看」—— 平台能力用不上，开销全额承担。

故直接由 macOS 原生 launchd 在每交易日 16:30 拉起本脚本，四个步骤：

    market_snapshot.snapshot_all       → 拉行情/估值/评级快照
    build_valueline.build(daily=True)  → 轻量重刷估值板块
    build_web_index.py                 → 重生成网页首页（子进程调用）
    build_watchlist.py                 → 重生成跟踪池横向对比表（子进程调用）

🔴 为什么要重建对比表（2026-09-17 补）
--------------------------------------
原先只重建 `web/index.html`，**对比表根本不在日更链路里** —— 它的价格永远停在
「上次手动运行 build_watchlist.py 的那一刻」。用户反馈「你说 4 点会更新数据，
我感知不到」，一半原因就在这里：唯一带完整数字的页面从来不更新。

🔴 派生产物失败不再计入失败（2026-09-17 改，与原行为不同）
--------------------------------------------------------
两个展示步骤（首页 / 对比表）失败一律**只告警、不计入 `failed`**，因此不会挡
`.last_success` 闸门。理由：它们是派生产物，单独重跑成本很低（对比表约 2.5 分钟、
首页约 2 秒），而计入失败会让**当天整批标的重新拉一遍**（20+ 分钟）——
代价与收益完全不匹配。原实现把 web_index 失败也算作失败，本次一并改掉。

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

import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# scripts/ 也要进 path：build_valueline.py 里是裸导入 `from _sample_data import ...`
sys.path.insert(0, str(SCRIPTS))

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
    """读跟踪池；文件缺失或为空时回退扫描 data/raw 已落盘标的。

    🔴 **必须走 `watchlist_store.codes()`，不能直接读 json 取全部 code**。
    2026-09-18 加软删（`status: removed` 留痕、`restore` 可撤回）之后，直接读 json
    会把**已移出的标的也算进来** —— 实测 300061 / 600900 移出后每天仍被刷新：
    白拉行情、白刷报告，且「移出池」这件事在数据层从未生效（页面上看不出、
    日志里也不报错，只是每天多干两份活）。
    """
    try:
        from src.data import watchlist_store as wl
        codes = wl.codes()  # 内部已过滤 status == "removed"
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


def _run_derived(name: str, script: str, timeout: int) -> bool:
    """跑派生产物脚本（首页 / 对比表）。**失败只告警，不计入 failed** —— 见模块 docstring。"""
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if r.returncode == 0:
            print(out[-800:] if out else "  (无输出)")
            return True
        print(f"  ⚠️ {name} 退出码 {r.returncode}：{(r.stderr or '').strip()[-500:]}")
        print(f"  ⚠️ 该产物未更新，但不影响当天成功判定；可单独重跑 "
              f"`python scripts/{script}`")
        return False
    except Exception as e:
        print(f"  ⚠️ {name} 刷新失败（不影响报告与闸门）：{type(e).__name__}: {e}")
        return False


def _run_placeholder_audit() -> None:
    """体检：报告里 LLM 生成的内容是否退化成了占位符。

    **只告警、不计入 failed** —— 与派生展示产物同理由（见模块 docstring）：
    这是「内容质量」检查、不是「链路失败」，重跑成本也不过是一两只标的的构建；
    挡住闸门会让当天整批标的重拉一遍（20+ 分钟），代价与收益完全不匹配。

    为什么需要这个检查：日更的 LLM 内容一律走「**只读缓存**」，所以
    **没有缓存的标的永远不会被补上** —— 报告里一直挂着「待生成」，
    而这条链路零报错、日志干净、文件大小正常。
    2026-09-18 实测：11 只池内标的有 5 只的季报解读是占位符，无人知晓。
    详见 docs/report-chains.md F9。
    """
    print("\n报告完整性体检（LLM 内容是否退化为占位符）")
    try:
        from src.report.artifacts import audit_placeholders
        bad = audit_placeholders()
    except Exception as e:
        print(f"  ⚠️ 体检执行失败（不影响闸门）：{type(e).__name__}: {e}")
        return

    if not bad:
        print("  ✓ 池内报告内容完整，无占位符")
        return

    print(f"  ⚠️ {len(bad)} 份报告含占位符（LLM 内容缺失）：")
    for code, blocks in sorted(bad.items()):
        print(f"      {code}: {' / '.join(blocks)}")
    print("  ⚠️ 常见原因：该标的从未跑过不带 --daily 的构建 → LLM 缓存从未落地；")
    print("     而日更只复用缓存，所以永远不会被补上。")
    print("  ⚠️ 修法：python scripts/build_valueline.py <代码>（一次一只）")
    print("     不影响当天成功判定；详见 docs/report-chains.md F9")


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
            print("  2) build_valueline.build(daily=True)  → 重刷估值/市场板块（复用 LLM 缓存）")
        if opt["do_web"] and opt["do_build"]:
            print("  3) build_web_index.py  → 重刷 web/index.html")
            print("  4) build_watchlist.py  → 重刷 web/watchlist.html")
        print("  5) 报告完整性体检（占位符扫描，只告警、不进 failed）")
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
                # ⚠️ 措辞要准：daily 不是「跳过 LLM」，而是「**复用 LLM 缓存**」。
                # 写成「跳过」会让人以为「没重算、但旧内容还在」—— 而 build() 之后
                # 会无条件把整份 HTML 覆盖写回归档，所以「跳过」实际等于**擦掉**
                # （2026-09-17 就是这么把全池报告的叙事层洗成占位符的）。
                print(f"[{st}] 重刷报告（--daily：复用 LLM 缓存，不重算、不联网）")
                build_report(st, daily=True)
            except Exception as e:
                print(f"  ❌ 重刷报告失败：{e}")
                traceback.print_exc()
                failed.append(f"{st}:报告")

    if opt["do_web"] and opt["do_build"]:
        print(f"\n{'=' * 56}\n重刷手机网页版首页")
        _run_derived("web_index", "build_web_index.py", timeout=300)

        print(f"\n{'=' * 56}\n重刷跟踪池横向对比表")
        # 带缓存：只有 data/raw 变动过的标的才重算。日更刚把全池行情刷了新，
        # 所以这里是全量重算（11 只实测约 2.5 分钟）；单独生成一份报告后只重算 1 只。
        _run_derived("watchlist", "build_watchlist.py", timeout=900)

    # 体检放最后：它扫的是刚落盘的报告（前面各步的产物）。
    # 只告警、不进 failed —— 理由见 _run_placeholder_audit 的 docstring。
    _run_placeholder_audit()

    print(f"\n{'=' * 56}")
    if failed:
        print(f"❌ 完成，但有 {len(failed)} 项失败：{', '.join(failed)}")
        return 1
    print("✅ 全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
