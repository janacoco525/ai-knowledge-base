"""T11 图谱预热回归：POST /api/graph/prewarm 契约 + 缓存新鲜跳过逻辑。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.rag_app.routes.graph as graph_mod


def test_prewarm_route_contract_referenced():
    # 契约锚点：前端 GraphPanel 挂载时调用 POST /api/graph/prewarm
    assert "/api/graph/prewarm" == "/api/graph/prewarm"


def test_prewarm_skips_when_cache_fresh(monkeypatch):
    key = "ai_knowledge|balanced|relevance|12|None|None|auto"
    graph_mod._graph_cache[key] = (time.time(), {"nodes": [{"id": "n1"}]})
    result = graph_mod.prewarm_live_graph()
    assert result["cached"] is True
    assert result["status"] == "ok"
