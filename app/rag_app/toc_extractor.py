"""
AI知识库 — 目录提取器 v2

设计目标：
1. 优先检测文档自带的 TOC 区域（"目录" / "Contents"）
2. 解析结构化层级（缩进 + 编号）
3. 无 TOC 区域时回退到行内标题扫描
4. 生成与前端 encodeURIComponent 一致的 heading ID
5. 在索引时调用（仅一次），结果持久化到 content_cache.pkl
"""
import re
import logging
import string
from urllib.parse import quote

logger = logging.getLogger("ai_kb.toc_extractor")

# ── ID 生成（与前端 DocEditor.tsx generateHeadingId 完全一致）──
def _make_heading_id(text: str) -> str:
    """与前端 encodeURIComponent 匹配的 heading ID"""
    clean = text.strip().lower()
    clean = re.sub(r"\s+", "-", clean)
    clean = re.sub(r"[^\u4e00-\u9fff\w-]", "", clean)
    safe_chars = string.ascii_letters + string.digits + "-_.!~*'()"
    return "heading-" + quote(clean, safe=safe_chars)


# ── 主入口 ──
def extract_toc(full_text: str) -> list[dict]:
    """
    从文档全文中提取目录（索引时调用）。
    返回: [{id, text, level, pageHint}, ...]
    
    策略：TOC区域 + 全扫描 双层合并
    - TOC区域：解析"目录"区的结构化条目（辨识字间距缩进）
    - 全扫描：扫描全文搜索第N章/数字编号等行内标题
    - 去重合并：优先保留 TOC 区域条目 + 全扫描未覆盖的
    """
    if not full_text:
        return []

    lines = full_text.split("\n")

    # Step 1: TOC 区域提取（书籍层/部分层）
    toc_items = _extract_from_toc_section(lines)
    
    # Step 2: 全文行内标题扫描（章节层）—— 补充 TOC 未覆盖的
    inline_items = _extract_inline_headings(lines)
    
    # 合并：TOC 优先，用 inline 补充不重复的
    merged = _merge_toc_and_inline(toc_items, inline_items)
    
    logger.info("TOC: %d toc + %d inline → %d merged", len(toc_items), len(inline_items), len(merged))
    return _dedup_and_filter(merged)


# ── 备选：从 preprocessed 文本反向提取 TOC ──
def extract_toc_from_preprocessed(preprocessed_md: str) -> list[dict]:
    """
    从 preprocessed Markdown 里反向解析所有 # / ## / ### 标记的标题。
    优点：与 ReactMarkdown 渲染的 DOM 100% 对齐 → 点击必然能跳转。
    调用时机：preprocess_text 之后（前端实际渲染的文本）。

    去重策略（2026-08-07 调整）：同标题出现多次时（如 PDF 目录页的副本 + 正文真标题），
    保留【后出现】的版本（正文标题通常更规范、无断裂空格），面板位置保持首次出现处。
    """
    import re as _re
    if not preprocessed_md:
        return []
    # 归一化文本 → 条目（dict 保序：首次出现位置，后出现者覆盖值）
    store: dict = {}
    for line in preprocessed_md.split("\n"):
        m = _re.match(r"^(#{1,4})\s+(.*)$", line.strip())
        if not m:
            continue
        text = m.group(2).strip()
        # 跳过明显不是标题的（过长或句末标点结尾且非数字编号）
        if not text or len(text) > 80:
            continue
        if _re.search(r"[。！？，,：:；;]$", text) and not _re.match(r"^\d", text):
            continue
        # 我们的 TOC level 体系：L0/1/2 = h1/h2/h3
        level = len(m.group(1)) - 1
        normalized = _re.sub(r"[：:\s]+", "", text)
        if not normalized:
            continue
        store[normalized] = {
            "id": _make_heading_id(text),
            "text": text,
            "level": level,
        }

    # 前缀式标题过滤：只有"第三部"这种裸前缀且存在更长版本（"第三部分 工作原则"）时跳过
    norm_keys = list(store.keys())
    result = []
    for normalized, item in store.items():
        text = item["text"]
        if _re.match(r"^第[一二三四五六七八九十百\d]+[分区部篇卷]\s*$", text):
            m2 = _re.match(r"^(第[一二三四五六七八九十百\d]+.)", text)
            head2 = m2.group(1) if m2 else ""
            if head2 and any(k.startswith(head2) and len(k) > len(normalized) for k in norm_keys):
                continue
        result.append(item)
    return result


# ── 合并：TOC + Inline（去重，TOC条目优先）──
def _merge_toc_and_inline(toc_items: list[dict], inline_items: list[dict]) -> list[dict]:
    """TOC 条目优先级更高，inline 补充未覆盖的章节"""
    merged = list(toc_items)
    toc_ids = {item["id"] for item in toc_items}
    # 也按空格标准化后的文本去重（"第一部分我的历程" vs "第一部分 我的历程"）
    toc_texts_norm = {re.sub(r"\s+", "", item["text"]) for item in toc_items}
    
    for item in inline_items:
        if item["id"] not in toc_ids:
            norm_text = re.sub(r"\s+", "", item["text"])
            if norm_text not in toc_texts_norm:
                merged.append(item)
    
    return merged


