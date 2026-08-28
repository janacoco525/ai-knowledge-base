"""
AI知识库 - 知识延展推荐路由
基于已索引知识分析知识图谱中的缺口，推荐外部学习资源。
诚实边界：LLM建议仅供拓展参考，不担保资源可用性。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.rag_app.config import Config
from app.rag_app.shared_engine import get_engine

router = APIRouter(prefix="/api/recommend", tags=["知识延展"])


class RecommendRequest(BaseModel):
    domain: Optional[str] = Field(default=None, description="限定知识领域")
    concept: Optional[str] = Field(default=None, description="围绕特定概念")
    focus: str = Field(default="resources", description="推荐侧重：resources(外部资源) / books(书籍) / courses(课程)")
    max_recommendations: int = Field(default=5, ge=1, le=10)


@router.post("")
async def recommend_resources(request: RecommendRequest):
    """根据知识库内容缺口推荐外部学习资源"""
    eng = get_engine()

    # 获取知识库中的主题/概念作为上下文
    scope = eng.get_analysis_scope(
        file_ids=[],
        domain=request.domain,
        max_files=10,
        max_chunks_per_file=2,
    )
    chunks = scope.get("chunks", [])
    selected_files = scope.get("selected_files", [])

    if not chunks:
        raise HTTPException(status_code=404, detail="知识库中没有已索引内容，无法分析缺口。")

    # 构建上下文摘要
    file_summary = "、".join(f["name"] for f in selected_files[:8]) or "当前已索引范围"
    topics_summary = []
    for chunk in chunks[:8]:
        text = (chunk.get("text") or "")[:120].strip()
        src = chunk.get("source_file", "")
        if text:
            topics_summary.append(f"[{src}] {text}")
    knowledge_context = "\n".join(topics_summary[:6])

    focus_instruction = {
        "resources": "推荐3-5个相关的优质外部学习资源（网址、工具、平台等），说明每个资源与当前知识库内容的关联",
        "books": "推荐3-5本相关的书籍，说明每本书的核心内容及与知识库内容的互补关系",
        "courses": "推荐3-5个相关的在线课程或教程，说明每个课程适合的学习阶段和前置知识",
    }.get(request.focus, "推荐3-5个相关的外部学习资源")

    concept_hint = f"，特别围绕'{request.concept}'这一概念" if request.concept else ""

    prompt = (
        f"基于以下知识库内容摘要{concept_hint}，{focus_instruction}。\n"
        "输出JSON数组，每个元素包含：\n"
        "- title: 资源名称\n"
        "- type: 资源类型（book/course/tool/article）\n"
        "- description: 推荐理由（40-80字，说明与知识库的关联）\n"
        "- difficulty: 难度（beginner/intermediate/advanced）\n"
        "只推荐真实存在的知名资源，不确定的资源不要编造。\n\n"
        f"知识库范围：{file_summary}\n"
        f"知识库内容摘要：\n{knowledge_context}"
    )

    try:
        response = eng.llm_client.chat.completions.create(
            model=eng.model_name,
            messages=[
                {"role": "system", "content": "你是一个知识管理助手，擅长根据已有知识推荐学习路径和外部资源。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        raw = (response.choices[0].message.content or "").strip()
        import json as _json
        recommendations = []
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                recommendations = _json.loads(raw[start:end])
        except Exception:
            pass

        # 持久化推荐结果
        _save_recs({"items": recommendations[:request.max_recommendations], "generated_at": datetime.now().isoformat()})

        return {
            "status": "ok",
            "recommendations": recommendations[:request.max_recommendations],
            "scope": {
                "knowledge_base": file_summary,
                "domain": request.domain,
                "concept": request.concept,
                "focus": request.focus,
            },
            "meta": {"source_mode": "llm-backed-recommendation"},
        }

    except Exception:
        return {
            "recommendations": [],
            "scope": {
                "knowledge_base": file_summary,
                "domain": request.domain,
                "concept": request.concept,
                "focus": request.focus,
            },
            "meta": {"source_mode": "extractive-fallback-no-recommendation"},
            "note": "LLM不可用，建议浏览知识框架树或图谱串联面板发现知识关联。",
        }


# 推荐结果持久化
RECOMMEND_FILE = os.path.join(Config.ROUTES_DATA_DIR, "recommendations.json")

def _load_recs() -> dict:
    try:
        os.makedirs(os.path.dirname(RECOMMEND_FILE), exist_ok=True)
        if os.path.exists(RECOMMEND_FILE):
            with open(RECOMMEND_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return {"items": []}

def _save_recs(data: dict):
    os.makedirs(os.path.dirname(RECOMMEND_FILE), exist_ok=True)
    with open(RECOMMEND_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.get("")
async def get_recommendations():
    """获取最近一次推荐结果"""
    data = _load_recs()
    return {"recommendations": data.get("items", []), "generated_at": data.get("generated_at", "")}


@router.delete("")
async def clear_recommendations():
    """清除推荐缓存"""
    _save_recs({"items": []})
    return {"status": "ok"}
