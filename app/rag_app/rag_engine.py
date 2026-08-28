"""
AI知识库 - RAG检索与生成引擎
基于检索结果的AI问答生成
"""
from typing import Optional, Generator, Dict, Any, List
import re
import logging
import jieba
from app.rag_app.config import Config
from app.rag_app.knowledge_base import KnowledgeBase
from app.rag_app.llm_client_factory import create_llm_client

logger = logging.getLogger("ai_kb.rag_engine")

# 统计类问题识别（2026-08-13）：多少字/页/章/节/段/句/词/篇幅等 → 直接由全文统计回答
_STATS_QUESTION_RE = re.compile(
    r"(多少字|多少页|多少章|多少节|多少段|多少句|多少词|多少行|多长|多厚|"
    r"字数|总字数|页数|篇幅|全[文篇本].{0,4}(字|页|章|段|句)|"
    r"(共|总共|一共|合计).{0,4}(字|页|章|节|段|句|词))"
)

# 知识库级统计（2026-08-13）：多少本/几本书/什么格式/哪些书等 → 文件索引直答
_LIBRARY_STATS_RE = re.compile(
    r"多少本|几本书|多少个文件|几份文件|共有.{0,6}本|一共.{0,6}本|"
    r"哪些书|有哪些书|书都是什么|都有哪些|"
    r"书的格式|什么格式|文件格式|格式是|格式分布|几种格式|格式分别|分别是什么格式|都是什么格式"
)

_SINGLE_FILE_FORMAT_RE = re.compile(r"(这本|当前|该)(书|文档|文件).{0,8}(格式|类型)")

# 找句子/关键词/原句类查询（触发全文精确扫描补证据）
_LOOKUP_QUESTION_RE = re.compile(
    r"(找|查|定位|搜索|检索|原话|原句|这句话|那句|句子|包含|出处|在哪|第几)"
)

_WEAK_EVIDENCE_THRESHOLD = 0.010  # top1 RRF 低于此值视为弱证据（只加警告，不硬切）
_REF_TOKEN_RE = re.compile(r"\[(参考资料|网页)(\d+)\]")

# 背景/创立/历程类问题（2026-08-14）：触发叙事连贯模式（时间线+因果衔接，防碎片罗列）
_NARRATIVE_QUESTION_RE = re.compile(
    r"(背景|创建|创办|创立|成立|创业|起源|由来|来龙去脉|发展历程|演进|"
    r"起步|白手起家|怎么来|如何来|历史沿革)"
)

# 全书总括类问题（2026-08-14）：书级指代 + 总括词 → 触发"结构骨架"模式
_OVERVIEW_QUESTION_RE = re.compile(
    r"(这[本份部]?[书文件章]|全书|本书|整本书|这部作品|这本文档|这篇(文章|文档)|"
    r"整[本份]文档).{0,14}(讲|说|内容|主题|观点|思想|写了|关于|有哪些)|"
    r"总结[一下]?[这那][本份部]?[书文]|"
    r"(这本书|全书|整本书|这[本份]文档).{0,8}什么"
)

# 单文件问答时的泛指总括（2026-08-14）：勾选单文档问"这里面到底讲了什么"场景
_SINGLE_DOC_OVERVIEW_RE = re.compile(
    r"(这里面|里面|这个|它).{0,10}(讲|说|内容|主题|写了|关于|有哪些|什么|哪些|几条|多少)"
)

# 枚举/穷举意图（2026-08-14）：触发 map-reduce 全书清单（单次生成无法穷举）
_ENUMERATE_QUESTION_RE = re.compile(
    r"(有哪些|哪些|清单|列出|罗列|多少条|几[条个]|分别有|包含哪些|包括哪些|"
    r"全面总结|都讲了|讲了哪些|总结一下)"
)

# 创立/历程类叙述词汇（2026-08-14）：证据过滤时对含这些词的历程句放行，
# 防止"一败涂地后几乎破产"等不含实体名的关键背景句被误滤
_HISTORY_VOCAB = (
    "创办", "创立", "成立", "创业", "起步", "白手起家", "一败涂地",
    "早期", "之初", "发展历程", "起源", "经历",
)

_ANTI_HALLUCINATION_RULES = (
    "反幻觉铁律（必须严格遵守）：\n"
    "1. 回答中的每一个事实性断言，必须能在上方参考资料或[关键句证据]中找到原文支撑\n"
    "2. 找不到支撑的断言，严禁用自身知识、常识补充——直接写\"资料未覆盖\"；"
    "但基于[关键句证据]原文的合理推断是允许的，须标注\"（据第X页推断）\"且不超出证据边界\n"
    "3. 引用编号 [参考资料N]/[网页N] 只能引用实际提供的内容；没有依据时不要编造编号\n"
    "4. 宁可回答\"不知道/未找到\"，也绝对不允许无证据编造或脑补（基于证据的推断按第2条执行）"
)

_ANSWER_QUALITY_RULES = (
    "回答质量规范（与反幻觉铁律互补，必须同时遵守）：\n"
    "1. 结论先行：第一句直接给出问题的答案/结论，再展开支撑证据；不要铺垫、绕弯或先声明\"没有直接记载\"\n"
    "2. 要点精炼不碎片化：只保留与问题直接相关的信息，弱关联不硬凑；"
    "相关要点合并为连贯表述，背景/历程/原因类问题按时间与因果顺序组织，"
    "禁止写成互不相干的编号要点列表（对比/列举类问题除外）\n"
    "3. 逐条带出处：每一条都要标注 [参考资料N]/[网页N] 或\"（据第X页推断）\"\n"
    "4. 事实与推断分离：原文明确记载的写为事实；基于原文的合理推断必须标注\"（据第X页推断）\"\n"
    "5. 表述精确不绝对化：不用\"官方并未/从未/全部/一定\"类全称判断，明确限定证据覆盖的范围\n"
    "6. 篇幅控制：默认 300~500 字，除非问题本身需要更长展开；宁少勿滥\n"
    "注意：结论先行不豁免反幻觉铁律——第一句结论同样必须有证据支撑，或按规则标注推断。"
)

# 证据实体对齐过滤用停用词（2026-08-14）：通用动词/衬词/泛组织词不视为实体，
# 防止"创建/基金"等词把"麦修创建中国关爱基金"类噪声句兜进证据
_ENTITY_STOPWORDS = {
    "为什么", "什么", "怎么", "如何", "关于", "这个", "那个", "一个",
    "可以", "没有", "不是", "就是", "然后", "后面", "突然", "的事",
    "原因", "背景", "内容", "相关", "问题", "告诉", "提到", "说明",
    "以及", "还是", "但是", "因为", "所以", "如果", "那么", "这样", "那样",
    "创建", "创立", "创办", "成立", "建立", "进行", "开始", "出现", "发生",
    "成为", "介绍", "说说", "讲讲", "描述", "分析", "解释", "找出", "寻找",
    "查找", "搜索", "定位", "知道", "认为", "觉得", "看到", "听到", "想要",
    "应该", "需要", "得到", "找到", "叫做", "称为", "来自", "包含",
    "基金", "公司", "机构", "组织", "项目", "部门", "企业", "团体",
    "中心", "部分", "方面", "情况", "事情", "方法", "方式", "过程",
    "历史", "故事", "书里", "书中", "全书", "里面", "信息",
}


