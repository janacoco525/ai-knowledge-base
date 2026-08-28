"""T42 回归测试：/api/ai/summarize、/api/ai/generate-cards、/api/ai/define-term 核心逻辑。

覆盖：结构化摘要、记忆闪卡 JSON 解析、上下文术语定义；LLM 客户端用桩（无网络）。
端点契约（与前端调用点对齐）：
  - POST /api/ai/summarize      {title, content}            -> {summary}
  - POST /api/ai/generate-cards {docId, title, content}     -> {cards: [{docId, front, back, tags}]}
  - POST /api/ai/define-term    {term, context}             -> {definition}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.routes.chat_ops import _ddgs_search
from app.rag_app.routes.llm_ops import (
    _do_summarize,
    _do_generate_cards,
    _do_define,
)


class FakeChoice:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class FakeCompletions:
    def __init__(self, content: str | list[str]):
        self._contents = list(content) if isinstance(content, list) else [content]

    def create(self, **kwargs):
        content = self._contents.pop(0) if self._contents else ""
        return type("R", (), {"choices": [FakeChoice(content)]})()


class FakeChat:
    def __init__(self, content: str):
        self.completions = FakeCompletions(content)


class FakeLLM:
    def __init__(self, content: str = "", contents: list[str] | None = None):
        seq = list(contents or [])
        if content:
            seq.insert(0, content)
        self.chat = FakeChat(seq)


class FakeKB:
    def __init__(self):
        self.cache = {}
        self.chunks = []

    def get_llm_cache(self, key, kind):
        return self.cache.get((key, kind))

    def set_llm_cache(self, key, kind, value):
        self.cache[(key, kind)] = value

    def get_chunks_by_file(self, fid, max_chunks=5):
        return self.chunks[:max_chunks]


class FakeEngine:
    def __init__(self, llm):
        self.llm_client = llm
        self.model_name = "fake-model"
        self.kb = FakeKB()


def test_summarize_returns_structured_summary():
    eng = FakeEngine(FakeLLM("【核心主题】测试\n【要点】1. 要点A\n【结论/启示】结论"))
    summary = _do_summarize(eng, "标题", "正文内容")
    assert "核心主题" in summary
    assert eng.kb.cache  # 结果已缓存


def test_generate_cards_parses_json_array():
    raw = '[{"front": "什么是X？", "back": "X是Y。", "tags": ["概念"]}, {"front": "Q2", "back": "A2"}]'
    eng = FakeEngine(FakeLLM(raw))
    cards = _do_generate_cards(eng, "标题", "正文", doc_id="doc-1")
    assert len(cards) == 2
    assert cards[0]["docId"] == "doc-1"
    assert cards[0]["front"] == "什么是X？"
    assert cards[0]["back"] == "X是Y。"
    assert cards[0]["tags"] == ["概念"]


def test_generate_cards_falls_back_when_llm_garbage():
    eng = FakeEngine(FakeLLM("不是 JSON"))
    cards = _do_generate_cards(eng, "标题", "正文")
    assert cards == []


def test_generate_cards_retries_once_when_first_parse_fails():
    """首次返回不可解析内容 → 自动重试一次拿到合法 JSON（2049 偶发 0 张根因）。"""
    raw_ok = '[{"front": "Q1", "back": "A1", "tags": ["概念"]}]'
    eng = FakeEngine(FakeLLM(contents=["不是 JSON", raw_ok]))
    cards = _do_generate_cards(eng, "标题", "正文")
    assert len(cards) == 1
    assert cards[0]["front"] == "Q1"


def test_sample_doc_uniform_covers_head_middle_tail():
    """全书等距采样：首/中/尾都覆盖（长文档首部截断会漏核心知识点 → 0 张根因）。"""
    from app.rag_app.routes.llm_ops import _sample_doc_uniform
    eng = FakeEngine(FakeLLM("[]"))
    eng.kb.chunks = [
        {"text": f"第{i}块" + "x" * 100, "metadata": {"chunk_index": i, "page_number": i}}
        for i in range(50)
    ]
    sample = _sample_doc_uniform(eng, "doc-1")
    assert "第0块" in sample        # 首部覆盖
    assert "第49块" in sample       # 尾部覆盖
    assert any(f"第{i}块" in sample for i in (24, 25, 26))  # 中部覆盖
    assert len(sample) <= 30000
    # 采样应远小于全量（50 块 × ~104 字符）
    assert len(sample) < 5000


def test_generate_cards_with_docid_prefers_uniform_sample():
    """有 docId 时后端走等距采样而非传入内容（前端只传标识的契约）。"""
    eng = FakeEngine(FakeLLM('[{"front": "Q", "back": "A", "tags": ["概念"]}]'))
    eng.kb.chunks = [
        {"text": f"块{i}内容" + "y" * 50, "metadata": {"chunk_index": i}}
        for i in range(20)
    ]
    cards = _do_generate_cards(eng, "标题", "旧的占位内容", doc_id="doc-1")
    assert len(cards) == 1
    assert cards[0]["docId"] == "doc-1"
    # 采样结果参与生成（空/旧内容被采样覆盖）；缓存键为 v2
    assert any(k.startswith("ai-cards-v2:") for k, _ in eng.kb.cache)


def test_parse_llm_json_markdown_fenced_array():
    """回归：```json 围栏包裹的多元素数组必须解析为完整 list。
    旧实现先试 {…} 贪婪匹配最后一个 } → 数组被误拆成最后一个对象 → 卡片 0 张。"""
    from app.rag_app.llm_client_factory import parse_llm_json
    raw = (
        "```json\n[\n"
        '  {"front": "Q1", "back": "A1", "tags": ["t1"]},\n'
        '  {"front": "Q2", "back": "A2", "tags": ["t2"]}\n'
        "]\n```"
    )
    parsed = parse_llm_json(raw)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["front"] == "Q1"
    assert parsed[1]["front"] == "Q2"


