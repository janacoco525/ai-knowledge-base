"""用户状态同步契约测试（任务二十八）：/api/user-state GET/POST/超限。

覆盖：空状态、保存→读取往返、超大 body 413；不污染真实数据目录（tmp_path 注入）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from app.rag_app.api_server import app

client = TestClient(app)


def test_user_state_get_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rag_app.routes.user_state.USER_STATE_FILE", tmp_path / "user_state.json")
    r = client.get("/api/user-state")
    assert r.status_code == 200
    assert r.json() == {"exists": False}


def test_user_state_save_and_get_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rag_app.routes.user_state.USER_STATE_FILE", tmp_path / "user_state.json")
    payload = {
        "version": 1,
        "updatedAt": "2026-08-14T00:00:00Z",
        "data": {"kb_knowledge_cards": [{"front": "Q", "back": "A"}], "kb_nodes": []},
    }
    r = client.post("/api/user-state", json=payload)
    assert r.status_code == 200
    assert r.json()["success"] is True

    r2 = client.get("/api/user-state")
    assert r2.status_code == 200
    body = r2.json()
    assert body["exists"] is True
    assert body["updatedAt"] == "2026-08-14T00:00:00Z"
    assert body["data"]["kb_knowledge_cards"][0]["front"] == "Q"


def test_user_state_oversize_rejected_413(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rag_app.routes.user_state.USER_STATE_FILE", tmp_path / "user_state.json")
    big = {"version": 1, "updatedAt": "x", "data": {"pad": "a" * (9 * 1024 * 1024)}}
    r = client.post("/api/user-state", json=big)
    assert r.status_code == 413


def test_migration_guide_endpoint():
    """任务三十：设置页"下载迁移指南"按钮的数据源——返回真实文档内容。"""
    r = client.get("/api/migration-guide")
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "数据迁移指南.md"
    assert "数据迁移" in body["content"]
