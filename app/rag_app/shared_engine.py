"""
共享引擎单例 — 供所有路由模块使用。
统一 RAGEngine 和 KnowledgeBase 为单一实例，消除多实例数据不一致风险。
"""
from __future__ import annotations

from app.rag_app.config import Config
from app.rag_app.knowledge_base import KnowledgeBase
from app.rag_app.rag_engine import RAGEngine

_engine: RAGEngine | None = None
_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    """获取全局唯一的 KnowledgeBase 实例（懒加载）"""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(Config())
    return _kb


def get_engine() -> RAGEngine:
    """获取全局唯一的 RAGEngine 实例（懒加载，复用同一个 KB）"""
    global _engine
    if _engine is None:
        _engine = RAGEngine(Config(), kb=get_kb())
    return _engine
