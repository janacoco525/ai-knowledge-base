"""图谱降级自救回归：force_llm 参数接线 + 降级短缓存不变式。"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.rag_app.routes.graph as graph_mod


def test_force_llm_param_wired():
    # 路由与核心函数都暴露 force_llm（前端"重试 LLM"按钮依赖）
    sig_route = inspect.signature(graph_mod.get_graph_data)
    assert "force_llm" in sig_route.parameters
    sig_core = inspect.signature(graph_mod._try_load_live_graph_payload)
    assert sig_core.parameters["force_llm"].default is False


def test_fallback_ttl_shorter_than_normal():
    # 降级结果只短缓存（60s），llm-first 长缓存（600s）——坏图不能黏 10 分钟
    assert graph_mod._GRAPH_FALLBACK_TTL < graph_mod._GRAPH_CACHE_TTL
    assert graph_mod._GRAPH_FALLBACK_TTL == 60
