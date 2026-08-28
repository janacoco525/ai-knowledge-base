"""
问答操作路由（chat_ops）
职责：智能问答 chat / SSE 流式 chat_stream / 联网搜索(ddgs) / 多轮改写 / follow-ups
2026-08-13 从 llm_ops.py 拆分（文件规模治理）：问答相关代码独立成模块，llm_ops 专注生成类能力。
"""
from __future__ import annotations
import json
import re
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.rag_app.llm_client_factory import token_budget

router = APIRouter(tags=["ChatOps"])


def _stream_refs_clean(text: str, max_local: int, max_web: int):
    """流式引用校验：剔除越界 [参考资料N]/[网页N]，保留可能被截断的尾部 token。
    返回 (可输出的清洗文本, 待拼接尾部)。"""
    from app.rag_app.rag_engine import _strip_invalid_refs
    cleaned = _strip_invalid_refs(text, max_local, max_web)
    tail = ""
    m = re.search(r"\[(参考资料|网页)(\d*)$", cleaned)
    if m and "]" not in m.group(0):
        tail = m.group(0)
        cleaned = cleaned[: -len(tail)]
    return cleaned, tail


# ============ Web Search（免费，无 API Key） ============

_WEB_SPAM_HINTS = (
    "porn", "xxx", "adult", "sex", "nude", "dating", "casino", "bet",
    "成人", "色情", "博彩", "赌博",
    # ⛔ 2026-08-19：免费搜索质量波动大，补充强特征垃圾词（标题/域名层命中才过滤，避免误伤正文）
    "在线观看", "伦理片", "无码", "高清资源",
)

# 低质/垃圾域名黑名单（免费搜索引擎偶发召回，直接丢弃）
_WEB_SPAM_DOMAINS = (
    "kanliao.org", "e-ham.ru",
)


def _refine_web_query(q: str) -> str:
    """把自然语言问题提炼为搜索引擎友好的关键词（2026-08-19）。
    免费搜索引擎对 30 字长句召回不稳定（实测同查询时而百度百科时而无关站点）；
    《书名》类问题提炼为“书名+限定词”（如《原则》…作者是谁 → 原则 作者）。
    """
    m = re.search(r"《([^》]{1,30})》", q or "")
    if m:
        name = m.group(1).strip()
        for kw in ("作者", "书", "简介", "内容", "谁"):
            if kw in q:
                return f"{name} {kw}"
        return f"{name} 书"
    return (q or "").strip()[:30]