def test_parse_llm_json_object_with_nested_array():
    """对象包数组（{"cards": [...]}）仍走对象优先路径，不被误判。"""
    from app.rag_app.llm_client_factory import parse_llm_json
    raw = '{"cards": [{"front": "Q1", "back": "A1"}, {"front": "Q2", "back": "A2"}]}'
    parsed = parse_llm_json(raw)
    assert isinstance(parsed, dict)
    assert len(parsed["cards"]) == 2


def test_define_term_returns_definition():
    eng = FakeEngine(FakeLLM("Transformer 是一种基于自注意力机制的序列模型架构。"))
    definition = _do_define(eng, "Transformer", "本文讨论 Transformer 架构。")
    assert "自注意力" in definition
    assert eng.kb.cache


def test_llm_missing_raises_503():
    import pytest
    from fastapi import HTTPException
    eng = FakeEngine(None)
    with pytest.raises(HTTPException) as exc:
        _do_summarize(eng, "t", "c")
    assert exc.value.status_code == 503


# ===== 免费联网搜索（ddgs）容错测试：不访问真实网络 =====

def test_ddgs_search_filters_and_structures_results(monkeypatch):
    """成功路径：只保留有 title+href 的结果，字段裁剪到 title/uri/body。"""
    import sys
    import app.rag_app.routes.chat_ops as chat_ops

    class FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=5, backend="ddg"):
            return [
                {"title": "真实标题", "href": "https://example.com/a", "body": "这是一段足够长的真实摘要内容，超过十个字"},
                {"title": "   ", "href": "https://example.com/bad", "body": "空标题应被过滤"},
                {"title": "无链接", "href": "", "body": "无链接应被过滤"},
            ]

    fake_module = type(sys)("fake_ddgs")
    fake_module.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    out = chat_ops._ddgs_search("问题", max_results=5)
    assert len(out) == 1
    assert out[0]["uri"] == "https://example.com/a"
    assert out[0]["body"].startswith("这是一段足够长的真实摘要内容")


def test_ddgs_search_filters_blank_and_spam(monkeypatch):
    """垃圾过滤：空白页（body 空/过短）、成人/博彩类结果被剔除，正常结果保留。"""
    import sys
    import app.rag_app.routes.chat_ops as chat_ops

    class FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=5, backend="ddg"):
            return [
                {"title": "正常百科条目", "href": "https://zh.wikipedia.org/wiki/西琴", "body": "撒迦利亚·西琴是《地球编年史》作者，主张外星生物创造论"},
                {"title": "空白页", "href": "https://example.com/blank", "body": ""},
                {"title": "摘要太短", "href": "https://example.com/short", "body": "短"},
                {"title": "成人视频", "href": "https://porn.example.com/v", "body": "这里有很多成人内容视频资源"},
                {"title": "赌场优惠", "href": "https://casino.example.com", "body": "在线赌场注册即送彩金博彩活动"},
            ]

    fake_module = type(sys)("fake_ddgs")
    fake_module.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    out = chat_ops._ddgs_search("西琴", max_results=5)
    assert len(out) == 1
    assert "wikipedia" in out[0]["uri"]


def test_ddgs_search_failure_returns_empty(monkeypatch):
    """异常/限流路径：任何异常都降级为 []，不抛出、不阻塞主链路。"""
    import sys
    import app.rag_app.routes.chat_ops as chat_ops

    class BoomDDGS:
        def __init__(self, *a, **k):
            raise RuntimeError("network down / rate limited")

    fake_module = type(sys)("fake_ddgs")
    fake_module.DDGS = BoomDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_module)

    assert chat_ops._ddgs_search("问题") == []


# ===== 回答质量补强：文档统计 + 句子级定位（2026-08-13） =====

def test_split_sentences_chinese():
    """中文分句：句末标点保留、空句剔除。"""
    from app.rag_app.knowledge_base import _split_sentences
    text = "第一句。第二句！第三句？第四句；第五句…\n\n新段落。"
    sents = _split_sentences(text)
    assert sents[0] == "第一句。"
    assert "第二句！" in sents
    assert "新段落。" in sents


def test_stats_question_detection():
    from app.rag_app.rag_engine import _is_stats_question
    assert _is_stats_question("这本书一共有多少字？")
    assert _is_stats_question("全文多少页")
    assert _is_stats_question("这本书多长")
    assert not _is_stats_question("这本书讲了什么？")
    assert not _is_stats_question("这个句子是什么意思？")


