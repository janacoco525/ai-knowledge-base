"""
AI知识库 — 文本预处理器
在服务端完成分段/脚注清理/标题标记，前端直接取用渲染
与前端 DocEditor.tsx 的 preprocessContent() + isStructuralHeading() 逻辑对等
"""
import re
import logging
import html as html_module

logger = logging.getLogger("ai_kb.text_preprocessor")

# 特殊节标题（短行独立成段，不可被吞并进正文）——模块级共享，Step2/Step3 都用
SPECIAL_SECTIONS = (
    "目录", "中文版序", "导言", "序言", "前言", "绪论", "引言",
    "结语", "结论与展望", "参考文献", "致谢", "摘要", "Abstract",
    "References", "Conclusion", "后记", "附录", "导读",
    "中文版", "译者序", "作者的话", "代序", "推荐语", "原序",
    "中译本总序",  # 2026-08-07 新增：地球编年史等译著的序言曾被吞进正文导致目录缺失
)


def _norm_special(text: str) -> str:
    """特殊节标题归一化（2026-08-07）：剥离【】括号与全角空格，
    让 '【前　言】' 也能被识别为 '前言'（地球编年史2 曾因此漏检）"""
    return re.sub(r"[【】\u3000]", "", text.strip())


def _collapse_cjk_spaces(text: str) -> str:
    """折叠汉字之间的孤立空格（2026-08-07）：PDF 提取常把词拆成 '第一部 分'，
    标题展示与锚点生成前需还原为 '第一部分'（与 toc_extractor._normalize_pdf_whitespace 同规则）"""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", text)
    return text


def _norm_heading_spacing(text: str) -> str:
    """标题空格归一化（2026-08-07）：折叠汉字间空格修复词内断裂（'第一部 分'→'第一部分'），
    再由 _detect_heading 按真实前缀重建分隔空格（'第一部分 我的历程'）。
    前缀与标题间原有空格（'第一章 背景介绍'）则保留分隔。
    检测失败/长后缀被截时保持折叠结果，词内断裂比缺个分隔空格更难看"""
    # 断裂判定：前缀本身完整但其后紧跟 '空格+结构性字符'（'第一部 分'）才算词被拆；
    # 前缀内部含空格（'第十二 章'）折叠后自然修复；前缀后接普通标题字（'第一章 背景'）不算
    m_pre = re.match(
        r"^(第\s*[一二三四五六七八九十百\d]+\s*[章节回课分部篇卷])(\s+)([\u4e00-\u9fff])", text
    )
    prefix_broken = bool(
        m_pre and not re.search(r"\s", m_pre.group(1)) and m_pre.group(3) in "章节回课分部篇卷"
    )
    t = _collapse_cjk_spaces(text)
    h = _detect_heading(t)
    if not h:
        return t
    m_p = re.match(r"^(第\s*[一二三四五六七八九十百\d]+\s*[章节回课分部篇卷])(.*)$", h[1])
    if prefix_broken and m_p:
        # 前缀被 PDF 空格拆过：正则贪婪断在 '第一部'，后缀首字（'分'）实属前缀，
        # 归位重建 → '第一部分 我的历程'
        suf = m_p.group(2).strip()
        if re.match(r"^[\u4e00-\u9fff]", suf):
            fixed_prefix = _collapse_cjk_spaces(m_p.group(1)) + suf[0]
            rest = suf[1:].strip()
            return (fixed_prefix + " " + rest) if rest else fixed_prefix
    # 前缀未断裂：检测结果即规范形（前缀 + 分隔空格 + 后缀）；
    # len 守卫防 is_super 规则丢弃长后缀，此时保持折叠结果不丢字
    if len(h[1]) >= len(t):
        return h[1]
    return t

try:
    import markdown as md_lib
    _MARKDOWN_AVAILABLE = True
except ImportError:
    _MARKDOWN_AVAILABLE = False
    md_lib = None


