"""
AI知识库 - AI Diff 语义对比接口
对两个已索引文件做语义级差异描述，不是逐行diff。
"""
from __future__ import annotations

from typing import List, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.rag_app.config import Config
from app.rag_app.shared_engine import get_engine

router = APIRouter(prefix="/api/diff", tags=["AI Diff"])


class DiffRequest(BaseModel):
    file_id_a: str = Field(..., description="已索引文件 ID A，取自 /api/kb/files")
    file_id_b: str = Field(..., description="已索引文件 ID B，取自 /api/kb/files")
    diff_focus: Literal["summary", "details", "highlights"] = Field(default="summary", description="差异描述关注面")
    max_changes: int = Field(default=5, ge=1, le=10, description="最多返回多少条差异描述")


@router.post("")
async def generate_diff(request: DiffRequest):
    eng = get_engine()
    scope = eng.get_diff_scope(
        file_id_a=request.file_id_a,
        file_id_b=request.file_id_b,
        max_chunks_per_file=5,
    )
    chunks_a = scope.get("chunks_a", [])
    chunks_b = scope.get("chunks_b", [])
    if not chunks_a and not chunks_b:
        raise HTTPException(status_code=404, detail="两个文件的已索引内容均为空，无法对比。")
    if not chunks_a:
        raise HTTPException(status_code=404, detail=f"文件 A（{request.file_id_a}）的已索引内容为空，无法对比。")
    if not chunks_b:
        raise HTTPException(status_code=404, detail=f"文件 B（{request.file_id_b}）的已索引内容为空，无法对比。")

    file_name_a = scope.get("file_meta_a", {}).get("file_name", request.file_id_a)
    file_name_b = scope.get("file_meta_b", {}).get("file_name", request.file_id_b)

    diff_payload = eng.generate_semantic_diff(
        chunks_a=chunks_a,
        chunks_b=chunks_b,
        file_name_a=file_name_a,
        file_name_b=file_name_b,
        max_changes=request.max_changes,
    )
    diff_payload["meta"].update(
        {
            "file_a": file_name_a,
            "file_b": file_name_b,
            "chunk_count_a": len(chunks_a),
            "chunk_count_b": len(chunks_b),
            "diff_focus": request.diff_focus,
        }
    )
    return diff_payload