def test_rag_engine_stats_answer_without_llm():
    """统计类问题：直接由 get_library_stats 回答，不调用 LLM。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def get_library_stats(self, file_ids=None):
            return {
                "files": [{
                    "file_name": "测试书", "physical_name": "t.epub",
                    "total_chars": 120000, "chinese_chars": 100000, "words": 60000,
                    "sentences": 5000, "paragraphs": 3000, "pages": 400, "chapters": 20,
                }],
                "totals": {"total_chars": 120000, "chinese_chars": 100000, "words": 60000,
                           "sentences": 5000, "paragraphs": 3000, "pages": 400},
                "file_count": 1,
            }

    class FakeLLM:
        def __init__(self):
            self.completions = None

    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = FakeKB()
    eng.config = type("C", (), {})()
    eng.llm_client = FakeLLM()
    eng.model_name = "fake"
    res = eng.query("这本书一共有多少字？", top_k=5)
    assert res.get("stats_answer") is True
    assert "120,000" in res["answer"]
    assert "400" in res["answer"]


def test_find_exact_sentences_locations():
    """全文精确扫描：返回包含关键词的原句 + 页码/段落定位。"""
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._embed_texts = [
        "第一段内容：这是关键原句，涉及深度神经网络训练。",
        "另一段无关内容。",
    ]
    kb._embed_metadatas = [
        {"file_name": "测试书", "physical_name": "t.epub", "page_number": 3, "chunk_index": 0},
        {"file_name": "测试书", "physical_name": "t.epub", "page_number": 4, "chunk_index": 1},
    ]
    hits = kb.find_exact_sentences("深度神经网络", top_k=5)
    assert hits
    assert hits[0]["page_number"] == 3
    assert "深度神经网络" in hits[0]["text"]


def test_extract_sentence_evidence():
    """句子级证据：从检索 chunk 中提取含查询词的原句。"""
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    results = [{
        "text": "这是第一句。这里提到了强化学习与奖励函数。这是第三句。",
        "metadata": {"file_name": "测试书", "physical_name": "t.epub",
                     "page_number": 1, "chunk_index": 0},
    }]
    ev = kb.extract_sentence_evidence(results, "强化学习")
    assert ev
    assert "强化学习" in ev[0]["text"]


# ===== 批次 A：MMR 多样性重排 + 多查询融合/改写（2026-08-13） =====

def test_mmr_select_picks_diverse():
    """MMR：两个相似簇中各选一个，避免 top_k 全是重复片段。"""
    import numpy as np
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    # 4 个向量：前两个几乎相同（簇A），后两个几乎相同（簇B）
    kb._embeddings = np.array([
        [1.0, 0.0], [0.99, 0.01], [0.0, 1.0], [0.01, 0.99],
    ])
    candidates = [
        {"text": "A1", "emb_idx": 0, "rrf_score": 0.10},
        {"text": "A2", "emb_idx": 1, "rrf_score": 0.09},
        {"text": "B1", "emb_idx": 2, "rrf_score": 0.08},
        {"text": "B2", "emb_idx": 3, "rrf_score": 0.07},
    ]
    selected = kb._mmr_select(candidates, top_k=2)
    texts = [c["text"] for c in selected]
    assert "A1" in texts and "B1" in texts, f"MMR 应跨簇选择，实际 {texts}"


def test_query_phrase_terms_extracts_entities():
    """专名词条提取：两字专名（西琴）与长查询实体词都被提取，衬词被剔除。"""
    from app.rag_app.knowledge_base import _query_phrase_terms
    assert _query_phrase_terms("西琴") == ["西琴"]
    terms = _query_phrase_terms("帮我找出桥水基金创建的背景")
    assert "桥水" in terms
    assert "帮我" not in terms and "背景" not in terms
    terms2 = _query_phrase_terms("为什么叶文洁要提醒罗辑")
    assert "叶文洁" in terms2 and "罗辑" in terms2
    assert "为什么" not in terms2


def test_apply_phrase_bonus_boosts_exact_entity_hit():
    """专名精确命中加权：含实体词条的候选被加分，未命中者不加分。"""
    from app.rag_app.knowledge_base import _apply_phrase_bonus
    candidates = [
        {"text": "撒迦利亚·西琴在《地球编年史》系列图书中回答的远不止这些", "rrf_score": 0.005},
        {"text": "目标距琴三公里！古筝行动开始", "rrf_score": 0.007},
    ]
    _apply_phrase_bonus(candidates, "西琴")
    assert candidates[0]["rrf_score"] > candidates[1]["rrf_score"]
    assert candidates[1]["rrf_score"] == 0.007


def test_query_passes_retrieval_queries_and_diversity():
    """RAG query：透传检索查询列表并开启 MMR。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def __init__(self):
            self.calls = []

        def search(self, question, top_k=5, domain=None, where_filter=None,
                   queries=None, diversity=False):
            self.calls.append({
                "question": question, "top_k": top_k,
                "queries": queries, "diversity": diversity,
            })
            return []

    class FakeLLM:
        def __init__(self):
            self.completions = None

    kb = FakeKB()
    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = kb
    eng.config = type("C", (), {})()
    eng.llm_client = FakeLLM()
    eng.model_name = "fake"
    res = eng.query("这本书讲了什么？", top_k=8, retrieval_queries=["改写问题", "扩展角度"])
    assert "知识库中未找到与该问题直接相关的内容" in res["answer"]
    call = kb.calls[0]
    assert call["diversity"] is True
    assert call["queries"] == ["改写问题", "扩展角度"]


