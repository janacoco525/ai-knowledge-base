import React, { useState, useEffect, useMemo, useCallback, useRef, Suspense } from "react";
import { lazyRetry } from "./lib/lazyRetry";
import Sidebar from "./components/Sidebar";
import ErrorBoundary from "./components/ErrorBoundary";
import ScanStatusIndicator from "./components/ScanStatusIndicator";
import type { GraphSession } from "./components/GraphPanel";
import { Library, Document, GraphNode, GraphEdge, ChatMessage, NodeCategory, KnowledgeCard, VocabularyTerm, EvidenceItem } from "./types";
import { 
  initialLibraries, initialDocuments, initialNodes, initialEdges 
} from "./lib/mockData";
import { 
  Layers, FileText, MessageSquare, Settings, FolderOpen, Brain, Loader2, RefreshCw
} from "lucide-react";
import { loadChatHistory, saveChatHistory, clearChatHistory, migrateLocalStorageChat, buildSessionPayload } from "./services/chatApi";
import { buildLocalDataBundle, applyLocalDataBundle } from "./lib/userData";
import { getServerUserState, saveServerUserState } from "./services/userStateApi";

const DocEditor = lazyRetry(() => import("./components/DocEditor"));
const ChatPanel = lazyRetry(() => import("./components/ChatPanel"));
const DocComparison = lazyRetry(() => import("./components/DocComparison"));
const KnowledgeCards = lazyRetry(() => import("./components/KnowledgeCards"));
const SettingsPanel = lazyRetry(() => import("./components/SettingsPanel"));
const GraphPanel = lazyRetry(() => import("./components/GraphPanel"));

