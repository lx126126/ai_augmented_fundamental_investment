#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跟踪池命令行入口：查看 / 手工入池 / **移出池** / 同步 .gitignore 白名单。

为什么需要「移出」这一半
------------------------
2026-09-17 把入池规则改成「生成过报告即入池」（自动，不设硬上限）—— 这解决了
「生成过的报告在网页上消失」，但**只做了加法，没有减法**。

2026-09-18 实测后果：随手试生成一只（长江电力 600900）就**永久留在池子里**，
并连带三处污染，且全程无提示：

1. 首页多一张卡片、对比表多一列（页面产物要手工重建才同步）
2. `data/raw/<code>/` 留下一份冷数据
3. `.gitignore` 的跟踪池白名单要**手工**补一行，忘了 → 报告不进版本库
   （`reports/**/*.html` 默认全忽略，靠 `!reports/**/<code>.html` 逐只放行）

本脚本补齐减法，并把「重建派生网页」和「同步白名单」一起做掉 —— 这三件事
本来就是一体的，拆开做必然漏。

移出是**软删**（`status` 置 `removed`，保留名称/行业/颜色）
------------------------------------------------------------------
不是洁癖：`build_web_index` 对「有报告但不在池里」有一张兜底卡片（防入池链路静默断掉）。
硬删条目会让**刻意移出**与**链路故障**长得一模一样，兜底卡片便照旧显示被移出的标的 ——
2026-09-18 实测：硬删 600900 后首页仍有它的卡片，名称还退化成裸代码 `600900`。
留一个 `status` 就能分开：移出的排除在兜底外，链路故障的照旧兜底。想彻底删用
`watchlist_store.prune()`。

用法
----
    python scripts/watchlist.py list                     # 查看当前池（含已移出）
    python scripts/watchlist.py add 600900               # 手工入池（名称/行业自动查）
    python scripts/watchlist.py remove 600900            # 移出池（软删，可多个代码）
    python scripts/watchlist.py remove 600900 -r "试功能" # 带移出原因（回看用）
    python scripts/watchlist.py restore 600900           # 恢复入池（撤回移出）
    python scripts/watchlist.py sync-gitignore           # 只同步白名单，不重建网页
    python scripts/watchlist.py remove 600900 --no-rebuild   # 只改清单，不重建网页

⚠️ 移出池**只改清单，不删磁盘文件**。报告/raw/行情归档的清理单独提示、由人决定 ——
   报告是「按需生成」的产物，留着不代表入池，删掉也不影响（可重新生成）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data import watchlist_store as wl  # noqa: E402

SCRIPTS = ROOT / "scripts"
GITIGNORE = ROOT / ".gitignore"

#: 池子变动后必须重建的派生产物：(脚本, 超时秒)。
#: 与 `web/server.py` 的 `_DERIVED_STEPS` **同源** —— 首页和对比表是一组，不能只重建一个。
_DERIVED = (("build_web_index.py", 300), ("build_watchlist.py", 1800))

#: `.gitignore` 白名单段的边界标记。收在同一行、可 grep，改由脚本整段重写。
_BEGIN = "# >>> 跟踪池报告白名单"
_END = "# <<< 跟踪池报告白名单 <<<"


# --------------------------------------------------------------------------- #
# 白名单同步
# --------------------------------------------------------------------------- #

def _whitelist_block(codes: list[str]) -> str:
    lines = [
        f"{_BEGIN} —— 由 `python scripts/watchlist.py sync-gitignore` 生成，勿手改 >>>",
        "# 机制：reports/ 下的报告**默认全部忽略**（按需查询的随手标的没有归档价值，",
        "#       否则仓库随使用无限膨胀）；只有「跟踪池」标的的报告入库。",
        "# 真源：watchlist/watchlist.json。要增删标的请改那份 json 再跑 sync-gitignore ——",
        "#       **手工在这里加一行会在下次 sync 时被覆盖**。",
        "# 漂移防线：tests/test_watchlist_store.py 有一条断言，白名单与跟踪池不一致就报错。",
        "reports/**/*.html",
    ]
    lines += [f"!reports/**/{c}.html" for c in codes]
    lines.append(_END)
    return "\n".join(lines)


def sync_gitignore(quiet: bool = False) -> bool:
    """把白名单段重写为跟踪池当前内容。返回是否有改动。"""
    text = GITIGNORE.read_text(encoding="utf-8") if GITIGNORE.exists() else ""
    block = _whitelist_block(wl.codes())

    # 已有标记段 → 整段替换（连同它上面的旧注释一起吃掉，避免注释残留堆积）
    pat = re.compile(rf"^(?:#[^\n]*\n)*{re.escape(_BEGIN)}.*?^{re.escape(_END)}[^\n]*$",
                     re.M | re.S)
    if pat.search(text):
        new = pat.sub(lambda _m: block, text, count=1)
    else:
        # 首次迁移：没有标记段 → 把旧的手工白名单区整块换成标记段。
        # ⚠️ 旧区里注释与白名单行是**交替**的（`reports/**/*.html` 之后先跟一行
        #    `# 跟踪池（...）`，再是 `!reports/**/*.html`，中间还夹着「中国海油」注释）。
        #    只匹配 `!reports/...` 会出现「替换掉了注释、白名单行却全留在下面」的
        #    半替换（首次实测踩到）—— 所以两种行都要吃，直到遇到空行才停（挡住下文
        #    `templates/valueline.html` 那段无关注释）。
        old = re.compile(
            r"^(?:#[^\n]*\n)*reports/\*\*/\*\.html\n"
            r"(?:#[^\n]*\n|!reports/\*\*/\S+\.html\n)*",
            re.M)
        new = old.sub(lambda _m: block + "\n", text, count=1)
        if new == text:  # 连旧段也没匹配上 → 追加到文件末尾
            new = text.rstrip("\n") + "\n\n" + block + "\n"

    if new == text:
        if not quiet:
            print(f"· .gitignore 白名单已是最新（{len(wl.codes())} 只），未改动")
        return False
    GITIGNORE.write_text(new, encoding="utf-8")
    if not quiet:
        print(f"✓ .gitignore 白名单已同步：{len(wl.codes())} 只")
    return True


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

def cmd_list(_args) -> int:
    items = wl.stocks()
    if not items:
        print("跟踪池为空")
        return 0
    print(f"跟踪池 {len(items)} 只（rules.max_size = {wl.max_size()}，仅页面提示、不阻断入池）\n")
    for i, s in enumerate(items, 1):
        print(f"  {i:>2}. {s['code']:<11} {s.get('name', ''):<10} "
              f"│ {s.get('industry', ''):<10} │ {s.get('lynch', '')}"
              f"  {s.get('color', '')}  ({s.get('source', '')}, {s.get('since', '')})")

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
                  f"（{s.get('industry')} · {s.get('lynch')}）")
        else:
            print(f"· 已在池中或代码非法，跳过：{code}")
    if not added:
        return 0
    sync_gitignore()
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
    sync_gitignore()
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

    sync_gitignore()
    if args.no_rebuild:
        print("（--no-rebuild：已跳过网页重建，页面仍是旧内容）")
        return 0
    print("\n重建派生产物：")
    return 0 if rebuild() else 1


def cmd_sync(args) -> int:
    sync_gitignore(quiet=args.quiet)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="fqf 跟踪池管理（查看 / 入池 / 移出 / 同步 .gitignore 白名单）",
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

    p_sync = sub.add_parser("sync-gitignore", help="把 .gitignore 白名单同步成跟踪池内容")
    p_sync.add_argument("--quiet", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
