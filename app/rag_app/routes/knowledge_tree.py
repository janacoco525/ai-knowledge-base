"""
知识框架树路由
提供知识框架树生成接口
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from app.rag_app.config import Config
from app.rag_app.shared_engine import get_engine

router = APIRouter(prefix="/api/knowledge-tree", tags=["知识框架树"])


# 请求模型
class KnowledgeTreeRequest(BaseModel):
    file_ids: Optional[List[str]] = None
    max_depth: Optional[int] = 3
    focus_area: Optional[str] = None


# 响应模型
class KnowledgeTreeResponse(BaseModel):
    tree_id: str
    title: str
    generated_at: str
    meta: Dict[str, Any]
    tree: Dict[str, Any]
    insights: List[str]


@router.post("/generate", response_model=KnowledgeTreeResponse)
async def generate_knowledge_tree(request: KnowledgeTreeRequest):
    """
    生成知识框架树

    - 默认使用所有已索引文件
    - 可通过file_ids指定文件
    - 返回层次化的知识分类树
    """
    try:
        eng = get_engine()

        # 调用引擎方法生成知识框架树
        result = eng.build_knowledge_tree(
            file_ids=request.file_ids,
            max_depth=request.max_depth or 3,
            focus_area=request.focus_area
        )

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成知识框架树失败: {str(e)}")
