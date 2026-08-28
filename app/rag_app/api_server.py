"""
AI知识库 - FastAPI主服务
统一入口，注册所有路由
"""
import sys
import os
from contextlib import asynccontextmanager
from pathlib import Path

# 确保项目根目录在 sys.path 中（core.* / app.rag_app.* 包导入的前提）
project_root = Path(__file__).parent.parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.logger import get_logger
_logger = get_logger("ai_kb")

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from app.rag_app.config import Config



@asynccontextmanager
async def lifespan(_app: FastAPI):
    """在服务宣告健康前加载索引，避免首个图谱请求承担冷启动成本。"""
    from app.rag_app.shared_engine import get_kb
    get_kb()
    yield


app = FastAPI(title="AI知识库", version=Config.PRODUCT_VERSION, docs_url="/docs", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # 默认用 127.0.0.1；localhost 双源为兼容旧书签/直输地址（localStorage 按源隔离，勿删）
        "http://127.0.0.1:8501",
        "http://localhost:8501",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ----- 全局异常处理 + 访问日志（2026-08-19 降噪：控制台不再被 GET 刷屏） -----
_access_logger = get_logger("ai_kb.access", file_only=True)

@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    """防止未捕获异常导致服务崩溃；同时记录访问日志：2xx 只写文件，非 2xx 才上控制台"""
    try:
        response = await call_next(request)
        status = response.status_code
        if status >= 400:
            _logger.warning("请求异常 %s %s -> %s", request.method, request.url.path, status)
        _access_logger.info("%s %s -> %s", request.method, request.url.path, status)
        return response
    except Exception as exc:
        _logger.error("未捕获异常 @ %s %s: %s", request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "服务内部错误，请查看服务日志"},
        )

# ⛔ v2 原型已停用：rag_app/web/ 不再挂载为产品入口
# 旧 /web/* URL 会收到 301 重定向到 React SPA 根
# 原型文件本身保留在磁盘供历史参考，但不再通过 HTTP 对外暴露



# ----- 路由注册 -----
from app.rag_app.routes.query import router as query_router
from app.rag_app.routes.knowledge import router as knowledge_router
from app.rag_app.routes.scan import router as scan_router
from app.rag_app.routes.graph import router as graph_router
from app.rag_app.routes.analysis import router as analysis_router
from app.rag_app.routes.analysis import interpret_router
from app.rag_app.routes.analysis import translate_router
from app.rag_app.routes.diff import router as diff_router
from app.rag_app.routes.knowledge_tree import router as knowledge_tree_router
from app.rag_app.routes.reading import router as reading_router
from app.rag_app.routes.cards import router as cards_router
from app.rag_app.routes.recommend import router as recommend_router
from app.rag_app.routes.gemini_adapter import router as gemini_adapter_router
from app.rag_app.routes.providers import router as providers_router
from app.rag_app.routes.llm_ops import router as llm_ops_router
from app.rag_app.routes.chat_ops import router as chat_ops_router
from app.rag_app.routes.domains import router as domains_router
from app.rag_app.routes.chat_history import router as chat_history_router
from app.rag_app.routes.user_state import router as user_state_router

app.include_router(query_router)
app.include_router(knowledge_router)
app.include_router(scan_router)
app.include_router(graph_router)
app.include_router(analysis_router)
app.include_router(interpret_router)
app.include_router(translate_router)
app.include_router(diff_router)
app.include_router(knowledge_tree_router)
app.include_router(reading_router)
app.include_router(cards_router)
app.include_router(recommend_router)
app.include_router(gemini_adapter_router)
app.include_router(providers_router)
app.include_router(llm_ops_router)
app.include_router(chat_ops_router)
app.include_router(domains_router)
app.include_router(chat_history_router)
app.include_router(user_state_router)


# ----- 首页重定向 -----


# Old root redirect removed - now served by SPA fallback


# ----- v2 原型弃用重定向 -----
# 旧 /web/* URL 301 重定向到 SPA 首页，避免后续模型和用户误用旧入口
@app.get("/web/{path:path}")
async def redirect_old_web(path: str):
    return RedirectResponse(url="/", status_code=301)


@app.get("/web")
async def redirect_old_web_root():
    return RedirectResponse(url="/", status_code=301)