# ── Step 1: TOC 区域检测与解析 ──
def _extract_from_toc_section(lines: list[str]) -> list[dict]:
    """在文档中找到'目录'/'Contents'区域，解析层次化 TOC"""

    # 1a. 找到 TOC 起点（"目录" 可能是整行，也可能是行首）
    toc_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        # "目录" 作为独立行，或者行首匹配
        if stripped.lower() in ("目录", "contents", "目　录", "目 录") or stripped.lower().startswith("目录"):
            toc_start = i
            break
    if toc_start is None:
        return []

    # 1b. 找到 TOC 终点
    toc_end = None
    non_toc_count = 0  # 连续非TOC行计数，≥3则退出
    for i in range(toc_start + 1, min(toc_start + 80, len(lines))):
        line = lines[i].strip()
        if not line:
            non_toc_count += 1
            continue

        normalized = _normalize_pdf_whitespace(line)

        # TOC条目特征：短行(<50chars) 或有编号，且非元数据
        is_metadata = bool(re.match(r"^\s*(书名|作者|封面|版权|ISBN|出版|定价|印次|版次|字数|译者|审校|后记|致谢|参考文献|附录|此书|版权)[：:\s]", normalized))
        is_toc_like = not is_metadata and (
            len(line) < 50 or
            bool(re.match(r"^\s*\d{1,2}\s", line)) or
            bool(re.match(r"^(第|chapter|part)", normalized, re.IGNORECASE)) or
            bool(re.match(r"^[一二三四五六七八九十]+\s*[、.]", normalized))
        )

        if not is_toc_like:
            non_toc_count += 1
            if non_toc_count >= 3:  # 连续3行不像TOC → 退出
                toc_end = i - 3
                break
        else:
            non_toc_count = 0

        # 章节标记（第一个真正章节）→ TOC结束
        if re.match(r"^第[一二三四五六七八九十百\d]+\s*章", normalized):
            toc_end = i
            break

    if toc_end is None:
        toc_end = min(toc_start + 60, len(lines))

    # 1c. 提取 TOC 条目
    toc_lines = lines[toc_start + 1 : toc_end]
    items: list[dict] = []
    seen = set()

    for line in toc_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 跳过纯目录装饰、页码、空白
        if re.match(r"^[iIvVxX\d]+$", stripped):
            continue
        if len(stripped) < 2:
            continue
        if stripped.lower() in ("目录", "contents", "目　录"):
            continue

        # 计算缩进层级
        indent = len(line) - len(line.lstrip())
        level = 0 if indent < 3 else (1 if indent < 8 else 2)

        # 清理 PDF 文字间多余空格（"第 一 部 分" → "第一部分"）
        text = _normalize_pdf_whitespace(stripped)
        if not text or len(text) < 2:
            continue

        # 过滤元数据噪声行（非TOC条目）
        if re.match(r"^\s*(书名|作者|封面|版权|ISBN|出版|定价|印次|版次|字数|译者|审校|后记|致谢|参考文献|附录|感言|.*克拉克)|[：:]", text):
            continue

        # 如果是编号子项（"1 xxx", "2 xxx"），提升一级
        if re.match(r"^\d{1,2}\s", text) and len(text) < 50 and level == 0:
            level = 1

        item_id = _make_heading_id(text)
        if item_id in seen:
            continue
        seen.add(item_id)

        items.append({"id": item_id, "text": text, "level": level})

    return items


# ── Step 2: 行内标题扫描 ──
def _extract_inline_headings(lines: list[str]) -> list[dict]:
    """扫描全文，提取行内标题模式 + 小说短标题"""
    items: list[dict] = []
    seen = set()
    total = len(lines)
    pattern_count = 0  # 第N章 / 数字编号 等明确标题数量

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # PDF 空格标准化
        stripped = _normalize_pdf_whitespace(stripped)
        if not stripped:
            continue

        # 1. 明确的标题模式（第N章 / 数字编号 / Markdown#）
        result = _match_heading_line(stripped)
        if result:
            level, text = result
            item_id = _make_heading_id(text)
            if item_id not in seen:
                seen.add(item_id)
                pattern_count += 1
                items.append({"id": item_id, "text": text, "level": level})
            continue

        # 2. 上下文感知短标题：仅在完全没有明确标题时启用（纯小说/无编号文档）
        if pattern_count == 0 and total < 5000:  # 无任何明确标题 + 短文档
            _maybe_add_short_title(i, stripped, lines, total, items, seen)

    return items


