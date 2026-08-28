"""回归测试：脑图生成进度查询 GET /api/gemini/mindmap/progress（功能闭环）。

⛔ 2026-08-20：长文档脑图多章任务耗时 3-5 分钟，前端需轮询进度避免误判卡死。
本测试覆盖进度注册表的查询端点（纯内存，无 LLM、无网络）。

端点契约（与前端 GraphPanel.tsx 轮询调用对齐）：
  GET /api/gemini/mindmap/progress?file_id=xxx
    -> {running, phase, total, done, current, pct, finished}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.routes.llm_ops import (
    mindmap_progress,
    _report_mindmap_progress,
    _MINDMAP_PROGRESS,
    _MINDMAP_PROGRESS_LOCK,
)


def _cleanup(key: str):
    with _MINDMAP_PROGRESS_LOCK:
        _MINDMAP_PROGRESS.pop(key, None)


def test_progress_empty_file_id():
    """无 file_id 直接返回空进度（短文档/缓存命中不注册）。"""
    r = mindmap_progress("")
    assert r["running"] is False and r["total"] == 0 and r["pct"] == 0


def test_progress_unknown_file():
    """未注册的 file_id 返回空进度（前端轮询最多跑 1 次即停止）。"""
    r = mindmap_progress("no-such-doc")
    assert r["running"] is False and r["phase"] == ""


def test_progress_registered_running():
    """已注册的多章任务返回 running=True 与正确百分比。"""
    _report_mindmap_progress("doc-A", phase="chapters", total=10, done=3, current="第3章")
    try:
        r = mindmap_progress("doc-A")
        assert r["running"] is True
        assert r["done"] == 3 and r["total"] == 10
        assert r["pct"] == 30
        assert r["phase"] == "chapters"
    finally:
        _cleanup("doc-A")


def test_progress_finished_state():
    """完成态返回 finished=True、running=False（前端据此停止轮询）。"""
    _report_mindmap_progress("doc-B", phase="done", total=5, done=5, finished=True)
    try:
        r = mindmap_progress("doc-B")
        assert r["running"] is False and r["finished"] is True
        assert r["pct"] == 100
    finally:
        _cleanup("doc-B")
