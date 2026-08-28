"""
测试 scanner diff 逻辑 — 验证同名文件在不同子目录下不会互相覆盖
"""
import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class FakeKB:
    def __init__(self, files=None):
        self._files = files or []

    def list_files(self):
        return self._files


def _make_scanner_and_diff(tmpdir, indexed_files):
    from app.rag_app.config import Config
    from app.rag_app.scanner import Scanner
    with patch.object(Config, '_scan_paths_raw', tmpdir), \
         patch.object(Config, 'DATA_DIR', ''):
        cfg = Config()
        scanner = Scanner(config=cfg, kb=FakeKB(indexed_files))
        return scanner.diff()


def test_diff_uses_relative_path_not_filename():
    """同名文件在不同子目录下应各自独立，不应互相覆盖"""
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir1 = Path(tmpdir) / "docs"
        subdir2 = Path(tmpdir) / "notes"
        subdir1.mkdir()
        subdir2.mkdir()

        file1 = subdir1 / "readme.md"
        file2 = subdir2 / "readme.md"
        file1.write_text("# Docs README", encoding="utf-8")
        file2.write_text("# Notes README", encoding="utf-8")

        result = _make_scanner_and_diff(tmpdir, [])

        assert result["new_count"] == 2, f"Expected 2 new files, got {result['new_count']}: {[f['name'] for f in result['new']]}"


def test_diff_deleted_detection_with_relative_paths():
    """已索引文件用相对路径存储时，磁盘存在则不应报 deleted"""
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = Path(tmpdir) / "docs"
        subdir.mkdir()
        f = subdir / "guide.md"
        f.write_text("# Guide", encoding="utf-8")

        indexed_files = [{
            "physical_name": "docs/guide.md",
            "file_name": "guide.md",
            "file_mtime": f.stat().st_mtime,
            "file_size": f.stat().st_size,
        }]

        result = _make_scanner_and_diff(tmpdir, indexed_files)

        assert result["deleted_count"] == 0, f"Should be 0 deleted, got {result['deleted_count']}: {result['deleted']}"
        assert result["new_count"] == 0, f"Should be 0 new, got {result['new_count']}"


def test_diff_backward_compat_with_old_filename_index():
    """旧数据 physical_name 为纯文件名时，diff 仍能匹配"""
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "guide.md"
        f.write_text("# Guide", encoding="utf-8")

        indexed_files = [{
            "physical_name": "guide.md",
            "file_name": "guide.md",
            "file_mtime": f.stat().st_mtime,
            "file_size": f.stat().st_size,
        }]

        result = _make_scanner_and_diff(tmpdir, indexed_files)

        assert result["deleted_count"] == 0, f"Old filename index should still match, got {result['deleted_count']} deleted"
        assert result["new_count"] == 0, f"Should be 0 new, got {result['new_count']}"


def test_diff_detects_modified_by_mtime():
    """文件 mtime 变化应报 modified"""
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "doc.md"
        f.write_text("# v1", encoding="utf-8")

        indexed_files = [{
            "physical_name": "doc.md",
            "file_name": "doc.md",
            "file_mtime": f.stat().st_mtime - 100,
            "file_size": f.stat().st_size,
        }]

        result = _make_scanner_and_diff(tmpdir, indexed_files)

        assert result["modified_count"] == 1, f"Expected 1 modified, got {result['modified_count']}"


def test_make_path_identity_basic():
    """路径身份三件套：rel_path/rel_dir/source_root 规范生成（结构化 v2）"""
    from app.rag_app.scanner import make_path_identity
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        f = root / "a" / "b" / "doc.md"
        f.parent.mkdir(parents=True)
        f.write_text("x", encoding="utf-8")
        rel_path, rel_dir, source_root = make_path_identity(root, f)
        assert rel_path == "a/b/doc.md", f"got {rel_path}"
        assert rel_dir == "a/b", f"got {rel_dir}"
        assert source_root == str(root.resolve()), f"got {source_root}"
        # 根目录文件 → rel_dir = '.'
        f2 = root / "top.md"
        f2.write_text("y", encoding="utf-8")
        rp2, rd2, _ = make_path_identity(root, f2)
        assert rp2 == "top.md"
        assert rd2 == "."


def test_diff_new_files_carry_path_identity():
    """新扫描结果条目必须携带 source_root/rel_dir 身份字段（结构化 v2）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = Path(tmpdir) / "docs" / "guide"
        subdir.mkdir(parents=True)
        f = subdir / "intro.md"
        f.write_text("# Intro", encoding="utf-8")
        result = _make_scanner_and_diff(tmpdir, [])
        assert result["new_count"] == 1, f"got {result['new_count']}"
        item = result["new"][0]
        assert item["source_root"] == str(Path(tmpdir).resolve())
        assert item["rel_dir"] == "docs/guide", f"got {item['rel_dir']}"
        assert item["name"] == "intro.md"


def test_diff_identity_matches_with_source_root():
    """新数据 (source_root, rel_path) 精确身份匹配：同名不同根互不覆盖"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root1 = Path(tmpdir) / "root1"
        root2 = Path(tmpdir) / "root2"
        (root1 / "docs").mkdir(parents=True)
        (root2 / "docs").mkdir(parents=True)
        f1 = root1 / "docs" / "readme.md"
        f2 = root2 / "docs" / "readme.md"
        f1.write_text("# R1", encoding="utf-8")
        f2.write_text("# R2", encoding="utf-8")

        indexed = [{
            "physical_name": "docs/readme.md",
            "file_name": "readme.md",
            "file_mtime": f1.stat().st_mtime,
            "file_size": f1.stat().st_size,
            "source_root": str(root1.resolve()),
            "rel_path": "docs/readme.md",
            "rel_dir": "docs",
        }]
        result = _make_scanner_and_diff(f"{root1},{root2}", indexed)
        new_names = [i["name"] for i in result["new"]]
        assert new_names == ["readme.md"], f"root2 readme should be new, got {new_names}"
        assert result["deleted_count"] == 0, f"No deletion expected: {result['deleted']}"


def test_diff_deleted_keeps_old_filename_fallback():
    """最旧纯文件名索引数据：磁盘文件仍存在时不应报 deleted"""
    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "legacy.md"
        f.write_text("# Legacy", encoding="utf-8")
        indexed_files = [{
            "physical_name": "legacy.md",
            "file_name": "legacy.md",
            "file_mtime": f.stat().st_mtime,
            "file_size": f.stat().st_size,
        }]
        result = _make_scanner_and_diff(tmpdir, indexed_files)
        assert result["deleted_count"] == 0, f"got {result['deleted']}"
        assert result["new_count"] == 0


if __name__ == "__main__":
    tests = [
        ("diff uses relative path for same-name files", test_diff_uses_relative_path_not_filename),
        ("diff no false deleted with relative path index", test_diff_deleted_detection_with_relative_paths),
        ("diff backward compat old filename index", test_diff_backward_compat_with_old_filename_index),
        ("diff detects modified by mtime", test_diff_detects_modified_by_mtime),
        ("path identity basic (rel_path/rel_dir/source_root)", test_make_path_identity_basic),
        ("diff new files carry source_root/rel_dir", test_diff_new_files_carry_path_identity),
        ("diff identity match with source_root", test_diff_identity_matches_with_source_root),
        ("diff deleted keeps old filename fallback", test_diff_deleted_keeps_old_filename_fallback),
    ]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  [PASS] {name}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {name}: {e}")
    print(f"\n  Result: {passed}/{passed+failed} passed")