def preprocess_text(raw_text: str) -> str:
    """
    对全文做服务端预处理，返回可直接渲染的 Markdown 文本。
    处理步骤：清理 → 智能分段 → 标题标记 → 脚注修复。
    """
    if not raw_text:
        return ""

    # ── Step 1：基础清理 ──
    text = raw_text
    text = re.sub(r"---?\s*第\s*\d+\s*页\s*/\s*共\s*\d+\s*页\s*-{0,3}", "", text)
    text = re.sub(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页", "", text)
    text = re.sub(r"\b(?:ISBN|ISBN-13|ISBN-10)\s*[：:]\s*[\d\-Xx]+", "", text)
    text = re.sub(r"^[（(]?\s*(?:全[书本]?\s*)?[完终結结]\s*[)）]?\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d{1,4}\s*/\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d{4}[-\/]\d{1,2}[-\/]\d{1,2}\s*$", "", text, flags=re.MULTILINE)
    # 清理 EPUB 脚注行
    text = re.sub(r"^\s*\[\d+\]\s+.+$", "", text, flags=re.MULTILINE)
    # 清理纯页码行 "-2-"
    text = re.sub(r"^\s*-\d+-\s*$", "", text, flags=re.MULTILINE)
    # 清理纯脚注数字残留 " [1] "
    text = re.sub(r"^\s*\[\d+\]\s*$", "", text, flags=re.MULTILINE)
    # 折叠过多空行
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # 行首多余空格
    text = re.sub(r"^ {4,}", "  ", text, flags=re.MULTILINE)
    # 幻灯片型 PDF：行内项目符号 • 未换行（2026-08-07）——拆成列表行，
    # 避免多个要点粘在同一行；行首已有的 • 不受影响（前面无字符）
    text = re.sub(r"(?<=\S)[ \t]*•[ \t]*", "\n- ", text)
    # 幻灯片 PDF 勾选框字形残留：句末标点/汉字后紧跟孤立 'Y'（Wingdings 类字体的 ✓ 误提取）
    text = re.sub(r"(?<=[。！？；：）」》〉\u4e00-\u9fff])Y(?=\s*$)", "", text, flags=re.MULTILINE)

    # ── Step 2：智能分段 ──
    blocks = re.split(r"\n\n+", text)
    processed_blocks = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # 标题块保持原样
        first_line = block.split("\n")[0].strip()
        if first_line.startswith("#") or re.search(
            r"^第[一二三四五六七八九十百\d]+[章节部篇卷]", first_line
        ):
            processed_blocks.append(block)
            continue

        # 块内按行合并：行末无句末标点 → 与下行合并
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue

        merged = []
        buf = lines[0]
        sentences_in_buf = 1
        # 标记 buf 是否是标题行（标题行后下一行不能合并进去）
        buf_is_heading = bool(
            re.search(r"^第[一二三四五六七八九十百\d]+[章节部篇卷]", buf) or
            re.match(r"^第\s*\d+\.?\s", buf) or
            re.match(r"^#+\s", buf) or
            # 特殊节标题（含【前言】类括号/全角空格变体，2026-08-07）
            _norm_special(buf) in SPECIAL_SECTIONS
        )

        for line in lines[1:]:
            # 当前行如果像标题（第N章/N. 等），强制从新段落开始，不与上行合并
            stripped_line = line.strip()
            is_heading_line = bool(
                stripped_line.startswith("#") or
                re.search(r"^第[一二三四五六七八九十百\d]+[章节部篇卷]", stripped_line) or
                re.match(r"^第\s*\d+\.?\s", stripped_line) or  # "第43章"
                # 明确子标题模式：省略号结尾 OR 已是 markdown 标题
                stripped_line.endswith("……") or
                # 特殊节标题（归一化后匹配，2026-08-07）
                _norm_special(stripped_line) in SPECIAL_SECTIONS
            )
            if is_heading_line and buf:
                merged.append(buf)
                buf = line
                buf_is_heading = True
                sentences_in_buf = 1
                continue
            # 如果 buf 是标题行，下一行不能合并（避免标题吞掉正文）
            if buf_is_heading and buf:
                merged.append(buf)
                buf = line
                buf_is_heading = False
                sentences_in_buf = 1
                continue
            # 防吞并护栏（2026-08-07）：目录页码行（第X章开头/数字编号开头且以页码数字结尾）
            # 不允许与下行合并，否则目录页被吞成巨型单行。
            # 注意：不能拦所有"无标点短行"——正文短行换行仍需合并（见单测 merges_short_lines）
            if re.search(r"\d\s*$", buf) and re.match(r"^[第\d一二三四五六七八九十]", buf):
                merged.append(buf)
                buf = line
                buf_is_heading = False
                sentences_in_buf = 1
                continue
            # 当前行是新句子开头
            is_new_sentence = bool(re.search(r"[。！？」\"」』\)）]$", buf))
            # 当前行是新的编号标题（数字+空格开头）→ 强制flush
            is_new_numbered_heading = bool(re.match(r"^\d+(\.\d+)?\s+\S", stripped_line))
            if (is_new_sentence and len(buf) > 60 and sentences_in_buf >= 2) or is_new_numbered_heading:
                merged.append(buf)
                buf = line
                buf_is_heading = False
                sentences_in_buf = 1
            else:
                buf += line
                # 粗估句子数
                sentences_in_buf += sum(1 for c in line if c in "。！？」")

        if buf:
            merged.append(buf)

        processed_blocks.append("\n\n".join(merged))

    # ── Step 3：清理碎片块 ──
    cleaned = []
    for p in processed_blocks:
        p = p.strip()
        if not p:
            continue
        # 短碎片合并到上一段 — 但特殊节标题不能合并（模块级清单，2026-08-07）
        is_special = _norm_special(p) in SPECIAL_SECTIONS
        if len(p) < 15 and not re.match(r"^[#第\d]", p) and not is_special and cleaned:
            cleaned[-1] += p
        else:
            cleaned.append(p)

    text = "\n\n".join(cleaned)

    # ── Step 4：标题标记 ──
    lines = text.split("\n")
    result_lines = []
    in_code_block = False

    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_code_block = not in_code_block
            result_lines.append(line)
            continue
        if in_code_block or not trimmed:
            result_lines.append(line)
            continue

        if trimmed.startswith("#"):
            # 已有 # 标记的标题也归一化空格（2026-08-07）：折叠词内空格但保留前缀后分隔空格
            m_hash = re.match(r"^(#{1,6})\s+(.+)$", trimmed)
            if m_hash:
                result_lines.append(m_hash.group(1) + " " + _norm_heading_spacing(m_hash.group(2)))
            else:
                result_lines.append(line)
            continue

        # 检查是不是标题
        heading = _detect_heading(trimmed)
        if heading:
            level, heading_text = heading
            # level 0/1/2 → markdown 1/2/3 个 #  (level 0 = 部分/前言，至少给 1 个 #)
            hash_count = max(1, level + 1)
            # 检查是否 heading_text 后面还粘了正文（合并残留）→ 切掉
            # 标题应以第N章/N节/部分/章/部/篇/卷/数字编号 开头，正文不应再继续
            tail = trimmed[len(heading_text):].strip()
            # 标题文本归一化空格（2026-08-07）：'第一部 分 我的历程'→'第一部分 我的历程'，
            # 同时保留 '第一章 背景介绍' 前缀后的分隔空格
            heading_out = _norm_heading_spacing(heading_text)
            if tail and _looks_like_body(tail):
                # heading 是 trimmed 头部，把尾部作为单独段落输出
                result_lines.append("#" * hash_count + " " + heading_out)
                result_lines.append(tail)
            else:
                result_lines.append("#" * hash_count + " " + heading_out)
        else:
            result_lines.append(line)

    return "\n".join(result_lines)


def _looks_like_body(text: str) -> bool:
    """判断一段文本看起来像正文（不是标题的延伸）。
    正文的特征：包含动词、含句末标点、长度>20、不是单个名词短语
    """
    if not text:
        return False
    if len(text) < 2:
        return False
    # 含句末标点 → 必是正文（包括中文省略号 ……）
    if re.search(r"[。！？!?……]", text):
        return True
    # 长度>15 且包含常用动词 → 倾向正文
    if len(text) > 15 and re.search(
        r"(是|了|有|在|和|与|及|为|的|会|要|能|会|着|过|向|把|被|让|给|从|到|由|为|对)",
        text
    ):
        return True
    # 长度>20 但不是已知的短标题模式（1./2.等）→ 倾向正文
    if len(text) > 20:
        return True
    return False


def _detect_heading(text: str):
    """与前端 isStructuralHeading 逻辑对等"""
    if not text:
        return None

    # Chapter/Part
    m = re.match(
        r"^(Chapter\s+\d+|第\s*[一二三四五六七八九十百\d]+\s*[章节回课分部篇卷])\s*[:：、\s]?\s*(.*)$",
        text, re.IGNORECASE,
    )
    if m and len(text) < 90:
        prefix = m.group(1)
        suffix = (m.group(2) or "").strip()

        if re.match(r"^[（(]?\s*完\s*[)）]?[。.！!]?\s*$", suffix):
            return None
        if re.match(r"^[，,]", suffix):
            return None
        if re.search(
            r"(介绍了|阐述了|描述了|讨论了|探讨了|介绍过|阐述过|描述过|讨论过|探讨过|曾经|就已经|将会|可以|需要|应该|主要|首先|最后|接着|然后)\s*\S",
            suffix,
        ):
            return None

        is_super = bool(re.search(r"[部篇卷]", prefix))
        level = 0 if is_super else 1
        # 部分/篇/卷 标题：只取短小后缀（≤15字、无其他标题/正文特征）
        # 章节/章/节 标题：保留后缀作为副标题
        if is_super:
            if suffix and len(suffix) <= 15 and not _looks_like_body(suffix) and not re.search(r"\d", suffix):
                heading_text = f"{prefix} {suffix}"
            else:
                heading_text = prefix
        else:
            heading_text = f"{prefix} {suffix}" if suffix else prefix
        return (level, heading_text.strip())

    # 中国序号 "一、" "二、"
    m = re.match(r"^([一二三四五六七八九十]+、)\s*(.+)$", text)
    if m and len(text) < 40 and not re.search(r"[。！？，,：:；;]", text):
        return (2, text.strip())

    # Numeric "1.2.3 Title"
    m = re.match(r"^(\d+(\.\d+){1,3})\s+(.+)$", text)
    if m and len(text) < 60 and not re.search(r"[。！？，,：:；;]", text):
        title = m.group(3)
        # 标题部分也是纯数字（如"40  40  40"）→ 不是真标题
        if re.match(r"^[\d\s\u3000\.\-]+$", title):
            pass
        # 标题部分 3+ 个数字段被空格分隔 → 页码序列
        elif len(re.findall(r"\b\d+\b", title)) >= 3:
            pass
        # 数字超过 70% → 纯数字噪声
        elif len(re.findall(r"\d", title)) >= len(title) * 0.7:
            pass
        else:
            level = min(3, len(m.group(1).split(".")))
            return (level, text.strip())

    # Simple numeric "1. Title"
    m = re.match(r"^(\d+)\.\s+([^。，,！!？?：:；;、\n]+)$", text)
    if m and len(text) < 30 and not re.search(r"[告诉应当可以需要必须应该能够会要想让去].{2,}", text):
        return (2, text.strip())

    # 简单数字 "1 Title"（中文版章节常见格式：无点号）
    m = re.match(r"^(\d+)\s+(.{2,40})$", text)
    if m and len(text) < 50 and not re.search(r"[告诉应当可以需要必须应该能够会要想让去].{2,}", text) and not re.search(r"[。！？，,：:；;]", text):
        title = m.group(2)
        # 标题部分也是纯数字/页码序列（如"40  40  40"）→ 拒绝
        if re.match(r"^[\d\s\u3000\.\-]+$", title):
            pass
        # 标题部分是 3+ 个数字段被空格分隔（如"40 4 20 6 40 7"）→ 页码序列
        elif len(re.findall(r"\b\d+\b", title)) >= 3:
            pass
        # 标题部分数字超过 70%（防年份"1995—2010"误伤，但拦截纯页码）
        elif len(re.findall(r"\d", title)) >= len(title) * 0.7:
            pass
        else:
            return (1, text.strip())

    # Special sections（归一化匹配：'【前　言】' → '前言'，2026-08-07）
    special = [
        "前言", "引言", "序言", "导言", "绪论", "结语", "结论与展望",
        "参考文献", "致谢", "目录", "Abstract", "References", "Conclusion",
        "中文版序", "译者序", "中文版", "作者的话", "代序", "推荐语", "原序", "导读",
        "中译本总序",
    ]
    norm = _norm_special(text)
    has_sentence_end = bool(re.search(r"[。！？!?]", norm))
    if norm in special:
        return (1, norm)
    if len(norm) < 25 and not has_sentence_end and any(norm.startswith(s) or norm.endswith(s) for s in special):
        return (1, norm)
    if re.match(r"^关于[\u4e00-\u9fa5]{2,15}$", norm) and not has_sentence_end:
        return (1, norm)

    return None


def render_to_html(preprocessed_md: str) -> str:
    """
    将预处理后的 Markdown 渲染为 HTML，供前端直接注入。
    使用 Python markdown 库，输出带语义标签的 HTML。
    如果 markdown 库不可用，返回转义后的纯文本 HTML。
    """
    if not preprocessed_md or not _MARKDOWN_AVAILABLE:
        safe = html_module.escape(preprocessed_md or "")
        return f"<pre>{safe}</pre>"

    md = preprocessed_md

    html_output = md_lib.markdown(
        md,
        extensions=["extra", "codehilite", "toc"],
        output_format="html5",
    )

    # 清理 markdown 库可能产生的空段落
    html_output = re.sub(r"<p>\s*</p>", "", html_output)

    return html_output


def render_to_html_fast(md: str) -> str:
    """
    轻量级 HTML 渲染：不依赖 markdown 库，纯正则转换。
    作为 markdown 库不可用时的降级方案。
    支持：标题/段落/粗体/斜体/列表/引用块/代码
    """
    if not md:
        return ""

    lines = md.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # 空行 → 跳过
        if not line.strip():
            i += 1
            continue

        # 代码块 (```...```)
        if line.strip().startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(html_module.escape(lines[i]))
                i += 1
            i += 1  # skip closing ```
            lang = line.strip()[3:].strip()
            lang_attr = f' class="language-{lang}"' if lang else ""
            result.append(f"<pre><code{lang_attr}>{"\n".join(code_lines)}</code></pre>")
            continue

        # 标题
        h_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if h_match:
            level = len(h_match.group(1))
            heading_id = _make_heading_id(h_match.group(2))
            result.append(f'<h{level} id="{heading_id}">{html_module.escape(h_match.group(2))}</h{level}>')
            i += 1
            continue

        # 水平线
        if re.match(r"^[-*_]{3,}\s*$", line):
            result.append("<hr>")
            i += 1
            continue

        # 无序列表
        ul_match = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if ul_match:
            result.append("<ul>")
            while i < len(lines):
                m = re.match(r"^(\s*)[-*+]\s+(.+)$", lines[i])
                if not m:
                    break
                result.append(f"<li>{_render_inline(m.group(2))}</li>")
                i += 1
            result.append("</ul>")
            continue

        # 有序列表
        ol_match = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
        if ol_match:
            result.append("<ol>")
            while i < len(lines):
                m = re.match(r"^(\s*)\d+\.\s+(.+)$", lines[i])
                if not m:
                    break
                result.append(f"<li>{_render_inline(m.group(2))}</li>")
                i += 1
            result.append("</ol>")
            continue

        # 引用块
        if line.strip().startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            result.append(f'<blockquote><p>{"<br>".join(html_module.escape(l) for l in quote_lines)}</p></blockquote>')
            continue

        # 普通段落
        para_lines = []
        while i < len(lines) and lines[i].strip():
            para_lines.append(lines[i].strip())
            i += 1
        if para_lines:
            text = " ".join(para_lines)
            result.append(f"<p>{_render_inline(text)}</p>")

    return "\n".join(result)


def _render_inline(text: str) -> str:
    """渲染行内格式：粗体、斜体、代码"""
    text = html_module.escape(text)
    # 粗体 **text** 或 __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    # 斜体 *text* 或 _text_
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"_(.+?)_", r"<em>\1</em>", text)
    # 行内代码 `code`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def _make_heading_id(text: str) -> str:
    """从标题文本生成 HTML id"""
    import re as _re
    cleaned = _re.sub(r"[*_`#\[\]()（）]", "", text)
    cleaned = _re.sub(r"\s+", "-", cleaned.strip())
    return cleaned.lower().rstrip("-")
