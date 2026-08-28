"""
模型提供商管理路由 — 从 gemini_adapter.py 拆出
职责：API Key 存储/验证/切换/列表、健康检查
"""
from __future__ import annotations
import json, os, re as _re
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.rag_app.config import PROJECT_DIR

router = APIRouter(tags=["ProviderManagement"])


# ============ Key Storage ============

PROVIDER_KEYS_FILE = Path(__file__).resolve().parent / "data" / "provider_keys.json"


def _ensure_data_dir():
    os.makedirs(str(PROVIDER_KEYS_FILE.parent), exist_ok=True)


def _load_provider_keys() -> dict:
    _ensure_data_dir()
    if PROVIDER_KEYS_FILE.exists():
        try:
            return json.loads(PROVIDER_KEYS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    env_path = PROJECT_DIR / ".env"
    legacy_key = os.getenv("STEP_API_KEY", "")
    if not legacy_key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("STEP_API_KEY="):
                legacy_key = line.strip().split("=", 1)[1]
                break
    if legacy_key:
        legacy_base = os.getenv("STEP_API_BASE", "https://api.deepseek.com")
        legacy_model = os.getenv("STEP_MODEL", "deepseek-v4-pro")
        keys = {"deepseek": {"apiKey": legacy_key, "base_url": legacy_base, "model": legacy_model, "updatedAt": datetime.now().isoformat()}}
        PROVIDER_KEYS_FILE.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
        return keys
    return {}


def _save_provider_key(provider_id: str, api_key: str, base_url: str, model: str):
    _ensure_data_dir()
    keys = _load_provider_keys()
    keys[provider_id] = {
        "apiKey": api_key, "base_url": base_url, "model": model,
        "updatedAt": datetime.now().isoformat(),
    }
    PROVIDER_KEYS_FILE.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask_key(key: str) -> str:
    if not key or len(key) < 10:
        return ""
    return key[:5] + "..." + key[-4:]


def _write_env(env_path: Path, updates: dict[str, str]):
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    new_lines = []
    written = set()
    for line in lines:
        replaced = False
        for key, val in updates.items():
            if line.strip().startswith(f"{key}="):
                new_lines.append(f"{key}={val}")
                written.add(key)
                replaced = True
                break
        if not replaced:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ============ Endpoints ============

@router.get("/api/health")
def health():
    provider_keys = _load_provider_keys()
    default_key = bool(os.getenv("STEP_API_KEY", ""))
    return {
        "status": "ok",
        "apiKeyPresent": default_key,
        "message": "AI-KB backend running",
        "providerKeys": {pid: _mask_key(info["apiKey"]) for pid, info in provider_keys.items()},
        "providerKeyPresent": {pid: True for pid in provider_keys},
    }


@router.get("/api/providers")
def list_providers():
    from app.rag_app.providers import list_providers as _list
    provider_keys = _load_provider_keys()
    providers = _list()
    known_ids = {p["id"] for p in providers}
    for p in providers:
        pid = p["id"]
        if pid in provider_keys:
            k = provider_keys[pid]
            p["keyConfigured"] = True
            p["keyPreview"] = _mask_key(k["apiKey"])
            p["configuredModel"] = k["model"]
        else:
            p["keyConfigured"] = False
            p["keyPreview"] = ""
            p["configuredModel"] = ""
    for pid, info in provider_keys.items():
        if pid not in known_ids:
            providers.append({
                "id": pid, "name": pid,
                "base_url": info.get("base_url", ""),
                "models": [info.get("model", "")],
                "docs": "", "keyConfigured": True,
                "keyPreview": _mask_key(info.get("apiKey", "")),
                "configuredModel": info.get("model", ""),
            })
    return {"providers": providers}


@router.get("/api/providers/active")
def active_provider():
    from app.rag_app.providers import list_providers as _list
    provider_keys = _load_provider_keys()
    active_pid = None
    active_model = ""
    active_base = ""
    active_key_preview = ""
    for pid, info in provider_keys.items():
        active_pid = pid
        active_model = info.get("model", "")
        active_base = info.get("base_url", "")
        active_key_preview = _mask_key(info.get("apiKey", ""))
        break
    if not active_pid:
        env_base = os.getenv("STEP_API_BASE", "")
        env_model = os.getenv("STEP_MODEL", "")
        env_key = os.getenv("STEP_API_KEY", "")
        if env_key:
            providers = _list()
            for p in providers:
                if p["base_url"] == env_base or p["base_url"].split("://")[1].split("/")[0] in env_base:
                    active_pid = p["id"]
                    break
            if not active_pid:
                active_pid = "custom"
            active_model = env_model
            active_base = env_base
            active_key_preview = _mask_key(env_key)
    return {
        "activeProvider": active_pid, "activeModel": active_model,
        "activeBase": active_base, "activeKeyPreview": active_key_preview,
        "hasActiveConfig": bool(active_pid),
    }


class SwitchReq(BaseModel):
    providerId: str


@router.post("/api/providers/switch")
async def switch_provider(r: SwitchReq):
    from app.rag_app.providers import get_provider
    provider_keys = _load_provider_keys()
    if r.providerId not in provider_keys:
        return {"success": False, "error": f"提供商 '{r.providerId}' 尚未配置 API Key，请先保存"}
    saved = provider_keys[r.providerId]
    api_key = saved.get("apiKey", "")
    base_url = saved.get("base_url", "")
    model = saved.get("model", "")
    if not api_key:
        return {"success": False, "error": f"提供商 '{r.providerId}' 的 Key 为空"}
    ep = PROJECT_DIR / ".env"
    _write_env(ep, {"STEP_API_KEY": api_key, "STEP_API_BASE": base_url, "STEP_MODEL": model})
    os.environ["STEP_API_KEY"] = api_key
    os.environ["STEP_API_BASE"] = base_url
    os.environ["STEP_MODEL"] = model
    from app.rag_app.config import Config
    Config.STEP_API_KEY = api_key
    Config.STEP_API_BASE = base_url
    Config.STEP_MODEL = model
    try:
        from app.rag_app import shared_engine
        shared_engine._engine = None
    except Exception:
        pass
    provider_name = get_provider(r.providerId)
    name = provider_name["name"] if provider_name else r.providerId
    return {
        "success": True, "message": f"已切换至 {name} · {model}",
        "providerId": r.providerId, "providerName": name, "model": model,
    }


class ValidateReq(BaseModel):
    apiKey: str
    providerId: str
    model: str = ""
    baseUrl: str = ""


@router.post("/api/providers/validate")
def validate_provider(r: ValidateReq):
    from app.rag_app.providers import get_provider, validate_key
    provider = get_provider(r.providerId)
    if not provider:
        if not r.baseUrl:
            return {"success": False, "error": "自定义提供商需要提供 API 端点地址"}
        base_url = r.baseUrl.rstrip("/")
        model = r.model or "default"
    else:
        base_url = provider["base_url"]
        model = r.model or provider["models"][0]
    result = validate_key(r.apiKey, base_url, model)
    # 验证失败时不保存 Key，避免 404、429 或网络错误把无效凭据持久化。
    should_save = result["success"]
    if should_save:
        _save_provider_key(r.providerId, r.apiKey, base_url, model)
        ep = PROJECT_DIR / ".env"
        _write_env(ep, {"STEP_API_KEY": r.apiKey, "STEP_API_BASE": base_url, "STEP_MODEL": model})
        os.environ["STEP_API_KEY"] = r.apiKey
        os.environ["STEP_API_BASE"] = base_url
        os.environ["STEP_MODEL"] = model
        from app.rag_app.config import Config
        Config.STEP_API_KEY = r.apiKey
        Config.STEP_API_BASE = base_url
        Config.STEP_MODEL = model
        try:
            from app.rag_app import shared_engine
            shared_engine._engine = None
        except Exception:
            pass
    if not result["success"] and should_save:
        result["saved"] = True
        result["message"] = "配置已保存（验证未通过但已写入，稍后可用）"
    return result


@router.post("/api/providers/clear")
async def clear_provider():
    PROVIDER_KEYS_FILE.write_text("{}", encoding="utf-8")
    ep = PROJECT_DIR / ".env"
    if ep.exists():
        lines = ep.read_text(encoding="utf-8").splitlines()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("STEP_API_KEY=") or stripped.startswith("STEP_API_BASE=") or stripped.startswith("STEP_MODEL="):
                new_lines.append("# " + line)
            else:
                new_lines.append(line)
        ep.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    for k in ("STEP_API_KEY", "STEP_API_BASE", "STEP_MODEL"):
        os.environ.pop(k, None)
    from app.rag_app.config import Config
    Config.STEP_API_KEY = ""
    Config.STEP_API_BASE = ""
    Config.STEP_MODEL = ""
    try:
        from app.rag_app import shared_engine
        shared_engine._engine = None
    except Exception:
        pass
    return {"success": True, "message": "配置已清除，服务需重启后完全生效"}


class DeleteProviderReq(BaseModel):
    providerId: str


@router.post("/api/providers/delete")
async def delete_provider(r: DeleteProviderReq):
    """删除单个提供商的 API Key 配置"""
    keys = _load_provider_keys()
    if r.providerId not in keys:
        return {"success": False, "error": f"提供商 '{r.providerId}' 未配置"}
    del keys[r.providerId]
    _ensure_data_dir()
    PROVIDER_KEYS_FILE.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "message": f"已删除 {r.providerId} 的配置"}


