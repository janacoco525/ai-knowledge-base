"""T6：toc_extractor 核心逻辑单元测试（extract_toc / extract_toc_from_preprocessed）"""
import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.toc_extractor import extract_toc, extract_toc_from_preprocessed


# ── extract_toc_from_preprocessed：从预处理文本反向解析标题 ──

def test_toc_from_preprocessed_basic():
    md = "# 第一章 背景\n\n正文\n\n## 1.1 细节\n\n## 参考文献\n"
    items = extract_toc_from_preprocessed(md)
    texts = [i["text"] for i in items]
    assert texts == ["第一章 背景", "1.1 细节", "参考文献"]
    # level 映射：h1→0, h2→1（我们的 L0/L1/L2 体系）
    levels = [i["level"] for i in items]
    assert levels == [0, 1, 1]
    # id 格式：heading-<slug>
    assert all(i["id"].startswith("heading-") for i in items)


def test_toc_from_preprocessed_dedup_same_text():
    md = "## 第一章\n\n正文\n\n## 第一章\n"
    items = extract_toc_from_preprocessed(md)
    assert len(items) == 1


def test_toc_from_preprocessed_filters_punctuation_ending():
    # 以句末/逗号结尾且不以数字开头的行不是标题
    md = "## 这是正文句。\n\n## 正常标题\n"
    items = extract_toc_from_preprocessed(md)
    assert "这是正文句。" not in [i["text"] for i in items]
    assert "正常标题" in [i["text"] for i in items]


def test_toc_from_preprocessed_filters_too_long():
    md = "## " + "长" * 100 + "\n\n## 正常标题\n"
    items = extract_toc_from_preprocessed(md)
    assert all(len(i["text"]) <= 80 for i in items)
    assert "正常标题" in [i["text"] for i in items]


def test_toc_from_preprocessed_empty():
    assert extract_toc_from_preprocessed("") == []
    assert extract_toc_from_preprocessed("没有标题的纯正文\n第二行") == []


# ── extract_toc：全文提取入口 ──

def test_extract_toc_empty_input():
    assert extract_toc("") == []
    assert extract_toc(None) == []


def test_extract_toc_returns_list():
    sample = "第一章 背景\n正文内容。\n\n第二章 方案\n更多正文。"
    items = extract_toc(sample)
    assert isinstance(items, list)
    # 返回条目具备 id/text/level 三要素
    for item in items:
        assert "id" in item and "text" in item and "level" in item
