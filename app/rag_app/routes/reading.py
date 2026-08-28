"""
AI知识库 - 阅读记录路由
追踪文件阅读历史，支持回溯和继续阅读建议。
诚实边界：只记录已索引文件的阅读行为，不冒充完整用户行为分析系统。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.rag_app.config import Config
from app.rag_app.shared_engine import get_engine

router = APIRouter(prefix="/api/reading-history", tags=["阅读记录"])

# 数据文件路径 — 统一走 Config
DATA_DIR = Config.ROUTES_DATA_DIR
READING_RECORDS_FILE = os.path.join(DATA_DIR, "reading_records.json")


class ReadingRecordRequest(BaseModel):
    file_id: str


def _ensure_data_dir():
    """确保数据目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_records() -> dict:
    """读取阅读记录，自动过滤测试数据"""
    records = {}
    if os.path.exists(READING_RECORDS_FILE):
        try:
            with open(READING_RECORDS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 过滤冒烟测试产生的假数据
            records = {k: v for k, v in raw.items() if not str(k).startswith("smoke_test")}
        except (json.JSONDecodeError, OSError):
            records = {}
    return records


def _save_records(records: dict):
    """保存阅读记录"""
    _ensure_data_dir()
    with open(READING_RECORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _get_file_name(file_id: str) -> str:
    """从RAGEngine获取文件显示名"""
    try:
        eng = get_engine()
        stats = eng.kb.get_stats()
        files = stats.get("files", [])
        for f in files:
            if f.get("file_id") == file_id:
                return f.get("file_name", file_id)
    except Exception:
        pass
    return file_id


@router.post("")
async def record_reading(request: ReadingRecordRequest):
    """记录阅读行为，自动拒绝测试数据"""
    file_id = request.file_id
    if not file_id or not file_id.strip():
        raise HTTPException(status_code=400, detail="缺少 file_id")
    if file_id.startswith("smoke_test"):
        return {"status": "skipped", "reason": "测试数据不记录"}

    file_name = _get_file_name(file_id)

    records = _load_records()
    now = datetime.now().isoformat()

    if file_id not in records:
        records[file_id] = {
            "file_id": file_id,
            "file_name": file_name,
            "first_read_at": now,
            "last_read_at": now,
            "read_count": 1,
            "total_read_seconds": 0,
        }
    else:
        rec = records[file_id]
        rec["last_read_at"] = now
        rec["read_count"] = rec.get("read_count", 0) + 1
        if not rec.get("file_name"):
            rec["file_name"] = file_name

    _save_records(records)

    return {
        "status": "ok",
        "file_id": file_id,
        "read_count": records[file_id]["read_count"],
    }


class RenameRecordRequest(BaseModel):
    file_id: str
    new_name: str


@router.put("")
async def rename_record(request: RenameRecordRequest):
    """重命名阅读记录中的文件别名"""
    records = _load_records()
    if request.file_id not in records:
        raise HTTPException(status_code=404, detail="记录不存在")
    records[request.file_id]["file_name"] = request.new_name.strip()
    _save_records(records)
    return {"status": "ok", "file_id": request.file_id, "new_name": request.new_name}


@router.get("")
async def get_reading_history(limit: int = Query(default=20, ge=1, le=100), sort_by: str = Query(default="last_read_at")):
    """获取阅读历史"""
    records = _load_records()
    records_list = list(records.values())

    # 排序
    if sort_by == "read_count":
        records_list.sort(key=lambda x: x.get("read_count", 0), reverse=True)
    elif sort_by == "first_read_at":
        records_list.sort(key=lambda x: x.get("first_read_at", ""), reverse=False)
    else:  # last_read_at
        records_list.sort(key=lambda x: x.get("last_read_at", ""), reverse=True)

    records_list = records_list[:limit]

    return {
        "records": records_list,
        "total": len(records),
    }


@router.get("/continue")
async def get_continue_reading_suggestions():
    """获取继续阅读建议"""
    records = _load_records()

    if not records:
        return {"suggestions": []}

    records_list = list(records.values())
    records_list.sort(key=lambda x: x.get("last_read_at", ""), reverse=True)

    suggestions = []
    for rec in records_list[:5]:
        suggestions.append({
            "file_id": rec["file_id"],
            "file_name": rec.get("file_name", rec["file_id"]),
            "last_read_at": rec.get("last_read_at"),
            "reason": "最近阅读过，建议继续",
        })

    return {"suggestions": suggestions}
