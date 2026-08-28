#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/stategraph 手工验证脚本（2026-07-31 从项目根迁入 tests/，导入路径追平 core/ 包）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stategraph import StateGraph, GraphExecutor
from core.checkpointer import InMemoryCheckpointer


from core.stategraph import create_research_graph

def _research(state):
    return {'notes': 'Research on ' + state['topic'], 'sources': ['src1', 'src2']}

def _write(state):
    return {'draft': 'Draft based on ' + str(state.get('notes'))}

def _review(state):
    human = state.get('human_input', {})
    if human.get('approved'):
        return {'final': state.get('draft')}
    return {'revision_needed': True, 'feedback': human.get('feedback')}

def test_research_graph_structure_and_flow():
    """create_research_graph 工厂：结构校验 + 暂停/恢复全流程"""
    checkpointer = InMemoryCheckpointer()
    compiled, executor = create_research_graph(_research, _write, _review, checkpointer=checkpointer)

    layers = compiled.get_layers()
    nodes = list(compiled.graph.nodes.keys())
    edges = [(e.source, e.target, e.condition) for e in compiled.graph.edges]
    print('Graph layers:', layers)
    print('Nodes:', nodes)
    print('Edges:', edges)
    assert {'research', 'write', 'review'} <= set(nodes), "missing nodes"

    result = executor.invoke({'topic': 'Graph Engineering'}, thread_id='test-1', pause_nodes={'review'})
    assert result['status'] == 'paused', f"expected paused, got {result['status']}"
    assert result.get('paused_at'), "paused_at empty"

    result = executor.resume('test-1', {'approved': True})
    assert result['status'] == 'completed', f"resume failed: {result['status']}"
    assert 'final' in result.get('final_state', {}), "final state missing 'final' key"
    print("[OK] research graph structure/flow passed")

if __name__ == "__main__":
    test_research_graph_structure_and_flow()
