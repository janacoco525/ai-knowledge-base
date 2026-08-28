"""
AI知识库 — 标题提取器
从文档全文中提取章节/部/篇/卷标题结构
与前端 DocEditor.tsx 的 isStructuralHeading() + getHeadings() 逻辑对等
"""
import re
import logging

logger = logging.getLogger("ai_kb.heading_extractor")


def extract_headings(full_text: str) -> list[dict]:
    """从全文提取标题层级，返回 [{id, text, level}] 列表"""
    if not full_text:
        return []

    lines = full_text.split("\n")
    found_headings: list[dict] = []  # [{id, text, level, lineIndex}]
    seen_ids: set[str] = set()

    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        result = _is_heading(stripped)
        if not result:
            continue

        level, heading_text = result

        # 去重 (相同文字只保留第一次出现)
        heading_id = _generate_heading_id(heading_text)
        if heading_id in seen_ids:
            continue
        seen_ids.add(heading_id)

        found_headings.append({
            "id": heading_id,
            "text": heading_text,
            "level": level,
            "lineIndex": line_idx,
        })

    # === 过滤内联目录块 ===
    found_headings = _filter_inline_toc(found_headings)

    # === 同类标题去重：同一"第N章/部/篇/卷"前缀只保留首次 ===
    found_headings = _dedup_by_chapter_prefix(found_headings)

    # 返回时去掉 lineIndex
    return [{"id": h["id"], "text": h["text"], "level": h["level"]} for h in found_headings]


def _generate_heading_id(text: str) -> str:
    """生成稳定的 heading id — 与前端 DocEditor.tsx generateHeadingId 完全一致（都用 encodeURIComponent 等价算法）"""
    import string
    from urllib.parse import quote
    clean = text.strip().lower()
    clean = re.sub(r"\s+", "-", clean)
    clean = re.sub(r"[^\u4e00-\u9fff\w-]", "", clean)
    # JS encodeURIComponent 保留: A-Za-z0-9-_.!~*'()
    safe_chars = string.ascii_letters + string.digits + "-_.!~*'()"
    return "heading-" + quote(clean, safe=safe_chars)


def _is_heading(text: str) -> tuple[int, str] | None:
    """判断一行是不是标题，返回 (level, heading_text) 或 None"""
    if not text:
        return None

    # 1. Already markdown heading
    if text.startswith("#"):
        m = re.match(r"^(#{1,6})\s+(.+)$", text)
        if m:
            heading_text = m.group(2).strip()
            # 标题太长说明正文粘一起了 → 截取到第一个中文标点或逗号
            if len(heading_text) > 60:
                parts = re.split(r"[。！？，,\n]", heading_text, maxsplit=1)
                heading_text = parts[0].strip()
            return (len(m.group(1)), heading_text)

    # 2. Chapter/Part detection
    m = re.match(
        r"^(Chapter\s+\d+|第\s*[一二三四五六七八九十百\d]+\s*[章节回课分部篇卷])\s*[:：、\s]?\s*(.*)$",
        text, re.IGNORECASE,
    )
    if m and len(text) < 200:  # 放宽到200字（EPUB解析可能把标题+正文粘一起）
        prefix = m.group(1)
        suffix = (m.group(2) or "").strip()
        prefix = m.group(1)
        suffix = (m.group(2) or "").strip()

        # 排除：章节结束标记 "第一章完。"
        if re.match(r'^[（(]?\s*完\s*[)）]?[。.！!]?\s*$', suffix):
            return None

        # 排除：正文引用/句式 "第五章，被称作..." / "第2章介绍了..."
        if re.match(r"^[，,]", suffix):
            return None
        if re.search(
            r"(介绍了|阐述了|描述了|讨论了|探讨了|介绍过|阐述过|描述过|讨论过|探讨过|曾经|就已经|将会|可以|需要|应该|主要|首先|最后|接着|然后)\s*\S",
            suffix,
        ):
            return None

        # 部/篇/卷 → level 0, 章/节 → level 1
        is_super = bool(re.search(r"[部篇卷]", prefix))
        level = 0 if is_super else 1
        # 如果后缀过长，说明 EPUB 解析把正文粘在后面了——只取前缀
        if suffix and len(suffix) > 40:
            truncated = re.split(r"[。！？，,\n]", suffix, maxsplit=1)[0].strip()
            heading_text = f"{prefix} {truncated}" if truncated else prefix
        else:
            heading_text = f"{prefix} {suffix}" if suffix else prefix
        return (level, heading_text.strip())

    # 3. 中国序号 "一、" "二、"
    m = re.match(r"^([一二三四五六七八九十]+、)\s*(.+)$", text)
    if m and len(text) < 40 and not re.search(r"[。！？，,：:；;]", text):
        return (2, text.strip())

    # 4. Numeric headings "1.2.3 Title"
    m = re.match(r"^(\d+(\.\d+){1,3})\s+(.+)$", text)
    if m and len(text) < 60 and not re.search(r"[。！？，,：:；;]", text):
        level = min(3, len(m.group(1).split(".")))
        return (level, text.strip())

    # 5. Simple numeric "1. Title"
    m = re.match(r"^(\d+)\.\s+([^。，,！!？?：:；;、\n]+)$", text)
    if m and len(text) < 30 and not re.search(r"[告诉应当可以需要必须应该能够会要想让去].{2,}", text):
        return (2, text.strip())

    # 6. Special sections
    special = [
        "前言", "引言", "序言", "导言", "绪论", "结语", "结论与展望",
        "参考文献", "致谢", "目录", "Abstract", "References", "Conclusion",
    ]
    has_sentence_end = bool(re.search(r"[。！？!?]", text))
    if text in special:
        return (1, text)
    if len(text) < 25 and not has_sentence_end and any(text.startswith(s) or text.endswith(s) for s in special):
        return (1, text)
    if re.match(r"^关于[\u4e00-\u9fa5]{2,15}$", text) and not has_sentence_end:
        return (1, text)

    return None


def _filter_inline_toc(headings: list[dict]) -> list[dict]:
    """过滤内联目录块：连续5+标题间距较小则整块移除"""
    if len(headings) <= 5:
        return headings

    toc_start = -1
    max_consecutive = 0
    consec = 1

    for i in range(1, len(headings)):
        gap = headings[i]["lineIndex"] - headings[i - 1]["lineIndex"]
        if gap <= 25:
            consec += 1
        else:
            if consec >= 5:
                max_consecutive = max(max_consecutive, consec)
                if toc_start < 0:
                    toc_start = i - consec
            consec = 1

    if consec >= 5:
        max_consecutive = max(max_consecutive, consec)
        if toc_start < 0:
            toc_start = len(headings) - consec

    if max_consecutive >= 5 and (toc_start <= 5 or toc_start >= len(headings) - max_consecutive - 3):
        return [h for i, h in enumerate(headings) if i < toc_start or i >= toc_start + max_consecutive]

    return headings


def _dedup_by_chapter_prefix(headings: list[dict]) -> list[dict]:
    """同一"第N章/部/篇/卷"前缀只保留首次出现"""
    seen_prefixes: set[str] = set()
    result: list[dict] = []

    for h in headings:
        m = re.match(r"^(第\s*[一二三四五六七八九十百\d]+\s*[章節节回课分部篇卷])", h["text"])
        if m:
            prefix = re.sub(r"\s+", "", m.group(1))
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)
        result.append(h)

    return result
