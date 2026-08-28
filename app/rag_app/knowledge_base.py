"""
AI知识库 - 知识库管理
基于离线 TF-IDF+SVD + BM25 + pickle向量
"""
import os, re, pickle, numpy as np, jieba, time, logging
from typing import Optional, List, Dict, Any
from app.rag_app.config import Config
from app.rag_app.parser import DocumentParser, join_chunk_texts
from app.rag_app.toc_extractor import extract_toc
from app.rag_app.graph_selector import normalize_focus_value, parse_uploaded_at_timestamp, select_graph_source_chunks

jieba.setLogLevel(20)
logger = logging.getLogger("ai_kb.knowledge_base")
_BM25_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+")

# 正文缓存版本号（2026-08-14：epub 分块修复，3 = 保留段落换行+硬上限切块）：
# 全文重建/预处理规则每次升级时 +1，
# 旧版本缓存在 _load_content_cache 中作废重建，避免存量文档永远带着旧缺陷
_CONTENT_CACHE_VERSION = 3


def _jieba_tokenizer(text: str) -> List[str]:
    return list(jieba.cut(text))


def _bm25_tokenizer(text: str) -> List[str]:
    """BM25 使用轻量分词，避免首次加载历史索引时逐段初始化 jieba。"""
    return _BM25_TOKEN_PATTERN.findall(text)


def _split_sentences(text: str) -> list[str]:
    """中文分句：按句末标点/换行切分并保留标点，剔除空句。
    用途：句子级检索证据（2026-08-13，对标 sentence-window retrieval）。"""
    if not text:
        return []
    sentences: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        for part in re.split(r"(?<=[。！？；…])", para):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def _compute_text_stats(text: str) -> dict:
    """从全文计算统计：总字数（去空白）/中文字符/词数/句数/段数。"""
    if not text:
        return {"total_chars": 0, "chinese_chars": 0, "words": 0, "sentences": 0, "paragraphs": 0}
    body = re.sub(r"\s+", "", text)
    total_chars = len(body)
    chinese_chars = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff")
    words = sum(
        1 for t in jieba.cut(body)
        if t.strip() and any(("\u4e00" <= c <= "\u9fff") or c.isalnum() for c in t)
    )
    paragraphs = sum(1 for p in re.split(r"\n\s*\n", text) if p.strip())
    sentences = len(_split_sentences(text))
    return {
        "total_chars": total_chars,
        "chinese_chars": chinese_chars,
        "words": words,
        "sentences": sentences,
        "paragraphs": paragraphs,
    }


_QUERY_STOPWORDS = {
    "为什么", "什么", "怎么", "如何", "关于", "这个", "那个", "一个",
    "可以", "没有", "不是", "就是", "然后", "后面", "突然", "的事",
    "原因", "背景", "内容", "相关", "问题", "告诉", "提到", "说明",
    "以及", "还是", "但是", "因为", "所以", "如果", "那么", "这样", "那样",
}


def _query_terms(query: str) -> list[str]:
    """查询词项：jieba 分词后保留长度>=2 的中英文词，剔除提问衬词（用于句子命中打分）。"""
    if not query:
        return []
    terms = []
    for t in jieba.cut(query):
        t = t.strip()
        if (len(t) >= 2 and t not in _QUERY_STOPWORDS
                and all(("\u4e00" <= c <= "\u9fff") or c.isalnum() for c in t)):
            terms.append(t)
    return terms


def _query_phrase_terms(query: str, max_len: int = 4) -> list[str]:
    """查询中的实体性词条（2026-08-14）：jieba 2~4 字词（剔停用词/纯数字）；
    jieba 无结果时用 2 字 bigram 兜底（专名如"西琴"未登录词场景）。
    用于 RRF 融合的"专名精确命中加权"，解决两字专名被单字 BM25/向量稀释、
    精确段落排不上前的检索失灵（经验：地球编年史"西琴"查询）。
    """
    if not query:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in jieba.cut(query):
        t = t.strip()
        if (2 <= len(t) <= max_len and t not in _QUERY_STOPWORDS and not t.isdigit()
                and all(("\u4e00" <= c <= "\u9fff") or c.isalnum() for c in t)):
            if t not in seen:
                seen.add(t)
                out.append(t)
    if not out:
        chars = [c for c in query if "\u4e00" <= c <= "\u9fff" or c.isalnum()]
        for i in range(len(chars) - 1):
            g = "".join(chars[i:i + 2])
            if g not in seen and not any(st in g for st in _QUERY_STOPWORDS):
                seen.add(g)
                out.append(g)
    return out[:8]


def _apply_phrase_bonus(candidates, query: str) -> None:
    """专名/术语精确命中加权：候选文本连续出现查询实体词条 → 加 RRF 分。
    仅对命中者加分（未命中不加），幅度 0.02~0.06，足以把精确段落提到弱证据阈值之上。
    """
    grams = _query_phrase_terms(query)
    if not grams:
        return
    for cand in candidates:
        text = cand.get("text", "") or ""
        hits = sum(1 for g in grams if g in text)
        if hits:
            cand["rrf_score"] = float(cand.get("rrf_score", 0.0)) + min(0.06, 0.02 * hits)


def _extract_quoted_phrase(query: str) -> str:
    """提取引号内的短语（中文引号/英文引号），用于精确子串定位。"""
    m = re.search(r"[\"“「『]([^\"”」』]{2,})[\"”」』]", query)
    return m.group(1).strip() if m else ""


def _file_format(name: str) -> str:
    """从文件名提取格式后缀（.pdf/.epub/.txt/.md/.docx…），无后缀返回 unknown。"""
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    return ext or "unknown"