def test_prepare_retrieval_queries_fallback():
    """改写/HyDE：无 LLM 客户端时降级为原问题列表。"""
    from app.rag_app.routes.chat_ops import _prepare_retrieval_queries
    eng = type("E", (), {"llm_client": None})()
    assert _prepare_retrieval_queries([{"role": "user", "content": "这本书讲了什么？"}], "这本书讲了什么？", eng) == ["这本书讲了什么？"]


def test_query_injects_sentence_evidence_from_rewrite_queries():
    """多轮改写激活时：改写查询也做精确扫描，关键句证据注入 LLM prompt。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def search(self, question, top_k=5, domain=None, where_filter=None,
                   queries=None, diversity=False):
            return [{
                "text": "候选块内容。",
                "metadata": {"file_name": "测试书", "physical_name": "t.epub",
                             "page_number": 1, "chunk_index": 0},
            }]

        def extract_sentence_evidence(self, results, query, max_sentences=8):
            return []

        def find_exact_sentences(self, query, top_k=5, file_ids=None):
            return [{
                "text": "这是关键原句，涉及五步流程。",
                "file_name": "测试书", "physical_name": "t.epub",
                "page_number": 3, "chunk_index": 1,
            }]

    class FakeCompletions:
        def __init__(self):
            self.captured = None

        def create(self, **kwargs):
            self.captured = kwargs
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "OK"})()})()]})()

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    kb = FakeKB()
    chat = FakeChat()
    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = kb
    eng.config = type("C", (), {})()
    eng.llm_client = type("L", (), {"chat": chat})()
    eng.model_name = "fake"
    res = eng.query("帮我找一句话", top_k=8, retrieval_queries=["五步流程的改写查询"])
    assert res["answer"] == "OK"
    prompt = chat.completions.captured["messages"][-1]["content"]
    assert "关键句证据" in prompt
    assert "涉及五步流程" in prompt
    assert "第3页" in prompt
    assert any(e["text"] == "这是关键原句，涉及五步流程。" for e in res["evidence"])


# ===== 批次 B：follow-ups + 流式/证据结构化（2026-08-13） =====

def test_followups_parse_json_array():
    from app.rag_app.routes.chat_ops import _do_followups
    eng = FakeEngine(FakeLLM('["追问一","追问二","追问三"]'))
    ups = _do_followups(eng, "问题", "回答")
    assert ups == ["追问一", "追问二", "追问三"]


def test_followups_garbage_returns_empty():
    from app.rag_app.routes.chat_ops import _do_followups
    eng = FakeEngine(FakeLLM("这不是 JSON"))
    assert _do_followups(eng, "问题", "回答") == []


def test_followups_no_llm_returns_empty():
    from app.rag_app.routes.chat_ops import _do_followups
    eng = FakeEngine(None)
    assert _do_followups(eng, "问题", "回答") == []


# ===== 任务十：反幻觉硬护栏（2026-08-13） =====

def test_strip_invalid_refs_removes_out_of_range():
    """引用校验：越界 [参考资料N]/[网页N] 被剔除，合法引用保留。"""
    from app.rag_app.rag_engine import _strip_invalid_refs
    text = "结论A[参考资料3]结论B[参考资料9]结论C[网页7]"
    cleaned = _strip_invalid_refs(text, max_local=5, max_web=5)
    assert "[参考资料3]" in cleaned
    assert "[参考资料9]" not in cleaned
    assert "[网页7]" not in cleaned


def test_build_answer_prompt_contains_anti_hallucination_rules():
    """回答 prompt 必须含反幻觉铁律；弱证据时追加诚实拒绝警告。"""
    from app.rag_app.rag_engine import _build_answer_prompt
    prompt = _build_answer_prompt("问题", "上下文", "")
    assert "反幻觉铁律" in prompt
    assert "绝对不允许无证据编造" in prompt
    weak = _build_answer_prompt("问题", "上下文", "", weak_evidence=True)
    assert "[警告]" in weak
    assert "知识库未覆盖该问题" in weak
    why = _build_answer_prompt("为什么叶文洁要提醒罗辑？", "上下文", "", evidence_block="\n[关键句证据] 测试句")
    assert "为什么/原因" in why
    assert "不要先声明" in why


def test_build_answer_prompt_contains_answer_quality_rules():
    """回答 prompt 必须含回答质量规范（结论先行/要点精炼/事实推断分离/不绝对化）。"""
    from app.rag_app.rag_engine import _build_answer_prompt
    prompt = _build_answer_prompt("问题", "上下文", "")
    assert "回答质量规范" in prompt
    assert "结论先行" in prompt
    assert "要点精炼" in prompt
    assert "事实与推断分离" in prompt
    assert "不绝对化" in prompt
    # 质量规则不得削弱反幻觉铁律：结论先行不豁免证据要求
    assert "结论先行不豁免反幻觉铁律" in prompt
    assert "反幻觉铁律" in prompt


def test_build_answer_prompt_narrative_mode_for_background_questions():
    """背景/创立/历程类问题触发叙事连贯模式（时间线+因果衔接，禁止碎片罗列）。"""
    from app.rag_app.rag_engine import _build_answer_prompt, _NARRATIVE_QUESTION_RE
    assert _NARRATIVE_QUESTION_RE.search("帮我找出桥水基金创建的背景")
    prompt = _build_answer_prompt("帮我找出桥水基金创建的背景", "上下文", "")
    assert "连贯的因果叙述" in prompt
    assert "起因" in prompt and "关键转折" in prompt
    assert "逐条罗列孤立要点" in prompt
    # 非背景/历程类问题不触发叙事模式
    normal = _build_answer_prompt("三体中的黑暗森林法则是什么", "上下文", "")
    assert "连贯的因果叙述" not in normal


def test_build_answer_prompt_overview_structure_mode():
    """全书总括类问题触发结构骨架模式（先骨架后展开，不平铺散点）；单文件泛指也触发。"""
    from app.rag_app.rag_engine import _build_answer_prompt
    prompt = _build_answer_prompt("这本书到底讲了什么原则", "上下文", "")
    assert "全书总括" in prompt
    assert "结构骨架" in prompt
    assert "不要平铺互不相关的零散要点" in prompt
    assert "总述本书主题" in prompt
    # 勾选单文档问"这里面到底讲了什么"（用户实际场景）→ 触发
    p2 = _build_answer_prompt("这里面到底讲了什么原则", "上下文", "", single_doc=True)
    assert "结构骨架" in p2
    # 非单文件时不触发（避免全库泛泛触发）
    p3 = _build_answer_prompt("这里面到底讲了什么原则", "上下文", "")
    assert "结构骨架" not in p3
    # 具体概念题不误触发
    p4 = _build_answer_prompt("黑暗森林法则的核心内容是什么？", "上下文", "")
    assert "结构骨架" not in p4


def test_is_overview_question_single_doc_generic():
    """总括判定：明确书级指代或单文件'这里面有哪些'触发；非单文件泛指针与概念题不触发。"""
    from app.rag_app.rag_engine import _is_overview_question
    assert _is_overview_question("这本书里面有哪些原则") is True
    assert _is_overview_question("这里面有哪些原则", single_doc=True) is True
    assert _is_overview_question("这里面有哪些原则") is False
    assert _is_overview_question("黑暗森林法则的核心内容是什么？") is False
    # 用户真实问法：单文件 + 纯枚举词（无书级指代）也应触发（此前漏判导致修复未生效）
    assert _is_overview_question("有哪些原则呢", single_doc=True) is True
    assert _is_overview_question("有哪些原则呢") is False


def test_overview_sampling_covers_whole_book():
    """总括类单文件：40 chunks 等距采样 ≤24 个，首尾覆盖、分布均匀、score=1.0。"""
    from app.rag_app.rag_engine import _overview_sampling

    class FakeKB:
        def get_chunks_by_file(self, fid, max_chunks=5):
            return [
                {"text": f"第{i}块内容",
                 "metadata": {"file_name": "书.pdf", "physical_name": fid,
                              "page_number": i, "chunk_index": i, "domain": "default"}}
                for i in range(40)
            ]

    res = _overview_sampling(FakeKB(), "问题", ["书.pdf"])
    assert res is not None
    assert len(res) <= 32
    idxs = [r["metadata"]["chunk_index"] for r in res]
    assert idxs[0] == 0 and idxs[-1] == 39  # 首尾覆盖
    gaps = [idxs[i + 1] - idxs[i] for i in range(len(idxs) - 1)]
    assert max(gaps) <= 2  # 等距均匀
    assert all(r["score"] == 1.0 for r in res)


def test_overview_sampling_budget_adapts_to_chunk_len():
    """长 chunk 预算自适应：14K字符预算下长块（1000字符）只采 ≤14 个，防小上下文挤爆。"""
    from app.rag_app.rag_engine import _overview_sampling

    class FakeKB:
        def get_chunks_by_file(self, fid, max_chunks=5):
            return [
                {"text": "长" * 1000,
                 "metadata": {"file_name": "书.pdf", "physical_name": fid,
                              "page_number": i, "chunk_index": i, "domain": "default"}}
                for i in range(100)
            ]

    res = _overview_sampling(FakeKB(), "问题", ["书.pdf"])
    assert res is not None
    assert len(res) <= 14  # 14000 // 1000 = 14


def test_enumerate_question_re_detects_list_intent():
    """枚举意图判定：有哪些/清单/列出/总结触发 map-reduce；普通总括与概念题不触发。"""
    from app.rag_app.rag_engine import _ENUMERATE_QUESTION_RE
    assert _ENUMERATE_QUESTION_RE.search("这本书里面有哪些原则")
    assert _ENUMERATE_QUESTION_RE.search("这里面有哪些可能呢")
    assert _ENUMERATE_QUESTION_RE.search("列出这本书的所有原则")
    assert _ENUMERATE_QUESTION_RE.search("总结一下这本书的主要内容")
    assert not _ENUMERATE_QUESTION_RE.search("这本书讲了什么")
    assert not _ENUMERATE_QUESTION_RE.search("黑暗森林法则是什么")


def test_overview_group_chunks_partitions_whole_book():
    """map-reduce 分组：405 chunks → 4 组，每组 ≤8 代表，首尾覆盖、组序正确。"""
    from app.rag_app.rag_engine import _overview_group_chunks
    chunks = [
        {"text": f"第{i}块", "metadata": {"chunk_index": i, "page_number": i}}
        for i in range(405)
    ]
    groups, k = _overview_group_chunks(chunks)
    assert k == 4
    assert len(groups) == 4
    assert all(len(g) <= 8 for g in groups)
    all_idxs = [c["metadata"]["chunk_index"] for g in groups for c in g]
    assert 0 in all_idxs and 404 in all_idxs
    assert groups[0][-1]["metadata"]["chunk_index"] < 150
    assert groups[-1][0]["metadata"]["chunk_index"] > 250


def test_filter_evidence_by_entity_removes_noise():
    """证据实体对齐：只匹配到通用动词（创建）/泛词（基金）的噪声句被滤，实体句保留。"""
    from app.rag_app.rag_engine import _filter_evidence_by_entity
    ev = [
        {"text": "2000年，还是中学生的麦修创建了中国关爱基金", "page_number": 201},
        {"text": "桥水基金由瑞·达利欧从公寓的第二间卧室开始创业", "page_number": 30},
        {"text": "还有一家基金会在隔壁城市运作", "page_number": 99},
    ]
    kept = _filter_evidence_by_entity(ev, "帮我找出桥水基金创建的背景")
    assert len(kept) == 1
    assert "桥水" in kept[0]["text"]


def test_filter_evidence_by_entity_fallback_when_no_terms():
    """问题无语义实体词（纯查找动词+停用词）时，证据原样返回，不误伤查找类问题。"""
    from app.rag_app.rag_engine import _filter_evidence_by_entity
    ev = [{"text": "这里创建了一个项目，后来逐步扩大", "page_number": 1}]
    assert _filter_evidence_by_entity(ev, "找包含创建的句子") == ev


def test_filter_evidence_narrative_keeps_history_vocab():
    """叙事类问题放行不含实体名但含历程词汇的关键句；且历程句限定在实体句所在文件内。"""
    from app.rag_app.rag_engine import _filter_evidence_by_entity
    ev = [
        {"text": "在1975年，我创办了桥水。",
         "file_name": "原则.pdf", "physical_name": "原则.pdf", "page_number": 52},
        {"text": "在我一败涂地后，我几乎破产，甚至筹不到足够的钱买飞机票",
         "file_name": "原则.pdf", "physical_name": "原则.pdf", "page_number": 84},
        {"text": "2000年，还是中学生的麦修创建了中国关爱基金",
         "file_name": "原则.pdf", "physical_name": "原则.pdf", "page_number": 201},
        {"text": "这段早期经历让他创办了学校",
         "file_name": "与神对话.epub", "physical_name": "与神对话.epub", "page_number": 88},
    ]
    kept = _filter_evidence_by_entity(
        ev, "帮我找出桥水基金创建的背景", allow_history_vocab=True,
    )
    texts = [e["text"] for e in kept]
    assert any("桥水" in t for t in texts)
    assert any("一败涂地" in t for t in texts)
    assert not any("麦修" in t for t in texts)
    assert all("与神对话" not in (e.get("file_name") or "") for e in kept)


def test_narrative_expansion_query_entity_plus_history_vocab():
    """叙事类问题生成 实体词+历程词汇 扩展检索查询；非叙事问题不生成。"""
    from app.rag_app.rag_engine import _narrative_expansion_query
    q = _narrative_expansion_query("帮我找出桥水基金创建的背景")
    assert q is not None
    assert "桥水" in q
    assert "创办" in q and "创立" in q and "白手起家" in q
    assert _narrative_expansion_query("三体中的黑暗森林法则是什么") is None


def test_query_weak_evidence_warns_in_prompt():
    """弱证据（top1 分数低于阈值）时，prompt 注入诚实拒绝警告。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def search(self, question, top_k=5, domain=None, where_filter=None,
                   queries=None, diversity=False):
            return [{
                "text": "弱相关片段。",
                "metadata": {"file_name": "测试书", "physical_name": "t.epub",
                             "page_number": 1, "chunk_index": 0},
                "score": 0.003,
            }]

        def extract_sentence_evidence(self, results, query, max_sentences=8):
            return []

    class FakeCompletions:
        def __init__(self):
            self.captured = None

        def create(self, **kwargs):
            self.captured = kwargs
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "知识库未覆盖该问题。"})()})()]})()

    kb = FakeKB()
    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = kb
    eng.config = type("C", (), {})()
    eng.llm_client = type("L", (), {"chat": type("C2", (), {"completions": FakeCompletions()})()})()
    eng.model_name = "fake"
    res = eng.query("这是一个无法在知识库回答的问题吗？", top_k=5)
    prompt = eng.llm_client.chat.completions.captured["messages"][-1]["content"]
    assert "[警告]" in prompt
    assert "反幻觉铁律" in prompt
    assert res["answer"] == "知识库未覆盖该问题。"
    assert res.get("weak_evidence") is True


