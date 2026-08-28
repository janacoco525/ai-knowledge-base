"""
AI知识库 - 分析总结最小接口
先支持已索引文件集合的结构化摘要，不直接冒充完整文件夹分析系统。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait as _fut_wait, FIRST_COMPLETED as _FUT_FIRST_COMPLETED
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.rag_app.config import Config
from app.rag_app.shared_engine import get_engine

router = APIRouter(prefix="/api/analysis", tags=["分析总结"])

class AnalysisSummaryRequest(BaseModel):
    file_ids: List[str] = Field(default_factory=list, description="已索引文件 ID 列表，取自 /api/kb/files")
    domain: Optional[str] = Field(default=None, description="可选知识域筛选")
    max_files: int = Field(default=3, ge=1, le=8)
    max_chunks_per_file: int = Field(default=3, ge=1, le=8)
    max_highlights: int = Field(default=4, ge=1, le=8)
    analysis_focus: Literal["summary", "topics", "risks"] = "summary"


class TopicExtractionRequest(BaseModel):
    file_ids: List[str] = Field(default_factory=list, description="已索引文件 ID 列表")
    domain: Optional[str] = Field(default=None, description="可选知识域筛选")
    max_files: int = Field(default=5, ge=2, le=10, description="至少选2个文件做跨文件主题提炼")
    max_topics: int = Field(default=5, ge=2, le=10)


def _build_extractive_highlights(chunks: List[Dict[str, Any]], max_highlights: int) -> List[str]:
    highlights: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        text = " ".join((chunk.get("text") or "").split())
        if not text:
            continue
        snippet = text[:120].strip()
        if snippet and snippet not in seen:
            seen.add(snippet)
            highlights.append(snippet)
        if len(highlights) >= max_highlights:
            break
    return highlights


@router.post("/summary")
def summarize_scope(request: AnalysisSummaryRequest):
    eng = get_engine()
    scope = eng.get_analysis_scope(
        file_ids=request.file_ids,
        domain=request.domain,
        max_files=request.max_files,
        max_chunks_per_file=request.max_chunks_per_file,
    )
    chunks = scope.get("chunks", [])
    if not chunks:
        raise HTTPException(status_code=404, detail="当前筛选范围内没有可用于分析总结的已索引内容。")

    selected_files = scope.get("selected_files", [])
    scope_label = "、".join(file_item["name"] for file_item in selected_files[:3]) or "当前已索引范围"
    summary_payload = eng.summarize_chunks(
        chunks,
        scope_label=scope_label,
        analysis_focus=request.analysis_focus,
        max_highlights=request.max_highlights,
    )
    summary_payload["selected_files"] = selected_files
    summary_payload["meta"].update(
        {
            "scope_mode": scope.get("scope_mode", "recent"),
            "available_file_count": scope.get("available_file_count", 0),
            "selected_file_count": scope.get("selected_file_count", len(selected_files)),
            "selected_chunk_count": len(chunks),
        }
    )
    summary_payload["highlights"] = _build_extractive_highlights(chunks, request.max_highlights)
    return summary_payload


@router.post("/topics")
def extract_topics(request: TopicExtractionRequest):
    """跨文件主题提炼：分析多个文件，提取共同主题及来源映射"""
    eng = get_engine()
    scope = eng.get_analysis_scope(
        file_ids=request.file_ids,
        domain=request.domain,
        max_files=request.max_files,
        max_chunks_per_file=5,
    )
    chunks = scope.get("chunks", [])
    if len(chunks) < 2:
        raise HTTPException(status_code=404, detail="需要至少2个有内容的chunk才能做跨文件主题提炼。")

    selected_files = scope.get("selected_files", [])
    file_names = [f["name"] for f in selected_files]
    scope_label = "、".join(file_names[:5])

    topics_result = eng.extract_cross_file_topics(
        chunks=chunks,
        scope_label=scope_label,
        file_names=file_names,
        max_topics=request.max_topics,
    )

    topics_result["selected_files"] = selected_files
    topics_result["meta"].update({
        "scope_mode": scope.get("scope_mode", "recent"),
        "selected_file_count": len(selected_files),
        "selected_chunk_count": len(chunks),
    })
    return topics_result


# ----- AI 解释器 -----

interpret_router = APIRouter()

# 解读缓存（省 token）：同文本 + 同模式结果复用，避免反复解读同一段文字重复花钱
_interpret_cache: dict[str, str] = {}
_INTERPRET_CACHE_MAX = 200


def _interpret_cache_key(mode: str, text: str) -> str:
    """缓存键 = 模式 + 文本归一化哈希（去除空白差异）"""
    import hashlib
    normalized = " ".join(text.split())
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
    return f"{mode}:{digest}"


class InterpretRequest(BaseModel):
    text: str
    mode: Literal["simple", "deep"]

@interpret_router.post("/api/ai/interpret")
def interpret_text(request: InterpretRequest):
    """对选中的文本进行AI解读（同文本+模式缓存，省 token）"""
    from app.rag_app.config import Config
    from app.rag_app.llm_client_factory import create_llm_client

    # 命中缓存 → 直接返回（不消耗 LLM token）
    cache_key = _interpret_cache_key(request.mode, request.text)
    cached = _interpret_cache.get(cache_key)
    if cached is not None:
        return {"interpretation": cached, "cached": True}

    client = create_llm_client()

    prompts = {
        "simple": "请用1-3句话精准解释以下内容的核心含义，语言简洁专业，不做展开，不举例，不要口语化：",
        "deep": "请系统分析以下内容，按【要点】→【原理】→【启发】三部分展开，每部分用标题标注：",
    }
    prompt = prompts.get(request.mode, prompts["simple"])

    try:
        resp = client.chat.completions.create(
            model=Config.STEP_MODEL,
            messages=[
                {"role": "user", "content": f"{prompt}\n\n{request.text}"}
            ],
            temperature=0.3, max_tokens=1200, timeout=60,
        )
        interpretation = resp.choices[0].message.content or ""
        interpretation = interpretation.strip()
        # 写入缓存（LRU 上限保护）
        if len(_interpret_cache) >= _INTERPRET_CACHE_MAX:
            first_key = next(iter(_interpret_cache))
            _interpret_cache.pop(first_key, None)
        _interpret_cache[cache_key] = interpretation
        return {"interpretation": interpretation, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI解释失败: {str(e)[:200]}")


# ----- 全文翻译（非中文 → 中文）-----

translate_router = APIRouter()

# 翻译缓存（省 token）：同文本结果复用
# 结构：{"tr:{md5}": "译文文本", "trp:{md5}": {"groups": [{src,tgt,skipped}...], "complete": bool}}
_translate_cache: dict = {}
_TRANSLATE_CACHE_MAX = 100
# 磁盘持久化缓存：重启不丢 + 断点续传（2026-08-06 长文档翻译提速）
_TRANSLATE_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "translate_cache.json"
)
_translate_cache_loaded = False
_translate_lock = threading.Lock()

# 段落级翻译任务（后台执行，前端轮询增量获取译文）
_translate_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()
_TASKS_MAX = 20
_TASKS_TTL_SECONDS = 3600
_TRANSLATE_CONCURRENCY = 6  # 并发 3 → 6：长文档吞吐翻倍
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    """全局翻译线程池（懒初始化，避免模块导入时创建）"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_TRANSLATE_CONCURRENCY)
    return _executor