class OfflineEmbedder:
    def __init__(self, dim=384, vectorizer_path=None):
        self.dim = dim
        self.vectorizer_path = vectorizer_path
        self._fitted = False
        self.vectorizer = None
        self.svd = None
        self._init_vectorizer()

    def _init_vectorizer(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        if self.vectorizer_path and os.path.exists(self.vectorizer_path):
            try:
                with open(self.vectorizer_path, "rb") as f:
                    data = pickle.load(f)
                self.vectorizer = data["vectorizer"]
                self.svd = data["svd"]
                self._fitted = True
                logger.info("OfflineEmbedder: Loaded from disk")
                return
            except Exception as e:
                logger.warning("OfflineEmbedder: Load failed: %s", e)
        self.vectorizer = TfidfVectorizer(max_features=5000, ngram_range=(1, 2),
                                          tokenizer=_jieba_tokenizer, token_pattern=None)
        self.svd = TruncatedSVD(n_components=self.dim, random_state=42)

    def fit(self, documents):
        if self._fitted or not documents:
            return
        logger.info("OfflineEmbedder: Fitting on %d docs...", len(documents))
        tfidf = self.vectorizer.fit_transform(documents)
        # 小知识库的特征数通常远小于默认维度，SVD 维度必须随训练数据收缩。
        from sklearn.decomposition import TruncatedSVD
        self.svd = TruncatedSVD(
            n_components=max(1, min(self.dim, tfidf.shape[1])),
            random_state=42,
        )
        self._emb = self.svd.fit_transform(tfidf)
        self._fitted = True
        if self.vectorizer_path:
            try:
                with open(self.vectorizer_path, "wb") as f:
                    pickle.dump({"vectorizer": self.vectorizer, "svd": self.svd}, f)
                logger.info("OfflineEmbedder: Saved to %s", self.vectorizer_path)
            except Exception as e:
                logger.warning("OfflineEmbedder: Save failed: %s", e)

    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        if not self._fitted:
            self.fit(texts)
        tfidf = self.vectorizer.transform(texts)
        return self.svd.transform(tfidf)
def _match_file(fn: str, filters: set | None) -> bool:
    """文件过滤——支持子串匹配（处理撇号等特殊字符导致URL编码截断）"""
    if not filters:
        return True
    return fn in filters or any(f in fn for f in filters if len(f) >= 10)


class KnowledgeBase:
    """知识库管理类"""

    def __init__(self, config=None):
        self.config = config or Config()
        vectorizer_path = os.path.join(self.config.VECTOR_DATA_DIR, "tfidf_svd.pkl")
        self.embed_model = OfflineEmbedder(dim=384, vectorizer_path=vectorizer_path)
        logger.info("Using offline embedder, no model download needed")
        os.makedirs(self.config.VECTOR_DATA_DIR, exist_ok=True)
        self.parser = DocumentParser(self.config)
        self.bm25 = None
        self.bm25_docs = []
        self.bm25_ids = []
        self.bm25_metadatas = []
        self._init_bm25()
        self._embeddings = None
        self._embed_texts = None
        self._embed_ids = None
        self._embed_metadatas = None
        self._init_embeddings()
        self._file_text_cache: dict[str, str] = {}  # physical_name → full_text
        self._file_chunk_map: dict[str, list[int]] = {}  # physical_name → chunk_indices
        self._file_headings: dict[str, list[dict]] = {}  # physical_name → headings (预计算)
        self._file_preprocessed: dict[str, str] = {}  # physical_name → preprocessed text
        self._file_html: dict[str, str] = {}  # physical_name → rendered HTML
        self._stats_cache: dict[str, dict] = {}  # physical_name → 文档统计（2026-08-13）
        # LLM 产出缓存（省 tokens / 省时间）：physical_name → {mindmap, graph, tree, summary}
        self._llm_cache: dict[str, dict[str, any]] = {}
        self._load_content_cache()

    def _load_content_cache(self):
        """加载持久化的正文缓存（避免服务重启后重新解析）"""
        cache_path = os.path.join(self.config.VECTOR_DATA_DIR, "content_cache.pkl")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    cache = pickle.load(f)
                # 缓存版本护栏（2026-08-07）：全文重建改为 overlap 去重、预处理规则升级后，
                # 旧缓存的 texts/preprocessed/headings/html 均带旧缺陷 → 作废按需重建；
                # LLM 缓存（mindmap/graph/summary 等，花费 tokens）保留
                if int(cache.get("version", 0)) < _CONTENT_CACHE_VERSION:
                    self._llm_cache = cache.get("llm", {})
                    logger.info("Content cache version outdated — texts/preprocessed/headings will be rebuilt on demand (llm cache kept: %d items)", len(self._llm_cache))
                    return
                self._file_text_cache = cache.get("texts", {})
                self._file_preprocessed = cache.get("preprocessed", {})
                self._file_headings = cache.get("headings", {})
                self._file_html = cache.get("html", {})
                self._llm_cache = cache.get("llm", {})
                # 统计缓存与文本版本无关（2026-08-14）：分块/预处理升级不影响字数统计
                self._stats_cache = cache.get("stats", {})
                logger.info("Loaded content cache: %d files preprocessed, %d llm items", len(self._file_preprocessed), len(self._llm_cache))
            except Exception as e:
                logger.warning("Failed to load content cache: %s", e)

    def _save_content_cache(self):
        """持久化正文缓存（避免服务重启后重新解析）"""
        cache_path = os.path.join(self.config.VECTOR_DATA_DIR, "content_cache.pkl")
        try:
            cache = {
                "version": _CONTENT_CACHE_VERSION,
                "texts": self._file_text_cache,
                "preprocessed": self._file_preprocessed,
                "headings": self._file_headings,
                "html": self._file_html,
                "llm": self._llm_cache,
                "stats": self._stats_cache,
            }
            with open(cache_path, "wb") as f:
                pickle.dump(cache, f)
        except Exception as e:
            logger.warning("Failed to save content cache: %s", e)

    # ── LLM 产出缓存 API（省 tokens）──
    def get_llm_cache(self, file_id: str, key: str) -> any:
        """读取 LLM 缓存（如 mindmap/graph/tree/summary）"""
        return self._llm_cache.get(file_id, {}).get(key)

    def set_llm_cache(self, file_id: str, key: str, value: any):
        """写入 LLM 缓存并持久化"""
        self._llm_cache.setdefault(file_id, {})[key] = value
        self._save_content_cache()

    def clear_llm_cache(self, file_id: str):
        """文件重导入或删除时清除其 LLM 缓存"""
        self._llm_cache.pop(file_id, None)
        self._save_content_cache()

    def _init_bm25(self):
        try:
            from rank_bm25 import BM25Okapi
            self.BM25Okapi = BM25Okapi
            bm25_path = os.path.join(self.config.VECTOR_DATA_DIR, "bm25_data.pkl")
            if os.path.exists(bm25_path):
                with open(bm25_path, "rb") as f:
                    data = pickle.load(f)
                self.bm25_docs = data.get("docs", [])
                self.bm25_ids = data.get("ids", [])
                self.bm25_metadatas = data.get("metadatas", [])
            if self.bm25_docs:
                tokenized_docs = [_bm25_tokenizer(doc) for doc in self.bm25_docs]
                self.bm25 = self.BM25Okapi(tokenized_docs)
                logger.info("BM25: Initialized with %d docs", len(self.bm25_docs))
            else:
                logger.info("BM25: No data available, not initialized")
        except Exception as e:
            logger.error("BM25: Init failed: %s", e)

    def _init_embeddings(self):
        embed_path = os.path.join(self.config.VECTOR_DATA_DIR, "embeddings.pkl")
        if os.path.exists(embed_path):
            try:
                with open(embed_path, "rb") as f:
                    ed = pickle.load(f)
                self._embeddings = ed["embeddings"]
                self._embed_texts = ed["texts"]
                self._embed_ids = ed["ids"]
                self._embed_metadatas = ed["metadatas"]
                logger.info("Loaded embeddings: %s", self._embeddings.shape)
                # 维度一致性护栏：SVD 输出维度必须与已存嵌入一致，否则后续 vstack 崩溃
                svd_dim = self.embed_model.svd.components_.shape[0] if (self.embed_model.svd is not None and hasattr(self.embed_model.svd, "components_")) else None
                emb_dim = self._embeddings.shape[1] if self._embeddings is not None else None
                if svd_dim is not None and emb_dim is not None and svd_dim != emb_dim:
                    logger.warning(
                        "Embedding dimension mismatch: svd=%s emb=%s. 运行 scripts/fix_tfidf_svd.py 重建对齐",
                        svd_dim, emb_dim,
                    )
            except Exception as e:
                logger.warning("Failed to load embeddings: %s", e)

    def index_file_with_metadata(self, file_path: str, file_name: str, uploaded_at: str,
                                  file_size: int, domain: str, physical_name: str,
                                  file_mtime: float = 0.0,
                                  source_root: str = "", rel_path: str = "", rel_dir: str = "") -> int:
        try:
            chunks = self.parser.parse_file(file_path)
            if not chunks:
                logger.warning("No chunks parsed from %s", file_name)
                return 0
            texts = [chunk['text'] for chunk in chunks]

            # 刷新或重复导入同一物理文件时，先移除旧版本，避免新旧 chunks 共存。
            if any(meta.get("physical_name") == physical_name for meta in self._embed_metadatas or []):
                self.remove_file(physical_name)

            # ── 标题提取：优先用 EPUB 原生 TOC（精准），否则从文本提取 ──
            try:
                native_toc = chunks[0].get("_native_toc") if chunks else None
                if native_toc:
                    self._file_headings[physical_name] = native_toc
                    logger.info("EPUB native TOC: %d headings from %s", len(native_toc), file_name)
                else:
                    # 用 overlap 去重拼接（2026-08-07）：旧 "\n\n".join 会把分块重叠段重复计入且拦腰截断段落
                    full_text = join_chunk_texts(texts)
                    headings = extract_toc(full_text)
                    self._file_headings[physical_name] = headings
                    logger.info("Extracted %d headings from %s", len(headings), file_name)
            except Exception as e:
                logger.warning("Heading extraction failed for %s: %s", file_name, e)

            metadatas = []
            ids = []
            for i, chunk in enumerate(chunks):
                meta = {
                    "file_name": file_name,
                    "physical_name": physical_name,
                    "file_path": file_path,
                    "domain": domain,
                    "uploaded_at": uploaded_at,
                    "file_size": file_size,
                    "file_mtime": file_mtime,
                    "chunk_index": i,
                    "page_number": chunk.get("page_number", 0),
                    # 路径身份三件套（2026-08-20 结构化 v2）：
                    # source_root=扫描根规范化绝对路径, rel_path=相对根POSIX路径(唯一身份),
                    # rel_dir=目录层级；旧数据无此字段时 diff 按 rel_path/文件名 fallback 兼容
                    "source_root": source_root,
                    "rel_path": rel_path or physical_name,
                    "rel_dir": rel_dir,
                }
                metadatas.append(meta)
                ids.append(f"{physical_name}_{i}")
            embeddings = self.embed_model.encode(texts)
            if self._embeddings is not None:
                self._embeddings = np.vstack([self._embeddings, embeddings])
                self._embed_texts.extend(texts)
                self._embed_ids.extend(ids)
                self._embed_metadatas.extend(metadatas)
            else:
                self._embeddings = embeddings
                self._embed_texts = texts.copy()
                self._embed_ids = ids.copy()
                self._embed_metadatas = metadatas.copy()
            tokenized_docs = [_bm25_tokenizer(doc) for doc in texts]
            if self.bm25 is None:
                self.bm25 = self.BM25Okapi(tokenized_docs)
                self.bm25_docs = texts.copy()
                self.bm25_ids = ids.copy()
                self.bm25_metadatas = metadatas.copy()
            else:
                self.bm25_docs.extend(texts)
                self.bm25_ids.extend(ids)
                self.bm25_metadatas.extend(metadatas)
                all_tokenized = [_bm25_tokenizer(doc) for doc in self.bm25_docs]
                self.bm25 = self.BM25Okapi(all_tokenized)
            self._save_embeddings()
            self._save_bm25()
            logger.info("Indexed %d chunks from %s", len(chunks), file_name)
            return len(chunks)
        except Exception as e:
            logger.error("Index failed: %s", e)
            raise e

    def _save_embeddings(self):
        try:
            embed_path = os.path.join(self.config.VECTOR_DATA_DIR, "embeddings.pkl")
            with open(embed_path, "wb") as f:
                pickle.dump({
                    "embeddings": self._embeddings,
                    "texts": self._embed_texts,
                    "ids": self._embed_ids,
                    "metadatas": self._embed_metadatas
                }, f)
        except Exception as e:
            logger.warning("Failed to save embeddings: %s", e)

    def _save_bm25(self):
        try:
            bm25_path = os.path.join(self.config.VECTOR_DATA_DIR, "bm25_data.pkl")
            with open(bm25_path, "wb") as f:
                pickle.dump({
                    "docs": self.bm25_docs,
                    "ids": self.bm25_ids,
                    "metadatas": self.bm25_metadatas
                }, f)
        except Exception as e:
            logger.warning("Failed to save BM25 data: %s", e)

    def remove_file(self, file_id: str) -> Dict[str, Any]:
        """按 physical_name 删除文件的所有 chunks，从 BM25 和 embeddings 中移除"""
        if not self._embed_metadatas:
            return {"removed": False, "reason": "知识库为空"}

        # 安全检查：防止空 ID 导致误删所有文件
        if not file_id or not file_id.strip():
            return {"removed": False, "reason": "文件 ID 不能为空"}

        keep_indices = []
        removed_chunks = 0
        for i, meta in enumerate(self._embed_metadatas):
            if meta.get("physical_name") == file_id or meta.get("file_name") == file_id:
                removed_chunks += 1
            else:
                keep_indices.append(i)

        if removed_chunks == 0:
            return {"removed": False, "reason": f"未找到文件: {file_id}"}

        if self._embeddings is not None and keep_indices:
            self._embeddings = self._embeddings[keep_indices]
        elif not keep_indices:
            self._embeddings = None

        self._embed_texts = [self._embed_texts[i] for i in keep_indices] if keep_indices else []
        self._embed_ids = [self._embed_ids[i] for i in keep_indices] if keep_indices else []
        self._embed_metadatas = [self._embed_metadatas[i] for i in keep_indices] if keep_indices else []

        bm25_keep = []
        for i, meta in enumerate(self.bm25_metadatas):
            if meta.get("physical_name") != file_id and meta.get("file_name") != file_id:
                bm25_keep.append(i)

        self.bm25_docs = [self.bm25_docs[i] for i in bm25_keep]
        self.bm25_ids = [self.bm25_ids[i] for i in bm25_keep]
        self.bm25_metadatas = [self.bm25_metadatas[i] for i in bm25_keep]

        # 清除 chunk 缓存
        self._file_chunk_map = {}

        if self.bm25_docs:
            from rank_bm25 import BM25Okapi
            tokenized = [_bm25_tokenizer(doc) for doc in self.bm25_docs]
            self.bm25 = BM25Okapi(tokenized)
        else:
            self.bm25 = None
        self._save_embeddings()
        self._save_bm25()

        # 清除该文件的预处理缓存 + LLM 缓存
        getattr(self, "_file_text_cache", {}).pop(file_id, None)
        getattr(self, "_file_preprocessed", {}).pop(file_id, None)
        getattr(self, "_file_html", {}).pop(file_id, None)
        getattr(self, "_file_headings", {}).pop(file_id, None)
        getattr(self, "_llm_cache", {}).pop(file_id, None)
        getattr(self, "_stats_cache", {}).pop(file_id, None)
        self._save_content_cache()  # 同步磁盘缓存
        logger.info("Removed %d chunks for file: %s", removed_chunks, file_id)
        return {"removed": True, "file_id": file_id, "chunks_removed": removed_chunks}

    def list_files(self) -> List[Dict[str, Any]]:
        if not hasattr(self, '_embed_metadatas') or not self._embed_metadatas:
            return []
        try:
            files_dict = {}
            texts = self._embed_texts if len(getattr(self, '_embed_texts', []) or []) == len(self._embed_metadatas) else []
            for idx, meta in enumerate(self._embed_metadatas):
                file_name = meta.get('file_name', 'unknown')
                physical_name = meta.get('physical_name', file_name)
                if physical_name not in files_dict:
                    ext = os.path.splitext(physical_name)[1].lower() if '.' in physical_name else ''
                    files_dict[physical_name] = {
                        'id': physical_name, 'name': file_name,
                        'file_path': meta.get('file_path', ''),
                        'file_type': ext,
                        'domain': meta.get('domain', 'unknown'),
                        'uploaded_at': meta.get('uploaded_at', ''),
                        'file_size': meta.get('file_size', 0),
                        'file_mtime': meta.get('file_mtime', 0),
                        'chunks': 0, 'physical_name': physical_name,
                        # 字符数：按全部分块文本累计（修复 2026-08-19：旧实现取首块
                        # 缓存长度，《2049》显示 709 实际 94,327，误导"这本书没内容"）
                        'char_count': 0,
                    }
                files_dict[physical_name]['chunks'] += 1
                if texts:
                    # 主路径：累加该文件所有分块的文本长度（真实全文量）
                    files_dict[physical_name]['char_count'] += len(texts[idx] or '')
                elif files_dict[physical_name]['char_count'] == 0:
                    # 无嵌入文本兜底：退回正文缓存长度（近似）
                    cached = (
                        getattr(self, '_file_preprocessed', {}).get(physical_name)
                        or getattr(self, '_file_text_cache', {}).get(physical_name)
                        or ''
                    )
                    files_dict[physical_name]['char_count'] = len(cached)
            return list(files_dict.values())
        except Exception:
            return []

    def rebuild_full_text(self, physical_name: str) -> str:
        """从 chunks 重建全文（2026-08-07）：overlap 去重拼接。

        旧实现 "\n\n".join(chunk texts) 把分块重叠段（chunk_overlap）重复计入，
        且在每个分块边界制造伪段落边界、拦腰截断段落与标题——存量文档正文
        格式错乱的根因。此方法供 /text 接口、对比接口等所有全文重建场景统一调用。
        """
        chunks = self.get_chunks_by_file(physical_name, max_chunks=999999)
        texts = [c.get("text", "") for c in chunks if c.get("text")]
        return join_chunk_texts(texts)

    def get_chunks_by_file(self, physical_name: str, max_chunks: int = 5) -> List[Dict[str, Any]]:
        """按physical_name获取该文件的chunks。"""
        if not self._embed_texts or not self._embed_metadatas:
            return []
        
        # 延迟构建 chunk 索引（首次 ~50ms，后续 0）
        if not self._file_chunk_map:
            for i, meta in enumerate(self._embed_metadatas):
                pn = meta.get('physical_name', '')
                if pn not in self._file_chunk_map:
                    self._file_chunk_map[pn] = []
                self._file_chunk_map[pn].append(i)
        
        indices = self._file_chunk_map.get(physical_name, [])
        result = []
        for idx in indices:
            result.append({'text': self._embed_texts[idx], 'metadata': self._embed_metadatas[idx]})
            if len(result) >= max_chunks:
                break
        return result

    def get_file_metadata(self, physical_name: str) -> Dict[str, Any]:
        """获取文件元数据。"""
        if not self._embed_metadatas:
            return {}
        for meta in self._embed_metadatas:
            if meta.get('physical_name') == physical_name:
                return {
                    'file_name': meta.get('file_name', physical_name),
                    'physical_name': physical_name,
                    'file_path': meta.get('file_path', ''),
                    'domain': meta.get('domain', 'unknown'),
                    'uploaded_at': meta.get('uploaded_at', ''),
                    'file_size': meta.get('file_size', 0),
                }
        return {'file_name': physical_name, 'physical_name': physical_name}

    # ── 文档统计（2026-08-13：多少字/页/章等聚合问题直答，不依赖 LLM 猜数）──

    def _file_page_count(self, physical_name: str) -> int:
        pages = 0
        if not self._embed_metadatas:
            return 0
        for m in self._embed_metadatas:
            if m.get("physical_name") == physical_name:
                try:
                    pages = max(pages, int(m.get("page_number", 0) or 0))
                except Exception:
                    pass
        return pages

    def get_document_stats(self, physical_name: str) -> Dict[str, Any]:
        """按需计算单篇文档统计（优先全文缓存，回退拼接 chunk）。"""
        if physical_name in self._stats_cache:
            return self._stats_cache[physical_name]
        text = self._file_text_cache.get(physical_name, "")
        if not text:
            chunks = self.get_chunks_by_file(physical_name, max_chunks=100_000)
            text = join_chunk_texts([c.get("text", "") for c in chunks])
        stats = _compute_text_stats(text)
        meta = self.get_file_metadata(physical_name)
        stats["file_name"] = meta.get("file_name") or physical_name
        stats["physical_name"] = physical_name
        stats["pages"] = self._file_page_count(physical_name)
        headings = self._file_headings.get(physical_name)
        stats["chapters"] = len(headings) if headings else None
        self._stats_cache[physical_name] = stats
        return stats

    def get_library_stats(self, file_ids: Optional[list[str]] = None) -> Dict[str, Any]:
        """多篇/全库统计聚合。file_ids 传 physical_name 子串即可过滤。"""
        names = [f.get("physical_name") for f in self.list_files() if f.get("physical_name")]
        if file_ids:
            ids = [fid for fid in file_ids if fid]
            names = [n for n in names if any(fid in n for fid in ids)]
        if not names:
            return {"files": [], "totals": _compute_text_stats(""), "file_count": 0, "formats": {}}
        per_file = []
        totals = _compute_text_stats("")
        formats: dict[str, int] = {}
        for n in names:
            s = self.get_document_stats(n)
            fmt = _file_format(n)
            s["format"] = fmt
            formats[fmt] = formats.get(fmt, 0) + 1
            per_file.append(s)
            for k in ("total_chars", "chinese_chars", "words", "sentences", "paragraphs"):
                totals[k] = totals.get(k, 0) + int(s.get(k, 0) or 0)
            totals["pages"] = totals.get("pages", 0) + int(s.get("pages", 0) or 0)
        totals["chapters"] = None
        return {"files": per_file, "totals": totals, "file_count": len(per_file), "formats": formats}

    # ── 句子级证据（2026-08-13：找句子/推出相关句子/关键词定位）──

    def extract_sentence_evidence(self, results: list[dict], query: str,
                                  max_sentences: int = 8) -> list[dict]:
        """从检索 top chunks 中切句，按查询词命中数 + 句内位置排序，取关键句证据。"""
        terms = _query_terms(query)
        if not terms or not results:
            return []
        scored: list[dict] = []
        for r in results:
            meta = r.get("metadata", {})
            for si, sent in enumerate(_split_sentences(r.get("text", ""))):
                hit = sum(1 for t in terms if t in sent)
                if hit == 0:
                    continue
                scored.append({
                    "text": sent,
                    "hit_count": hit,
                    "pos": si,
                    "file_name": meta.get("file_name") or meta.get("physical_name") or "未知来源",
                    "physical_name": meta.get("physical_name", ""),
                    "page_number": meta.get("page_number", 0),
                    "chunk_index": meta.get("chunk_index", 0),
                })
        scored.sort(key=lambda x: (-x["hit_count"], x["pos"]))
        # ⛔ 2026-08-13：每 chunk 最多 2 句（与 find_exact_sentences 一致，防同片段刷屏）
        per_chunk: dict[str, int] = {}
        deduped: list[dict] = []
        for e in scored:
            key = f"{e.get('physical_name')}_{e.get('chunk_index')}"
            if per_chunk.get(key, 0) >= 2:
                continue
            per_chunk[key] = per_chunk.get(key, 0) + 1
            deduped.append(e)
            if len(deduped) >= max_sentences:
                break
        return deduped

    def find_exact_sentences(self, query: str, top_k: int = 8,
                             file_ids: Optional[list[str]] = None) -> list[dict]:
        """全文精确扫描：引号短语做子串定位，否则按查询词项命中定位原句。
        返回句子 + 《书名》第X页第X段，供"搜关键词/找句子"直接引用。"""
        phrase = _extract_quoted_phrase(query)
        terms = [] if phrase else _query_terms(query)
        if not phrase and not terms:
            return []
        filters = {fid for fid in (file_ids or []) if fid}
        scored: list[dict] = []
        seen: set[str] = set()
        # 人名共现加权：2~4 字中文词视作候选人名，窗口内 ≥2 个不同人名 → 强相关（对话/互动场景）
        names = [
            t for t in terms
            if 2 <= len(t) <= 4 and all("\u4e00" <= c <= "\u9fff" for c in t)
        ]
        if not self._embed_texts:
            return []
        for i, text in enumerate(self._embed_texts):
            meta = self._embed_metadatas[i] if i < len(self._embed_metadatas) else {}
            if filters and not _match_file(meta.get("physical_name", ""), filters):
                continue
            all_sents = _split_sentences(text)
            if not all_sents:
                continue
            if phrase:
                if phrase not in text:
                    continue
                candidates = [(si, s) for si, s in enumerate(all_sents) if phrase in s] or [(0, text[:200])]
            else:
                candidates = [(si, s) for si, s in enumerate(all_sents) if any(t in s for t in terms)]
            for si, sent in candidates:
                key = sent[:80]
                if key in seen:
                    continue
                seen.add(key)
                # 上下文窗口（sentence-window retrieval）：前后各 1 句，帮助 LLM 理解因果
                window = "".join(
                    all_sents[max(0, si - 1): si + 2]
                )
                name_bonus = (
                    3
                    if len(names) >= 2
                    and sum(1 for n in set(names) if n in window) >= 2
                    else 0
                )
                scored.append({
                    "text": sent,
                    "hit_count": sum(1 for t in (terms or [phrase]) if t in sent) + name_bonus,
                    "pos": len(scored),
                    "window": window,
                    "file_name": meta.get("file_name") or meta.get("physical_name") or "未知来源",
                    "physical_name": meta.get("physical_name", ""),
                    "page_number": meta.get("page_number", 0),
                    "chunk_index": meta.get("chunk_index", 0),
                })
        scored.sort(key=lambda x: (-x["hit_count"], x["pos"]))
        # ⛔ 2026-08-13：每个 chunk 最多取 2 句，防止单一 chunk 刷屏挤掉关键对话句
        per_chunk: dict[str, int] = {}
        selected: list[dict] = []
        for e in scored:
            key = f"{e.get('physical_name')}_{e.get('chunk_index')}"
            if per_chunk.get(key, 0) >= 2:
                continue
            per_chunk[key] = per_chunk.get(key, 0) + 1
            selected.append(e)
            if len(selected) >= top_k:
                break
        return selected

    def _collect_graph_source_chunks(self, domain: str | None = None, single_file: str | None = None) -> List[Dict[str, Any]]:
        if not self._embed_texts or not self._embed_ids or not self._embed_metadatas:
            return []

        # ── 单一文件模式：忽略 domain 过滤（因为文件可能在任意 domain）──
        normalized_domain = None if (not domain or domain == "all" or single_file) else domain
        chunks: List[Dict[str, Any]] = []
        for text, chunk_id, metadata in zip(self._embed_texts, self._embed_ids, self._embed_metadatas):
            if normalized_domain and metadata.get("domain") != normalized_domain:
                continue
            if single_file and metadata.get("physical_name") != single_file:
                continue
            chunks.append({
                "text": text,
                "source_file": metadata.get("file_name") or metadata.get("physical_name") or "unknown",
                "source_chunk_id": chunk_id,
                "domain": metadata.get("domain") or "all",
                "uploaded_at": metadata.get("uploaded_at") or "",
                "chunk_index": int(metadata.get("chunk_index", 0) or 0),
            })

        return chunks

    def get_graph_source_chunk_view(
        self,
        domain: str | None = None,
        *,
        max_chunks: int = 48,
        selection_profile: str = "balanced",
        sorting_strategy: str = "relevance",
        focus_concept: str | None = None,
        single_file: str | None = None,
        spread_single_file: bool = False,
    ) -> Dict[str, Any]:
        all_chunks = self._collect_graph_source_chunks(domain=domain, single_file=single_file)
        # 兼容旧版前端固定发送的 ai_knowledge；若该域不存在，则使用当前全库。
        if not all_chunks and domain == "ai_knowledge" and not single_file:
            all_chunks = self._collect_graph_source_chunks(domain=None)
        selected_chunks = select_graph_source_chunks(
            all_chunks,
            max_chunks=max_chunks,
            selection_profile=selection_profile,
            sorting_strategy=sorting_strategy,
            focus_concept=focus_concept,
            spread_single_file=spread_single_file,
        )
        normalized_focus = normalize_focus_value(focus_concept)
        focus_matched_chunk_count = 0
        if normalized_focus:
            focus_matched_chunk_count = sum(
                1 for chunk in selected_chunks if int(chunk.get("focus_score", 0)) > 0
            )
        return {
            "chunks": selected_chunks,
            "available_chunk_count": len(all_chunks),
            "selected_chunk_count": len(selected_chunks),
            "selection_profile": (selection_profile or "balanced").strip().lower(),
            "sorting_strategy": (sorting_strategy or "relevance").strip().lower(),
            "source_file_count": len({chunk["source_file"] for chunk in all_chunks}),
            "focus_concept": focus_concept or "",
            "focus_matched_chunk_count": focus_matched_chunk_count,
            "focus_fallback_used": bool(normalized_focus and focus_matched_chunk_count == 0),
        }

    def get_graph_source_chunks(
        self,
        domain: str | None = None,
        max_chunks: int = 48,
        selection_profile: str = "balanced",
        sorting_strategy: str = "relevance",
        focus_concept: str | None = None,
    ) -> List[Dict[str, Any]]:
        """为图谱相关接口暴露当前已索引的轻量 chunk 视图。"""
        view = self.get_graph_source_chunk_view(
            domain=domain,
            max_chunks=max_chunks,
            selection_profile=selection_profile,
            sorting_strategy=sorting_strategy,
            focus_concept=focus_concept,
        )
        return view["chunks"]

    def get_analysis_scope(
        self,
        *,
        file_ids: Optional[List[str]] = None,
        domain: str | None = None,
        max_files: int = 3,
        max_chunks_per_file: int = 3,
    ) -> Dict[str, Any]:
        if not self._embed_texts or not self._embed_ids or not self._embed_metadatas:
            return {
                "chunks": [],
                "selected_files": [],
                "available_file_count": 0,
                "selected_file_count": 0,
                "scope_mode": "empty",
            }

        normalized_domain = None if not domain or domain == "all" else domain
        requested_ids = [item for item in (file_ids or []) if item]

        grouped: Dict[str, Dict[str, Any]] = {}
        for text, chunk_id, metadata in zip(self._embed_texts, self._embed_ids, self._embed_metadatas):
            physical_name = metadata.get("physical_name") or metadata.get("file_name") or "unknown"
            if normalized_domain and metadata.get("domain") != normalized_domain:
                continue
            if requested_ids and physical_name not in requested_ids:
                continue

            entry = grouped.setdefault(
                physical_name,
                {
                    "id": physical_name,
                    "name": metadata.get("file_name") or physical_name,
                    "domain": metadata.get("domain") or "all",
                    "uploaded_at": metadata.get("uploaded_at") or "",
                    "uploaded_at_ts": parse_uploaded_at_timestamp(str(metadata.get("uploaded_at") or "")),
                    "chunks": [],
                },
            )
            entry["chunks"].append(
                {
                    "text": text,
                    "source_file": metadata.get("file_name") or physical_name,
                    "source_chunk_id": chunk_id,
                    "domain": metadata.get("domain") or "all",
                    "uploaded_at": metadata.get("uploaded_at") or "",
                    "chunk_index": int(metadata.get("chunk_index", 0) or 0),
                    "page_number": metadata.get("page_number", 0),
                }
            )

        if not grouped:
            return {
                "chunks": [],
                "selected_files": [],
                "available_file_count": 0,
                "selected_file_count": 0,
                "scope_mode": "file_ids" if requested_ids else ("domain" if normalized_domain else "recent"),
            }

        ordered_files = list(grouped.values())
        if requested_ids:
            order_map = {file_id: index for index, file_id in enumerate(requested_ids)}
            ordered_files.sort(key=lambda item: (order_map.get(item["id"], 9999), item["name"]))
            scope_mode = "file_ids"
        else:
            ordered_files.sort(key=lambda item: (-float(item["uploaded_at_ts"]), item["name"]))
            scope_mode = "domain" if normalized_domain else "recent"

        capped_files = ordered_files[: max(1, min(int(max_files or 3), 8))]
        selected_chunks: List[Dict[str, Any]] = []
        selected_files: List[Dict[str, Any]] = []
        per_file_cap = max(1, min(int(max_chunks_per_file or 3), 8))

        for item in capped_files:
            item["chunks"].sort(
                key=lambda chunk: (
                    int(chunk.get("page_number") or 0),
                    int(chunk.get("chunk_index") or 0),
                    str(chunk.get("source_chunk_id") or ""),
                )
            )
            file_chunks = item["chunks"][:per_file_cap]
            selected_chunks.extend(file_chunks)
            selected_files.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "domain": item["domain"],
                    "uploaded_at": item["uploaded_at"],
                    "selected_chunk_count": len(file_chunks),
                    "available_chunk_count": len(item["chunks"]),
                }
            )

        return {
            "chunks": selected_chunks,
            "selected_files": selected_files,
            "available_file_count": len(ordered_files),
            "selected_file_count": len(selected_files),
            "scope_mode": scope_mode,
        }

    def search(self, query: str, top_k: int = 3, domain: str = None,
               where_filter: dict = None, search_mode: str = "hybrid",
               queries: list[str] | None = None,
               diversity: bool = False) -> List[Dict[str, Any]]:
        filter_domain = None
        filter_files = None
        if where_filter and isinstance(where_filter, dict):
            filter_domain = where_filter.get('domain', [None])[0] if isinstance(where_filter.get('domain'), list) else where_filter.get('domain')
            raw_files = where_filter.get('file_name')
            if isinstance(raw_files, dict):
                raw_files = raw_files.get('$in')
            if isinstance(raw_files, str):
                raw_files = [raw_files]
            if isinstance(raw_files, (list, tuple, set)):
                filter_files = {str(item) for item in raw_files if item}
        if not filter_domain:
            filter_domain = domain if (domain and domain != 'all') else None

        # ⛔ 2026-08-13：多查询融合（原问题 + 改写/扩展查询），候选取各查询的最优 rank
        query_list: list[str] = []
        for q in [query] + list(queries or []):
            q = (q or "").strip()
            if q and q not in query_list and len(query_list) < 3:
                query_list.append(q)

        candidates = {}
        for q in query_list:
            if search_mode in ("semantic", "hybrid"):
                if hasattr(self, '_embeddings') and self._embeddings is not None:
                    try:
                        query_vec = self.embed_model.encode([q])[0]
                        scores = np.dot(self._embeddings, query_vec)
                        valid_indices = [
                            i for i, m in enumerate(self._embed_metadatas)
                            if (not filter_domain or m.get('domain') == filter_domain)
                            and _match_file(m.get('physical_name', ''), filter_files)
                        ]
                        # 候选池扩大到 max(top_k*4, 40)（2026-08-14）：top_k*2 时，
                        # 含专名的 chunk 因单路排名 11~40 被截出候选池，短语精确命中
                        # 加权无从生效（经验：地球编年史卷数查询，前言 chunk 被截掉）
                        sorted_indices = sorted(valid_indices, key=lambda i: scores[i], reverse=True)[:max(top_k * 4, 40)]
                        for rank, idx in enumerate(sorted_indices):
                            if scores[idx] <= 0.01:
                                continue
                            text = self._embed_texts[idx]
                            meta = self._embed_metadatas[idx] if self._embed_metadatas else {}
                            key = (text[:100], meta.get('file_name', ''))
                            if key not in candidates:
                                candidates[key] = {
                                    'text': text, 'metadata': meta, 'emb_idx': idx,
                                    'vec_score': 0.0, 'bm25_score': 0.0,
                                    'vec_rank': 9999, 'bm25_rank': 9999,
                                }
                            candidates[key]['vec_score'] = max(candidates[key]['vec_score'], float(scores[idx]))
                            candidates[key]['vec_rank'] = min(candidates[key]['vec_rank'], rank + 1)
                    except Exception as e:
                        logger.warning("Embedding search failed: %s", e)

            if search_mode in ("keyword", "hybrid"):
                try:
                    bm25_results = self.bm25_search(
                        q,
                        top_k=max(top_k * 4, 40),
                        domain=filter_domain,
                        file_ids=filter_files,
                    )
                    for rank, r in enumerate(bm25_results):
                        text = r['text']
                        meta = r.get('metadata', {})
                        key = (text[:100], meta.get('file_name', ''))
                        if key not in candidates:
                            candidates[key] = {
                                'text': text, 'metadata': meta, 'emb_idx': None,
                                'vec_score': 0.0, 'bm25_score': 0.0,
                                'vec_rank': 9999, 'bm25_rank': 9999,
                            }
                        candidates[key]['bm25_score'] = max(candidates[key]['bm25_score'], float(r.get('score', 0.0)))
                        candidates[key]['bm25_rank'] = min(candidates[key]['bm25_rank'], rank + 1)
                except Exception as e:
                    logger.warning("BM25 search failed: %s", e)

        if not candidates:
            return []

        RRF_K = self.config.RRF_K
        RRF_VEC_WEIGHT = self.config.RRF_VEC_WEIGHT
        RRF_BM25_WEIGHT = self.config.RRF_BM25_WEIGHT
        # 自适应权重：根据候选文档类型动态调整。
        # 原理——小说/叙事类内容词频匹配重要（关键词精确），学术/科技类内容语义重要（概念理解）
        # 来源：SuperAI 16种RAG方法 -> 混合RAG 应"智能组合方法，不可能单一方法覆盖所有查询类型"
        _novel_hints = {"野狗", "三体", "番外", "小说", "传", "红", "黑", "斗"}
        _academic_hints = {"原则", "导论", "教程", "教材", "算法", "分析", "哲学", "经济"}
        for cand in candidates.values():
            meta = cand.get('metadata', {})
            source = meta.get('file_name', meta.get('source', ''))
            domain = meta.get('domain', '')
            doc_hint = source + domain
            if any(h in doc_hint for h in _novel_hints):
                vec_w, bm25_w = 0.55, 0.45  # 小说偏词频
            elif any(h in doc_hint for h in _academic_hints):
                vec_w, bm25_w = 0.80, 0.20  # 学术偏语义
            else:
                vec_w, bm25_w = RRF_VEC_WEIGHT, RRF_BM25_WEIGHT  # 默认
            vec_rrf = 1.0 / (RRF_K + cand.get('vec_rank', 9999))
            bm25_rrf = 1.0 / (RRF_K + cand.get('bm25_rank', 9999))
            cand['rrf_score'] = vec_w * vec_rrf + bm25_w * bm25_rrf

        # ⛔ 2026-08-14：专名精确命中加权 —— 两字专名（如"西琴"）被单字 BM25/
        # 向量语义稀释、精确段落排不上前时，按实体词条连续命中加分提升召回
        _apply_phrase_bonus(candidates.values(), query)

        sorted_candidates = sorted(candidates.values(), key=lambda x: x['rrf_score'], reverse=True)

        # ⛔ 2026-08-13：MMR 多样性重排（消除 top_k 内语义重复片段，RAG 问答开启）
        if diversity and sorted_candidates:
            sorted_candidates = self._mmr_select(sorted_candidates, top_k)

        results = []
        for cand in sorted_candidates[:top_k]:
            results.append({
                'text': cand['text'], 'metadata': cand['metadata'],
                'score': float(cand['rrf_score']),
                'distance': float(1.0 - cand.get('vec_score', 0.0))
            })
        return results

    def _mmr_select(self, candidates: list[dict], top_k: int,
                    lambda_weight: float = 0.7) -> list[dict]:
        """MMR 多样性重排：λ·相关性 − (1−λ)·与已选集合的最大相似度。
        参考标准 MMR 算法（hybrid-rag 等开源实现），缺向量的候选现算嵌入。"""
        if len(candidates) <= top_k:
            return candidates
        pool = candidates[: max(top_k * 4, 16)]
        vecs = []
        for cand in pool:
            vec = None
            emb_idx = cand.get("emb_idx")
            if emb_idx is not None and self._embeddings is not None and 0 <= emb_idx < len(self._embeddings):
                vec = self._embeddings[emb_idx]
            else:
                try:
                    vec = self.embed_model.encode([cand["text"]])[0]
                except Exception:
                    vec = None
            if vec is not None:
                norm = float(np.linalg.norm(vec))
                vec = vec / norm if norm > 1e-9 else None
            vecs.append(vec)
        selected: list[dict] = []
        selected_vecs: list = []
        remaining = list(range(len(pool)))
        scores = [cand.get("rrf_score", 0.0) for cand in pool]
        while remaining and len(selected) < top_k:
            best_i, best_val = None, -1.0
            for i in remaining:
                sim = 0.0
                if vecs[i] is not None and selected_vecs:
                    sim = max(float(np.dot(vecs[i], v)) for v in selected_vecs)
                val = lambda_weight * scores[i] - (1.0 - lambda_weight) * sim
                if val > best_val:
                    best_val, best_i = val, i
            if best_i is None:
                break
            selected.append(pool[best_i])
            if vecs[best_i] is not None:
                selected_vecs.append(vecs[best_i])
            remaining.remove(best_i)
        return selected

    def bm25_search(self, query: str, top_k: int = 3, domain: str = None,
                    file_ids: set[str] | None = None) -> List[Dict[str, Any]]:
        if not self.bm25 or not self.bm25_docs:
            return []
        try:
            tokenized_query = _bm25_tokenizer(query)
            scores = self.bm25.get_scores(tokenized_query)
            eligible_indices = [
                i for i, meta in enumerate(self.bm25_metadatas)
                if (not domain or meta.get('domain') == domain)
                and _match_file(meta.get('physical_name', ''), file_ids)
            ]
            top_indices = sorted(eligible_indices, key=lambda i: scores[i], reverse=True)[:top_k]
            sources = []
            for i in top_indices:
                meta = self.bm25_metadatas[i] if self.bm25_metadatas else {}
                if scores[i] > 0:
                    sources.append({
                        'text': self.bm25_docs[i],
                        'metadata': meta,
                        'score': float(scores[i])
                    })
            return sources
        except Exception as e:
            logger.error("BM25 search failed: %s", e)
            return []

    def get_stats(self) -> Dict[str, Any]:
        try:
            count = len(self.bm25_docs) if self.bm25_docs else 0
            files = self.list_files()
            return {'total_chunks': count, 'file_count': len(files), 'files': files, 'domain_stats': {}}
        except Exception as e:
            logger.error("Get stats failed: %s", e)
            return {'total_chunks': 0, 'file_count': 0, 'domain_stats': {}}