def test_try_web_supplement_skips_when_local_answer_ok():
    """本地答案充分（非弱证据）时不触发联网补充。"""
    from app.rag_app.routes.chat_ops import _try_web_supplement
    eng = FakeEngine(FakeLLM("本地答案"))
    res = {"weak_evidence": False}
    assert _try_web_supplement(eng, "问题", res, "正常本地回答") is None


def test_try_web_supplement_success(monkeypatch):
    """弱证据时自动联网补充：返回带标注的回答 + 真实网页源。"""
    from app.rag_app.routes.chat_ops import _try_web_supplement
    monkeypatch.setattr(
        "app.rag_app.routes.chat_ops._ddgs_search",
        lambda q, max_results=5: [{"title": "网页A", "uri": "https://a.com", "body": "内容A"}],
    )
    eng = FakeEngine(FakeLLM("这是网页答案[网页1]"))
    res = {"weak_evidence": True}
    sup = _try_web_supplement(eng, "问题", res, "知识库未覆盖该问题。")
    assert sup is not None
    text, sources = sup
    assert "联网实时搜索补充" in text
    assert "网页答案" in text
    assert sources == [{"title": "网页A", "uri": "https://a.com"}]


def test_try_web_supplement_fallback_when_search_empty(monkeypatch):
    """ddgs 无结果时保持诚实回答（返回 None）。"""
    from app.rag_app.routes.chat_ops import _try_web_supplement
    monkeypatch.setattr(
        "app.rag_app.routes.chat_ops._ddgs_search",
        lambda q, max_results=5: [],
    )
    eng = FakeEngine(FakeLLM("不应被调用"))
    res = {"weak_evidence": True}
    assert _try_web_supplement(eng, "问题", res, "知识库中未找到相关内容。") is None


