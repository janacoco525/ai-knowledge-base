"""
AI知识库 - 知识卡片路由
概念浓缩卡片视图，支持浏览/搜索/跳转图谱。
诚实边界：只基于已索引文件提取概念卡片，不冒充完整知识卡片管理系统。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import asyncio

from app.rag_app.config import Config

router = APIRouter(prefix="/api/cards", tags=["知识卡片"])

# 数据文件路径 — 统一走 Config
DATA_DIR = Config.ROUTES_DATA_DIR
CARDS_FILE = os.path.join(DATA_DIR, "knowledge_cards.json")


class CardsGenerateRequest(BaseModel):
    source_mode: str = Field(default="auto", description="数据来源：auto / live / sample")
    max_cards: int = Field(default=20, ge=2, le=50, description="最多生成卡片数")
    domain: str | None = Field(default=None, description="知识域，None=全部")


class CardsExtractRequest(BaseModel):
    file_ids: List[str] = Field(default_factory=list, description="限定文件ID列表，空=全部")
    concept_ids: List[str] = Field(default_factory=list, description="限定概念ID列表，空=全部")
    keyword: str = Field(default="", description="按关键词筛选概念标签（可选过滤）")
    max_cards: int = Field(default=20, ge=1, le=50, description="最多生成卡片数")
    source_mode: str = Field(default="auto", description="数据来源：auto / live / sample")
    domain: str | None = Field(default=None, description="知识域，None=全部")


def _ensure_data_dir():
    """确保数据目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_cards() -> dict:
    """读取知识卡片（自动去重：相同label只保留重要度最高的）"""
    if not os.path.exists(CARDS_FILE):
        return {}
    try:
        with open(CARDS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    # 按label去重，保留importance最高的
    seen = {}
    for cid, card in raw.items():
        label_key = card.get("label", "").strip().lower()
        if label_key not in seen or card.get("importance", 0) > seen[label_key].get("importance", 0):
            seen[label_key] = card
    # 构建去重后字典（过滤停用标签）
    deduped = {}
    for card in seen.values():
        label = card.get("label", "").strip()
        if len(label) <= 1:
            continue
        # 从 concept_extractor 导入停用词
        from app.rag_app.concept_extractor import _SKIP_LABELS
        if label.lower() in _SKIP_LABELS:
            continue
        deduped[card.get("concept_id", card["label"])] = card
    if len(deduped) < len(raw):
        _save_cards(deduped)
    # 自动清理：移除引用已删除文件的卡片
    if deduped:
        try:
            from app.rag_app.shared_engine import get_kb
            active_names = {f["name"] for f in get_kb().list_files()}
            cleaned = {k: v for k, v in deduped.items()
                       if not v.get("source_files") or
                       any(sf in active_names for sf in v.get("source_files", []))}
            if len(cleaned) < len(deduped):
                _save_cards(cleaned)
                return cleaned
        except Exception:
            pass
    return deduped

def _save_cards(cards: dict):
    """保存知识卡片"""
    _ensure_data_dir()
    with open(CARDS_FILE, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)


def _normalize_source_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"auto", "live", "sample"}:
        raise HTTPException(status_code=400, detail="source_mode 只支持 auto / live / sample。")
    return mode


def _fetch_graph_data(source_mode: str, domain: str | None = None) -> dict:
    """复用图谱路由的逻辑获取图数据"""
    from app.rag_app.routes.graph import _resolve_graph_payload

    payload, _ = _resolve_graph_payload(
        source_mode=source_mode,
        domain=domain,
        max_nodes=12,
        selection_profile="compact",
        sorting_strategy="relevance",
        max_chunks=12,
        focus_concept=None,
    )
    return payload


def _build_related_map(edges: list[dict]) -> dict[str, list[str]]:
    """从边列表构建概念关联映射"""
    related: dict[str, list[str]] = {}
    for edge in edges:
        from_id = str(edge.get("from", ""))
        to_id = str(edge.get("to", ""))
        if from_id:
            related.setdefault(from_id, []).append(to_id)
        if to_id:
            related.setdefault(to_id, []).append(from_id)
    return related


