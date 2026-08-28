"""
文档主旨提取器 — 图谱提取的前置步骤
先理解文档讲什么，再提取核心实体
"""
import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "graph", "llm_cache")


def _get_llm_client():
    from app.rag_app.llm_client_factory import create_llm_client
    return create_llm_client()


def _sample_representative(text: str, max_chars: int = 12000) -> str:
    """全书多处采样：开头、中段、结尾"""
    total = len(text)
    if total <= max_chars:
        return text
    parts = []
    parts.append(text[:int(total * 0.15)])
    mid = int(total * 0.35)
    parts.append(text[mid:mid + int(total * 0.25)])
    parts.append(text[int(total * 0.85):])
    return "\n\n--- 节选 ---\n\n".join(parts)[:max_chars]


THESIS_PROMPT = """你是一位专业的文档分析师。请分析以下文档节选，提取核心信息。

要求：
1. 判断文档的领域/主题
2. 提炼文档的核心论点或主旨（一句话）
3. 列出文档最关键的3-5个概念框架或核心术语
4. 简要概述文档的主要内容结构

只返回严格JSON，格式：
{{
  "domain": "领域",
  "thesis": "核心论点（一句话）",
  "key_frameworks": ["框架1", "框架2", "框架3"],
  "chapter_summary": "文档主要内容概述"
}}

文档节选：
{text}"""


def extract_document_thesis(full_text: str, file_id: str) -> Optional[Dict[str, Any]]:
    """
    提取文档主旨。结果缓存，同一文档只提取一次。
    返回: {domain, thesis, key_frameworks, chapter_summary}
    """
    if not full_text or len(full_text) < 100:
        return None

    # 检查缓存
    # ⛔ 2026-08-19：缓存 key 加内容指纹——仅按文件名缓存时，PDF 被替换/内容变化后
    # 永远命中旧主旨（《2049》曾命中 Self-Improving Agents 错档，图谱被错误主旨引导）
    import hashlib
    _sig = hashlib.md5(full_text[:2000].encode("utf-8", errors="ignore")).hexdigest()[:12]
    cache_key = f"{file_id}_{_sig}_thesis"
    cache_file = os.path.join(_CACHE_DIR, f"{cache_key}.json")
    os.makedirs(_CACHE_DIR, exist_ok=True)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("thesis"):
                logger.info("Thesis cache hit: %s", file_id)
                return cached
        except Exception:
            pass

    # 采样文本
    sample = _sample_representative(full_text)

    # 调 LLM
    try:
        client = _get_llm_client()
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": THESIS_PROMPT.format(text=sample)}],
            temperature=0.1,
            max_tokens=500,
            timeout=30,
        )
        raw = resp.choices[0].message.content or ""
        # 提取 JSON
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            thesis = json.loads(m.group())
            if isinstance(thesis, dict) and thesis.get("thesis"):
                # 缓存
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(thesis, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                logger.info("Thesis extracted: %s -> %s", file_id, thesis.get("domain"))
                return thesis
    except Exception as e:
        logger.warning("Thesis extraction failed: %s", e)

    return None
