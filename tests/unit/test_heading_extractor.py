"""T6：heading_extractor 核心逻辑单元测试（extract_headings / _is_heading / id 生成）"""
import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.heading_extractor import (
    extract_headings,
    _is_heading,
    _generate_heading_id,
    _dedup_by_chapter_prefix,
)


# ── _is_heading：标题识别 ──

def test_is_heading_markdown():
    assert _is_heading("# 一级标题") == (1, "一级标题")
    assert _is_heading("## 二级标题") == (2, "二级标题")


def test_is_heading_chapter_and_part():
    level, text = _is_heading("第一章 背景介绍")
    assert level == 1
    assert text == "第一章 背景介绍"
    # 部/篇/卷 → level 0
    assert _is_heading("第一部 我的历程")[0] == 0


def test_is_heading_excludes_sentence_patterns():
    # 章节结束标记 / 正文句式排除
    assert _is_heading("第一章完。") is None
    assert _is_heading("第五章，被称作。") is None


def test_is_heading_chinese_numeric():
    assert _is_heading("一、背景") == (2, "一、背景")


def test_is_heading_dotted_numeric():
    assert _is_heading("1.2.3 标题")[0] == 3
    assert _is_heading("1. 标题")[0] == 2


def test_is_heading_special_sections():
    assert _is_heading("参考文献") == (1, "参考文献")
    assert _is_heading("前言") == (1, "前言")


def test_is_heading_plain_text():
    assert _is_heading("这是普通正文内容，没有任何标题特征。") is None


# ── _generate_heading_id ──

def test_generate_heading_id_stable():
    assert _generate_heading_id("第一章 背景") == "heading-%E7%AC%AC%E4%B8%80%E7%AB%A0-%E8%83%8C%E6%99%AF"
    # 稳定：相同输入 → 相同输出
    assert _generate_heading_id("第一章 背景") == _generate_heading_id("第一章 背景")


# ── extract_headings：完整流程 ──

def test_extract_headings_dedup_and_structure():
    items = extract_headings("第一章 背景\n正文\n\n第一章 背景\n\n第二章 方案\n更多内容")
    # 相同标题只保留首次
    assert [i["text"] for i in items] == ["第一章 背景", "第二章 方案"]
    for item in items:
        assert set(item.keys()) == {"id", "text", "level"}


def test_extract_headings_empty():
    assert extract_headings("") == []
    assert extract_headings("没有标题的纯正文。") == []


# ── _dedup_by_chapter_prefix ──

def test_dedup_by_chapter_prefix_keeps_first():
    headings = [
        {"id": "a", "text": "第一章 背景", "level": 1},
        {"id": "b", "text": "第一章 背景 补充", "level": 1},
        {"id": "c", "text": "第二章 方案", "level": 1},
    ]
    result = _dedup_by_chapter_prefix(headings)
    assert [h["text"] for h in result] == ["第一章 背景", "第二章 方案"]
