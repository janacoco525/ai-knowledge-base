"""规则兜底图谱质量：通用词过滤 + 多字术语排序加成 + char_count 累计（2026-08-19）。

问题来源：《2049：未来10000天的可能》图谱降级时节点全是
"因为/比如/大多数/真正/变得/看到"等虚词/形容词（用户实测反馈）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.concept_extractor import ConceptExtractor, _is_valid_concept
from app.rag_app.knowledge_base import KnowledgeBase
from app.rag_app.llm_graph_extractor import extract_graph_fallback


def test_generic_cn_stopwords_rejected():
    """通用功能词不得成为图谱节点（《2049》实测泄漏词全覆盖）。"""
    for w in [
        "因为", "所以", "但是", "然而", "比如", "例如", "大多数", "少数",
        "真正", "完全", "变得", "成为", "看到", "认为", "进行", "通过",
        "年内", "多年", "今年", "普及", "定义", "搜集", "取代", "助理",
        "这些", "那些", "什么", "怎么", "自己", "人们",
    ]:
        assert not _is_valid_concept(w), w


def test_domain_terms_accepted():
    """领域术语/复合概念必须保留（不被通用词表误伤）。"""
    for w in [
        "镜像世界", "脑机接口", "数字孪生", "远程医疗", "人形机器人",
        "通用目的技术", "极度透明", "可信度加权", "五步流程", "AI", "ChatGPT",
        # 含单个功能字的合法词（"的/和/种"在词内出现 1 次不算残片）
        "通用目的技术", "和谐", "物种", "用人不当",
        # 长专名（组织名）保留
        "中国国际信托投资公司",
    ]:
        assert _is_valid_concept(w), w


def test_sentence_fragments_rejected():
    """句子残片/断词不得成为图谱节点（《原则》《三体》实测泄漏词）。"""
    for w in [
        "数据分析工具以及其他算法", "们可以考察这些数据和算法",
        "经常可以组成可识别的类型", "意识到错误是事物演变过程",
        "且一个人可以符合多种类型", "生活中反复遇到的多种类型",
        "个将问题转化为进步的过程", "要更关注发言人的推理过程",
        "骑着自行", "等汪淼回", "淼找到了", "接过木盒", "道那些传",
        "盯着曲线", "人也都在", "略的重要", "冬的母亲", "真对不起",
    ]:
        assert not _is_valid_concept(w), w


def _make_general_chunks(n: int = 20):
    """通用书风格分块：领域术语反复出现，且混入大量虚词/通用词。"""
    terms = [
        # 选 jieba 能整词输出的多字术语（镜像世界/脑机接口等会被分词拆开）
        "智能手机", "全球定位系统", "心灵感应", "虚拟现实", "人工智能",
        "自动驾驶", "机器人", "增强现实", "大语言模型", "去中心化",
    ]
    filler = [
        "因为", "比如", "大多数", "真正", "变得", "看到", "年内",
        "普及", "定义", "搜集", "取代", "未来", "中国", "技术", "世界",
        "用户", "数据", "模型", "人类", "工作",
    ]
    chunks = []
    for i in range(n):
        body = "".join(f"{t}是未来的重要方向。" for t in terms)
        body += "。".join(filler)
        chunks.append({
            "text": body,
            "source_file": "book.txt",
            "source_chunk_id": f"c{i}",
            "domain": "default",
        })
    return chunks


def test_fallback_graph_no_generic_words_and_keeps_terms():
    chunks = _make_general_chunks()
    payload = extract_graph_fallback(chunks, max_nodes=20)
    labels = [str(n.get("label", "")) for n in payload.get("nodes", [])]
    junk = {
        "因为", "比如", "大多数", "真正", "变得", "看到", "年内",
        "普及", "定义", "搜集", "取代",
    }
    leaked = junk & set(labels)
    assert not leaked, f"虚词泄漏为节点: {leaked}"
    # 多字领域术语应进入图谱（词长加成后不被高频虚词挤掉）
    assert any(l in labels for l in ("智能手机", "全球定位系统", "心灵感应", "虚拟现实")), labels


def test_list_files_char_count_sums_all_chunks():
    """char_count 必须按该文件所有分块累计（修复首块缓存误导）。"""
    kb = KnowledgeBase.__new__(KnowledgeBase)
    kb._embed_texts = [
        "中信出版集团制作发行\n版权所有·侵权必究\n导语\n\n我相信中国将是未来世界中最强大的力量之一。",
        "法中，你要生成多个场景，每个场景都有略微不同的视角，尽可能涵盖更多不同的可能性。",
        "运作的方式，以及它在这个世界中可能扮演的角色。我给自己的想象设定了25年的期限。",
    ]
    kb._embed_metadatas = [
        {
            "file_name": "2049：未来10000天的可能.pdf",
            "physical_name": "2049：未来10000天的可能.pdf",
            "file_path": "app/rag_app/data/uploads/x.pdf",
            "domain": "default",
            "uploaded_at": "2026-08-05 17:51:12",
            "file_size": 1218164,
            "file_mtime": 1785923472,
            "chunk_index": 0,
        },
        {
            "file_name": "2049：未来10000天的可能.pdf",
            "physical_name": "2049：未来10000天的可能.pdf",
            "file_path": "app/rag_app/data/uploads/x.pdf",
            "domain": "default",
            "uploaded_at": "2026-08-05 17:51:12",
            "file_size": 1218164,
            "file_mtime": 1785923472,
            "chunk_index": 1,
        },
        {
            "file_name": "2049：未来10000天的可能.pdf",
            "physical_name": "2049：未来10000天的可能.pdf",
            "file_path": "app/rag_app/data/uploads/x.pdf",
            "domain": "default",
            "uploaded_at": "2026-08-05 17:51:12",
            "file_size": 1218164,
            "file_mtime": 1785923472,
            "chunk_index": 2,
        },
    ]
    kb._file_text_cache = {"2049：未来10000天的可能.pdf": "只有首块的709字缓存"}
    kb._file_preprocessed = {}
    files = kb.list_files()
    assert len(files) == 1
    expected = sum(len(t) for t in kb._embed_texts)
    assert files[0]["char_count"] == expected, files[0]["char_count"]
    # 且不等于首块缓存长度（旧 bug 特征）
    assert files[0]["char_count"] > len(kb._file_text_cache["2049：未来10000天的可能.pdf"])


def test_novel_fallback_keeps_names_drops_fragments():
    """小说模式：人名保留，断词残片不进图谱（通用规则，非针对《三体》）。"""
    names = ["汪淼", "罗辑", "叶文洁", "史强"]
    fragments = ["骑着自行", "等汪淼回", "淼找到了"]
    chunks = []
    for i in range(8):
        body = (
            "汪淼说道别问了罗辑问道为什么叶文洁笑了笑。"
            "史强喊道快走汪淼骑着自行车走了，罗辑跟在后面。"
            "叶文洁回到了基地，汪淼找到了答案。"
            "文洁默默地点头，淼平静地看着前方。"
        )
        chunks.append({
            "text": body,
            "source_file": "小说.txt",
            "source_chunk_id": f"c{i}",
            "domain": "default",
        })
    payload = extract_graph_fallback(chunks, max_nodes=20)
    labels = [str(n.get("label", "")) for n in payload.get("nodes", [])]
    assert any(n in labels for n in names), labels
    assert not set(fragments) & set(labels), f"残片泄漏: {set(fragments) & set(labels)}"
    # 副词尾缀残片（"文洁默默/淼平静地"）不得进图谱
    assert not any(l in labels for l in ("文洁默默", "淼平静地")), labels