def _build_source_files_map(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """构建每个概念的来源文件列表"""
    source_map: dict[str, list[str]] = {}
    for node in nodes:
        nid = str(node.get("id", ""))
        sf = node.get("source_file")
        if nid and sf:
            source_map.setdefault(nid, [])
            if sf not in source_map[nid]:
                source_map[nid].append(str(sf))
    for edge in edges:
        sf = edge.get("source_file")
        if not sf:
            continue
        for key in ("from", "to"):
            nid = str(edge.get(key, ""))
            if nid:
                source_map.setdefault(nid, [])
                if str(sf) not in source_map[nid]:
                    source_map[nid].append(str(sf))
    return source_map


def _infer_card_type(node_type: str | None, label: str) -> str:
    """推断卡片类型"""
    if node_type in ("concept", "topic", "method"):
        return node_type
    label_lower = label.lower()
    method_keywords = ("method", "algorithm", "approach", "technique", "策略", "方法", "算法")
    if any(kw in label_lower for kw in method_keywords):
        return "method"
    return "concept"


_SYSTEM_EXPLAIN = "你是一位知识管理专家。请用两段话解释以下概念：第一段用精确、权威的语言给出专业定义，说明核心原理和关键特征；第二段用通俗易懂的语言解释这个概念，让初学者也能理解。每段2-3句话。请使用中文。"


def _explain_concept_sync(label: str, context_chunks: list[str]) -> str:
    """同步调用LLM生成概念解释"""
    from app.rag_app.llm_client_factory import create_llm_client
    from app.rag_app.config import Config

    if not Config.STEP_API_KEY:
        return ""
    # 卡片生成是可选增强，失败时有规则描述兜底，不应因重试拖住整条接口。
    client = create_llm_client(max_retries=0)
    context_text = "\n".join(context_chunks) if context_chunks else f"概念：{label}"
    prompt = f"请解释以下概念：\n\n{label}\n\n参考上下文：\n{context_text}"
    try:
        response = client.chat.completions.create(
            model=Config.STEP_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_EXPLAIN},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=256,
            timeout=5,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        import sys
        print(f"[WARN] cards LLM failed for '{label}': {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return ""


async def _explain_concept(label: str, context_chunks: list[str]) -> str:
    """异步调用LLM生成概念解释（2-3句通俗解释）"""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(None, _explain_concept_sync, label, context_chunks)


async def _make_description(node: dict, related_labels: list[str]) -> str:
    """生成有学习价值的卡片描述"""
    label = node.get("label", "未知概念")
    summary = (node.get("summary") or "").strip()

    # 已有详细summary且非规则生成的，依然用LLM覆盖（确保使用最新prompt风格）
    # LLM生成的描述统一重新生成，保持风格一致
    if summary and len(summary) > 30 and "规则优先抽取自" not in summary and "提示：前往" not in summary:
        pass  # 继续往下走LLM路径

    # 尝试LLM生成解释（summary不足30字符时）
    context = [label]
    if related_labels:
        context.append("关联概念：" + "、".join(related_labels[:5]))
    if summary:
        context.append("已有摘要：" + summary)

    explanation = await _explain_concept(label, context)
    if explanation and len(explanation) > 10:
        return explanation

    # 回退：规则描述（LLM失败或返回过短时）
    parts = [f"**{label}**"]
    if summary and len(summary) > 5:
        parts.append(f"— {summary}")
    if related_labels:
        top3 = "、".join(related_labels[:3])
        parts.append(f"\n\n关联概念：{top3}")
    return "".join(parts)


async def _make_description_batch(
    nodes: list[dict], related_map: dict[str, list[str]], node_lookup: dict[str, dict]
) -> dict[str, str]:
    """并发生成卡片描述，超时则立即使用规则描述，避免接口被 LLM 拖住。"""
    semaphore = asyncio.Semaphore(16)

    async def generate(node: dict) -> tuple[str, str]:
        nid = str(node.get("id", ""))
        related_labels = [
            str(node_lookup.get(rid, {}).get("label", rid))
            for rid in related_map.get(nid, [])
            if rid in node_lookup
        ]
        async with semaphore:
            return nid, await _make_description(node, related_labels)

    tasks = {asyncio.create_task(generate(node)): node for node in nodes}
    done, pending = await asyncio.wait(tasks, timeout=8)
    for task in pending:
        task.cancel()

    descriptions: dict[str, str] = {}
    for task in done:
        try:
            nid, description = task.result()
            descriptions[nid] = description
        except Exception:
            pass

    # LLM 超时或失败时给出确定性的本地描述，卡片仍然可用。
    for node in nodes:
        nid = str(node.get("id", ""))
        if nid in descriptions:
            continue
        label = str(node.get("label", nid))
        summary = (node.get("summary") or "").strip()
        related_ids = related_map.get(nid, [])
        related_labels = [
            str(node_lookup.get(rid, {}).get("label", rid))
            for rid in related_ids[:3]
            if rid in node_lookup
        ]
        parts = [f"**{label}**"]
        if summary:
            parts.append(f"\n\n{summary}")
        if related_labels:
            parts.append(f"\n\n关联概念：{'、'.join(related_labels)}")
        descriptions[nid] = "".join(parts)
    return descriptions


@router.post("/generate")
async def generate_cards(request: CardsGenerateRequest, req: Request):
    """基于当前图数据生成知识卡片"""
    source_mode = _normalize_source_mode(request.source_mode)

    try:
        graph_data = _fetch_graph_data(source_mode, request.domain)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"获取图数据失败：{exc}") from exc

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    if not nodes:
        raise HTTPException(status_code=404, detail="当前图数据中没有概念节点，请先完成索引或概念提取。")

    related_map = _build_related_map(edges)
    source_files_map = _build_source_files_map(nodes, edges)
    node_lookup = {str(n["id"]): n for n in nodes}
    target_nodes = nodes[:request.max_cards]
    descriptions = await _make_description_batch(target_nodes, related_map, node_lookup)

    cards = _load_cards()
    now = datetime.now().isoformat(timespec="seconds")
    generated_count = 0

    for node in target_nodes:
        nid = str(node.get("id", ""))
        label = str(node.get("label", nid))

        related_ids = related_map.get(nid, [])
        related_labels = [
            str(node_lookup.get(rid, {}).get("label", rid))
            for rid in related_ids
            if rid in node_lookup
        ]

        source_files = source_files_map.get(nid, [])
        importance = float(node.get("weight", 0.5))
        card_type = _infer_card_type(node.get("type"), label)
        description = descriptions.get(nid, "")

        if nid in cards:
            cards[nid]["description"] = description
            cards[nid]["source_files"] = source_files
            cards[nid]["related_concepts"] = related_labels
            cards[nid]["importance"] = importance
            cards[nid]["updated_at"] = now
        else:
            cards[nid] = {
                "concept_id": nid,
                "label": label,
                "description": description,
                "source_files": source_files,
                "related_concepts": related_labels,
                "importance": importance,
                "card_type": card_type,
                "created_at": now,
                "updated_at": now,
            }
        generated_count += 1

    # 清理：移除不在当前图谱节点中的旧卡片（已被LLM过滤筛掉的概念）
    current_ids = {str(n.get("id", "")) for n in nodes}
    stale_keys = [k for k in cards if k not in current_ids]
    for k in stale_keys:
        del cards[k]

    _save_cards(cards)

    return {
        "status": "ok",
        "generated_count": generated_count,
        "total_cards": len(cards),
    }


def _filter_graph_by_scope(
    nodes: list[dict],
    edges: list[dict],
    *,
    file_ids: list[str],
    concept_ids: list[str],
    keyword: str,
) -> tuple[list[dict], list[dict]]:
    """按文件/概念/关键词过滤图谱数据，实现独立提取"""
    # 构建节点ID→来源文件映射
    node_files: dict[str, set[str]] = {}
    for node in nodes:
        nid = str(node.get("id", ""))
        sf = node.get("source_file")
        if nid and sf:
            node_files.setdefault(nid, set()).add(str(sf))
    for edge in edges:
        sf = edge.get("source_file")
        if not sf:
            continue
        for key in ("from", "to"):
            nid = str(edge.get(key, ""))
            if nid:
                node_files.setdefault(nid, set()).add(str(sf))

    allowed_nids: set[str] | None = None

    # 按文件过滤
    if file_ids:
        allowed_nids = set()
        for nid, files in node_files.items():
            if files & set(file_ids):
                allowed_nids.add(nid)

    # 按概念ID过滤
    if concept_ids:
        cids = set(concept_ids)
        if allowed_nids is None:
            allowed_nids = cids
        else:
            allowed_nids &= cids

    # 按关键词过滤
    if keyword.strip():
        kw = keyword.strip().lower()
        kw_nids = {str(n.get("id", "")) for n in nodes if kw in str(n.get("label", "")).lower()}
        if allowed_nids is None:
            allowed_nids = kw_nids
        else:
            allowed_nids &= kw_nids

    if allowed_nids is not None:
        filtered_nodes = [n for n in nodes if str(n.get("id", "")) in allowed_nids]
        filtered_edges = [
            e for e in edges
            if str(e.get("from", "")) in allowed_nids and str(e.get("to", "")) in allowed_nids
        ]
        return filtered_nodes, filtered_edges

    return nodes, edges


@router.post("/extract")
async def extract_cards(request: CardsExtractRequest, req: Request):
    """独立提取知识卡片：按文件/概念/关键词精准生成"""
    source_mode = _normalize_source_mode(request.source_mode)

    try:
        graph_data = _fetch_graph_data(source_mode, request.domain)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"获取图数据失败：{exc}") from exc

    nodes, edges = _filter_graph_by_scope(
        graph_data.get("nodes", []),
        graph_data.get("edges", []),
        file_ids=request.file_ids,
        concept_ids=request.concept_ids,
        keyword=request.keyword,
    )

    if not nodes:
        scope_desc = []
        if request.file_ids:
            scope_desc.append(f"文件{len(request.file_ids)}个")
        if request.concept_ids:
            scope_desc.append(f"概念{len(request.concept_ids)}个")
        if request.keyword:
            scope_desc.append(f"关键词'{request.keyword}'")
        detail = "、".join(scope_desc) or "当前范围"
        raise HTTPException(status_code=404, detail=f"{detail}内没有匹配的概念节点。")

    related_map = _build_related_map(edges)
    source_files_map = _build_source_files_map(nodes, edges)
    node_lookup = {str(n["id"]): n for n in nodes}
    target_nodes = nodes[:request.max_cards]
    descriptions = await _make_description_batch(target_nodes, related_map, node_lookup)

    cards = _load_cards()
    now = datetime.now().isoformat(timespec="seconds")
    generated_count = 0

    for node in target_nodes:
        nid = str(node.get("id", ""))
        label = str(node.get("label", nid))

        related_ids = related_map.get(nid, [])
        related_labels = [
            str(node_lookup.get(rid, {}).get("label", rid))
            for rid in related_ids if rid in node_lookup
        ]

        source_files = source_files_map.get(nid, [])
        importance = float(node.get("weight", 0.5))
        card_type = _infer_card_type(node.get("type"), label)
        description = descriptions.get(nid, "")

        if nid in cards:
            cards[nid]["description"] = description
            cards[nid]["source_files"] = source_files
            cards[nid]["related_concepts"] = related_labels
            cards[nid]["importance"] = importance
            cards[nid]["updated_at"] = now
        else:
            cards[nid] = {
                "concept_id": nid, "label": label, "description": description,
                "source_files": source_files, "related_concepts": related_labels,
                "importance": importance, "card_type": card_type,
                "created_at": now, "updated_at": now,
            }
        generated_count += 1

    # 清理不在当前图的旧卡片
    current_ids = {str(n.get("id", "")) for n in nodes}
    for k in [k for k in cards if k not in current_ids]:
        del cards[k]

    _save_cards(cards)

    return {
        "status": "ok",
        "generated_count": generated_count,
        "total_cards": len(cards),
        "scope": {
            "file_ids": request.file_ids,
            "concept_ids": request.concept_ids,
            "keyword": request.keyword,
            "matched_nodes": len(nodes),
        },
    }


