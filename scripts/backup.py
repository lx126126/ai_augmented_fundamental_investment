# -*- coding: utf-8 -*-
"""备份不可再生的易失数据：投研日记 + 工作记忆。

背景（潇姐 2026-09-08）：journal/ 和 .workbuddy/memory/ 都是主观/积累产物，
一旦误删或磁盘损坏不可再生；而 data/raw、data/warehouse 是可再生的（重跑
fetch/warehouse 即可），不备份。

设计原则：
- 只备份「不可再生的易失数据」，不备份「可再生数据」（省空间、聚焦真风险）
- .env 含 API key，属密钥，不自动打进明文备份包（单独提示，由用户决定）
- 滚动保留最近 N 份，避免备份目录无限膨胀
- 备份产物落 backups/（已 gitignore），不污染仓库

用法：
    python scripts/backup.py                 # 备份一次
    python scripts/backup.py --list          # 列出现有备份
    python scripts/backup.py --keep 10       # 备份并滚动保留最近 10 份
    python scripts/backup.py --restore 最新  # 查看如何恢复（提示性输出）
"""
from __future__ import annotations

import argparse
import sys
import tarfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backups"

# 备份目标：不可再生的易失数据（主观产物 + 跨会话积累）
TARGETS: list[tuple[str, str]] = [
    ("journal", "投研日记（主观判断，gitignore，丢了不可再生）"),
    (".workbuddy/memory", "工作记忆（日志 + MEMORY.md，跨会话复利积累）"),
]

# 明确不备份：data/raw、data/warehouse 可再生；.env 含密钥单独处理
SKIP_REASON = {
    "data/raw": "可再生（重跑 fetch_stock）",
    "data/warehouse": "可再生（重跑 warehouse）",
    ".env": "含 API key，属密钥，请用密码管理器单独备份，不进明文包",
}


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _missing_targets() -> list[str]:
    """返回当前不存在的目标（用于提示，不阻断备份其余目标）。"""
    missing = []
    for rel, _desc in TARGETS:
        if not (ROOT / rel).exists():
            missing.append(rel)
    return missing


def create_backup(keep: int = 10) -> Path:
    """打包备份目标，返回生成的 tar.gz 路径。"""
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = _now_stamp()
    out = BACKUP_DIR / f"fqf_backup_{stamp}.tar.gz"

    present = [(r, d) for r, d in TARGETS if (ROOT / r).exists()]
    if not present:
        print("[backup] 没有可备份的目标（journal 和 memory 都不存在）")
        raise SystemExit(1)

    with tarfile.open(out, "w:gz") as tar:
        for rel, _desc in present:
            path = ROOT / rel
            tar.add(path, arcname=rel)

    size_mb = out.stat().st_size / 1024 / 1024
    print(f"[backup] 已生成 {out.name}（{size_mb:.2f} MB）")
    print(f"[backup] 包含 {len(present)} 类目标：")
    for rel, desc in present:
        print(f"    - {rel}: {desc}")

    missing = [r for r, _d in TARGETS if (ROOT / r).exists() is False]
    if missing:
        print(f"[backup] ⚠️ 未包含（不存在，跳过）：{', '.join(missing)}")

    _prune(keep)
    return out


def _prune(keep: int) -> None:
    """滚动保留最近 keep 份，删除更旧的。"""
    backups = sorted(BACKUP_DIR.glob("fqf_backup_*.tar.gz"))
    if len(backups) <= keep:
        return
    for old in backups[:-keep]:
        old.unlink()
        print(f"[backup] 滚动清理旧备份：{old.name}")


def list_backups() -> None:
    backups = sorted(BACKUP_DIR.glob("fqf_backup_*.tar.gz"), reverse=True)
    if not backups:
        print("[backup] 暂无备份")
        return
    print(f"[backup] 现有 {len(backups)} 份备份：")
    for b in backups:
        size_mb = b.stat().st_size / 1024 / 1024
        print(f"    {b.name}  ({size_mb:.2f} MB)")


def show_restore_hint() -> None:
    """提示性输出：如何恢复（不自动恢复，避免误覆盖现有数据）。"""
    print("[backup] 恢复方法（手动，避免误覆盖现有数据）：")
    print("    1. 列出内容: tar -tzf backups/fqf_backup_<时间戳>.tar.gz")
    print("    2. 恢复:     tar -xzf backups/fqf_backup_<时间戳>.tar.gz -C <目标目录>")
    print("    3. 注意: 解压会生成 journal/ 和 .workbuddy/memory/ 目录，请先确认不覆盖现有文件")


def main() -> None:
    parser = argparse.ArgumentParser(description="备份投研日记 + 工作记忆")
    parser.add_argument("--list", action="store_true", help="列出现有备份")
    parser.add_argument("--keep", type=int, default=10, help="滚动保留最近 N 份（默认 10）")
    parser.add_argument("--restore", action="store_true", help="显示恢复方法（提示性，不自动恢复）")
    args = parser.parse_args()

    if args.list:
        list_backups()
        return
    if args.restore:
        show_restore_hint()
        return

    create_backup(keep=args.keep)

    # 附上 .env 提示（密钥不进明文包）
    if (ROOT / ".env").exists():
        print("[backup] 提示：.env 含 API key，未打包。请用密码管理器单独备份。")


if __name__ == "__main__":
    main()
