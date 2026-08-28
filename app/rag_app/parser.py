"""
AI知识库 - 文档解析模块
支持 PDF / DOCX / EPUB / MOBI / TXT / MD / 代码文件 的解析与分块
"""
import os, re, logging
from pathlib import Path
from typing import Optional
from app.rag_app.config import Config

logger = logging.getLogger("ai_kb.parser")

# 小说/图书文本清洗规则
_TEXT_CLEANUP_PATTERNS = [
    re.compile(r"---?\s*第\s*\d+\s*页\s*/\s*共\s*\d+\s*页\s*-{0,3}"),  # "--- 第2页 / 共139页 ---"
    re.compile(r"第\s*\d+\s*页\s*/\s*共\s*\d+\s*页"),                  # "第2页/共139页"
    re.compile(r"^-{3,}\s*\d+\s*/\s*\d+\s*-{3,}$", re.MULTILINE),     # "--- 2/139 ---"
    re.compile(r"^\s*\d{1,4}\s*/\s*\d{1,4}\s*$", re.MULTILINE),       # 纯页码 "2/139"
    re.compile(r"\b(?:ISBN|ISBN-13|ISBN-10)\s*[：:]\s*[\d\-Xx]+"),     # ISBN信息
    re.compile(r"版权信息\s*[\s\S]{0,200}?(?=\n\s*\n)"),               # 版权信息块（到空行为止）
    re.compile(r"更多免费电子书\s*[\s\S]{0,100}?(?=\n\s*\n)"),         # 广告引流
    re.compile(r"扫码关注|加微信|微信号|公众号\s*[\s\S]{0,50}"),        # 二维码引流
]

def clean_text(text: str) -> str:
    """清洗小说/图书文本中的页码标记和广告信息"""
    for pattern in _TEXT_CLEANUP_PATTERNS:
        text = pattern.sub("", text)
    # 合并多余空行（3个以上连续空行 → 2个空行）
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # 去除纯页码行（单独一行的数字）
    text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


# CJK 字符判定（中日韩统一表意文字 + 全角标点）：中文相邻行合并时不插空格
_CJK_EDGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]")


def _join_pdf_lines(lines: list) -> str:
    """把同一段落的物理行合并为一行：
    - 连字符断词还原（adapta- + tion → adaptation）
    - 中文相邻直接拼接（不插空格）
    - 其余用单空格连接
    """
    out = lines[0]
    for nxt in lines[1:]:
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and nxt[:1].islower():
            out = out[:-1] + nxt
        elif _CJK_EDGE.search(out[-1]) or _CJK_EDGE.search(nxt[0]):
            out += nxt
        else:
            out += " " + nxt
    return out


def join_chunk_texts(texts: list) -> str:
    """把分块文本拼回全文（2026-08-07）：先剪掉分块 overlap 携带的重复前缀。

    分块器（_chunk_pages）会把上一块末尾的 chunk_overlap 字符作为下一块开头（检索重叠），
    旧实现直接 "\n\n".join 导致：每个边界 50 字重复文本 + 段落/标题被拦腰截断
    （正文格式错乱、目录标题断裂的根因）。此处精确匹配后剪除重复前缀，
    块内真实的段落边界（\n\n）原样保留，块间用单换行连接（预处理会再合并段内硬换行）。
    """
    if not texts:
        return ""
    out = [texts[0]]
    for cur in texts[1:]:
        prev = out[-1].rstrip()
        lead_ws = len(cur) - len(cur.lstrip())
        c_stripped = cur.lstrip()
        cut = 0
        max_k = min(len(prev), len(c_stripped), 120)
        for k in range(max_k, 7, -1):
            if prev.endswith(c_stripped[:k]):
                cut = lead_ws + k
                break
        out.append(cur[cut:] if cut else cur)
    return "\n".join(out)


