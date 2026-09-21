#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跟踪池命令行入口：查看 / 手工入池 / **移出池** / Lynch 口径回填。

为什么需要「移出」这一半
------------------------
2026-09-17 把入池规则改成「生成过报告即入池」（自动，不设硬上限）—— 这解决了
「生成过的报告在网页上消失」，但**只做了加法，没有减法**。

2026-09-18 实测后果：随手试生成一只（长江电力 600900）就**永久留在池子里**，
并连带三处污染，且全程无提示：

1. 首页多一张卡片、对比表多一列（页面产物要手工重建才同步）
2. `data/raw/<code>/` 留下一份冷数据
3. 本地 `reports/` 多一份报告产物（**不入库**，见下）

本脚本补齐减法，并把「重建派生网页」一起做掉 —— 这两件事本来就是一体的，拆开做必然漏。

⚠️ 第 3 项在 2026-09-18 之前更严重：当时 `reports/**/*.html` 默认全忽略、靠
`!reports/**/<code>.html` 逐只拉链式放行，**忘了补白名单 → 报告静默不入库（零报错）**。
现在 `reports/` 已整目录出库，那条漂移风险连同「同步白名单」这一步一起消掉了。

移出是**软删**（`status` 置 `removed`，保留名称/行业/颜色）
------------------------------------------------------------------
不是洁癖：`build_web_index` 对「有报告但不在池里」有一张兜底卡片（防入池链路静默断掉）。
硬删条目会让**刻意移出**与**链路故障**长得一模一样，兜底卡片便照旧显示被移出的标的 ——
2026-09-18 实测：硬删 600900 后首页仍有它的卡片，名称还退化成裸代码 `600900`。
留一个 `status` 就能分开：移出的排除在兜底外，链路故障的照旧兜底。想彻底删用
`watchlist_store.prune()`。

Lynch 分类：报告口径是唯一真源，且值必须收成 6 个规范值（2026-09-18 两次定）
-----------------------------------------------------------------------------
① 同一类别原先在项目里**有两套实现、产出不同字符串**：

| 来源 | 实现 | 举例（长江电力 600900） |
|---|---|---|
| 报告里显示的 | LLM 按真实业务判（`src/report/llm.py` 的 `lynch_type`） | 稳健**成长**型 |
| 跟踪池/对比表里的 | `watchlist_store.classify_lynch()` 关键字命中行业名 | 稳健**增长**型 · 收息 |

同一个标的在两处显示不同归类 —— 结构性冲突，不是 600900 特有。已定：**以报告为准**
（LLM 读了业务构成，比关键字猜行业名可靠；且报告是用户直接看的那份）。

② 但「以报告为准」只解决**谁说了算**，没解决**说法不统一**：LLM 是自由输出的，
同一天同一池子里出现了 `稳健成长` / `稳健成长型` / `稳健增长型 · 收息` 三种写法，
对比表把这几只排在一起时，同一类别看起来像三个类别。所以再收一层：

- **分类值冻结为 `src/review/lynch.py` 的 `CANONICAL_TYPES` 六个**，全仓唯一口径
- **注解走独立字段 `lynch_note`**（`周期型` + `高股息现金牛`），不再混在分类字符串里
- prompt 已改成「必须原样输出 6 值之一」（`src/report/llm.py`）；
  存量缓存靠 `normalize()` 在**渲染时**兜住 —— 不重调 LLM、不花钱
- 落库端 `watchlist_store._normalize_lynch()` 是最后一道闸，
  认不出的值**保留原文**（界面上看得出来）而不是套个默认值

落地：`build_valueline.build()` 在**真实数据**分支结束时调
`watchlist_store.upsert_from_report()` 回写；`classify_lynch()` 降级为
「标的还没生成过报告时的占位值」。存量标的用本脚本的 `sync-lynch` 补一次。

用法
----
    python scripts/watchlist.py list                     # 查看当前池（含已移出）
    python scripts/watchlist.py add 600900               # 手工入池（名称/行业自动查）
    python scripts/watchlist.py remove 600900            # 移出池（软删，可多个代码）
    python scripts/watchlist.py remove 600900 -r "试功能" # 带移出原因（回看用）
    python scripts/watchlist.py restore 600900           # 恢复入池（撤回移出）
    python scripts/watchlist.py sync-lynch               # 全池 Lynch 按报告口径回填
    python scripts/watchlist.py remove 600900 --no-rebuild   # 只改清单，不重建网页

⚠️ 移出池**只改清单，不删磁盘文件**。报告/raw/行情归档的清理单独提示、由人决定 ——
   报告是「按需生成」的产物，留着不代表入池，删掉也不影响（可重新生成）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import watchlist_store as wl  # noqa: E402

SCRIPTS = ROOT / "scripts"

#: 池子变动后必须重建的派生产物：(脚本, 超时秒)。
#: 与 `web/server.py` 的 `_DERIVED_STEPS` **同源** —— 首页和对比表是一组，不能只重建一个。
_DERIVED = (("build_web_index.py", 300), ("build_watchlist.py", 1800))