export default function App() {
  const [readingProgress, setReadingProgress] = useState(0);
  
  // Master states with local storage backup
  const [libraries, setLibraries] = useState<Library[]>(() => {
    const saved = localStorage.getItem("kb_libraries");
    const raw: Library[] = saved ? JSON.parse(saved) : initialLibraries;
    // 启动时按 name 去重（防御历史脏数据 — 多次会话累计的同名 library）
    const seen = new Set<string>();
    return raw.filter(lib => {
      if (seen.has(lib.name)) return false;
      seen.add(lib.name);
      return true;
    });
  });

  // 文档列表：从后端获取
  const [documents, setDocuments] = useState<Document[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);

  // 从后端拉取文档列表 — domain 直接当 libraryId，不再经过映射
  const fetchDocuments = useCallback(() => {
    fetch("/api/kb/files")
      .then(r => r.json())
      .then(data => {
        const files = data.files || [];
        // 收集后端所有 domain，确保每个 domain 在前端都有对应库
        const backendDomains = new Set<string>(files.map((f: any) => f.domain || "default"));
        // 用 setLibraries 函数式更新：基于 prev state 推算
        // ① 删除后端已不存在的历史脏库（如"AI 核心与 RAG 引擎"、"企业开发与安全守则"等旧名）
        // ② 按 name 去重
        // ③ 补齐后端所有 domain
        setLibraries(prevLibraries => {
          // 删除后端没有的库 — 通用型修复：解决 7个目录显示但实际只有4个的脏数据 bug
          const cleaned = prevLibraries.filter(lib => backendDomains.has(lib.name));
          // 按 name 去重 — 保留 name 第一次出现
          const seenNames = new Set();
          const deduped = cleaned.filter(lib => {
            if (seenNames.has(lib.name)) return false;
            seenNames.add(lib.name);
            return true;
          });
          const existingNames = new Set(deduped.map(l => l.name));
          const newLibraries = [...deduped];
          let libsChanged = cleaned.length !== prevLibraries.length;
          // 排序：default 永远在最前
          backendDomains.forEach(domain => {
            if (!existingNames.has(domain)) {
              newLibraries.push({
                id: domain,
                name: domain,
                description: domain === "default" ? "默认文件夹" : `自动创建 — 来自后端 domain「${domain}」`,
                createdAt: new Date().toISOString(),
                color: domain === "default" ? "emerald" : "zinc",
              });
              existingNames.add(domain);
              libsChanged = true;
              console.warn("[fetchDocuments] 自动创建文件夹:", domain);
            }
          });
          // 排序：default 在前，其他按字母
          newLibraries.sort((a, b) => {
            if (a.id === "default") return -1;
            if (b.id === "default") return 1;
            return a.name.localeCompare(b.name, "zh-CN");
          });
          // 如果后端完全没有文件（用户已删完），清空 libraries
          // ⛔ 2026-08-19 修复：已是空数组时返回原引用，否则每次返回新 [] 会触发
          // useCallback([libraries]) 重建 → useEffect 重跑 → 无限请求 /api/kb/files（刷屏根因）
          if (files.length === 0) {
            return prevLibraries.length === 0 ? prevLibraries : [];
          }
          // libsChanged 为 false 且 deduped 长度 = prevLibraries 长度 → 完全没变
          if (!libsChanged && deduped.length === prevLibraries.length) {
            return prevLibraries;
          }
          return newLibraries;
        });

        // domain 直接作为 libraryId，前后端统一
        const newDocs = files.map((f: any) => {
          const domain = f.domain || "default";
          return {
            id: f.id,
            libraryId: domain,
            title: f.name || f.physical_name || f.id,
            content: "",
            tags: [],
            createdAt: f.uploaded_at || "",
            updatedAt: f.uploaded_at || "",
            summary: "",
            entitiesProcessed: true,
            fileType: f.file_type || "",
            charCount: typeof f.char_count === "number" ? f.char_count : undefined,
          };
        });
        // 合并 localStorage 中的书签/高亮（防止刷新丢失）
        const savedAnnotations = (() => {
          try { return JSON.parse(localStorage.getItem("kb_annotations") || "{}"); }
          catch { return {}; }
        })();
        const merged = newDocs.map(d => {
          const saved = savedAnnotations[d.id];
          if (!saved) return d;
          return {
            ...d,
            bookmarks: saved.bookmarks || d.bookmarks || [],
            highlights: saved.highlights || d.highlights || [],
          };
        });
        setDocuments(merged);
        // ⛔ 2026-08-10 修复：不再用"后端列表为空"判定清理前端图谱/卡片/聊天。
        // 冷启动瞬间 get_kb() 索引未加载完，/api/kb/files 短暂返回空列表，
        // 会误清用户手工生成的图谱会话/知识卡片/聊天记录（数据丢失事故）。
        // 前端手工数据只应在【用户明确删除】时清理，不由后端列表状态决定。
        // 若确有删除需求，应走 handleDeleteDocument 等显式操作。
      })
      .catch(() => {})
      .finally(() => setDocsLoading(false));
  }, [libraries]);

  // 启动时拉取一次
  useEffect(() => { fetchDocuments(); }, [fetchDocuments]);

  const [graphSessions, setGraphSessions] = useState<GraphSession[]>(() => {
      const ver = localStorage.getItem("kb_graph_ver");
      if (ver !== "v4") {
        const migrated: GraphSession[] = [];
        try {
          const oldNodes: GraphNode[] = JSON.parse(localStorage.getItem("kb_nodes") || "[]");
          const oldEdges: GraphEdge[] = JSON.parse(localStorage.getItem("kb_edges") || "[]");
          if (oldNodes.length > 0 && oldNodes[0]?.docId) {
            const doc = initialDocuments.find(d => d.id === oldNodes[0].docId);
            migrated.push({ id: "gs-legacy", docTitle: doc?.title || "旧图谱", docId: oldNodes[0].docId, timestamp: new Date().toISOString(), nodeCount: oldNodes.length, edgeCount: oldEdges.length, nodes: oldNodes, edges: oldEdges });
          }
        } catch {}
        ["kb_nodes","kb_edges","kb_deleted_nodes","kb_deleted_edges","kb_graph_migrated_v2","kb_graph_ver"].forEach(k => localStorage.removeItem(k));
        localStorage.setItem("kb_graph_ver", "v4");
        localStorage.setItem("kb_graph_sessions", JSON.stringify(migrated));
        return migrated;
      }
      const s = localStorage.getItem("kb_graph_sessions");
      // ⛔ 2026-08-10 防丢失：主数据为空时尝试从备份恢复（防止误清/意外清空）
      if (!s) {
        const backup = localStorage.getItem("kb_graph_sessions_backup");
        if (backup) {
          try {
            const restored = JSON.parse(backup);
            if (Array.isArray(restored) && restored.length > 0) {
              // 恢复并把备份写回主数据
              localStorage.setItem("kb_graph_sessions", backup);
              return restored;
            }
          } catch { /* 备份损坏则忽略 */ }
        }
        return [];
      }
      const parsed = JSON.parse(s);
      return parsed;
  });
  const [activeSessionId, setActiveSessionId] = useState<string | null>(graphSessions[0]?.id || null);
  const activeSession = graphSessions.find(s => s.id === activeSessionId);
  const nodes = activeSession?.nodes || [];
  const edges = activeSession?.edges || [];

  // setNodes/setEdges 封装：更新 activeSession 在 graphSessions 中的副本
  const setNodes = useCallback((arg: GraphNode[] | ((prev: GraphNode[]) => GraphNode[])) => {
    setGraphSessions(prev => prev.map(s => {
      if (s.id !== activeSessionId) return s;
      const newNodes = typeof arg === 'function' ? arg(s.nodes) : arg;
      return { ...s, nodes: newNodes, nodeCount: newNodes.length };
    }));
  }, [activeSessionId]);
  const setEdges = useCallback((arg: GraphEdge[] | ((prev: GraphEdge[]) => GraphEdge[])) => {
    setGraphSessions(prev => prev.map(s => {
      if (s.id !== activeSessionId) return s;
      const newEdges = typeof arg === 'function' ? arg(s.edges) : arg;
      return { ...s, edges: newEdges, edgeCount: newEdges.length };
    }));
  }, [activeSessionId]);

  // 图谱 session 同步：写 localStorage + 双写备份（2026-08-10 防丢失）
  // ⛔ 2026-08-19：列表被删空时同步删除备份，否则旧备份会在主数据缺失时复活已删除的会话
  useEffect(() => {
    localStorage.setItem("kb_graph_sessions", JSON.stringify(graphSessions));
    if (graphSessions.length > 0) {
      localStorage.setItem("kb_graph_sessions_backup", JSON.stringify(graphSessions));
    } else {
      localStorage.removeItem("kb_graph_sessions_backup");
    }
  }, [graphSessions]);

  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [chatHistoryLoaded, setChatHistoryLoaded] = useState(false);

  // 启动时从后端加载聊天记录（优先后端，fallback localStorage 迁移）
  useEffect(() => {
    (async () => {
      // 1. 尝试从后端加载
      const remote = await loadChatHistory();
      if (remote.length > 0) {
        setChatHistory(remote);
        setChatHistoryLoaded(true);
        return;
      }
      // 2. 后端无数据，尝试 localStorage 迁移
      const migrated = await migrateLocalStorageChat();
      if (migrated) {
        const after = await loadChatHistory();
        setChatHistory(after);
      } else {
        // 3. 也没有可迁移的数据，fallback localStorage 直接读
        const saved = localStorage.getItem("kb_chat");
        if (saved) {
          try {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) {
              setChatHistory(parsed);
              localStorage.removeItem("kb_chat");
            }
          } catch { localStorage.removeItem("kb_chat"); }
        }
      }
      setChatHistoryLoaded(true);
    })();
  }, []);

  const [deletedNodes, setDeletedNodes] = useState<GraphNode[]>([]);
  const [deletedEdges, setDeletedEdges] = useState<{[nodeId: string]: GraphEdge[]}>({});

  const [knowledgeCards, setKnowledgeCards] = useState<KnowledgeCard[]>(() => {
    const saved = localStorage.getItem("kb_knowledge_cards");
    if (saved) {
      try {
        const cards = JSON.parse(saved);
        if (!Array.isArray(cards)) return [];
        // ⛔ 2026-08-14（任务十六）：新卡状态模型迁移。
        // 旧实现把新卡一律写死 easy（"已记住"），实际从未学习——一次性迁移为
        // "new"（未学）态，状态才有意义；此后新卡默认 new、评分后才进 easy/medium/hard。
        const validStates = ["new", "easy", "medium", "hard"];
        const migKey = "kb_cards_state_v2";
        const needsMig = !localStorage.getItem(migKey);
        // 清理硬编码的示例卡片（docId 不存在的）+ 按 id 去重（经验 #026：读时必须去重）
        const seen = new Set<string>();
        const cleaned = cards.filter((c: any) => {
          if (c.docId === "doc-rag-basics") return false;
          if (!c.id) return true; // 无 id 的旧数据保留（导入时会补）
          if (seen.has(c.id)) return false;
          seen.add(c.id);
          return true;
        }).map((c: any) => {
          // 无效/缺失难度 → new；一次性迁移：存量 easy（自动默认值）→ new
          let difficulty = c.difficulty;
          if (!validStates.includes(difficulty)) difficulty = "new";
          else if (needsMig && difficulty === "easy") difficulty = "new";
          return { ...c, difficulty };
        });
        if (needsMig) localStorage.setItem(migKey, "1");
        return cleaned;
      } catch {
        // 经验 #026：localStorage 脏数据不能崩 App，回退空卡箱
        return [];
      }
    }
    return [];
  });

  // State for recorded vocabulary/glossary terms
  const [vocabularyTerms, setVocabularyTerms] = useState<VocabularyTerm[]>(() => {
    const saved = localStorage.getItem("kb_vocabulary_terms");
    if (saved) {
      try {
        const terms = JSON.parse(saved);
        return Array.isArray(terms) ? terms : [];
      } catch {
        return [];
      }
    }
    return [
      {
        id: "vocab-init-1",
        term: "RAG",
        definition: "**中文释义：** 检索增强生成 (Retrieval-Augmented Generation)，一种在向大模型提问前，先在多语意文档库中检索相关的资料并合并提交的高阶系统架构。\n\n- **开发应用：** 精准过滤大模型幻觉，实现企业内部专有知识域的高效精准检索与安全阅读。",
        createdAt: new Date().toISOString(),
        status: "learning"
      },
      {
        id: "vocab-init-2",
        term: "Embedding (向量嵌入)",
        definition: "**中文释义：** 将纯文本、实体词汇通过算法映射为含有丰富语义信息的实数密集向量，是高维向量相似度检索的技术基础。\n\n- **开发应用：** 语义检索与关联性搜索定位，完成高维匹配而仅是关键词字面比较。",
        createdAt: new Date().toISOString(),
        status: "mastered"
      }
    ];
  });

  const [activeVocabId, setActiveVocabId] = useState<string | null>(null);
  const [isDefiningTerm, setIsDefiningTerm] = useState(false);
  const [vocabLookupWord, setVocabLookupWord] = useState("");

  // Primary Platform view — sync with URL hash so refresh/back/forward work
  const VALID_VIEWS = ["documents", "graph", "chat", "settings", "compare", "cards"] as const;
  type ViewName = typeof VALID_VIEWS[number];
  const getHashView = (): ViewName => {
    const h = window.location.hash.slice(1); // strip #
    return VALID_VIEWS.includes(h as ViewName) ? (h as ViewName) : "documents";
  };
  const [currentView, _setView] = useState<ViewName>(getHashView);
  const switchView = (v: ViewName) => {
    _setView(v);
    window.location.hash = v;
  };
  // Listen for back/forward (hashchange) and initial load
  useEffect(() => {
    const onHash = () => _setView(getHashView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const [compareSubView, setCompareSubView] = useState<"compare" | "scan">("scan");

  // Navigation and Workspace Active layouts inside Documents Office
  const [selectedLibId, setSelectedLibId] = useState<string | null>(null);
  // 从 localStorage 恢复上次打开的文档（防止刷新回到首页）
  const [selectedDocId, setSelectedDocId] = useState<string | null>(() => {
    try { return localStorage.getItem("kb_selected_doc") || "doc-rag-basics"; }
    catch { return "doc-rag-basics"; }
  });
  const [activeTab, setActiveTab] = useState<"graph" | "editor">("editor");

  useEffect(() => {
    setReadingProgress(0);
    // 同步到 localStorage（防刷新丢失）
    if (selectedDocId) {
      try { localStorage.setItem("kb_selected_doc", selectedDocId); } catch {}
    }
  }, [selectedDocId]);

  // Server health state
  const [serverHealthy, setServerHealthy] = useState<boolean | null>(null);
  const [serverLoading, setServerLoading] = useState(true);
  const [healthMessage, setHealthMessage] = useState("");

  // ===== 动态模型配置 =====
  const [apiProviders, setApiProviders] = useState<Array<{
    id: string; name: string; base_url: string; models: string[]; docs: string;
    keyConfigured?: boolean; keyPreview?: string; configuredModel?: string;
  }>>([]);
  const [selectedProvider, setSelectedProvider] = useState("deepseek");
  const [activeProviderId, setActiveProviderId] = useState("");
  const [activeModelName, setActiveModelName] = useState("");
  const [activeKeyPreview, setActiveKeyPreview] = useState("");
  const [currentProviderKeyPreview, setCurrentProviderKeyPreview] = useState("");
  const [currentProviderHasKey, setCurrentProviderHasKey] = useState(false);
  
  // 429 限流冷却（问答页）
  const [chatCooldownUntil, setChatCooldownUntil] = useState<number>(0);
  const isChatCoolingDown = chatCooldownUntil > Date.now();
  const chatCooldownSeconds = Math.max(0, Math.ceil((chatCooldownUntil - Date.now()) / 1000));

  // 页面加载 — 从 /health 获取一切，不可用时降级到旧接口
  useEffect(() => {
    async function loadConfig() {
      try {
        const hResp = await fetch("/health");
        const data = await hResp.json();

        // 全局状态
        setServerHealthy(data.apiKeyPresent);
        setHealthMessage(data.message || "");

        if (data.providers && data.providers.length > 0) {
          setApiProviders(data.providers);
          const activePid = data.activeProviderId || "";
          if (activePid) {
            setActiveProviderId(activePid);
            setActiveModelName(data.modelName || "");
            setActiveKeyPreview(data.apiKeyPreview || "");
            setSelectedProvider(activePid);
            const activeProv = data.providers.find((p: any) => p.id === activePid);
            if (activeProv && activeProv.keyConfigured) {
              setCurrentProviderKeyPreview(activeProv.keyPreview || "");
              setCurrentProviderHasKey(true);
            }
          }
          return;
        }

        const pResp = await fetch("/api/providers");
        const pData = await pResp.json();
        if (pData.providers) {
          setApiProviders(pData.providers);
          const ep = data.apiEndpoint || localStorage.getItem("kb_llm_api_endpoint") || "";
          let activePid = "";
          if (ep) {
            const match = pData.providers.find((p: any) => ep.includes(
              (p.base_url || "").replace(/https?:\/\//, "").split("/")[0]
            ));
            if (match) activePid = match.id;
          }
          if (!activePid) {
            const first = pData.providers.find((p: any) => p.keyConfigured);
            activePid = first?.id || "";
          }
          if (activePid) {
            setActiveProviderId(activePid);
            const ap = pData.providers.find((p: any) => p.id === activePid);
            setActiveModelName(ap?.configuredModel || data.modelName || "");
            setActiveKeyPreview(ap?.keyPreview || data.apiKeyPreview || "");
            setSelectedProvider(activePid);
            if (ap?.keyConfigured) {
              setCurrentProviderKeyPreview(ap.keyPreview || "");
              setCurrentProviderHasKey(true);
            }
          }
        }
      } catch (err) {
        setServerHealthy(false);
        setHealthMessage("无法连接服务器");
      } finally {
        setServerLoading(false);
      }
    }
    loadConfig();
  }, []);

  // Submitting AI prompts status
  const [isSendingMessage, setIsSendingMessage] = useState(false);

  // Left folders sidebar width state
  const [leftSidebarWidth, setLeftSidebarWidth] = useState(() => {
    const saved = localStorage.getItem("kb_left_sidebar_width");
    return saved ? parseInt(saved, 10) : 280;
  });

  // Sync left sidebar width to localstorage
  useEffect(() => {
    localStorage.setItem("kb_left_sidebar_width", leftSidebarWidth.toString());
  }, [leftSidebarWidth]);

  const activeDocument = documents.find(d => d.id === selectedDocId);

  // 后端文档内容缓存（最多保留 20 份，防内存泄漏）
  // 同时缓存 text + preprocessed + headings，避免重复计算
  const contentCache = useRef<Record<string, { text: string; preprocessed: string; headings: any[] }>>({});
  const CONTENT_CACHE_MAX = 20;
  // T38：正文加载状态由 App 统一管理（DocEditor 不再自行 fetch，消除双请求竞态）
  const [contentLoading, setContentLoading] = useState(false);
  const contentLoadingDocRef = useRef<string | null>(null);

  // 后端文档：点击时自动获取内容（带缓存）
  // 依赖 documents：修复刷新恢复文档时 documents 尚未加载、正文永不加载的竞态
  useEffect(() => {
    if (!selectedDocId) return;
    const doc = documents.find(d => d.id === selectedDocId);
    if (!doc) return;
    if (doc.content) return; // 已有内容则跳过
    if (contentCache.current[selectedDocId]) {
      // 从缓存恢复
      const cached = contentCache.current[selectedDocId];
      setDocuments(prev => prev.map(d => d.id === selectedDocId ? { ...d, content: cached.text } : d));
      return;
    }
    // 防重复：该文档已在加载中则跳过（documents 变化会触发本 effect 重跑）
    if (contentLoadingDocRef.current === selectedDocId) return;
    setContentLoading(true);
    contentLoadingDocRef.current = selectedDocId;
    fetch(`/api/kb/files/${encodeURIComponent(selectedDocId)}/text`)
      .then(r => r.json())
      .then(data => {
        const text = data.text || "";
        if (text) {
          // LRU 风格：超过上限时删最旧的
          const keys = Object.keys(contentCache.current);
          if (keys.length >= CONTENT_CACHE_MAX) {
            delete contentCache.current[keys[0]];
          }
          contentCache.current[selectedDocId] = {
            text,
            preprocessed: data.preprocessed || "",
            headings: data.headings || [],
          };
          setDocuments(prev => prev.map(d => d.id === selectedDocId ? { ...d, content: text } : d));
        }
      })
      .catch(() => {})
      .finally(() => {
        // 防竞态：仅当仍是当前选中文档时才清除 loading
        if (contentLoadingDocRef.current === selectedDocId) {
          setContentLoading(false);
          contentLoadingDocRef.current = null;
        }
      });
  }, [selectedDocId, documents]);

  // Sync to localStorage on state changes (去重后写入，避免历史脏数据累积)
  useEffect(() => {
    const seen = new Set<string>();
    const deduped = libraries.filter(lib => {
      if (seen.has(lib.name)) return false;
      seen.add(lib.name);
      return true;
    });
    if (deduped.length !== libraries.length) {
      // 静默 setLibraries 修复 — 不触发额外渲染，但下次 effect 会重写 localStorage
      setLibraries(deduped);
      return;
    }
    localStorage.setItem("kb_libraries", JSON.stringify(libraries));
  }, [libraries]);
  // 文档不再存 localStorage，从后端拉取

  // 一次性清理 V3 旧 key
  useEffect(() => {
    ["kb_nodes","kb_edges","kb_deleted_nodes","kb_deleted_edges","kb_graph_migrated_v2"].forEach(k => {
      if (localStorage.getItem(k) !== null) localStorage.removeItem(k);
    });
  }, []);


  // ⛔ 2026-08-14：历史保存链路修复 —— 防抖 + ref + 卸载兜底，根治"刷新后引用段落丢失"
  const chatHistoryRef = useRef<ChatMessage[]>([]);
  useEffect(() => {
    chatHistoryRef.current = chatHistory;
  }, [chatHistory, chatHistoryLoaded]);

  // 保存防抖 800ms：流式中间态不频繁写库，最终态稳定落库
  useEffect(() => {
    if (!chatHistoryLoaded) return;
    const t = window.setTimeout(() => saveChatHistory(chatHistory), 800);
    return () => window.clearTimeout(t);
  }, [chatHistory, chatHistoryLoaded]);

  // 刷新/关闭页面前兜底保存（sendBeacon 可靠送达，不依赖防抖时序）
  useEffect(() => {
    const flush = () => {
      const data = chatHistoryRef.current;
      if (!data || data.length === 0) return;
      try {
        navigator.sendBeacon(
          "/api/chat/sessions",
          new Blob([buildSessionPayload(data)], { type: "application/json" })
        );
      } catch {
        /* 兜底失败静默 */
      }
    };
    window.addEventListener("beforeunload", flush);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("pagehide", flush);
    };
  }, []);

  useEffect(() => {
    localStorage.setItem("kb_knowledge_cards", JSON.stringify(knowledgeCards));
  }, [knowledgeCards]);

  useEffect(() => {
    localStorage.setItem("kb_vocabulary_terms", JSON.stringify(vocabularyTerms));
  }, [vocabularyTerms]);

  // ⛔ 2026-08-14（任务二十八）：用户状态服务端同步——标注/图谱/卡片/词条等落盘到
  // app/rag_app/data/user_state.json，复制数据文件夹即可整机迁移（换电脑带数据）
  useEffect(() => {
    let cancelled = false;
    const pushLocal = async () => {
      const bundle = buildLocalDataBundle();
      const dataJson = JSON.stringify(bundle.data);
      if (localStorage.getItem("kb_user_state_hash") === dataJson) return; // 无变化不重复推
      const ok = await saveServerUserState(bundle);
      if (ok && !cancelled) {
        localStorage.setItem("kb_user_state_hash", dataJson);
        localStorage.setItem("kb_user_state_synced_at", bundle.updatedAt);
      }
    };
    (async () => {
      const server = await getServerUserState();
      if (cancelled) return;
      const marker = localStorage.getItem("kb_user_state_synced_at") || "";
      if (server && server.updatedAt && server.updatedAt > marker) {
        // 服务端更新（换电脑/换浏览器后首次打开）→ 写回 localStorage 并重载以全量生效
        const n = applyLocalDataBundle(server.data);
        if (n > 0) {
          localStorage.setItem("kb_user_state_synced_at", server.updatedAt);
          localStorage.removeItem("kb_user_state_hash");
          window.location.reload();
          return;
        }
      }
      await pushLocal();
    })();
    const interval = window.setInterval(pushLocal, 60000);
    const flush = () => {
      const bundle = buildLocalDataBundle();
      const dataJson = JSON.stringify(bundle.data);
      if (localStorage.getItem("kb_user_state_hash") === dataJson) return;
      try {
        navigator.sendBeacon("/api/user-state", new Blob([JSON.stringify(bundle)], { type: "application/json" }));
      } catch { /* 兜底失败静默 */ }
      localStorage.setItem("kb_user_state_hash", dataJson);
      localStorage.setItem("kb_user_state_synced_at", bundle.updatedAt);
    };
    window.addEventListener("beforeunload", flush);
    window.addEventListener("pagehide", flush);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("pagehide", flush);
    };
  }, []);

  // Ctrl+Z Undo hotkey mapping to restore last deleted node
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        const activeEl = document.activeElement;
        const isInput = activeEl && (
          activeEl.tagName === "INPUT" || 
          activeEl.tagName === "TEXTAREA" || 
          (activeEl as HTMLElement).isContentEditable
        );
        if (isInput) return;

        if (deletedNodes.length > 0) {
          e.preventDefault();
          const targetNode = deletedNodes[0];
          handleRestoreNode(targetNode.id);
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [deletedNodes, deletedEdges]);

  // Workspace Navigators
  const handleSelectLibrary = (libId: string | null) => {
    setSelectedLibId(libId);
    if (libId) {
      const libraryDocs = documents.filter(doc => doc.libraryId === libId);
      if (libraryDocs.length > 0) {
        setSelectedDocId(libraryDocs[0].id);
      } else {
        setSelectedDocId(null);
      }
    } else {
      setSelectedDocId(null);
    }
  };

  const handleSelectDocument = (docId: string | null) => {
    setSelectedDocId(docId);
    if (docId) {
      const doc = documents.find(d => d.id === docId);
      if (doc) {
        setSelectedLibId(doc.libraryId);
        setActiveTab("editor");
      }
    }
  };

  const handleNavigateToDoc = (docId: string) => {
    handleSelectDocument(docId);
    switchView("documents");
  };

  // Add Library
  const handleAddLibrary = (name: string, description: string, color: string, customId?: string) => {
    if (!name || name.trim().length < 2) {
      console.warn(`[handleAddLibrary] 拒绝创建：名称过短 "${name}"`);
      return;
    }
    // 检查同名文件夹
    const trimmedName = name.trim();
    if (libraries.some(lib => lib.name === trimmedName)) {
      alert(`已存在同名文件夹「${trimmedName}」，请使用其他名称`);
      return;
    }
    // 直接用 name 当 ID，确保前后端 domain=libraryId 统一
    const newLib: Library = {
      id: customId || trimmedName,
      name: trimmedName,
      description,
      createdAt: new Date().toISOString(),
      color
    };
    setLibraries([...libraries, newLib]);
    setSelectedLibId(newLib.id);
    setSelectedDocId(null);
  };

  // Add Document
  const handleAddDocument = (title: string, libraryId: string, content: string, tags: string[]) => {
    const newDoc: Document = {
      id: `doc-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      libraryId,
      title,
      content,
      tags,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    setDocuments([...documents, newDoc]);
    setSelectedDocId(newDoc.id);
  };

  // Update Library details
  const handleUpdateLibrary = (libId: string, updates: Partial<Library>) => {
    setLibraries(prevLibs => 
      prevLibs.map(lib => lib.id === libId ? { ...lib, ...updates } : lib)
    );
  };

  // Delete Library
  const handleDeleteLibrary = async (id: string) => {
    // 先调后端删除域及其文件，确认成功后再清前端 state
    try {
      const resp = await fetch(`/api/domains/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        console.error("[handleDeleteLibrary] 后端删除失败:", errData);
        return;  // 后端失败 → 不清前端
      }
    } catch (err) {
      console.error("[handleDeleteLibrary] 网络错误:", err);
      return;  // 网络失败 → 不清前端
    }

    // 后端确认删除成功 → 刷新列表 + 清理前端
    await fetchDocuments();
    setLibraries(libraries.filter(l => l.id !== id));
    setNodes(nodes.filter(n => n.libraryId !== id));
    setEdges(edges.filter(e => e.libraryId !== id));
    if (selectedLibId === id) {
      setSelectedLibId(null);
      setSelectedDocId(null);
    }
  };

  // Update document content/details
  const handleUpdateDocument = (docId: string, updates: Partial<Document>) => {
    setDocuments(prevDocs => {
      const next = prevDocs.map(doc => doc.id === docId ? { ...doc, ...updates } : doc);
      // 持久化 bookmarks/highlights 到 localStorage（防刷新丢失）
      if (updates.bookmarks !== undefined || updates.highlights !== undefined) {
        try {
          const updated = next.find(d => d.id === docId);
          if (updated) {
            const all = (() => {
              try { return JSON.parse(localStorage.getItem("kb_annotations") || "{}"); }
              catch { return {}; }
            })();
            all[docId] = {
              bookmarks: updated.bookmarks || [],
              highlights: updated.highlights || [],
            };
            localStorage.setItem("kb_annotations", JSON.stringify(all));
          }
        } catch (e) { /* ignore quota errors */ }
      }
      return next;
    });
  };

  // Delete Document
  const handleDeleteDocument = async (docId: string) => {
    const isLocalDoc = docId.startsWith("doc-");
    if (!isLocalDoc) {
      try {
        const resp = await fetch(`/api/kb/files/${encodeURIComponent(docId)}`, { method: "DELETE" });
        if (resp.ok) {
          // 后端删除成功 → 刷新列表确认（防御 #017 后端静默失败）
          await fetchDocuments();
        } else {
          console.error("[handleDeleteDocument] 后端删除失败:", resp.status);
        }
      } catch (err) {
        console.error("[handleDeleteDocument] 网络错误:", err);
      }
    } else {
      // 本地文档直接从 state 移除
      setDocuments(prev => prev.filter(d => d.id !== docId));
    }
    // 标记关联的图谱节点为"来源已移除"（保留历史记录）
    setNodes(prevNodes => prevNodes.map(n => 
      n.docId === docId ? { ...n, sourceRemoved: true } : n
    ));
    setEdges(prevEdges => prevEdges.map(e => 
      e.docId === docId ? { ...e, sourceRemoved: true } : e
    ));
    // ⛔ 2026-08-14：知识卡片/词条标记"来源已移除"（保留历史，避免孤儿卡
    // 在卡箱里显示成"跨篇独立卡"）
    setKnowledgeCards(prev => prev.map(c =>
      c.docId === docId ? { ...c, sourceRemoved: true } : c
    ));
    setVocabularyTerms(prev => prev.map(v =>
      v.docId === docId ? { ...v, sourceRemoved: true } : v
    ));
    // 聊天记录保留（历史记录）
    if (selectedDocId === docId) {
      setSelectedDocId(null);
    }
  };

  // Knowledge Cards Management Handlers
  const handleAddKnowledgeCard = (card: Omit<KnowledgeCard, "id" | "createdAt">) => {
    const newCard: KnowledgeCard = {
      ...card,
      id: `card-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
      createdAt: new Date().toISOString()
    };
    setKnowledgeCards(prev => [newCard, ...prev]);
  };

  const handleUpdateKnowledgeCard = (id: string, updates: Partial<KnowledgeCard>) => {
    setKnowledgeCards(prev => prev.map(c => c.id === id ? { ...c, ...updates } : c));
  };

  const handleDeleteKnowledgeCard = (id: string) => {
    setKnowledgeCards(prev => prev.filter(c => c.id !== id));
  };

  const handleImportKnowledgeCards = (imported: KnowledgeCard[]) => {
    // ⛔ 2026-08-14：导入强制重新生成 id（忽略原 id，防 React key 冲突/重复导入）
    // 并按 front+back 与现有卡去重（同一文件重复导入不产生重复卡）
    setKnowledgeCards(prev => {
      const existingKeys = new Set(prev.map(c => `${c.front}||${c.back}`));
      const fresh = imported
        .filter(c => c.front && c.back)
        .filter(c => {
          const key = `${c.front}||${c.back}`;
          if (existingKeys.has(key)) return false;
          existingKeys.add(key);
          return true;
        })
        .map((c, idx) => ({
          ...c,
          id: `card-imported-${Date.now()}-${idx}-${Math.floor(Math.random() * 100000)}`,
          createdAt: c.createdAt || new Date().toISOString(),
          // ⛔ 2026-08-14（任务十六）：导入难度兜底——缺失/无效状态一律视为未学
          difficulty: ["new", "easy", "medium", "hard"].includes(c.difficulty)
            ? c.difficulty
            : "new"
        }));
      return [...fresh, ...prev];
    });
  };

  // ⛔ 2026-08-14：重置卡箱到初始状态（清空全部卡片，localStorage 由 useEffect 同步清空）
  const handleResetKnowledgeCards = () => {
    setKnowledgeCards([]);
  };

  // Nodes management in currently active library
  const handleAddNode = (label: string, category: NodeCategory) => {
    const targetLibId = selectedLibId || (libraries.length > 0 ? libraries[0].id : "lib-ai");
    const newNode: GraphNode = {
      id: `node-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      label,
      category,
      libraryId: targetLibId
    };
    setNodes([...nodes, newNode]);
  };

  const handleDeleteNode = (id: string) => {
    const nodeToDelete = nodes.find(n => n.id === id);
    if (nodeToDelete) {
      setDeletedNodes(prev => [nodeToDelete, ...prev.filter(n => n.id !== id)]);
      const connectedEdges = edges.filter(e => e.source === id || e.target === id);
      setDeletedEdges(prev => ({
        ...prev,
        [id]: connectedEdges
      }));
    }
    setNodes(nodes.filter(n => n.id !== id));
    setEdges(edges.filter(e => e.source !== id && e.target !== id));
  };

  const handleRestoreNode = (id: string) => {
    const nodeToRestore = deletedNodes.find(n => n.id === id);
    if (nodeToRestore) {
      setNodes(prev => [...prev, nodeToRestore]);
      setDeletedNodes(prev => prev.filter(n => n.id !== id));
      
      const edgesToRestore = deletedEdges[id];
      if (edgesToRestore && edgesToRestore.length > 0) {
        setEdges(prev => {
          const existingIds = new Set(prev.map(e => e.id));
          return [...prev, ...edgesToRestore.filter(e => !existingIds.has(e.id))];
        });
        setDeletedEdges(prev => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }
    }
  };

  const handleAddEdge = (source: string, target: string, label: string) => {
    const targetLibId = selectedLibId || (libraries.length > 0 ? libraries[0].id : "lib-ai");
    const newEdge: GraphEdge = {
      id: `edge-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      source,
      target,
      label,
      libraryId: targetLibId
    };
    setEdges([...edges, newEdge]);
  };

  const handleDeleteEdge = (id: string) => {
    setEdges(edges.filter(e => e.id !== id));
  };

  // Take JSON structures extracted by Gemini and merge seamlessly with Graph
  const handleCreateExternalGraph = (incomingNodes: any[], incomingEdges: any[], docId: string) => {
    const targetLibId = selectedLibId || (libraries.length > 0 ? libraries[0].id : "lib-ai");

    const formattedNodes: GraphNode[] = incomingNodes.map((n, i) => ({
      id: `${n.id || `node-${i}`}-${Date.now()}`,
      label: n.label || "重点实体",
      category: n.category || "concept",
      libraryId: targetLibId,
      docId
    }));

    const idMap: Record<string, string> = {};
    incomingNodes.forEach((n, idx) => {
      idMap[n.id] = formattedNodes[idx].id;
    });

    const formattedEdges: GraphEdge[] = incomingEdges
      .map((e, i) => {
        const mappedSource = idMap[e.source];
        const mappedTarget = idMap[e.target];
        if (!mappedSource || !mappedTarget) return null;

        return {
          id: `edge-ai-${i}-${Date.now()}`,
          source: mappedSource,
          target: mappedTarget,
          label: e.label || "联系",
          libraryId: targetLibId,
          docId
        };
      })
      .filter(e => e !== null) as GraphEdge[];

    setNodes(prevNodes => [...prevNodes, ...formattedNodes]);
    setEdges(prevEdges => [...prevEdges, ...formattedEdges]);
    switchView("graph");
  };

  // Intelligent conversational Chatbot dispatch
  const handleSendMessage = async (queryText: string, scope: "all" | "local" | "web" | "custom", customDocIds?: string[]) => {
    if (isChatCoolingDown) {
      return;
    }

    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: "user",
      content: queryText,
      timestamp: new Date().toISOString()
    };

    const updatedHistory = [...chatHistory, userMessage];
    setChatHistory(updatedHistory);
    setIsSendingMessage(true);

    // ⛔ 2026-08-13：SSE 流式回答 —— 先插入空回答占位，逐 chunk 更新
    const assistantId = `msg-ai-${Date.now()}`;
    setChatHistory(prev => [...prev, {
      id: assistantId,
      role: "assistant",
      content: "",
      timestamp: new Date().toISOString(),
      streaming: true,
      isSearchingWeb: scope === "web",
      scope,
      customDocIds: scope === "custom" ? customDocIds : undefined,
    }]);
    const updateAssistant = (patch: Partial<ChatMessage>) => {
      setChatHistory(prev => prev.map(m => m.id === assistantId ? { ...m, ...patch } : m));
    };

    try {
      let contextDocs: Document[] = [];
      if (scope === "all") {
        contextDocs = documents;
      } else if (scope === "local") {
        if (selectedDocId) {
          contextDocs = documents.filter(doc => doc.id === selectedDocId);
        } else if (selectedLibId) {
          contextDocs = documents.filter(doc => doc.libraryId === selectedLibId);
        }
      } else if (scope === "custom" && customDocIds) {
        contextDocs = documents.filter(doc => customDocIds.includes(doc.id));
      }

      const response = await fetch("/api/gemini/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: updatedHistory.slice(-40),
          documentContext: contextDocs,
          webSearchEnabled: scope === "web"
        })
      });

      if (!response.ok || !response.body) throw new Error("大模型链路响应异常。");
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buf = "";
      let fullText = "";
      let evidence: EvidenceItem[] = [];
      let groundingSources: { title: string; uri: string }[] = [];
      let webSupplemented = scope === "web";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let sepIdx;
        while ((sepIdx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, sepIdx);
          buf = buf.slice(sepIdx + 2);
          if (!raw.startsWith("data: ")) continue;
          const payload = raw.slice(6).trim();
          if (!payload) continue;
          let ev: any;
          try { ev = JSON.parse(payload); } catch { continue; }
          if (ev.type === "chunk" || ev.type === "text") {
            fullText = ev.type === "text" ? ev.content : fullText + ev.content;
            updateAssistant({ content: fullText });
          } else if (ev.type === "sources") {
            if (Array.isArray(ev.groundingSources)) groundingSources = ev.groundingSources;
            if (Array.isArray(ev.evidence)) evidence = ev.evidence;
            if (ev.supplement) webSupplemented = true;
          } else if (ev.type === "error") {
            throw new Error(ev.content || "流式回答失败");
          }
        }
      }
      updateAssistant({
        content: fullText || "（回答为空）",
        streaming: false,
        isSearchingWeb: false,
        groundingSources: (scope === "web" || webSupplemented) && groundingSources.length ? groundingSources : undefined,
        evidence: evidence.length ? evidence : undefined,
        webSupplemented: webSupplemented && scope !== "web" ? true : undefined,
      });
      // ⛔ 2026-08-14：显式保存最终态（含 evidence），不依赖防抖时序
      window.setTimeout(() => saveChatHistory(chatHistoryRef.current), 300);
    } catch (err: any) {
      console.error(err);
      const errMsg = err.message || "未知错误";
      if (errMsg.includes("429") || errMsg.includes("rate") || errMsg.includes("Rate")) {
        const until = Date.now() + 30000;
        setChatCooldownUntil(until);
        window.setTimeout(() => setChatCooldownUntil(0), 30000);
      }
      updateAssistant({
        content: `模型调用失败: ${errMsg}\n\n` +
          (errMsg.includes("429") || errMsg.includes("rate")
            ? "API 已触发限流保护，请等待 30 秒冷却后再试。后端已自动重试 3 次仍失败。"
            : "请在设置管理中检查 API Key 是否配置正确。"),
        streaming: false,
        isSearchingWeb: false,
      });
    } finally {
      setIsSendingMessage(false);
    }
  };

  // ⛔ 2026-08-13：追问建议（点击触发，不阻塞主回答）
  const [generatingFollowupsFor, setGeneratingFollowupsFor] = useState<string | null>(null);
  const handleGenerateFollowups = async (msgId: string) => {
    if (generatingFollowupsFor) return;
    const idx = chatHistory.findIndex(m => m.id === msgId);
    if (idx < 0) return;
    const msg = chatHistory[idx];
    const userMsg = [...chatHistory.slice(0, idx)].reverse().find(m => m.role === "user");
    if (!userMsg) return;
    setGeneratingFollowupsFor(msgId);
    try {
      const resp = await fetch("/api/gemini/followups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: userMsg.content, answer: msg.content }),
      });
      if (!resp.ok) throw new Error("追问生成失败");
      const data = await resp.json();
      setChatHistory(prev => prev.map(m => m.id === msgId ? { ...m, followUps: data.followUps || [] } : m));
    } catch (err: any) {
      console.error(err);
    } finally {
      setGeneratingFollowupsFor(null);
    }
  };

  const handleAskFollowUp = (text: string, msgId: string) => {
    const msg = chatHistory.find(m => m.id === msgId);
    const scope = msg?.scope || "all";
    const custom = msg?.scope === "custom" ? msg.customDocIds : undefined;
    handleSendMessage(text, scope, custom);
  };

  // ⛔ 2026-08-13：引用直达原文 —— 跳到文档并高亮证据句
  const [locateRequest, setLocateRequest] = useState<{ text: string; ts: number } | null>(null);
  const handleLocateEvidence = (evidence: EvidenceItem) => {
    const docId = evidence.physical_name || evidence.file_name;
    if (!docId) return;
    if (selectedDocId !== docId) {
      handleSelectDocument(docId);
    }
    switchView("documents");
    setLocateRequest({ text: evidence.text, ts: Date.now() });
  };

  const handleLookupAndAddTerm = async (termName: string) => {
    if (!termName.trim()) return;
    setIsDefiningTerm(true);
    
    const newId = `vocab-${Date.now()}`;
    const pendingCard: VocabularyTerm = {
      id: newId,
      term: termName.trim(),
      definition: "*正在通过 智能 AI 翻译并分析学术含义中...*",
      createdAt: new Date().toISOString(),
      status: "learning"
    };

    setVocabularyTerms(prev => {
      const filtered = prev.filter(t => t.term.toLowerCase() !== termName.trim().toLowerCase());
      return [pendingCard, ...filtered];
    });
    setActiveVocabId(newId);

    try {
      const resp = await fetch("/api/ai/define-term", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          term: termName.trim(), 
          context: activeDocument?.content || "" 
        })
      });
      if (!resp.ok) throw new Error("无法成功从查词代理层取得反馈。");
      const data = await resp.json();
      
      setVocabularyTerms(prev => prev.map(t => 
        t.id === newId ? { ...t, definition: data.definition } : t
      ));
    } catch (err: any) {
      console.error(err);
      setVocabularyTerms(prev => prev.map(t => 
        t.id === newId ? { ...t, definition: `智囊反馈失败: ${err.message || "由于外部原因请求失败"}` } : t
      ));
    } finally {
      setIsDefiningTerm(false);
    }
  };

  const handleClearHistory = () => {
    setChatHistory([]);
    localStorage.removeItem("kb_chat");
    clearChatHistory();
  };

  const handleDeleteMessage = (msgId: string) => {
    setChatHistory(prev => prev.filter(m => m.id !== msgId));
  };

  return (
    <div id="app-root-frame" className="flex h-screen w-screen bg-zinc-100/50 font-sans text-zinc-900 antialiased overflow-hidden">
      
      {/* EXTREME LEFT: Unified Hub Navigation Rail Sidebar */}
      <nav id="submenu-navigation-rail" className="w-[200px] shrink-0 bg-white border-r border-zinc-200 flex flex-col justify-between h-full select-none z-10">
        <div className="flex flex-col">
          {/* Logo Brand Emblem */}
          <div className="px-5 py-5 border-b border-zinc-100 bg-zinc-50/30">
            <div className="flex items-center gap-2.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0 shadow-sm shadow-emerald-500/50 animate-pulse" />
              <div>
                <h1 className="text-zinc-900 font-extrabold text-sm tracking-tight leading-none">AI 知识库</h1>
                <p className="text-[9px] font-mono text-emerald-600 font-bold uppercase tracking-wider mt-1">
                  Intellectual Hub
                </p>
              </div>
            </div>
          </div>

          {/* Core App Submenu Buttons */}
          <div className="px-3 py-4 space-y-1">
            <span className="text-[9px] font-extrabold text-zinc-400 uppercase tracking-widest px-3 block mb-2.5 font-mono">
              主视窗
            </span>
            
            {/* View Tab 1: Documents Workspace */}
            <button
               onClick={() => switchView("documents")}
              className={`w-full px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between transition-all text-left cursor-pointer ${
                currentView === "documents"
                  ? "bg-zinc-900 text-white shadow-md shadow-zinc-950/10"
                  : "text-zinc-650 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <div className="flex items-center gap-2">
                <FileText className={`w-3.5 h-3.5 ${currentView === "documents" ? "text-emerald-400" : "text-zinc-400"}`} />
                <span>文档管理</span>
              </div>
              <span className={`text-[10px] font-mono px-1.5 py-0.2 rounded-full ${currentView === "documents" ? "bg-zinc-800 text-zinc-300" : "bg-zinc-100 text-zinc-500"}`}>
                {documents.length}
              </span>
            </button>

            {/* View Tab 1.5: Document Path Scandisk & Cross Comparing */}
            <button
               onClick={() => {
                 switchView("compare");
                 setCompareSubView("scan");
               }}
              className={`w-full px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between transition-all text-left cursor-pointer ${
                currentView === "compare"
                  ? "bg-zinc-900 text-white shadow-md shadow-zinc-950/10"
                  : "text-zinc-650 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <div className="flex items-center gap-2">
                <FolderOpen className={`w-3.5 h-3.5 ${currentView === "compare" ? "text-emerald-400" : "text-zinc-400"}`} />
                <span>导入与比对</span>
              </div>
            </button>

            {/* View Tab 2: Holistic Knowledge Graph */}
            <button
              onClick={() => switchView("graph")}
              className={`w-full px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between transition-all text-left cursor-pointer ${
                currentView === "graph"
                  ? "bg-zinc-900 text-white shadow-md shadow-zinc-950/10"
                  : "text-zinc-650 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <div className="flex items-center gap-2">
                <Layers className={`w-3.5 h-3.5 ${currentView === "graph" ? "text-emerald-400" : "text-zinc-400"}`} />
                <span>知识网络</span>
              </div>
              <span className={`text-[10px] font-mono px-1.5 py-0.2 rounded-full ${currentView === "graph" ? "bg-zinc-800 text-zinc-300" : "bg-zinc-100 text-zinc-500"}`}>
                {nodes.length}
              </span>
            </button>

            {/* View Tab 3: Expanded AI Chat panel */}
            <button
              onClick={() => switchView("chat")}
              className={`w-full px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-2 transition-all text-left cursor-pointer ${
                currentView === "chat"
                  ? "bg-zinc-900 text-white shadow-md shadow-zinc-950/10"
                  : "text-zinc-650 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <MessageSquare className={`w-3.5 h-3.5 ${currentView === "chat" ? "text-emerald-400" : "text-zinc-400"}`} />
              <span>知识问答</span>
            </button>

            {/* View Tab 3.5: Knowledge Cards Workbench */}
            <button
              onClick={() => switchView("cards")}
              className={`w-full px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between transition-all text-left cursor-pointer ${
                currentView === "cards"
                  ? "bg-zinc-900 text-white shadow-md shadow-zinc-950/10"
                  : "text-zinc-650 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <div className="flex items-center gap-2 font-semibold">
                <Brain className={`w-3.5 h-3.5 ${currentView === "cards" ? "text-emerald-400" : "text-zinc-400"}`} />
                <span>记忆卡箱</span>
              </div>
              <span className={`text-[10px] font-mono px-1.5 py-0.2 rounded-full ${currentView === "cards" ? "bg-zinc-800 text-zinc-300" : "bg-zinc-100 text-zinc-500"}`}>
                {knowledgeCards.length}
              </span>
            </button>

            {/* View Tab 4: System Settings Page */}
            <button
              onClick={() => switchView("settings")}
              className={`w-full px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between transition-all text-left cursor-pointer ${
                currentView === "settings"
                  ? "bg-zinc-900 text-white shadow-md shadow-zinc-950/10"
                  : "text-zinc-650 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <div className="flex items-center gap-2">
                <Settings className={`w-3.5 h-3.5 ${currentView === "settings" ? "text-emerald-400" : "text-zinc-400"}`} />
                <span>设置管理</span>
              </div>
            </button>
          </div>
        </div>

        {/* Footer Connections Status Indicator */}
        <div className="p-4 border-t border-zinc-150 bg-zinc-50/50">
          <div className="flex items-center select-none">
            <div className="flex items-center gap-2 text-[11px] font-semibold text-zinc-500">
              <span className={`w-1.5 h-1.5 rounded-full ${
                serverHealthy === true ? "bg-emerald-500 animate-pulse bg-emerald-500" : "bg-orange-400"
              }`} />
              <span>{serverHealthy === true ? "AI 状态：正常" : "离线安全模式"}</span>
            </div>
          </div>

          {/* 扫描状态指示器 */}
          <ScanStatusIndicator />
        </div>
      </nav>

      {/* CENTRAL / MAIN ACTIVE CONTENT */}
      <div className="flex-1 flex flex-col h-screen min-w-0 overflow-hidden relative">
        
        {/* Dynamic Warning Notification bar if Key API is missing */}
        {!serverLoading && serverHealthy === false && (
          <div className="bg-zinc-100 border-b border-zinc-200 px-5 py-2 flex items-center justify-between text-[11px] text-zinc-600 select-none shrink-0 border-dashed">
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 shrink-0" />
              {/* ⛔ 2026-08-14（任务二十六）：不再提旧 Gemini 环境变量名，与现行 provider 配置一致 */}
              <span>当前处于本地离线模式。若需开启 AI 自动解析、脑图生成及闪卡提炼，可前往「设置管理」配置模型 API Key。</span>
            </div>
          </div>
        )}

        {/* WORKSPACE VIEW RENDERING SWITCHER */}
        <div className="flex-1 overflow-hidden min-h-0 relative">
          
          {/* 1. DOCUMENT STUDIO VIEW (📂 文档资产) */}
          {currentView === "documents" && (
            <div className="flex h-full w-full overflow-hidden animate-fadeIn">
              <Sidebar
                width={leftSidebarWidth}
                libraries={libraries}
                documents={documents}
                selectedLibId={selectedLibId}
                selectedDocId={selectedDocId}
                onSelectLibrary={handleSelectLibrary}
                onSelectDocument={handleSelectDocument}
                onAddLibrary={handleAddLibrary}
                onDeleteLibrary={handleDeleteLibrary}
                onUpdateLibrary={handleUpdateLibrary}
                onUpdateDocument={handleUpdateDocument}
                onDeleteDocument={handleDeleteDocument}
                onGoToScanning={() => {
                  switchView("compare");
                  setCompareSubView("scan");
                }}
                fetchDocuments={fetchDocuments}
              />
              
              {/* Divider resize handler */}
              <div
                className="w-px hover:w-1 active:w-1 h-full cursor-col-resize bg-zinc-200 hover:bg-zinc-400 active:bg-zinc-500 transition-all z-20 select-none shrink-0"
                title="拖拽调整左侧栏宽度"
                onMouseDown={(e) => {
                  e.preventDefault();
                  window.document.body.style.cursor = "col-resize";
                  window.document.body.style.userSelect = "none";
                  const startX = e.clientX;
                  const startWidth = leftSidebarWidth;
                  let rafId: number | null = null;
                  let latestClientX = startX;

                  const handleMouseMove = (moveEvent: MouseEvent) => {
                    latestClientX = moveEvent.clientX;
                    if (rafId === null) {
                      rafId = requestAnimationFrame(() => {
                        rafId = null;
                        const newWidth = startWidth + (latestClientX - startX);
                        setLeftSidebarWidth(Math.max(180, Math.min(newWidth, 480)));
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
              
              {/* Document Editor and preview block */}
              <main className="flex-1 h-full overflow-hidden flex flex-col min-w-0 bg-white">
                <div className="flex-1 h-full flex flex-col min-h-0">
                  <Suspense fallback={<div className="flex-1 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-400" /></div>}>
                    <ErrorBoundary fallbackTitle="文档编辑器出错">
                      <DocEditor
                        key={activeDocument?.id || 'empty'}
                        document={activeDocument || null}
                        onUpdateDocument={handleUpdateDocument}
                        onDeleteDocument={handleDeleteDocument}
                        onCreateExternalGraph={handleCreateExternalGraph}
                        onLookupAndAddTerm={handleLookupAndAddTerm}
                        isDefiningTerm={isDefiningTerm}
                        contentLoading={contentLoading}
                        cachedPreprocessed={contentCache.current[activeDocument?.id || ""]?.preprocessed}
                        cachedHeadings={contentCache.current[activeDocument?.id || ""]?.headings}
                        locateText={locateRequest ? locateRequest.text : null}
                        locateSeq={locateRequest ? locateRequest.ts : 0}
                      />
                    </ErrorBoundary>
                  </Suspense>
                </div>
              </main>
            </div>
          )}

          {/* 2. HOLISTIC INTERACTIVE NETWORK GRAPH VIEW (🕸️ 知识网络图谱) */}
          {currentView === "graph" && (
            <Suspense fallback={<div className="flex-1 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-400" /></div>}>
              <ErrorBoundary fallbackTitle="知识图谱出错">
                <GraphPanel
                libraries={libraries}
                documents={documents}
                nodes={nodes}
                setNodes={setNodes}
                edges={edges}
                setEdges={setEdges}
                graphSessions={graphSessions}
                setGraphSessions={setGraphSessions}
                activeSessionId={activeSessionId}
                setActiveSessionId={setActiveSessionId}
                deletedNodes={deletedNodes}
                setDeletedNodes={setDeletedNodes}
                deletedEdges={deletedEdges}
                setDeletedEdges={setDeletedEdges}
                handleAddNode={handleAddNode}
                handleDeleteNode={handleDeleteNode}
                handleAddEdge={handleAddEdge}
                handleDeleteEdge={handleDeleteEdge}
                handleRestoreNode={handleRestoreNode}
              />
              </ErrorBoundary>
            </Suspense>
          )}

          {/* 3. CENTERED RAG CHAT BOT VIEW (💬 智能问答) */}
          {currentView === "chat" && (
            <div className="h-full w-full p-5 bg-zinc-100/30 overflow-hidden animate-fadeIn flex justify-center">
              <div className="w-full max-w-6xl h-full flex flex-col">
                <Suspense fallback={<div className="flex-1 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-400" /></div>}>
                <ErrorBoundary fallbackTitle="智能问答出错">
                <ChatPanel
                  documents={documents}
                  libraries={libraries}
                  selectedLibId={selectedLibId}
                  selectedDocId={selectedDocId}
                  activeChatMessages={chatHistory}
                  onSendMessage={handleSendMessage}
                  onClearHistory={handleClearHistory}
                  onDeleteMessage={handleDeleteMessage}
                  onGenerateFollowups={handleGenerateFollowups}
                  onAskFollowUp={handleAskFollowUp}
                  onLocateEvidence={handleLocateEvidence}
                  generatingFollowupsFor={generatingFollowupsFor}
                  onNavigateToDoc={handleNavigateToDoc}
                  isSendingMessage={isSendingMessage}
                  isChatCoolingDown={isChatCoolingDown}
                  chatCooldownSeconds={chatCooldownSeconds}
                />
                </ErrorBoundary>
                </Suspense>
              </div>
            </div>
          )}

          {/* 4. SPACE ANALYTICS & SYSTEM SETTINGS VIEW (📊 空间设置卡及备份器) */}
          {currentView === "settings" && (
            <Suspense fallback={<div className="flex-1 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-400" /></div>}>
              <ErrorBoundary fallbackTitle="设置面板出错">
              <SettingsPanel
              serverHealthy={serverHealthy}
              setServerHealthy={setServerHealthy}
              healthMessage={healthMessage}
              serverLoading={serverLoading}
              apiProviders={apiProviders}
              setApiProviders={setApiProviders}
              selectedProvider={selectedProvider}
              setSelectedProvider={setSelectedProvider}
              activeProviderId={activeProviderId}
              setActiveProviderId={setActiveProviderId}
              activeModelName={activeModelName}
              setActiveModelName={setActiveModelName}
              activeKeyPreview={activeKeyPreview}
              setActiveKeyPreview={setActiveKeyPreview}
              currentProviderKeyPreview={currentProviderKeyPreview}
              setCurrentProviderKeyPreview={setCurrentProviderKeyPreview}
              currentProviderHasKey={currentProviderHasKey}
              setCurrentProviderHasKey={setCurrentProviderHasKey}
            />
              </ErrorBoundary>
            </Suspense>
          )}

          {/* 5. MULTI-DOCUMENT COMPARISON & ANALYSIS (📊 交叉对比) */}
          {currentView === "compare" && (
            <div className="h-full w-full p-5 bg-zinc-100/30 overflow-hidden animate-fadeIn flex flex-col">
              <Suspense fallback={<div className="flex-1 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-400" /></div>}>
                <ErrorBoundary fallbackTitle="文档比对出错">
                <DocComparison
                  documents={documents}
                  libraries={libraries}
                  onAddDocument={handleAddDocument}
                  onAddLibrary={handleAddLibrary}
                  defaultSubView={compareSubView}
                  fetchDocuments={fetchDocuments}
                />
                </ErrorBoundary>
              </Suspense>
            </div>
          )}

          {/* 6. KNOWLEDGE FLASHCARDS SYSTEM (🃏 知识人名卡/闪卡) */}
          {currentView === "cards" && (
            <div className="h-full w-full p-5 bg-zinc-100/30 overflow-hidden animate-fadeIn flex flex-col">
              <Suspense fallback={<div className="flex-1 flex items-center justify-center"><Loader2 className="w-6 h-6 animate-spin text-zinc-400" /></div>}>
                <ErrorBoundary fallbackTitle="知识卡片出错">
                <KnowledgeCards
                documents={documents}
                libraries={libraries}
                cards={knowledgeCards}
                onAddCard={handleAddKnowledgeCard}
                onUpdateCard={handleUpdateKnowledgeCard}
                onDeleteCard={handleDeleteKnowledgeCard}
                onImportCards={handleImportKnowledgeCards}
                onResetCards={handleResetKnowledgeCards}
              />
                </ErrorBoundary>
              </Suspense>
            </div>
          )}

        </div>

      </div>

    </div>
  );
}
