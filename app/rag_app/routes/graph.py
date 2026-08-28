"""
AI知识库 - 图谱路由
最小只读图谱数据接口
"""
from pathlib import Path
import json
import logging
import time as _time
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from app.rag_app.concept_extractor import ConceptExtractor
from app.rag_app.llm_graph_extractor import extract_llm_graph, extract_graph_fallback
from app.rag_app.config import PROJECT_DIR
from core.verifier import VerifierManager
from core.checkpointer import GraphRunner, SqliteCheckpointer
from core.parallel import parallel_graph_generation, TokenBucketRateLimiter

router = APIRouter(prefix="/api/graph", tags=["知识图谱"])

# ⛔ 项目根必须指向仓库根（app/rag_app/routes/graph.py → parents[3]），不能指向 app/
PROJECT_ROOT = PROJECT_DIR
SAMPLE_GRAPH_PATH = PROJECT_ROOT / "output" / "graph" / "graph-data-sample.v1.json"
SAMPLE_CHAIN_PATH = PROJECT_ROOT / "output" / "graph" / "graph-chain-sample.v1.json"
extractor = ConceptExtractor()

# Verifier 管理器 - 用于图谱质量、代码、摘要的自动校验
verifier = VerifierManager(project_root=PROJECT_ROOT, graph_min_score=0.55)

# GraphRunner - 带检查点的图执行器（耐久执行）
# 支持：暂停/恢复、时间旅行、人在回路、失败重试、检查点持久化
_checkpointer = SqliteCheckpointer(str(PROJECT_ROOT / ".aikb" / "graph_checkpoints.db"))
graph_runner = GraphRunner(_checkpointer, max_retries=2, step_timeout=300.0)

# 并行图谱生成的 LLM 限流器（保护 API 配额）
_llm_rate_limiter = TokenBucketRateLimiter(rate=5.0, burst=3)  # 5 req/s, burst 3

# 简单 TTL 缓存：避免同文档同参数重复 LLM 调用
_graph_cache: dict = {}
_GRAPH_CACHE_TTL = 600  # 秒（本地知识库图谱不常变，10分钟缓存避免超时）
_GRAPH_FALLBACK_TTL = 60  # 秒（2026-08-12：降级结果只短缓存，避免坏图黏 10 分钟；LLM 恢复后下次即好）


class GraphExtractRequest(BaseModel):
    text: str | None = Field(default=None, description="直接输入的文本")
    file_path: str | None = Field(default=None, description="本地文件路径")
    source_file: str | None = Field(default=None, description="文本来源文件名")
    domain: str | None = Field(default="ai_knowledge", description="知识域")
    max_nodes: int = Field(default=8, ge=2, le=20, description="最多提取节点数")
    graph_mode: str = Field(default="auto", description="图谱模式：auto / concept / structure")


class GraphChainRequest(BaseModel):
    concept: str = Field(description="要追踪的起点概念")
    domain: str | None = Field(default="ai_knowledge", description="知识域")
    max_chain_steps: int = Field(default=4, ge=2, le=8, description="最多返回几步串联链")
    max_gap_hints: int = Field(default=3, ge=1, le=5, description="最多返回几个缺口提示")
    source_mode: str = Field(default="auto", description="图状态来源：auto / live / sample")
    selection_profile: str = Field(default="balanced", description="live 图状态选材档位：compact / balanced / wide")
    sorting_strategy: str = Field(default="relevance", description="live 图状态排序策略：relevance / recency / diversity")
    max_chunks: int = Field(default=48, ge=6, le=200, description="live 图状态最多消费多少个 chunks")
    focus_concept: str | None = Field(default=None, description="可选：显式指定 live 图状态聚焦的概念")


# ===== Graph Execution with Checkpointing (耐久执行) =====

class GraphRunRequest(BaseModel):
    """启动/恢复图执行"""
    graph_def: Dict[str, Any] = Field(..., description="图定义：nodes/edges")
    initial_state: Dict[str, Any] = Field(default_factory=dict, description="初始状态")
    thread_id: str = Field(..., description="执行线程 ID（用于断点续跑）")
    pause_nodes: Optional[List[str]] = Field(default=None, description="需人工暂停的节点 ID 列表")
    human_input: Optional[Dict[str, Any]] = Field(default=None, description="人工审批输入（恢复时提供）")
    resume_from_step: Optional[int] = Field(default=None, description="时间旅行：回退到指定步骤重新执行")


class GraphForkRequest(BaseModel):
    """从历史检查点分叉新执行分支"""
    thread_id: str = Field(..., description="源线程 ID")
    from_step: int = Field(..., description="分叉起始步骤")
    new_thread_id: str = Field(..., description="新线程 ID")


# ===== Parallel Graph Generation (多库/多域并行生成) =====