# --------------------------------------------------------------------------- #
# 派生网页重建
# --------------------------------------------------------------------------- #

def rebuild() -> bool:
    """重建首页 + 对比表。任一失败不影响另一件，返回是否全成功。"""
    ok_all = True
    for script, timeout in _DERIVED:
        print(f"  重建 {script} … ", end="", flush=True)
        try:
            r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
            good = r.returncode == 0
            print("成功" if good else "失败")
            if not good:
                print(f"    stderr: {(r.stderr or '').strip()[-300:]}")
        except Exception as e:
            good = False
            print(f"异常：{type(e).__name__}: {e}")
        ok_all = ok_all and good
    return ok_all


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #

def _lynch_label(s: dict) -> str:
    """列表里显示的 Lynch 分类：`规范值 · 注解`（注解可缺，缺就只显示规范值）。

    ⚠️ 这里**刻意不再做一次归一化** —— `watchlist_store` 已经保证落库的 `lynch`
    是 6 个规范值之一，显示层再归一化一遍就会出现「json 里一个值、界面另一个值」，
    排查时根本看不出是哪一层改的。分类值真源在 `src/review/lynch.py`。
    """
    lynch = (s.get("lynch") or "").strip()
    note = (s.get("lynch_note") or "").strip()
    if not lynch:
        return "—"
    return f"{lynch} · {note}" if note else lynch


def cmd_list(_args) -> int:
    items = wl.stocks()
    if not items:
        print("跟踪池为空")
        return 0
    print(f"跟踪池 {len(items)} 只（不设上限 —— 生成过报告即入池）\n")
    for i, s in enumerate(items, 1):
        # `source` / `since` 是老条目可能没有的字段（它们入池时这套元数据还没引入）
        # —— 缺值必须兜底成 `—`，否则打印成 `(, 2026-08)`，看起来像字段坏了。
        src = s.get("source") or "—"
        since = s.get("since") or "—"
        print(f"  {i:>2}. {s['code']:<11} {s.get('name', ''):<10} "
              f"│ {s.get('industry', ''):<10} │ {_lynch_label(s)}"
              f"  {s.get('color', '')}  ({src}, {since})")

    gone = [s for s in wl.stocks(include_removed=True) if s.get("status") == "removed"]
    if gone:
        print(f"\n已移出 {len(gone)} 只（软删留痕，`restore` 可撤回）：")
        for s in gone:
            why = f"  原因：{s['removed_reason']}" if s.get("removed_reason") else ""
            print(f"      {s['code']:<11} {s.get('name', ''):<10} "
                  f"移出于 {s.get('removed_at', '—')}{why}")
    return 0


def cmd_add(args) -> int:
    added = []
    for code in args.codes:
        if wl.add(code, source="manual"):
            s = wl.get(code) or {}
            added.append(code)
            print(f"✓ 已入池：{s.get('code')} {s.get('name')}"
                  f"（{s.get('industry')} · {_lynch_label(s)}）")
        else:
            print(f"· 已在池中或代码非法，跳过：{code}")
    if not added:
        return 0
    if args.no_rebuild:
        print("（--no-rebuild：已跳过网页重建，页面仍是旧内容）")
        return 0
    print("重建派生产物：")
    return 0 if rebuild() else 1


def cmd_restore(args) -> int:
    done = []
    for code in args.codes:
        s = wl.get(code, include_removed=True)
        if not s:
            print(f"· 不在池中，跳过：{code}")
            continue
        if s.get("status") != "removed":
            print(f"· 本就在池中，跳过：{s['code']} {s.get('name', '')}")
            continue
        if wl.restore(code):
            done.append(code)
            print(f"✓ 已恢复入池：{s['code']} {s.get('name', '')}")
    if not done:
        return 0
    if args.no_rebuild:
        print("（--no-rebuild：已跳过网页重建，页面仍是旧内容）")
        return 0
    print("\n重建派生产物：")
    return 0 if rebuild() else 1


def cmd_remove(args) -> int:
    removed: list[dict] = []
    for code in args.codes:
        s = wl.get(code, include_removed=True)
        if not s:
            print(f"· 不在池中，跳过：{code}")
            continue
        if s.get("status") == "removed":
            print(f"· 已是移出状态，跳过：{s['code']} {s.get('name', '')}")
            continue
        out = wl.remove(code, reason=args.reason or "")
        if out:
            removed.append(out)
            print(f"✓ 已移出：{out['code']} {out.get('name', '')}")
    if not removed:
        return 0

    # 磁盘残留提示 —— 只提示，不删（删除是不可逆动作，交给人决定）
    for s in removed:
        b = s["bare"]
        left: list[str] = []
        left += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "reports").glob(f"*/{b}.html"))]
        raw = ROOT / "data" / "raw" / b
        if raw.exists():
            left.append(f"{raw.relative_to(ROOT)}/  （{len(list(raw.glob('*.parquet')))} 张表）")
        left += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "data" / "market").glob(f"{b}_*.parquet"))]
        if left:
            print(f"\n⚠ {b} 的磁盘文件仍在（移出池只改清单，不删数据）：")
            for p in left:
                print(f"    {p}")

    if args.no_rebuild:
        print("（--no-rebuild：已跳过网页重建，页面仍是旧内容）")
        return 0
    print("\n重建派生产物：")
    return 0 if rebuild() else 1