@router.get("")
async def list_cards(
    search: str = Query(default="", description="按概念名搜索"),
    sort_by: str = Query(default="importance", description="排序字段：importance / label / updated_at"),
    limit: int = Query(default=20, ge=1, le=500, description="返回条数上限"),
    all: bool = Query(default=False, description="导出模式：忽略limit返回全部卡片"),
):
    """查询知识卡片列表"""
    cards = _load_cards()
    cards_list = list(cards.values())

    # 搜索过滤
    if search.strip():
        search_lower = search.strip().lower()
        cards_list = [
            c for c in cards_list
            if search_lower in c.get("label", "").lower()
            or search_lower in c.get("description", "").lower()
        ]

    # 排序
    if sort_by == "label":
        cards_list.sort(key=lambda x: x.get("label", ""))
    elif sort_by == "updated_at":
        cards_list.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    else:  # importance
        cards_list.sort(key=lambda x: x.get("importance", 0), reverse=True)

    if not all:
        cards_list = cards_list[:limit]

    return {
        "cards": cards_list,
        "total": len(cards_list),
        "total_all": len(cards),
    }


@router.get("/{concept_id}")
async def get_card(concept_id: str):
    """获取单个知识卡片详情"""
    cards = _load_cards()

    if concept_id not in cards:
        raise HTTPException(status_code=404, detail=f"未找到概念 {concept_id} 的知识卡片")

    return cards[concept_id]