def _ensure_translate_cache_loaded() -> None:
    """磁盘翻译缓存懒加载（文件缺失/损坏时静默重建空缓存，不阻断翻译）"""
    global _translate_cache_loaded
    if _translate_cache_loaded:
        return
    with _translate_lock:
        if _translate_cache_loaded:
            return
        try:
            with open(_TRANSLATE_CACHE_FILE, "r", encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                for k, v in disk.items():
                    if k not in _translate_cache:
                        _translate_cache[k] = v
        except Exception:
            pass  # 文件不存在或损坏 → 空缓存
        _translate_cache_loaded = True


def _save_translate_cache() -> None:
    """翻译缓存全量落盘（原子写：tmp + os.replace）"""
    with _translate_lock:
        try:
            tmp = _TRANSLATE_CACHE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_translate_cache, f, ensure_ascii=False)
            os.replace(tmp, _TRANSLATE_CACHE_FILE)
        except Exception:
            pass  # 写失败不致命，下次继续写


def _cache_set(key: str, value: Any) -> None:
    """写缓存（LRU 上限保护，统一入口）"""
    with _translate_lock:
        if key not in _translate_cache and len(_translate_cache) >= _TRANSLATE_CACHE_MAX:
            first_key = next(iter(_translate_cache))
            _translate_cache.pop(first_key, None)
        _translate_cache[key] = value


