"""
LLM 操作路由
职责：总结、脑图、实体提取、对比、术语解释、卡片生成
（2026-08-13 拆分：问答/联网/流式/追问 已迁至 chat_ops.py）
"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.rag_app.llm_client_factory import token_budget

router = APIRouter(tags=["LLMOps"])

# ⛔ 2026-08-19：脑图生成进度注册表（线程安全）——长文档脑图最多拆 48 章任务、
# 每章一次 LLM 调用（约 30s），6 并发全量生成需 3-5 分钟；新电脑无缓存首次生成
# 更久。前端轮询此表显示“第 N/M 章”，避免用户以为卡死；key = fileId or title。
_MINDMAP_PROGRESS: dict[str, dict] = {}
_MINDMAP_PROGRESS_LOCK = threading.Lock()


def _report_mindmap_progress(progress_key: str, **kw):
    """更新/重置某个脑图生成任务的进度（无 key 则跳过，短文档/缓存命中不注册）。"""
    if not progress_key:
        return
    with _MINDMAP_PROGRESS_LOCK:
        p = _MINDMAP_PROGRESS.setdefault(progress_key, {
            "phase": "structure", "total": 0, "done": 0,
            "current": "", "started_at": time.time(), "finished": False,
        })
        p.update(kw)


def _parse_llm_json(raw: str) -> dict:
    """兼容旧调用点：委托公共容错解析器，返回 dict（失败则空 dict）。"""
    from app.rag_app.llm_client_factory import parse_llm_json
    parsed = parse_llm_json(raw)
    if isinstance(parsed, dict):
        return parsed
    return {}


# ============ Request Models ============

class SummaryReq(BaseModel):
    title: str = ""
    content: str = Field(..., max_length=100_000)


class ExtractReq(BaseModel):
    content: str = Field(..., max_length=100_000)
    max_nodes: int = 30
    doc_type: str = "auto"


class MindmapReq(BaseModel):
    title: str = ""
    content: str = Field(...)
    fileId: str = ""  # 可选，用于 LLM 缓存键


class CompareReq(BaseModel):
    documents: list[dict] = Field(..., min_length=2, max_length=10)


class DefineReq(BaseModel):
    term: str = Field(..., max_length=500)
    context: str = Field(default="", max_length=20_000)


class CardReq(BaseModel):
    title: str = ""
    # 2026-08-14：放宽到 350K 防其他调用方传全文触发 422（_do_generate_cards 内部截断 30000）
    content: str = Field(..., max_length=350_000)
    docId: str = ""


# ============ Summarize ============

def _llm_text(eng, prompt: str, max_tokens: int = 900) -> str:
    """统一的纯 LLM 文本调用（T42，2026-08-12）。"""
    if not getattr(eng, "llm_client", None):
        raise HTTPException(503, "LLM 客户端未配置")
    from app.rag_app.llm_client_factory import token_budget
    resp = eng.llm_client.chat.completions.create(
        model=getattr(eng, "model_name", None),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=token_budget(max_tokens),
        timeout=120,
    )
    return (resp.choices[0].message.content or "").strip()


def _do_summarize(eng, title: str, content: str) -> str:
    """结构化摘要（T42：/api/ai/summarize 与 /api/gemini/summarize 共用）。"""
    import hashlib
    cache_key = f"ai-summarize:{hashlib.md5((title + content[:40000]).encode()).hexdigest()[:16]}"
    cached = eng.kb.get_llm_cache(cache_key, "ai")
    if cached:
        return cached
    text = content[:40000]
    prompt = (
        "你是严谨的学术摘要助手。请对以下文档生成中文结构化摘要，格式：\n"
        "【核心主题】一句话\n【要点】1. ... 2. ...（最多6条）\n【结论/启示】一到两句话\n"
        f"标题：{title}\n\n正文：\n{text}"
    )
    summary = _llm_text(eng, prompt, max_tokens=900)
    if summary:
        eng.kb.set_llm_cache(cache_key, "ai", summary)
    return summary or "未能生成摘要"


def _do_define(eng, term: str, context: str) -> str:
    """上下文敏感术语定义（T42：/api/ai/define-term 与 /api/gemini/define 共用）。"""
    import hashlib
    cache_key = f"ai-define:{hashlib.md5((term + context[:10000]).encode()).hexdigest()[:16]}"
    cached = eng.kb.get_llm_cache(cache_key, "ai")
    if cached:
        return cached
    prompt = (
        f"请基于给定上下文，用中文给出术语「{term}」的学术化定义（150字内），"
        f"并简述它在该语境下的作用。\n上下文：{context[:10000] or '（无上下文）'}"
    )
    definition = _llm_text(eng, prompt, max_tokens=400)
    if definition:
        eng.kb.set_llm_cache(cache_key, "ai", definition)
    return definition or f"（未能生成「{term}」的定义）"


def _sample_doc_uniform(eng, doc_id: str, max_chars: int = 30000, max_chunks: int = 40) -> str:
    """全书等距采样（对标 RAG 长文档均匀采样，2026-08-14，调研见任务十五）。

    禁止"首部截断"：长文档开头可能是自传/导言/序章，知识点密度低且遗漏核心
    （《原则》前 3 万字基本是"我的历程"+导言，生活/工作原则在正文 → 首部截断
    提炼 0 张）。按 chunk_index 等距取代表块覆盖首/中/尾，拼接后累计 ≤ max_chars，
    与 rag_engine._overview_sampling 同款算法（含 token 预算自适应）。
    """
    if not doc_id:
        return ""
    try:
        chunks = eng.kb.get_chunks_by_file(doc_id, max_chunks=999999)
    except Exception:
        return ""
    if not chunks:
        return ""
    n = len(chunks)
    avg_len = max(1, sum(len(c.get("text") or "") for c in chunks) // n)
    max_chunks = min(max_chunks, max(8, max_chars // max(avg_len, 200)))
    picked = chunks
    if n > max_chunks:
        picked = [chunks[round(i * (n - 1) / (max_chunks - 1))] for i in range(max_chunks)]
        # 去重（等距索引可能重复）
        seen: set[int] = set()
        uniq = []
        for c in picked:
            ci = (c.get("metadata") or {}).get("chunk_index", 0)
            if ci in seen:
                continue
            seen.add(ci)
            uniq.append(c)
        picked = uniq or picked
    parts = []
    total = 0
    for c in picked:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(text[:remaining])
            break
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts)


def _do_generate_cards(eng, title: str, content: str, doc_id: str = "") -> list[dict]:
    """记忆闪卡提炼（T42：/api/ai/generate-cards 与 /api/gemini/card 共用）。"""
    # ⛔ 2026-08-14（v2）：长文档一律走"全书等距采样"，禁止首部截断。
    # 旧链路 rebuild_full_text 重建 29.7 万字后再 content[:30000] 仍是首部偏见——
    # 《原则》前 3 万字是自传+导言，核心原则在正文，实测提炼 0 张。
    # 有 doc_id 即以库内 chunks 等距采样覆盖首/中/尾（前端不再拉全文/截断）。
    if doc_id:
        content = _sample_doc_uniform(eng, doc_id) or content
    elif not content or not content.strip():
        return []
    import hashlib
    cache_key = f"ai-cards-v2:{hashlib.md5((title + content[:30000]).encode()).hexdigest()[:16]}"
    cached = eng.kb.get_llm_cache(cache_key, "ai")
    if isinstance(cached, list):
        return cached
    cards = []
    for attempt in (1, 2):
        # ⛔ 2026-08-14：attempt=2 强制"直接输出 JSON 数组"——实测偶发返回
        # 不可解析内容（2049 首次 0 张），重试一次避免用户手动再点
        prompt = (
            "你是记忆卡片提炼助手。从文档中提炼 8~15 张问答式记忆闪卡，"
            "返回 JSON 数组，每项形如 {\"front\": \"问题\", \"back\": \"答案\", \"tags\": [\"标签\"]}。"
            "只返回 JSON，不要多余文字。\n"
            f"标题：{title}\n正文：{content[:30000]}"
        )
        if attempt == 2:
            prompt = "直接输出 JSON 数组本身（不要 ```json 代码围栏、不要解释文字）。\n" + prompt
        raw = _llm_text(eng, prompt, max_tokens=1500)
        from app.rag_app.llm_client_factory import parse_llm_json as _parse_json_raw
        parsed = _parse_json_raw(raw)  # 数组/对象都要（llm_ops._parse_llm_json 只透传 dict）
        cards_raw = parsed.get("cards") if isinstance(parsed, dict) else parsed
        if not isinstance(cards_raw, list):
            cards_raw = []
        for item in cards_raw[:15]:
            if isinstance(item, dict) and item.get("front") and item.get("back"):
                cards.append({
                    "docId": doc_id,
                    "front": str(item["front"])[:300],
                    "back": str(item["back"])[:800],
                    "tags": item.get("tags") if isinstance(item.get("tags"), list) else ["AI 生成"],
                })
        if cards:
            break
    if cards:
        eng.kb.set_llm_cache(cache_key, "ai", cards)
    return cards


@router.post("/api/gemini/summarize")
def summarize(r: SummaryReq):
    from app.rag_app.shared_engine import get_engine
    return {"summary": _do_summarize(get_engine(), r.title, r.content)}


@router.post("/api/ai/summarize")
def ai_summarize(r: SummaryReq):
    """T42：DocEditor handleAISummarize 调用点（原 404）。"""
    from app.rag_app.shared_engine import get_engine
    return {"summary": _do_summarize(get_engine(), r.title, r.content)}


# ============ Mindmap ============

MAX_CHAPTER_CHARS = 8000
MAX_CHAPTERS = 12
MAX_PARTS = 6
MAX_CHAPTERS_PER_PART = 8
MAX_CHUNK_CHARS = 6000
MAX_PARALLEL = 6
OVERLAP_CHARS = 200


@router.get("/api/gemini/mindmap/progress")
def mindmap_progress(file_id: str = ""):
    """⛔ 2026-08-19：脑图生成进度查询（长文档多章任务，前端轮询显示“第 N/M 章”）。
    已完成条目惰性清理（120s 后删除），防止注册表无限增长。"""
    empty = {"running": False, "phase": "", "total": 0, "done": 0,
             "current": "", "pct": 0, "finished": False}
    if not file_id:
        return empty
    with _MINDMAP_PROGRESS_LOCK:
        p = _MINDMAP_PROGRESS.get(file_id)
        if not p:
            return empty
        if p.get("finished") and time.time() - p.get("started_at", 0) > 120:
            _MINDMAP_PROGRESS.pop(file_id, None)
            return empty
        pct = int(p["done"] / p["total"] * 100) if p.get("total") else 0
        return {**p, "running": not p.get("finished", False), "pct": pct}


@router.post("/api/gemini/generate-mindmap")
def generate_mindmap(r: MindmapReq):
    from app.rag_app.shared_engine import get_engine
    import hashlib, time
    eng = get_engine()
    content = r.content or ""
    title = r.title or "无标题"
    progress_key = r.fileId or title

    content_hash = hashlib.md5(content.encode()).hexdigest()[:16]
    # ⛔ 2026-08-13：缓存 key 加生成逻辑版本号——此前脑图生成算法多次升级
    # （章节覆盖→部分分层→卷结构重建），但 key 只有 fileId+hash，旧缓存永久命中，
    # 用户重新生成看到的永远是旧结果（"强刷还是不行"根因）。
    # v3：归属过滤修复（level 0 分节挂载、level>=2 列表噪音剔除）
    # v4：extract_toc 漏掉的章节标题补充（三体II 序章/上部/中部/下部）→ v3 缓存作废
    # v5：假卷误检修复 + 扁平章节分桶（2026-08-19，《2049》29 章被压成 2 个残片卷）→ v4 缓存作废
    cache_key = f"mindmap-v5:{r.fileId or title}:{content_hash}"

    cached = eng.kb.get_llm_cache(cache_key, "mindmap")
    if cached:
        return {"mindmap": cached, "cached": True}

    start_ts = time.time()
    # ⛔ 2026-08-19：重置进度并开始计时（前端轮询 /progress?file_id= 看进展）
    _report_mindmap_progress(progress_key, phase="structure", total=0, done=0,
                             current="", started_at=time.time(), finished=False)
    result = _do_generate_mindmap(eng, content, title, progress_key)
    latency_ms = int((time.time() - start_ts) * 1000)

    if hasattr(eng, "logger") and eng.logger is not None:
        eng.logger.info("llm.call", extra={
            "task": "mindmap",
            "file_id": r.fileId or title[:32],
            "input_chars": len(content),
            "latency_ms": latency_ms,
            "cached": False,
            "status": "success" if result.get("mindmap") else "error",
        })

    if cache_key and result.get("mindmap"):
        eng.kb.set_llm_cache(cache_key, "mindmap", result["mindmap"])
    return result


def _do_generate_mindmap(eng, content: str, title: str, progress_key: str = ""):
    """脑图生成核心逻辑（长文档分层处理）"""
    import json, time, threading
    import re

    if len(content) <= 8000:
        # ⛔ 2026-08-19：短文档直接生成，进度仅 1 步
        _report_mindmap_progress(progress_key, phase="chapters", total=1, done=0, current=title)
        result = _generate_mindmap_direct(eng, content, title)
        _report_mindmap_progress(progress_key, phase="done", done=1, finished=True)
        return result

    # ⛔ 2026-08-12：章节来源改为"全文真实结构优先"（EPUB 原生目录/标题扫描），
    # 不再只靠 LLM 猜前 12000 字——否则长书脑图只覆盖开头几节（总序/前言），主体全丢。
    chapters_info = _structure_chapters(content)
    if len(chapters_info) < 3:
        chapters_info = _extract_chapters(eng, content[:12000], title)
    if not chapters_info:
        # ⛔ 2026-08-19：回退分支也要收尾进度，否则前端轮询永远停在“分析文档结构”
        _report_mindmap_progress(progress_key, phase="done", finished=True)
        return _generate_mindmap_direct(eng, content[:8000], title)

    # ⛔ 2026-08-12 分层（对标 Text-to-MindMap 层级化抽取式）：
    # 把 TOC 按"部分(level 0) → 章节(level 1)"组织，避免序言/部分标题
    # 挤占章节名额（实测《原则》12 个名额被历程+序言占满，工作原则全丢）。
    parts = _group_chapters_by_part(chapters_info)

    # ⛔ 2026-08-13：合集/书名卷结构（如《三体》五大卷）时，
    # _group_chapters_by_part 识别失败（parts=0）→ 改用正文位置重建卷→章。
    volumes = _structure_volumes_by_position(content) if len(parts) < 2 else []
    use_volumes = len(volumes) >= 2

    tasks = []  # (group_title, ch_title, segment, is_volume_summary)
    if use_volumes:
        for vol in volumes[:MAX_PARTS]:
            # 卷级概括任务（无章节的卷也生成，保证"概括性"）
            tasks.append((vol["title"], None, None, True))
            # 卷内章节任务（每卷限 MAX_CHAPTERS_PER_PART 章，控制量/时间）
            for ch in vol["chapters"][:MAX_CHAPTERS_PER_PART]:
                tasks.append((vol["title"], ch["title"], None, False))
            # 无章节的卷：整卷正文提炼要点（如球状闪电无章标题）
            if not vol["chapters"]:
                tasks.append((vol["title"], "本卷要点", None, False))
    elif len(parts) >= 2:
        for part in parts[:MAX_PARTS]:
            for ch in part["chapters"][:MAX_CHAPTERS_PER_PART]:
                tasks.append((part["title"], ch["title"], None, False))
    if not tasks:
        # 回退：扁平章节（无有效部分/卷结构时）——分桶保证全书覆盖
        # （2026-08-19：>MAX_CHAPTERS 不再只取前 12 章，后半本全丢）
        for group_title, ch_title in _bucket_flat_chapters(chapters_info):
            tasks.append((group_title, ch_title, None, False))

    # 章节边界定位（2026-08-06 修复）：在全文搜索各章节标题出现位置，
    # 按真实边界切片，避免字符均分切断段落/章节错位
    import re as _re
    located = []
    for group_title, ch_title, _seg, is_summary in tasks:
        if ch_title is None or ch_title == "本卷要点":
            # 卷概括 / 无章节卷要点：用该卷正文起点定位（卷标题在正文中的位置）
            idx = content.find(group_title)
            if idx <= 5000:
                # 跳过封面/目录区，取正文中的卷标题位置
                start = idx + 1
                while True:
                    nxt = content.find(group_title, start)
                    if nxt < 0 or nxt > 5000:
                        idx = nxt if nxt > 5000 else idx
                        break
                    start = nxt + 1
        else:
            idx = content.find(ch_title)
        if idx >= 0:
            located.append((idx, group_title, ch_title, is_summary))
    # 若标题定位失败 ≥ 半数，回退到字符均分
    if len(located) < max(1, len(tasks) // 2):
        chunk_size = max(1, len(content) // max(1, len(tasks)))
        located = [
            (i * chunk_size, group_title, ch_title, is_summary)
            for i, (group_title, ch_title, _seg, is_summary) in enumerate(tasks)
        ]

    located.sort(key=lambda x: x[0])

    def _vol_segment(vol_title: str) -> str:
        vol_pos = _body_pos_of_title(content, vol_title)
        if vol_pos < 0:
            return ""
        # 下一卷位置：取所有卷标题正文位置中 > vol_pos 的最小值
        nxt = len(content)
        for other_vol in volumes:
            other_pos = _body_pos_of_title(content, other_vol["title"])
            if other_pos > vol_pos:
                nxt = min(nxt, other_pos)
        vol_text = content[vol_pos:nxt]
        # ⛔ 2026-08-13：无章节的卷（如三体II 的"序章/上部 面壁者"不被 extract_toc
        # 识别）只取开头 8000 字会"不全面"——改为卷内均匀采样 3 段拼接，
        # 保证开头/中段/结尾都有覆盖（与图谱全书采样同一思路）。
        if len(vol_text) <= MAX_CHAPTER_CHARS:
            return vol_text
        parts = []
        seg_size = min(3000, MAX_CHAPTER_CHARS // 3)
        for frac in (0.0, 0.45, 0.85):
            start = int(len(vol_text) * frac)
            parts.append(vol_text[start : start + seg_size])
        return "\n…\n".join(parts)[:MAX_CHAPTER_CHARS]

    resolved_tasks = []
    for i, (pos, group_title, ch_title, is_summary) in enumerate(located):
        if ch_title is None or ch_title == "本卷要点":
            segment = _vol_segment(group_title)
        else:
            start = max(0, pos - (200 if i > 0 else 0))
            end = located[i + 1][0] if i + 1 < len(located) else len(content)
            segment = content[start:min(end, start + 6000)][:6000]
        if segment.strip():
            resolved_tasks.append((group_title, ch_title, segment, is_summary))
    if not tasks:
        # ⛔ 2026-08-19：回退分支也要收尾进度（同上）
        _report_mindmap_progress(progress_key, phase="done", finished=True)
        return _generate_mindmap_direct(eng, content[:8000], title)

    # ⛔ 2026-08-19：任务总数确定后上报（前端显示“第 N/M 章”
    # —— 中长篇首次生成无缓存时 3-5 分钟起步，必须有可见进展）
    _report_mindmap_progress(progress_key, phase="chapters", total=len(resolved_tasks), done=0, current="")

    sem = threading.Semaphore(6)
    results = [None] * len(resolved_tasks)

    def process_chapter(idx, group_title, ch_title, segment, is_summary):
        with sem:
            try:
                if is_summary:
                    summary = _summarize_volume(eng, group_title, segment)
                    results[idx] = (group_title, None, summary, True)
                else:
                    result = _process_chapter(
                        eng, f"{group_title}：{ch_title}" if group_title else ch_title, segment
                    )
                    results[idx] = (group_title, ch_title, result, False)
            finally:
                # ⛔ 2026-08-19：无论成功失败都推进进度（失败返回 [] 也计 1 步）
                if progress_key:
                    with _MINDMAP_PROGRESS_LOCK:
                        p = _MINDMAP_PROGRESS.get(progress_key)
                        if p:
                            p["done"] = min(p.get("done", 0) + 1, p.get("total", 1))
                            p["current"] = (ch_title or group_title or "")[:40]

    threads = [
        threading.Thread(target=process_chapter, args=(i, g, c, s, sm))
        for i, (g, c, s, sm) in enumerate(resolved_tasks)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ⛔ 2026-08-19：全部章节处理完成，标记 finished（前端停止轮询）
    _report_mindmap_progress(progress_key, phase="done", current="", finished=True)

    result_children = []
    seen_topics = set()
    # ⛔ 分组聚合：root → 卷/部分 → 章节 → 要点
    part_groups: dict = {}
    flat_children = []
    volume_summaries: dict = {}
    for group_title, ch_title, payload, is_summary in results:
        if is_summary and group_title:
            volume_summaries[group_title] = payload
        elif group_title:
            part_groups.setdefault(group_title, []).append((ch_title, payload))
        else:
            flat_children.append((ch_title, payload))

    for part_title, chapters in part_groups.items():
        part_nodes = []
        # 卷级主线概括放最前（解决"无概括性"）
        summary = volume_summaries.get(part_title, "")
        if summary:
            part_nodes.append({"topic": f"📌 主线：{summary}"})
        for ch_title, sub_children in chapters:
            # ⛔ 跨章节相似要点去重（2026-08-12，对标 mindmap-generator 模糊/语义去重）
            deduped = []
            for child in (sub_children or []):
                topic = (child.get("topic") or "").strip()
                norm_key = re.sub(r"[\s，。、；：！？,.!?;:'\"“”‘’\-—()（）]+", "", topic).lower()
                if not norm_key or norm_key in seen_topics:
                    continue
                seen_topics.add(norm_key)
                deduped.append(child)
            if deduped:
                part_nodes.append({"topic": ch_title, "children": deduped})
            else:
                part_nodes.append({"topic": ch_title, "children": [{"topic": "详见原文"}]})
        if part_nodes:
            result_children.append({"topic": part_title, "children": part_nodes})

    for ch_title, sub_children in flat_children:
        if sub_children:
            deduped = []
            for child in sub_children:
                topic = (child.get("topic") or "").strip()
                norm_key = re.sub(r"[\s，。、；：！？,.!?;:'\"“”‘’\-—()（）]+", "", topic).lower()
                if not norm_key or norm_key in seen_topics:
                    continue
                seen_topics.add(norm_key)
                deduped.append(child)
            if deduped:
                result_children.append({"topic": ch_title, "children": deduped})
            else:
                result_children.append({"topic": ch_title, "children": [{"topic": "详见原文"}]})
        else:
            result_children.append({"topic": ch_title, "children": [{"topic": "详见原文"}]})

    return {"mindmap": {"topic": title, "children": result_children}}


def _structure_chapters(content: str) -> list[dict]:
    """从全文真实结构提取章节（TOC 区域 + 行内标题），返回 [{title, points:[]}, ...]。
    优先用 toc_extractor（EPUB 原生目录/标题扫描），避免 LLM 转述标题与原文不匹配。
    2026-08-12：全量收集（不再按 MAX_CHAPTERS 截断）——截断会让后面的
    "第三部分 工作原则"整体丢失（《原则》实测 12 名额被历程+序言占满）。
    数量控制交给 _group_chapters_by_part + MAX_CHAPTERS_PER_PART。
    """
    import re
    from app.rag_app.toc_extractor import extract_toc
    items = extract_toc(content)
    seen = set()
    chapters = []
    for it in items:
        text = (it.get("text") or "").strip()
        if not text or len(text) < 2 or len(text) > 80:
            continue
        if text in seen:
            continue
        seen.add(text)
        chapters.append({"title": text, "points": [], "level": int(it.get("level", 0) or 0)})
        if len(chapters) >= 200:  # 防御性上限，正常书籍远达不到
            break
    return chapters


def _group_chapters_by_part(chapters: list[dict]) -> list[dict]:
    """把扁平 TOC 按"部分(level 0) → 章节(level 1)"组织。
    过滤序言/导言/概要与列表/附录等非正文结构，避免挤占章节名额。
    对标 Text-to-MindMap：长文档脑图 = 章节标题 → 逐章从原文抽取要点。
    """
    import re

    NOISE_KEYWORDS = (
        "序", "导言", "引言", "前言", "概要与列表", "总结", "附录",
        "参考文献", "致谢", "后记", "目录", "审校", "版权",
    )

    def _is_part(text: str) -> bool:
        if not text:
            return False
        if any(k in text for k in NOISE_KEYWORDS):
            return False
        # 部分标题特征：以"第X部分"开头且**带后缀标题**（如"第三部分 工作原则"）。
        # 裸"第三部分"是 PDF 页眉/正文残留，不是结构标题 → 拒绝。
        if re.match(r"^第[一二三四五六七八九十]+部分[^$]", text):
            return True
        return False

    parts = []
    current_part = None
    for ch in chapters:
        text = ch.get("title", "").strip()
        level = int(ch.get("level", 0) or 0)
        if level == 0 and _is_part(text):
            current_part = {"title": text, "chapters": []}
            parts.append(current_part)
            continue
        # ⛔ 挂载规则：
        # - level >= 1 的真实章节 → 挂到当前部分
        # - level 0 的"分节标题"（如"打造良好的文化……""用对人……"）→ 也挂到当前部分
        #   （它们是"第三部分 工作原则"下的分组，不是新顶级部分）
        # - level 0 噪音（参考文献/致谢/概要与列表等）→ 跳过
        # - 裸"第X部分"（PDF 页眉残留）→ 跳过
        is_bare_part = bool(re.fullmatch(r"第[一二三四五六七八九十]+部分", text))
        is_noise = any(k in text for k in NOISE_KEYWORDS)
        if current_part is not None and not is_bare_part and not is_noise and level >= 0:
            if len(text) >= 4 or level >= 1:
                current_part["chapters"].append({"title": text})
    # 丢弃无有效章节的部分，及纯噪音部分
    return [p for p in parts if len(p["chapters"]) >= 1]


def _is_valid_volume_title(text: str) -> bool:
    """卷标题质量门禁（2026-08-19，通用）。

    背景：_find_volume_title_lines 的行扫描会把任意"后跟 ≥3 个章节标题的独立短行"
    当成卷（《2049》实测 '2024年12月/要。/果。'），把普通书压成残片卷。
    拒绝：句末标点结尾、纯日期行、去标点后 <2 字、纯数字/符号。
    保留：真实卷名（三体I/球状闪电/超新星纪元/三体II·黑暗森林）。
    """
    import re
    t = (text or "").strip()
    if not t or len(t) < 2 or len(t) > 20:
        return False
    if t[-1] in "。，、；：！？…":
        return False
    if re.fullmatch(r"\d{2,4}年\d{1,2}月(?:\d{1,2}日)?", t):
        return False
    # 图/表/公式注记残片（"图1""表2""公式3"）
    if re.fullmatch(r"(?:图|表|公式)\s*\d+", t):
        return False
    # 词内功能字 → 正文残片（"的文化/要的"），与概念残片门禁同一思路
    if any(ch in t for ch in "的着了是在和与及或但而且并也都就还这那们个种等若如之其"):
        return False
    stripped = re.sub(r"[·—\-_()（）「」『』《》\"'“”‘’、，。；：！？…\s]+", "", t)
    if len(stripped) < 2:
        return False
    # 2 字且无卷/部/篇/集/章标记 → 人名/残片（"吴晨"）
    if len(stripped) < 3 and not re.search(r"[卷部篇集章]", t):
        return False
    if re.fullmatch(r"[0-9a-zA-Z.\-]+", stripped):
        return False
    return True


def _bucket_flat_chapters(chapters: list[dict]) -> list[tuple]:
    """无部分/卷结构时按连续章节分桶（2026-08-19，通用）。

    >MAX_CHAPTERS 的扁平章节不再只取前 12 章（后半本全丢，经验：
    《2049》29 章只有前 12 章可进脑图），改为 ≤MAX_PARTS 组、
    每组 ≤MAX_CHAPTERS_PER_PART 章，保证全书覆盖且量受控。
    桶标题取组内首章名 + "等N章"（零 LLM 成本）。
    返回 [(group_title|None, ch_title), ...]。
    """
    import math
    if len(chapters) <= MAX_CHAPTERS:
        return [(None, ch.get("title", "")) for ch in chapters]
    n_buckets = min(MAX_PARTS, math.ceil(len(chapters) / MAX_CHAPTERS_PER_PART))
    bucket_size = math.ceil(len(chapters) / n_buckets)
    tasks = []
    for b in range(n_buckets):
        group = chapters[b * bucket_size:(b + 1) * bucket_size]
        group_title = group[0].get("title", "")
        if len(group) > 1:
            group_title = f"{group_title} 等{len(group)}章"
        for ch in group:
            tasks.append((group_title, ch.get("title", "")))
    return tasks


def _structure_volumes_by_position(content: str) -> list[dict]:
    """按正文位置重建"卷 → 章"层级（2026-08-13）。

    背景：合集 EPUB（如《三体》：球状闪电 / 三体I / 三体II·黑暗森林 /
    三体III·死神永生 / 超新星纪元）的 extract_toc 层级归组错乱——
    36 个章节全归到最后一个卷下，且卷标题不是"第X部分"，
    _group_chapters_by_part 无法识别 → 脑图只取前 12 章、4/5 卷丢失。

    方案：不信任 extract_toc 的 level 归组，改用【标题在正文中的位置】
    重建归属：卷标题按正文位置排序；"第X部/第X部分"视为分节（挂到最近卷，
    不当作顶级卷——真正以"第X部分"为主结构的文档会走 _group_chapters_by_part）；
    章节（level>=1 或 分节）挂到位置之前最近的卷。
    """
    import re
    from app.rag_app.toc_extractor import extract_toc

    items = extract_toc(content)
    NOISE = (
        "序", "导言", "引言", "前言", "概要与列表", "总结", "附录",
        "参考文献", "致谢", "后记", "目录", "审校", "版权", "全集",
    )

    # 1) 卷候选：level 0 + 非噪音 + 正文中可定位
    # ⛔ 位置选择必须逐个进行并记录已选位置（合集页会把卷名挤在一起，
    # 如三体I@190037 / 三体II@190045 / 三体III@190059，其中只有第一个是真正的卷首）。
    seen_vol = set()
    vol_candidates = []
    chosen_positions: list[int] = []
    for it in items:
        text = (it.get("text") or "").strip()
        level = int(it.get("level", 0) or 0)
        if level != 0 or not text or len(text) < 2 or len(text) > 60:
            continue
        if any(k in text for k in NOISE):
            continue
        # ⛔ 2026-08-19：卷标题质量门禁（拒绝"2024年12月/要。/果。"等残片）
        if not _is_valid_volume_title(text):
            continue
        # ⛔ "第X部/第X部分"在 volumes 路径中视为分节，不当顶级卷
        if re.match(r"^第[一二三四五六七八九十百千]+部", text):
            continue
        if text in seen_vol:
            continue
        seen_vol.add(text)
        pos = _select_volume_pos(content, text, chosen_positions)
        if pos >= 0:
            chosen_positions.append(pos)
            vol_candidates.append({"title": text, "pos": pos, "chapters": []})

    # 2) 补充 extract_toc 漏掉的卷标题（如"超新星纪元"在正文为独立短行，
    #    extract_toc 未列入 level 0）：独立行 + 其后 2 万字符内有 ≥3 个章节标题
    vol_titles_known = {v["title"] for v in vol_candidates}
    for line_title, line_pos in _find_volume_title_lines(content):
        if line_title in vol_titles_known or line_title in seen_vol:
            continue
        # ⛔ 2026-08-19：行扫描候选同样过卷标题质量门禁
        if not _is_valid_volume_title(line_title):
            continue
        if any(abs(line_pos - v["pos"]) < 5000 for v in vol_candidates):
            continue  # 与已知卷位置过近 → 不是独立卷（合集页/分节）
        following = 0
        for it in items:
            if int(it.get("level", 0) or 0) < 1:
                continue
            ch_pos = _find_title_with_whitespace_variants(content, it.get("text", ""))
            if ch_pos >= line_pos and ch_pos - line_pos < 20000:
                following += 1
        if following >= 3:
            vol_candidates.append({"title": line_title, "pos": line_pos, "chapters": []})

    # 3) 按正文位置排序 → 卷列表
    vol_candidates.sort(key=lambda v: v["pos"])
    volumes = vol_candidates

    # 4) 章节归属：level>=1 标题 或 非卷的 level 0 分节（如三体III 下的
    #    "第一部/第二部…"）→ 挂到位置之前最近的卷；
    #    正文标题带空格变体（如"第一章 死 星 终 结"）用空白容错定位。
    seen_ch = set()
    vol_titles = {v["title"] for v in volumes}
    for it in items:
        text = (it.get("text") or "").strip()
        level = int(it.get("level", 0) or 0)
        if not text or len(text) < 2 or len(text) > 80:
            continue
        if text in vol_titles:
            continue  # 已是卷，不再作为章节
        # ⛔ 2026-08-13 修复：level 0 分节（如三体III 的"第一部/第二部…"）
        # 必须挂到最近卷；level >= 2 是正文深层标题/列表项（"一、对太阳系…"），
        # 属于噪音，不再当章节——这是"结构混乱"的另一来源。
        if level >= 2:
            continue
        if level == 0:
            # level 0 分节候选：非噪音；噪音（序/附录/参考文献等）不挂
            if any(k in text for k in NOISE):
                continue
        if level < 0 or level > 1:
            continue
        if _find_title_with_whitespace_variants(content, text) < 0:
            continue
        if text in seen_ch:
            continue
        seen_ch.add(text)
        pos = _find_title_with_whitespace_variants(content, text)
        if pos < 0:
            continue
        owner = None
        for v in volumes:
            if v["pos"] <= pos:
                owner = v
            else:
                break
        if owner is not None:
            owner["chapters"].append({"title": text, "pos": pos})

    # 5) 每卷章节按位置排序 + 标题去重
    for v in volumes:
        v["chapters"].sort(key=lambda c: c["pos"])
        dedup = []
        t_seen = set()
        for c in v["chapters"]:
            if c["title"] not in t_seen:
                t_seen.add(c["title"])
                dedup.append(c)
        v["chapters"] = dedup

    # 6) ⛔ 2026-08-13：extract_toc 漏掉的章节标题补充（如三体II 的
    #    "序章 / 上部 面壁者 / 中部 咒语 / 下部 黑暗森林"）。
    #    在章节为空的卷区间内扫描"章/部/序"模式的独立短行，避免整卷只有"本卷要点"。
    _SECTION_RE = None
    for v in volumes:
        if v["chapters"]:
            continue
        vol_end = len(content)
        for other in volumes:
            if other["pos"] > v["pos"]:
                vol_end = min(vol_end, other["pos"])
        vol_text = content[v["pos"] : vol_end]
        section_lines = []
        for line in vol_text.split("\n"):
            line = line.strip()
            if not line or len(line) > 40:
                continue
            # 模式：序章 / 上部/中部/下部 + 名称 / 第X章 / 尾声 / 后记
            if not re.match(
                r"^(序\s*章|上\s*部|中\s*部|下\s*部|第[一二三四五六七八九十百千0-9]+\s*[章部]|尾声|后记|楔子|引子)\s*",
                line,
            ):
                continue
            section_lines.append({"title": line, "pos": content.find(line, v["pos"])})
        if section_lines:
            # 去重 + 排序，最多 12 个
            seen = set()
            uniq = []
            for s in section_lines:
                # ⛔ 全角/半角空格、间隔号归一后去重（"序 章" vs "序　章"）
                key = s["title"].replace(" ", "").replace("\u3000", "")
                if key not in seen and s["pos"] >= 0:
                    seen.add(key)
                    uniq.append(s)
            uniq.sort(key=lambda s: s["pos"])
            v["chapters"] = uniq[:12]
    return volumes


def _body_pos_of_title(content: str, title: str) -> int:
    """找标题在正文中的位置：跳过封面/目录区（首次出现位置 > 5000 字符）。
    返回 -1 表示正文中未定位到。
    """
    if not title:
        return -1
    start = 0
    while True:
        idx = content.find(title, start)
        if idx < 0:
            return -1
        if idx > 5000:
            return idx
        start = idx + 1


def _find_title_with_whitespace_variants(content: str, title: str) -> int:
    """按标题定位正文位置，容忍标题内的空白变体（如正文"第一章 死 星 终 结"
    vs extract_toc 的"第一章 死星终结"）。返回 > 5000 的第一个正文位置。
    宽松模式：标题逐字符间允许任意空白（先精确尝试，失败再宽松）。
    """
    import re

    if not title:
        return -1
    # 精确匹配（标题无空格或空格与正文一致）
    pattern = re.escape(title).replace(r"\ ", r"\s*")
    for m in re.finditer(pattern, content):
        if m.start() > 5000:
            return m.start()
    # 宽松匹配：每个字符间允许空白（对付"死 星 终 结"式排版）。
    # ⛔ 2026-08-13 性能保护：仅对短标题启用——长标题逐字正则 O(n*m) 极慢
    # （80 字标题在 50 万字文档上 3.5s+，8 文档批量识别直接超时），
    # 且长标题多为正文句尾噪音（非真章节标题），宽松匹配收益为零。
    if len(title) > 30:
        return -1
    loose = r"\s*".join(re.escape(ch) for ch in title)
    for m in re.finditer(loose, content):
        if m.start() > 5000:
            return m.start()
    return -1


def _find_volume_title_lines(content: str, max_count: int = 12) -> list[tuple[str, int]]:
    """扫描正文独立短行，补充 extract_toc 漏掉的卷标题（如"超新星纪元"）。
    特征：独立成行、2-12 字、非噪音、非"第X部/章/节"、非列表项。
    """
    import re

    NOISE_SUB = (
        "序", "导言", "引言", "前言", "概要与列表", "总结", "附录",
        "参考文献", "致谢", "后记", "目录", "审校", "版权", "书名",
        "作者", "封面", "授权", "内容简介",
    )
    candidates = []
    seen = set()
    for line in content.split("\n"):
        text = line.strip()
        if not text or len(text) < 2 or len(text) > 12:
            continue
        if re.match(r"^第[一二三四五六七八九十百千]+[章部节]", text):
            continue
        if re.match(r"^[一二三四五六七八九十]+、", text):
            continue
        if re.match(r"^[0-9]+[.．、\s]", text):
            continue
        if any(k in text for k in NOISE_SUB):
            continue
        if text in seen:
            continue
        pos = _find_title_with_whitespace_variants(content, text)
        if pos < 0:
            continue
        seen.add(text)
        candidates.append((text, pos))
        if len(candidates) >= max_count:
            break
    return candidates


def _select_volume_pos(content: str, title: str, chosen_positions: list[int]) -> int:
    """为卷标题选择"正文卷首"位置（2026-08-13）。

    合集 EPUB 的封面/目录页会把多个卷名紧挨着列一遍（如《三体》里
    "三体I @190037 / 三体II @190045 / 三体III @190059"），这些是合集页
    引用而非卷正文起点。规则：
    - 遍历标题所有出现位置；
    - 剔除与【已选其他卷位置】间隔 < 5000 的出现（合集页重复）；
    - 剩余中取第一个正文位置（> 5000 字符，跳过文件头目录区）。
    """
    positions = []
    start = 0
    while True:
        idx = content.find(title, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + 1
    for pos in positions:
        if pos <= 5000:
            continue
        if any(abs(pos - cp) < 5000 for cp in chosen_positions):
            continue
        return pos
    return -1


def _summarize_volume(eng, vol_title: str, segment: str) -> str:
    """生成卷级主线概括（20-40 字），解决"脑图无概括性"（2026-08-13）。"""
    try:
        from app.rag_app.llm_client_factory import token_budget
        p = (
            f"请用一句话概括《{vol_title}》这部分的主线内容（20-40字），"
            f"突出核心情节/论点，不要评价。只返回概括本身。\n\n{segment[:3000]}"
        )
        resp = eng.llm_client.chat.completions.create(
            model=eng.model_name, messages=[{"role": "user", "content": p}],
            temperature=0.2, max_tokens=token_budget(120), timeout=30,
        )
        summary = (resp.choices[0].message.content or "").strip().split("\n")[0]
        return summary[:60] if summary else ""
    except Exception:
        return ""


def _generate_mindmap_direct(eng, content: str, title: str):
    p = (
        "你是专业内容分析师。请把以下文档整理成**可读、可折叠、重点明确**的层级脑图。\n\n"
        "## 结构目标\n根节点(1) -> 二级主题(4-9个) -> 三级要点(每个主题2-5个) -> 必要细节(仅在有证据时展开到第四层)\n\n"
        "## 正确示范\n{\"topic\":\"机器学习三大范式与核心算法\",\"children\":[{\"topic\":\"监督学习：基于标注数据学习输入到输出的映射关系\",\"children\":[{\"topic\":\"线性回归：通过最小化均方误差拟合连续值预测函数\",\"children\":[{\"topic\":\"梯度下降法：沿负梯度方向迭代更新参数使损失函数收敛\"}]}]}]}\n\n"
        "## 要求\n1. 每个节点8-32字，优先使用原文中有依据的信息点\n2. 二级主题不要超过9个\n3. 中文输出，JSON必须合法，只返回JSON对象\n\n"
        f"文档标题：{title}\n内容：{content[:8000]}"
    )
    try:
        from app.rag_app.llm_client_factory import token_budget
        resp = eng.llm_client.chat.completions.create(
            model=eng.model_name, messages=[{"role": "user", "content": p}],
            temperature=0.3, max_tokens=token_budget(8000), timeout=180
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = _parse_llm_json(raw)
        if parsed and "topic" in parsed:
            # ⛔ 直接生成路径同样过 reality check（2026-08-12）
            children = parsed.get("children", [])
            if children:
                parsed["children"] = _reality_check_children(children, content)
            return {"mindmap": parsed}
        return {"mindmap": {"topic": title, "children": [{"topic": "解析失败：LLM 返回格式异常"}]}}
    except Exception as e:
        return {"mindmap": {"topic": title, "children": [{"topic": f"生成失败: {str(e)[:200]}"}]}}


def _extract_chapters(eng, content: str, title: str):
    p1 = (
        "请对以下文档进行深度结构分析，输出每个章节/部分的标题及其3-5个核心要点。\n"
        "格式：每行一个条目，\"章节标题 | 要点1；要点2；要点3\"\n最多25个章节。\n\n"
        + content[:12000]
    )
    chapters_info = []
    seen = set()
    try:
        from app.rag_app.llm_client_factory import token_budget
        resp = eng.llm_client.chat.completions.create(
            model=eng.model_name, messages=[{"role": "user", "content": p1}],
            temperature=0.2, max_tokens=token_budget(800), timeout=60
        )
        for line in (resp.choices[0].message.content or "").split("\n"):
            parts = line.split("|", 1)
            if len(parts) == 2:
                ch_title = parts[0].strip().lstrip(" -•1234567890.#")
                points = [p.strip() for p in parts[1].split("；") if p.strip()]
                if ch_title and len(ch_title) >= 2 and ch_title not in seen:
                    seen.add(ch_title)
                    chapters_info.append({"title": ch_title, "points": points[:5]})
    except Exception:
        pass
    return chapters_info


def _process_chapter(eng, ch_title: str, segment: str):
    import re
    p_ch = (
        f"深度拆解此章节内容，提取4-8个具体要点（每个15-30字）。每个要点可有1-2个细节子点。\n"
        f"⚠️ 每个要点必须直接来自原文，禁止编造原文没有的内容。\n"
        f"JSON：{{\"children\":[{{\"topic\":\"要点\",\"children\":[{{\"topic\":\"细节\"}}]}}]}}\n\n"
        f"章节：{ch_title}\n{segment[:5000]}"
    )
    try:
        from app.rag_app.llm_client_factory import token_budget
        resp = eng.llm_client.chat.completions.create(
            model=eng.model_name, messages=[{"role": "user", "content": p_ch}],
            temperature=0.2, max_tokens=token_budget(600), timeout=30
        )
        raw = resp.choices[0].message.content or "{}"
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group(0))
            children = data.get("children", [])
            # ⛔ reality check（2026-08-12，对标 mindmap-generator）：
            # 要点必须能在原文中找到依据（双字词/三元组命中原文即视为有据），
            # 无依据的要点删除，避免"自由发挥"污染脑图。
            return _reality_check_children(children, segment)
    except Exception:
        pass
    return []


def _reality_check_children(children: list, segment: str) -> list:
    """对标 mindmap-generator 的 reality check：逐条核对要点是否有原文依据。
    判定：取要点中的 2-4 字词（中文双字词 + 英文三元组），任一词在原文出现即通过；
    全部未命中 → 视为无依据，剔除。防止 LLM 在长章节里"自由发挥"。
    """
    import re

    seg_norm = re.sub(r"\s+", "", segment or "")
    kept = []
    for ch in children:
        topic = (ch.get("topic") or "").strip()
        if not topic:
            continue
        # 提取证据词：连续中文（切成 2 字滑动窗口）+ 英文/数字 3+ 字符
        cjk_parts = re.findall(r"[\u4e00-\u9fff]{2,}", topic)
        evidence = []
        for part in cjk_parts:
            if len(part) <= 4:
                evidence.append(part)
            else:
                for i in range(len(part) - 1):
                    evidence.append(part[i : i + 2])
        evidence += re.findall(r"[A-Za-z0-9]{3,}", topic)
        if not evidence:
            kept.append(ch)
            continue
        hit = any(word in seg_norm for word in evidence)
        if hit:
            kept.append(ch)
    return kept


# ============ Entity Extraction ============

def _summarize_for_graph(content: str, eng, *, focus: str = "knowledge") -> str:
    MAX_INPUT = 12000

    def _summarize_block(text: str) -> str:
        prompt_intro = (
            "你正在为“知识图谱构建”准备原料。请把下面这段文字压缩成结构化摘要，"
            "只保留对构建人物/概念/事件关系图谱有用的信息。\n\n"
            "输出格式：\n## 人物/主体\n## 核心事件\n## 关键概念/术语\n## 重要关系\n"
        ) if focus == "knowledge" else (
            "你正在为“人物关系图谱构建”准备原料。请把下面这段小说压缩成结构化摘要，"
            "只保留人物、事件、人物关系。\n\n"
            "输出格式：\n## 人物\n## 核心事件\n## 人物关系\n"
        )
        p = prompt_intro + f"\n## 文本（{len(text)}字）\n{text[:12000]}"
        try:
            from app.rag_app.llm_client_factory import token_budget
            resp = eng.llm_client.chat.completions.create(
                model=eng.model_name, messages=[{"role": "user", "content": p}],
                temperature=0.2, max_tokens=token_budget(800), timeout=50,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return ""

    blocks = []
    if len(content) <= 12000:
        blocks = [content]
    else:
        step = 12000
        for i in range(0, len(content), step):
            blocks.append(content[i:i + step])
            if len(blocks) >= 6:
                break

    partial_summaries = []
    for idx, blk in enumerate(blocks):
        s = _summarize_block(blk)
        if s:
            partial_summaries.append(s)

    return "\n\n".join(partial_summaries)


@router.post("/api/gemini/extract-entities")
def extract_entities(r: ExtractReq):
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()
    summary = _summarize_for_graph(r.content, eng, focus=r.doc_type)
    return {"nodes": [], "edges": []}


@router.post("/api/gemini/compare")
def compare(r: CompareReq):
    """多文档交叉对比（实现 2026-08-07）：原为返回 not implemented 的 stub，
    前端读 data.comparison 永远为 undefined→点“开始对比”看似无反应。
    现：前端 content 为空（懒加载未打开）时后端按 id 从知识库取正文，LLM 输出 Markdown 对比报告。"""
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()
    kb = eng.kb

    PER_DOC_CHARS = 6000  # 每篇取样上限，控制总 prompt 体积
    docs = []
    for d in r.documents:
        if not isinstance(d, dict):
            continue
        title = str(d.get("title") or d.get("id") or "未命名文档")
        content = str(d.get("content") or "").strip()
        # 前端正文懒加载，未打开过的文档 content 为空 → 按 id 从 KB 取
        if not content:
            fid = str(d.get("id") or "")
            content = (getattr(kb, "_file_preprocessed", {}) or {}).get(fid) or (getattr(kb, "_file_text_cache", {}) or {}).get(fid) or ""
            if not content and fid:
                # overlap 去重重建（2026-08-07）：旧 "\n\n".join 会把分块重叠段重复送进 prompt
                content = kb.rebuild_full_text(fid)
        if not content.strip():
            continue
        docs.append({"title": title, "content": content[:PER_DOC_CHARS]})

    if len(docs) < 2:
        raise HTTPException(400, "有效内容的文档不足 2 篇，无法对比（请确认文档已入库且有正文）")

    doc_blocks = "\n\n".join(
        f"【文档{i + 1}】{item['title']}\n{item['content']}" for i, item in enumerate(docs)
    )
    system_prompt = (
        "你是一个严谨的文档交叉对比分析师，使用中文回答。"
        "请对多篇文档做内容级交叉对比，输出 Markdown 格式报告。"
        "排版硬性要求（前端渲染器只认标准 Markdown 结构）：\n"
        "1. 内容必须用项目符号列表（- ）、表格或分段落组织，禁止把多个对比维度用竖线 | 分隔堆在同一段里；\n"
        "2. 每个对比维度独立成一行（如 '- **维度名**：内容'）或表格的一行；\n"
        "3. 关键词用 **加粗** 强调；不同小节、段落之间留空行。"
    )
    user_prompt = (
        f"请对比以下 {len(docs)} 篇文档，输出 Markdown 报告，包含：\n"
        "## 共同主题\n用项目符号列出各文档共同覆盖的主题/概念\n"
        "## 各自侧重\n每篇文档独有的内容与视角（逐篇用加粗文档名引出，列表展开）\n"
        "## 关键差异与互补\n用表格呈现（列：维度 | 差异 | 互补），表格之外可用列表补充说明\n"
        "## 一句话总结\n\n"
        f"{doc_blocks}"
    )

    try:
        response = eng.llm_client.chat.completions.create(
            model=eng.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=3000,
            timeout=120,
        )
        comparison = (response.choices[0].message.content or "").strip()
        if not comparison:
            raise ValueError("LLM 返回空内容")
        return {"comparison": comparison, "doc_count": len(docs)}
    except HTTPException:
        raise
    except Exception as e:
        # 降级：LLM 失败时返回结构化错误，前端会展示而非无反应
        raise HTTPException(502, f"对比生成失败：{e}")

@router.post("/api/gemini/define")
def define(r: DefineReq):
    from app.rag_app.shared_engine import get_engine
    return {"definition": _do_define(get_engine(), r.term, r.context)}


@router.post("/api/ai/define-term")
def ai_define_term(r: DefineReq):
    """T42：App.tsx 查词调用点（原 404）。"""
    from app.rag_app.shared_engine import get_engine
    return {"definition": _do_define(get_engine(), r.term, r.context)}

@router.post("/api/gemini/card")
def card(r: CardReq):
    from app.rag_app.shared_engine import get_engine
    return {"card": _do_generate_cards(get_engine(), r.title, r.content, r.docId)}


@router.post("/api/ai/generate-cards")
def ai_generate_cards(r: CardReq):
    """T42：KnowledgeCards handleTriggerAICardGeneration 调用点（原 404）。"""
    from app.rag_app.shared_engine import get_engine
    return {"cards": _do_generate_cards(get_engine(), r.title, r.content, r.docId)}