def _reflow_pdf_text(text: str) -> str:
    """PDF 排版重整（2026-08-06）：
    - 去除行首缩进空格（PDF 居中/缩进排版携带的大量前导空格，
      还会被 markdown 误识为代码块）
    - 以空行为段落边界，合并段落内物理换行（每行 ~80 字符的硬换行）
    无空行的密集排版（如小说扫描版，一行一段）：只去行首空格不合并，
    避免段落粘连。
    """
    stripped = [l.strip() for l in text.split("\n")]
    # 全文无空行 → 密集排版（一行一段），不合并，仅去缩进
    if not any(l == "" for l in stripped):
        return "\n".join(stripped)
    paragraphs: list = []
    cur: list = []

    def flush():
        if cur:
            paragraphs.append(_join_pdf_lines(cur))
            cur.clear()

    for line in stripped:
        if not line:
            flush()
            continue
        cur.append(line)
    flush()
    return "\n\n".join(paragraphs)


class DocumentParser:
    """文档解析器：将各种格式文档转为统一的结构化文本块"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.chunk_size = self.config.CHUNK_SIZE
        self.chunk_overlap = self.config.CHUNK_OVERLAP

    def parse_file(self, file_path: str) -> list[dict]:
        path = Path(file_path)
        if not path.exists():
            logger.warning("File not found: %s", file_path)
            return []
        ext = path.suffix.lower()
        parsers = {
            ".pdf": self._parse_pdf,
            ".docx": self._parse_docx,
            ".doc": self._parse_docx,
            ".txt": self._parse_txt,
            ".md": self._parse_markdown,
            ".py": self._parse_code,
            ".js": self._parse_code,
            ".json": self._parse_code,
            ".csv": self._parse_txt,
            ".epub": self._parse_epub,
            ".mobi": self._parse_epub,
        }
        parser = parsers.get(ext)
        if not parser:
            logger.warning("Unsupported format: %s", ext)
            return []
        pages = parser(file_path)
        chunks = self._chunk_pages(pages, str(path))
        domain = self._infer_domain(str(path))
        for chunk in chunks:
            chunk["domain"] = domain
        
        # 提取 EPUB 原生 TOC 并注入到第一个 chunk 的 metadata
        if ext == ".epub" or ext == ".mobi":
            from zipfile import ZipFile
            with ZipFile(file_path, "r") as zf:
                # 找 OPF 路径
                try:
                    container = zf.read("META-INF/container.xml").decode("utf-8")
                    m = re.search(r'full-path="([^"]+)"', container)
                    opf_path = m.group(1) if m else ""
                except Exception:
                    opf_path = ""
                if opf_path:
                    native_toc = self._extract_epub_toc(zf, opf_path)
                    if native_toc and chunks:
                        chunks[0]["_native_toc"] = native_toc
                        logger.info("EPUB native TOC: %d entries", len(native_toc))
        logger.info("%s -> %d chunks", path.name, len(chunks))
        return chunks

    def _parse_pdf(self, file_path: str) -> list[dict]:
        pages = []
        try:
            import fitz
            doc = fitz.open(file_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                # 优先用 sort=True 按阅读顺序输出，解决中文排版错位
                text = page.get_text("text", sort=True)
                # 如果乱码比例过高（非常用字符多），回退到 blocks 模式
                if text.strip() and self._garbled_ratio(text) > 0.15:
                    text = self._extract_pdf_blocks(page)
                if text.strip():
                    # 排版重整：去行首缩进空格 + 合并段落内物理换行（2026-08-06）
                    text = _reflow_pdf_text(text)
                    text = clean_text(text)
                    pages.append({"page_num": page_num + 1, "text": text.strip()})
            doc.close()
        except Exception as e:
            logger.error("PDF parse error: %s", e)
        return pages

    @staticmethod
    def _garbled_ratio(text: str) -> float:
        """检测乱码比例：非常用中文字符 + 乱码符号占比"""
        if not text:
            return 0.0
        total = 0
        garbled = 0
        for ch in text:
            if ch.isspace() or ch in ".,;:!?，。；：！？""''（）()【】[]《》""''、—…·":
                continue
            total += 1
            cp = ord(ch)
            # 常用中文范围 CJK统一汉字基本区 + 扩展A
            if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
                continue
            # 常用拉丁字母和数字
            if 0x0020 <= cp <= 0x007E:
                continue
            # 全角字母数字
            if 0xFF01 <= cp <= 0xFF5E:
                continue
            garbled += 1
        return garbled / max(total, 1)

    @staticmethod
    def _extract_pdf_blocks(page) -> str:
        """用 blocks 模式提取，按位置排序拼接，对乱码 PDF 更友好"""
        blocks = page.get_text("blocks")
        # blocks: [(x0, y0, x1, y1, text, block_no, block_type)]
        # 只取文本块(type=0)，按 y0→x0 排序
        text_blocks = [b for b in blocks if b[6] == 0]
        text_blocks.sort(key=lambda b: (round(b[1], 1), b[0]))
        lines = []
        for b in text_blocks:
            line = b[4].strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _parse_docx(self, file_path: str) -> list[dict]:
        pages = []
        try:
            from docx import Document
            doc = Document(file_path)
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            if text.strip():
                pages.append({"page_num": 1, "text": text.strip()})
        except Exception as e:
            logger.error("DOCX parse error: %s", e)
        return pages

    def _parse_txt(self, file_path: str) -> list[dict]:
        pages = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if text.strip():
                text = clean_text(text)
                pages.append({"page_num": 1, "text": text.strip()})
        except Exception as e:
            logger.error("TXT parse error: %s", e)
        return pages

    def _parse_markdown(self, file_path: str) -> list[dict]:
        """Markdown解析：按标题分块，保留标题层级信息"""
        pages = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if not text.strip():
                return pages

            # 按标题分块
            sections = self._split_markdown_by_headers(text)
            for i, (title, content, level) in enumerate(sections):
                if content.strip():
                    section_text = f"{'#' * level} {title}\n{content}" if title else content
                    pages.append({"page_num": i + 1, "text": section_text.strip()})
        except Exception as e:
            logger.error("Markdown parse error: %s", e)
        return pages

    def _split_markdown_by_headers(self, text: str) -> list[tuple]:
        """按Markdown标题分割，返回 (标题, 内容, 层级) 列表"""
        lines = text.split("\n")
        sections = []
        current_title = ""
        current_content = []
        current_level = 0
        in_code_block = False

        for line in lines:
            # 跟踪代码块状态
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                current_content.append(line)
                continue

            if in_code_block:
                current_content.append(line)
                continue

            # 检测标题行（不在代码块内）
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if header_match:
                # 保存前一个section
                if current_content or current_title:
                    sections.append((current_title, "\n".join(current_content), current_level))
                current_level = len(header_match.group(1))
                current_title = header_match.group(2).strip()
                current_content = []
            else:
                current_content.append(line)

        # 最后一个section
        if current_content or current_title:
            sections.append((current_title, "\n".join(current_content), current_level))

        # 如果没有找到标题，整体作为一个section
        if not sections:
            sections.append(("", text, 0))

        return sections

    # ── EPUB 原生 TOC 提取 ──
    @staticmethod
    def _extract_epub_toc(zf: "zipfile.ZipFile", opf_path: str) -> list[dict]:
        """
        从 EPUB NCX (EPUB2) 或 nav.xhtml (EPUB3) 提取原生目录。
        返回: [{id, text, level, href}, ...]  — 可直接作为 TOC 使用。
        """
        import xml.etree.ElementTree as ET
        
        # Step 1: 尝试找 NCX (EPUB2 — 绝大多数中文 EPUB 用这个)
        opf_dir = os.path.dirname(opf_path)
        try:
            opf_xml = zf.read(opf_path).decode("utf-8")
            opf_root = ET.fromstring(opf_xml)
        except Exception:
            return []
        
        # 找 spine 的 toc 属性 → NCX id
        ns_map = {"opf": "http://www.idpf.org/2007/opf"}
        spine_toc = opf_root.attrib.get("toc", "") or ""
        if not spine_toc:
            spine_el = (opf_root.find(".//opf:spine", ns_map) or opf_root.find(".//spine"))
            if spine_el is not None:
                spine_toc = spine_el.attrib.get("toc", "")
        
        ncx_href = None
        if spine_toc:
            manifest = opf_root.find(".//opf:manifest", ns_map) or opf_root.find(".//manifest")
            if manifest is not None:
                for item in manifest.findall(".//opf:item", ns_map) + manifest.findall(".//item"):
                    if item.attrib.get("id") == spine_toc:
                        ncx_href = item.attrib.get("href", "")
                        break
        
        # 如果 manifest 里没找到，盲搜 toc.ncx
        if not ncx_href:
            for name in zf.namelist():
                if name.lower().endswith("toc.ncx"):
                    ncx_href = name
                    break
        
        # 还没找到：查 manifest 中 media-type 为 application/x-dtbncx+xml 的 item
        if not ncx_href:
            manifest = opf_root.find(".//opf:manifest", ns_map) or opf_root.find(".//manifest")
            if manifest is not None:
                for item in manifest.findall(".//opf:item", ns_map) + manifest.findall(".//item"):
                    mt = item.attrib.get("media-type", "")
                    iid = item.attrib.get("id", "").lower()
                    if mt == "application/x-dtbncx+xml" or "ncx" in iid or "toc" in iid:
                        ncx_href = item.attrib.get("href", "")
                        break
        
        # Step 2: 解析 NCX（用正则避免 XML 命名空间问题）
        ncx_toc = []
        if ncx_href:
            try:
                full_ncx = os.path.normpath(os.path.join(opf_dir, ncx_href)).replace("\\", "/")
                ncx_xml = zf.read(full_ncx).decode("utf-8", errors="ignore")
                # 用正则直接提取 <navPoint> 块（忽略 XML 命名空间）
                nav_blocks = re.findall(r'<navPoint[^>]*>(.*?)</navPoint>', ncx_xml, re.DOTALL)
                for nav in nav_blocks:
                    label_m = re.search(r'<text>(.*?)</text>', nav)
                    src_m = re.search(r'content\s+src="([^"]+)"', nav)
                    if label_m:
                        from app.rag_app.toc_extractor import _make_heading_id
                        ncx_toc.append({
                            "id": _make_heading_id(label_m.group(1).strip()),  # 与前端 DOM ID 对齐
                            "text": label_m.group(1).strip(),
                            "level": 1,  # NCX 简单模式：全部 L1
                            "href": src_m.group(1) if src_m else "",
                        })
            except Exception as e:
                logger.warning("EPUB NCX parse failed: %s", e)
        
        # Step 3: 如果 NCX 为空，尝试 nav.xhtml (EPUB3)
        if not ncx_toc:
            for name in zf.namelist():
                if "nav.xhtml" in name.lower() or name.lower().endswith("nav.html"):
                    try:
                        html = zf.read(name).decode("utf-8", errors="ignore")
                        # 简单提取 <a> 标签（导航链接）
                        anchors = re.findall(r'<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>', html, re.DOTALL)
                        from app.rag_app.toc_extractor import _make_heading_id
                        for href, raw_label in anchors:
                            label = re.sub(r'<[^>]+>', '', raw_label).strip()
                            if label and len(label) < 80:
                                ncx_toc.append({
                                    "id": _make_heading_id(label),  # 与前端 DOM ID 对齐
                                    "text": label,
                                    "level": 1,
                                    "href": href,
                                })
                    except Exception as e:
                        logger.warning("EPUB nav.xhtml parse failed: %s", e)
        
        return ncx_toc

    def _parse_epub(self, file_path: str) -> list[dict]:
        """解析 EPUB/MOBI 电子书，提取纯文本并按章节分页"""
        import zipfile
        import xml.etree.ElementTree as ET
        from html.parser import HTMLParser

        pages = []
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                # Step 1: 找 container.xml 定位 OPF 文件
                try:
                    container_xml = zf.read("META-INF/container.xml").decode("utf-8")
                except KeyError:
                    logger.warning("EPUB: META-INF/container.xml not found in %s", file_path)
                    return pages

                root = ET.fromstring(container_xml)
                ns = {"ct": "urn:oasis:names:tc:opendocument:xmlns:container"}
                rootfile = root.find(".//ct:rootfile", ns)
                if rootfile is None:
                    logger.warning("EPUB: no rootfile in container.xml")
                    return pages
                opf_path = rootfile.attrib.get("full-path", "")

                # Step 2: 解析 OPF 获取 spine 顺序和 manifest
                opf_dir = os.path.dirname(opf_path)
                try:
                    opf_xml = zf.read(opf_path).decode("utf-8")
                except KeyError:
                    logger.warning("EPUB: OPF file %s not found", opf_path)
                    return pages

                opf_root = ET.fromstring(opf_xml)
                opf_ns = {
                    "opf": "http://www.idpf.org/2007/opf",
                    "dc": "http://purl.org/dc/elements/1.1/",
                }
                # Fallback: 有些 EPUB 不用命名空间
                manifest = opf_root.find(".//opf:manifest", opf_ns) or opf_root.find(".//{http://www.idpf.org/2007/opf}manifest") or opf_root.find(".//manifest")
                spine = opf_root.find(".//opf:spine", opf_ns) or opf_root.find(".//{http://www.idpf.org/2007/opf}spine") or opf_root.find(".//spine")
                if spine is None or manifest is None:
                    logger.warning("EPUB: missing spine/manifest in OPF")
                    return pages

                # 构建 id->href 映射
                id_to_href = {}
                for item in manifest.findall(".//{http://www.idpf.org/2007/opf}item") + manifest.findall(".//item"):
                    item_id = item.attrib.get("id", "")
                    href = item.attrib.get("href", "")
                    if item_id and href:
                        id_to_href[item_id] = href

                # Step 3: 按 spine 顺序提取各章节文本
                class TextStripper(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.texts = []
                        self.skip = False
                        self.stack = []  # 标签栈：判断 <a> 是否在列表/导航上下文（目录条目，2026-08-07）
                    def handle_starttag(self, tag, attrs):
                        if tag in ("script", "style", "head"):
                            self.skip = True
                        self.stack.append(tag)
                    def handle_endtag(self, tag):
                        if tag in ("script", "style", "head"):
                            self.skip = False
                        # 列表/导航内的 <a> 是目录条目，条目间需换行（三体合集 EPUB 目录曾粘成巨型单行）
                        if tag == "a" and any(t in ("ol", "ul", "nav", "dl") for t in self.stack):
                            self.texts.append("\n")
                        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                            self.texts.append("\n")
                        # 弹栈（容忍不闭合标签：从栈顶往下找最近一个同名标签）
                        for i in range(len(self.stack) - 1, -1, -1):
                            if self.stack[i] == tag:
                                del self.stack[i:]
                                break
                    def handle_data(self, data):
                        if not self.skip and data.strip():
                            self.texts.append(data.strip())

                spine_items = spine.findall(".//{http://www.idpf.org/2007/opf}itemref") + spine.findall(".//itemref")
                page_num = 0
                for itemref in spine_items:
                    ref_id = itemref.attrib.get("idref", "")
                    href = id_to_href.get(ref_id)
                    if not href:
                        continue
                    # 解析相对路径
                    full_href = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
                    try:
                        html_content = zf.read(full_href).decode("utf-8", errors="ignore")
                    except KeyError:
                        # 尝试直接匹配文件名
                        found = False
                        for name in zf.namelist():
                            if name.endswith(href) or name.endswith("/" + href):
                                html_content = zf.read(name).decode("utf-8", errors="ignore")
                                found = True
                                break
                        if not found:
                            continue

                    stripper = TextStripper()
                    try:
                        stripper.feed(html_content)
                    except Exception:
                        pass
                    # 2026-08-14：用空串拼接保留 handle_endtag 写入的段落换行；
                    # 旧" ".join 把 \n 全变成空格 → 整章无段落边界 → 分块退化为巨型 chunk
                    chapter_text = "".join(stripper.texts)
                    if chapter_text.strip():
                        page_num += 1
                        chapter_text = clean_text(chapter_text)
                        pages.append({"page_num": page_num, "text": chapter_text.strip()})

                # 如果 spine 解析失败或无内容，回退到提取所有 XHTML
                if not pages:
                    for name in zf.namelist():
                        if name.lower().endswith((".xhtml", ".html", ".htm")):
                            try:
                                html_content = zf.read(name).decode("utf-8", errors="ignore")
                            except Exception:
                                continue
                            stripper = TextStripper()
                            try:
                                stripper.feed(html_content)
                            except Exception:
                                pass
                            chapter_text = "".join(stripper.texts)
                            if chapter_text.strip():
                                page_num += 1
                                chapter_text = clean_text(chapter_text)
                                pages.append({"page_num": page_num, "text": chapter_text.strip()})

        except zipfile.BadZipFile:
            logger.warning("EPUB: not a valid ZIP file: %s", file_path)
        except Exception as e:
            logger.error("EPUB parse error for %s: %s", file_path, e)

        logger.info("EPUB %s -> %d chapters extracted", Path(file_path).name, len(pages))
        return pages

    def _parse_code(self, file_path: str) -> list[dict]:
        """代码文件解析：保留文件名和基本结构"""
        pages = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if not text.strip():
                return pages

            ext = Path(file_path).suffix.lower()
            lang_map = {".py": "python", ".js": "javascript", ".json": "json", ".csv": "csv"}
            lang = lang_map.get(ext, "")

            # 用代码块标记包裹
            formatted = f"文件: {Path(file_path).name}\n```{lang}\n{text}\n```"
            pages.append({"page_num": 1, "text": formatted.strip()})
        except Exception as e:
            logger.error("Code parse error: %s", e)
        return pages

    def _chunk_pages(self, pages: list[dict], source_path: str) -> list[dict]:
        chunks = []
        current_chunk = ""
        current_page = 1
        # 硬上限（2026-08-14）：超过必须切 —— 防"整章无换行"退化成 5~7万字巨型 chunk，
        # 巨型 chunk 会稀释 BM25 词频导致检索失灵（经验：地球编年史"西琴"查不到）
        hard_limit = self.chunk_size * 4
        min_cut = max(int(self.chunk_size * 0.4), 1)
        for page in pages:
            text = page["text"]
            current_chunk += text + "\n"
            # 轻微超阈值（旧行为，保留）：优先在段落/行边界切分，允许略超 chunk_size
            if self.chunk_size <= len(current_chunk) < hard_limit:
                # 优先在段落/行边界切分（2026-08-07）：避免把段落、标题从中间截断，
                # 导致全文重建时出现伪段落边界与断裂标题
                cut = len(current_chunk)
                last_para = current_chunk.rfind("\n\n")
                last_line = current_chunk.rfind("\n")
                if last_para >= min_cut:
                    cut = last_para
                elif last_line >= min_cut:
                    cut = last_line
                head, rest = current_chunk[:cut], current_chunk[cut:]
                chunks.append({
                    "text": head.strip(),
                    "page_number": current_page,
                    "file_name": Path(source_path).name,
                    "chunk_index": len(chunks),
                })
                overlap = min(self.chunk_overlap, len(head))
                current_chunk = (head[-overlap:] if overlap > 0 else "") + rest
            # 硬上限循环切块：任何超长文本都切成 chunk_size 量级，绝不整页成块
            while len(current_chunk) >= hard_limit:
                cut = self.chunk_size
                last_para = current_chunk.rfind(
                    "\n\n", min_cut, self.chunk_size + self.chunk_overlap)
                last_line = current_chunk.rfind(
                    "\n", min_cut, self.chunk_size + self.chunk_overlap)
                if last_para > 0:
                    cut = last_para + 2
                elif last_line > 0:
                    cut = last_line + 1
                head, rest = current_chunk[:cut], current_chunk[cut:]
                chunks.append({
                    "text": head.strip(),
                    "page_number": current_page,
                    "file_name": Path(source_path).name,
                    "chunk_index": len(chunks),
                })
                overlap = min(self.chunk_overlap, len(head))
                current_chunk = (head[-overlap:] if overlap > 0 else "") + rest
            current_page = page.get("page_num", current_page)
        if current_chunk.strip():
            chunks.append({
                "text": current_chunk.strip(),
                "page_number": current_page,
                "file_name": Path(source_path).name,
                "chunk_index": len(chunks),
            })
        return chunks

    def _infer_domain(self, file_path: str) -> str:
        name = Path(file_path).name.lower()
        if any(k in name for k in ["机器", "学习", "深度", "神经", "transformer", "gpt", "llm", "ai"]):
            return "ai_knowledge"
        return "default"
