"""
AI知识库 - 文件夹扫描路由
支持启动扫描、查询进度、停止扫描、路径管理、变更检测、差异刷新
"""
import threading
import time
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field
from app.rag_app.config import Config
from app.rag_app.scanner import Scanner

import logging
logger = logging.getLogger("ai_kb.scan")

router = APIRouter(prefix="/api/scan", tags=["文件夹扫描"])

# 全局扫描器实例（单例）
_scanner: Scanner = None
_scan_thread: threading.Thread = None
_last_result: dict = {}


def get_scanner() -> Scanner:
    global _scanner
    if _scanner is None:
        from app.rag_app.shared_engine import get_kb
        _scanner = Scanner(Config(), kb=get_kb())
    return _scanner


# ==================== 扫描操作 ====================

@router.post("/start")
async def start_scan(
    directory: str = Query(..., description="要扫描的目录路径"),
    domain: str = Query("default", description="知识域标签"),
    force: bool = Query(False, description="是否强制全量重扫"),
):
    """启动文件夹扫描（异步执行）"""
    scanner = get_scanner()
    if scanner.is_scanning:
        return {"status": "already_scanning", "progress": scanner.progress}

    import os
    if not os.path.isdir(directory):
        raise HTTPException(400, f"目录不存在: {directory}")

    global _scan_thread, _last_result

    def _do_scan():
        global _last_result
        _last_result = scanner.scan_directory(directory, domain=domain, force=force)
        _last_result["status"] = "completed"

    _scan_thread = threading.Thread(target=_do_scan, daemon=True)
    _scan_thread.start()

    return {
        "status": "started",
        "directory": directory,
        "domain": domain,
        "force": force,
        "message": f"正在扫描 {directory}...",
    }


@router.get("/status")
async def scan_status():
    """获取扫描进度"""
    scanner = get_scanner()
    progress = scanner.progress
    progress["is_scanning"] = scanner.is_scanning
    if not scanner.is_scanning and _last_result:
        progress["last_result"] = _last_result
    return progress


@router.post("/stop")
async def stop_scan():
    """停止扫描"""
    scanner = get_scanner()
    if not scanner.is_scanning:
        return {"status": "not_scanning", "message": "当前没有正在进行的扫描"}
    scanner.stop()
    return {"status": "stopping", "message": "扫描已请求停止"}


# ==================== 扫描路径管理 ====================

@router.get("/paths")
async def get_scan_paths():
    """获取当前配置的所有扫描路径"""
    paths = Config.get_scan_paths()
    return {
        "paths": paths,
        "count": len(paths),
        "data_dir": Config.DATA_DIR,
    }


class AddPathRequest(BaseModel):
    path: str = Field(..., description="要添加的扫描目录路径")


@router.post("/paths")
async def add_scan_path(request: AddPathRequest):
    """添加扫描路径（更新 .env SCAN_PATHS）
    （2026-08-20 结构化：路径 resolve 规范化后去重，杜绝同目录多写法并存）"""
    import os as _os
    target = request.path.strip()
    if not target:
        raise HTTPException(400, "路径不能为空")
    norm = str(Path(target).resolve())
    if not _os.path.isdir(target):
        current = Config.get_scan_paths()
        if norm in current:
            return {"status": "already_exists", "paths": current, "message": "路径已存在"}
        current.append(norm)
        _update_scan_paths_env(current)
        return {"status": "added", "paths": current, "message": f"已添加: {norm}（⚠️ 该路径目前不存在，扫描时将跳过）"}

    current = Config.get_scan_paths()
    if norm in current:
        return {"status": "already_exists", "paths": current, "message": "路径已存在"}

    current.append(norm)
    _update_scan_paths_env(current)
    return {"status": "added", "paths": current, "message": f"已添加: {norm}"}


@router.delete("/paths/{index}")
async def delete_scan_path(index: int):
    """删除指定索引的扫描路径"""
    current = Config.get_scan_paths()
    if index < 0 or index >= len(current):
        raise HTTPException(400, f"索引超出范围: {index} (共{len(current)}条)")
    removed = current.pop(index)
    _update_scan_paths_env(current)
    return {"status": "deleted", "removed": removed, "paths": current, "message": f"已移除: {removed}"}


# ==================== 单文件导入 ====================

class SingleFileRequest(BaseModel):
    path: str = Field(..., description="要导入的单个文件路径")


@router.post("/single")
async def scan_single_file(request: SingleFileRequest):
    """导入单个文件，自动识别父文件夹作为分类"""
    import os as _os
    file_path = request.path.strip()
    if not file_path:
        raise HTTPException(400, "路径不能为空")
    if not _os.path.isfile(file_path):
        raise HTTPException(400, f"文件不存在: {file_path}")

    path = Path(file_path)
    ext = path.suffix.lower()
    if ext not in Config.SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式: {ext}，支持: {', '.join(sorted(Config.SUPPORTED_EXTENSIONS))}")

    # 父文件夹名作为建议分类
    parent_folder = path.parent.name
    if parent_folder in (".", "..", ""):
        parent_folder = "默认分类"

    try:
        stat = path.stat()
        scanner = get_scanner()
        chunk_count = scanner.kb.index_file_with_metadata(
            file_path=str(path),
            file_name=path.name,
            uploaded_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            file_size=stat.st_size,
            domain=parent_folder,  # 域名用文件夹名
            physical_name=path.name,
            file_mtime=stat.st_mtime,
            # 结构化 v2：单文件无扫描根，以父目录为根，身份自洽
            source_root=str(path.parent.resolve()),
            rel_path=path.name,
            rel_dir=".",
        )

        return {
            "status": "success",
            "file_name": path.name,
            "category": parent_folder,
            "chunks": chunk_count,
            "file_size": stat.st_size,
            "message": f"已导入 {path.name} → 分类「{parent_folder}」({chunk_count} 个文本块)",
        }
    except Exception as e:
        logger.error("Failed to import %s: %s", file_path, e)
        raise HTTPException(500, f"导入失败: {str(e)}")


# ==================== 变更检测 ====================

@router.get("/diff")
async def scan_diff():
    """对比扫描路径下的文件与已索引文件，返回新增/变更/删除统计"""
    scanner = get_scanner()
    return scanner.diff()


@router.post("/refresh")
async def scan_refresh():
    """仅重索引变更的文件（new + modified）"""
    scanner = get_scanner()
    if scanner.is_scanning:
        return {"status": "already_scanning", "progress": scanner.progress}

    global _scan_thread, _last_result

    def _do_refresh():
        global _last_result
        _last_result = scanner.scan_refresh()
        _last_result["status"] = "completed"

    _scan_thread = threading.Thread(target=_do_refresh, daemon=True)
    _scan_thread.start()

    return {"status": "started", "message": "正在刷新变更文件..."}


# ==================== 辅助函数 ====================

def _update_scan_paths_env(paths: list):
    """更新 .env 文件中的 SCAN_PATHS 行"""
    import os as _os
    env_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env")
    new_value = ",".join(paths)
    # 同步当前进程内的配置，下一次请求无需重启服务即可看到新路径。
    Config._scan_paths_raw = new_value

    if _os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        with open(env_path, "w", encoding="utf-8") as f:
            for line in lines:
                if line.startswith("SCAN_PATHS="):
                    f.write(f"SCAN_PATHS={new_value}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"\nSCAN_PATHS={new_value}\n")
    else:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"SCAN_PATHS={new_value}\n")