def test_try_web_supplement_triggers_on_llm_no_answer_marker(monkeypatch):
    """LLM 回答"资料未覆盖"（非空结果路径）同样触发联网补充。"""
    from app.rag_app.routes.chat_ops import _try_web_supplement
    monkeypatch.setattr(
        "app.rag_app.routes.chat_ops._ddgs_search",
        lambda q, max_results=5: [{"title": "网页A", "uri": "https://a.com", "body": "内容A"}],
    )
    eng = FakeEngine(FakeLLM("联网答案[网页1]"))
    res = {"weak_evidence": False}
    sup = _try_web_supplement(eng, "问题", res, "资料未覆盖。参考资料中没有任何关于该问题的信息。")
    assert sup is not None
    assert "联网实时搜索补充" in sup[0]


def test_try_web_supplement_triggers_on_not_found_marker(monkeypatch):
    """LLM 回答"没有找到…"（另一种无答案变体）也触发联网补充。"""
    from app.rag_app.routes.chat_ops import _try_web_supplement
    monkeypatch.setattr(
        "app.rag_app.routes.chat_ops._ddgs_search",
        lambda q, max_results=5: [{"title": "网页A", "uri": "https://a.com", "body": "内容A"}],
    )
    eng = FakeEngine(FakeLLM("联网答案[网页1]"))
    res = {"weak_evidence": False}
    sup = _try_web_supplement(eng, "问题", res, "根据提供的参考资料，没有找到相关内容。")
    assert sup is not None


