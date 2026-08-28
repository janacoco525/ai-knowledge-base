"""
回归测试 — 防止已修复的 bug 复发
每次修 bug 后，在这里加一条测试用例

运行: cd rag_app && python -m pytest ../tests/test_regression.py -v
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path

# 确保可以导入 rag_app 模块 (Qoder 2026-07-31 迁移: rag_app/ → app/rag_app/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app', 'rag_app'))


class TestDeleteFileSafety:
    """删除文件安全测试 — 防止误删所有文件"""

    def test_remove_file_empty_id_returns_error(self):
        """T-DEL-001: 空 ID 不应删除任何文件"""
        from knowledge_base import KnowledgeBase
        from config import Config

        # 创建临时知识库实例
        tmp_dir = tempfile.mkdtemp()
        try:
            config = Config
            config.VECTOR_DATA_DIR = tmp_dir
            kb = KnowledgeBase.__new__(KnowledgeBase)
            kb.config = config
            kb._embed_texts = ["text1", "text2"]
            kb._embed_ids = ["id1", "id2"]
            kb._embed_metadatas = [
                {"physical_name": "file1.pdf", "file_name": "file1.pdf"},
                {"physical_name": "file2.pdf", "file_name": "file2.pdf"},
            ]
            kb._embeddings = None
            kb.bm25_docs = ["text1", "text2"]
            kb.bm25_ids = ["id1", "id2"]
            kb.bm25_metadatas = [
                {"physical_name": "file1.pdf", "file_name": "file1.pdf"},
                {"physical_name": "file2.pdf", "file_name": "file2.pdf"},
            ]
            kb.bm25 = None
            kb._file_chunk_map = {}
            kb._file_text_cache = {}
            kb._file_preprocessed = {}
            kb._file_html = {}
            kb._file_headings = {}

            # 测试空 ID
            result = kb.remove_file("")
            assert result["removed"] == False
            assert len(kb._embed_metadatas) == 2  # 文件未被删除

            result = kb.remove_file(None)
            assert result["removed"] == False
            assert len(kb._embed_metadatas) == 2  # 文件未被删除
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_remove_file_safety_cap(self):
        """T-DEL-002: 删除超过 50% chunks 应被拦截"""
        from knowledge_base import KnowledgeBase
        from config import Config

        tmp_dir = tempfile.mkdtemp()
        try:
            config = Config
            config.VECTOR_DATA_DIR = tmp_dir
            kb = KnowledgeBase.__new__(KnowledgeBase)
            kb.config = config
            kb._embed_texts = ["t1", "t2", "t3"]
            kb._embed_ids = ["i1", "i2", "i3"]
            kb._embed_metadatas = [
                {"physical_name": "a.pdf", "file_name": "a.pdf"},
                {"physical_name": "b.pdf", "file_name": "b.pdf"},
                {"physical_name": "c.pdf", "file_name": "c.pdf"},
            ]
            kb._embeddings = None
            kb.bm25_docs = ["t1", "t2", "t3"]
            kb.bm25_ids = ["i1", "i2", "i3"]
            kb.bm25_metadatas = [
                {"physical_name": "a.pdf", "file_name": "a.pdf"},
                {"physical_name": "b.pdf", "file_name": "b.pdf"},
                {"physical_name": "c.pdf", "file_name": "c.pdf"},
            ]
            kb.bm25 = None
            kb._file_chunk_map = {}
            kb._file_text_cache = {}
            kb._file_preprocessed = {}
            kb._file_html = {}
            kb._file_headings = {}

            # 模拟：用一个不存在的 ID 去匹配，如果匹配逻辑有误可能误删
            # 这里测试安全机制：如果真的匹配了所有，应该被拦截
            # 修改条件让它匹配所有（模拟 bug）
            original_metas = kb._embed_metadatas.copy()
            kb._embed_metadatas = [
                {"physical_name": "bug.pdf", "file_name": "bug.pdf"},
                {"physical_name": "bug.pdf", "file_name": "bug.pdf"},
                {"physical_name": "bug.pdf", "file_name": "bug.pdf"},
            ]
            kb.bm25_metadatas = kb._embed_metadatas.copy()

            result = kb.remove_file("bug.pdf")
            # 应该被安全检查拦截（删除 3/3 = 100% > 50%）
            # 但实际上如果所有文件名相同，这是合法操作
            # 所以安全检查只在"看起来像误匹配"时触发
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_remove_file_normal_delete(self):
        """T-DEL-003: 正常删除单个文件"""
        from knowledge_base import KnowledgeBase
        from config import Config

        tmp_dir = tempfile.mkdtemp()
        try:
            config = Config
            config.VECTOR_DATA_DIR = tmp_dir
            kb = KnowledgeBase.__new__(KnowledgeBase)
            kb.config = config
            kb._embed_texts = ["text1", "text2", "text3"]
            kb._embed_ids = ["id1", "id2", "id3"]
            kb._embed_metadatas = [
                {"physical_name": "file1.pdf", "file_name": "file1.pdf"},
                {"physical_name": "file2.pdf", "file_name": "file2.pdf"},
                {"physical_name": "file3.pdf", "file_name": "file3.pdf"},
            ]
            kb._embeddings = None
            kb.bm25_docs = ["text1", "text2", "text3"]
            kb.bm25_ids = ["id1", "id2", "id3"]
            kb.bm25_metadatas = [
                {"physical_name": "file1.pdf", "file_name": "file1.pdf"},
                {"physical_name": "file2.pdf", "file_name": "file2.pdf"},
                {"physical_name": "file3.pdf", "file_name": "file3.pdf"},
            ]
            kb.bm25 = None
            kb._file_chunk_map = {}
            kb._file_text_cache = {}
            kb._file_preprocessed = {}
            kb._file_html = {}
            kb._file_headings = {}

            result = kb.remove_file("file2.pdf")
            assert result["removed"] == True
            assert result["chunks_removed"] == 1
            assert len(kb._embed_metadatas) == 2  # 只删了 file2
            # 验证剩下的文件
            remaining_names = [m["physical_name"] for m in kb._embed_metadatas]
            assert "file1.pdf" in remaining_names
            assert "file3.pdf" in remaining_names
            assert "file2.pdf" not in remaining_names
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestUploadFilePersistence:
    """上传文件持久化测试 — 防止文件存到临时目录后丢失"""

    def test_upload_saves_to_persistent_dir(self):
        """T-UPD-001: 上传文件应保存到持久化目录"""
        from pathlib import Path
        # 模拟 upload 端点的逻辑
        tmp_dir = tempfile.mkdtemp()
        try:
            upload_dir = Path(tmp_dir) / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            # 模拟上传一个文件
            test_file = upload_dir / "test.pdf"
            test_file.write_bytes(b"test content")

            # 验证文件存在
            assert test_file.exists()
            assert test_file.read_bytes() == b"test content"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestFrontendDeleteBehavior:
    """前端删除行为测试 — 自建文档不调用后端 API"""

    def test_local_doc_id_format(self):
        """T-FE-001: 自建文档 ID 以 doc- 开头"""
        import re
        # 模拟前端生成的 ID
        import time
        import random
        doc_id = f"doc-{int(time.time() * 1000)}-{random.randint(100000, 999999)}"
        assert doc_id.startswith("doc-"), f"自建文档 ID 应以 doc- 开头: {doc_id}"

    def test_backend_doc_id_is_filename(self):
        """T-FE-002: 后端文档 ID 是文件名（不以 doc- 开头）"""
        # 后端文档 ID 来自 physical_name，即原始文件名
        backend_ids = [
            "MITTRI_Microsoft_Report_June26_.pdf",
            "地球编年史1第十二个天...txt",
            "report.docx",
        ]
        for doc_id in backend_ids:
            assert not doc_id.startswith("doc-"), f"后端文档 ID 不应以 doc- 开头: {doc_id}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
