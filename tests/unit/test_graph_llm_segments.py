"""图谱大文档处理升级：全书顺序分块 + 归并 + LLM 再总结（2026-08-18）。

对应 llm_graph_extractor.py 的 _build_long_text_sample / _compact_segment /
_call_llm_consolidate / extract_llm_graph 管线改造。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.rag_app.llm_graph_extractor as lge


def test_short_text_single_segment():
    text = "甲" * 6000
    segs = lge._build_long_text_sample(text)
    assert len(segs) == 1
    assert segs[0] == text


def test_medium_text_four_segments_cover_head_and_tail():
    text = "乙" * 200000
    segs = lge._build_long_text_sample(text)
    assert len(segs) == 4
    assert all(len(s) <= 12000 for s in segs)
    # 头尾必须保留：首段以原文开头开头，末段以原文结尾结尾
    assert segs[0].startswith(text[:100])
    assert segs[-1].endswith(text[-100:])


def test_big_text_six_segments():
    text = "戊" * 500000
    segs = lge._build_long_text_sample(text)
    assert len(segs) == 6
    assert all(len(s) <= 12000 for s in segs)
    assert segs[0].startswith(text[:100])
    assert segs[-1].endswith(text[-100:])


def test_large_text_eight_segments():
    text = "丙" * 1200000
    segs = lge._build_long_text_sample(text)
    assert len(segs) == 8
    assert all(len(s) <= 12000 for s in segs)
    assert segs[0].startswith(text[:100])
    assert segs[-1].endswith(text[-100:])


def test_compact_segment_three_point_sample():
    seg = "丁" * 100000
    compacted = lge._compact_segment(seg, cap=12000)
    assert len(compacted) <= 12000
    assert "……[中略]……" in compacted
    # 前/中/后三点都有采样内容（非纯段首）
    assert compacted.startswith("丁" * 100)
    assert compacted.endswith("丁" * 100)


class _FakeCompletions:
    """最小可用 fake：client.chat.completions.create(...) → 固定响应。"""

    def __init__(self, content, finish_reason="stop"):
        self._content = content
        self._finish = finish_reason

    def create(self, **kwargs):
        class _Msg:
            pass

        m = _Msg()
        m.content = self._content

        class _Choice:
            pass

        c = _Choice()
        c.message = m
        c.finish_reason = self._finish

        class _Resp:
            pass

        r = _Resp()
        r.choices = [c]
        return r


def _patch_client(monkeypatch, content, finish_reason="stop"):
    fake = _FakeCompletions(content, finish_reason)
    client = type("Client", (), {"chat": type("Chat", (), {"completions": fake})})()
    monkeypatch.setattr(lge, "_get_llm_client", lambda: client)
    return fake


def test_consolidate_merges_synonyms_and_renames_edges(monkeypatch):
    _patch_client(
        monkeypatch,
        (
            '{"nodes": [{"label": "叶文洁", "category": "person"}, '
            '{"label": "红岸基地", "category": "location"}], '
            '"edges": [{"from": "叶文洁", "to": "红岸基地", "label": "属于"}]}'
        ),
    )
    nodes = [
        {"label": "叶文洁", "category": "person", "count": 3, "degree": 2, "score": 1.0},
        {"label": "叶文洁(红岸基地)", "category": "person", "count": 2, "degree": 1, "score": 0.8},
        {"label": "红岸基地", "category": "location", "count": 2, "degree": 1, "score": 0.7},
    ]
    edges = [
        {"from": "叶文洁", "to": "红岸基地", "label": "属于", "weight": 0.8},
        {"from": "叶文洁(红岸基地)", "to": "红岸基地", "label": "属于", "weight": 0.8},
    ]
    result = lge._call_llm_consolidate(nodes, edges, max_nodes=3)
    assert result is not None
    labels = [n["label"] for n in result["nodes"]]
    # 同义节点合并为一个"叶文洁"，"叶文洁(红岸基地)"消失
    assert labels.count("叶文洁") == 1
    assert "叶文洁(红岸基地)" not in labels
    # 边跟随实体改名，两端都在节点集内
    for e in result["edges"]:
        assert e["from"] in labels and e["to"] in labels


def test_consolidate_failure_returns_none(monkeypatch):
    fake = _FakeCompletions("", "stop")
    client = type("Client", (), {"chat": type("Chat", (), {"completions": fake})})()
    fake.create = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setattr(lge, "_get_llm_client", lambda: client)
    nodes = [{"label": "节点A", "category": "concept", "count": 1, "degree": 0, "score": 1.0}]
    assert lge._call_llm_consolidate(nodes, [], max_nodes=3) is None


def test_consolidate_unparseable_returns_none(monkeypatch):
    _patch_client(monkeypatch, "这不是 JSON，也没有任何 label 字段")
    nodes = [{"label": "节点A", "category": "concept", "count": 1, "degree": 0, "score": 1.0}]
    assert lge._call_llm_consolidate(nodes, [], max_nodes=3) is None


def _make_chunks(n, per_chunk_text):
    return [
        {
            "text": f"第{i}章。{per_chunk_text}",
            "source_file": "book.md",
            "chunk_index": i,
            "source_chunk_id": f"c{i}",
        }
        for i in range(n)
    ]


def _patch_pipeline(monkeypatch, tmp_path, extract_result, consolidate_result=None):
    calls = {"extract": 0, "consolidate": 0}

    def fake_extract(text, max_nodes=6, thesis=None):
        calls["extract"] += 1
        return extract_result

    def fake_consolidate(nodes, edges, max_nodes, thesis=None):
        calls["consolidate"] += 1
        return consolidate_result

    monkeypatch.setattr(lge, "_call_llm_extract", fake_extract)
    monkeypatch.setattr(lge, "_call_llm_consolidate", fake_consolidate)
    monkeypatch.setattr(lge, "_cache_path", lambda h: str(tmp_path / f"{h}.json"))
    return calls


def test_extract_pipeline_multisegment_calls_consolidate(monkeypatch, tmp_path):
    chunks = _make_chunks(40, "内容片段" * 200)  # 40 × ~800 字 → >12000 触发 4 段
    extract_result = {
        "nodes": [{"label": "概念A", "category": "concept"}, {"label": "人物甲", "category": "person"}],
        "edges": [{"from": "人物甲", "to": "概念A", "label": "提出"}],
    }
    calls = _patch_pipeline(
        monkeypatch, tmp_path, extract_result,
        consolidate_result={"nodes": [{"label": "概念A", "category": "concept"}], "edges": []},
    )
    result = lge.extract_llm_graph(chunks, max_nodes=6, use_parallel=False)
    assert result is not None and result["nodes"]
    assert calls["extract"] == 4
    assert calls["consolidate"] == 1
    assert result["meta"]["segment_count"] == 4
    assert len(result["nodes"]) <= 6


def test_extract_pipeline_single_segment_skips_consolidate(monkeypatch, tmp_path):
    chunks = _make_chunks(5, "短内容片段。" * 20)  # 总长 ~630 字（>50 且 <12000）→ 1 段
    extract_result = {
        "nodes": [{"label": "概念B", "category": "concept"}],
        "edges": [],
    }
    calls = _patch_pipeline(monkeypatch, tmp_path, extract_result, consolidate_result={"nodes": [], "edges": []})
    result = lge.extract_llm_graph(chunks, max_nodes=6, use_parallel=False)
    assert result is not None and result["nodes"]
    assert calls["extract"] == 1
    assert calls["consolidate"] == 0  # 单段文档不额外调用再总结
    assert result["meta"]["segment_count"] == 1


def test_extract_pipeline_consolidate_failure_keeps_rule_merged(monkeypatch, tmp_path):
    chunks = _make_chunks(40, "内容片段" * 200)
    extract_result = {
        "nodes": [{"label": "概念C", "category": "concept"}],
        "edges": [],
    }
    calls = _patch_pipeline(monkeypatch, tmp_path, extract_result, consolidate_result=None)
    result = lge.extract_llm_graph(chunks, max_nodes=6, use_parallel=False)
    assert result is not None and result["nodes"]
    assert calls["consolidate"] == 1
    # 再总结失败 → 保留规则归并结果，不阻塞、不降级
    assert result["nodes"][0]["label"] == "概念C"