class ParallelGraphRequest(BaseModel):
    """并行生成多个域的图谱"""
    domains: List[str] = Field(..., description="要生成图谱的域列表")
    max_nodes: int = Field(default=8, ge=2, le=20, description="每个域最多节点数")
    selection_profile: str = Field(default="balanced", description="选材档位")
    sorting_strategy: str = Field(default="relevance", description="排序策略")
    max_chunks: int = Field(default=48, ge=6, le=200, description="最大 chunk 数")
    focus_concept: Optional[str] = Field(default=None, description="聚焦概念")
    graph_mode: str = Field(default="auto", description="图谱模式")


class ParallelGraphResponse(BaseModel):
    results: Dict[str, Any] = Field(default_factory=dict)
    errors: Dict[str, str] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)


class GraphHistoryResponse(BaseModel):
    thread_id: str
    checkpoints: List[Dict[str, Any]]


def _load_json_payload(path: Path, missing_detail: str):
    if not path.exists():
        raise HTTPException(status_code=503, detail=missing_detail)

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"{path.name} 解析失败: {exc.msg}") from exc


def _normalize_concept(value: str) -> str:
    return value.strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def _normalize_source_mode(value: str | None) -> str:
    mode = (value or "auto").strip().lower()
    if mode not in {"auto", "live", "sample"}:
        raise HTTPException(status_code=400, detail="source_mode 只支持 auto / live / sample。")
    return mode


def _normalize_selection_profile(value: str | None) -> str:
    profile = (value or "balanced").strip().lower()
    if profile not in {"compact", "balanced", "wide"}:
        raise HTTPException(status_code=400, detail="selection_profile 只支持 compact / balanced / wide。")
    return profile


def _normalize_sorting_strategy(value: str | None) -> str:
    strategy = (value or "relevance").strip().lower()
    if strategy not in {"relevance", "recency", "diversity"}:
        raise HTTPException(status_code=400, detail="sorting_strategy 只支持 relevance / recency / diversity。")
    return strategy


def _normalize_max_chunks(value: int | None) -> int:
    chunk_count = int(value or 48)
    if chunk_count < 6 or chunk_count > 200:
        raise HTTPException(status_code=400, detail="max_chunks 只支持 6 到 200 之间的整数。")
    return chunk_count


def _match_node(nodes: list[dict], requested: str) -> dict | None:
    normalized = _normalize_concept(requested)
    for node in nodes:
        candidates = [
            _normalize_concept(str(node.get("id", ""))),
            _normalize_concept(str(node.get("label", ""))),
        ]
        if normalized in candidates:
            return node
    return None


def _sort_edges(edges: list[dict]) -> list[dict]:
    return sorted(
        edges,
        key=lambda edge: (-float(edge.get("weight", 0)), str(edge.get("to", "")), str(edge.get("label", ""))),
    )


def _make_gap_hint(concept_id: str, label: str, reason: str, suggested_next_step: str, priority: str) -> dict:
    return {
        "concept_id": concept_id,
        "label": label,
        "reason": reason,
        "suggested_next_step": suggested_next_step,
        "priority": priority,
    }