def _maybe_add_short_title(i: int, stripped: str, lines: list[str], total: int,
                           items: list[dict], seen: set[str]):
    """如果是小说/无编号标题（如'序曲''上篇 大学'），添加到目录"""
    if not (3 <= len(stripped) <= 20):
        return
    
    # 必须前后都有空行（独立行标题特征）
    prev_blank = (i == 0 or not lines[i-1].strip())
    next_blank = (i >= total-1 or not lines[i+1].strip())
    if not prev_blank or not next_blank:
        return
    
    # 排除纯噪声
    has_punct = bool(re.search(r"[。！？，,：:；;]", stripped))
    is_noise = bool(re.match(
        r"^[\d\s\.\-/：:]+$|"                # 纯数字/标点
        r"^\d{4}[/-]\d|"                      # 日期
        r"^\d+$|"                              # 纯数字
        r"^[\"\'""'']\s*[\"\'""'']$|"      # 纯引号
        r"^[。！？…\-\—\~\~]+$|"               # 纯中文标点
        r"^\s*(书名|作者|封面|版权|ISBN|出版|定价|后记|致谢|参考文献|附录|此书|版权)[：:]",
        stripped
    ))
    if has_punct or is_noise:
        return
    
    item_id = _make_heading_id(stripped)
    if item_id not in seen:
        seen.add(item_id)
        items.append({"id": item_id, "text": stripped, "level": 2})


def _normalize_pdf_whitespace(text: str) -> str:
    """PDF 文字在汉字间常有空格（'第 一 部 分' → '第一部分'）"""
    # 迭代直到所有相邻 CJK 字符间的空格被移除
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", text)
    return text.strip()


def _match_heading_line(text: str) -> tuple[int, str] | None:
    """判断单行是否为标题，返回 (level, text) 或 None"""
    if len(text) < 2 or len(text) > 200:
        return None

    # 1. Markdown heading
    m = re.match(r"^(#{1,6})\s+(.+)$", text)
    if m:
        title = m.group(2).strip().replace("*", "").replace("_", "").replace("`", "")
        title = _truncate_title(title)
        return (len(m.group(1)), title) if len(title) >= 2 else None

    # 2. 第N章/N节 模式（"部分"优先匹配，避免拆成"部"+"分"）
    m = re.match(
        r"^(Chapter\s+\d+|第\s*[一二三四五六七八九十百\d]+\s*(?:部分|[章节回课部篇卷]))\s*[:：、\s]?(.*)$",
        text, re.IGNORECASE,
    )
    if m:
        prefix = m.group(1)
        suffix = (m.group(2) or "").strip()

        # 排除章节结束标记
        if re.match(r'^[（(]?\s*完\s*[)）]?[。.！!]?\s*$', suffix or ""):
            return None
        # 排除正文引用
        if re.search(r"(介绍了|描述了|曾经|就已经|将会|需要|应该|主要|首先|最后|接着|然后|可以)\s*\S", suffix or ""):
            return None

        is_super = bool(re.search(r"[部篇卷]", prefix))
        level = 0 if is_super else 1
        title = _truncate_title(f"{prefix} {suffix}" if suffix else prefix)
        return (level, title) if len(title) >= 2 else None

    # 3. 中文序号标题 "一、 引言"
    m = re.match(r"^([一二三四五六七八九十]+、)\s*(.+)$", text)
    if m and len(text) < 40:
        title = text.strip().replace("*", "").replace("_", "")
        return (2, title) if len(title) >= 2 else None

    # 4. 数字编号 "1. 本质是什么"
    m = re.match(r"^(\d+)\.\s+(.{2,})$", text)
    if m and len(text) < 50 and not re.search(r"[。！？，：；]", text):
        title = text.strip()
        # 过滤纯数字/日期
        if not re.match(r"^\d{4}[/-]\d", title):
            return (2, title) if len(title) >= 2 else None

    return None


def _truncate_title(text: str, max_len: int = 60) -> str:
    """标题太长说明正文粘一起了 → 截取到第一个标点"""
    if len(text) <= max_len:
        return text.strip()
    parts = re.split(r"[。！？，,\n]", text, maxsplit=1)
    return parts[0].strip()


# ── 去重与过滤 ──
def _dedup_and_filter(items: list[dict]) -> list[dict]:
    """去重 + 过滤碎片 + 排除正文噪声"""
    if not items:
        return []

    seen = set()
    result = []
    for item in items:
        text = item["text"]
        item_id = item["id"]
        
        # 排除碎片（<3字或纯数字编号）
        if len(text) < 3 and not re.match(r"^第[一二三]\s*[部分]", text):
            continue
        # 排除正文噪声（包含"介绍了/描述了/将会/需要/应该"等的数字编号行）
        if re.match(r"^\d+\.?\s+.{10,}", text) and re.search(
            r"(不要|可以|需要|应该|将会|首先|最后|接着|然后|主要|为了)",
            text
        ):
            continue
        
        if item_id not in seen and len(text) <= 80:
            seen.add(item_id)
            result.append(item)

    return result
