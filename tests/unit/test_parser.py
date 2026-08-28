"""T6：parser 核心逻辑单元测试（clean_text / _garbled_ratio / _split_markdown_by_headers / _chunk_pages / _infer_domain）"""
import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.parser import DocumentParser, clean_text, _reflow_pdf_text, _join_pdf_lines, join_chunk_texts


def _parser(chunk_size=100, chunk_overlap=20) -> DocumentParser:
    """绕过 Config 构造，直接绑定实例属性"""
    p = DocumentParser.__new__(DocumentParser)
    p.chunk_size = chunk_size
    p.chunk_overlap = chunk_overlap
    return p


# ── clean_text：小说/图书文本清洗 ──

def test_clean_text_removes_page_and_isbn():
    sample = "--- 第2页 / 共139页 ---\n第3页/共139页\nISBN：978-7-111-12345-6\n\n正文内容。"
    out = clean_text(sample)
    assert "第2页" not in out
    assert "第3页" not in out
    assert "ISBN" not in out
    assert "正文内容" in out


def test_clean_text_removes_promo_lines():
    sample = "版权信息\n\n扫码关注公众号\n微信号：abc123\n\n正文内容。"
    out = clean_text(sample)
    assert "版权信息" not in out
    assert "扫码" not in out
    assert "微信号" not in out
    assert "正文内容" in out


def test_clean_text_removes_bare_page_numbers():
    sample = "正文内容。\n\n42\n\n结尾。"
    out = clean_text(sample)
    assert "\n42\n" not in out
    assert "正文内容" in out


def test_clean_text_collapses_blank_lines():
    sample = "第一段。\n\n\n\n\n第二段。"
    out = clean_text(sample)
    assert "\n\n\n\n" not in out


# ── _garbled_ratio：乱码比例检测 ──

def test_garbled_ratio_normal_text_is_zero():
    assert DocumentParser._garbled_ratio("这是一段正常中文文本") == 0.0
    assert DocumentParser._garbled_ratio("plain ascii text 123") == 0.0


def test_garbled_ratio_foreign_chars_high():
    # 日文假名不属于白名单 → 计为乱码
    assert DocumentParser._garbled_ratio("これは日本語のテキスト") > 0.15


def test_garbled_ratio_empty():
    assert DocumentParser._garbled_ratio("") == 0.0


# ── _split_markdown_by_headers：按标题分割 ──

def test_split_markdown_by_headers_basic():
    md = "# 第一章\n正文A\n\n## 1.1 小节\n正文B"
    sections = _parser()._split_markdown_by_headers(md)
    assert len(sections) == 2
    assert sections[0][0] == "第一章"
    assert sections[0][2] == 1  # level
    assert "正文A" in sections[0][1]
    assert sections[1][0] == "1.1 小节"


def test_split_markdown_by_headers_ignores_code_block():
    md = "# 标题\n```\n# 这不是标题\n```\n正文"
    sections = _parser()._split_markdown_by_headers(md)
    # 代码块内的 # 不应触发新 section
    assert len(sections) == 1
    assert sections[0][0] == "标题"


def test_split_markdown_by_headers_no_headers():
    md = "纯正文\n没有标题"
    sections = _parser()._split_markdown_by_headers(md)
    assert len(sections) == 1
    assert sections[0][0] == ""


# ── _chunk_pages：分块 + overlap ──

def test_chunk_pages_splits_with_overlap():
    p = _parser(chunk_size=100, chunk_overlap=20)
    chunks = p._chunk_pages([
        {"page_num": 1, "text": "a" * 150},
        {"page_num": 2, "text": "b" * 80},
    ], "test.txt")
    assert len(chunks) == 3
    # 首块完整保留
    assert chunks[0]["text"] == "a" * 150
    # 字段齐备
    for c in chunks:
        assert c["chunk_index"] == chunks.index(c)
        assert c["file_name"] == "test.txt"
        assert "page_number" in c
    # 末块来自第二页
    assert chunks[-1]["text"].startswith("b")


def test_chunk_pages_single_small_page():
    p = _parser()
    chunks = p._chunk_pages([{"page_num": 1, "text": "短内容"}], "t.txt")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "短内容"


# ── _infer_domain：域名推断 ──