def test_query_injects_sentence_evidence_for_plain_question():
    """普通"为什么"问题（无查找词/无改写查询）同样注入句子级证据。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def search(self, question, top_k=5, domain=None, where_filter=None,
                   queries=None, diversity=False):
            return [{
                "text": "候选块内容。",
                "metadata": {"file_name": "三体", "physical_name": "santi.epub",
                             "page_number": 88, "chunk_index": 76},
            }]

        def extract_sentence_evidence(self, results, query, max_sentences=8):
            return []

        def find_exact_sentences(self, query, top_k=5, file_ids=None):
            return [{
                "text": "叶文洁看看表：你可以先从这两条公理着手创立这门学科。",
                "file_name": "三体", "physical_name": "santi.epub",
                "page_number": 88, "chunk_index": 76,
            }]

    class FakeCompletions:
        def __init__(self):
            self.captured = None

        def create(self, **kwargs):
            self.captured = kwargs
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "OK"})()})()]})()

    kb = FakeKB()
    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = kb
    eng.config = type("C", (), {})()
    eng.llm_client = type("L", (), {"chat": type("C2", (), {"completions": FakeCompletions()})()})()
    eng.model_name = "fake"
    res = eng.query("为什么叶文洁要提醒罗辑？", top_k=5)
    prompt = eng.llm_client.chat.completions.captured["messages"][-1]["content"]
    assert "关键句证据" in prompt
    assert "叶文洁看看表" in prompt
    assert any(e["page_number"] == 88 for e in res["evidence"])


def test_find_exact_sentences_caps_per_chunk():
    """单个 chunk 刷屏保护：同一 chunk 最多返回 2 句，避免挤掉其他关键句。"""
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._embed_texts = [
        "叶文洁的第一句。叶文洁的第二句。叶文洁的第三句。叶文洁的第四句。",
        "罗辑的唯一关键句，叶文洁给他的提示。",
    ]
    kb._embed_metadatas = [
        {"file_name": "三体", "physical_name": "s.epub", "page_number": 88, "chunk_index": 76},
        {"file_name": "三体", "physical_name": "s.epub", "page_number": 91, "chunk_index": 79},
    ]
    hits = kb.find_exact_sentences("叶文洁 罗辑 提示", top_k=4)
    c76 = [h for h in hits if h["chunk_index"] == 76]
    c79 = [h for h in hits if h["chunk_index"] == 79]
    assert len(c76) <= 2, "同一 chunk 不应超过 2 句"
    assert len(c79) == 1


def test_extract_sentence_evidence_caps_per_chunk():
    """检索切句同样限制每 chunk 2 句，避免同一片段多句刷屏。"""
    from app.rag_app.knowledge_base import KnowledgeBase
    kb = KnowledgeBase.__new__(KnowledgeBase)
    results = [{
        "text": "头脑极度开放的基础是担忧。极度开放是一种能力。奉行头脑极度开放的话。大多数人不明白。",
        "metadata": {"file_name": "测试书", "physical_name": "t.epub",
                     "page_number": 299, "chunk_index": 157},
    }]
    ev = kb.extract_sentence_evidence(results, "头脑极度开放", max_sentences=8)
    assert len(ev) <= 2


def test_query_evidence_dedup_by_position():
    """证据按 (文件|页|段) 去重：同位置最多 2 条，消除"重复定位"。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def search(self, question, top_k=5, domain=None, where_filter=None,
                   queries=None, diversity=False):
            return [{
                "text": "候选块。",
                "metadata": {"file_name": "测试书", "physical_name": "t.epub",
                             "page_number": 299, "chunk_index": 157},
            }]

        def extract_sentence_evidence(self, results, query, max_sentences=8):
            return [
                {"text": "句一。", "file_name": "测试书", "physical_name": "t.epub",
                 "page_number": 299, "chunk_index": 157},
                {"text": "句二。", "file_name": "测试书", "physical_name": "t.epub",
                 "page_number": 299, "chunk_index": 157},
                {"text": "句三。", "file_name": "测试书", "physical_name": "t.epub",
                 "page_number": 299, "chunk_index": 157},
            ]

        def find_exact_sentences(self, query, top_k=5, file_ids=None):
            return []

    kb = FakeKB()
    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = kb
    eng.config = type("C", (), {})()
    class FakeCompletions:
        def create(self, **kwargs):
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "OK"})()})()]})()

    eng.llm_client = type("L", (), {"chat": type("C2", (), {"completions": FakeCompletions()})()})()
    res = eng.query("头脑极度开放", top_k=5)
    pos = [e for e in res["evidence"] if e.get("chunk_index") == 157]
    assert len(pos) <= 2