def _filter_evidence_by_entity(evidence: list[dict], question: str,
                               allow_history_vocab: bool = False) -> list[dict]:
    """证据句实体对齐过滤：证据句必须命中至少一个问题实体词，否则视为噪声丢弃。
    叙事类问题（allow_history_vocab=True）额外放行含创立/历程词汇的句子
    （如"一败涂地后几乎破产"），因为这些句子常以"我"指代实体，不含实体名；
    但历程句仅放行自"实体命中句所在文件"的句子，防止跨书"创立/早期/经历"噪声
    （经验：西琴背景查询混入《与神对话》《三体》的"创立/创办/早期"句）。
    回退：过滤后为空 → 返回原证据（防误伤查找/短语类问题，此时问题无实体词或全部被滤）。
    """
    if not evidence or not question:
        return evidence
    terms = []
    for t in jieba.cut(question):
        t = t.strip()
        if (len(t) >= 2 and t not in _ENTITY_STOPWORDS
                and all(("\u4e00" <= c <= "\u9fff") or c.isalnum() for c in t)):
            terms.append(t)
    if not terms:
        return evidence
    entity_hits = [e for e in evidence if any(t in e.get("text", "") for t in terms)]
    if allow_history_vocab and entity_hits:
        entity_files = {
            e.get("physical_name") or e.get("file_name")
            for e in entity_hits if e.get("physical_name") or e.get("file_name")
        }
        history_hits = [
            e for e in evidence
            if (e.get("physical_name") or e.get("file_name")) in entity_files
            and any(v in e.get("text", "") for v in _HISTORY_VOCAB)
        ]
        kept = entity_hits + [h for h in history_hits if h not in entity_hits]
        return kept or evidence
    return entity_hits or evidence


def _narrative_expansion_query(question: str) -> Optional[str]:
    """叙事类问题的检索扩展：实体词 + 创立/历程词汇，帮助召回"1975年创办桥水"等
    原文段（原问题只含"创建背景"，BM25 难命中"创办/一败涂地"等叙事词汇）。
    """
    if not question or not _NARRATIVE_QUESTION_RE.search(question):
        return None
    terms = []
    for t in jieba.cut(question):
        t = t.strip()
        if (len(t) >= 2 and t not in _ENTITY_STOPWORDS
                and all(("\u4e00" <= c <= "\u9fff") or c.isalnum() for c in t)):
            terms.append(t)
    if not terms:
        return None
    entity = max(terms, key=len)  # 取最具体实体词（如"桥水"，排除"基金/创建/背景"）
    return f"{entity} 创办 创立 成立 创业 起步 白手起家 一败涂地 早期经历"


def _is_overview_question(question: str, single_doc: bool = False) -> bool:
    """总括类问题判定：明确书级指代；或单文件场景下的泛指（这里面/里面/它…）；
    或单文件场景下的纯枚举词（有哪些/哪些/多少条…，如用户问"有哪些原则呢"——
    无书级指代但勾选了文档，意图明确是问该书内容）。"""
    if _OVERVIEW_QUESTION_RE.search(question):
        return True
    if single_doc and _SINGLE_DOC_OVERVIEW_RE.search(question):
        return True
    return bool(single_doc and _ENUMERATE_QUESTION_RE.search(question))


