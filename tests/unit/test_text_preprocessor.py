"""T6：text_preprocessor 核心逻辑单元测试（preprocess_text / render_to_html_fast / _looks_like_body）"""
import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.text_preprocessor import (
    preprocess_text,
    render_to_html_fast,
    _looks_like_body,
    _norm_heading_spacing,
)


# ── preprocess_text：清理层 ──

def test_preprocess_removes_page_and_isbn_lines():
    sample = "--- 第1页 / 共10页 ---\nISBN：978-7-111-12345-6\n\n正文内容。"
    out = preprocess_text(sample)
    assert "第1页" not in out
    assert "ISBN" not in out
    assert "正文内容" in out


def test_preprocess_removes_footnotes():
    sample = "正文第一段。\n[1] 脚注内容\n\n第二段。\n\n-3-\n"
    out = preprocess_text(sample)
    assert "[1]" not in out
    assert "脚注内容" not in out
    assert "-3-" not in out


def test_preprocess_collapses_blank_lines():
    sample = "第一段。\n\n\n\n\n第二段。"
    out = preprocess_text(sample)
    assert "\n\n\n" not in out


def test_preprocess_empty_input():
    assert preprocess_text("") == ""
    assert preprocess_text(None) == ""


# ── preprocess_text：标题标记层 ──

def test_preprocess_marks_chapter_headings():
    sample = "第一章 背景介绍\n\n这是正文内容。"
    out = preprocess_text(sample)
    assert "## 第一章 背景介绍" in out


def test_preprocess_marks_special_sections():
    sample = "正文内容。\n\n参考文献\n\n这里是后续较长的正文内容说明。"
    out = preprocess_text(sample)
    assert "## 参考文献" in out


def test_preprocess_keeps_plain_paragraphs():
    sample = "这是第一段正文内容，介绍项目背景。\n\n这是第二段内容。"
    out = preprocess_text(sample)
    assert "这是第一段正文内容，介绍项目背景。" in out
    assert "这是第二段内容。" in out


# ── preprocess_text：智能分段（短行合并）──

def test_preprocess_merges_short_lines_into_paragraph():
    # 无句末标点的连续短行应合并为一段
    sample = "这是没有句号的短行\n第二行继续"
    out = preprocess_text(sample)
    assert "这是没有句号的短行第二行继续" in out


# ── 2026-08-07 新增：导入格式修复回归 ──

def test_norm_heading_spacing_collapses_word_internal_space():
    # PDF 词内断裂修复：'第一部 分' → '第一部分'，但保留前缀后分隔空格
    assert _norm_heading_spacing("第一部 分 我的历程") == "第一部分 我的历程"
    assert _norm_heading_spacing("第二章 台 球") == "第二章 台球"
    assert _norm_heading_spacing("第一章 背景介绍") == "第一章 背景介绍"


def test_preprocess_collapses_split_heading_in_output():
    sample = "第一部 分 工作原则\n\n这是正文内容说明，足够长以避免碎片合并。"
    out = preprocess_text(sample)
    assert "第一部分 工作原则" in out
    assert "第一部 分" not in out


def test_preprocess_bracket_special_section():
    # '【前　言】'（全角空格+括号）应被识别为特殊节标题而非吞进正文
    sample = "【前　言】\n\n这是前言正文第一句，介绍背景。"
    out = preprocess_text(sample)
    assert "## 前言" in out


def test_preprocess_toc_pagenum_line_not_swallowed():
    # 目录页码行不能被下行吞并（数字编号开头+页码结尾）
    sample = "这是导语段落，介绍全书内容背景。\n\n3 我的低谷（1979—1982年） 45\n4 我的试炼之路 67"
    out = preprocess_text(sample)
    assert "3 我的低谷" in out
    assert "45" in out and "4 我的试炼之路" in out


def test_preprocess_body_line_ending_digit_still_merges():
    # 正文行以数字结尾但非目录形态，仍应正常合并（防吞并护栏不误伤）
    sample = "这本书出版于2019\n年获得大奖。"
    out = preprocess_text(sample)
    assert "这本书出版于2019年获得大奖。" in out


# ── _looks_like_body ──

def test_looks_like_body_sentence_end():
    assert _looks_like_body("这里是一段正文。") is True
    assert _looks_like_body("内容……") is True


def test_looks_like_body_long_text_with_verb():
    assert _looks_like_body("这里是一段比较长的正文内容包含了常见动词") is True


def test_looks_like_body_short_title():
    assert _looks_like_body("目录") is False
    assert _looks_like_body("") is False


# ── render_to_html_fast：无依赖降级渲染 ──

def test_html_fast_headings():
    out = render_to_html_fast("## 第一章 背景")
    assert '<h2 id="第一章-背景">第一章 背景</h2>' in out


def test_html_fast_lists():
    out = render_to_html_fast("- 甲\n- 乙")
    assert "<ul>" in out and "<li>甲</li>" in out and "<li>乙</li>" in out and "</ul>" in out


def test_html_fast_inline_bold_and_code():
    out = render_to_html_fast("这是**加粗**和`代码`")
    assert "<strong>加粗</strong>" in out
    assert "<code>代码</code>" in out


def test_html_fast_quote_and_codeblock():
    out = render_to_html_fast("> 引用内容\n\n```python\nprint(1)\n```")
    assert "<blockquote><p>引用内容</p></blockquote>" in out
    assert '<pre><code class="language-python">' in out


def test_html_fast_empty():
    assert render_to_html_fast("") == ""
