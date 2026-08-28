"""
域管理路由 — 从 api_server.py 拆出
职责：自定义知识域的 CRUD、前端配置聚合、API 配置保存
"""
from __future__ import annotations
import json, os
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel
from app.rag_app.config import Config, PROJECT_DIR

router = APIRouter(tags=["DomainManagement"])

DOMAINS_FILE = Path(__file__).resolve().parent / "data" / "custom_domains.json"


def _load_custom_domains() -> dict:
    try:
        DOMAINS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if DOMAINS_FILE.exists():
            return json.loads(DOMAINS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_custom_domains(domains: dict):
    DOMAINS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOMAINS_FILE.write_text(json.dumps(domains, ensure_ascii=False, indent=2), encoding="utf-8")


class DomainRequest(BaseModel):
    key: str
    label: str


class ConfigUpdateRequest(BaseModel):
    api_key: str = ""
    api_base: str = ""
    model: str = ""


@router.get("/api/config")
async def get_config():
    builtin = dict(Config.KNOWLEDGE_DOMAINS)
    custom = _load_custom_domains()
    return {
        "domains": {**builtin, **custom},
        "quick_questions": Config.QUICK_QUESTIONS,
        "api_base": Config.STEP_API_BASE,
        "model": Config.STEP_MODEL,
        "api_key": Config.STEP_API_KEY[:8] + "***" if Config.STEP_API_KEY else "",
    }


@router.get("/api/config/domains")
async def get_domains():
    builtin = dict(Config.KNOWLEDGE_DOMAINS)
    custom = _load_custom_domains()
    return {"domains": {**builtin, **custom}, "builtin": list(builtin.keys()), "custom": list(custom.keys())}


@router.post("/api/config/domains")
async def add_domain(req: DomainRequest):
    custom = _load_custom_domains()
    if req.key in custom:
        return {"status": "error", "detail": f"域 {req.key} 已存在"}
    custom[req.key] = req.label
    _save_custom_domains(custom)
    return {"status": "ok", "key": req.key, "label": req.label}


@router.delete("/api/config/domains/{key}")
async def remove_domain(key: str):
    custom = _load_custom_domains()
    if key not in custom:
        return {"status": "error", "detail": f"域 {key} 不存在或是内置域(不可删除)"}
    del custom[key]
    _save_custom_domains(custom)
    return {"status": "ok"}


@router.delete("/api/domains/{domain_id}")
async def delete_domain_with_files(domain_id: str):
    """删除域及其所有文件（从知识库索引中移除）"""
    from app.rag_app.routes.knowledge import get_kb
    engine = get_kb()
    
    # 找到该域下的所有文件
    files_to_delete = []
    if hasattr(engine, '_embed_metadatas') and engine._embed_metadatas:
        for meta in engine._embed_metadatas:
            if meta.get("domain") == domain_id or meta.get("library_id") == domain_id:
                file_id = meta.get("physical_name") or meta.get("file_name")
                if file_id and file_id not in files_to_delete:
                    files_to_delete.append(file_id)
    
    # 删除所有文件（同步版本，不使用异步线程）
    deleted_count = 0
    for file_id in files_to_delete:
        # 直接操作数据，不触发异步重建
        if not engine._embed_metadatas:
            continue
        
        keep_indices = []
        removed_chunks = 0
        for i, meta in enumerate(engine._embed_metadatas):
            if meta.get("physical_name") == file_id or meta.get("file_name") == file_id:
                removed_chunks += 1
            else:
                keep_indices.append(i)
        
        if removed_chunks == 0:
            continue
        
        if engine._embeddings is not None and keep_indices:
            engine._embeddings = engine._embeddings[keep_indices]
        elif not keep_indices:
            engine._embeddings = None
        
        engine._embed_texts = [engine._embed_texts[i] for i in keep_indices] if keep_indices else []
        engine._embed_ids = [engine._embed_ids[i] for i in keep_indices] if keep_indices else []
        engine._embed_metadatas = [engine._embed_metadatas[i] for i in keep_indices] if keep_indices else []
        
        bm25_keep = []
        for i, meta in enumerate(engine.bm25_metadatas):
            if meta.get("physical_name") != file_id and meta.get("file_name") != file_id:
                bm25_keep.append(i)
        
        engine.bm25_docs = [engine.bm25_docs[i] for i in bm25_keep]
        engine.bm25_ids = [engine.bm25_ids[i] for i in bm25_keep]
        engine.bm25_metadatas = [engine.bm25_metadatas[i] for i in bm25_keep]
        
        deleted_count += 1
    
    # 所有文件删除完成后，一次性重建 BM25 并保存
    if engine.bm25_docs:
        import jieba
        from rank_bm25 import BM25Okapi
        tokenized = [list(jieba.cut(doc)) for doc in engine.bm25_docs]
        engine.bm25 = BM25Okapi(tokenized)
    else:
        engine.bm25 = None
    
    engine._save_embeddings()
    engine._save_bm25()
    
    # 如果是自定义域，也从配置中删除
    custom = _load_custom_domains()
    if domain_id in custom:
        del custom[domain_id]
        _save_custom_domains(custom)
    
    return {"status": "ok", "domain_id": domain_id, "files_deleted": deleted_count}


@router.post("/api/config/save")
async def save_config(req: ConfigUpdateRequest):
    env_path = PROJECT_DIR / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = {"STEP_API_KEY": False, "STEP_API_BASE": False, "STEP_MODEL": False}
    for i, line in enumerate(lines):
        for key in updated:
            if line.strip().startswith(key + "="):
                new_val = {"STEP_API_KEY": req.api_key, "STEP_API_BASE": req.api_base, "STEP_MODEL": req.model}[key]
                if new_val:
                    lines[i] = f"{key}={new_val}"
                    updated[key] = True
                break
    for key in updated:
        if not updated[key]:
            new_val = {"STEP_API_KEY": req.api_key, "STEP_API_BASE": req.api_base, "STEP_MODEL": req.model}[key]
            if new_val:
                lines.append(f"{key}={new_val}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "ok", "note": "配置已保存，需重启服务生效"}