def _ddgs_search(query: str, max_results: int = 5) -> list[dict]:
    """免费联网搜索（DuckDuckGo ddgs 多后端轮询，免 API Key）。

    失败/限流/无结果一律返回 []，由调用方降级为本地回答，不阻塞主链路。
    2026-08-14：过滤垃圾结果（空白页/摘要过短/成人或博彩类域名词），
    避免“西琴”类短查询召回无关成人链接污染回答。
    ⛔ 2026-08-19：ddgs 9.x 默认 backend="auto" 只优先 Brave（search.brave.com），
    本机网络访问超时 → 全部结果静默丢失（用户反馈：搜《原则》作者返回“没找到”）。
    改为显式轮询 ddg（DuckDuckGo 原生，实测可用）→ bing → brave，任一成功即返回；
    全部失败记 warning（不再无痕降级，便于后续排查）。
    返回项：{"title", "uri", "body"}。
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        from ddgs import DDGS
    except Exception:
        logger.warning("ddgs import failed, web search disabled")
        return []

    out: list[dict] = []
    seen_uris: set[str] = set()
    # 多查询：提炼词优先（长句召回不稳定），无关/失败时回退原句
    search_queries = [_refine_web_query(query)]
    if search_queries[0] != query:
        search_queries.append(query)
    book_name = None
    _m = re.search(r"《([^》]{1,30})》", query or "")
    if _m:
        book_name = _m.group(1).strip()

    def _collect_from(qq: str) -> None:
        """单查询 × 多后端收集（ddg → bing → brave，首个有结果的后端即返回）"""
        for backend in ("ddg", "bing", "brave"):
            if len(out) >= max_results:
                return
            try:
                with DDGS(timeout=10) as ddgs:
                    for r in ddgs.text(qq, max_results=max_results, backend=backend):
                        href = (r.get("href") or "").strip()
                        title = (r.get("title") or "").strip()
                        body = (r.get("body") or "").strip()
                        if href and title and href not in seen_uris:
                            seen_uris.add(href)
                            # 低质域名直接丢弃
                            if any(d in href.lower() for d in _WEB_SPAM_DOMAINS):
                                continue
                            # 过滤空白页/摘要过短（用户曾反馈召回“空白页面”）
                            if not body or len(body) < 20:
                                continue
                            # 过滤成人/博彩类垃圾（用户曾反馈召回“无关成人内容链接”）
                            low = f"{title} {href} {body}".lower()
                            if any(h in low for h in _WEB_SPAM_HINTS):
                                continue
                            out.append({
                                "title": title[:200],
                                "uri": href[:500],
                                "body": body[:500],
                            })
                if out:
                    return  # 首个有结果的后端即返回，避免多后端结果混杂
            except Exception as e:
                logger.warning("DDGS backend=%s failed: %s", backend, e)

    for qq in search_queries:
        _collect_from(qq)
        if out:
            break

    # ⛔ 2026-08-19：书名相关性过滤——免费搜索波动大（“原则 作者”时而召回
    # 百度百科、时而召回鸡汤文）；标题/摘要须命中《书名》完整形式才视为相关，
    # 裸词匹配会误放无关结果（“Google 21条原则”也含“原则”）；
    # 提炼词结果不足 2 条时用原句（含完整书名）兜底再搜一轮。
    if book_name and out:
        def _relevant(it: dict) -> bool:
            hay = f"{it['title']} {it['body']}"
            return f"《{book_name}》" in hay

        relevant = [it for it in out if _relevant(it)]
        if len(relevant) < 2 and len(search_queries) > 1:
            out = []
            _collect_from(search_queries[1])
            relevant = [it for it in out if _relevant(it)]
        out = relevant

    if not out:
        logger.warning("DDGS all backends failed/empty, query=%r", query[:60])
    return out[:max_results]


def _prepare_retrieval_queries(messages: list[dict], q: str, eng) -> list[str]:
    """多轮改写 + HyDE 扩展检索查询（一次 LLM 调用；失败/无客户端 → 降级原问题）。

    返回去重后的检索查询列表（上限 3）：[改写查询, 扩展角度] 或 [原问题]。
    """
    try:
        if not getattr(eng, "llm_client", None):
            return [q]
        recent = [
            m for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        ][-6:]
        conv = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}: {str(m.get('content', ''))[:200]}"
            for m in recent
        )
        prompt = (
            "你是检索查询优化器。基于以下对话历史，把用户最后的问题改写为：\n"
            "1) 独立检索查询：补全指代（如\"它/那/第二个\"），成为可在知识库检索的完整问句\n"
            "2) 扩展检索角度：另一个与问题相关的检索短语/角度，帮助提高召回\n"
            "严格输出两行，不要解释：\n改写：<查询>\n扩展：<短语>"
        )
        resp = eng.llm_client.chat.completions.create(
            model=getattr(eng, "model_name", None),
            messages=[{"role": "user", "content": prompt + "\n\n对话历史：\n" + conv}],
            temperature=0.2,
            max_tokens=token_budget(200),
            timeout=30,
        )
        raw = (resp.choices[0].message.content or "").strip()
        rewrite, expand = "", ""
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("改写") and "：" in line:
                rewrite = line.split("：", 1)[1].strip()
            elif line.startswith("扩展") and "：" in line:
                expand = line.split("：", 1)[1].strip()
        rewrite_ok = 3 <= len(rewrite) <= 80 and rewrite != q
        expand_ok = 3 <= len(expand) <= 80
        # 改写成功时直接用 [改写+扩展] 检索，丢弃含指代的原问题（避免噪音候选稀释）
        queries = []
        if rewrite_ok:
            queries.append(rewrite)
        else:
            queries.append(q)
        if expand_ok and expand not in queries:
            queries.append(expand)
        return queries[:3]
    except Exception:
        return [q]


def _build_history_text(messages: list[dict], max_pairs: int = 4) -> str:
    """提取最近几轮对话文本（排除最后一条用户消息=当前问题），
    ⛔ 2026-08-19：供联网合成等无历史 prompt 拼接，让模型能理解指代与上文。"""
    msgs = [
        m for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        and str(m.get("content", "")).strip()
    ]
    if msgs:
        msgs = msgs[:-1]  # 去掉当前问题（最后一条 user）
    recent = msgs[-max_pairs * 2:]
    if not recent:
        return ""
    return "\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}: {str(m.get('content', ''))[:300]}"
        for m in recent
    )


def _build_web_supplement_prompt(web_sources: list[dict], q: str, history: str = "") -> str:
    """知识库未覆盖时的联网补充 prompt（反幻觉规则随 SYSTEM_PROMPT）。"""
    web_ctx = "\n\n".join(
        f"[网页{i}] {s['title']}\nURL：{s['uri']}\n摘要：{s['body']}"
        for i, s in enumerate(web_sources, 1)
    )
    hist_part = f"\n对话历史：\n{history}\n" if history else ""
    return (
        "你是一个联网问答助手。知识库中未找到相关资料，请基于以下实时网页搜索结果回答。\n"
        "回答要求：\n"
        "1. 关键信息标注来源：[网页i] 对应网页编号\n"
        "2. 网页资料不足时明确说明，不要编造\n"
        "3. 简洁、有条理\n\n"
        f"实时网页搜索结果：\n{web_ctx}\n\n{hist_part}---\n问题：{q}"
    )


def _try_web_supplement(eng, q: str, res: dict, ans: str, history: str = "") -> Optional[tuple[str, list[dict]]]:
    """知识库无答案/弱证据 → 自动联网补充（ddgs 免费搜索）。
    失败/无结果返回 None（保持诚实回答）；成功返回 (回答文本, 网页源列表)。"""
    from app.rag_app.rag_engine import _strip_invalid_refs
    try:
        # 本地已做证据推断（含"推断/据第/关键句证据"）→ 视为已作答，不再联网补充
        already_inferred = any(k in ans for k in ("推断", "据第", "关键句证据"))
        no_local_answer = (
            bool(res.get("weak_evidence"))
            or ans.startswith(("知识库中未找到", "资料未覆盖"))
            or ("未覆盖" in ans[:80] and not already_inferred)
            or ("没有找到" in ans[:60] and not already_inferred)
            or ("无法明确回答" in ans[:60] and not already_inferred)
            or ("没有任何关于" in ans[:100] and not already_inferred)
        )
        if not no_local_answer:
            return None
        web_sources = _ddgs_search(q, max_results=5)
        if not web_sources:
            return None
        from app.rag_app.rag_engine import SYSTEM_PROMPT as RAG_SYSTEM_PROMPT
        resp = eng.llm_client.chat.completions.create(
            model=getattr(eng, "model_name", None),
            messages=[
                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                {"role": "user", "content": _build_web_supplement_prompt(web_sources, q, history=history)},
            ],
            temperature=0.3,
            max_tokens=token_budget(2048),
            timeout=120,
        )
        web_ans = (resp.choices[0].message.content or "").strip()
        web_ans = _strip_invalid_refs(web_ans, max_web=len(web_sources))
        if not web_ans or web_ans.startswith("[LLM调用失败"):
            return None
        prefix = (
            "（知识库资料有限，以下为联网补充信息）\n\n"
            if len(res.get("evidence") or []) > 0
            else "（知识库未覆盖，以下为联网实时搜索补充）\n\n"
        )
        return (
            prefix + web_ans,
            [{"title": s["title"], "uri": s["uri"]} for s in web_sources],
        )
    except Exception:
        return None


# ============ Request Models ============

class ChatReq(BaseModel):
    messages: list[dict] = Field(default_factory=list, max_length=50)
    documentContext: Optional[list[dict]] = Field(default=None, max_length=100)
    webSearchEnabled: bool = False


class FollowupReq(BaseModel):
    question: str = Field(..., max_length=2000)
    answer: str = Field(..., max_length=20_000)


# ============ Chat ============

@router.post("/api/gemini/chat")
def chat(r: ChatReq):
    from app.rag_app.shared_engine import get_engine
    q = ""
    for m in reversed(r.messages):
        if isinstance(m, dict) and m.get("role") == "user":
            q = m.get("content", "")
            break
    if not q:
        raise HTTPException(400, "Empty message")
    try:
        eng = get_engine()
        file_ids = []
        for document in r.documentContext or []:
            if isinstance(document, dict):
                file_id = document.get("id") or document.get("physical_name")
                if file_id:
                    file_ids.append(str(file_id))
        # ⛔ 2026-08-13：多轮追问改写 + HyDE 扩展（一次 LLM 调用；失败降级原问题）。
        # 仅对 多轮/长问题 且 非统计/非查找/非联网 启用，避免拖慢简单问答。
        retrieval_queries = None
        from app.rag_app.rag_engine import (
            _is_stats_question as _is_stats_q,
            _is_library_stats_question as _is_lib_stats_q,
            _LOOKUP_QUESTION_RE,
        )
        user_msgs = [m for m in r.messages if isinstance(m, dict) and m.get("role") == "user"]
        # ⛔ 2026-08-19：联网模式也启用多轮改写（原排除联网 → 指代性问题检索/搜索均失败）
        if (len(user_msgs) >= 2 or len(q) >= 8) \
                and not _is_stats_q(q) and not _LOOKUP_QUESTION_RE.search(q):
            retrieval_queries = _prepare_retrieval_queries(r.messages, q, eng)
        # 2026-08-13：top_k 5→8 提高召回（句子证据/关键词定位需要更多候选；前端展示不变）
        res = eng.query(q, top_k=8, file_ids=file_ids or None, retrieval_queries=retrieval_queries)
        ans = res.get("answer") or res.get("content") or "No results found"
        # ⛔ 2026-08-13：反幻觉 —— 剔除回答中不存在的引用编号
        from app.rag_app.rag_engine import _strip_invalid_refs
        ans = _strip_invalid_refs(ans, max_local=len(res.get("sources") or []))
        # ⛔ 2026-08-13：知识库无答案/弱证据 → 自动联网补充（非统计/查找/清单类）
        search_q = retrieval_queries[0] if retrieval_queries else q
        if not r.webSearchEnabled and not (
            _is_stats_q(q) or _is_lib_stats_q(q) or _LOOKUP_QUESTION_RE.search(q)
        ):
            supplement = _try_web_supplement(
                eng, search_q, res, ans, history=_build_history_text(r.messages),
            )
            if supplement:
                sup_text, sup_sources = supplement
                return {
                    "text": sup_text,
                    "groundingSources": sup_sources,
                    "evidence": [],
                    "webSupplemented": True,
                }
        # ⛔ 2026-08-13：错误语义化——LLM 失败不再用 200 + 错误文本糊弄前端
        # （前端 !response.ok 检测不到 → 429 冷却/友好提示全失效）。
        if ans.startswith("[LLM调用失败"):
            raise HTTPException(502, f"大模型链路响应异常：{ans[:200]}")
        # ⛔ 2026-08-13：联网模式接入真实免费搜索（DuckDuckGo ddgs，无 API Key）。
        # 搜索成功 → 网页结果 + 本地 RAG 合成回答，groundingSources 返回真实网页源；
        # 搜索失败/限流 → 降级为基于本地知识库的诚实提示，不返回伪网页源。
        if r.webSearchEnabled:
            # ⛔ 2026-08-19：搜索词用改写结果（补全指代），生成 prompt 带对话历史
            web_sources = _ddgs_search(search_q, max_results=5)
            if web_sources:
                history_text = _build_history_text(r.messages)
                hist_part = f"\n对话历史：\n{history_text}\n" if history_text else ""
                web_ctx = "\n\n".join(
                    f"[网页{i}] {s['title']}\nURL：{s['uri']}\n摘要：{s['body']}"
                    for i, s in enumerate(web_sources, 1)
                )
                web_prompt = (
                    "你是一个联网问答助手。请优先基于实时网页搜索结果回答，"
                    "可结合本地知识库初步回答补充。回答要求：\n"
                    "1. 关键信息标注来源：[网页i] 对应网页编号，或 [本地知识库]\n"
                    "2. 网页资料不足时明确说明，不要编造\n"
                    "3. 简洁、有条理\n\n"
                    f"实时网页搜索结果：\n{web_ctx}\n\n"
                    f"本地知识库初步回答（仅供参考）：\n{ans}\n{hist_part}"
                    f"---\n问题：{q}"
                )
                try:
                    from app.rag_app.rag_engine import SYSTEM_PROMPT as RAG_SYSTEM_PROMPT
                    web_resp = eng.llm_client.chat.completions.create(
                        model=getattr(eng, "model_name", None),
                        messages=[
                            {"role": "system", "content": RAG_SYSTEM_PROMPT},
                            {"role": "user", "content": web_prompt},
                        ],
                        temperature=0.3,
                        max_tokens=token_budget(2048),
                        timeout=120,
                    )
                    web_ans = (web_resp.choices[0].message.content or "").strip()
                    web_ans = _strip_invalid_refs(web_ans, max_web=len(web_sources))
                    if web_ans and not web_ans.startswith("[LLM调用失败"):
                        return {
                            "text": web_ans,
                            "groundingSources": [
                                {"title": s["title"], "uri": s["uri"]} for s in web_sources
                            ],
                            "evidence": res.get("evidence") or [],
                        }
                except Exception:
                    pass  # 网页合成失败 → 降级本地回答
            return {
                "text": "（联网搜索暂不可用，以下回答基于本地知识库；可稍后重试联网模式）\n\n" + ans,
                "groundingSources": [],
                "evidence": res.get("evidence") or [],
            }
        return {"text": ans, "groundingSources": [], "evidence": res.get("evidence") or []}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"问答链路异常：{e}")


# ============ Follow-ups + SSE 流式 ============

def _do_followups(eng, question: str, answer: str) -> list[str]:
    """基于问答生成 2-3 条延伸追问；失败/无客户端 → []。"""
    try:
        if not getattr(eng, "llm_client", None):
            return []
        from app.rag_app.llm_client_factory import parse_llm_json
        prompt = (
            "你是提问建议器。基于用户问题与回答，生成 2-3 条最有价值的延伸追问，"
            "要求：与回答内容强相关、能在知识库或联网中继续深挖、每条 30 字内。\n"
            "只返回 JSON 数组，如 [\"追问1\", \"追问2\"]，不要多余文字。\n\n"
            f"用户问题：{question[:500]}\n\n回答：{answer[:3000]}"
        )
        resp = eng.llm_client.chat.completions.create(
            model=getattr(eng, "model_name", None),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=token_budget(200),
            timeout=30,
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = parse_llm_json(raw)
        items = parsed if isinstance(parsed, list) else (
            parsed.get("followUps") if isinstance(parsed, dict) else []
        )
        follow_ups = [str(x).strip() for x in (items or []) if str(x).strip()][:3]
        return follow_ups
    except Exception:
        return []


@router.post("/api/gemini/followups")
def followups(r: FollowupReq):
    """基于问答生成 2-3 条延伸追问（前端点击触发，不阻塞主回答）。"""
    from app.rag_app.shared_engine import get_engine
    try:
        eng = get_engine()
    except Exception:
        return {"followUps": []}
    return {"followUps": _do_followups(eng, r.question, r.answer)}


@router.post("/api/gemini/chat/stream")
async def chat_stream(r: ChatReq):
    """SSE 流式问答：meta → sources → chunk* → done / error。
    本地 RAG 与联网模式均支持；统计类问题直接输出文本。"""
    from app.rag_app.shared_engine import get_engine
    eng = get_engine()

    async def event_generator():
        def emit(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        try:
            q = ""
            for m in reversed(r.messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    q = m.get("content", "")
                    break
            if not q:
                yield emit({"type": "error", "content": "空消息"})
                return
            file_ids = []
            for document in r.documentContext or []:
                if isinstance(document, dict):
                    file_id = document.get("id") or document.get("physical_name")
                    if file_id:
                        file_ids.append(str(file_id))

            # 多轮改写/HyDE（与 chat 非流式同规则）
            retrieval_queries = None
            from app.rag_app.rag_engine import (
                _is_stats_question as _is_stats_q,
                _is_library_stats_question as _is_lib_stats_q,
                _LOOKUP_QUESTION_RE,
            )
            user_msgs = [m for m in r.messages if isinstance(m, dict) and m.get("role") == "user"]
            # ⛔ 2026-08-19：联网模式也启用多轮改写（原排除联网 → 指代性问题检索/搜索均失败）
            if (len(user_msgs) >= 2 or len(q) >= 8) \
                    and not _is_stats_q(q) and not _LOOKUP_QUESTION_RE.search(q):
                retrieval_queries = _prepare_retrieval_queries(r.messages, q, eng)

            if r.webSearchEnabled:
                search_q = retrieval_queries[0] if retrieval_queries else q
                history_text = _build_history_text(r.messages)
                web_sources = _ddgs_search(search_q, max_results=5)
                if not web_sources:
                    yield emit({"type": "meta", "mode": "local-fallback"})
                    max_local = 0
                    validate_pending = ""
                    for ev in eng.query_stream(
                        q, top_k=8, file_ids=file_ids or None,
                        retrieval_queries=retrieval_queries,
                    ):
                        if ev.get("type") == "error":
                            yield emit({"type": "error", "content": "联网搜索暂不可用，本地链路异常"})
                            return
                        if ev.get("type") == "sources":
                            max_local = len(ev.get("sources") or [])
                            yield emit(ev)
                            continue
                        if ev.get("type") == "chunk":
                            validate_pending += ev["content"]
                            cleaned, tail = _stream_refs_clean(validate_pending, max_local, 0)
                            validate_pending = tail
                            if cleaned:
                                yield emit({"type": "chunk", "content": cleaned})
                            continue
                        yield emit(ev)
                    if validate_pending:
                        cleaned = _stream_refs_clean(validate_pending, max_local, 0)[0]
                        if cleaned:
                            yield emit({"type": "chunk", "content": cleaned})
                    yield emit({"type": "done"})
                    return
                yield emit({"type": "sources", "groundingSources": [
                    {"title": s["title"], "uri": s["uri"]} for s in web_sources
                ]})
                web_ctx = "\n\n".join(
                    f"[网页{i}] {s['title']}\nURL：{s['uri']}\n摘要：{s['body']}"
                    for i, s in enumerate(web_sources, 1)
                )
                hist_part = f"\n对话历史：\n{history_text}\n" if history_text else ""
                web_prompt = (
                    "你是一个联网问答助手。请优先基于实时网页搜索结果回答。回答要求：\n"
                    "1. 关键信息标注来源：[网页i] 对应网页编号\n"
                    "2. 网页资料不足时明确说明，不要编造\n"
                    "3. 简洁、有条理\n\n"
                    f"实时网页搜索结果：\n{web_ctx}\n\n{hist_part}---\n问题：{q}"
                )
                from app.rag_app.rag_engine import SYSTEM_PROMPT as RAG_SYSTEM_PROMPT
                try:
                    response = eng.llm_client.chat.completions.create(
                        model=getattr(eng, "model_name", None),
                        messages=[
                            {"role": "system", "content": RAG_SYSTEM_PROMPT},
                            {"role": "user", "content": web_prompt},
                        ],
                        temperature=0.3,
                        max_tokens=token_budget(2048),
                        timeout=120,
                        stream=True,
                    )
                    validate_pending = ""
                    for chunk in response:
                        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                            validate_pending += chunk.choices[0].delta.content
                            cleaned, tail = _stream_refs_clean(validate_pending, 0, len(web_sources))
                            validate_pending = tail
                            if cleaned:
                                yield emit({"type": "chunk", "content": cleaned})
                    if validate_pending:
                        cleaned = _stream_refs_clean(validate_pending, 0, len(web_sources))[0]
                        if cleaned:
                            yield emit({"type": "chunk", "content": cleaned})
                    yield emit({"type": "done"})
                except Exception:
                    yield emit({"type": "error", "content": "联网合成失败，请重试"})
                return

            yield emit({"type": "meta", "mode": "rag"})
            max_local = 0
            validate_pending = ""
            rag_full_text = ""
            weak_ev = False
            rag_evidence = 0
            for ev in eng.query_stream(
                q, top_k=8, file_ids=file_ids or None,
                retrieval_queries=retrieval_queries,
            ):
                if ev.get("type") == "sources":
                    max_local = len(ev.get("sources") or [])
                    weak_ev = bool(ev.get("weak_evidence"))
                    rag_evidence = len(ev.get("evidence") or [])
                    yield emit(ev)
                    continue
                if ev.get("type") == "error" and str(ev.get("content", "")).startswith("[LLM调用失败"):
                    yield emit({"type": "error", "content": "大模型链路响应异常，请稍后重试"})
                    return
                if ev.get("type") == "chunk":
                    validate_pending += ev["content"]
                    cleaned, tail = _stream_refs_clean(validate_pending, max_local, 0)
                    validate_pending = tail
                    if cleaned:
                        yield emit({"type": "chunk", "content": cleaned})
                        rag_full_text += cleaned
                    continue
                yield emit(ev)
            if validate_pending:
                cleaned = _stream_refs_clean(validate_pending, max_local, 0)[0]
                if cleaned:
                    yield emit({"type": "chunk", "content": cleaned})
                    rag_full_text += cleaned
            sup_prefix = (
                "（知识库资料有限，以下为联网补充信息）\n\n"
                if rag_evidence > 0
                else "（知识库未覆盖，以下为联网实时搜索补充）\n\n"
            )
            # ⛔ 2026-08-13：知识库无答案/弱证据 → 流式追加联网补充段
            already_inferred = any(k in rag_full_text for k in ("推断", "据第", "关键句证据"))
            no_local = (
                (weak_ev and not already_inferred)
                or rag_full_text.startswith(("知识库中未找到", "资料未覆盖"))
                or ("未覆盖" in rag_full_text[:80] and not already_inferred)
                or ("没有任何关于" in rag_full_text[:100] and not already_inferred)
            )
            if no_local and not (
                _is_stats_q(q) or _is_lib_stats_q(q) or _LOOKUP_QUESTION_RE.search(q)
            ):
                # ⛔ 2026-08-19：补充搜索也用改写词（指代补全），prompt 带对话历史
                sup_q = retrieval_queries[0] if retrieval_queries else q
                sup_history = _build_history_text(r.messages)
                web_sources = _ddgs_search(sup_q, max_results=5)
                if web_sources:
                    yield emit({"type": "sources", "groundingSources": [
                        {"title": s["title"], "uri": s["uri"]} for s in web_sources
                    ], "supplement": True})
                    yield emit({
                        "type": "chunk",
                        "content": sup_prefix,
                    })
                    from app.rag_app.rag_engine import SYSTEM_PROMPT as RAG_SYSTEM_PROMPT
                    try:
                        response = eng.llm_client.chat.completions.create(
                            model=getattr(eng, "model_name", None),
                            messages=[
                                {"role": "system", "content": RAG_SYSTEM_PROMPT},
                                {"role": "user", "content": _build_web_supplement_prompt(web_sources, q, history=sup_history)},
                            ],
                            temperature=0.3,
                            max_tokens=token_budget(2048),
                            timeout=120,
                            stream=True,
                        )
                        pending = ""
                        for chunk in response:
                            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                                pending += chunk.choices[0].delta.content
                                cleaned, tail = _stream_refs_clean(pending, 0, len(web_sources))
                                pending = tail
                                if cleaned:
                                    yield emit({"type": "chunk", "content": cleaned})
                        if pending:
                            cleaned = _stream_refs_clean(pending, 0, len(web_sources))[0]
                            if cleaned:
                                yield emit({"type": "chunk", "content": cleaned})
                    except Exception:
                        yield emit({"type": "chunk", "content": "\n（联网补充失败，以上为知识库诚实回答）"})
            yield emit({"type": "done"})
        except Exception as e:
            yield emit({"type": "error", "content": f"问答链路异常：{e}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