def _overview_sampling(kb, question: str, file_ids: Optional[list[str]],
                       max_chunks: int = 28,
                       budget_chars: int = 14000) -> Optional[list[dict]]:
    """总括类单文件：全书均匀采样 chunks（按 chunk_index 等距取代表，覆盖各章节）。
    替代 top_k 相关排序检索——top_k 只看 8 个片段，全书几百条原则漏掉大半。
    2026-08-14 通用性：max_chunks 按 token 预算自适应（budget_chars / 平均chunk长），
    长 chunk 少采、短 chunk 多采；换小上下文模型也不会挤爆 context。
    返回与 kb.search 同构的结果（metadata 含 page/chunk/domain，score=1.0 防弱证据误判）。
    """
    if not file_ids or len(file_ids) != 1:
        return None
    try:
        chunks = kb.get_chunks_by_file(file_ids[0], max_chunks=999999)
    except Exception:
        return None
    if not chunks:
        return None
    n = len(chunks)
    avg_len = max(1, sum(len((c.get("text") or "")) for c in chunks) // n)
    max_chunks = min(max_chunks, max(8, budget_chars // max(avg_len, 200)))
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
        picked = uniq
    else:
        picked = chunks
    results = []
    for c in picked:
        meta = c.get("metadata") or {}
        results.append({
            "text": c.get("text", ""),
            "metadata": {
                "file_name": meta.get("file_name") or file_ids[0],
                "physical_name": file_ids[0],
                "page_number": meta.get("page_number", 0),
                "chunk_index": meta.get("chunk_index", 0),
                "domain": meta.get("domain", ""),
                "score": 1.0,
            },
            "score": 1.0,
        })
    return results or None


def _overview_group_chunks(chunks: list[dict], max_groups: int = 6,
                           per_group: int = 8) -> tuple[list[list[dict]], int]:
    """全书 chunks 按序分组 + 组内等距采样（map-reduce 的 MAP 输入划分）。
    组数按 chunk 总量自适应；每组返回 per_group 个代表块。"""
    n = len(chunks)
    if n <= 100:
        k = 3
    elif n <= 600:
        k = 4
    elif n <= 2000:
        k = 5
    else:
        k = 6
    k = min(k, max_groups)
    groups = []
    for gi in range(k):
        lo = round(gi * n / k)
        hi = round((gi + 1) * n / k)
        g = chunks[lo:hi]
        m = min(per_group, len(g))
        idxs = sorted({round(i * (len(g) - 1) / (m - 1)) for i in range(m)}) if m > 1 else [0]
        groups.append([g[i] for i in idxs])
    return groups, k


def _is_stats_question(question: str) -> bool:
    return bool(question and _STATS_QUESTION_RE.search(question))


def _is_library_stats_question(question: str) -> bool:
    return bool(question and _LIBRARY_STATS_RE.search(question))


def _format_evidence_loc(e: dict) -> str:
    loc = []
    if e.get("page_number"):
        loc.append(f"第{e['page_number']}页")
    if e.get("chunk_index") is not None:
        try:
            loc.append(f"第{int(e.get('chunk_index', 0)) + 1}段")
        except Exception:
            pass
    return "·".join(loc)


def _build_answer_prompt(question: str, context: str, graph_context: str,
                         evidence_block: str = "", weak_evidence: bool = False,
                         single_doc: bool = False) -> str:
    """统一回答 prompt（query/query_stream 共用，避免规则漂移）。
    注入反幻觉铁律；弱证据时追加诚实拒绝警告；
    总括类问题（single_doc 时含"这里面"泛指）注入结构骨架模式。"""
    parts = [
        "基于以下参考资料回答用户问题。",
        f"参考资料：\n{context}{graph_context}{evidence_block}",
        "---",
        f"请简洁概括地回答：{question}",
        "确保每个关键信息标注来源。",
        _ANTI_HALLUCINATION_RULES,
        _ANSWER_QUALITY_RULES,
    ]
    if weak_evidence:
        parts.append(
            "[警告] 以上资料与问题相关性可能不足；如果无法从资料中找到答案，"
            "请直接回答\"知识库未覆盖该问题\"，不要推测。"
        )
    if evidence_block:
        parts.append(
            "如果用户要求找句子/关键词/原句，请直接引用[关键句证据]中的原文并标注位置，不要改写原文。"
        )
        parts.append(
            "如果[关键句证据]已给出与问题相关的原文句子，请基于证据句之间的关联直接回答"
            "\"为什么/原因\"类问题并标注引用；不要仅因证据句与问题措辞不一致就放弃回答。"
        )
    if any(k in question for k in ("为什么", "原因", "为何", "怎么回")):
        parts.append(
            "这是\"为什么/原因\"类问题：不要寻找能逐字回答的直接记载；"
            "请先引用[关键句证据]中的原文，再基于证据句之间的关联给出推断，标注\"（据第X页推断）\"。"
            "只要证据句与问题人物/事件相关，就必须给出推断性回答；只有证据完全无关时才回答\"资料未覆盖\"。"
            "回答请直接以结论开头（如\"叶文洁提醒罗辑，是因为…\"），不要先声明\"没有直接记载/无法直接回答\"。"
        )
    if _NARRATIVE_QUESTION_RE.search(question):
        parts.append(
            "这是\"背景/创立/历程\"类问题：请组织成连贯的因果叙述，而不是逐条罗列孤立要点。\n"
            "第一句给出总括结论后，按\"起因 → 早期经历 → 关键转折 → 结果/后续\"的脉络展开；\n"
            "事件之间用时间/因果衔接词（因此、随后、在此基础上、但）自然连接，让前后文成为一条线；\n"
            "表达同一主题的多条证据合并为一段叙述，不要拆成编号列表；\n"
            "若资料未给出明确先后顺序，按证据支持的合理因果排序，并标注\"（据第X页推断）\"。"
        )
    is_overview = bool(_OVERVIEW_QUESTION_RE.search(question))
    if not is_overview and single_doc and _SINGLE_DOC_OVERVIEW_RE.search(question):
        is_overview = True
    if is_overview:
        parts.append(
            "这是\"全书总括\"类问题：请先给出整本书的结构骨架，再按骨架展开，"
            "不要平铺互不相关的零散要点。\n"
            "1. 第一句总述本书主题/定位；\n"
            "2. 然后给出结构骨架：若参考资料含目录/章节/分区信息，严格按该结构组织"
            "（如\"本书分为生活原则与工作原则两大部分：生活原则含…；工作原则含…\"）；"
            "资料未明确分区时，按内容归纳出主要板块并标注\"（据内容归纳）\"；\n"
            "3. 再按骨架逐块展开关键内容，每块带来源标注；\n"
            "4. 结构骨架本身不得编造——只能来自参考资料中明确出现的章节/目录/分区，"
            "或如实标注为归纳；宁缺毋滥。\n"
            "5. 若问题含\"有哪些/哪些/清单/罗列/列出/多少条\"等枚举意图："
            "请逐条列出参考资料中明确出现的原则/章节/条目（保留编号或标题），"
            "按结构分组；宁可多列（有出处的），也不要只概括方向性结论——"
            "参考资料里出现过的条目都要尽量列出，未出现的不得编造。"
        )
    return "\n".join(parts)


def _strip_invalid_refs(text: str, max_local: int = 0, max_web: int = 0) -> str:
    """回答后引用校验：剔除不存在的 [参考资料N]/[网页N] 编号（防幻觉引用）。"""
    if not text or (max_local <= 0 and max_web <= 0):
        return text

    def _repl(m) -> str:
        kind, num = m.group(1), int(m.group(2))
        limit = max_local if kind == "参考资料" else max_web
        if limit and num > limit:
            return ""
        return m.group(0)

    return _REF_TOKEN_RE.sub(_repl, text)


SYSTEM_PROMPT = """你是一个知识库智能助手。请使用中文回答。请基于提供的参考资料回答用户问题。
要求：
1. 必须基于参考资料回答，不要编造信息
2. 每个关键信息标注来源（参考资料编号）
3. 如果资料不足或某断言无出处，明确标注（如"资料未覆盖/无证据"），不要编造或脑补
4. 回答简洁、有条理
5. 如果有'[图谱关系]'信息，结合关系链推断答案（节点间的连接词表示语义关系）"""


def _build_graph_context(search_results: list, max_total_edges: int = 8) -> str:
    """从检索结果中提取图谱节点和边，构建简洁的关系上下文。
    零 LLM 开销——直接复用 concept_extractor 的规则提取。
    """
    try:
        from app.rag_app.concept_extractor import ConceptExtractor
        extractor = ConceptExtractor()
        all_chunks = []
        for r in search_results:
            meta = r.get("metadata", {})
            all_chunks.append({
                "text": r.get("text", ""),
                "source_file": meta.get("file_name", meta.get("source", "unknown")),
                "source_chunk_id": f"{meta.get('file_name', 'unk')}_{meta.get('chunk_index', 0)}",
                "domain": meta.get("domain", "all"),
            })
        if not all_chunks:
            return ""
        graph = extractor.extract_from_chunks(all_chunks, max_nodes=6, graph_mode="auto")
        edges = graph.get("edges", [])
        if not edges:
            return ""
        lines = ["\n[图谱关系] 以下概念关联信息来自规则提取，可用于推理："]
        added = 0
        for e in edges:
            label = e.get("label", "").strip()
            if not label or label in ("同句", "关联", ""):
                continue
            la = e.get("_label_a", e.get("from", "?"))
            lb = e.get("_label_b", e.get("to", "?"))
            lines.append(f"  · {la} —{label}— {lb}")
            added += 1
            if added >= max_total_edges:
                break
        return "\n".join(lines) if added > 0 else ""
    except Exception:
        return ""


class RAGEngine:
    def __init__(self, config=None, kb=None):
        self.config = config or Config()
        self.kb = kb or KnowledgeBase(self.config)
        self.llm_client = create_llm_client()
        self.model_name = self.config.STEP_MODEL

    def _overview_map_reduce(self, question: str,
                             file_ids: Optional[list[str]]) -> Optional[str]:
        """枚举类总括：全书分块 MAP 提取 → REDUCE 归约（GraphRAG global search 模式）。
        返回最终完整清单文本；任何一步失败返回 None（调用方降级为单次采样）。"""
        try:
            chunks = self.kb.get_chunks_by_file(file_ids[0], max_chunks=999999)
        except Exception:
            return None
        if not chunks:
            return None
        groups, k = _overview_group_chunks(chunks)
        map_outputs: list[str] = []
        for gi, group in enumerate(groups, 1):
            try:
                context = "\n\n".join(
                    f"[片段{ci + 1}] 第{(c.get('metadata') or {}).get('page_number', '?')}页：{c.get('text', '')}"
                    for ci, c in enumerate(group)
                )
                prompt = (
                    f"你是全书要点提取器。下面是《当前书籍》第 {gi}/{k} 部分"
                    f"（全文按序共 {k} 部分）的代表性片段。\n"
                    f"用户问题：{question}\n"
                    "请提取该部分中出现的【所有】相关条目（原则/要点/可能性/内容章节），"
                    "逐条列出：\n"
                    "- 每条一行，格式：编号. 条目标题（20~40字概括）｜页码\n"
                    "- 只提取片段中明确出现的，绝不编造；宁缺毋滥，不概括类别\n"
                    f"片段内容：\n{context}"
                )
                resp = self.llm_client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2, max_tokens=1024, timeout=60,
                )
                map_outputs.append(f"【第 {gi}/{k} 部分提取】\n{resp.choices[0].message.content or ''}")
            except Exception:
                return None  # 任一部分失败 → 降级
        try:
            reduce_prompt = (
                f"用户问题：{question}\n"
                "以下是按全书顺序分块提取的条目清单。请合并去重、按逻辑结构分组"
                "（如生活原则/工作原则、主题板块），输出【最终完整清单】：\n"
                "- 每条保留出处页码；同一条目多次出现只保留一次\n"
                "- 用标题分组，条目逐条列出，不要只概括类别\n"
                "- 只保留提取结果中出现的条目，不得补充未出现内容\n\n"
                + "\n\n".join(map_outputs)
            )
            resp = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": reduce_prompt}],
                temperature=0.2, max_tokens=4000, timeout=90,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return None

    def _llm_chat_retry(self, system_prompt: str, user_prompt: str,
                        max_tokens: int = 2048, timeout: int = 60,
                        attempts: int = 3, retry_on_truncation: bool = False,
                        truncation_boost: int = 2000) -> tuple[str, str]:
        """LLM 调用容错重试（2026-08-18，便携版实测问题修复）。

        - 空 content 重试：DeepSeek 等偶发返回空内容（便携版实测概率约 50%）；
        - retry_on_truncation=True 时 finish_reason=length 用更大 max_tokens 重试；
        - 异常不在此吞掉（向上抛，调用方决定失败文案）。
        返回 (content, finish_reason)。
        """
        last_reason = ""
        for i in range(attempts):
            resp = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3, max_tokens=max_tokens, timeout=timeout,
            )
            choice = resp.choices[0]
            content = (getattr(choice.message, "content", None) or "").strip()
            reason = getattr(choice, "finish_reason", None) or ""
            last_reason = reason
            if content and not (retry_on_truncation and reason == "length"):
                return content, reason
            if reason == "length" and retry_on_truncation:
                max_tokens += truncation_boost
            elif not content:
                logger.warning("LLM返回空内容，重试 %d/%d", i + 1, attempts)
        return "", last_reason

    def query(self, question: str, top_k: int = 5, domain: str = None,
              file_ids: Optional[list[str]] = None,
              retrieval_queries: Optional[list[str]] = None) -> Dict[str, Any]:
        where_filter = None
        if file_ids and len(file_ids) > 0:
            where_filter = {"file_name": file_ids[0] if len(file_ids) == 1 else {"$in": file_ids}}

        # ⛔ 2026-08-13：统计类问题（多少字/页/章/段/句）直接由全文统计回答，不依赖检索猜数
        if _is_stats_question(question):
            stats_ans = self._answer_stats(question, file_ids, domain)
            if stats_ans:
                return {"answer": stats_ans, "sources": [], "domain": domain, "stats_answer": True}
        # ⛔ 2026-08-13：知识库级统计（多少本/什么格式）由文件索引直答，不进 RAG
        if _is_library_stats_question(question):
            lib_ans = self._answer_library_stats(question, file_ids)
            if lib_ans:
                return {"answer": lib_ans, "sources": [], "domain": domain, "stats_answer": True}

        single_doc = bool(file_ids and len(file_ids) == 1)
        # ⛔ 2026-08-14：枚举类总括 → map-reduce 全书清单（单次生成无法穷举）
        is_overview = single_doc and _is_overview_question(question, single_doc=True)
        if is_overview and _ENUMERATE_QUESTION_RE.search(question):
            mapped = self._overview_map_reduce(question, file_ids)
            if mapped:
                return {
                    "answer": mapped,
                    "sources": [], "domain": domain,
                    "evidence": [], "weak_evidence": False,
                    "overview_map_reduce": True,
                }
        # ⛔ 2026-08-14：总括类单文件 → 全书均匀采样（覆盖各章节，防 top_k 片面）
        overview_results = None
        if is_overview:
            overview_results = _overview_sampling(self.kb, question, file_ids)
        if overview_results is not None:
            search_results = overview_results
        else:
            # ⛔ 2026-08-14：叙事类问题检索扩展 —— 原问题只含"创建背景"难命中
            # "1975年创办桥水/一败涂地/白手起家"等历程原文，补充实体+历程词汇扩展查询
            narrative_exp = _narrative_expansion_query(question)
            if narrative_exp:
                base = list(retrieval_queries or [])
                retrieval_queries = (base[:1] + [narrative_exp])[:2] if base else [narrative_exp]

        search_results = self.kb.search(
            question, top_k=top_k, domain=domain, where_filter=where_filter,
            queries=retrieval_queries, diversity=True,
        )
        if not search_results:
            return {
                "answer": "知识库中未找到与该问题直接相关的内容，我不会凭空编造答案。"
                          "请换一种问法，或切换到联网模式实时搜索。",
                "sources": [], "domain": domain,
            }

        context_parts = []
        sources = []
        seen_sources = set()
        for i, result in enumerate(search_results, 1):
            meta = result["metadata"]
            source_label = meta.get("source", meta.get("file_name", "未知来源"))
            page_number = meta.get("page_number", "")
            chunk_idx = meta.get("chunk_index", "")
            location = f"第 {page_number} 页" if page_number else ""
            location += f" · 第 {chunk_idx} 段落" if chunk_idx else location
            context_parts.append(f"[参考资料{i}] 来源：{source_label}{' (' + location + ')' if location else ''}\n{result['text']}")
            src_key = f"{meta.get('file_name', '')}_{page_number}_{chunk_idx}"
            if src_key not in seen_sources:
                seen_sources.add(src_key)
                sources.append({
                    "source": source_label, "file_name": meta.get("file_name", ""),
                    "page_num": page_number, "chunk_index": chunk_idx,
                    "location": location, "score": round(result.get("distance", result.get("score", 1.0)), 4),
                    "domain": meta.get("domain", ""), "text": result["text"],
                })

        context = "\n\n---\n\n".join(context_parts)
        graph_context = _build_graph_context(search_results)
        # ⛔ 2026-08-14 通用性：总括类单文件注入目录/章节标题（headings），
        # "有哪些"类问题可据目录逐条列举，弥补均匀采样漏掉的章节标题
        if overview_results is not None:
            heads = ((self.kb._file_headings or {}).get(file_ids[0]) or [])
            titles = [h.get("text") for h in heads
                      if isinstance(h, dict) and h.get("text")]
            if titles:
                head_block = (
                    "[全书结构参考] 该书目录/章节标题（回答\"有哪些\"类问题时"
                    "请按此目录逐条列举并标注来源）：\n"
                    + "\n".join("· " + str(t) for t in titles[:60])
                )
                context = head_block + "\n\n---\n\n" + context

        # ⛔ 2026-08-13：句子级证据 —— 检索 top chunks 切句取证 + 找句子类查询全文精确扫描
        evidence: list[dict] = []
        # ⛔ 2026-08-13：精确扫描优先（含改写查询，特异性更高），再补充检索结果切句，
        # 防止背景句（红岸/天体物理）先占满证据列表挤掉关键对话句
        try:
            hit_files = sorted({
                (r.get("metadata") or {}).get("physical_name") or (r.get("metadata") or {}).get("file_name")
                for r in search_results
                if (r.get("metadata") or {}).get("physical_name") or (r.get("metadata") or {}).get("file_name")
            })
            scan_q = " ".join([question] + list(retrieval_queries or []))
            evidence.extend(self.kb.find_exact_sentences(scan_q, top_k=6, file_ids=hit_files or file_ids))
        except Exception:
            pass
        try:
            evidence.extend(self.kb.extract_sentence_evidence(search_results, question, max_sentences=8))
        except Exception:
            pass
        seen_ev: set[str] = set()
        unique_evidence: list[dict] = []
        for e in evidence:
            key = e.get("text", "")[:80]
            if key not in seen_ev:
                seen_ev.add(key)
                unique_evidence.append(e)
        # ⛔ 2026-08-13：按位置 (文件名|页|段) 去重，同位置最多 2 条，避免"重复定位"
        pos_counts: dict[str, int] = {}
        capped_evidence: list[dict] = []
        for e in unique_evidence:
            pos_key = f"{e.get('physical_name')}|{e.get('page_number')}|{e.get('chunk_index')}"
            if pos_counts.get(pos_key, 0) >= 2:
                continue
            pos_counts[pos_key] = pos_counts.get(pos_key, 0) + 1
            capped_evidence.append(e)
        unique_evidence = capped_evidence
        # ⛔ 2026-08-14：实体对齐去噪 —— 只匹配到通用动词（如"创建"）的无关句
        # （如"麦修创建中国关爱基金"）会被过滤，过滤空则回退原证据防误伤
        unique_evidence = _filter_evidence_by_entity(
            unique_evidence, question,
            allow_history_vocab=bool(_NARRATIVE_QUESTION_RE.search(question)),
        )
        evidence_block = ""
        if unique_evidence:
            ev_lines = []
            for e in unique_evidence[:12]:
                loc = _format_evidence_loc(e)
                head = f"· 《{e.get('file_name') or e.get('physical_name') or '未知来源'}》"
                if loc:
                    head += f" {loc}"
                line = f"{head}：「{e['text']}」"
                window = e.get("window") or ""
                if window and window != e["text"]:
                    line += f"\n    （上下文：{window}）"
                ev_lines.append(line)
            evidence_block = (
                "\n\n[关键句证据] 以下是与问题直接相关的原文句子（含定位）；"
                "用户要求找句子/关键词/原句时，请优先原样引用并标注《书名》页码段落：\n"
                + "\n".join(ev_lines)
            )

        top_score = float(search_results[0].get("score", 0.0) or 0.0)
        weak_evidence = top_score < _WEAK_EVIDENCE_THRESHOLD
        enhanced_prompt = _build_answer_prompt(
            question, context, graph_context, evidence_block, weak_evidence,
            single_doc=bool(file_ids and len(file_ids) == 1),
        )
        try:
            # ⛔ 2026-08-18：LLM 偶发返回空 content（便携版实测约 50%）→ 最多重试 3 次
            answer, _reason = self._llm_chat_retry(
                SYSTEM_PROMPT, enhanced_prompt, max_tokens=2048, timeout=60, attempts=3,
            )
            if not answer:
                answer = "\n\n（模型未返回有效回答，请稍后重试）"
        except Exception as e:
            answer = f"\n\n[LLM调用失败: {str(e)}]"
        # ⛔ 2026-08-13：反幻觉 —— 剔除回答中不存在的引用编号
        answer = _strip_invalid_refs(answer, max_local=len(search_results))
        return {"answer": answer, "sources": sources, "domain": domain,
                "evidence": unique_evidence[:12], "weak_evidence": weak_evidence}

    @staticmethod
    def _format_stats_line(s: dict, prefix: str = "") -> str:
        name = s.get("file_name") or s.get("physical_name") or "文档"
        head = prefix or f"《{name}》"
        parts = [
            f"总字数约 {int(s.get('total_chars', 0) or 0):,} 字（不含空白）",
            f"中文字符约 {int(s.get('chinese_chars', 0) or 0):,}",
            f"词数约 {int(s.get('words', 0) or 0):,}",
            f"句子约 {int(s.get('sentences', 0) or 0):,} 句",
            f"段落约 {int(s.get('paragraphs', 0) or 0):,} 段",
        ]
        if s.get("pages"):
            parts.append(f"共 {int(s['pages'])} 页")
        if s.get("chapters"):
            parts.append(f"共 {int(s['chapters'])} 章")
        return f"{head}：{'；'.join(parts)}。"

    def _answer_stats(self, question: str, file_ids: Optional[list[str]],
                      domain: Optional[str]) -> str:
        try:
            stats = self.kb.get_library_stats(file_ids=file_ids)
        except Exception:
            return ""
        files = stats.get("files") or []
        totals = stats.get("totals") or {}
        if not files:
            return ""
        if len(files) == 1:
            return self._format_stats_line(files[0]) + "\n（以上由知识库全文正文统计，非估算）"
        lines = [f"共 {len(files)} 篇文档，全文统计如下："]
        lines.append(self._format_stats_line(totals, prefix="全库合计"))
        for s in files[:5]:
            lines.append(self._format_stats_line(s))
        lines.append("（以上由知识库全文正文统计，非估算）")
        return "\n".join(lines)

    def _answer_library_stats(self, question: str, file_ids: Optional[list[str]]) -> str:
        """知识库级统计直答：共几本书 + 格式分布 +（问题含"哪些/分别"时）逐本格式清单。"""
        try:
            # "库"意图或未指定文件 → 全库；否则按给定范围统计
            use_all = ("库" in question) or not file_ids
            stats = self.kb.get_library_stats(file_ids=None if use_all else file_ids)
        except Exception:
            return ""
        files = stats.get("files") or []
        if not files:
            return ""
        # 单文件格式问法（"这本书/当前文档是什么格式"）
        if _SINGLE_FILE_FORMAT_RE.search(question) and len(files) == 1:
            f = files[0]
            name = f.get("file_name") or f.get("physical_name") or "文档"
            fmt = f.get("format") or "unknown"
            return f"《{name}》是 {fmt.upper() if fmt != 'unknown' else '未知'} 格式。"
        fmt_lines = [
            f"- {fmt.upper() if fmt != 'unknown' else '未知'}: {cnt} 本"
            for fmt, cnt in sorted(stats.get("formats", {}).items(), key=lambda x: -x[1])
        ]
        lines = [f"知识库中共有 {len(files)} 本书："] + (fmt_lines or ["（暂无格式信息）"])
        if re.search(r"哪些|分别|都有|清单|什么格式", question):
            lines.append("清单：")
            for f in sorted(files, key=lambda x: str(x.get("file_name") or x.get("physical_name") or "")):
                name = f.get("file_name") or f.get("physical_name") or "?"
                fmt = f.get("format") or "unknown"
                base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                title = base.rsplit(".", 1)[0] if "." in base else base
                lines.append(f"- 《{title}》({fmt})")
        return "\n".join(lines)

    def query_stream(self, question: str, top_k: int = 5, domain: Optional[str] = None,
                     file_ids: Optional[list[str]] = None, search_mode: str = "hybrid",
                     analysis_mode: str = "summary",
                     retrieval_queries: Optional[list[str]] = None) -> Generator[Dict[str, Any], None, None]:
        if not self.kb:
            yield {"type": "error", "content": "知识库未初始化。\n\n"}
            return

        # ⛔ 2026-08-13：统计类问题直答（不流式，直接输出统计文本）
        if _is_stats_question(question):
            stats_ans = self._answer_stats(question, file_ids, domain)
            if stats_ans:
                yield {"type": "text", "content": stats_ans, "stats": True}
                return
        # ⛔ 2026-08-13：知识库级统计直答（多少本/什么格式）
        if _is_library_stats_question(question):
            lib_ans = self._answer_library_stats(question, file_ids)
            if lib_ans:
                yield {"type": "text", "content": lib_ans, "stats": True}
                return

        # ⛔ 2026-08-14：枚举类总括 → map-reduce 全书清单（与 query() 同款）
        single_doc = bool(file_ids and len(file_ids) == 1)
        if single_doc and _is_overview_question(question, single_doc=True) \
                and _ENUMERATE_QUESTION_RE.search(question):
            mapped = self._overview_map_reduce(question, file_ids)
            if mapped:
                yield {"type": "text", "content": mapped, "overview_map_reduce": True}
                return

        where_filter = None
        if file_ids and len(file_ids) > 0:
            where_filter = {"file_name": file_ids[0] if len(file_ids) == 1 else {"$in": file_ids}}

        single_doc = bool(file_ids and len(file_ids) == 1)
        # ⛔ 2026-08-14：总括类单文件 → 全书均匀采样（与 query() 同款）
        overview_results = None
        if single_doc and _is_overview_question(question, single_doc=True):
            overview_results = _overview_sampling(self.kb, question, file_ids)
        if overview_results is not None:
            search_results = overview_results
        else:
            # ⛔ 2026-08-14：叙事类问题检索扩展（与 query() 同款）—— 召回创立历程原文段
            narrative_exp = _narrative_expansion_query(question)
            if narrative_exp:
                base = list(retrieval_queries or [])
                retrieval_queries = (base[:1] + [narrative_exp])[:2] if base else [narrative_exp]

            search_results = self.kb.search(
                question, top_k=top_k, domain=domain,
                where_filter=where_filter, search_mode=search_mode,
                queries=retrieval_queries, diversity=True,
            )
        if not search_results:
            yield {
                "type": "text",
                "content": "知识库中未找到与该问题直接相关的内容，我不会凭空编造答案。"
                           "请换一种问法，或切换到联网模式实时搜索。",
            }
            return

        context_parts = []
        sources = []
        seen_sources = set()
        for i, result in enumerate(search_results, 1):
            meta = result["metadata"]
            source_label = meta.get("source", meta.get("file_name", "未知来源"))
            page_number = meta.get("page_number", "")
            chunk_idx = meta.get("chunk_index", "")
            location = ""
            if page_number:
                location = f"第 {page_number} 页"
            if chunk_idx:
                location = f"第 {chunk_idx} 段落" if not page_number else f"{location} · 第 {chunk_idx} 段落"
            context_parts.append(f"[参考资料{i}] 来源：{source_label}{' (' + location + ')' if location else ''}\n{result['text']}")
            src_key = f"{meta.get('file_name', '')}_{page_number}_{chunk_idx}"
            if src_key not in seen_sources:
                seen_sources.add(src_key)
                sources.append({
                    "source": source_label, "file_name": meta.get("file_name", ""),
                    "page_num": page_number, "chunk_index": chunk_idx,
                    "location": location,
                    "score": round(result.get("distance", result.get("score", 1.0)), 4),
                    "domain": meta.get("domain", ""), "text": result["text"],
                })

        # ⛔ 2026-08-13：句子证据（检索切句 + 查找类/多轮改写精确扫描，与 query() 同款）
        evidence: list[dict] = []
        # ⛔ 2026-08-13：同 query() —— 精确扫描优先，再补充检索结果切句
        try:
            hit_files = sorted({
                (r.get("metadata") or {}).get("physical_name") or (r.get("metadata") or {}).get("file_name")
                for r in search_results
                if (r.get("metadata") or {}).get("physical_name") or (r.get("metadata") or {}).get("file_name")
            })
            scan_q = " ".join([question] + list(retrieval_queries or []))
            evidence.extend(self.kb.find_exact_sentences(scan_q, top_k=6, file_ids=hit_files or file_ids))
        except Exception:
            pass
        try:
            evidence.extend(self.kb.extract_sentence_evidence(search_results, question, max_sentences=8))
        except Exception:
            pass
        seen_ev: set[str] = set()
        unique_evidence: list[dict] = []
        for e in evidence:
            key = e.get("text", "")[:80]
            if key not in seen_ev:
                seen_ev.add(key)
                unique_evidence.append(e)
        # ⛔ 2026-08-13：同 query() —— 按位置去重，同位置最多 2 条
        pos_counts: dict[str, int] = {}
        capped_evidence: list[dict] = []
        for e in unique_evidence:
            pos_key = f"{e.get('physical_name')}|{e.get('page_number')}|{e.get('chunk_index')}"
            if pos_counts.get(pos_key, 0) >= 2:
                continue
            pos_counts[pos_key] = pos_counts.get(pos_key, 0) + 1
            capped_evidence.append(e)
        unique_evidence = capped_evidence
        # ⛔ 2026-08-14：实体对齐去噪（与 query() 同款），叙事类放行历程词汇句
        unique_evidence = _filter_evidence_by_entity(
            unique_evidence, question,
            allow_history_vocab=bool(_NARRATIVE_QUESTION_RE.search(question)),
        )
        evidence_block = ""
        if unique_evidence:
            ev_lines = []
            for e in unique_evidence[:12]:
                loc = _format_evidence_loc(e)
                head = f"· 《{e.get('file_name') or e.get('physical_name') or '未知来源'}》"
                if loc:
                    head += f" {loc}"
                line = f"{head}：「{e['text']}」"
                window = e.get("window") or ""
                if window and window != e["text"]:
                    line += f"\n    （上下文：{window}）"
                ev_lines.append(line)
            evidence_block = (
                "\n\n[关键句证据] 以下是与问题直接相关的原文句子（含定位）；"
                "用户要求找句子/关键词/原句时，请优先原样引用并标注《书名》页码段落：\n"
                + "\n".join(ev_lines)
            )

        context = "\n\n---\n\n".join(context_parts)
        graph_context = _build_graph_context(search_results)
        # ⛔ 2026-08-14 通用性：总括类单文件注入目录/章节标题（与 query() 同款）
        if overview_results is not None:
            heads = ((self.kb._file_headings or {}).get(file_ids[0]) or [])
            titles = [h.get("text") for h in heads
                      if isinstance(h, dict) and h.get("text")]
            if titles:
                head_block = (
                    "[全书结构参考] 该书目录/章节标题（回答\"有哪些\"类问题时"
                    "请按此目录逐条列举并标注来源）：\n"
                    + "\n".join("· " + str(t) for t in titles[:60])
                )
                context = head_block + "\n\n---\n\n" + context
        inferred_domain = domain

        top_score = float(search_results[0].get("score", 0.0) or 0.0)
        weak_evidence = top_score < _WEAK_EVIDENCE_THRESHOLD
        enhanced_prompt = _build_answer_prompt(
            question, context, graph_context, evidence_block, weak_evidence,
            single_doc=bool(file_ids and len(file_ids) == 1),
        )

        yield {"type": "sources", "sources": sources, "domain": inferred_domain,
               "evidence": unique_evidence[:12], "weak_evidence": weak_evidence}

        try:
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": enhanced_prompt},
                ],
                temperature=0.3, max_tokens=2048, timeout=60, stream=True
            )
            for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield {"type": "chunk", "content": delta.content}
        except Exception as e:
            yield {"type": "error", "content": f"\n\n[LLM调用失败: {str(e)}]"}

    def summarize_chunks(
        self,
        chunks: List[Dict[str, Any]],
        *,
        scope_label: str,
        analysis_focus: str = "summary",
        max_highlights: int = 4,
    ) -> Dict[str, Any]:
        cleaned_chunks = [chunk for chunk in chunks if (chunk.get("text") or "").strip()]
        if not cleaned_chunks:
            return {
                "title": f"{scope_label} 摘要",
                "summary": "当前范围内没有可用于总结的有效文本。",
                "highlights": [],
                "meta": {
                    "source_mode": "empty-indexed-scope",
                    "analysis_focus": analysis_focus,
                    "selected_chunk_count": 0,
                },
            }

        focus_instructions = {
            "summary": "提炼主要内容、核心结论和最值得先读的部分。",
            "topics": "提炼 3 个以内的主要主题，并简述每个主题各自覆盖的内容。",
            "risks": "优先指出关键风险、不确定点和需要补看的地方。",
        }
        instruction = focus_instructions.get(analysis_focus, focus_instructions["summary"])

        context_parts = []
        for index, chunk in enumerate(cleaned_chunks[:12], start=1):
            page_number = chunk.get("page_number") or ""
            chunk_index = chunk.get("chunk_index") or 0
            location = f"第 {page_number} 页 · 第 {chunk_index} 段" if page_number else f"第 {chunk_index} 段"
            context_parts.append(
                f"[资料{index}] 来源：{chunk.get('source_file', '未知文件')} ({location})\n{chunk.get('text', '')}"
            )
        context = "\n\n---\n\n".join(context_parts)
        prompt = (
            f"请基于以下已索引资料，为「{scope_label}」生成一份结构化中文摘要。\n\n"
            "请按以下结构输出（使用中文）：\n"
            "1. **核心发现**：提炼2-3个最重要的发现或结论，每个发现后面标注对应的资料编号\n"
            "2. **关键概念**：列出3-5个关键概念及其简要解释（不是泛泛而谈，要基于实际内容）\n"
            "3. **关联关系**：说明这些概念之间的内在联系和逻辑关系\n"
            "4. **知识缺口**：指出资料中没有覆盖但相关的重要内容（2-3项）\n\n"
            "要求：\n"
            "- 给出具体而非泛泛的摘要，引用资料中的实际内容\n"
            f"- {instruction}\n"
            "- 不要编造未出现的信息，资料不足要明确写出\n\n"
            f"资料：\n{context}"
        )

        ANALYSIS_SYSTEM_PROMPT = """你是一位专业的知识分析师，擅长从文档中提取深层洞察并给出结构化分析。
请始终使用中文回答。你的分析应该：
- 抓住核心而非表面内容
- 找出概念之间的联系
- 识别知识的盲区和缺口
- 给出有实际价值的总结"""

        def build_fallback_summary() -> str:
            lead_files = []
            for chunk in cleaned_chunks[: min(3, len(cleaned_chunks))]:
                source_file = chunk.get("source_file", "未知文件")
                if source_file not in lead_files:
                    lead_files.append(source_file)
            file_part = "、".join(lead_files) if lead_files else scope_label
            return f"{scope_label} 当前已基于 {file_part} 的已索引内容做最小摘要；后续可继续补更深层的对比、风险和主题归纳。"

        try:
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=800, timeout=60,
            )
            summary_text = (response.choices[0].message.content or "").strip()
            source_mode = "llm-backed-indexed-scope"
        except Exception as e:
            import logging
            logging.getLogger("ai_kb.rag_engine").warning(f"LLM summarize failed: {e}")
            summary_text = build_fallback_summary()
            source_mode = "extractive-fallback-indexed-scope"
            import logging
            logging.getLogger("ai_kb.rag_engine").warning(f"LLM call failed, using fallback: {e}")

        highlights = []
        seen = set()
        for chunk in cleaned_chunks:
            text = " ".join((chunk.get("text") or "").split())
            if not text:
                continue
            snippet = text[:100].strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                highlights.append(snippet)
            if len(highlights) >= max_highlights:
                break

        return {
            "title": f"{scope_label} 摘要",
            "summary": summary_text or build_fallback_summary(),
            "highlights": highlights,
            "meta": {
                "source_mode": source_mode,
                "analysis_focus": analysis_focus,
                "selected_chunk_count": len(cleaned_chunks),
            },
        }

    def extract_cross_file_topics(
        self,
        chunks: List[Dict[str, Any]],
        *,
        scope_label: str,
        file_names: List[str],
        max_topics: int = 5,
    ) -> Dict[str, Any]:
        """跨文件主题提炼：分析多个文件的chunk，提取共同主题及每主题的来源文件映射"""
        cleaned = [c for c in chunks if (c.get("text") or "").strip()]
        if len(cleaned) < 2:
            return {
                "title": f"{scope_label} 跨文件主题",
                "topics": [],
                "meta": {"source_mode": "empty-indexed-scope", "topic_count": 0},
            }

        context_parts = []
        for i, chunk in enumerate(cleaned[:15], start=1):
            src = chunk.get("source_file", "未知")
            context_parts.append(f"[资料{i}] {src}\n{chunk.get('text', '')}")
        context = "\n\n---\n\n".join(context_parts)

        prompt = (
            f"请分析以下{len(cleaned)}个资料片段（来自{len(file_names)}个文件：{'、'.join(file_names[:5])}），"
            f"提取{max_topics}个以内的跨文件共同主题。\n"
            "输出JSON数组，每个元素包含：\n"
            "- topic: 主题名（10字以内）\n"
            "- summary: 主题简要说明（30-60字）\n"
            "- source_files: 该主题涉及的文件名列表\n"
            "- key_points: 2-3个关键点\n"
            "不要编造未出现的信息，资料不足以提取主题时返回空数组。\n\n"
            f"资料：\n{context}"
        )

        try:
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1000, timeout=60,
            )
            raw = (response.choices[0].message.content or "").strip()
            # Try to parse JSON from response
            import json as _json
            topics = []
            try:
                # Find JSON array in response
                start = raw.find("[")
                end = raw.rfind("]") + 1
                if start >= 0 and end > start:
                    topics = _json.loads(raw[start:end])
            except Exception:
                pass
            source_mode = "llm-backed-cross-file-topics"
        except Exception:
            topics = []
            source_mode = "extractive-fallback-indexed-scope"

        # Build extractive fallback topics if LLM failed
        if not topics:
            source_mode = "extractive-fallback-indexed-scope"
            file_topics = {}
            for chunk in cleaned:
                src = chunk.get("source_file", "未知")
                if src not in file_topics:
                    file_topics[src] = []
                text = (chunk.get("text") or "")[:80].strip()
                if text:
                    file_topics[src].append(text)
            topics = [
                {
                    "topic": f"来自 {src}",
                    "summary": "基于已索引片段的提取（LLM不可用，使用回退模式）",
                    "source_files": [src],
                    "key_points": file_topics[src][:2] if src in file_topics else [],
                }
                for src in list(file_topics.keys())[:max_topics]
            ]

        return {
            "title": f"{scope_label} 跨文件主题",
            "topics": topics[:max_topics],
            "meta": {
                "source_mode": source_mode,
                "topic_count": len(topics),
                "max_topics": max_topics,
                "selected_chunk_count": len(cleaned),
                "file_count": len(file_names),
            },
        }

    def get_analysis_scope(
        self,
        *,
        file_ids: Optional[List[str]] = None,
        domain: Optional[str] = None,
        max_files: int = 3,
        max_chunks_per_file: int = 3,
    ) -> Dict[str, Any]:
        """Keep routes on a single facade instead of reaching into kb internals."""
        return self.kb.get_analysis_scope(
            file_ids=file_ids or [],
            domain=domain,
            max_files=max_files,
            max_chunks_per_file=max_chunks_per_file,
        )

    def get_diff_scope(
        self,
        *,
        file_id_a: str,
        file_id_b: str,
        max_chunks_per_file: int = 5,
    ) -> Dict[str, Any]:
        """获取两个文件的chunks，用于语义对比。"""
        chunks_a = self.kb.get_chunks_by_file(file_id_a, max_chunks=max_chunks_per_file)
        chunks_b = self.kb.get_chunks_by_file(file_id_b, max_chunks=max_chunks_per_file)
        file_meta_a = self.kb.get_file_metadata(file_id_a)
        file_meta_b = self.kb.get_file_metadata(file_id_b)
        return {
            "chunks_a": chunks_a,
            "chunks_b": chunks_b,
            "file_meta_a": file_meta_a,
            "file_meta_b": file_meta_b,
        }

    def generate_semantic_diff(
        self,
        *,
        chunks_a: List[Dict],
        chunks_b: List[Dict],
        file_name_a: str,
        file_name_b: str,
        max_changes: int = 5,
    ) -> Dict[str, Any]:
        """用LLM生成两个文件之间的语义级差异描述。"""
        context_a = "\n\n".join(chunk.get("text", "") for chunk in chunks_a[:5])
        context_b = "\n\n".join(chunk.get("text", "") for chunk in chunks_b[:5])

        system_prompt = """你是一个语义对比助手。请使用中文回答。请对比两份资料的内容，输出人类可读的语义级差异描述。
要求：
1. 不是逐行diff，而是语义级变更描述
2. 每条差异包含类型和描述
3. 类型只能是：added（新增）、removed（删除）、modified（修改）
4. 输出JSON数组，每条包含type和description字段
5. 最多输出{max_changes}条差异
6. 如果两份资料非常相似，similarity_score应该接近1.0""".format(max_changes=max_changes)

        user_prompt = f"""请对比以下两份资料，输出语义级差异：

资料A：{file_name_a}
{context_a}

资料B：{file_name_b}
{context_b}

请输出JSON格式：
[
  {{"type": "added", "description": "新增了..."}},
  {{"type": "modified", "description": "修改了..."}}
]

同时输出一个similarity_score（0-1之间，1表示完全相同）。"""

        def build_fallback_diff():
            return {
                "changes": [
                    {"type": "modified", "description": f"{file_name_a} 和 {file_name_b} 的内容存在差异，但LLM未返回结构化对比。"}
                ],
                "similarity_score": 0.5,
            }

        try:
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=800, timeout=60,
            )
            import json
            from app.rag_app.llm_client_factory import parse_llm_json
            content = (response.choices[0].message.content or "").strip()
            # 尝试解析JSON：统一走容错解析（LLM 输出常见尾逗号/markdown 围栏）
            parsed = parse_llm_json(content)
            changes = parsed if isinstance(parsed, list) else build_fallback_diff()["changes"]
            # 确保changes是列表
            if not isinstance(changes, list):
                changes = build_fallback_diff()["changes"]
            # 限制条数
            changes = changes[:max_changes]
            # 尝试提取similarity_score
            similarity_score = 0.5
            if '"similarity_score"' in content:
                try:
                    score_str = content.split('"similarity_score"')[1].split(":")[1].split(",")[0].strip()
                    similarity_score = float(score_str)
                except Exception:
                    pass
            source_mode = "llm-backed-indexed-scope"
        except Exception:
            changes = build_fallback_diff()["changes"]
            similarity_score = 0.5
            source_mode = "extractive-fallback-indexed-scope"

        return {
            "diff_title": f"{file_name_a} vs {file_name_b} 语义对比",
            "changes": changes,
            "similarity_score": similarity_score,
            "meta": {
                "source_mode": source_mode,
                "file_a": file_name_a,
                "file_b": file_name_b,
                "chunk_count_a": len(chunks_a),
                "chunk_count_b": len(chunks_b),
            },
        }

    def build_knowledge_tree(self, file_ids=None, max_depth=3, focus_area=None):
        """
        构建知识框架树
        
        参数:
            file_ids: 文件ID列表，为None则使用所有已索引文件
            max_depth: 树的最大深度
            focus_area: 聚焦领域（可选）
            
        返回:
            知识框架树字典
        """
        import time
        from datetime import datetime
        
        # 1. 获取chunks
        if file_ids and len(file_ids) > 0:
            chunks = []
            for file_id in file_ids:
                chunks.extend(self.kb.get_chunks_by_file(file_id))
        else:
            # 获取所有已索引文件，使用list_files()代替stats
            files = self.kb.list_files()
            chunks = []
            for file_info in files[:10]:  # 限制最多10个文件，避免超时
                file_id = file_info.get("id", file_info.get("physical_name", ""))
                if file_id:
                    chunks.extend(self.kb.get_chunks_by_file(file_id))
        
        if not chunks:
            files_list = self.kb.list_files()
            if files_list:
                tree_data = self._build_rule_based_tree(
                    [{"text": f"{f['name']} (文件)", "metadata": {"file_name": f['name']}} for f in files_list],
                    max_depth
                )
                source_mode = "rule-based-fallback"
                insights = [
                    f"基于 {len(files_list)} 个已注册文件生成（文件尚未索引，内容为占位）",
                    "建议在设置页执行文档扫描以丰富知识框架"
                ]
                return {
                    "tree_id": f"kt_{int(time.time())}",
                    "title": "{}知识框架树".format(f"{focus_area} - " if focus_area else ""),
                    "generated_at": datetime.now().isoformat(),
                    "meta": {
                        "source_mode": source_mode,
                        "file_count": len(files_list),
                        "chunk_count": 0,
                        "max_depth": max_depth,
                        "focus_area": focus_area
                    },
                    "tree": tree_data,
                    "insights": insights
                }
            else:
                return {
                    "tree_id": f"kt_{int(time.time())}",
                    "title": "知识框架树",
                    "generated_at": datetime.now().isoformat(),
                    "meta": {
                        "source_mode": "no-data",
                        "file_count": 0,
                        "chunk_count": 0,
                        "max_depth": max_depth,
                        "focus_area": focus_area
                    },
                    "tree": {"root": {"id": "root", "label": "知识库", "children": []}},
                    "insights": ["暂无已索引的文件，请先扫描文件夹"]
                }
        
        # 2. 提取文本：按文档均分配额（每篇至少 400 字），确保所有参与文档都进 prompt；
        # 旧逻辑拼接后直接 [:8000] 截断，多文档时只有排序靠前的第一篇可见，"多文档分析"名存实亡
        by_file = {}
        for chunk in chunks:
            fname = (chunk.get("metadata") or {}).get("file_name") or "未知文件"
            by_file.setdefault(fname, []).append(chunk.get("text", ""))
        budget = max(400, 8000 // max(1, len(by_file)))
        all_text = "\n".join("\n".join(texts)[:budget] for texts in by_file.values())[:8000]
        
        # 3. 使用LLM生成知识框架树
        try:
            # 构造prompt
            system_prompt = """你是一个知识组织专家和教育者。请使用中文回答。请将以下内容组织成层次化的知识框架树，使读者可以直接用于学习。

要求：
1. 输出严格的JSON格式，包含root节点和children数组
2. 树的深度不超过{}层
3. 每个节点包含：id、label（10字以内）、description（30字以内的简要说明）、children
4. 根节点id为"root"，label为"知识库"
5. description保持简短（≤30字），确保JSON输出完整不截断
6. 只返回JSON，不要有任何其他文字""".format(max_depth)
            
            focus_instruction = f"请特别关注{focus_area}领域" if focus_area else ""
            user_prompt = f"""请分析以下内容，生成知识框架树：

{all_text}

{focus_instruction}
生成严格的JSON格式知识框架树。"""
            
            # ⛔ 2026-08-18：大文档 JSON 树 4000 token 易被截断 → 提到 6000，
            # 截断时（finish_reason=length）自动加大上限重试 1 次
            content, tree_reason = self._llm_chat_retry(
                system_prompt, user_prompt, max_tokens=6000, timeout=120,
                attempts=2, retry_on_truncation=True, truncation_boost=2000,
            )
            if tree_reason == "length":
                logger.warning("知识框架树LLM输出重试后仍被截断(finish_reason=length)，可能解析失败")

            import json
            from app.rag_app.llm_client_factory import parse_llm_json

            # 解析JSON：统一走容错解析（LLM 常输出 markdown 围栏/尾逗号/未加引号键）
            parsed = parse_llm_json(content)
            if isinstance(parsed, dict) and "root" in parsed:
                tree_data = parsed
            elif isinstance(parsed, dict):
                tree_data = {"root": parsed}
            else:
                tree_data = {"root": {"id": "root", "label": "知识库", "children": []}}

            # 确保tree_data包含root节点
            if "root" not in tree_data:
                tree_data = {"root": tree_data}

            # T40：LLM 空树/脏树兜底——清洗节点结构；整树无效（空 children）则回退规则树
            cleaned = self._sanitize_tree(tree_data, max_depth)
            if cleaned is None:
                tree_data = self._build_rule_based_tree(chunks, max_depth)
                insights = ["LLM 生成了空树，已回退为基础版知识框架树", "建议补充更多相关领域知识"]
                source_mode = "rule-based-fallback"
            else:
                tree_data = cleaned
                # 4. 生成洞察
                insights = self._generate_tree_insights(tree_data, len(chunks))
                source_mode = "llm-generated"
            
        except Exception as e:
            logger.warning("知识框架树LLM生成失败: %s", e)
            logger.exception("知识框架树LLM生成失败详情", exc_info=True)
            # 回退到规则版
            tree_data = self._build_rule_based_tree(chunks, max_depth)
            insights = ["由于LLM不可用，已生成基础版知识框架树", "建议补充更多相关领域知识"]
            source_mode = "rule-based-fallback"
        
        return {
            "tree_id": f"kt_{int(time.time())}",
            "title": "{}知识框架树".format(f"{focus_area} - " if focus_area else ""),
            "generated_at": datetime.now().isoformat(),
            "meta": {
                "source_mode": source_mode,
                "file_count": len(file_ids) if file_ids else self.kb.get_stats().get("file_count", 0),
                "chunk_count": len(chunks),
                "max_depth": max_depth,
                "focus_area": focus_area
            },
            "tree": tree_data,
            "insights": insights
        }
    
    def _sanitize_tree(self, tree_data, max_depth):
        """T40：清洗/规整 LLM 输出的知识框架树
        - 过滤非 dict、缺 label 的脏节点（LLM 偶发输出异常结构）
        - 限制深度与每层 children 数量，补默认 description
        - 返回 None 表示整树无效（root 缺失或空 children）→ 调用方回退规则树
        """
        counter = [0]

        def clean(node, depth):
            if not isinstance(node, dict):
                return None
            label = str(node.get("label", "")).strip()
            if not label:
                return None
            counter[0] += 1
            children = node.get("children") or []
            valid = []
            if isinstance(children, list) and depth < max_depth:
                for c in children[:30]:
                    cleaned = clean(c, depth + 1)
                    if cleaned:
                        valid.append(cleaned)
            return {
                "id": str(node.get("id") or f"n{counter[0]}"),
                "label": label[:20],
                "description": str(node.get("description", ""))[:200] or f"关于「{label}」的知识点",
                "children": valid,
            }

        if not isinstance(tree_data, dict):
            return None
        root = tree_data.get("root")
        cleaned_root = clean(root, 0) if isinstance(root, dict) else None
        if cleaned_root is None or not cleaned_root.get("children"):
            return None
        return {"root": cleaned_root}

    def _generate_tree_insights(self, tree_data, chunk_count):
        """生成知识框架树的洞察"""
        insights = []
        
        # 统计节点数量
        def count_nodes(node):
            count = 1
            for child in node.get("children", []):
                count += count_nodes(child)
            return count
        
        root = tree_data.get("root", {})
        node_count = count_nodes(root) - 1  # 减去root节点
        
        insights.append(f"知识框架树包含{node_count}个知识点")
        insights.append(f"基于{chunk_count}个文本块分析生成")
        
        if node_count < 5:
            insights.append("建议补充更多相关领域知识，以构建更完整的知识框架")
        
        return insights
    
    def _build_rule_based_tree(self, chunks, max_depth):
        """基于规则构建知识框架树（LLM不可用时的回退方案）"""
        # 简单实现：按文件名分组
        file_groups = {}
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            file_name = metadata.get("file_name", "未知文件")
            if file_name not in file_groups:
                file_groups[file_name] = []
            file_groups[file_name].append(chunk)
        
        # 构建树
        children = []
        for i, (file_name, file_chunks) in enumerate(file_groups.items()):
            children.append({
                "id": f"node_{i}",
                "label": file_name,
                "description": f"包含{len(file_chunks)}个文本块",
                "file_count": 1,
                "children": []
            })
        
        return {
            "root": {
                "id": "root",
                "label": "知识库",
                "children": children
            }
        }
