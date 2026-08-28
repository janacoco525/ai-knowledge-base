"""
AI知识库 - 知识库管理路由
文件列表、统计、知识域等管理接口
⚠️ 上传功能已废弃(2026-05-06)，改为文件夹扫描入库(见routes/scan.py)
"""
import os
import re
import shutil
import urllib.parse
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Query, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.rag_app.config import Config
from app.rag_app.toc_extractor import extract_toc, extract_toc_from_preprocessed
from app.rag_app.text_preprocessor import preprocess_text, render_to_html

router = APIRouter(prefix="/api/kb", tags=["知识库管理"])


def get_kb():
    from app.rag_app.shared_engine import get_kb as _get_kb
    return _get_kb()


# 统一使用 Config 的扩展名集
ALLOWED_EXTENSIONS = Config.SUPPORTED_EXTENSIONS
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def is_allowed(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    domain: str = Query("default", description="知识域标签"),
):
    """上传文件到知识库——浏览器选文件夹后调用此接口"""
    engine = get_kb()
    uploaded = 0
    skipped = 0
    failed = 0
    errors = []

    # 使用持久化目录存放上传文件
    upload_dir = Path(Config.ROUTES_DATA_DIR) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    for upload_file in files:
        filename = upload_file.filename or "unknown"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped += 1
            continue

        try:
            # 保存到持久化目录
            safe_name = filename.replace("/", "_").replace("\\", "_")
            # 避免文件名冲突：添加时间戳前缀
            unique_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
            file_path = str(upload_dir / unique_name)
            content = bytearray()
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise ValueError("单个文件不能超过 25MB")
            with open(file_path, "wb") as f:
                f.write(content)

            # 索引到知识库
            fsize = len(content)
            count = engine.index_file_with_metadata(
                file_path=file_path,
                file_name=filename,
                uploaded_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                file_size=fsize,
                domain=domain,
                physical_name=filename,
                file_mtime=os.path.getmtime(file_path),
            )
            if count > 0:
                uploaded += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            errors.append(f"{filename}: {str(e)[:100]}")

    return {"uploaded": uploaded, "skipped": skipped, "failed": failed, "errors": errors}


@router.get("/files")
def list_files(domain: str = Query(None)):
    """获取已索引文件列表"""
    engine = get_kb()
    files = engine.list_files()
    if domain:
        files = [f for f in files if f.get("domain") == domain]
    return {"files": files}


# ⚠️ 上传接口已废弃 — 请使用 POST /api/scan/start 替代
# ✅ 2026-05-14: 新增 POST /api/kb/add-file 单文件添加入口


class AddFileRequest(BaseModel):
    path: str = Field(..., description="文件完整路径")
    domain: str = Field(default="default", description="所属知识域")


@router.post("/add-file")
def add_single_file(request: AddFileRequest):
    """添加单个文件到知识库——面向普通用户的最常用入库方式"""
    import os as _os
    engine = get_kb()
    path = request.path.strip()
    if not _os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"文件不存在: {path}")
    ext = _os.path.splitext(path)[1].lower()
    if ext not in Config.SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持 {ext} 格式。支持: {', '.join(sorted(Config.SUPPORTED_EXTENSIONS))}")
    fname = _os.path.basename(path)
    fsize = _os.path.getsize(path)
    count = engine.index_file_with_metadata(path, fname, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fsize, request.domain, fname)
    return {"status": "ok", "file": fname, "chunks": count, "domain": request.domain}


@router.get("/chunks")
def get_chunks(file_id: str = Query(..., description="文件ID"), max: int = Query(5, description="最多返回段落数")):
    """获取文件的内容段落预览"""
    engine = get_kb()
    chunks = engine.get_chunks_by_file(file_id, max_chunks=max)
    return {"chunks": chunks, "file_id": file_id}


@router.get("/files/{file_id:path}/text")
def get_file_text(file_id: str):
    """获取文件的完整正文和标题结构（缓存加速）—— 含服务端 HTML 渲染"""
    engine = get_kb()
    
    # 命中预处理器缓存 → 直接返回（含 HTML；超长文档 html 为空）
    preprocessed = engine._file_preprocessed.get(file_id)
    raw = engine._file_text_cache.get(file_id, "")
    if preprocessed and raw:
        headings = engine._file_headings.get(file_id, [])
        html = engine._file_html.get(file_id)
        if html is None and len(preprocessed) < 200000:
            html = render_to_html(preprocessed)
            engine._file_html[file_id] = html
        elif html is None:
            html = ""
            engine._file_html[file_id] = ""
        return {"file_id": file_id, "text": raw, "preprocessed": preprocessed,
                "headings": headings, "html": html, "chunk_count": -1, "cached": True}
    
    # 首次请求：构建全文 → 预处理 → 缓存
    raw = engine._file_text_cache.get(file_id)
    if raw is None:
        # overlap 去重重建（2026-08-07）：旧 "\n\n".join 会把分块重叠段重复计入并截断段落
        raw = engine.rebuild_full_text(file_id)
        if not raw:
            raise HTTPException(status_code=404, detail="文件无内容或不存在")
        engine._file_text_cache[file_id] = raw
    
    # 预处理（分段/脚注清理/标题标记）
    preprocessed = preprocess_text(raw)
    engine._file_preprocessed[file_id] = preprocessed
    
    # 服务端渲染 HTML —— ⚠️ 超长文档跳过（前端不用 html 字段，白耗 3s+；<20万字保留）
    # 前端正文用 preprocessed + ReactMarkdown 渲染，html 字段当前无消费者
    if len(preprocessed) < 200000:
        html = render_to_html(preprocessed)
    else:
        html = ""
    engine._file_html[file_id] = html

    # 持久化正文缓存（避免服务重启后重新解析）
    engine._save_content_cache()
    # 标题提取（2026-08-07 调整优先级）：preprocessed 的 # / ## 标记优先——
    # 与 ReactMarkdown 渲染 DOM 100% 对齐（点击必能跳转）且顺序与正文一致；
    # EPUB 原生 TOC/旧提取结果作兜底（preprocessed 无标题时才用）——注意与渲染锚点对齐
    pp_headings = extract_toc_from_preprocessed(preprocessed)
    if pp_headings:
        engine._file_headings[file_id] = pp_headings
        engine._save_content_cache()
    elif file_id not in engine._file_headings:
        engine._file_headings[file_id] = extract_toc(raw)
        engine._save_content_cache()  # 也会缓存新提取的标题
    
    return {"file_id": file_id, "text": raw, "preprocessed": preprocessed,
            "html": html, "chunk_count": -1, "headings": engine._file_headings[file_id],
            "cached": False}