def _try_load_live_graph_payload(
    domain: str | None,
    max_nodes: int,
    selection_profile: str,
    sorting_strategy: str,
    max_chunks: int,
    focus_concept: str | None = None,
    single_file: str | None = None,
    graph_mode: str = "auto",
    force_llm: bool = False,
) -> dict | None:
    from app.rag_app.shared_engine import get_kb

    # 缓存键：domain+profile+strategy+max_nodes+focus+file+mode
    _cache_key = f"{domain}|{selection_profile}|{sorting_strategy}|{max_nodes}|{focus_concept}|{single_file}|{graph_mode}"
    _now = _time.time()
    if not force_llm and _cache_key in _graph_cache:
        _cached_at, _cached_val = _graph_cache[_cache_key]
        # 降级（rules-fallback）结果只短缓存；llm-first 结果按长 TTL 缓存
        is_fallback = _cached_val.get("meta", {}).get("extractor_mode") == "rules-fallback"
        ttl = _GRAPH_FALLBACK_TTL if is_fallback else _GRAPH_CACHE_TTL
        if _now - _cached_at < ttl:
            return _cached_val

    # 单文件模式：不用选材策略，直接拿该文件所有chunks
    effective_max_chunks = max_chunks if not single_file else max(max_chunks, 200)
    effective_profile = selection_profile if not single_file else "wide"
    effective_strategy = sorting_strategy if not single_file else "relevance"

    chunk_view = get_kb().get_graph_source_chunk_view(
        domain=domain,
        max_chunks=effective_max_chunks,
        selection_profile=effective_profile,
        sorting_strategy=effective_strategy,
        focus_concept=focus_concept,
        single_file=single_file,
        spread_single_file=bool(single_file),
    )
    chunks = chunk_view["chunks"]
    if not chunks:
        return None

    # 第一步：提取文档主旨（引导图谱提取质量）
    from app.rag_app.thesis_extractor import extract_document_thesis
    full_text = "\n".join(c.get("text", "") for c in chunks if c.get("text", ""))
    source_file = chunks[0].get("source_file", "unknown") if chunks else "unknown"
    thesis = extract_document_thesis(full_text, source_file)

    # 策略1：LLM-first 提取（主旨引导）
    llm_payload = extract_llm_graph(chunks, max_nodes=max_nodes, thesis=thesis)
    if llm_payload and llm_payload.get("nodes"):
        # 标准化：category 中文映射 + from/to→source/target + 模糊边清洗（2026-08-06 修复：此前为死代码）
        norm = _normalize_llm_graph(llm_payload, max_nodes, chunk_view)
        if norm and norm.get("nodes"):
            llm_payload["nodes"] = norm["nodes"]
            llm_payload["edges"] = norm["edges"]
        llm_payload.setdefault("meta", {})["extractor_mode"] = "llm-first"
        _graph_cache[_cache_key] = (_now, llm_payload)
        return llm_payload

    # 策略2：规则 fallback（LLM 不可用或提取失败时）——降级必须留痕，避免再次“静默降级”无人发现
    logger.warning("Graph extraction DEGRADED to rules-fallback (domain=%s, single_file=%s, chunks=%d)——LLM 主路径未产出，请检查上方 LLM 报错日志", domain, single_file, len(chunks))
    payload = extract_graph_fallback(chunks, max_nodes=max_nodes)
    payload["meta"]["extractor_mode"] = "rules-fallback"
    payload["meta"].setdefault("warnings", []).append("LLM 提取未成功，已用规则兜底；可点击'重试 LLM'强制重新提取")
    if not force_llm:
        _graph_cache[_cache_key] = (_now, payload)
    return payload


def _normalize_llm_graph(llm_result: dict, max_nodes: int, chunk_view: dict) -> dict:
    """将 LLM 提取结果标准化为前端期望的图谱格式"""
    VALID_CATS = {"person", "event", "concept", "organization", "system", "tool", "process", "location"}
    _CAT_MAP = {
        "人物": "person", "地点": "location", "概念": "concept",
        "组织": "organization", "事件": "event", "系统": "system",
        "工具": "tool", "流程": "process", "技术": "tool",
        "理论": "concept", "作品": "concept", "机构": "organization",
    }

    nodes = []
    # ⛔ 2026-08-13：god score 连续映射 weight（不再硬编码 0.7），
    # 让提取端算出的"关键性"能到达前端可视化。
    _min_w, _max_w = 0.4, 1.0
    for n in llm_result.get("nodes", [])[:max_nodes]:
        cat = (n.get("category") or "").strip().lower()
        cat = _CAT_MAP.get(cat, cat)
        if cat not in VALID_CATS:
            cat = "concept"
        node_id = n.get("id") or n.get("label", "").lower().replace(" ", "-")
        raw_score = float(n.get("score", 0.0) or 0.0)
        # score 已在提取端归一化到 0~1；这里线性映射到 [_min_w, _max_w]
        node_weight = round(_min_w + (_max_w - _min_w) * raw_score, 3)
        nodes.append({
            "id": node_id,
            "label": n.get("label", node_id),
            "category": cat,
            "type": cat,
            "weight": node_weight,
            "score": raw_score,
            "summary": f"LLM 提取自 {n.get('source_file', '知识库')}",
            "source_file": n.get("source_file", ""),
        })

    edges = []
    node_map = {n["id"]: n["label"] for n in nodes}
    for e in llm_result.get("edges", []):
        src = e.get("source") or e.get("from")
        tgt = e.get("target") or e.get("to")
        lbl = (e.get("label") or "").strip()
        if not src or not tgt or src == tgt:
            continue
        # ⛔ 2026-08-12：关系标签已在提取器归一为受约束 schema（包含/属于/导致/支持/反对/
        # 实例/提出/应用于/影响/相关），"相关"是合法弱关系类型，不再清洗成 "A→B"。
        # 仅对空标签做兜底占位。
        if not lbl:
            src_label = node_map.get(src, src)
            tgt_label = node_map.get(tgt, tgt)
            lbl = f"{src_label[:6]}→{tgt_label[:6]}"
        edge_id = e.get("id") or f"{src}-{tgt}"
        edges.append({
            "id": edge_id,
            "source": src,
            "target": tgt,
            "from": src,
            "to": tgt,
            "label": lbl,
            "weight": float(e.get("weight", 0.6)),
        })

    # 孤立节点处理（修复 2026-08-06）：旧逻辑直接丢弃无任何边的节点，
    # 导致长文档实体被大量砍掉（如 20 节点被砍到 15）；实体本身有价值，保留展示
    connected = set()
    for e in edges:
        connected.add(e["from"])
        connected.add(e["to"])

    graph_payload = {
        "version": "graph-data.v1",
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "layout_hint": "force-network",
            "graph_title": "LLM 知识图谱",
            "graph_mode": "concept",
        },
    }

    # Verifier: 图谱质量自动评分
    try:
        v_result = verifier.verify_graph({"nodes": nodes, "edges": edges, "domain": "ai_knowledge"})
        graph_payload["meta"]["verification"] = v_result.to_dict()
        if not v_result.passed:
            # 记录警告但不阻塞，前端可展示评分
            graph_payload["meta"]["warnings"] = v_result.warnings
    except Exception as e:
        graph_payload["meta"]["verification_error"] = str(e)

    return graph_payload


