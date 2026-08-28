import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.rag_app.routes.graph import _filter_graph_payload_to_file


def test_filter_graph_payload_keeps_from_to_edges_for_single_file():
    payload = {
        "nodes": [
            {"id": "n1", "label": "概念A", "source_file": "doc-a.md"},
            {"id": "n2", "label": "概念B", "source_file": "doc-a.md"},
            {"id": "n3", "label": "概念C", "source_file": "doc-b.md"},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "label": "导致"},
            {"id": "e2", "from": "n2", "to": "n3", "label": "延伸"},
        ],
        "meta": {},
    }

    filtered = _filter_graph_payload_to_file(payload, "doc-a.md")

    assert [node["id"] for node in filtered["nodes"]] == ["n1", "n2"]
    assert [edge["id"] for edge in filtered["edges"]] == ["e1"]
    assert filtered["meta"]["effective_file_filter"] == "doc-a.md"


def test_filter_graph_payload_accepts_source_target_edges_too():
    payload = {
        "nodes": [
            {"id": "n1", "label": "概念A", "source_file": "doc-a.md"},
            {"id": "n2", "label": "概念B", "source_file": "doc-a.md"},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "label": "支撑"},
        ],
    }

    filtered = _filter_graph_payload_to_file(payload, "doc-a.md")

    assert [edge["id"] for edge in filtered["edges"]] == ["e1"]
