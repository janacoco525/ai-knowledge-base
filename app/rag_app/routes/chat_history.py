"""
AI知识库 - 聊天记录 API 路由
提供会话的 CRUD、列表和历史迁移
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.rag_app.chat_store import (
    init_db, save_session, get_session, list_sessions,
    delete_session, clear_all_sessions, total_sessions
)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 首次导入时初始化数据库
init_db()


class ChatMessageItem(BaseModel):
    id: str
    role: str
    content: str
    timestamp: str
    citations: Optional[List[dict]] = []
    groundingSources: Optional[List[dict]] = []
    evidence: Optional[List[dict]] = []
    followUps: Optional[List[str]] = []
    webSupplemented: Optional[bool] = False
    scope: Optional[str] = None
    customDocIds: Optional[List[str]] = []


class SaveSessionRequest(BaseModel):
    sessionId: str
    title: str = "新对话"
    libraryId: str = "all"
    messages: List[ChatMessageItem] = []


class MigrateRequest(BaseModel):
    """从 localStorage 迁移旧数据"""
    messages: List[ChatMessageItem] = []


@router.post("/sessions")
async def create_or_update_session(req: SaveSessionRequest):
    """保存/更新会话"""
    result = save_session(
        session_id=req.sessionId,
        title=req.title,
        messages=[m.dict() for m in req.messages],
        library_id=req.libraryId,
    )
    return result


@router.get("/sessions")
async def get_sessions(limit: int = 50, offset: int = 0):
    """获取会话列表"""
    sessions = list_sessions(limit=limit, offset=offset)
    return {"sessions": sessions, "total": total_sessions()}


@router.get("/sessions/{session_id}")
async def get_one_session(session_id: str):
    """获取单个会话详情"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.delete("/sessions/{session_id}")
async def remove_session(session_id: str):
    """删除单个会话"""
    return delete_session(session_id)


@router.delete("/sessions")
async def remove_all_sessions():
    """清空全部会话"""
    return clear_all_sessions()


@router.post("/migrate")
async def migrate_from_localstorage(req: MigrateRequest):
    """从 localStorage 迁移旧数据到后端"""
    if not req.messages:
        return {"status": "ok", "message": "无数据需要迁移"}
    result = save_session(
        session_id="migrated-legacy",
        title="历史对话（已迁移）",
        messages=[m.dict() for m in req.messages],
        library_id="all",
    )
    return {"status": "ok", "migrated": result["message_count"]}