@router.get("/files/{file_id:path}/raw")
def get_raw_file(request: Request, file_id: str):
    """获取原始文件（PDF/EPUB等二进制格式），用于浏览器内联预览"""
    import mimetypes
    from pathlib import Path as P
    import os as _os

    # ⛔ Windows 平台 encoding 问题：FastAPI 把 URL 字节按 GBK 解码，
    # 但浏览器发的是 UTF-8 percent-encoded → 必须从 request.url.path 拿原始路径再 UTF-8 解码
    raw_path = request.url.path
    parts = raw_path.split('/')
    encoded_id = parts[-2] if len(parts) >= 3 else file_id
    try:
        original_id = urllib.parse.unquote(encoded_id, encoding='utf-8')
    except (UnicodeDecodeError, ValueError):
        original_id = file_id

    engine = get_kb()
    meta = engine.get_file_metadata(original_id)
    file_path = meta.get("file_path", "")

    # ⛔ 兜底1：老路径 → 新路径
    if not file_path or not _os.path.isfile(file_path):
        if file_path and ("rag_app/data/uploads/" in file_path or "rag_app\\data\\uploads" in file_path):
            basename = _os.path.basename(file_path)
            alt = P(__file__).resolve().parent.parent / "data" / "uploads" / basename
            if alt.is_file():
                file_path = str(alt)

    # ⛔ 兜底2：按搜索键在 uploads 目录里找
    if not file_path or not _os.path.isfile(file_path):
        uploads_dir = P(__file__).resolve().parent.parent / "data" / "uploads"
        stem = _os.path.basename(original_id)
        if '_' in stem[:15]:
            stem = '_'.join(stem.split('_')[2:])
        for f in uploads_dir.iterdir():
            if stem in f.name or original_id in f.name:
                file_path = str(f)
                break

    if not file_path or not _os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="原始文件不存在")

    mime, _ = mimetypes.guess_type(file_path)
    return FileResponse(
        file_path,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": "inline"}
    )

    mime, _ = mimetypes.guess_type(file_path)
    return FileResponse(
        file_path,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": "inline"}
    )

    mime, _ = mimetypes.guess_type(file_path)
    return FileResponse(
        file_path,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": "inline"}
    )

    # ⛔ 兜底1：老索引可能存了 rag_app/data/uploads/ (Qoder重构前路径)
    if not file_path or not os.path.isfile(file_path):
        if file_path and ("rag_app/data/uploads/" in file_path or "rag_app\\data\\uploads" in file_path):
            basename = os.path.basename(file_path)
            alt = P(__file__).resolve().parent.parent / "data" / "uploads" / basename
            if alt.is_file():
                file_path = str(alt)

    # ⛔ 兜底2：file_id 编码不匹配导致 metadata 找不到（Windows GBK vs UTF-8）
    # 退化到按文件名前缀在 new uploads 目录里找
    if not file_path or not os.path.isfile(file_path):
        uploads_dir = P(__file__).resolve().parent.parent / "data" / "uploads"
        # 去掉扩展名后，用前 8 个 unicode 字符做模糊匹配
        file_id_no_ext = file_id.rsplit('.', 1)[0]
        prefix = file_id_no_ext[:8] if len(file_id_no_ext) >= 8 else file_id_no_ext
        try:
            # Recover UTF-8 from Windows-mangled file_id
            utf8_prefix = file_id.encode("latin-1").decode("utf-8").rsplit('.', 1)[0][:8]
        except (UnicodeDecodeError, UnicodeEncodeError, AttributeError):
            utf8_prefix = prefix

        for f in uploads_dir.iterdir():
            # 匹配中文PDF文件
            if f.suffix in ('.pdf', '.epub') and (
                prefix in f.name or utf8_prefix in f.name or file_id in f.name
            ):
                file_path = str(f)
                break

    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="原始文件不存在")

    mime, _ = mimetypes.guess_type(file_path)
    return FileResponse(
        file_path,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": "inline"}
    )


@router.delete("/files/{file_id:path}")
def delete_file(file_id: str):
    """删除已索引文件：从向量索引和BM25中移除该文件的所有chunks"""
    engine = get_kb()
    result = engine.remove_file(file_id)
    if not result.get("removed"):
        raise HTTPException(status_code=404, detail=result.get("reason", "文件未找到"))
    return result


@router.get("/stats")
def get_stats():
    """获取知识库统计"""
    engine = get_kb()
    return engine.get_stats()


@router.get("/domains")
async def list_domains():
    """获取可用知识域"""
    return {"domains": Config.KNOWLEDGE_DOMAINS}


@router.get("/quick-questions")
async def get_quick_questions():
    """获取快捷提问列表"""
    return {"questions": Config.QUICK_QUESTIONS}
