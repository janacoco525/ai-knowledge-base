"""
核心业务逻辑单元测试
覆盖 KnowledgeBase、Config、域管理
"""
import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path(__file__).parent.parent


# ============ Config ============

def test_config_version():
    """config.PRODUCT_VERSION 必须与 docs/VERSION.md 当前修订一致（防版本漂移）。"""
    from app.rag_app.config import Config
    version_md = (PROJECT_ROOT / "docs" / "VERSION.md").read_text(encoding="utf-8")
    import re
    m = re.search(r'\*\*当前修订\*\*\s*\|\s*`v?(\d+\.\d+\.\d+)`', version_md)
    assert m, "VERSION.md 缺少「当前修订」版本号"
    assert Config.PRODUCT_VERSION == m.group(1), (
        f"config.py 版本 {Config.PRODUCT_VERSION} 与 VERSION.md {m.group(1)} 不一致"
    )


def test_config_extensions():
    from app.rag_app.config import Config
    assert ".pdf" in Config.SUPPORTED_EXTENSIONS
    assert ".md" in Config.SUPPORTED_EXTENSIONS
    assert ".epub" in Config.SUPPORTED_EXTENSIONS
    assert len(Config.SUPPORTED_EXTENSIONS) == 10


def test_config_get_scan_paths_from_env():
    from app.rag_app.config import Config
    from pathlib import Path
    with patch.object(Config, '_scan_paths_raw', '/a,/b,/c'):
        paths = Config.get_scan_paths()
        assert paths == [str(Path(x).resolve()) for x in ['/a', '/b', '/c']]


def test_config_get_scan_paths_dedup_normalized():
    """结构化 v2：重复路径应 resolve 规范化并去重保序"""
    from app.rag_app.config import Config
    from pathlib import Path
    with patch.object(Config, '_scan_paths_raw', '/a,/a,/b'):
        paths = Config.get_scan_paths()
        assert paths == [str(Path('/a').resolve()), str(Path('/b').resolve())], f"got {paths}"


def test_config_get_scan_paths_empty():
    from app.rag_app.config import Config
    with patch.object(Config, '_scan_paths_raw', ''), \
         patch.object(Config, 'DATA_DIR', ''):
        paths = Config.get_scan_paths()
        assert paths == []


def test_config_get_scan_paths_fallback_to_data_dir():
    from app.rag_app.config import Config
    from pathlib import Path
    with patch.object(Config, '_scan_paths_raw', ''), \
         patch.object(Config, 'DATA_DIR', '/my/data'):
        paths = Config.get_scan_paths()
        assert paths == [str(Path('/my/data').resolve())]


def test_config_validate_missing_key():
    from app.rag_app.config import Config
    with patch.object(Config, 'STEP_API_KEY', ''):
        ok, msg = Config.validate()
        assert not ok
        assert 'STEP_API_KEY' in msg


def test_config_validate_ok():
    from app.rag_app.config import Config
    with patch.object(Config, 'STEP_API_KEY', 'sk-test12345'):
        ok, msg = Config.validate()
        assert ok


# ============ KnowledgeBase ============

def test_kb_list_files_empty():
    from app.rag_app.config import Config
    from app.rag_app.knowledge_base import KnowledgeBase
    with patch.object(Config, 'VECTOR_DATA_DIR', tempfile.mkdtemp()):
        cfg = Config()
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb._embed_metadatas = []
        kb._embed_texts = []
        kb._embed_ids = []
        kb._embeddings = None
        kb.bm25_docs = []
        kb.bm25_ids = []
        kb.bm25_metadatas = []
        kb.bm25 = None
        kb.config = cfg
        assert kb.list_files() == []


def test_kb_list_files_grouping():
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._embed_metadatas = [
        {"physical_name": "docs/guide.md", "file_name": "guide.md", "file_path": "/a/guide.md", "domain": "docs", "uploaded_at": "2026-01-01", "file_size": 100, "file_mtime": 1000},
        {"physical_name": "docs/guide.md", "file_name": "guide.md", "file_path": "/a/guide.md", "domain": "docs", "uploaded_at": "2026-01-01", "file_size": 100, "file_mtime": 1000},
        {"physical_name": "notes/todo.md", "file_name": "todo.md", "file_path": "/b/todo.md", "domain": "notes", "uploaded_at": "2026-01-02", "file_size": 50, "file_mtime": 2000},
    ]
    files = kb.list_files()
    assert len(files) == 2
    names = {f['name'] for f in files}
    assert names == {'guide.md', 'todo.md'}
    guide = next(f for f in files if f['name'] == 'guide.md')
    assert guide['chunks'] == 2
    assert guide['physical_name'] == 'docs/guide.md'


def test_kb_remove_file():
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._embed_metadatas = [
        {"physical_name": "a.md", "file_name": "a.md"},
        {"physical_name": "b.md", "file_name": "b.md"},
        {"physical_name": "a.md", "file_name": "a.md"},
    ]
    kb._embed_texts = ["text1", "text2", "text3"]
    kb._embed_ids = ["a_0", "b_0", "a_1"]
    kb._embeddings = None
    kb.bm25_docs = ["text1", "text2", "text3"]
    kb.bm25_ids = ["a_0", "b_0", "a_1"]
    kb.bm25_metadatas = [
        {"physical_name": "a.md", "file_name": "a.md"},
        {"physical_name": "b.md", "file_name": "b.md"},
        {"physical_name": "a.md", "file_name": "a.md"},
    ]
    kb.bm25 = None
    kb.config = type('C', (), {'VECTOR_DATA_DIR': tempfile.mkdtemp()})()

    result = kb.remove_file("a.md")
    assert result["removed"] is True
    assert result["chunks_removed"] == 2
    assert len(kb._embed_texts) == 1
    assert kb._embed_texts[0] == "text2"


def test_kb_remove_nonexistent():
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._embed_metadatas = [{"physical_name": "a.md", "file_name": "a.md"}]
    kb._embed_texts = ["text1"]
    result = kb.remove_file("nonexistent.md")
    assert result["removed"] is False
    assert "未找到" in result["reason"]


# ============ Domain Management ============

def test_domains_crud():
    from routes.domains import _load_custom_domains, _save_custom_domains, DOMAINS_FILE
    import tempfile as _tmp

    with patch('routes.domains.DOMAINS_FILE', Path(_tmp.mkdtemp()) / "domains.json"):
        # Empty initially
        assert _load_custom_domains() == {}

        # Save and reload
        _save_custom_domains({"ml": "机器学习", "nlp": "自然语言处理"})
        loaded = _load_custom_domains()
        assert loaded == {"ml": "机器学习", "nlp": "自然语言处理"}

        # Overwrite
        _save_custom_domains({"ai": "AI"})
        loaded = _load_custom_domains()
        assert loaded == {"ai": "AI"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print(f'  [PASS] {fn.__name__}')
        except Exception as e:
            failed += 1
            print(f'  [FAIL] {fn.__name__}: {e}')
    print(f'\n  Result: {passed}/{passed+failed} passed')
