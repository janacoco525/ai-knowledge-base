#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/stategraph 手工验证脚本（2026-07-31 从项目根迁入 tests/，导入路径追平 core/ 包）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.stategraph import StateGraph, GraphExecutor
from core.checkpointer import InMemoryCheckpointer


def _research(state):
    print(f"  research called with state keys: {list(state.keys())}")
    return {'notes': 'Research on ' + state['topic'], 'sources': ['src1', 'src2']}

def _write(state):
    return {'draft': 'Draft based on ' + str(state.get('notes'))}

def _review(state):
    human = state.get('human_input', {})
    if human.get('approved'):
        return {'final': state.get('draft')}
    return {'revision_needed': True, 'feedback': human.get('feedback')}

def test_pause_resume_flow():
    """建图 → 暂停在 review → 人工批准 → 恢复执行完成"""
    graph = StateGraph()
    graph.add_node("research", _research, type="llm", parallel=True, timeout=120)
    graph.add_node("write", _write, type="llm", parallel=True, timeout=120)
    graph.add_node("review", _review, type="human", pause_before=True)
    graph.add_edge("research", "write")
    graph.add_edge("write", "review")
    graph.add_edge("review", "write", condition="revision_needed")
    graph.set_entry("research")

    compiled = graph.compile()
    checkpointer = InMemoryCheckpointer()
    executor = GraphExecutor(compiled, checkpointer=checkpointer)

    result = executor.invoke({'topic': 'Test Topic'}, thread_id='simple-test', pause_nodes={'review'})
    assert result['status'] in ('paused', 'completed'), f"unexpected status: {result['status']}"

    result = executor.resume('simple-test', {'approved': True})
    assert result['status'] == 'completed', f"resume failed: {result['status']}"
    assert 'final' in result.get('final_state', {}), "final state missing 'final' key"
    print("[OK] pause/resume flow passed")

if __name__ == "__main__":
    test_pause_resume_flow()