def _resolve_graph_payload(
    source_mode: str,
    domain: str | None,
    max_nodes: int,
    selection_profile: str,
    sorting_strategy: str,
    max_chunks: int,
    focus_concept: str | None = None,
    single_file: str | None = None,
    graph_mode: str = "auto",
    force_llm: bool = False,
) -> tuple[dict, str]:
    normalized_mode = _normalize_source_mode(source_mode)
    normalized_profile = _normalize_selection_profile(selection_profile)
    normalized_strategy = _normalize_sorting_strategy(sorting_strategy)
    normalized_max_chunks = _normalize_max_chunks(max_chunks)
    normalized_graph_mode = graph_mode if graph_mode in ("auto", "concept", "structure") else "auto"
    if normalized_mode in {"auto", "live"}:
        live_payload = _try_load_live_graph_payload(
            domain=domain,
            max_nodes=max_nodes,
            selection_profile=normalized_profile,
            sorting_strategy=normalized_strategy,
            max_chunks=normalized_max_chunks,
            focus_concept=focus_concept,
            single_file=single_file,
            graph_mode=normalized_graph_mode,
            force_llm=force_llm,
        )
        if live_payload:
            return live_payload, "live-knowledge-base"
        if normalized_mode == "live":
            raise HTTPException(
                status_code=503,
                detail="当前知识库没有可用于图谱的 live chunks，请先完成索引或改用 sample 模式。",
            )

    sample_payload = _load_json_payload(
        SAMPLE_GRAPH_PATH,
        "图谱样例数据不存在，请先确认 output/graph/graph-data-sample.v1.json 已生成。",
    )
    sample_payload.setdefault("meta", {})
    sample_payload["meta"]["source_mode"] = "sample-graph-data"
    sample_payload["meta"]["selection_profile"] = normalized_profile
    sample_payload["meta"]["sorting_strategy"] = normalized_strategy
    sample_payload["meta"]["selected_chunk_count"] = len(sample_payload.get("nodes", []))
    sample_payload["meta"]["available_chunk_count"] = len(sample_payload.get("nodes", []))
    sample_payload["meta"]["focus_result"] = {
        "focus_concept": focus_concept or "",
        "matched_chunk_count": 0,
        "fallback_used": bool(focus_concept),
    }
    return sample_payload, "sample-graph-data"


def _read_edge_endpoint(edge: dict, side: str) -> str:
    if side == "source":
        return str(edge.get("source") or edge.get("from") or "")
    return str(edge.get("target") or edge.get("to") or "")


def _file_identity(a: str, b: str) -> bool:
    """文件身份归一化比较：chunks/节点的 source_file 用 file_name（无目录前缀），
    而 single_file 参数用 physical_name（带目录前缀，如 '地球编年史/地球编年史1第十二个天体.epub'）。
    严格相等优先，尾段（basename）一致即视为同一文件，避免子目录文档被后过滤清空。"""
    if not a or not b:
        return False
    if a == b:
        return True
    return a.replace("\\", "/").split("/")[-1] == b.replace("\\", "/").split("/")[-1]


def _filter_graph_payload_to_file(payload: dict, effective_file: str | None) -> dict:
    if not effective_file or not payload.get("nodes"):
        return payload

    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if _file_identity(node.get("source_file", ""), effective_file)
        or _file_identity(node.get("physical_name", ""), effective_file)
    ]
    node_ids = {str(node["id"]) for node in payload["nodes"]}
    payload["edges"] = [
        edge
        for edge in payload.get("edges", [])
        if _read_edge_endpoint(edge, "source") in node_ids and _read_edge_endpoint(edge, "target") in node_ids
    ]
    payload.setdefault("meta", {})
    payload["meta"]["effective_file_filter"] = effective_file
    return payload