def _para_cache_get(cache_key: str):
    """段落级缓存读取 → (groups 列表 or None, complete bool)；兼容旧格式纯列表"""
    with _translate_lock:
        v = _translate_cache.get(cache_key)
    if v is None:
        return None, False
    if isinstance(v, dict):
        return v.get("groups"), bool(v.get("complete"))
    return v, True  # 旧格式（纯列表）视为完整


def _para_cache_save_partial(cache_key: str, groups: list) -> None:
    """部分完成落盘（断点续传：中断后重试只翻剩余组）"""
    _cache_set(cache_key, {"groups": [dict(g) for g in groups], "complete": False})


def _para_cache_save_complete(cache_key: str, groups: list) -> None:
    """全部完成落盘 + 立即持久化"""
    _cache_set(cache_key, {"groups": [dict(g) for g in groups], "complete": True})
    _save_translate_cache()


def _para_cache_delete(cache_key: str) -> None:
    """删除缓存条目并落盘（重新翻译时清掉旧译文，避免后续误命中）"""
    with _translate_lock:
        existed = _translate_cache.pop(cache_key, None) is not None
    if existed:
        _save_translate_cache()


def _prune_translate_tasks() -> None:
    """清理过期/超限任务（防内存泄漏）"""
    now = time.time()
    with _tasks_lock:
        stale_ids = [
            tid for tid, t in _translate_tasks.items()
            if now - t.get("created_at", 0) > _TASKS_TTL_SECONDS
        ]
        for tid in stale_ids:
            _translate_tasks.pop(tid, None)
        while len(_translate_tasks) >= _TASKS_MAX:
            oldest = min(_translate_tasks, key=lambda k: _translate_tasks[k].get("created_at", 0))
            _translate_tasks.pop(oldest, None)


def _translate_cache_key(text: str) -> str:
    import hashlib
    normalized = " ".join(text.split())
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
    return f"tr:{digest}"


def _is_chinese(text: str) -> bool:
    """粗略判断文本是否主要为中文（含汉字比例 > 30%）"""
    if not text or not text.strip():
        return True
    hanzi = sum(1 for ch in text if "一" <= ch <= "鿿")
    return hanzi / max(1, len(text)) > 0.3


class TranslateRequest(BaseModel):
    text: str
    # 目标语言默认中文；保留字段便于未来扩展
    target_lang: str = "zh"
    # force=True：忽略缓存全量重新翻译（更新译文）
    force: bool = False
    # check_only=True：只查询磁盘缓存/运行中任务，命中直接返回，绝不启动新任务
    check_only: bool = False


@translate_router.post("/api/translate")
def translate_text(request: TranslateRequest):
    """全文翻译：非中文文本 → 中文（同文本缓存，省 token；磁盘持久化）"""
    from app.rag_app.config import Config
    from app.rag_app.llm_client_factory import create_llm_client

    _ensure_translate_cache_loaded()
    text = request.text or ""
    # 已是中文 → 无需翻译
    if _is_chinese(text):
        return {"translation": text, "cached": True, "skipped": True}

    cache_key = _translate_cache_key(text)
    cached = _translate_cache.get(cache_key)
    if cached is not None:
        return {"translation": cached, "cached": True}

    # 超长文本分段：LLM 有 token 上限，按段落切块翻译
    client = create_llm_client()
    prompt = "请将以下非中文内容完整翻译为简体中文。只输出翻译结果，不要解释、不要加注。原文：\n\n"

    # 若文本过长，截断到合理长度（约 4000 字），超出部分由前端分批调用
    MAX_CHARS = 4000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[内容过长已截断，请分段翻译]"

    try:
        resp = client.chat.completions.create(
            model=Config.STEP_MODEL,
            messages=[
                {"role": "user", "content": f"{prompt}{text}"}
            ],
            temperature=0.2, max_tokens=4096, timeout=90,
        )
        translation = (resp.choices[0].message.content or "").strip()
        _cache_set(cache_key, translation)
        return {"translation": translation, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"翻译失败: {str(e)[:200]}")