class SaveKeyReq(BaseModel):
    apiKey: str
    apiEndpoint: str = ""
    modelName: str = ""


@router.post("/api/save-key")
async def save_key(r: SaveKeyReq):
    from app.rag_app.config import Config
    k = r.apiKey.strip()
    if not k:
        return {"success": False, "error": "Empty key"}
    base_url = (r.apiEndpoint or "").strip() or Config.STEP_API_BASE
    base_url = _re.sub(r'/+$', '', base_url)
    base_url = _re.sub(r'/chat/completions/?$', '', base_url)
    base_url = _re.sub(r'/completions/?$', '', base_url)
    model = (r.modelName or "").strip() or Config.STEP_MODEL
    try:
        from app.rag_app.llm_client_factory import create_llm_client
        c = create_llm_client(api_key=k, base_url=base_url)
        c.chat.completions.create(model=model, messages=[{"role": "user", "content": "ok"}], max_tokens=5, timeout=15)
        ep = PROJECT_DIR / ".env"
        env_vars = {"STEP_API_KEY": k, "STEP_API_BASE": base_url, "STEP_MODEL": model}
        if ep.exists():
            ls = ep.read_text(encoding="utf-8").splitlines()
            new_lines = []
            written = set()
            for l in ls:
                stripped = l.strip()
                replaced = False
                for key, val in env_vars.items():
                    if stripped.startswith(f"{key}="):
                        new_lines.append(f"{key}={val}")
                        written.add(key)
                        replaced = True
                        break
                if not replaced:
                    new_lines.append(l)
            for key, val in env_vars.items():
                if key not in written:
                    new_lines.append(f"{key}={val}")
            ep.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.environ["STEP_API_KEY"] = k
        os.environ["STEP_API_BASE"] = base_url
        os.environ["STEP_MODEL"] = model
        Config.STEP_API_KEY = k
        Config.STEP_API_BASE = base_url
        Config.STEP_MODEL = model
        pid = base_url.split('://')[1].split('.')[0] if '://' in base_url else 'custom'
        _save_provider_key(pid, k, base_url, model)
        try:
            from app.rag_app import shared_engine
            shared_engine._engine = None
        except Exception:
            pass
        return {"success": True, "message": f"API key verified! ({model} @ {base_url})"}
    except Exception as e:
        err_msg = str(e)[:250]
        if "401" in err_msg or "Unauthorized" in err_msg:
            err_msg = f"认证失败(401): API Key无效或已过期"
        elif "404" in err_msg or "Not Found" in err_msg:
            err_msg = f"端点404: 模型 '{model}' 不存在或路径错误"
        elif "Connection" in err_msg or "timeout" in err_msg.lower() or "refused" in err_msg.lower():
            err_msg = f"网络不通: {err_msg[:100]}"
        return {"success": False, "error": err_msg, "debug": {"endpoint": base_url, "model": model}}