def _build_rule_backed_chain(request: GraphChainRequest) -> dict:
    focus_concept = request.focus_concept or request.concept
    graph_payload, graph_source_mode = _resolve_graph_payload(
        source_mode=request.source_mode,
        domain=request.domain,
        max_nodes=max(request.max_chain_steps + 3, 8),
        selection_profile=request.selection_profile,
        sorting_strategy=request.sorting_strategy,
        max_chunks=request.max_chunks,
        focus_concept=focus_concept,
    )
    nodes = graph_payload.get("nodes", [])
    edges = graph_payload.get("edges", [])
    if not nodes:
        raise HTTPException(status_code=500, detail="当前图状态缺少 nodes。")

    matched_node = _match_node(nodes, request.concept)
    if matched_node is None:
        raise HTTPException(
            status_code=404,
            detail="当前规则链暂未识别该概念，请先用 /api/graph/data 或 /api/graph/extract 确认图中是否存在该节点。",
        )

    node_lookup = {str(node["id"]): node for node in nodes}
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for edge in edges:
        outgoing.setdefault(str(edge.get("from", "")), []).append(edge)
        incoming.setdefault(str(edge.get("to", "")), []).append(edge)

    visited = {str(matched_node["id"])}
    ordered_steps = [
        {
            "order": 1,
            "concept_id": str(matched_node["id"]),
            "label": str(matched_node.get("label", matched_node["id"])),
            "relation_from_prev": None,
            "source_file": matched_node.get("source_file"),
        }
    ]

    current_node = matched_node
    while len(ordered_steps) < request.max_chain_steps:
        candidates = [
            edge for edge in _sort_edges(outgoing.get(str(current_node["id"]), []))
            if str(edge.get("to", "")) not in visited and str(edge.get("to", "")) in node_lookup
        ]
        if not candidates:
            break

        chosen_edge = candidates[0]
        next_node = node_lookup[str(chosen_edge["to"])]
        visited.add(str(next_node["id"]))
        ordered_steps.append(
            {
                "order": len(ordered_steps) + 1,
                "concept_id": str(next_node["id"]),
                "label": str(next_node.get("label", next_node["id"])),
                "relation_from_prev": str(chosen_edge.get("label") or chosen_edge.get("relation_type") or "相关"),
                "source_file": next_node.get("source_file"),
            }
        )
        current_node = next_node

    chain_labels = [step["label"] for step in ordered_steps]
    chain_id = f"{matched_node['id']}-rule-chain"
    chain_summary = " -> ".join(chain_labels)
    chain_payload = {
        "chain_id": chain_id,
        "title": f"{chain_labels[0]} 的规则串联链",
        "steps": ordered_steps,
        "summary": f"基于当前图数据和边关系，从 {chain_labels[0]} 出发得到的最小串联链：{chain_summary}。",
    }

    gap_hints: list[dict] = []
    first_step = ordered_steps[0]
    last_step = ordered_steps[-1]

    upstream_candidates = [
        edge for edge in _sort_edges(incoming.get(first_step["concept_id"], []))
        if str(edge.get("from", "")) not in visited and str(edge.get("from", "")) in node_lookup
    ]
    if upstream_candidates:
        source_node = node_lookup[str(upstream_candidates[0]["from"])]
        gap_hints.append(
            _make_gap_hint(
                concept_id=str(source_node["id"]),
                label=str(source_node.get("label", source_node["id"])),
                reason=f"当前链从 {first_step['label']} 起步，但图上还有上游概念尚未纳入当前链。",
                suggested_next_step=f"补看 {source_node.get('label', source_node['id'])} 与 {first_step['label']} 的关系，避免只看中段不看来源。",
                priority="medium",
            )
        )
    else:
        gap_hints.append(
            _make_gap_hint(
                concept_id=f"{first_step['concept_id']}-upstream-context",
                label=f"{first_step['label']} 的上游背景",
                reason=f"当前图里还没有 {first_step['label']} 更上游的明确关系，容易只看到局部链条。",
                suggested_next_step=f"补充更早的背景概念或来源文档，给 {first_step['label']} 建立前置上下文。",
                priority="medium",
            )
        )

    downstream_candidates = [
        edge for edge in _sort_edges(outgoing.get(last_step["concept_id"], []))
        if str(edge.get("to", "")) not in visited and str(edge.get("to", "")) in node_lookup
    ]
    if downstream_candidates:
        target_node = node_lookup[str(downstream_candidates[0]["to"])]
        gap_hints.append(
            _make_gap_hint(
                concept_id=str(target_node["id"]),
                label=str(target_node.get("label", target_node["id"])),
                reason=f"当前链暂时停在 {last_step['label']}，但图上还有下游关系未展开。",
                suggested_next_step=f"继续追踪到 {target_node.get('label', target_node['id'])}，看这条链是否还能延伸。",
                priority="high",
            )
        )
    else:
        gap_hints.append(
            _make_gap_hint(
                concept_id=f"{last_step['concept_id']}-downstream-context",
                label=f"{last_step['label']} 的后续应用",
                reason=f"当前链在 {last_step['label']} 处结束，说明图里还缺少它往后连接到模块、应用或整体结构的关系。",
                suggested_next_step=f"补充 {last_step['label']} 所在模块、应用场景或上层结构，避免链条停在孤立概念。",
                priority="high",
            )
        )

    return {
        "version": "graph-chain.v1",
        "generated_at": graph_payload.get("generated_at"),
        "request": {
            "concept": request.concept,
            "domain": request.domain,
            "max_chain_steps": request.max_chain_steps,
            "max_gap_hints": request.max_gap_hints,
            "selection_profile": _normalize_selection_profile(request.selection_profile),
            "sorting_strategy": _normalize_sorting_strategy(request.sorting_strategy),
            "max_chunks": _normalize_max_chunks(request.max_chunks),
            "focus_concept": focus_concept,
        },
        "related_chain": [chain_payload],
        "gap_hints": gap_hints[: request.max_gap_hints],
        "meta": {
            "source_mode": f"rule-backed-{graph_source_mode}",
            "graph_version": graph_payload.get("version", "graph-data.v1"),
            "selection_profile": graph_payload.get("meta", {}).get("selection_profile", "balanced"),
            "sorting_strategy": graph_payload.get("meta", {}).get("sorting_strategy", "relevance"),
            "selected_chunk_count": graph_payload.get("meta", {}).get("selected_chunk_count"),
            "available_chunk_count": graph_payload.get("meta", {}).get("available_chunk_count"),
            "focus_result": graph_payload.get("meta", {}).get("focus_result"),
            "warnings": [
                "当前为规则驱动最小链路，不代表完整智能推理。",
                "链路质量仍取决于当前图数据是否足够丰富。"
            ],
        },
    }