def test_infer_domain_ai_keywords():
    p = _parser()
    assert p._infer_domain("机器学习笔记.pdf") == "ai_knowledge"
    assert p._infer_domain("transformer实战.md") == "ai_knowledge"
    assert p._infer_domain("测试文档.txt") == "default"


# ── _reflow_pdf_text：PDF 排版重整（2026-08-06）──

def test_reflow_removes_leading_indent():
    # 居中/缩进排版携带的前导空格被去除
    out = _reflow_pdf_text("            Abstract\n\n      Some text here")
    assert out == "Abstract\n\nSome text here"


def test_reflow_merges_physical_linebreaks():
    # 段落内每行 ~80 字符的硬换行合并为完整段落，段落间保留空行
    sample = "Self-improving autonomous agents are moving\nto deployed systems. The primary goal\n\nSecond paragraph starts here"
    out = _reflow_pdf_text(sample)
    assert out == "Self-improving autonomous agents are moving to deployed systems. The primary goal\n\nSecond paragraph starts here"


def test_reflow_hyphenation_rejoin():
    # 连字符断词还原：adapta- + tion → adaptation
    out = _reflow_pdf_text("the primary goal is controllable evolution, or adapta-\ntion, from experience\n\nNext paragraph")
    assert "adaptation" in out
    assert "adapta-" not in out


def test_reflow_chinese_no_extra_space():
    # 中文相邻行合并不插空格（有空行的正常排版走合并路径）
    out = _reflow_pdf_text("现代智能体系统中的\n自我改进综述\n\n第二段")
    assert out == "现代智能体系统中的自我改进综述\n\n第二段"


def test_reflow_dense_layout_unchanged():
    # 无空行的密集排版（小说扫描版）：不合并仅去缩进，避免段落粘连
    out = _reflow_pdf_text("  第一段内容\n    第二段内容\n第三段内容")
    assert out == "第一段内容\n第二段内容\n第三段内容"


def test_join_lines_hyphen_requires_lowercase():
    # 连字符后接大写不还原（避免误合专有名词列表）
    assert _join_pdf_lines(["item-", "Based"]) == "item- Based"


# ── 2026-08-07 新增：段落边界切分 + overlap 去重重建 ──

def test_chunk_pages_cuts_at_paragraph_boundary():
    # 块达阈时优先在段落边界切，不把段落拦腰截断
    p = _parser(chunk_size=60, chunk_overlap=10)
    para_a, para_b = "甲" * 40, "乙" * 30
    pages = [{"page_num": 1, "text": para_a + "\n\n" + para_b}]
    chunks = p._chunk_pages(pages, "t.pdf")
    assert chunks[0]["text"] == para_a


def test_chunk_pages_hard_cuts_oversized_single_line_page():
    # 无换行的超长单行文本（epub 整章退化场景）：硬上限循环切块，绝不整页成块
    p = _parser(chunk_size=100, chunk_overlap=20)
    chunks = p._chunk_pages([{"page_num": 1, "text": "x" * 5000}], "t.txt")
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 400 for c in chunks)  # hard_limit = 100*4
    assert sum(c["text"].count("x") for c in chunks) >= 5000


def test_join_chunk_texts_dedups_overlap():
    # 分块 overlap 携带的重复前缀在重建时被剪掉，不产生重复内容
    t1 = "这是第一段正文内容，用于验证分块重叠去重。" * 4
    tail = t1[-50:]
    t2 = tail + "这是后续新增内容，不应重复出现。"
    out = join_chunk_texts([t1, t2])
    assert out.startswith(t1)
    assert out.endswith("这是后续新增内容，不应重复出现。")
    assert out.count("这是后续新增内容") == 1


def test_chunk_and_rebuild_roundtrip_no_duplicate():
    # 切分→重建全流程：段落不重复、不丢失
    p = _parser(chunk_size=60, chunk_overlap=10)
    para_a, para_b = "甲" * 40, "乙" * 50
    pages = [{"page_num": 1, "text": para_a + "\n\n" + para_b}]
    chunks = p._chunk_pages(pages, "t.pdf")
    out = join_chunk_texts([c["text"] for c in chunks])
    assert out.count(para_a) == 1
    assert out.count(para_b) == 1
