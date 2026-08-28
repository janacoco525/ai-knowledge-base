import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.rag_app.routes.llm_ops import _parse_llm_json
from app.rag_app.routes.providers import _mask_key


# ============ _parse_llm_json ============

def test_parse_valid_json():
    assert _parse_llm_json('{"nodes": [], "edges": []}') == {"nodes": [], "edges": []}


def test_parse_json_with_trailing_comma():
    result = _parse_llm_json('{"nodes": [], "edges": [],}')
    assert "nodes" in result


def test_parse_json_from_llm_markdown():
    raw = 'Here is the result:\n```json\n{"topic": "AI", "children": []}\n```\nDone.'
    result = _parse_llm_json(raw)
    assert result["topic"] == "AI"


def test_parse_empty_string():
    assert _parse_llm_json("") == {}


def test_parse_garbage():
    assert _parse_llm_json("no json here") == {}


def test_parse_json_with_unquoted_keys():
    raw = '{nodes: [{"id": "n1"}]}'
    result = _parse_llm_json(raw)
    assert "nodes" in result


# ============ _mask_key ============

def test_mask_normal_key():
    masked = _mask_key("sk-cz8woabcdefgh1234")
    assert masked.startswith("sk-cz")
    assert masked.endswith("1234")
    assert "..." in masked


def test_mask_short_key():
    assert _mask_key("short") == ""


def test_mask_empty_key():
    assert _mask_key("") == ""


def test_mask_none_key():
    assert _mask_key(None) == ""


# ============ Module imports ============

def test_providers_router_importable():
    from app.rag_app.routes.providers import router
    assert router is not None


def test_llm_ops_router_importable():
    from app.rag_app.routes.llm_ops import router
    assert router is not None


def test_gemini_adapter_router_importable():
    from app.rag_app.routes.gemini_adapter import router
    assert router is not None
