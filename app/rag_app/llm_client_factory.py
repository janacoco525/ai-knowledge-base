"""
LLM 客户端工厂 — 统一创建带重试的 OpenAI 兼容客户端

所有模块统一调用 create_llm_client()，禁止裸调 OpenAI()。
自动处理 429 限流、5xx 服务端错误、连接超时、编码问题等临时故障。
"""
from openai import OpenAI
import httpx
import json
import re

# httpx 层：代理环境自动禁用（项目不需要代理），强制 UTF-8 编码，禁用 Brotli 避免解压乱码
_HTTPX_CLIENT = httpx.Client(
    timeout=httpx.Timeout(60.0, connect=10.0),
    follow_redirects=True,
    # 显式禁用系统代理环境变量（2026-08-19：环境残留代理变量会导致 LLM 瞬时 Connection error 静默降级）
    trust_env=False,
    # 禁用 Brotli 压缩，避免 httpx 解压时导致 UTF-8 乱码
    headers={"Accept-Encoding": "gzip, deflate"},
)


def create_llm_client(api_key: str = None, base_url: str = None, max_retries: int = 3):
    from app.rag_app.config import Config
    return OpenAI(
        api_key=api_key or Config.STEP_API_KEY,
        base_url=base_url or Config.STEP_API_BASE,
        max_retries=max_retries,
        http_client=_HTTPX_CLIENT,
    )


def token_budget(desired_output: int, model: str = None) -> int:
    """DeepSeek v4 pro 等推理模型会消耗 reasoning tokens，需要更大的 max_tokens 预算。
    非推理模型直接返回 desired_output。"""
    from app.rag_app.config import Config
    m = (model or Config.STEP_MODEL or "").lower()
    if "deepseek" in m and ("v4" in m or "r1" in m):
        return desired_output * 3
    return desired_output


_JSON_BRACE = re.compile(r"\{[\s\S]*\}")
_JSON_BRACKET = re.compile(r"\[[\s\S]*\]")


def parse_llm_json(raw: str) -> dict | list | None:
    """容错解析 LLM 输出的 JSON（2026-08-05 统一入口，替代各处裸 json.loads）。

    LLM 输出常含 markdown 围栏、尾逗号、未加引号的键、前后杂文。
    逐级降级：严格解析 → 提取对象/数组 → 清理尾逗号 + 补键引号 → 兜底空。
    返回 None 表示解析失败，调用方需自行 fallback。
    """
    if not raw:
        return None
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # ⛔ 2026-08-14：提取最外层 JSON 对象/数组时，按"先出现的开放符号"定序。
    # 旧实现永远先试 {…}，贪婪匹配到最后一个 } —— 多元素数组被误拆成
    # 最后一个对象的 dict（实测 _do_generate_cards 因此 0 张，LLM 加 ```json
    # 围栏后必现）。数组以 [ 开头 → 先试 _JSON_BRACKET，对象才先试 _JSON_BRACE。
    first_brace = text.find("{")
    first_bracket = text.find("[")
    if first_bracket >= 0 and (first_brace < 0 or first_bracket < first_brace):
        m = _JSON_BRACKET.search(text) or _JSON_BRACE.search(text)
    else:
        m = _JSON_BRACE.search(text) or _JSON_BRACKET.search(text)
    if m:
        fragment = m.group(0)
        # 清理尾逗号（,} / ,]）
        fragment = re.sub(r",\s*([}\]])", r"\1", fragment)
        # 给未加引号的键补引号（{"foo": 1} → {"foo": 1}，但跳过已加引号与数字/布尔/null）
        fragment = re.sub(
            r"([{,])\s*([a-zA-Z_]\w*)\s*:",
            lambda mt: f'{mt.group(1)}"{mt.group(2)}":',
            fragment,
        )
        try:
            return json.loads(fragment)
        except json.JSONDecodeError:
            pass
    return None