def _translate_para_cache_key(text: str) -> str:
    """段落级翻译缓存键（与单段接口的 tr: 前缀区分，避免类型冲突）"""
    import hashlib
    normalized = " ".join(text.split())
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
    return f"trp:{digest}"


def _split_paragraph_groups(text: str, max_chars: int = 3500) -> list[str]:
    """按段落（\n\n）切分文本并聚合成不超过 max_chars 的组。
    含代码围栏（```）的段落强制独立成组，避免拆散代码块。
    与前端 splitParagraphGroups 规则一致，保证译文能逐组对齐。"""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    groups: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        p = para.strip("\n")
        if not p:
            continue
        if "```" in p:
            if current:
                groups.append(current)
                current = ""
            groups.append(p)
            continue
        if current and len(current) + len(p) + 2 > max_chars:
            groups.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        groups.append(current)
    return groups


def _split_oversized_group(g: str, max_chars: int = 3500) -> list[str]:
    """超长组切子块：优先按行切（保持行完整性）；无换行的超长单行按字符硬切，
    避免整行超长导致单请求超时或输出截断。长文档提速（2026-08-06）。"""
    if len(g) <= max_chars:
        return [g]
    subs: list[str] = []
    cur = ""
    for ln in g.split("\n"):
        # 超长单行（表格行/URL/长文本无换行）按字符硬切
        while len(ln) > max_chars:
            if cur:
                subs.append(cur)
                cur = ""
            subs.append(ln[:max_chars])
            ln = ln[max_chars:]
        if cur and len(cur) + len(ln) + 1 > max_chars:
            subs.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        subs.append(cur)
    return subs


def _build_translate_units(groups: list[str]) -> list[tuple[int, int, str]]:
    """把组展开为翻译单位（超长组切子块）：[(组下标, 子块下标, 文本)]。中文组跳过。"""
    units: list[tuple[int, int, str]] = []
    for gi, g in enumerate(groups):
        if _is_chinese(g):
            continue
        for si, s in enumerate(_split_oversized_group(g)):
            units.append((gi, si, s))
    return units


def _resume_from_cached(groups: list[dict], cached_groups: list) -> int:
    """断点续传：把缓存里已翻译的组填回 groups，返回复用组数"""
    reused = 0
    if not cached_groups:
        return 0
    for i, cg in enumerate(cached_groups):
        if i < len(groups) and isinstance(cg, dict) and cg.get("tgt"):
            groups[i]["tgt"] = cg["tgt"]
            reused += 1
    return reused


def _prepare_translate_task(text: str, force: bool = False) -> dict:
    """构建翻译任务（纯逻辑，可测）：分组 + 断点续传 + 单位展开。
    返回 {skipped} 或 {cache_key, groups, reused, units, cached}。
    force=True 时跳过完整缓存与断点续传（重新翻译语义，全部重翻）。"""
    _ensure_translate_cache_loaded()
    if not text or _is_chinese(text):
        return {"skipped": True}
    cache_key = _translate_para_cache_key(text)
    cached_groups, complete = (None, False) if force else _para_cache_get(cache_key)
    if complete and cached_groups:
        return {
            "cache_key": cache_key,
            "groups": [dict(g) for g in cached_groups],
            "reused": len(cached_groups), "units": [], "cached": True,
        }
    groups = [{"src": g, "tgt": "", "skipped": _is_chinese(g)} for g in _split_paragraph_groups(text)]
    if not groups:
        return {"skipped": True}
    reused = 0 if force else _resume_from_cached(groups, cached_groups)
    units: list[tuple[int, int, str]] = []
    for gi, si, s in _build_translate_units([g["src"] for g in groups]):
        if groups[gi]["tgt"]:
            continue  # 断点续传：已翻译的组跳过
        units.append((gi, si, s))
    return {"cache_key": cache_key, "groups": groups, "reused": reused, "units": units, "cached": False}