@router.get("/data")
def get_graph_data(
    source_mode: str = Query(default="auto", description="图状态来源：auto / live / sample"),
    domain: str = Query(default="ai_knowledge", description="知识域"),
    max_nodes: int = Query(default=12, ge=2, le=40, description="最多返回多少个节点"),
    selection_profile: str = Query(default="balanced", description="live 图状态选材档位：compact / balanced / wide"),
    sorting_strategy: str = Query(default="relevance", description="live 图状态排序策略：relevance / recency / diversity"),
    max_chunks: int = Query(default=48, ge=6, le=200, description="live 图状态最多消费多少个 chunks"),
    focus_concept: str | None = Query(default=None, description="可选：围绕某个概念聚焦 live 图状态"),
    focus_file: str | None = Query(default=None, description="已废弃：请使用 single_file。仅展示某个文件相关的图谱（后过滤，效率低）"),
    single_file: str | None = Query(default=None, description="可选：仅对单个文件生成图谱（physical_name，前端精确收集chunks）"),
    graph_mode: str = Query(default="auto", description="图谱模式：auto=自动检测, concept=概念图谱, structure=内容架构（章节/标题层级）"),
    force_llm: bool = Query(default=False, description="跳过缓存并强制 LLM 重新提取（降级后自救：点'重试 LLM'）"),
):
    """返回当前图状态，默认优先 live，无数据时回退样例。"""
    payload, _ = _resolve_graph_payload(
        source_mode=source_mode,
        domain=domain,
        max_nodes=max_nodes,
        selection_profile=selection_profile,
        sorting_strategy=sorting_strategy,
        max_chunks=max_chunks,
        focus_concept=focus_concept,
        single_file=single_file,
        graph_mode=graph_mode,
        force_llm=force_llm,
    )
    return _filter_graph_payload_to_file(payload, single_file or focus_file)


def prewarm_live_graph() -> dict:
    """预热默认 live 图谱缓存（T11 轻量落地，2026-08-12）。
    缓存新鲜则跳过；否则执行一次 live 提取（含 LLM，耗时可长，应放后台线程）。
    完整异步队列/周期性再生成仍登记 TODO T11。
    """
    import time as _t
    cache_key = "ai_knowledge|balanced|relevance|12|None|None|auto"
    _now = _t.time()
    if cache_key in _graph_cache and _now - _graph_cache[cache_key][0] < _GRAPH_CACHE_TTL:
        return {"status": "ok", "cached": True}
    try:
        payload, mode = _resolve_graph_payload(
            source_mode="live",
            domain="ai_knowledge",
            max_nodes=12,
            selection_profile="balanced",
            sorting_strategy="relevance",
            max_chunks=48,
            focus_concept=None,
            single_file=None,
            graph_mode="auto",
        )
        return {
            "status": "ok",
            "cached": False,
            "source_mode": mode,
            "nodes": len(payload.get("nodes", [])),
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)[:200]}


