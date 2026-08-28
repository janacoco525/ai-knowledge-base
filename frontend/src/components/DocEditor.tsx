import React, { useState, useEffect, useRef, useMemo, useCallback, useDeferredValue } from "react";
import { Document, Highlight } from "../types";
import { 
  Sparkles, FileText, Trash2, Eye, Code, 
  Cpu, FileCode, Loader2, Save, List, Edit3, 
  Download, Sparkle, Trash, ChevronRight, MessageSquare, BookOpen, Clock, Heart, Plus, ChevronDown, Bookmark, Languages, X
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import PdfViewer from "./PdfViewer";
import DocToolBar from "./DocToolBar"; // T9：工具栏面板提取（2026-08-06）
import { hasOriginalFile } from "../lib/fileStorage";
import { getInnerText, generateHeadingId, isStructuralHeading, preprocessContent, splitParagraphGroups } from "../lib/docEditorUtils";

// 正文渲染缓存：同内容文本不重复解析 ReactMarkdown（切换文档时复用）
const _mdRenderCache = new Map<string, {node: React.ReactNode; count: number}>();
const MD_CACHE_MAX = 15;  // 最多缓存 15 份文档的渲染结果

function _mdCacheKey(text: string, highlightsLen: number): string {
  // 用文本前 100 字符 + 高亮数量作为缓存键（避免高亮变化后使用旧渲染）
  return `${text.slice(0, 100)}|hl=${highlightsLen}`;
}

function _mdCachePut(key: string, node: React.ReactNode) {
  if (_mdRenderCache.size >= MD_CACHE_MAX) {
    const first = _mdRenderCache.keys().next().value;
    if (first) _mdRenderCache.delete(first);
  }
  _mdRenderCache.set(key, { node, count: _mdRenderCache.size });
}

// ⛔ 2026-08-13：把"去空白后的子串索引"映射回原始文本偏移（用于 Range 精确句子定位）
function _findNormRange(text: string, normalizedTarget: string): { start: number; end: number } | null {
  if (!text || !normalizedTarget) return null;
  const norm = text.replace(/\s+/g, "");
  const normIdx = norm.indexOf(normalizedTarget);
  if (normIdx < 0) return null;
  let raw = 0;
  let seen = 0;
  while (seen < normIdx && raw < text.length) {
    if (!/\s/.test(text[raw])) seen += 1;
    raw += 1;
  }
  const start = raw;
  let end = raw;
  while (seen < normIdx + normalizedTarget.length && end < text.length) {
    if (!/\s/.test(text[end])) seen += 1;
    end += 1;
  }
  return { start, end };
}

// 正文渲染组件（memo化 + 模块级缓存，避免切文档时重解析大文本）
const MarkdownBlock = React.memo(function MarkdownBlock({ 
  text, highlightCheckRegex, renderHighlights, highlightsLen
}: { 
  text: string; 
  highlightCheckRegex: RegExp | null; 
  renderHighlights: (node: React.ReactNode) => React.ReactNode;
  highlightsLen: number;
}) {
  if (!text) return null;

  // 模块级缓存：相同文本 + 无高亮变化 → 不重解析 ReactMarkdown
  const cacheKey = text.length > 200 ? _mdCacheKey(text, highlightsLen) : "";
  if (cacheKey && _mdRenderCache.has(cacheKey)) {
    const cached = _mdRenderCache.get(cacheKey)!;
    cached.count++;
    return cached.node as React.ReactElement;
  }

  // ReactMarkdown requires its root children to be a string. Highlights are
  // applied by the block renderers below, after Markdown has been parsed.
  const content = text;

  const mdNode = (
    <ReactMarkdown
      components={{
        h1: ({ children, ...props }) => {
          const id = generateHeadingId(getInnerText(children).trim());
          return <h1 id={id} className="text-2xl font-extrabold text-zinc-900 mt-8 mb-4 pb-2 border-b-2 border-zinc-200 scroll-mt-16">{renderHighlights(children)}</h1>;
        },
        h2: ({ children, ...props }) => {
          const id = generateHeadingId(getInnerText(children).trim());
          return <h2 id={id} className="text-lg font-bold text-zinc-850 mt-6 mb-3 pl-3 border-l-3 border-emerald-500 scroll-mt-16">{renderHighlights(children)}</h2>;
        },
        h3: ({ children, ...props }) => {
          const id = generateHeadingId(getInnerText(children).trim());
          return <h3 id={id} className="text-[15px] font-bold text-zinc-700 mt-5 mb-2 scroll-mt-16">{renderHighlights(children)}</h3>;
        },
        p: ({ children }) => {
          const textVal = getInnerText(children);
          const pageMatch = textVal.trim().match(/^---\s*第\s*(\d+)\s*页\s*\/\s*共\s*(\d+)\s*页\s*---$/);
          if (pageMatch) return null;
          return <p className="text-zinc-800 text-[15px] leading-8 mb-5 tracking-normal indent-4">{renderHighlights(children)}</p>;
        },
        h4: ({ children, ...props }) => {
          const id = generateHeadingId(getInnerText(children).trim());
          return <h4 id={id} className="text-[13px] font-semibold text-zinc-600 mt-4 mb-1.5 scroll-mt-16">{renderHighlights(children)}</h4>;
        },
        li: ({ children }) => <li className="text-zinc-800 text-[14px] leading-7 mb-1.5 font-sans">{renderHighlights(children)}</li>,
        ul: ({ children }) => <ul className="list-disc pl-5 mb-5 text-zinc-800 space-y-1">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal pl-5 mb-5 text-zinc-800 space-y-1">{children}</ol>,
        code: ({ children }) => <code className="p-0.5 px-1.5 bg-zinc-100 text-amber-800 rounded font-mono text-[11px] font-semibold">{children}</code>,
        pre: ({ children }) => <pre className="p-4 bg-zinc-900 text-zinc-200 rounded-lg overflow-x-auto text-[11px] font-mono my-4 shadow-inner">{children}</pre>,
        blockquote: ({ children }) => <blockquote className="pl-4 border-l-[3px] border-emerald-400 italic text-zinc-500 my-4 bg-zinc-50/60 py-2.5 pr-3 rounded-r-md">{renderHighlights(children)}</blockquote>,
        img: ({ src, alt }) => {
          if (!src) return null;
          return <figure className="my-6 text-center"><img src={src} alt={alt || "图片"} loading="lazy" className="max-w-full h-auto rounded-lg border border-zinc-200 mx-auto shadow-sm" style={{ maxHeight: "70vh" }} onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />{alt && <figcaption className="text-[11px] text-zinc-400 mt-2">{alt}</figcaption>}</figure>;
        },
        hr: () => <hr className="my-8 border-0 h-px bg-gradient-to-r from-transparent via-zinc-300 to-transparent" />,
        table: ({ children }) => <div className="overflow-x-auto my-4"><table className="w-full border-collapse text-sm">{children}</table></div>,
        th: ({ children }) => <th className="border border-zinc-200 bg-zinc-50 px-3 py-2 text-left font-semibold text-zinc-700 text-xs">{children}</th>,
        td: ({ children }) => <td className="border border-zinc-200 px-3 py-2 text-zinc-700 text-xs">{children}</td>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
  if (cacheKey) _mdCachePut(cacheKey, mdNode);
  return mdNode;
});


interface DocEditorProps {
  document: Document | null;
  onUpdateDocument: (docId: string, updates: Partial<Document>) => void;
  onDeleteDocument: (docId: string) => void;
  onCreateExternalGraph: (nodes: any[], edges: any[], docId: string) => void;
  onLookupAndAddTerm: (term: string) => void;
  isDefiningTerm: boolean;
  contentLoading?: boolean;  // T38：正文加载状态由 App 统一管理，DocEditor 只消费
  cachedPreprocessed?: string;
  cachedHeadings?: { id: string; text: string; level: number }[];
  locateText?: string | null;  // 2026-08-13：引用直达原文 —— 定位并高亮包含该句的段落
  locateSeq?: number;  // 2026-08-13：定位序号（同句重复点击也触发重新定位）
}

export default function DocEditor({
  document,
  onUpdateDocument,
  onDeleteDocument,
  onCreateExternalGraph,
  onLookupAndAddTerm,
  isDefiningTerm,
  contentLoading = false,
  cachedPreprocessed,
  cachedHeadings,
  locateText = null,
  locateSeq = 0,
}: DocEditorProps) {
  const [title, setTitle] = useState(document?.title || "");
  const [content, setContent] = useState(document?.content || "");
  
  // Percent width allocated to text editor component (0% is preview-only, 100% is edit-only, 50% is half-split)
  const [editorPercent, setEditorPercent] = useState<number>(() => {
    const saved = localStorage.getItem("kb_editor_percent");
    return saved ? parseInt(saved, 10) : 50;
  });

  // Sync editorPercent state
  useEffect(() => {
    localStorage.setItem("kb_editor_percent", editorPercent.toString());
  }, [editorPercent]);

  // Derived viewMode state matches old API beautifully to preserve external side-effects
  const viewMode = editorPercent === 0 ? "preview" : editorPercent === 100 ? "edit" : "split";
  
  // Live width allocated to the right analysis tool sidebar
  const [rightSidebarWidth, setRightSidebarWidth] = useState<number>(() => {
    const saved = localStorage.getItem("kb_right_sidebar_width");
    return saved ? parseInt(saved, 10) : 260;
  });

  // Sync right-sidebar width to localstorage layout settings
  useEffect(() => {
    localStorage.setItem("kb_right_sidebar_width", rightSidebarWidth.toString());
  }, [rightSidebarWidth]);

  // 阅读设置：字号 — 用 ref 直操作 DOM，不触发 React 渲染
  const readerFontSizeRef = useRef<number>(
    parseInt(localStorage.getItem("kb_reader_font_size") || "15", 10)
  );
  const [readerFontSizeDisplay, setReaderFontSizeDisplay] = useState(readerFontSizeRef.current);

  const updateReaderFontSize = (delta: number) => {
    const next = Math.max(10, Math.min(24, readerFontSizeRef.current + delta));
    readerFontSizeRef.current = next;
    setReaderFontSizeDisplay(next);
    localStorage.setItem("kb_reader_font_size", String(next));
    // 直接用 CSS 变量缩放，不触发 React 渲染
    const container = window.document.getElementById("rendered-preview-document-container");
    if (container) {
      container.style.setProperty("--reader-zoom", String(next / 16));
    }
  };

  // 初始化 CSS 变量
  useEffect(() => {
    const container = window.document.getElementById("rendered-preview-document-container");
    if (container) {
      container.style.setProperty("--reader-zoom", String(readerFontSizeRef.current / 16));
    }
  }, []);

  const [readerBg, setReaderBg] = useState<string>(() => {
    return localStorage.getItem("kb_reader_bg") || "light";
  });
  const [showPdfViewer, setShowPdfViewer] = useState(false);
  const [hasPdfFile, setHasPdfFile] = useState(false);
  // T38：正文加载状态改用 App 下发的 props（contentLoading），不再自行 fetch
  const [serverHeadings, setServerHeadings] = useState<{id:string;text:string;level:number}[]>([]);
  const [serverPreprocessed, setServerPreprocessed] = useState<string>("");  // 服务端预处理文本

  useEffect(() => {
    if (!document?.id) return;
    // PDF: 优先用后端 fileType，兜底 IndexedDB + 文件名后缀
    const isPdfFile = document.fileType === ".pdf"
      || (document.title || document.id || "").toLowerCase().endsWith(".pdf");
    hasOriginalFile(document.title).then(stored => setHasPdfFile(isPdfFile || stored));

    // T38：正文由 App 统一加载（content/loading 均为 props），这里只做同步，不再自行 fetch
    setContent(document.content || "");
    if (document.content) {
      if (cachedPreprocessed) setServerPreprocessed(cachedPreprocessed);
      if (cachedHeadings && cachedHeadings.length > 0) setServerHeadings(cachedHeadings);
    } else {
      setServerHeadings([]);
      setServerPreprocessed("");
    }
  }, [document?.title, document?.id, document?.content, cachedPreprocessed, cachedHeadings]);
  useEffect(() => {
    localStorage.setItem("kb_reader_bg", readerBg);
  }, [readerBg]);

  // Ref container to measure dynamic client percentage splits on drag
  const splitContainerRef = useRef<HTMLDivElement>(null);
  
  // View layout dropdown state
  const [showViewDropdown, setShowViewDropdown] = useState(false);
  
  // Selection and Annotation states
  const [selectedText, setSelectedText] = useState("");
  const [highlightColor, setHighlightColor] = useState<'yellow' | 'green' | 'pink' | 'blue'>('yellow');
  const [noteComment, setNoteComment] = useState("");

  // AI Task operations states
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);
  const [aiResponseStatus, setAiResponseStatus] = useState("");

  // Interpret Selected Phrase States
  const [interpretText, setInterpretText] = useState("");
  const [interpretationResult, setInterpretationResult] = useState("");
  const [isInterpreting, setIsInterpreting] = useState(false);
  const [interpretingMode, setInterpretingMode] = useState<"simple" | "deep" | null>(null);

  // Synchronize selection changes to interpret input box
  useEffect(() => {
    if (selectedText) {
      setInterpretText(selectedText);
    }
  }, [selectedText]);

  // Reading progress and side outline structure states
  const [readingProgress, setReadingProgress] = useState(0);
  const [showSidebar, setShowSidebar] = useState(true);
  const [sidebarTab, setSidebarTab] = useState<"outline" | "notes" | "ai">("outline");
  const previewContainerRef = useRef<HTMLDivElement>(null);

  // 2026-08-06 内联译文模式：全文翻译结果（与原文分组建对），渲染在每段原文下方
  const [translationGroups, setTranslationGroups] = useState<{ src: string; tgt: string; skipped?: boolean }[] | null>(null);
  const [isTranslatingFull, setIsTranslatingFull] = useState(false);
  // 2026-08-06 长文档提速：任务式增量翻译（进度 + 错误 + 轮询定时器）
  const [translateProgress, setTranslateProgress] = useState<{ done: number; total: number } | null>(null);
  const [translateError, setTranslateError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 2026-08-06 翻译暂存/更新/取消：任务 id 记录（供取消与刷新后恢复轮询）；ref 镜像供异步回调读最新值
  const translateTaskIdRef = useRef<string | null>(null);
  const isTranslatingFullRef = useRef(false);
  const translationGroupsRef = useRef<{ src: string; tgt: string; skipped?: boolean }[] | null>(null);

  // Bookmark Dialog States
  const [showBookmarkModal, setShowBookmarkModal] = useState(false);
  const [bookmarkInputLabel, setBookmarkInputLabel] = useState("");
  const [pendingBookmarkScrollTop, setPendingBookmarkScrollTop] = useState(0);
  const [pendingBookmarkRatio, setPendingBookmarkRatio] = useState(0);

  // Editing comment states
  const [editingHighlightId, setEditingHighlightId] = useState<string | null>(null);
  const [editingCommentText, setEditingCommentText] = useState("");

  // Dynamic extraction of headings to compile TOC
  const getHeadings = (text: string) => {
    if (!text) return [];
    const lines = text.split("\n");
    const foundHeadings: { id: string; text: string; level: number; lineIndex: number }[] = [];
    
    const seen = new Set<string>();
    lines.forEach((line, lineIdx) => {
      const trimmed = line.trim();
      if (trimmed.startsWith("![")) return;
      if (/^---?\s*第\s*\d+\s*页/.test(trimmed)) return;
      const match = trimmed.match(/^(#{1,6})\s+(.+)$/);
      if (match) {
        const level = match[1].length;
        let headingText = match[2].trim().replace(/[*_`#]/g, "").replace(/^\d+\s*[\.\、\s]+/, "");
        // 过滤无意义标题：单字、纯数字/标点、"完"/"终"等章节结束标记
        const noiseWords = /^[完终結结]$|^第?\s*\d+\s*[页頁]$|^[\(\)（）\[\]【】\{\}〈〉《》「」『』〝〞'\"‘’“”″′\.\,\;\:\!\?\、。，；：！？…\-\—\~\～\@\#\$\%\^\&\*\(\)\_\+\=\|\`\~\[\]\{\}\\\/\<\>]+$/;
        if (headingText.length < 2 || /^[\d\.\-\s]+$/.test(headingText) || noiseWords.test(headingText)) return;
        const id = generateHeadingId(headingText);
        if (seen.has(id)) return;
        seen.add(id);
        foundHeadings.push({ id, text: headingText, level, lineIndex: lineIdx });
      }
    });

    // 过滤内联目录块：连续5+个标题且间距远小于正文章节间距
    if (foundHeadings.length > 5) {
      let tocStart = -1;
      let maxConsecutive = 0;
      let consecutiveCount = 1;
      for (let i = 1; i < foundHeadings.length; i++) {
        const gap = foundHeadings[i].lineIndex - foundHeadings[i-1].lineIndex;
        // 内联目录条目间距通常 1~20 行，正文章节间距通常 48+ 行
        if (gap <= 25) {
          consecutiveCount++;
        } else {
          if (consecutiveCount >= 5) {
            maxConsecutive = Math.max(maxConsecutive, consecutiveCount);
            if (tocStart < 0) tocStart = i - consecutiveCount;
          }
          consecutiveCount = 1;
        }
      }
      if (consecutiveCount >= 5) {
        maxConsecutive = Math.max(maxConsecutive, consecutiveCount);
        if (tocStart < 0) tocStart = foundHeadings.length - consecutiveCount;
      }

      // 如果发现 TOC 块，移除其中重复度最高的条目（保留正文中的）
      if (maxConsecutive >= 5) {
        // 策略：大块连续标题出现在开头/末尾时，整块过滤
        if (tocStart <= 5 || tocStart >= foundHeadings.length - maxConsecutive - 3) {
          const filtered = foundHeadings.filter((_, i) => i < tocStart || i >= tocStart + maxConsecutive);
          return filtered.map(({ id, text, level }) => ({ id, text, level }));
        }
      }
    }

    // 同类标题去重：同一"第N章"前缀只保留第一次出现（正文中的）
    const chapterSeen = new Set<string>();
    const deduped: { id: string; text: string; level: number }[] = [];
    for (const h of foundHeadings) {
      const chMatch = h.text.match(/^(第\s*[一二三四五六七八九十百\d]+\s*[章節节回课分部篇卷])/);
      if (chMatch) {
        const prefix = chMatch[1].replace(/\s+/g, '');
        if (chapterSeen.has(prefix)) continue;
        chapterSeen.add(prefix);
      }
      deduped.push({ id: h.id, text: h.text, level: h.level });
    }
    return deduped;
  };

  // Dynamic extraction and on-the-fly markdown normalization of content directories
  const preprocessedContent = React.useMemo(() => {
    // 优先使用服务端预处理文本（已含分段/脚注清理/标题标记）
    if (serverPreprocessed) return serverPreprocessed;
    return preprocessContent(content);
  }, [content, serverPreprocessed]);

  // 延迟渲染：让 React 在后台处理大文本 ReactMarkdown，主线程不阻塞 UI 交互
  const deferredContent = useDeferredValue(preprocessedContent);
  const isContentStale = preprocessedContent !== deferredContent;

  // ── 超长文档分块渲染：每块独立 ReactMarkdown，互不重解析 ──
  const [renderChunkCount, setRenderChunkCount] = useState(1);
  // 超长文档译文内联：逐组渐进渲染（每批 12 组，避免百组级 MarkdownBlock 一次渲染卡首屏）
  const [pairRenderCount, setPairRenderCount] = useState(12);
  // 把超长文本切成块数组（每块约 8 万字符），每块独立渲染
  const contentChunks = React.useMemo(() => {
    const full = deferredContent;
    if (full.length <= 300000) return null;
    const chunkSize = 80000;
    const chunks: string[] = [];
    for (let i = 0; i < full.length; i += chunkSize) {
      chunks.push(full.slice(i, i + chunkSize));
    }
    return chunks;
  }, [deferredContent]);

  // 首屏只渲染前几块，后台逐块补全
  const visibleChunkCount = contentChunks
    ? Math.max(1, Math.min(renderChunkCount, contentChunks.length))
    : 0;
  const isChunkRendering = !!contentChunks && visibleChunkCount < contentChunks.length;

  useEffect(() => {
    if (!contentChunks) { setRenderChunkCount(1); return; }
    if (renderChunkCount >= contentChunks.length) return;
    const timer = setTimeout(() => setRenderChunkCount(c => Math.min(c + 1, contentChunks.length)), 200);
    return () => clearTimeout(timer);
  }, [contentChunks, renderChunkCount]);

  // 译文组渐进渲染：仅切换文档时复位（增量轮询会更新 translationGroups 引用，不能依赖它）
  useEffect(() => {
    setPairRenderCount(12);
  }, [document?.id]);

  // ⛔ 2026-08-13：引用直达原文 —— 找到包含证据句的段落，滚动到视口中央并闪烁高亮
  const locateSeqRef = useRef(0);
  useEffect(() => {
    // 内容未加载完（contentLoading）时不尝试；加载完成由依赖变化重新触发定位
    if (!locateText || contentLoading) return;
    const seq = ++locateSeqRef.current;
    let attempts = 0;
    const MAX_ATTEMPTS = 25;  // 大文档 ReactMarkdown 渲染耗时可能数秒：最长约 10 秒
    const normalize = (s: string) => s.replace(/\s+/g, "");

    const tryLocate = () => {
      if (locateSeqRef.current !== seq) return;
      const root = previewContainerRef.current;
      if (!root) return;
      // 匹配候选逐级降级：整句 → 去空白整句 → 前20字 → 前12字 → 前8字（容忍预处理/空白差异）
      const normText = normalize(locateText);
      const candidates = [locateText, normText];
      const head20 = normText.slice(0, 20);
      const head12 = normText.slice(0, 12);
      const head8 = normText.slice(0, 8);
      if (head20 && !candidates.includes(head20)) candidates.push(head20);
      if (head12 && !candidates.includes(head12)) candidates.push(head12);
      if (head8 && !candidates.includes(head8)) candidates.push(head8);
      const rootEls = Array.from(root.querySelectorAll<HTMLElement>("p, li, blockquote, h1, h2, h3, h4, td"));
      const docEls = root === window.document.body
        ? rootEls
        : Array.from(window.document.querySelectorAll<HTMLElement>("p, li, blockquote, h1, h2, h3, h4, td"));
      let target: HTMLElement | undefined;
      let matchedCandidate = "";
      for (const c of candidates) {
        if (!c) continue;
        const hit = rootEls.find(el => normalize(el.textContent || "").includes(c))
          || docEls.find(el => normalize(el.textContent || "").includes(c));
        if (hit) {
          target = hit;
          matchedCandidate = c;
          break;
        }
      }
      if (!target) {
        attempts += 1;
        if (attempts < MAX_ATTEMPTS) {
          window.setTimeout(tryLocate, 400);
        }
        return;
      }
      const cRect = root.getBoundingClientRect();
      // ⛔ 2026-08-13：精确句子定位 —— Range 定位证据句在段落内的字符位置，滚动到该行
      // （同一段落内不同证据句滚到不同行，而不是都停在段落顶部/中心）
      const exactRect = (() => {
        try {
          const walker = window.document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
          const normC = normalize(matchedCandidate);
          while (walker.nextNode()) {
            const tn = walker.currentNode as Text;
            const text = tn.textContent || "";
            const r = _findNormRange(text, normC);
            if (r) {
              const range = window.document.createRange();
              range.setStart(tn, r.start);
              range.setEnd(tn, r.end);
              const rect = range.getBoundingClientRect();
              if (rect && (rect.width > 0 || rect.height > 0)) return rect;
            }
          }
        } catch {
          /* 兼容性兜底：走段落级滚动 */
        }
        return null;
      })();
      if (exactRect) {
        root.scrollTop += exactRect.top - cRect.top - cRect.height / 2 + (exactRect.height || 18) / 2;
      } else {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        const tRect = target.getBoundingClientRect();
        if (tRect.top < cRect.top || tRect.bottom > cRect.bottom) {
          root.scrollTop += tRect.top - cRect.top - cRect.height / 2 + tRect.height / 2;
        }
      }
      target.style.transition = "background-color 0.45s ease, box-shadow 0.45s ease";
      target.style.backgroundColor = "#fef08a";
      target.style.boxShadow = "0 0 0 3px rgba(250, 204, 21, 0.45)";
      window.setTimeout(() => {
        target.style.backgroundColor = "";
        target.style.boxShadow = "";
      }, 2400);
    };
    const timer = window.setTimeout(tryLocate, 300);
    return () => window.clearTimeout(timer);
  }, [locateText, locateSeq, contentLoading]);

  const pairTotal = translationGroups?.length || 0;
  useEffect(() => {
    if (!pairTotal || pairRenderCount >= pairTotal) return;
    const timer = setTimeout(() => setPairRenderCount(c => Math.min(c + 12, pairTotal)), 200);
    return () => clearTimeout(timer);
  }, [pairTotal, pairRenderCount]);

  const headings = React.useMemo(() => {
    // 优先使用后端返回的标题（已在API中预计算，省去前端185KB扫描）
    if (serverHeadings.length > 0) return serverHeadings;
    return getHeadings(preprocessedContent);
  }, [preprocessedContent, serverHeadings]);

  // 当前可视章节追踪（IntersectionObserver）
  const [activeHeadingId, setActiveHeadingId] = useState<string>("");
  const [tocSearch, setTocSearch] = useState<string>("");  // TOC 搜索/过滤
  useEffect(() => {
    if (headings.length === 0) return;
    const container = previewContainerRef.current;
    if (!container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // 找第一个在视口内的标题（取最靠上的）
        const visible = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          setActiveHeadingId(visible[0].target.id);
        }
      },
      { root: container, rootMargin: "-60px 0px -60% 0px", threshold: 0 }
    );

    // 监听所有标题元素
    const headingElements = headings
      .map(h => window.document.getElementById(h.id))
      .filter(Boolean) as HTMLElement[];
    headingElements.forEach(el => observer.observe(el));

    return () => observer.disconnect();
  }, [headings, preprocessedContent]);

  const tocListRef = useRef<HTMLDivElement>(null);

  // TOC 自动滚动到当前激活的章节
  useEffect(() => {
    if (!activeHeadingId || !tocListRef.current) return;
    const activeEl = tocListRef.current.querySelector(`[data-heading-id="${CSS.escape(activeHeadingId)}"]`);
    if (activeEl) {
      activeEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [activeHeadingId]);

  // Pre-compiled regex cache for matching highlights with multi-line/whitespace tolerance
  const highlightRegex = React.useMemo(() => {
    const activeHighlights = document?.highlights || [];
    if (activeHighlights.length === 0) return null;
    
    // Process each highlight to create a whitespace-flexible regex pattern
    const patterns = activeHighlights
      .map(h => h.text.trim())
      .filter(Boolean)
      .sort((a, b) => b.length - a.length) // Match longer patterns first
      .map(txt => {
        const escaped = txt.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
        return escaped.replace(/\s+/g, '\\s+'); // support multi-space and line wraps
      });
      
    if (patterns.length === 0) return null;
    return new RegExp(`(${patterns.join('|')})`, 'gi');
  }, [document?.highlights]);

  // A ultra high-performance non-capturing regex check for quick early-outs
  const highlightRegexCheck = React.useMemo(() => {
    const activeHighlights = document?.highlights || [];
    if (activeHighlights.length === 0) return null;
    
    const patterns = activeHighlights
      .map(h => h.text.trim())
      .filter(Boolean)
      .map(txt => {
        const escaped = txt.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
        return escaped.replace(/\s+/g, '\\s+');
      });
      
    if (patterns.length === 0) return null;
    return new RegExp(patterns.join('|'), 'i'); // no capture group, no global tag, super fast
  }, [document?.highlights]);

  const handleScrollToHeading = (headingId: string, headingText?: string) => {
    let element = window.document.getElementById(headingId);
    // NCX 提取的 TOC 项 ID（如 ncx-93）不匹配 DOM 元素 ID（heading-...）
    // 退化：按文本重新计算 heading ID
    if (!element && headingText) {
      const realId = generateHeadingId(headingText);
      element = window.document.getElementById(realId);
    }
    const container = previewContainerRef.current;
    if (element && container) {
      const relativeTop = element.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 60;
      requestAnimationFrame(() => {
        container.scrollTop = relativeTop;
        requestAnimationFrame(() => { if (Math.abs(container.scrollTop - relativeTop) > 5) container.scrollTop = relativeTop; });
      });
    }
  };

  // 滚动进度：用 ref 直写 DOM 避免 React 渲染，仅每隔 200ms 同步 state
  const scrollTicking = useRef(false);
  const progressBarRef = useRef<HTMLDivElement>(null);
  const progressTextRef = useRef<HTMLElement>(null);
  const lastProgressStateUpdate = useRef(0);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const handleScrollProgress = (e: React.UIEvent<HTMLDivElement>) => {
    // ✅ 同步捕获：React 合成事件在回调返回后即被回收
    const target = e.currentTarget;
    if (!scrollTicking.current) {
      scrollTicking.current = true;
      requestAnimationFrame(() => {
        if (!target || !mountedRef.current) { scrollTicking.current = false; return; }
        const totalHeight = target.scrollHeight - target.clientHeight;
        // 短文档无滚动条：用户滚到底（scrollTop=0 但内容全可见）→ 100%
        // 用户在顶部 → 0%；避免一打开就跳 100%
        const percent = totalHeight <= 0
          ? (target.scrollTop > 0 ? 100 : 0)
          : Math.round((target.scrollTop / totalHeight) * 100);
        const clamped = Math.min(100, Math.max(0, percent));
        // 直写 DOM 进度条（不触发 React 渲染）
        if (progressBarRef.current) {
          progressBarRef.current.style.width = `${clamped}%`;
        }
        if (progressTextRef.current) {
          progressTextRef.current.textContent = `${clamped}%`;
        }
        // 仅每隔 200ms 同步一次 React state（供其他消费者读取）
        const now = Date.now();
        if (now - lastProgressStateUpdate.current > 200) {
          lastProgressStateUpdate.current = now;
          setReadingProgress(clamped);
        }
        scrollTicking.current = false;
      });
    }
    // localStorage 写入节流到 2 秒一次
    const now = Date.now();
    if (document?.id && now - (scrollSaveRef.current || 0) > 2000) {
      scrollSaveRef.current = now;
      localStorage.setItem("scroll_pos_" + document.id, String(target.scrollTop));
    }
  };
  const scrollSaveRef = useRef(0);

  // Sync scroll position tracking when content changes
  useEffect(() => {
    if (previewContainerRef.current) {
      const target = previewContainerRef.current;
      const totalHeight = target.scrollHeight - target.clientHeight;
      if (totalHeight <= 0) {
        // 短文档（无滚动条）→ 进度=100% 但仅当用户实际看到底部才设
        // 初次加载时仍是 0%，避免"一打开就 100%"的误导
        if (target.scrollTop > 0) {
          setReadingProgress(100);
        } else {
          setReadingProgress(0);
        }
      } else {
        const percent = Math.round((target.scrollTop / totalHeight) * 100);
        setReadingProgress(Math.min(100, Math.max(0, percent)));
      }
    }
  }, [content, viewMode]);

  // 切换文档或内容加载完成后恢复滚动位置
  useEffect(() => {
    if (!document?.id || !previewContainerRef.current) return;
    const saved = localStorage.getItem("scroll_pos_" + document.id);
    const top = saved ? parseInt(saved, 10) : 0;
    if (top <= 0) return;
    const el = previewContainerRef.current;
    // 重试恢复：内容可能仍在异步渲染，等 scrollHeight 足够大再设
    let attempts = 0;
    const tryRestore = () => {
      if (el.scrollHeight > top) {
        el.scrollTop = top;
        requestAnimationFrame(() => {
          if (Math.abs(el.scrollTop - top) > 5) el.scrollTop = top;
        });
      } else if (attempts < 10) {
        attempts++;
        requestAnimationFrame(tryRestore);
      }
    };
    requestAnimationFrame(tryRestore);
  }, [document?.id, content, serverPreprocessed]);

  // Bookmark actions
  const handleAddBookmark = () => {
    if (!document || !previewContainerRef.current) return;
    const element = previewContainerRef.current;
    const ratio = readingProgress;
    const scrollTop = element.scrollTop;
    
    let nearestHeading = "";
    if (headings.length > 0) {
      let bestHeading = headings[0].text;
      let minDiff = Infinity;
      headings.forEach((h) => {
        const el = window.document.getElementById(h.id);
        if (el) {
          const diff = scrollTop - el.offsetTop;
          if (diff >= -100 && diff < minDiff) {
            minDiff = diff;
            bestHeading = h.text;
          }
        }
      });
      nearestHeading = ` (${bestHeading})`;
    }

    setPendingBookmarkScrollTop(scrollTop);
    setPendingBookmarkRatio(ratio);
    setBookmarkInputLabel(`已读 ${ratio}%${nearestHeading}`);
    setShowBookmarkModal(true);
  };

  const handleConfirmAddBookmark = () => {
    if (!document) return;
    const label = bookmarkInputLabel.trim() || `已读 ${pendingBookmarkRatio}% 点`;
    const newBookmark = {
      id: `bm-${Date.now()}`,
      label,
      scrollTop: pendingBookmarkScrollTop,
      ratio: pendingBookmarkRatio,
      createdAt: new Date().toISOString()
    };
    
    const existing = document.bookmarks || [];
    const updated = [newBookmark, ...existing];
    onUpdateDocument(document.id, { bookmarks: updated } as any);
    
    setShowBookmarkModal(false);
    setAiResponseStatus("书签添加成功！已记入下方书签夹中。");
    setTimeout(() => setAiResponseStatus(""), 3000);
  };

  const handleDeleteBookmark = (bmId: string) => {
    if (!document) return;
    const existing = document.bookmarks || [];
    const updated = existing.filter(bm => bm.id !== bmId);
    onUpdateDocument(document.id, { bookmarks: updated } as any);
  };

  const handleScrollToBookmark = (scrollTop: number) => {
    const el = previewContainerRef.current;
    if (el) {
      // 1. Try modern smooth scroll API
      try {
        el.scrollTo({
          top: scrollTop,
          behavior: "smooth"
        });
      } catch (err) {
        console.warn("Smooth scroll failed", err);
      }
      
      // 2. Direct instant scrollTop assignment (this is 100% reliable in all environments/iframes)
      el.scrollTop = scrollTop;
      
      // 3. Multi-stage timeout fallbacks to counter rendering delay lag
      const t1 = setTimeout(() => { el.scrollTop = scrollTop; }, 30);
      const t2 = setTimeout(() => { el.scrollTop = scrollTop; }, 120);
      const t3 = setTimeout(() => { el.scrollTop = scrollTop; }, 300);
      
      return () => {
        clearTimeout(t1);
        clearTimeout(t2);
        clearTimeout(t3);
      };
    }
  };

  // Keep state sync with selected document changes
  useEffect(() => {
    if (document) {
      setTitle(document.title);
      setAiResponseStatus("");
      setSelectedText("");
      setNoteComment("");
      setTranslationGroups(null);
      setIsTranslatingFull(false);
      setTranslateProgress(null);
      setTranslateError(null);
      // 切换文档：停止旧任务轮询（任务在后端继续，缓存落盘；重开文档时自动恢复）
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      translateTaskIdRef.current = null;
      // content is loaded async via fetch in the other useEffect
    }
  }, [document]);

  // 卸载时清理翻译轮询定时器（防内存泄漏/卸载后 setState）
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  // ref 镜像：异步回调里读取最新 state（避免闭包过期）
  useEffect(() => { isTranslatingFullRef.current = isTranslatingFull; }, [isTranslatingFull]);
  useEffect(() => { translationGroupsRef.current = translationGroups; }, [translationGroups]);

  // 任务轮询：增量获取译文；done/failed/cancelled 均结束轮询（2026-08-06）
  const startPolling = useCallback((taskId: string) => {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    const poll = async () => {
      try {
        const r = await fetch(`/api/translate-paragraphs/${taskId}`);
        if (!r.ok) throw new Error(`翻译状态获取失败 (HTTP ${r.status})`);
        const t = await r.json();
        setTranslationGroups(t.groups || []);
        setTranslateProgress({ done: t.done_count || 0, total: t.total || 0 });
        if (t.status === "done") {
          translateTaskIdRef.current = null;
          setAiResponseStatus(`翻译完成，${t.total} 组译文已展示在原文下方`);
          setIsTranslatingFull(false);
          setTimeout(() => setAiResponseStatus(""), 8000);
          return;
        }
        if (t.status === "failed") {
          translateTaskIdRef.current = null;
          setTranslateError(t.error || "翻译失败");
          setAiResponseStatus("翻译失败，已完成部分已保留，可重新点击继续");
          setIsTranslatingFull(false);
          return;
        }
        if (t.status === "cancelled") {
          translateTaskIdRef.current = null;
          setIsTranslatingFull(false);
          setTranslateProgress(null);
          setAiResponseStatus("翻译已取消，已完成部分已保留（可重新翻译继续）");
          setTimeout(() => setAiResponseStatus(""), 6000);
          return;
        }
        pollTimerRef.current = setTimeout(poll, 1500);
      } catch (err: any) {
        translateTaskIdRef.current = null;
        setTranslateError(String(err?.message || "轮询异常"));
        setAiResponseStatus(`翻译状态获取失败: ${err?.message || "请求异常"}`);
        setIsTranslatingFull(false);
      }
    };
    pollTimerRef.current = setTimeout(poll, 1500);
  }, []);

  // 2026-08-06 翻译暂存恢复：正文就绪后自动查询磁盘缓存/运行中任务（check_only 不启动新任务）
  // 完整缓存命中 → 直接恢复译文（刷新不丢）；有运行中任务 → 恢复轮询继续增量（刷新不中断）
  useEffect(() => {
    const text = content || "";
    if (!text.trim()) return;
    let cancelled = false;
    const autoRestore = async () => {
      try {
        const resp = await fetch("/api/translate-paragraphs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, target_lang: "zh", check_only: true }),
        });
        if (!resp.ok || cancelled) return;
        const data = await resp.json();
        if (cancelled || translationGroupsRef.current) return;
        if (data.status === "done" && data.cached && data.groups?.length) {
          setTranslationGroups(data.groups);
          setAiResponseStatus(`已恢复上次翻译（缓存命中），${data.groups.length} 组译文已展示在原文下方`);
          setTimeout(() => setAiResponseStatus(""), 6000);
        } else if (data.status === "running" && data.task_id) {
          // 上次翻译未完成：恢复任务轮询，译文继续增量出现
          setTranslationGroups(data.groups || []);
          setTranslateProgress({ done: data.done_count || 0, total: data.total || 0 });
          setIsTranslatingFull(true);
          translateTaskIdRef.current = data.task_id;
          setAiResponseStatus(`检测到未完成的翻译任务，已恢复（${data.done_count || 0}/${data.total}）`);
          setTimeout(() => setAiResponseStatus(""), 6000);
          startPolling(data.task_id);
        }
      } catch {
        // 恢复检查失败静默，不影响阅读
      }
    };
    autoRestore();
    return () => { cancelled = true; };
  }, [content, startPolling]);

  // 全文翻译：译文按组分对内联渲染在每段原文下方（2026-08-06，替代弹窗模式）
  // 已有译文时再次点击 = 重新翻译（force 忽略缓存）；翻译中按钮变为取消（handleCancelTranslate）
  const handleTranslateFull = async () => {
    const text = content || "";
    if (!text.trim()) {
      setAiResponseStatus("当前文档没有可翻译的正文");
      setTimeout(() => setAiResponseStatus(""), 3000);
      return;
    }
    if (isTranslatingFull) return;
    const hasExisting = !!translationGroups?.length;
    setIsTranslatingFull(true);
    setTranslateError(null);
    if (hasExisting) {
      // 重新翻译：清掉旧译文与进度，等待新任务骨架
      setTranslationGroups(null);
      setTranslateProgress(null);
    }
    try {
      setAiResponseStatus(hasExisting ? "正在重新翻译（忽略缓存）…" : "正在启动翻译任务…");
      const resp = await fetch("/api/translate-paragraphs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, target_lang: "zh", force: hasExisting }),
      });
      if (!resp.ok) throw new Error("翻译服务暂时不可用");
      const data = await resp.json();
      if (data.skipped) {
        setTranslationGroups(null);
        setAiResponseStatus("内容已是中文，无需翻译");
        setIsTranslatingFull(false);
        return;
      }
      if (data.status === "done") {
        // 缓存命中：直接展示全部译文
        setTranslationGroups(data.groups || []);
        setAiResponseStatus(`翻译完成（缓存命中），${(data.groups || []).length} 组译文已展示在原文下方`);
        setIsTranslatingFull(false);
        setTimeout(() => setAiResponseStatus(""), 8000);
        return;
      }
      // 任务运行中：先填充原文骨架，译文逐组增量出现
      translateTaskIdRef.current = data.task_id;
      if (data.groups && data.groups.length) setTranslationGroups(data.groups);
      setTranslateProgress({ done: data.done_count || 0, total: data.total || 0 });
      startPolling(data.task_id);
    } catch (err: any) {
      setAiResponseStatus(`翻译失败: ${err.message || "请求异常"}`);
      setIsTranslatingFull(false);
      setTimeout(() => setAiResponseStatus(""), 5000);
    }
  };

  // 取消翻译：通知后端停止任务（已完成组保留为部分缓存可续传），停止轮询
  const handleCancelTranslate = async () => {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    const tid = translateTaskIdRef.current;
    translateTaskIdRef.current = null;
    if (tid) {
      try {
        await fetch(`/api/translate-paragraphs/${tid}/cancel`, { method: "POST" });
      } catch { /* 后端不可达也不阻塞本地复位 */ }
    }
    setIsTranslatingFull(false);
    setTranslateProgress(null);
    setTranslateError(null);
    setTranslationGroups(null);  // 清掉重翻时的空骨架，按钮回到“翻译全文”
    setAiResponseStatus("翻译已取消，已完成部分已保留（可重新翻译继续）");
    setTimeout(() => setAiResponseStatus(""), 6000);
  };

  // 译文内联渲染：原文与译文按组配对，译文显示在对应原文下方（2026-08-06）
  const renderTranslatedGroups = () => {
    const pairs = translationGroups || [];
    // 优先用后端返回的 src（与后端分组规则完全一致），兜底前端再切一次
    const srcGroups = pairs.length && pairs[0].src ? pairs.map(p => p.src) : splitParagraphGroups(deferredContent);
    // 原文去缩进：PDF 提取文本带大量前导空格，markdown 会把 4 空格缩进渲染成代码块（黑底框）
    const dedent = (s: string) => s.replace(/^[ \t]{2,}/gm, "");
    // 译文硬换行：单个 \n 转 markdown 硬换行（行尾双空格），保留段落结构
    const hardBreaks = (s: string) => s.replace(/\s*\n/g, "  \n");
    // 段落切分：组内按 \n\n 拆段，用于逐段配对展示
    const splitParas = (s: string) => s.split(/\n{2,}/).map(x => x.trim()).filter(Boolean);
    const nodes: React.ReactNode[] = [];
    const pairCount = Math.min(srcGroups.length, pairs.length);
    const renderUpTo = Math.min(pairRenderCount, pairCount);
    const tgtCls = `mt-1.5 mb-4 pl-3 border-l-2 py-2 pr-2 rounded-r-lg text-[13px] leading-relaxed select-text ${
      readerBg === "dark"
        ? "border-sky-700 bg-zinc-800/60 text-zinc-300"
        : readerBg === "sepia"
          ? "border-sky-300 bg-amber-100/60 text-amber-800"
          : "border-sky-200 bg-sky-50/60 text-zinc-600"
    }`;
    for (let i = 0; i < renderUpTo; i++) {
      const pair = pairs[i];
      const srcText = dedent(srcGroups[i]);
      const hasTgt = !!(pair && pair.tgt && !pair.skipped);
      // 逐段配对：组内原文/译文都按段落切分，段数对上（且>1）则每段原文下方紧跟该段译文；
      // 对不上（LLM 合并/拆段）兜底整组展示
      const srcParas = splitParas(srcText);
      const tgtParas = hasTgt ? splitParas(pair!.tgt) : [];
      const aligned = hasTgt && srcParas.length > 1 && srcParas.length === tgtParas.length;
      if (aligned) {
        srcParas.forEach((sp, pi) => {
          nodes.push(
            <MarkdownBlock
              key={`src-${i}-${pi}`}
              text={sp}
              highlightCheckRegex={highlightRegexCheck}
              renderHighlights={renderWithHighlights}
              highlightsLen={document?.highlights?.length || 0}
            />
          );
          nodes.push(
            <div key={`tgt-${i}-${pi}`} className={tgtCls}>
              <MarkdownBlock
                text={hardBreaks(tgtParas[pi])}
                highlightCheckRegex={highlightRegexCheck}
                renderHighlights={renderWithHighlights}
                highlightsLen={document?.highlights?.length || 0}
              />
            </div>
          );
        });
        continue;
      }
      nodes.push(
        <MarkdownBlock
          key={`src-${i}`}
          text={srcText}
          highlightCheckRegex={highlightRegexCheck}
          renderHighlights={renderWithHighlights}
          highlightsLen={document?.highlights?.length || 0}
        />
      );
      if (hasTgt) {
        nodes.push(
          <div key={`tgt-${i}`} className={tgtCls}>
            <MarkdownBlock
              text={hardBreaks(pair!.tgt)}
              highlightCheckRegex={highlightRegexCheck}
              renderHighlights={renderWithHighlights}
              highlightsLen={document?.highlights?.length || 0}
            />
          </div>
        );
      } else if (pair && !pair.skipped && isTranslatingFull) {
        // 增量模式：该组翻译未完成，显示占位
        nodes.push(
          <div key={`tgt-${i}`} className={`${tgtCls} opacity-50 italic`}>翻译中…</div>
        );
      }
    }
    // 组数不匹配时，多余译文兜底渲染在末尾
    for (let i = pairCount; i < Math.min(pairs.length, renderUpTo + (pairs.length - pairCount)); i++) {
      const pair = pairs[i];
      if (pair && pair.tgt && !pair.skipped) {
        nodes.push(
          <div key={`tgt-extra-${i}`} className={tgtCls}>
            <MarkdownBlock
              text={hardBreaks(pair.tgt)}
              highlightCheckRegex={highlightRegexCheck}
              renderHighlights={renderWithHighlights}
              highlightsLen={document?.highlights?.length || 0}
            />
          </div>
        );
      }
    }
    // 渐进渲染未完成：显示进度提示
    if (renderUpTo < pairCount) {
      nodes.push(
        <div key="pair-loading" className="py-6 text-center text-[10px] text-zinc-400 italic flex items-center justify-center gap-2">
          <Loader2 className="w-3 h-3 animate-spin" />
          渲染中… {Math.round(renderUpTo / pairCount * 100)}%
        </div>
      );
    }
    return <>{nodes}</>;
  };

  if (!document) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8 text-center bg-zinc-50 text-zinc-400 rounded-xl border border-zinc-200 border-dashed select-none animate-fadeIn">
        <span className="p-3 bg-white border border-zinc-200 text-zinc-400 rounded-full mb-3.5 shadow-sm">
          <FileText className="w-6 h-6 animate-pulse" />
        </span>
        <h3 className="text-zinc-700 font-bold text-sm">未选择任何文档</h3>
        <p className="text-xs text-zinc-400 max-w-sm mt-1 leading-relaxed">
          请在左侧侧边栏中选择一个存储的知识文档进行阅读与编辑，或者立即触发底部的 AI 实体映射网络，完成全自动思维脑图建立。
        </p>
      </div>
    );
  }

  const handleSelectionChange = () => {
    const sel = window.getSelection();
    if (sel) {
      const txt = sel.toString().trim();
      if (txt && txt.length >= 2 && txt.length <= 150) {
        setSelectedText(txt);
      }
    }
  };

  const handleSave = () => {
    onUpdateDocument(document.id, {
      title,
      content,
      updatedAt: new Date().toISOString()
    });
    setAiResponseStatus("文档修改已安全保存。");
    setTimeout(() => setAiResponseStatus(""), 3000);
  };

  // Add highlight and comment annotation
  const handleAddHighlight = (color: 'yellow' | 'green' | 'pink' | 'blue') => {
    if (!selectedText.trim()) return;
    const newHighlight: Highlight = {
      id: `hl-${Date.now()}`,
      text: selectedText,
      color,
      comment: noteComment.trim() || undefined,
      createdAt: new Date().toISOString()
    };

    const existingHighlights = document.highlights || [];
    const updated = [newHighlight, ...existingHighlights];
    onUpdateDocument(document.id, { highlights: updated });

    setSelectedText("");
    setNoteComment("");
    setAiResponseStatus("划词批注保存成功！已完美记入读书笔记。");
    setTimeout(() => setAiResponseStatus(""), 3000);
  };

  // Edit highlight comment
  const handleUpdateComment = (hlId: string) => {
    const existingHighlights = document.highlights || [];
    const updated = existingHighlights.map(hl => 
      hl.id === hlId ? { ...hl, comment: editingCommentText.trim() || undefined } : hl
    );
    onUpdateDocument(document.id, { highlights: updated });
    setEditingHighlightId(null);
    setEditingCommentText("");
    setAiResponseStatus("批注备注已保存。");
    setTimeout(() => setAiResponseStatus(""), 3000);
  };

  // Delete highlight color / annotation
  const handleDeleteHighlight = (hlId: string) => {
    const existingHighlights = document.highlights || [];
    const updated = existingHighlights.filter(hl => hl.id !== hlId);
    onUpdateDocument(document.id, { highlights: updated });
  };

  // Export reading notes / annotations as styled Markdown
  const handleExportNotes = () => {
    const activeHighlights = document.highlights || [];
    if (activeHighlights.length === 0) {
      alert("本文档当前暂无标注笔记或划词批注。您可以双击或划选预览文本中任意字符开始。");
      return;
    }

    let md = `# 知识大纲与批注读书笔记 -《${title}》\n`;
    md += `- **文献名称**: ${title}\n`;
    md += `- **导出时间**: ${new Date().toLocaleString()}\n`;
    md += `- **标注笔记处**: ${activeHighlights.length} 处\n\n`;
    md += `--- \n\n`;

    activeHighlights.forEach((hl, i) => {
      const colorChinese = { yellow: "核心词汇", green: "理论论证", pink: "重点思考", blue: "拓展见解" };
      md += `### 笔记 #${activeHighlights.length - i} - [${colorChinese[hl.color] || "标注"}]\n`;
      md += `> **划词原文**: ${hl.text}\n\n`;
      if (hl.comment) {
        md += `**我的备注**: \n${hl.comment}\n\n`;
      }
      md += `*记录于 ${new Date(hl.createdAt).toLocaleString()}*\n\n`;
      md += `--- \n\n`;
    });

    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    link.href = url;
    link.download = `${title}-阅读批注笔记.md`;
    link.click();
    URL.revokeObjectURL(url);
  };

  // 1. Core summarizer AI action
  const handleAISummarize = async () => {
    setIsSummarizing(true);
    setAiResponseStatus("正在生成摘要...");
    try {
      const resp = await fetch("/api/ai/summarize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, content })
      });
      if (!resp.ok) throw new Error("大模型在概括此篇文档时失去联络。");
      const data = await resp.json();
      
      onUpdateDocument(document.id, {
        summary: data.summary,
        updatedAt: new Date().toISOString()
      });
      setAiResponseStatus("摘要生成完成");
    } catch (err: any) {
      console.error(err);
      setAiResponseStatus(`错误: ${err.message || "请求失败"}`);
    } finally {
      setIsSummarizing(false);
      setTimeout(() => setAiResponseStatus(""), 4000);
    }
  };

  // 2. Automated mind map entity relations extractor
  const handleAIExtractEntities = async () => {
    setIsExtracting(true);
    setAiResponseStatus("正在提取文本结构信息、核心事物与知识实体并绘制知识网络图谱...");
    try {
      const resp = await fetch("/api/gemini/extract-entities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content })
      });
      if (!resp.ok) throw new Error("无法成功解析知识图谱，网络接口失败。");
      const data = await resp.json();

      if (data.nodes && Array.isArray(data.nodes)) {
        onCreateExternalGraph(data.nodes, data.edges || [], document.id);
        onUpdateDocument(document.id, {
          entitiesProcessed: true,
          updatedAt: new Date().toISOString()
        });
        setAiResponseStatus(`知识图谱构建成功！已快速加载并在画布注入 ${data.nodes.length} 个核心热点。`);
      } else {
        throw new Error("模型反馈空缺结构，请尝试增加正文或润色文章深度。");
      }
    } catch (err: any) {
      console.error(err);
      setAiResponseStatus(`错误: ${err.message || "未能读取大语言模型反馈数据"}`);
    } finally {
      setIsExtracting(false);
      setTimeout(() => setAiResponseStatus(""), 4000);
    }
  };

  // 3. Smart paragraph/sentence segment interpreter
  const handleAIInterpret = async (mode: "simple" | "deep") => {
    if (!interpretText.trim()) return;
    setIsInterpreting(true);
    setInterpretingMode(mode);
    setInterpretationResult("");
    setAiResponseStatus(mode === "deep" ? "正在进行深度分析..." : "正在进行简明解读...");
    try {
      const resp = await fetch("/api/ai/interpret", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: interpretText, mode })
      });
      if (!resp.ok) throw new Error("大模型在解读选中内容时失去联络。");
      const data = await resp.json();
      setInterpretationResult(data.interpretation);
      setAiResponseStatus("分析完成");
    } catch (err: any) {
      console.error(err);
      setAiResponseStatus(`解读失败: ${err.message || "请求异常"}`);
    } finally {
      setIsInterpreting(false);
      setTimeout(() => setAiResponseStatus(""), 4000);
    }
  };

  // Inline highlights renderer within standard ReactMarkdown text strings
  const renderTextWithHighlights = React.useCallback((rawText: string) => {
    if (!highlightRegex || !highlightRegexCheck || !document?.highlights) return rawText;
    
    // Quick early-out with optimized non-capturing regex search
    if (!highlightRegexCheck.test(rawText)) {
      return rawText;
    }
    
    const activeHighlights = document.highlights;
    const parts = rawText.split(highlightRegex);
    
    return parts.map((part, idx) => {
      if (idx % 2 === 1) {
        // Strip all whitespace for a 100% robust string comparison (avoids newline/space mismatch in Chinese & English)
        const partNormalized = part.replace(/\s+/g, '').toLowerCase();
        const foundHl = activeHighlights.find(h => {
          const hlNormalized = h.text.replace(/\s+/g, '').toLowerCase();
          return hlNormalized === partNormalized;
        });
        
        if (foundHl) {
          const colorStyles = {
            yellow: { backgroundColor: '#fef08a', color: '#1c1917', borderBottom: '2px solid #ca8a04' },
            green: { backgroundColor: '#bbf7d0', color: '#14532d', borderBottom: '2px solid #16a34a' },
            pink: { backgroundColor: '#fbcfe8', color: '#500725', borderBottom: '2px solid #db2777' },
            blue: { backgroundColor: '#cffafe', color: '#083344', borderBottom: '2px solid #0891b2' }
          };
          const style = colorStyles[foundHl.color] || colorStyles.yellow;
          
          return (
            <mark 
              key={idx} 
              style={{
                ...style,
                padding: '2px 4px',
                borderRadius: '3px',
                fontWeight: '500',
              }}
              className="cursor-help transition-all duration-150 inline relative group select-text" 
              title={foundHl.comment ? `备注: ${foundHl.comment}` : "高亮批注"}
            >
              {part}
              {foundHl.comment && (
                <span className="absolute z-50 left-1/2 -translate-x-1/2 bottom-full mb-2 hidden group-hover:block w-48 p-2 bg-zinc-900 text-white text-[10px] rounded-md shadow-lg font-sans text-left leading-normal">
                  💭 {foundHl.comment}
                </span>
              )}
            </mark>
          );
        }
      }
      return part;
    });
  }, [highlightRegex, highlightRegexCheck, document?.highlights]);

  const renderWithHighlights = React.useCallback((node: React.ReactNode): React.ReactNode => {
    if (typeof node === "string") {
      return renderTextWithHighlights(node);
    }
    if (Array.isArray(node)) {
      return node.map((child, i) => <React.Fragment key={i}>{renderWithHighlights(child)}</React.Fragment>);
    }
    if (React.isValidElement(node)) {
      const element = node as React.ReactElement<any>;
      if (element.props && element.props.children) {
        const nodeType = element.type as any;
        if (nodeType === 'code' || nodeType === 'a' || nodeType === 'pre') {
          return element;
        }
        return React.cloneElement(element, {
          ...element.props,
          children: renderWithHighlights(element.props.children)
        });
      }
    }
    return node;
  }, [renderTextWithHighlights]);

  return (
    <div className="flex flex-col bg-white border border-zinc-200 rounded-xl overflow-hidden h-full shadow-sm animate-fadeIn">
      {/* T9：工具栏面板（标题编辑 + 侧窗开关 + API 状态横幅）提取为独立组件 */}
      <DocToolBar
        title={title}
        onTitleChange={(value) => {
          setTitle(value);
          onUpdateDocument(document.id, { title: value });
        }}
        showSidebar={showSidebar}
        onToggleSidebar={() => setShowSidebar(prev => !prev)}
        aiResponseStatus={aiResponseStatus}
      />

      {/* Main Layout Content Panel (Widescreen Single Flow) */}
      <div ref={splitContainerRef} className="flex-1 flex overflow-hidden min-h-0 relative">
        
        {/* A. DESIGN-CRAFTED CORE DOCUMENT PREVIEW CANVAS (Fullscreen Layout) */}
        <div className="flex-1 flex flex-col h-full overflow-hidden bg-zinc-50/30 relative">
          
          {/* Symmetrical Top linear Reading Progress Track Indicator */}
          <div className="sticky top-0 left-0 right-0 z-30 bg-white/95 border-b border-zinc-200/80 px-4 py-2 flex items-center justify-between text-[10px] text-zinc-400 font-mono backdrop-blur-md select-none">
            {/* 左侧：PDF + 字体 + 背景 */}
            <div className="flex items-center gap-3">
              {hasPdfFile && (
                <button onClick={() => setShowPdfViewer(true)}
                  className="text-[10px] font-sans font-bold text-rose-600 hover:text-rose-700 bg-rose-50 hover:bg-rose-100 px-2 py-0.5 rounded border border-rose-200 shrink-0 flex items-center gap-1">
                  <FileText className="w-2.5 h-2.5" /> PDF预览
                </button>
              )}

              {/* 翻译全文：译文直接内联在对应原文下方（2026-08-06，替代弹窗模式）
                  翻译中 → 取消按钮；已有译文 → 重新翻译（force 忽略缓存） */}
              {isTranslatingFull ? (
                <button
                  onClick={handleCancelTranslate}
                  className="text-[10px] font-sans font-bold text-rose-600 hover:text-rose-700 bg-rose-50 hover:bg-rose-100 px-2 py-0.5 rounded border border-rose-200 shrink-0 flex items-center gap-1"
                  title="取消翻译，已完成部分保留（可续传）"
                >
                  <X className="w-2.5 h-2.5" /> {translateProgress ? `取消翻译 ${translateProgress.done}/${translateProgress.total}` : "取消翻译"}
                </button>
              ) : (
                <button
                  onClick={handleTranslateFull}
                  className="text-[10px] font-sans font-bold text-sky-600 hover:text-sky-700 bg-sky-50 hover:bg-sky-100 px-2 py-0.5 rounded border border-sky-200 shrink-0 flex items-center gap-1"
                  title={translateError || (translationGroups?.length ? "重新翻译全文（忽略缓存，更新译文）" : "将全文翻译为中文，译文展示在每段原文下方")}
                >
                  <Languages className="w-2.5 h-2.5" /> {translationGroups?.length ? "重新翻译" : "翻译全文"}
                </button>
              )}

              <span className="w-px h-3 bg-zinc-200 shrink-0" />

              <span className="text-[10px] text-zinc-400 shrink-0">字号</span>
              <button onClick={() => updateReaderFontSize(-1)} className="text-[10px] font-bold text-zinc-400 hover:text-zinc-600 w-5 h-5 rounded border border-zinc-200 flex items-center justify-center shrink-0">−</button>
              <span className="text-[10px] text-zinc-500 w-4 text-center shrink-0">{readerFontSizeDisplay}</span>
              <button onClick={() => updateReaderFontSize(1)} className="text-[10px] font-bold text-zinc-400 hover:text-zinc-600 w-5 h-5 rounded border border-zinc-200 flex items-center justify-center shrink-0">+</button>

              <span className="w-px h-3 bg-zinc-200 shrink-0" />

              <div onClick={() => setReaderBg("light")} className={`w-3.5 h-3.5 rounded-full border cursor-pointer shrink-0 ${readerBg === "light" ? "ring-1 ring-emerald-400" : "border-zinc-300"} bg-white`} title="白底" />
              <div onClick={() => setReaderBg("sepia")} className={`w-3.5 h-3.5 rounded-full border cursor-pointer shrink-0 ${readerBg === "sepia" ? "ring-1 ring-emerald-400" : "border-zinc-300"}`} style={{background:"#f5e6c8"}} title="暖黄" />
              <div onClick={() => setReaderBg("dark")} className={`w-3.5 h-3.5 rounded-full border cursor-pointer shrink-0 ${readerBg === "dark" ? "ring-1 ring-emerald-400" : "border-zinc-300"}`} style={{background:"#2d2d2d"}} title="深色" />
            </div>

            {/* 右侧：进度 + 书签 */}
            <div className="flex items-center gap-2">
              <span className="text-zinc-400 shrink-0"><strong className="text-emerald-600" ref={progressTextRef}>{readingProgress}%</strong></span>
              <div className="w-12 h-1.5 bg-zinc-200 rounded-full overflow-hidden shrink-0">
                <div ref={progressBarRef} className="h-full bg-emerald-500 rounded-full transition-all duration-300" style={{ width: `${readingProgress}%` }} />
              </div>
              <button type="button" onClick={handleAddBookmark}
                className="text-zinc-400 hover:text-emerald-600 hover:bg-emerald-50 p-1 rounded cursor-pointer shrink-0"
                title="保存书签"><Bookmark className="w-3.5 h-3.5" /></button>
            </div>
          </div>

          {/* Preview scroll body */}
              <div 
                ref={previewContainerRef}
                onScroll={handleScrollProgress}
                className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-5 h-full scroll-smooth"
                style={{
                  fontSize: `${readerFontSizeDisplay}px`,
                  backgroundColor: readerBg === "sepia" ? "#f5e6c8" : readerBg === "dark" ? "#1e1e1e" : "#ffffff",
                }}
              >
                
                {/* ✍️ Floating Annotation popover when user has highlighted text */}
                {selectedText && (
                  <div className="bg-white border border-zinc-200 rounded-xl p-4 shadow-md text-xs animate-fadeIn space-y-3 block select-none sticky top-2 z-40">
                    <div className="flex items-center justify-between pb-2 border-b border-zinc-100">
                      <div className="flex items-center gap-1.5">
                        <span className="px-1.5 py-0.5 bg-emerald-600 text-white rounded text-[9px] font-extrabold font-mono">划词</span>
                        <span className="text-zinc-700 font-bold">新建阅读批注 & 笔记</span>
                      </div>
                      <button 
                        onClick={() => setSelectedText("")}
                        className="text-zinc-400 hover:text-zinc-600 font-bold"
                      >
                        ✕
                      </button>
                    </div>

                    <div className="bg-zinc-50 rounded p-2 border border-zinc-150 text-zinc-700 font-mono italic max-h-16 overflow-y-auto custom-scrollbar">
                      "{selectedText}"
                    </div>

                    <div className="space-y-2">
                      <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">选择荧光笔分类颜色</span>
                      <div className="flex gap-2.5">
                        <button
                          onClick={() => setHighlightColor('yellow')}
                          className={`w-6 h-6 rounded-full bg-amber-300 border-2 transition-all ${highlightColor === 'yellow' ? 'border-amber-500 scale-110' : 'border-neutral-200 hover:scale-105'}`}
                          title="核心概念 (黄色)"
                        />
                        <button
                          onClick={() => setHighlightColor('green')}
                          className={`w-6 h-6 rounded-full bg-green-300 border-2 transition-all ${highlightColor === 'green' ? 'border-green-500 scale-110' : 'border-neutral-200 hover:scale-105'}`}
                          title="理论论点 (绿色)"
                        />
                        <button
                          onClick={() => setHighlightColor('pink')}
                          className={`w-6 h-6 rounded-full bg-rose-300 border-2 transition-all ${highlightColor === 'pink' ? 'border-rose-500 scale-110' : 'border-neutral-200 hover:scale-105'}`}
                          title="疑惑/思考 (粉色)"
                        />
                        <button
                          onClick={() => setHighlightColor('blue')}
                          className={`w-6 h-6 rounded-full bg-cyan-300 border-2 transition-all ${highlightColor === 'blue' ? 'border-cyan-500 scale-110' : 'border-neutral-200 hover:scale-105'}`}
                          title="拓展背景 (蓝色)"
                        />
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">书写我的批注/简评备注</span>
                      <textarea
                        value={noteComment}
                        onChange={(e) => setNoteComment(e.target.value)}
                        placeholder="在此输入当前文献标注下的个人体会/备注思考..."
                        className="w-full text-xs p-2 border border-zinc-200 rounded-lg h-14 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                      />
                    </div>

                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => setSelectedText("")}
                        className="px-3 py-1.5 bg-zinc-50 hover:bg-zinc-100 border border-zinc-200 text-zinc-650 rounded-lg text-[11px] font-bold"
                      >
                        取消
                      </button>
                      <button
                        onClick={() => handleAddHighlight(highlightColor)}
                        className="px-4 py-1.5 bg-zinc-900 hover:bg-zinc-850 text-white rounded-lg text-[11px] font-bold shadow-xs flex items-center gap-1"
                      >
                        <Plus className="w-3 h-3 text-emerald-400" />
                        添加笔记标注
                      </button>
                    </div>
                  </div>
                )}

                {/* Primary rendered paper body with rich styling */}
                <div className="bg-white border border-zinc-200/60 p-6 md:p-10 rounded-2xl shadow-sm select-text text-left max-w-3xl mx-auto min-h-full">
                  <header className="border-b border-zinc-100 pb-4 mb-5">
                    <h1 className="text-zinc-900 font-extrabold text-lg leading-snug tracking-tight mb-2">
                      {title || "无标题文档"}
                    </h1>
                    <div className="flex flex-col gap-0.5 text-[10px] text-zinc-400">
                      <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(document.updatedAt).toLocaleDateString()}</span>
                      <span className="flex items-center gap-1"><BookOpen className="w-3 h-3" />{content.length.toLocaleString()} 字</span>
                    </div>

                    {headings.length > 0 && (
                      <div className="mt-3 p-2 bg-emerald-50/50 border border-emerald-100/70 rounded-lg flex items-center justify-between text-[11px] animate-fadeIn select-none">
                        <span className="flex items-center gap-1.5 text-emerald-700 font-medium">
                          <BookOpen className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
                          共 {headings.length} 个章节
                        </span>
                        <button type="button" onClick={() => handleScrollToHeading(headings[0].id, headings[0].text)}
                          className="px-2.5 py-1 bg-zinc-900 hover:bg-zinc-800 font-bold text-white rounded text-[10px] flex items-center gap-0.5 cursor-pointer transition-all">
                          跳至正文 <ChevronRight className="w-3 h-3" />
                        </button>
                      </div>
                    )}
                  </header>

                  {/* Rendered markdown core display body */}
                  <div 
                    id="rendered-preview-document-container"
                    onMouseUp={handleSelectionChange}
                    style={{ contentVisibility: "auto", containIntrinsicSize: "0 500px", zoom: `var(--reader-zoom, 1)`, color: readerBg === "dark" ? "#d4d4d4" : readerBg === "sepia" ? "#5c3d1a" : undefined }}
                    className={`prose prose-neutral max-w-none focus:outline-none ${readerBg === "dark" ? "text-zinc-200" : readerBg === "sepia" ? "text-amber-900" : "text-zinc-800"}`}
                  >
                    {content ? (
                      <>
                        {translateError && (
                          <div className="mb-3 px-3 py-2 text-[11px] text-red-500 bg-red-50 border border-red-200 rounded-lg select-text">
                            翻译失败：{translateError}（已完成部分已保留，可重新点击翻译继续）
                          </div>
                        )}
                        {isContentStale && (
                          <div className="text-zinc-400 italic py-10 text-center text-xs flex items-center justify-center gap-2 animate-pulse">
                            <Loader2 className="w-3 h-3 animate-spin" /> 渲染正文中…
                          </div>
                        )}
                        <div style={{ display: isContentStale ? 'none' : 'block' }}>
                          {contentChunks && !(translationGroups && translationGroups.length > 0) ? (
                            <>
                              {/* 超长文档：分块渲染，每块独立 ReactMarkdown */}
                              {contentChunks.slice(0, visibleChunkCount).map((chunk, idx) => (
                                <MarkdownBlock
                                  key={`chunk-${idx}`}
                                  text={chunk}
                                  highlightCheckRegex={highlightRegexCheck}
                                  renderHighlights={renderWithHighlights}
                                  highlightsLen={document?.highlights?.length || 0}
                                />
                              ))}
                              {isChunkRendering && (
                                <div className="py-6 text-center text-[10px] text-zinc-400 italic flex items-center justify-center gap-2">
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                  长文渲染中… {Math.round(visibleChunkCount / contentChunks!.length * 100)}%
                                </div>
                              )}
                            </>
                          ) : (
                            // 有译文时（含超长文档）：原文与译文按组配对渲染，译文内联在对应原文下方
                            translationGroups && translationGroups.length > 0 ? (
                              renderTranslatedGroups()
                            ) : (
                              <MarkdownBlock
                                text={deferredContent}
                                highlightCheckRegex={highlightRegexCheck}
                                renderHighlights={renderWithHighlights}
                                highlightsLen={document?.highlights?.length || 0}
                              />
                            )
                          )}
                        </div>
                      </>
                    ) : contentLoading ? (
                      <div className="text-zinc-400 italic py-10 text-center text-xs flex items-center justify-center gap-2">
                        <Loader2 className="w-3 h-3 animate-spin" /> 加载正文中…
                      </div>
                    ) : (
                      <div className="text-zinc-400 italic py-10 text-center text-xs">
                        当前正文为空。
                      </div>
                    )}
                  </div>
                </div>

              </div>
            </div>

        {/* C. INTUITIVE WORKSPACE UTILITY SIDEBAR (ToC, Comments Timeline, AI Brains) */}
        {showSidebar && (
          <>
            {/* Right sidebar resize divider */}
            <div
              className="w-px hover:w-1 active:w-1 h-full cursor-col-resize bg-zinc-200 hover:bg-zinc-400 active:bg-zinc-500 transition-all z-20 select-none shrink-0"
              title="拖拽调整分析工具栏宽度"
              onMouseDown={(e) => {
                e.preventDefault();
                window.document.body.style.cursor = "col-resize";
                window.document.body.style.userSelect = "none";
                const startX = e.clientX;
                const startWidth = rightSidebarWidth;
                let rafId: number | null = null;
                let latestClientX = startX;

                const handleMouseMove = (moveEvent: MouseEvent) => {
                  latestClientX = moveEvent.clientX;
                  if (rafId === null) {
                    rafId = requestAnimationFrame(() => {
                      rafId = null;
                      const newWidth = startWidth - (latestClientX - startX);
                      setRightSidebarWidth(Math.max(180, Math.min(newWidth, 485)));
                    });
                  }
                };

                const handleMouseUp = () => {
                  window.removeEventListener("mousemove", handleMouseMove);
                  window.removeEventListener("mouseup", handleMouseUp);
                  if (rafId !== null) cancelAnimationFrame(rafId);
                  window.document.body.style.cursor = "";
                  window.document.body.style.userSelect = "";
                };

                window.addEventListener("mousemove", handleMouseMove);
                window.addEventListener("mouseup", handleMouseUp);
              }}
            />

            <div 
              style={{ width: `${rightSidebarWidth}px`, willChange: 'width' }} 
              className="border-l border-zinc-200 bg-white h-full flex flex-col shrink-0 overflow-hidden relative select-none animate-fadeIn"
            >
            
            {/* Sidebar headers */}
            <div className="px-3.5 py-3 border-b border-zinc-150 bg-zinc-50/25 shrink-0 flex items-center justify-between">
              <span className="text-[10px] uppercase font-bold tracking-widest text-zinc-500 font-mono">
                分析工作站
              </span>
              <button
            onClick={() => setShowSidebar(!showSidebar)}
                className="text-zinc-400 hover:text-zinc-650 text-xs px-1 hover:bg-zinc-100 rounded transition-all cursor-pointer font-bold"
              >
                ✕
              </button>
            </div>

            {/* Premium segmented control tabs */}
            <div className="px-2 py-2 border-b border-zinc-150 shrink-0 grid grid-cols-3 gap-1 bg-white">
              <button
                onClick={() => setSidebarTab("outline")}
                className={`py-1.5 rounded-lg text-[10px] font-bold tracking-wider transition-all flex items-center justify-center gap-1 ${
                  sidebarTab === "outline"
                    ? "bg-zinc-900 text-white shadow-xs"
                    : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800"
                }`}
              >
                大纲目录
              </button>
              <button
                onClick={() => setSidebarTab("notes")}
                className={`py-1.5 rounded-lg text-[10px] font-bold tracking-wider transition-all flex items-center justify-center gap-1 relative ${
                  sidebarTab === "notes"
                    ? "bg-zinc-900 text-white shadow-xs"
                    : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800"
                }`}
              >
                <span>我的批注</span>
                {document.highlights && document.highlights.length > 0 && (
                  <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-emerald-500 text-white text-[8px] font-mono flex items-center justify-center font-bold scale-90 border border-white shrink-0">
                    {document.highlights.length}
                  </span>
                )}
              </button>
              <button
                onClick={() => setSidebarTab("ai")}
                className={`py-1.5 rounded-lg text-[10px] font-bold tracking-wider transition-all flex items-center justify-center gap-1 ${
                  sidebarTab === "ai"
                    ? "bg-zinc-900 text-white shadow-xs"
                    : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800"
                }`}
              >
                <Sparkles className="w-2.5 h-2.5 text-amber-400 animate-pulse" />
                AI 大脑
              </button>
            </div>

            {/* Tab scroll content workspace */}
            <div className="flex-1 overflow-y-auto p-3.5 space-y-4">
              
              {/* TAB 1: OUTLINE STRUCTURE LIST */}
              {sidebarTab === "outline" && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Bookmark lists portion */}
                  <div className="space-y-2 border-b border-zinc-150 pb-3">
                    <div className="flex items-center justify-between text-[10.5px] font-bold text-zinc-500 uppercase tracking-wide">
                      <span className="flex items-center gap-1">
                        <Bookmark className="w-3.5 h-3.5 text-emerald-600 fill-emerald-600/10" />
                        我的置信书签 ({document.bookmarks?.length || 0})
                      </span>
                    </div>
                    
                    {!document.bookmarks || document.bookmarks.length === 0 ? (
                      <div className="text-[10px] text-zinc-400 italic py-3 text-center border border-dashed border-zinc-250 rounded-lg">
                        当前尚无保存的书签。
                        <button
                          type="button"
                          onClick={handleAddBookmark}
                          className="text-emerald-600 hover:text-emerald-700 font-bold ml-1 hover:underline cursor-pointer"
                        >
                          立即记录当前位置
                        </button>
                      </div>
                    ) : (
                      <div className="space-y-1.5 max-h-40 overflow-y-auto pr-0.5 custom-scrollbar">
                        {document.bookmarks.map((bm) => (
                          <div 
                            key={bm.id}
                            className="group flex items-center justify-between gap-1.5 p-1.5 px-2 bg-emerald-50/40 border border-emerald-100/60 rounded-md hover:bg-emerald-50 hover:border-emerald-250 transition-all cursor-pointer animate-fadeIn"
                          >
                            <div 
                              onClick={() => handleScrollToBookmark(bm.scrollTop)}
                              className="flex-1 min-w-0 flex items-center gap-1.5 text-left"
                              title="点击一键还原到该书签阅读位置"
                            >
                              <span className="px-1 py-0.5 rounded bg-emerald-100 text-[8.5px] font-mono font-black text-emerald-700 shrink-0">
                                {bm.ratio}%
                              </span>
                              <span className="text-zinc-700 text-[10.5px] font-medium truncate">
                                {bm.label}
                              </span>
                            </div>
                            
                            <button
                              type="button"
                              onClick={() => handleDeleteBookmark(bm.id)}
                              className="text-zinc-400 hover:text-rose-605 p-0.5 rounded hover:bg-white flex items-center justify-center shrink-0 cursor-pointer md:opacity-0 md:group-hover:opacity-100 transition-opacity"
                              title="删除此书签"
                            >
                              <Trash className="w-2.5 h-2.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Document Chapter outlines */}
                  <div className="space-y-2 w-full flex flex-col flex-1 min-h-0">
                    <div className="text-[10.5px] font-bold text-zinc-500 uppercase tracking-wide flex items-center gap-1.5 pb-1">
                      <BookOpen className="w-3.5 h-3.5 text-zinc-400" />
                      文献章节目录 ({headings.length})
                    </div>

                    {/* 搜索/过滤框 - 通用优化：目录多时不必滚动翻找 */}
                    {headings.length > 5 && (
                      <input
                        type="text"
                        value={tocSearch || ""}
                        onChange={e => setTocSearch(e.target.value)}
                        placeholder="🔍 搜索章节..."
                        className="w-full text-[10.5px] px-2 py-1 border border-zinc-200 rounded focus:outline-none focus:border-emerald-400 bg-zinc-50/50 placeholder-zinc-400"
                      />
                    )}
                    
                    <div ref={tocListRef} className="space-y-0.5 w-full flex-1 overflow-y-auto pr-1 custom-scrollbar min-h-0">
                      {headings.length === 0 ? (
                        <div className="text-[10px] text-zinc-400 italic text-center py-4 bg-zinc-50 rounded-lg">
                          未提取到章节目录。正文在书写完多级章节或带有 “第一章”、“1.1” 等标记后将自动生成大纲。
                        </div>
                      ) : (() => {
                        // 搜索过滤
                        const filtered = tocSearch
                          ? headings.filter(h => h.text.toLowerCase().includes(tocSearch.toLowerCase()))
                          : headings;
                        if (filtered.length === 0) {
                          return (
                            <div className="text-[10px] text-zinc-400 italic text-center py-4 bg-zinc-50 rounded-lg">
                              没有匹配 “{tocSearch}” 的章节
                            </div>
                          );
                        }
                        return filtered.map((h, i) => {
                          const isActive = h.id === activeHeadingId;
                          const isLevel0 = h.level === 0;
                          const isLevel1 = h.level === 1;
                          const isLevel2 = h.level === 2;
                          const paddingLeft = isLevel0 ? "pl-0.5" : isLevel1 ? "pl-2" : isLevel2 ? "pl-5" : "pl-8";
                          const textSpec = isActive
                            ? "text-emerald-700 font-bold text-[11px] bg-emerald-50/80 border-emerald-200"
                            : isLevel0
                              ? "text-zinc-900 font-extrabold text-[11.5px] tracking-wide"
                              : isLevel1 
                                ? "text-zinc-700 font-semibold text-[11px]" 
                                : isLevel2 
                                  ? "text-zinc-500 font-medium text-[10.5px]" 
                                  : "text-zinc-400 text-[10px]";
                          
                          return (
                            <div
                              key={`${h.id}-${i}`}
                              data-heading-id={h.id}
                              onClick={() => handleScrollToHeading(h.id, h.text)}
                              className={`w-full cursor-pointer hover:bg-zinc-50 hover:text-emerald-700 border rounded p-1.5 transition-all flex items-start gap-1.5 px-2 hover:border-zinc-200 ${paddingLeft} ${textSpec} ${isActive ? "border-emerald-200 shadow-sm" : "border-transparent"}`}
                              title={`跳转到: ${h.text}`}
                            >
                              <span className={`select-none shrink-0 mt-0.5 ${isActive ? "text-emerald-500" : isLevel0 ? "text-amber-500" : isLevel1 ? "text-zinc-400" : "text-zinc-300"}`}>
                                {isLevel0 ? "★" : isLevel1 ? "■" : isLevel2 ? "◆" : "◦"}</span>
                              <span className="flex-1 min-w-0 break-words leading-snug">{h.text}</span>
                            </div>
                          );
                        });
                        })()}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: MY ANNOTATIONS TIMELINE (HIGHLIGHTS & NOTES) */}
              {sidebarTab === "notes" && (
                <div className="space-y-3.5 animate-fadeIn flex flex-col h-full">
                  <div className="flex items-center justify-between shrink-0 mb-1">
                    <span className="text-[10.5px] font-bold text-zinc-500">我的批注列表</span>
                    
                    {document.highlights && document.highlights.length > 0 && (
                      <button
                        onClick={handleExportNotes}
                        className="text-[9.5px] font-extrabold text-emerald-700 bg-emerald-50 hover:bg-emerald-100 transition-all border border-emerald-200 px-2 py-1 rounded flex items-center gap-0.5"
                        title="将高亮原文与心得批注导出为独立读书笔记文件"
                      >
                        <Download className="w-2.5 h-2.5 shrink-0" />
                        导出笔记
                      </button>
                    )}
                  </div>

                  <div className="space-y-3 flex-1 overflow-y-auto pr-0.5">
                    {!document.highlights || document.highlights.length === 0 ? (
                      <div className="text-[10px] text-zinc-450 italic text-center py-10 p-4 bg-zinc-50/50 border border-zinc-200 border-dashed rounded-lg leading-relaxed select-none animate-fadeIn">
                        暂无读书笔记划词。请在预览区选中任何文献原文，即刻完成荧光高亮及主观评语批注！
                      </div>
                    ) : (
                      document.highlights.map((hl) => {
                        const bgColors = {
                          yellow: 'border-l-4 border-amber-400 bg-amber-50/20',
                          green: 'border-l-4 border-green-400 bg-green-50/20',
                          pink: 'border-l-4 border-rose-400 bg-rose-50/20',
                          blue: 'border-l-4 border-cyan-400 bg-cyan-50/20'
                        };
                        const colorLabel = { yellow: '概念词汇', green: '论点论证', pink: '重点思考', blue: '拓展内容' };

                        return (
                          <div 
                            key={hl.id} 
                            className={`p-2.5 rounded-lg text-xs space-y-1.5 transition-all text-left group border border-transparent hover:border-zinc-200 ${bgColors[hl.color] || 'border-l-4 border-zinc-200 bg-zinc-50'}`}
                          >
                            <div className="flex items-center justify-between text-[9px] text-zinc-400 font-semibold font-mono">
                              <span className="uppercase text-emerald-600 font-bold">{colorLabel[hl.color] || '标注'}</span>
                              <span>{new Date(hl.createdAt).toLocaleDateString()}</span>
                            </div>

                            <p className="text-zinc-700 font-mono italic text-[10.5px] leading-relaxed border-l-2 border-zinc-200 pl-1.5 select-text">
                              "{hl.text}"
                            </p>

                            {editingHighlightId === hl.id ? (
                              <div className="space-y-1.5 pt-1.5">
                                <textarea
                                  value={editingCommentText}
                                  onChange={(e) => setEditingCommentText(e.target.value)}
                                  className="w-full text-[11px] p-1.5 border border-zinc-300 rounded focus:outline-none focus:border-zinc-500 h-10 font-sans"
                                />
                                <div className="flex justify-end gap-1.5">
                                  <button
                                    onClick={() => setEditingHighlightId(null)}
                                    className="px-1.5 py-1 text-[9px] font-bold bg-zinc-100 hover:bg-zinc-200 rounded text-zinc-650"
                                  >
                                    取消
                                  </button>
                                  <button
                                    onClick={() => handleUpdateComment(hl.id)}
                                    className="px-2 py-1 text-[9px] font-bold bg-zinc-900 text-white rounded hover:bg-zinc-805"
                                  >
                                    保存
                                  </button>
                                </div>
                              </div>
                            ) : (
                              hl.comment && (
                                <p className="text-zinc-600 bg-white/70 shadow-2xs rounded p-1.5 leading-relaxed text-[11px] font-sans">
                                  {hl.comment}
                                </p>
                              )
                            )}

                            {editingHighlightId !== hl.id && (
                              <div className="flex justify-end gap-2 pt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                <button
                                  onClick={() => {
                                    setEditingHighlightId(hl.id);
                                    setEditingCommentText(hl.comment || "");
                                  }}
                                  className="text-[9.5px] font-semibold text-zinc-500 hover:text-zinc-900 hover:underline flex items-center gap-0.5"
                                >
                                  <Edit3 className="w-2.5 h-2.5" />
                                  备注
                                </button>
                                <button
                                  onClick={() => handleDeleteHighlight(hl.id)}
                                  className="text-[9.5px] font-semibold text-rose-500 hover:text-rose-700 hover:underline flex items-center gap-0.5"
                                >
                                  删除
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              )}

              {/* TAB 3: SMART AI BRAIN OPERATIONS */}
              {sidebarTab === "ai" && (
                <div className="space-y-3 animate-fadeIn">
                  {/* Compact input area */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-bold text-zinc-500">选中内容</span>
                      {interpretText && (
                        <button onClick={() => setInterpretText("")} className="text-[9px] text-zinc-400 hover:text-rose-500">清空</button>
                      )}
                    </div>
                    <textarea
                      value={interpretText}
                      onChange={(e) => setInterpretText(e.target.value)}
                      placeholder="在文档中划选文字即可自动填入，或直接粘贴..."
                      className="w-full text-[11px] p-2 border border-zinc-200 rounded-lg h-16 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 resize-none bg-white"
                    />

                    {/* Action buttons — compact row */}
                    <div className="flex gap-2">
                      <button
                        disabled={!interpretText.trim()}
                        onClick={() => handleAIInterpret("simple")}
                        className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold transition-all flex items-center justify-center gap-1 ${
                          isInterpreting && interpretingMode !== "simple"
                            ? "bg-zinc-100 text-zinc-400 cursor-not-allowed"
                            : "bg-zinc-800 text-white hover:bg-zinc-700 cursor-pointer"
                        }`}
                      >
                        {isInterpreting && interpretingMode === "simple" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                        简单解读
                      </button>
                      <button
                        disabled={!interpretText.trim()}
                        onClick={() => handleAIInterpret("deep")}
                        className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold transition-all flex items-center justify-center gap-1 ${
                          isInterpreting && interpretingMode !== "deep"
                            ? "bg-zinc-100 text-zinc-400 cursor-not-allowed"
                            : "bg-emerald-700 text-white hover:bg-emerald-600 cursor-pointer"
                        }`}
                      >
                        {isInterpreting && interpretingMode === "deep" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Cpu className="w-3 h-3" />}
                        深度解析
                      </button>
                    </div>
                  </div>

                  {/* Result */}
                  {interpretationResult ? (
                    <div className="p-3 bg-white border border-zinc-200 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-bold text-emerald-700">解读结果</span>
                        <button
                          onClick={() => { navigator.clipboard.writeText(interpretationResult); setAiResponseStatus("已复制"); setTimeout(() => setAiResponseStatus(""), 2000); }}
                          className="text-[9px] text-zinc-400 hover:text-zinc-600"
                        >复制</button>
                      </div>
                      <div className="text-[11px] text-zinc-700 leading-relaxed max-h-60 overflow-y-auto pr-1 custom-scrollbar">
                        <ReactMarkdown>{interpretationResult}</ReactMarkdown>
                      </div>
                    </div>
                  ) : !isInterpreting && (
                    <div className="py-6 text-center text-[10px] text-zinc-300 italic">选中文字后点击上方按钮开始解读</div>
                  )}

                  {isInterpreting && (
                    <div className="py-8 flex items-center justify-center gap-2 text-[11px] text-zinc-400">
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500" />
                      {interpretingMode === "deep" ? "深度分析中..." : "解读中..."}
                    </div>
                  )}
                </div>
              )}

            </div>
          </div>
          </>
        )}

      </div>

      {showBookmarkModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-xs flex items-center justify-center z-[9999] p-4 font-sans animate-fadeIn">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-sm border border-zinc-200 overflow-hidden animate-scaleIn">
            <div className="bg-emerald-50 px-4 py-3 border-b border-emerald-100 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-emerald-800 font-bold text-xs">
                <Bookmark className="w-3.5 h-3.5 text-emerald-600 fill-emerald-600/10" />
                <span>一键记录当前阅读进度进度</span>
              </div>
              <button
                type="button"
                onClick={() => setShowBookmarkModal(false)}
                className="text-zinc-400 hover:text-zinc-600 text-xs font-bold leading-none cursor-pointer p-1"
              >
                ✕
              </button>
            </div>
            
            <div className="p-4 space-y-3.5">
              <div className="space-y-1">
                <span className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider">当前阅读进度</span>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-850 text-xs font-mono font-bold">
                    {pendingBookmarkRatio}%
                  </span>
                  <span className="text-[11px] text-zinc-500">
                    (滚动像素: {pendingBookmarkScrollTop}px)
                  </span>
                </div>
              </div>
              
              <div className="space-y-1.5">
                <label className="text-[10px] text-zinc-400 font-bold uppercase tracking-wider block">
                  自定义书签备注
                </label>
                <input
                  type="text"
                  value={bookmarkInputLabel}
                  onChange={(e) => setBookmarkInputLabel(e.target.value)}
                  placeholder="请输入该书签位置下的标记，例如：第一章完结"
                  className="w-full text-xs p-2.5 border border-zinc-200 rounded-lg focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 font-sans"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      handleConfirmAddBookmark();
                    }
                  }}
                />
              </div>
            </div>
            
            <div className="px-4 py-3 bg-zinc-50 border-t border-zinc-150 flex justify-end gap-2 shrink-0">
              <button
                type="button"
                onClick={() => setShowBookmarkModal(false)}
                className="px-3 py-1.5 bg-white hover:bg-zinc-100 border border-zinc-200 text-zinc-650 rounded-lg text-[11px] font-bold cursor-pointer"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleConfirmAddBookmark}
                className="px-4 py-1.5 bg-zinc-900 hover:bg-zinc-800 text-white rounded-lg text-[11px] font-bold shadow-xs cursor-pointer flex items-center gap-1"
              >
                保存书签
              </button>
            </div>
          </div>
        </div>
      )}

      {/* PDF 浏览模式 */}
      {showPdfViewer && document && (
        <PdfViewer docTitle={document.title} docId={document.id} onClose={() => setShowPdfViewer(false)} />
      )}
    </div>
  );
}
