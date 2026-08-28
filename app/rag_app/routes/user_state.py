"""用户状态同步（2026-08-14，任务二十八）

浏览器侧用户数据（标注/图谱/卡片/词条/文件夹等）落盘到服务端数据目录
user_state.json，使"复制数据文件夹"即可完成整机迁移（换电脑带数据）。

契约：
  GET  /api/user-state  -> {exists, version, updatedAt, data: {key: value}}
  POST /api/user-state  body {version, updatedAt, data} -> {success, updatedAt}
  data 为 localStorage 各键解析后的值；单文件上限 8MB。
"""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["UserState"])

from app.rag_app.config import Config, PROJECT_DIR

USER_STATE_FILE = Path(Config.ROUTES_DATA_DIR) / "user_state.json"
_MAX_BYTES = 8 * 1024 * 1024
MIGRATION_GUIDE_FILE = PROJECT_DIR / "docs" / "数据迁移指南.md"


class UserStateReq(BaseModel):
    version: int = 1
    updatedAt: str = ""
    data: dict = Field(default_factory=dict)


@router.get("/api/migration-guide")
def get_migration_guide():
    """返回迁移指南文档（任务三十）：设置→数据迁移面板"下载迁移指南"按钮的数据源。
    契约：{filename, content}；文件不存在 404。"""
    if not MIGRATION_GUIDE_FILE.exists():
        raise HTTPException(status_code=404, detail="迁移指南文件不存在")
    try:
        content = MIGRATION_GUIDE_FILE.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}")
    return {"filename": MIGRATION_GUIDE_FILE.name, "content": content}


@router.get("/api/user-state")
def get_user_state():
    """读取落盘的用户状态；不存在或损坏时返回 exists=False，前端走本地推送。"""
    if not USER_STATE_FILE.exists():
        return {"exists": False}
    try:
        payload = json.loads(USER_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return {"exists": False}
        return {"exists": True, **payload}
    except Exception:
        return {"exists": False}


@router.post("/api/user-state")
def save_user_state(r: UserStateReq):
    """整体覆盖写入用户状态（前端按"最新 updatedAt 胜出"策略同步）。"""
    raw = json.dumps(r.model_dump(), ensure_ascii=False)
    if len(raw.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="用户状态数据过大（>8MB）")
    try:
        USER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USER_STATE_FILE.write_text(raw, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写入失败: {e}")
    return {"success": True, "updatedAt": r.updatedAt}