# ===== 任务十一：知识库级统计（多少本/什么格式） =====

def test_library_stats_question_detection():
    from app.rag_app.rag_engine import _is_library_stats_question
    assert _is_library_stats_question("库里目前有多少本书，分别是什么格式的？")
    assert _is_library_stats_question("知识库有几本书？")
    assert _is_library_stats_question("所有文件的格式是什么？")
    assert not _is_library_stats_question("这本书讲了什么？")


def test_answer_library_stats_full_library():
    """全库统计直答：数量 + 格式分布 + 逐本清单。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def get_library_stats(self, file_ids=None):
            return {
                "files": [
                    {"file_name": "a.pdf", "physical_name": "a.pdf", "format": "pdf"},
                    {"file_name": "b.pdf", "physical_name": "b.pdf", "format": "pdf"},
                    {"file_name": "c.epub", "physical_name": "c.epub", "format": "epub"},
                ],
                "formats": {"pdf": 2, "epub": 1},
                "file_count": 3,
            }

    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = FakeKB()
    eng.config = type("C", (), {})()
    ans = eng._answer_library_stats("库里目前有多少本书，分别是什么格式的？", None)
    assert "共有 3 本书" in ans
    assert "PDF: 2 本" in ans
    assert "EPUB: 1 本" in ans
    assert "《a》(pdf)" in ans


def test_answer_library_stats_single_file_format():
    """单文件格式问法：直接答格式，不列全库。"""
    from app.rag_app.rag_engine import RAGEngine

    class FakeKB:
        def get_library_stats(self, file_ids=None):
            return {
                "files": [{"file_name": "原则.pdf", "physical_name": "原则.pdf", "format": "pdf"}],
                "formats": {"pdf": 1},
                "file_count": 1,
            }

    eng = RAGEngine.__new__(RAGEngine)
    eng.kb = FakeKB()
    eng.config = type("C", (), {})()
    ans = eng._answer_library_stats("这本书是什么格式？", ["原则.pdf"])
    assert "《原则.pdf》是 PDF 格式" in ans
