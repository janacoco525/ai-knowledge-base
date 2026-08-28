"""
AI知识库 - RAG查询路由
基于FastAPI的SSE流式知识问答接口
"""
import json
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from app.rag_app.shared_engine import get_engine
from app.rag_app.config import Config

router = APIRouter(prefix="/api", tags=["RAG查询"])


@router.get("/query")
def query(
    q: str = Query(..., description="用户问题"),
    top_k: int = Query(5, ge=1, le=20),
    domain: str = Query(None, description="知识域筛选"),
    search_mode: str = Query("hybrid", description="搜索模式: hybrid/keyword/semantic"),
):
    """知识查询（非流式）"""
    eng = get_engine()
    result = eng.query(q, top_k=top_k, domain=domain)
    return result


@router.get("/query/stream")
async def query_stream(
    request: Request,
    q: str = Query(..., description="用户问题"),
    top_k: int = Query(5, ge=1, le=20),
    domain: str = Query(None, description="知识域筛选"),
    search_mode: str = Query("hybrid", description="搜索模式"),
    file_ids: str = Query(None, description="限定文件问答，逗号分隔多个ID"),
):
    """知识查询（SSE流式）"""
    eng = get_engine()
    ids_list = [fid.strip() for fid in file_ids.split(",") if fid.strip()] if file_ids else None

    async def event_generator():
        for event in eng.query_stream(q, top_k=top_k, domain=domain, search_mode=search_mode, file_ids=ids_list):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/query/quick")
def quick_query(q: str = Query(...)):
    """快速提问（首页快捷问题）"""
    eng = get_engine()
    result = eng.query(q, top_k=3)
    return result
