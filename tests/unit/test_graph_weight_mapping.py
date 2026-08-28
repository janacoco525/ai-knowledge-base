"""图谱 god score → 前端 weight 连续映射回归（2026-08-13 关键节点突出）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.routes.graph import _normalize_llm_graph


def _chunk_view(nodes):
    return {"chunks": [{"text": "x", "source_file": "book.md"}], "nodes": nodes}


def test_node_weight_follows_score_monotonically():
    llm_result = {
        "nodes": [
            {"id": "a", "label": "核心概念", "category": "concept", "score": 1.0},
            {"id": "b", "label": "普通概念", "category": "concept", "score": 0.5},
            {"id": "c", "label": "边缘概念", "category": "concept", "score": 0.1},
        ],
        "edges": [],
    }
    norm = _normalize_llm_graph(llm_result, max_nodes=3, chunk_view=_chunk_view([]))
    w = {n["label"]: n["weight"] for n in norm["nodes"]}
    assert w["核心概念"] > w["普通概念"] > w["边缘概念"]
    assert w["核心概念"] >= 0.9      # score 1.0 → weight≈1.0
    assert w["边缘概念"] <= 0.5      # score 0.1 → weight≈0.46


def test_node_weight_fallback_without_score():
    llm_result = {
        "nodes": [
            {"id": "a", "label": "无分概念", "category": "concept"},
        ],
        "edges": [],
    }
    norm = _normalize_llm_graph(llm_result, max_nodes=1, chunk_view=_chunk_view([]))
    assert norm["nodes"][0]["weight"] == 0.4  # 无 score → 下限权重