# --------------------------------------------------------------------------- #
# Lynch 分类对齐（报告口径 → 跟踪池）
# --------------------------------------------------------------------------- #

#: 叙事层缓存目录（`build_valueline` 写、这里读）。两者必须指向同一处：
#: 它是 LLM 判定值的落地点，也就是报告里显示的那个 lynch_type 的真源。
NARRATIVE_CACHE = ROOT / "data" / "cache" / "narrative"


def _cached_lynch(code: str) -> tuple[str, str, str]:
    """从叙事缓存读该标的的报告口径 `(lynch_type, lynch_note, industry)`；读不到返回三个空串。

    返回的 `lynch_type` 是**LLM 的自由写法**（`周期型（高股息现金牛）` 这类），
    不需要在这里归一化 —— `wl.update_fields()` 会收成规范值、把注解拆到 `lynch_note`。
    """
    p = NARRATIVE_CACHE / f"{wl.bare(code)}.json"
    if not p.exists():
        return "", "", ""
    try:
        narr = (json.loads(p.read_text(encoding="utf-8")) or {}).get("narrative") or {}
        return (str(narr.get("lynch_type") or ""),
                str(narr.get("lynch_note") or ""),
                str(narr.get("industry") or ""))
    except Exception:
        return "", "", ""


def cmd_sync_lynch(args) -> int:
    """把跟踪池的 Lynch 分类对齐到报告口径（LLM 判定），并收成 6 个规范值。"""
    targets = args.codes or wl.codes()
    if not targets:
        print("跟踪池为空，无事可做")
        return 0

    rows, skipped = [], []
    for code in targets:
        s = wl.get(code, include_removed=True)
        if not s:
            skipped.append((wl.bare(code), "不在池中"))
            continue
        if s.get("status") == "removed":
            skipped.append((wl.bare(code), "已移出（刻意移出的不因缓存回填而复活）"))
            continue
        lynch, note, industry = _cached_lynch(code)
        if not lynch:
            skipped.append((wl.bare(code), "无叙事缓存 —— 该标的还没生成过完整报告"))
            continue
        before = _lynch_label(s)
        wl.update_fields(code, lynch=lynch, lynch_note=note, industry=industry)
        after = _lynch_label(wl.get(code) or {})
        rows.append((wl.bare(code), s.get("name", ""), before, after))

    changed = [r for r in rows if r[2] != r[3]]
    print(f"Lynch 分类对齐：处理 {len(rows)} 只，其中 {len(changed)} 只有变化"
          f"（口径 = 报告里 LLM 判定的值，已归一化为 6 个规范值）\n")
    for code, name, before, after in rows:
        mark = "✎" if before != after else " "
        print(f"  {mark} {code:<8} {name:<8} {before or '(空)':<22} → {after or '(空)'}")

    if skipped:
        print(f"\n跳过 {len(skipped)} 只：")
        for code, why in skipped:
            print(f"    {code:<8} {why}")

    if not changed:
        print("\n无字段变化，未重写文件、未重建网页。")
        return 0
    if args.no_rebuild:
        print("\n（--no-rebuild：已跳过网页重建，页面仍是旧内容）")
        return 0
    print("\n重建派生产物：")
    return 0 if rebuild() else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="fqf 跟踪池管理（查看 / 入池 / 移出 / 用报告口径回填字段）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="查看当前跟踪池").set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="手工入池（名称/行业自动查）")
    p_add.add_argument("codes", nargs="+", help="股票代码，可给多个")
    p_add.add_argument("--no-rebuild", action="store_true", help="不重建网页产物")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", help="移出跟踪池（软删，只改清单，不删磁盘文件）")
    p_rm.add_argument("codes", nargs="+", help="股票代码，可给多个")
    p_rm.add_argument("-r", "--reason", default="", help="移出原因（写进 json，回看用）")
    p_rm.add_argument("--no-rebuild", action="store_true", help="不重建网页产物")
    p_rm.set_defaults(func=cmd_remove)

    p_rs = sub.add_parser("restore", help="恢复入池（撤回一次 remove）")
    p_rs.add_argument("codes", nargs="+", help="股票代码，可给多个")
    p_rs.add_argument("--no-rebuild", action="store_true", help="不重建网页产物")
    p_rs.set_defaults(func=cmd_restore)

    p_ln = sub.add_parser("sync-lynch",
                          help="用报告口径（LLM 判定）回填 Lynch 分类；不带代码=全池")
    p_ln.add_argument("codes", nargs="*", help="股票代码，留空则处理全池")
    p_ln.add_argument("--no-rebuild", action="store_true", help="不重建网页产物")
    p_ln.set_defaults(func=cmd_sync_lynch)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
