# -*- coding: utf-8 -*-
"""单元测试：backup 脚本的核心逻辑（目标选择 / 滚动清理 / .env 排除）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import backup as B


def test_targets_are_irreproducible_only():
    """备份目标必须只含「不可再生」数据，不含可再生的 raw/warehouse。"""
    targets = {rel for rel, _ in B.TARGETS}
    assert "journal" in targets
    assert ".workbuddy/memory" in targets
    # 可再生的数据源/数仓绝不能进备份目标
    assert "data/raw" not in targets
    assert "data/warehouse" not in targets
    # 密钥不进明文包（在 SKIP_REASON 里声明，不自动打包）
    assert ".env" in B.SKIP_REASON


def test_skip_reason_explains_env_and_reproducible():
    """SKIP_REASON 必须解释为什么 .env 和可再生数据不备份。"""
    assert "API key" in B.SKIP_REASON[".env"] or "密钥" in B.SKIP_REASON[".env"]
    assert "可再生" in B.SKIP_REASON["data/raw"]
    assert "可再生" in B.SKIP_REASON["data/warehouse"]


def test_missing_targets_detected(tmp_path, monkeypatch):
    """不存在的目标应被识别并提示（不阻断其余目标备份）。"""
    monkeypatch.setattr(B, "ROOT", tmp_path)
    # tmp_path 下没有 journal/memory，两个都该被判为 missing
    missing = B._missing_targets()
    assert set(missing) == {"journal", ".workbuddy/memory"}


def test_create_backup_packages_present_targets(tmp_path, monkeypatch):
    """存在哪些目标就打包哪些，.env 不打包。"""
    # 构造一个含 journal 和 .env 的假根目录
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "601088").mkdir()
    (tmp_path / "journal" / "601088" / "2026-09.md").write_text("日记内容", encoding="utf-8")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret", encoding="utf-8")

    monkeypatch.setattr(B, "ROOT", tmp_path)
    monkeypatch.setattr(B, "BACKUP_DIR", tmp_path / "backups")

    out = B.create_backup(keep=10)

    assert out.exists()
    # 解包验证：含 journal，不含 .env
    import tarfile
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
        assert any(n.startswith("journal/") for n in names)
        assert not any(".env" in n for n in names)


def test_prune_keeps_newest_n(tmp_path, monkeypatch):
    """滚动清理只保留最近 N 份，删除更旧的。"""
    bdir = tmp_path / "backups"
    bdir.mkdir()
    # 造 5 份备份
    for i in range(5):
        (bdir / f"fqf_backup_20260908_00000{i}.tar.gz").write_bytes(b"x")

    monkeypatch.setattr(B, "BACKUP_DIR", bdir)
    B._prune(keep=2)

    remaining = sorted(bdir.glob("fqf_backup_*.tar.gz"))
    assert len(remaining) == 2
    # 保留的应是最新的两份（时间戳最大）
    assert remaining[0].name.endswith("000003.tar.gz")
    assert remaining[1].name.endswith("000004.tar.gz")
