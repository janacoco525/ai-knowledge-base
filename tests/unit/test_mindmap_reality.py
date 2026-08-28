"""脑图 reality check 回归（2026-08-12，对标 mindmap-generator 防幻觉）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.routes.llm_ops import _reality_check_children


def test_keeps_points_with_source_evidence():
    segment = "痛苦加上反思等于进步。可信度加权决策能提升团队智慧。"
    children = [
        {"topic": "痛苦加上反思等于进步", "children": []},
        {"topic": "可信度加权提升团队智慧", "children": []},
    ]
    kept = _reality_check_children(children, segment)
    assert len(kept) == 2


def test_drops_hallucinated_points():
    segment = "本章讨论决策原则与可信度加权。"
    children = [
        {"topic": "章鱼有九个大脑", "children": []},       # 无原文依据
        {"topic": "决策原则提升团队智慧", "children": []},  # 有依据（决策原则）
    ]
    kept = _reality_check_children(children, segment)
    assert len(kept) == 1
    assert kept[0]["topic"].startswith("决策原则")


def test_short_topic_kept_when_no_evidence_words():
    # 无法提取证据词的要点（过短/纯标点）不误删
    kept = _reality_check_children([{"topic": "—"}], "任意原文")
    assert len(kept) == 1