@app.get("/health")
def health_check():
    from app.rag_app.providers import list_providers as _list, match_provider_by_base_url
    api_key = os.environ.get("STEP_API_KEY", Config.STEP_API_KEY)
    api_key_configured = bool(api_key and len(api_key) > 5)
    # 读取 provider_keys.json → 所有已保存 Key 的提供商
    try:
        from app.rag_app.routes.providers import _load_provider_keys, _mask_key
        provider_keys = _load_provider_keys()
    except Exception:
        provider_keys = {}
        _mask_key = lambda k: k[:8] + "***" if k and len(k) > 10 else ""

    # 根据 os.environ 当前值匹配当前生效的提供商（运行时最新值，不依赖 Config 类变量缓存）
    active_provider_id = ""
    providers = _list()
    env_base = os.environ.get("STEP_API_BASE", Config.STEP_API_BASE)
    env_model = os.environ.get("STEP_MODEL", Config.STEP_MODEL)
    active_provider_id = match_provider_by_base_url(env_base) or ""
    if not active_provider_id and env_base:
        normalized_env_base = env_base.rstrip("/")
        for pid, info in provider_keys.items():
            if info.get("base_url", "").rstrip("/") == normalized_env_base and info.get("model", "") == env_model:
                active_provider_id = pid
                break
        if not active_provider_id:
            for pid, info in provider_keys.items():
                if info.get("base_url", "").rstrip("/") == normalized_env_base:
                    active_provider_id = pid
                    break
    if not active_provider_id and api_key_configured:
        active_provider_id = "custom"

    # 构建 provider status 列表
    provider_status = []
    known_ids = {p["id"] for p in providers}
    for p in providers:
        pid = p["id"]
        saved = provider_keys.get(pid, {})
        provider_status.append({
            "id": pid,
            "name": p["name"],
            "base_url": p["base_url"],
            "models": p["models"],
            "docs": p.get("docs", ""),
            "keyConfigured": pid in provider_keys,
            "keyPreview": _mask_key(saved.get("apiKey", "")) if saved else "",
            "configuredModel": saved.get("model", ""),
        })
    # 追加 provider_keys.json 中的自定义提供商
    for pid, info in provider_keys.items():
        if pid not in known_ids:
            provider_status.append({
                "id": pid,
                "name": pid,
                "base_url": info.get("base_url", ""),
                "models": [info.get("model", "")],
                "docs": "",
                "keyConfigured": True,
                "keyPreview": _mask_key(info.get("apiKey", "")),
                "configuredModel": info.get("model", ""),
            })

    return {
        "status": "healthy",
        "version": Config.PRODUCT_VERSION,
        "apiKeyPresent": api_key_configured,
        "apiKeyPreview": (os.environ.get("STEP_API_KEY", Config.STEP_API_KEY)[:8] + "***") if api_key_configured else "",
        "apiEndpoint": env_base if api_key_configured else "",
        "modelName": env_model if api_key_configured else "",
        "activeProviderId": active_provider_id if api_key_configured else "",
        "providers": provider_status,
    }


@app.get("/api/config/test-llm")
def test_llm():
    """测试LLM API连通性——返回延迟和状态"""
    import time
    from app.rag_app.llm_client_factory import create_llm_client
    try:
        cfg = Config()
        client = create_llm_client()
        start = time.time()
        response = client.chat.completions.create(
            model=cfg.STEP_MODEL,
            messages=[{"role": "user", "content": "回复一个字：通"}],
            max_tokens=5,
            timeout=10,
        )
        latency = int((time.time() - start) * 1000)
        return {"status": "ok", "latency_ms": latency, "model": cfg.STEP_MODEL}
    except Exception as e:
        return {"status": "error", "detail": str(e)[:200]}


# 域管理、前端配置、API配置保存 → routes/domains.py


# ----- 启动入口 -----
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8501"))
    host = os.getenv("HOST", "127.0.0.1")
    _logger.info("启动AI知识库服务: http://127.0.0.1:%s", port)
    _logger.info("API文档: http://127.0.0.1:%s/docs", port)
    _logger.info("完整日志落盘: logs/ai_kb.log（控制台只显示警告/错误，访问日志进文件）")
    # access_log=False：访问日志由自定义中间件接管（2xx 只写文件），避免控制台刷屏
    uvicorn.run("api_server:app", host=host, port=port, reload=False, access_log=False)

# ----- SPA fallback（必须放在所有路由之后） -----
import mimetypes

# 显式注册 MIME 类型（Windows 下 mimetypes 可能不完整，导致 ESM 模块被拒绝）
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")


def _serve_static_with_mime(path: Path):
    """返回带正确 Content-Type 的 FileResponse"""
    return FileResponse(str(path), headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }, media_type=mimetypes.guess_type(str(path))[0])

# 支持 PyInstaller 打包模式
if getattr(sys, "frozen", False):
    _BASE = sys._MEIPASS
    FRONTEND_DIST = Path(_BASE) / "frontend" / "dist"
else:
    FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # API routes: let FastAPI handle
        if any(full_path.startswith(p) for p in ("api/", "docs", "openapi", "health")):
            raise HTTPException(status_code=404)
        # Static assets: serve directly with correct MIME
        if full_path.startswith("assets/") or full_path.startswith("lib/"):
            frontend_root = FRONTEND_DIST.resolve()
            asset_path = (FRONTEND_DIST / full_path).resolve()
            if asset_path.is_relative_to(frontend_root) and asset_path.is_file():
                return _serve_static_with_mime(asset_path)
            raise HTTPException(status_code=404)
        # SPA fallback: return index.html for all other paths
        index_path = FRONTEND_DIST / "index.html"
        if index_path.exists():
            return _serve_static_with_mime(index_path)
        raise HTTPException(status_code=404)

    @app.get("/")
    async def serve_root():
        return _serve_static_with_mime(FRONTEND_DIST / "index.html")
