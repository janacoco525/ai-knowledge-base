"""图谱受约束 schema + 实体解析 + god nodes 排序回归（2026-08-12 A+B）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.llm_graph_extractor import (
    _canonical_label,
    _dedupe_and_rank,
    _normalize_relation,
    _looks_like_abstract_concept,
)


def test_canonical_label_merges_aliases():
    assert _canonical_label("瑞·达利欧") == "达利欧"
    assert _canonical_label("Ray Dalio") == "达利欧" == _canonical_label("Ray Dalio(Ray Dalio)")
    assert _canonical_label(" 达利欧 ") == "达利欧"


def test_relation_normalized_to_schema():
    assert _normalize_relation("发明了") == "提出"
    assert _normalize_relation("导致") == "导致"
    assert _normalize_relation("出生于") == "属于"
    assert _normalize_relation(")") is None  # 无法映射 → 丢弃


def test_dedupe_and_rank_god_nodes():
    raw_nodes = [
        {"label": "达利欧", "category": "person"},
        {"label": "瑞·达利欧", "category": "person"},
        {"label": "痛苦+反思=进步", "category": "concept"},
        {"label": "可信度加权", "category": "concept"},
        {"label": "极度透明", "category": "concept"},
        {"label": "原则", "category": "concept"},
    ]
    raw_edges = [
        {"from": "达利欧", "to": "原则", "label": "写了"},
        {"from": "瑞·达利欧", "to": "痛苦+反思=进步", "label": "提出"},
        {"from": "痛苦+反思=进步", "to": "可信度加权", "label": "相关"},
        {"from": "极度透明", "to": "可信度加权", "label": "支持"},
    ]
    nodes, edges = _dedupe_and_rank(raw_nodes, raw_edges, max_nodes=4)
    labels = {n["label"] for n in nodes}
    # 达利欧 别名已合并（不出现两个节点）
    assert "达利欧" in labels
    assert "瑞·达利欧" not in labels
    assert len(nodes) <= 4
    # 关系全部归一为 schema 类型
    for e in edges:
        assert e["label"] in {
            "包含", "属于", "导致", "支持", "反对", "实例",
            "提出", "应用于", "影响", "相关",
        }
    # 边两端都在节点集内
    for e in edges:
        assert e["from"] in labels and e["to"] in labels


def test_high_degree_node_survives_truncation():
    raw_nodes = [
        {"label": f"概念{i}", "category": "concept"} for i in range(10)
    ]
    raw_edges = [
        {"from": "概念0", "to": f"概念{i}", "label": "支持"} for i in range(1, 10)
    ]
    nodes, _ = _dedupe_and_rank(raw_nodes, raw_edges, max_nodes=3)
    # 概念0 度最高，必须留在 top3（god nodes 排序生效）
    assert any(n["label"] == "概念0" for n in nodes)
    # ⛔ 2026-08-13：god score 必须随节点输出（否则前端看不到关键点）
    assert all("score" in n for n in nodes)
    hub = next(n for n in nodes if n["label"] == "概念0")
    assert hub["score"] >= 0.9  # 最高分节点接近 1.0
    assert 0.0 <= hub["score"] <= 1.0


def test_person_label_heuristics():
    # LLM 把抽象概念标成 person 的二次纠偏（2026-08-12）
    assert not _looks_like_abstract_concept("乔布斯")   # 真实人名不误伤
    assert _looks_like_abstract_concept("机器") is False
