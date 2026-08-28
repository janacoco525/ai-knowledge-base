"""AI 模型提供商注册表 — 单一权威来源, 2026-06-12 验证"""

from typing import Optional
from urllib.parse import urlparse

PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-chat",
        ],
        "docs": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2", "gpt-5.1", "gpt-5"],
        "docs": "https://platform.openai.com/api-keys",
    },
    "dashscope": {
        "name": "千问 (Qwen)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash"],
        "docs": "https://bailian.console.aliyun.com/",
    },
    "moonshot": {
        "name": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.6", "kimi-k2.5"],
        "docs": "https://platform.kimi.com/console/api-keys",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.1", "glm-5", "glm-4-plus", "glm-4-flash"],
        "docs": "https://open.bigmodel.cn/",
    },
    "stepfun": {
        "name": "阶跃星辰 (StepFun)",
        "base_url": "https://api.stepfun.com/v1",
        "models": ["step-2-16k"],
        "docs": "https://platform.stepfun.com/",
    },
    "mimo": {
        "name": "MiMo (小米)",
        "base_url": "https://api.xiaomimimo.com/v1",
        "models": ["mimo-v2.5"],
        "docs": "",
    },
}

def list_providers() -> list[dict]:
    """返回所有提供商列表"""
    return [
        {
            "id": key,
            "name": val["name"],
            "base_url": val["base_url"],
            "models": val["models"],
            "docs": val["docs"],
        }
        for key, val in PROVIDERS.items()
    ]

def get_provider(provider_id: str) -> Optional[dict]:
    """获取单个提供商信息"""
    p = PROVIDERS.get(provider_id)
    if not p:
        return None
    return {
        "id": provider_id,
        "name": p["name"],
        "base_url": p["base_url"],
        "models": p["models"],
        "docs": p["docs"],
    }

def match_provider_by_base_url(base_url: str) -> Optional[str]:
    """根据 base_url 匹配已注册的 provider。"""
    if not base_url:
        return None
    normalized = base_url.rstrip("/")
    for pid, info in PROVIDERS.items():
        if info["base_url"].rstrip("/") == normalized:
            return pid

    try:
        target_host = urlparse(normalized).hostname or ""
    except Exception:
        target_host = ""

    if not target_host:
        return None

    for pid, info in PROVIDERS.items():
        try:
            provider_host = urlparse(info["base_url"]).hostname or ""
        except Exception:
            provider_host = ""
        if provider_host == target_host:
            return pid
    return None


def guess_provider(api_key: str, base_url: str | None = None) -> Optional[str]:
    """优先按 base_url 判断；仅凭 key 前缀时宁可不猜，也不返回错误 provider。"""
    by_base_url = match_provider_by_base_url(base_url or "")
    if by_base_url:
        return by_base_url
    if not api_key:
        return None
    k = api_key.lower()
    if k.startswith("gsk_"):
        return "groq"
    return None

def validate_key(api_key: str, base_url: str, model: str, timeout: int = 15, max_retries: int = 3) -> dict:
    """验证 API Key 是否有效（带指数退避重试，防御 429 限流）"""
    import urllib.request
    import urllib.error as _err
    import json as _json
    import time as _time
    import random as _random

    url = base_url.rstrip("/") + "/chat/completions"
    data = _json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode()

    last_error = None

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=timeout)
            _ = _json.loads(resp.read())
            return {"success": True, "message": f"验证通过 ({model} @ {base_url})"}
        except _err.HTTPError as e:
            code = e.code
            if code == 401:
                return {"success": False, "error": "认证失败(401): API Key 无效或已过期"}
            elif code == 404:
                return {"success": False, "error": f"端点404: 模型 '{model}' 不存在或路径错误"}
            elif code == 429:
                last_error = e
                if attempt < max_retries:
                    wait = 2.0 * (2 ** attempt) + _random.uniform(0, 1)
                    if hasattr(e, 'headers'):
                        ra = e.headers.get('Retry-After')
                        if ra:
                            try:
                                wait = float(ra)
                            except ValueError:
                                pass
                    _time.sleep(wait)
                    continue
                return {"success": False, "error": "限流(429): API 限流过于频繁，已重试 3 次仍失败，请稍后再试"}
            else:
                if code >= 500 and attempt < max_retries:
                    last_error = e
                    _time.sleep(2.0 * (2 ** attempt))
                    continue
                body = e.read().decode(errors="replace")[:300]
                return {"success": False, "error": f"HTTP {code}: {body}"}
        except Exception as e:
            if attempt < max_retries:
                last_error = e
                _time.sleep(1.0 * (2 ** attempt))
                continue
            return {"success": False, "error": f"网络错误: {str(e)[:200]}"}

    # 不应到达此处
    body = getattr(last_error, 'read', lambda: b'')()
    if isinstance(body, bytes):
        body = body.decode(errors="replace")[:300]
    return {"success": False, "error": f"请求失败: {body if body else str(last_error)[:200]}"}