@router.post("/prewarm")
def prewarm_graph():
    """T11：后台预热 live 图谱，立即返回，不阻塞请求。"""
    import threading

    def _warm():
        try:
            prewarm_live_graph()
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True).start()
    return {"status": "started"}


@router.post("/extract")
def extract_graph_data(request: GraphExtractRequest):
    """基于规则优先策略，从文本或文件中提取最小图谱。"""
    if not request.text and not request.file_path:
        raise HTTPException(
            status_code=400,
            detail="请至少提供 text 或 file_path 其中一个输入。"
        )

    payload = extractor.extract_from_request(
        text=request.text,
        file_path=request.file_path,
        source_file=request.source_file,
        domain=request.domain,
        max_nodes=request.max_nodes,
        graph_mode=request.graph_mode,
    )

    if not payload["nodes"]:
        raise HTTPException(
            status_code=422,
            detail="当前输入未抽取到足够概念节点，请换更明确的文本或文件。"
        )

    # Verifier: 图谱质量自动评分
    try:
        v_result = verifier.verify_graph({
            "nodes": payload.get("nodes", []),
            "edges": payload.get("edges", []),
            "domain": request.domain or "ai_knowledge"
        })
        payload["meta"] = payload.get("meta", {})
        payload["meta"]["verification"] = v_result.to_dict()
        if not v_result.passed:
            payload["meta"]["warnings"] = v_result.warnings
    except Exception as e:
        payload.setdefault("meta", {})["verification_error"] = str(e)

    return payload


@router.post("/chain")
def get_graph_chain(request: GraphChainRequest):
    """基于当前图数据返回最小规则链和缺口提示。"""
    return _build_rule_backed_chain(request)


class ConceptExplainRequest(BaseModel):
    concept: str = Field(..., description="要解释的概念名")
    max_tokens: int = Field(default=300, ge=100, le=600)


