import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_shared_engine_exposes_get_kb():
    from app.rag_app.shared_engine import get_kb
    assert callable(get_kb)


def test_shared_engine_exposes_get_engine():
    from app.rag_app.shared_engine import get_engine
    assert callable(get_engine)


def test_shared_kb_is_singleton():
    import app.rag_app.shared_engine as shared_engine
    shared_engine._kb = None
    shared_engine._engine = None
    kb1 = shared_engine.get_kb()
    kb2 = shared_engine.get_kb()
    assert kb1 is kb2


def test_shared_engine_reuses_kb():
    import app.rag_app.shared_engine as shared_engine
    shared_engine._kb = None
    shared_engine._engine = None
    eng = shared_engine.get_engine()
    kb = shared_engine.get_kb()
    assert eng.kb is kb


def test_rag_engine_accepts_external_kb():
    from app.rag_app.config import Config
    from app.rag_app.knowledge_base import KnowledgeBase
    from app.rag_app.rag_engine import RAGEngine
    cfg = Config()
    external_kb = KnowledgeBase(cfg)
    eng = RAGEngine(cfg, kb=external_kb)
    assert eng.kb is external_kb