_TRANSLATE_PROMPT = (
    "请将以下非中文内容完整翻译为简体中文。"
    "严格保持原文的段落划分，不要合并或拆分段落，"
    "只输出翻译结果，不要解释、不要加注。原文：\n\n"
)


def _translate_unit_with_retry(client, text: str, is_long: bool) -> str:
    """单单位翻译；429/连接/超时自动退避重试（最多 3 次，间隔 2s/4s）"""
    attempts = 3
    for i in range(attempts):
        try:
            resp = client.chat.completions.create(
                model=Config.STEP_MODEL,
                messages=[{"role": "user", "content": f"{_TRANSLATE_PROMPT}{text}"}],
                temperature=0.2,
                max_tokens=8192 if is_long else 4096,
                timeout=180 if is_long else 90,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            err = str(e)
            retriable = (
                "429" in err or "rate" in err.lower() or "timeout" in err.lower()
                or "connection" in err.lower()
            )
            if retriable and i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise


def _run_translate_task(task_id: str, cache_key: str) -> None:
    """后台执行翻译任务：单位并发 → 组内子块按序拼接 → 组级落盘（断点续传）。
    任一组失败 → 任务标记 failed，已完成组保留。
    取消：提交/消费循环均检查 status == cancelled → 停止并保留已完成组（可续传）。"""
    from app.rag_app.llm_client_factory import create_llm_client

    with _tasks_lock:
        task = _translate_tasks[task_id]
        units = list(task["_units"])
        groups = task["groups"]
    client = create_llm_client()
    executor = _get_executor()
    cancelled = False
    task_finished = False
    # 滚动窗口提交：按文档顺序维持至多 N 个在途单位，先完成先补位提交下一单位，
    # 保证译文严格按文档从上到下顺序出现（一次性全提交会导致完成顺序随机、底部译文先出）
    window: dict = {}
    next_idx = 0
    try:
        while not cancelled and not task_finished and (window or next_idx < len(units)):
            with _tasks_lock:
                if task.get("status") == "cancelled":
                    cancelled = True
                    break
            while next_idx < len(units) and len(window) < _TRANSLATE_CONCURRENCY:
                gi, si, text = units[next_idx]
                window[executor.submit(_translate_unit_with_retry, client, text, len(text) > 8000)] = (gi, si)
                next_idx += 1
            if not window:
                break
            done_futs, _ = _fut_wait(set(window.keys()), return_when=_FUT_FIRST_COMPLETED)
            for fut in done_futs:
                gi, si = window.pop(fut)
                tgt = fut.result()
                group_done = False
                with _tasks_lock:
                    results = task["_results"]
                    results[(gi, si)] = tgt
                    need = task["_sub_need"].get(gi, 1)
                    done_subs = sum(1 for (g2, _s2) in results if g2 == gi)
                    group_done = done_subs >= need
                    if group_done:
                        parts = [results[(gi, j)] for j in range(need)]
                        groups[gi]["tgt"] = "\n".join(parts)
                        for key in [k for k in results if k[0] == gi]:
                            del results[key]
                        task["done_count"] += 1
                        if task["done_count"] >= task["total"]:
                            task_finished = True
                if group_done:
                    _para_cache_save_partial(cache_key, groups)
                    if task["done_count"] % 5 == 0:
                        _save_translate_cache()
                if task_finished:
                    break
        with _tasks_lock:
            if task.get("status") != "cancelled":
                task["status"] = "done"
            task["_units"] = []
            task["_results"] = {}
        if not cancelled and task.get("status") != "cancelled":
            _para_cache_save_complete(cache_key, groups)
        else:
            # 取消：已完成组保留为部分缓存，下次继续（断点续传）
            _para_cache_save_partial(cache_key, groups)
    except Exception as e:
        with _tasks_lock:
            task["status"] = "failed"
            task["error"] = str(e)[:300]
            task["_units"] = []
        _save_translate_cache()  # 保留已完成组，断点续传



@translate_router.post("/api/translate-paragraphs")
def start_translate_task(request: TranslateRequest):
    """段落级全文翻译（任务化，2026-08-06 长文档提速）：
    - 长文档翻译不阻塞请求：立即返回 task_id，后台并发 6 路执行
    - 前端轮询 /api/translate-paragraphs/{task_id} 增量获取译文（翻完一组出一组）
    - 磁盘持久化缓存：重复翻译秒出；中断后重试断点续传（只翻剩余组）
    - force=True：忽略缓存全量重翻（更新译文）；check_only=True：只查缓存/运行中任务，不启动
    - 全文缓存完整命中 → 直接返回 done"""
    prep = _prepare_translate_task(request.text or "", force=request.force)
    if prep.get("skipped"):
        return {"task_id": None, "status": "done", "total": 0, "done_count": 0, "groups": [], "cached": True, "skipped": True}

    groups = prep["groups"]
    cache_key = prep["cache_key"]
    # 重新翻译：先清掉旧缓存，避免新任务期间 check_only 误命中旧译文
    if request.force:
        _para_cache_delete(cache_key)
    if prep.get("cached"):
        return {"task_id": None, "status": "done", "total": len(groups), "done_count": len(groups), "groups": groups, "cached": True, "skipped": False}
    # 只查不启动：命中运行中任务则返回 task_id 让前端续上轮询，否则 idle
    if request.check_only:
        with _tasks_lock:
            running = next((t for t in _translate_tasks.values()
                            if t.get("cache_key") == cache_key and t.get("status") == "running"), None)
        if running:
            return {"task_id": running["id"], "status": "running", "total": running["total"],
                    "done_count": running["done_count"], "groups": running["groups"],
                    "cached": False, "reused": 0, "skipped": False}
        return {"task_id": None, "status": "idle", "total": 0, "done_count": 0,
                "groups": [], "cached": False, "skipped": False}
    units = prep["units"]
    # 断点续传后无剩余 → 补全缓存并直接返回
    if not units:
        _para_cache_save_complete(cache_key, groups)
        return {"task_id": None, "status": "done", "total": len(groups), "done_count": len(groups), "groups": groups, "cached": True, "skipped": False}

    sub_need: dict[int, int] = {}
    for gi, si, _s in units:
        sub_need[gi] = max(sub_need.get(gi, 0), si + 1)
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id, "status": "running", "error": None,
        "total": len(groups), "done_count": 0,
        "groups": groups,
        "_units": units, "_results": {}, "_sub_need": sub_need,
        "cache_key": cache_key,
        "created_at": time.time(),
    }
    _prune_translate_tasks()
    with _tasks_lock:
        _translate_tasks[task_id] = task
    threading.Thread(target=_run_translate_task, args=(task_id, cache_key), daemon=True).start()
    return {
        "task_id": task_id, "status": "running",
        "total": task["total"], "done_count": 0, "reused": prep["reused"],
        "groups": groups, "cached": False, "skipped": False,
    }


@translate_router.get("/api/translate-paragraphs/{task_id}")
def get_translate_task(task_id: str):
    """轮询翻译任务：返回增量译文（未完成组 tgt 为空字符串）"""
    with _tasks_lock:
        task = _translate_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="翻译任务不存在或已过期")
    return {
        "task_id": task_id,
        "status": task["status"],
        "error": task.get("error"),
        "total": task["total"],
        "done_count": task["done_count"],
        "groups": task["groups"],
        "cached": False,
    }


@translate_router.post("/api/translate-paragraphs/{task_id}/cancel")
def cancel_translate_task(task_id: str):
    """取消翻译任务：任务线程检测 cancelled 标志后停止，已完成组保留为部分缓存（可续传）"""
    with _tasks_lock:
        task = _translate_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="翻译任务不存在或已过期")
    with _tasks_lock:
        if task["status"] == "running":
            task["status"] = "cancelled"
    return {"task_id": task_id, "status": task["status"]}