@router.post("/explain")
def explain_concept(request: ConceptExplainRequest):
    """AI深度解释一个概念——用户双击图谱节点时触发"""
    from app.rag_app.shared_engine import get_engine

    try:
        eng = get_engine()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"引擎初始化失败: {e}")

    # 从知识库中检索与这个概念相关的内容
    try:
        results = eng.kb.search(request.concept, top_k=5)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"知识库检索失败: {e}")

    context_parts = []
    for r in results:
        src = r.get("metadata", {}).get("file_name", "未知")
        text = (r.get("text", "") or "").strip()
        if text:
            context_parts.append(f"[{src}] {text[:300]}")
    context = "\n\n".join(context_parts[:5]) or "知识库中暂无相关内容"

    prompt = (
        f"请用200-350字深度解释这个概念：'{request.concept}'。\n"
        "要求：\n"
        "1. 先给一句话定义（15字以内）\n"
        "2. 展开核心原理或关键特征\n"
        "3. 说明实际应用场景或为什么重要\n"
        "4. 如果知识库有相关内容，引用并说明\n"
        "5. 结构化输出（标题+正文），容易阅读\n\n"
        f"知识库相关内容：\n{context}"
    )

    try:
        response = eng.llm_client.chat.completions.create(
            model=eng.model_name,
            messages=[
                {"role": "system", "content": "你是一位知识导师，擅长用通俗易懂的方式解释复杂概念。请使用中文回答。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=request.max_tokens,
            timeout=30,
        )
        explanation = (response.choices[0].message.content or "").strip()
        return {"status": "ok", "concept": request.concept, "explanation": explanation}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"概念解释生成失败: {e}")


# ===== Graph Execution with Checkpointing =====

@router.post("/run")
def run_graph(request: GraphRunRequest):
    """
    启动或恢复图执行（耐久执行）。
    - 首次调用：提供 graph_def + initial_state，指定 pause_nodes 可在特定节点暂停等人工审批
    - 暂停后恢复：同一 thread_id 再次调用，提供 human_input
    - 时间旅行：指定 resume_from_step 回退到历史步骤分叉新执行
    """
    try:
        result = graph_runner.run(
            graph_def=request.graph_def,
            initial_state=request.initial_state,
            thread_id=request.thread_id,
            pause_nodes=set(request.pause_nodes) if request.pause_nodes else None,
            human_input=request.human_input,
            resume_from_step=request.resume_from_step,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图执行失败: {e}")


@router.get("/run/history/{thread_id}")
def get_execution_history(thread_id: str):
    """获取执行历史（时间旅行用：列出所有检查点）"""
    try:
        history = graph_runner.get_history(thread_id)
        return {
            "thread_id": thread_id,
            "checkpoints": [cp.to_dict() for cp in history],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史失败: {e}")


@router.post("/run/fork")
def fork_execution(request: GraphForkRequest):
    """从历史检查点分叉新执行分支（时间旅行：从某步骤重新开始，不影响原分支）"""
    try:
        forked_cp = graph_runner.fork(request.thread_id, request.from_step, request.new_thread_id)
        return {
            "status": "forked",
            "new_thread_id": forked_cp.thread_id,
            "from_step": forked_cp.step_idx,
            "forked_from": forked_cp.parent_checkpoint_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分叉失败: {e}")


@router.get("/run/pending/{thread_id}/{step_idx}")
def get_pending_writes(thread_id: str, step_idx: int):
    """获取某超级步的待写入输出（失败恢复调试用）"""
    try:
        pending = _checkpointer.get_pending(thread_id, step_idx)
        return {"thread_id": thread_id, "step_idx": step_idx, "pending_writes": [p.to_dict() for p in pending]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 pending 失败: {e}")


# ===== Parallel Graph Generation =====

@router.post("/generate/parallel")
def generate_parallel_graphs(request: ParallelGraphRequest):
    """
    并行生成多个域的知识图谱（Fan-out/Fan-in）
    - Fan-out: 每个域独立并行生成图谱
    - Fan-in: 合并结果，返回统一响应
    - 内置 LLM 限流保护
    """
    from app.rag_app.shared_engine import get_kb
    from app.rag_app.thesis_extractor import extract_document_thesis

    def _generate_one(domain: str) -> tuple[str, dict]:
        """生成单个域的图谱"""
        chunk_view = get_kb().get_graph_source_chunk_view(
            domain=domain,
            max_chunks=request.max_chunks,
            selection_profile=request.selection_profile,
            sorting_strategy=request.sorting_strategy,
            focus_concept=request.focus_concept,
            single_file=None,
        )
        chunks = chunk_view["chunks"]
        if not chunks:
            return domain, {"error": f"域 {domain} 无可用 chunks"}

        # 提取主旨
        full_text = "\n".join(c.get("text", "") for c in chunks if c.get("text", ""))
        source_file = chunks[0].get("source_file", "unknown") if chunks else "unknown"
        thesis = extract_document_thesis(full_text, source_file)

        # LLM-first 提取
        llm_payload = extract_llm_graph(chunks, max_nodes=request.max_nodes, thesis=thesis)
        if llm_payload and llm_payload.get("nodes"):
            # 标准化：category 中文映射 + from/to→source/target + 模糊边清洗（2026-08-06 修复）
            norm = _normalize_llm_graph(llm_payload, request.max_nodes, chunk_view)
            if norm and norm.get("nodes"):
                llm_payload["nodes"] = norm["nodes"]
                llm_payload["edges"] = norm["edges"]
            llm_payload.setdefault("meta", {})["extractor_mode"] = "llm-first"
            return domain, llm_payload

        # Fallback: 规则提取——降级必须留痕
        logger.warning("Batch graph extraction DEGRADED to rules-fallback (domain=%s)——请检查上方 LLM 报错日志", domain)
        payload = extract_graph_fallback(chunks, max_nodes=request.max_nodes)
        payload["meta"]["extractor_mode"] = "rules-fallback"
        return domain, payload

    # 并行执行（带限流）
    try:
        result = parallel_graph_generation(
            request.domains,
            _generate_one,
            max_workers=min(3, len(request.domains)),
            rate_limiter=_llm_rate_limiter,
            on_progress=lambda c, t: logger.info(f"Parallel graph generation: {c}/{t}"),
        )
    except Exception as e:
        logger.error(f"Parallel graph generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"并行图谱生成失败: {e}")

    # Fan-in: 整合结果
    results = {}
    errors = {}
    for domain, payload in zip(request.domains, result.results):
        if isinstance(payload, tuple) and len(payload) == 2:
            d, p = payload
        else:
            d, p = domain, payload
        if "error" in p:
            errors[d] = p["error"]
        else:
            # Verifier 评分
            try:
                v_result = verifier.verify_graph({
                    "nodes": p.get("nodes", []),
                    "edges": p.get("edges", []),
                    "domain": d,
                })
                p["meta"] = p.get("meta", {})
                p["meta"]["verification"] = v_result.to_dict()
                if not v_result.passed:
                    p["meta"]["warnings"] = v_result.warnings
            except Exception as ve:
                p.setdefault("meta", {})["verification_error"] = str(ve)
            results[d] = p

    # 汇总
    summary = {
        "total_domains": len(request.domains),
        "success_count": len(results),
        "error_count": len(errors),
        "total_nodes": sum(len(p.get("nodes", [])) for p in results.values()),
        "total_edges": sum(len(p.get("edges", [])) for p in results.values()),
    }

    return ParallelGraphResponse(
        results=results,
        errors=errors,
        summary=summary,
    )
