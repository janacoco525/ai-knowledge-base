import React, { useState, useEffect, useMemo } from "react";
import { Layers, Brain, Loader2, Sparkles, FolderOpen } from "lucide-react";
import { Library, Document, GraphNode, GraphEdge, NodeCategory } from "../types";
import KnowledgeGraph from "./KnowledgeGraph";
// T10：markmap-view 按需加载（2026-08-06）——脑图/框架树 tab 打开时才拉取 markmap chunk
import { lazyRetry } from "../lib/lazyRetry";
const MindMap = lazyRetry(() => import("./MindMap"));
import { useGraphFilters } from "../lib/useGraphFilters";

// 知识框架树节点（T29：与 MindMap 的 topic/children 结构兼容）
interface MindMapNodeLike {
  id: string;
  topic: string;
  isCollapsed?: boolean;
  children?: MindMapNodeLike[];
}

export interface GraphSession {
  id: string;
  docTitle: string;
  docId: string;
  timestamp: string;
  nodeCount: number;
  edgeCount: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface MindMapSession {
  id: string;
  docTitle: string;
  timestamp: string;
  data: any;
  docId?: string;
}

interface GraphPanelProps {
  libraries: Library[];
  documents: Document[];
  nodes: GraphNode[];
  setNodes: React.Dispatch<React.SetStateAction<GraphNode[]>>;
  edges: GraphEdge[];
  setEdges: React.Dispatch<React.SetStateAction<GraphEdge[]>>;
  graphSessions: GraphSession[];
  setGraphSessions: React.Dispatch<React.SetStateAction<GraphSession[]>>;
  activeSessionId: string | null;
  setActiveSessionId: React.Dispatch<React.SetStateAction<string | null>>;
  deletedNodes: GraphNode[];
  setDeletedNodes: React.Dispatch<React.SetStateAction<GraphNode[]>>;
  deletedEdges: {[nodeId: string]: GraphEdge[]};
  setDeletedEdges: React.Dispatch<React.SetStateAction<{[nodeId: string]: GraphEdge[]}>>;
  handleAddNode: (label: string, category: NodeCategory) => void;
  handleDeleteNode: (id: string) => void;
  handleAddEdge: (source: string, target: string, label: string) => void;
  handleDeleteEdge: (id: string) => void;
  handleRestoreNode: (id: string) => void;
}

export default function GraphPanel({
  libraries,
  documents,
  nodes,
  setNodes,
  edges,
  setEdges,
  graphSessions,
  setGraphSessions,
  activeSessionId,
  setActiveSessionId,
  deletedNodes,
  setDeletedNodes,
  deletedEdges,
  setDeletedEdges,
  handleAddNode,
  handleDeleteNode,
  handleAddEdge,
  handleDeleteEdge,
  handleRestoreNode,
}: GraphPanelProps) {
  // Tabs & Views
  const [graphSubTab, setGraphSubTab] = useState<"network" | "mindmap" | "tree">("network");

  // 通用型修复：useGraphFilters hook — 文档+目录筛选原子管理
  // graphFilterDocIds 和 graphFilterLibIds 永远同步（选中文档→自动勾选目录，反之亦然）
  // 统一持久化到 localStorage(key: kb_graph_filters_v2)，刷新/切标签完整恢复
  const {
    docIds: graphFilterDocIds,
    libIds: graphFilterLibIds,
    setSingleDoc,
    clearAll: clearFilterAll,
    selectAll: selectAllFilter,
    toggleDoc,
    toggleLib,
  } = useGraphFilters(documents, libraries);

  const [showLibDropdown, setShowLibDropdown] = useState(false);
  const [showDocDropdown, setShowDocDropdown] = useState(false);

  // ⛔ 2026-08-19：脑图本地缓存版本标记——后端算法版本变化（mindmap-v5，假卷误检
  // 修复）时，旧结构缓存（kb_mindmaps_v2）必须清掉，否则前端强刷仍显示旧坏脑图
  // （《2049》旧 v4 缓存只有 2 分支 6 节点的根因之二）。与后端缓存键 mindmap-v5 同步。
  const MINDSNAP_CACHE_VERSION = "v5";

  // Local Mind Map states
  const [mindmaps, setMindmaps] = useState<Record<string, any>>(() => {
    try {
      // ⛔ 2026-08-13：localStorage key 加版本号——此前脑图结构多次升级
      // （章节覆盖→分层→卷结构），旧 key 里存的是旧结构，强刷后前端
      // 直接加载旧会话，用户永远看不到新效果（"强刷还是不行"根因之二）。
      // ⛔ 2026-08-19：key 名不再变化（v2 沿用），改为独立版本标记——后端
      // 算法升级（mindmap-v5）时自动清旧缓存，避免 key 名长期不随算法同步。
      if (localStorage.getItem("kb_mindmaps_ver") !== MINDSNAP_CACHE_VERSION) {
        localStorage.removeItem("kb_mindmaps_v2");
        return {};
      }
      const saved = localStorage.getItem("kb_mindmaps_v2");
      if (!saved) return {};
      const parsed = JSON.parse(saved);
      // 过滤掉格式不正确的缓存数据
      const valid: Record<string, any> = {};
      for (const [k, v] of Object.entries(parsed)) {
        if (v && typeof v === "object" && (v as any).topic) valid[k] = v;
      }
      return valid;
    } catch { return {}; }
  });
  const [isGeneratingMindmap, setIsGeneratingMindmap] = useState(false);
  // ⛔ 2026-08-19：脑图生成进度文案（轮询后端 /progress 显示“第 N/M 章”
  // —— 中长篇首次生成无缓存需 3-5 分钟，无反馈用户以为卡死）
  const [mindmapProgress, setMindmapProgress] = useState("");
  const [mindmapSelectedDocId, setMindmapSelectedDocId] = useState<string>("");

  const [mindmapSessions, setMindmapSessions] = useState<MindMapSession[]>(() => {
    try {
      const s = localStorage.getItem("kb_mindmap_sessions_v2");
      return s ? JSON.parse(s) : [];
    } catch {
      return [];
    }
  });

  // 知识框架树状态（T29 前端接入，2026-08-05）
  const [treeData, setTreeData] = useState<MindMapNodeLike | null>(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [treeError, setTreeError] = useState("");
  const [treeTitle, setTreeTitle] = useState("");
  const [treeInsights, setTreeInsights] = useState<string[]>([]);
  const [treeMeta, setTreeMeta] = useState<{ file_count?: number; chunk_count?: number } | null>(null);

  // 后端 tree 数据 → MindMapNode 结构转换（label→topic）
  const toMindMapNode = (node: any): MindMapNodeLike | null => {
    if (!node) return null;
    return {
      id: node.id || "",
      topic: node.label || node.topic || "",
      children: (node.children || []).filter(Boolean).map(toMindMapNode).filter(Boolean) as MindMapNodeLike[],
    };
  };

  const handleGenerateTree = async () => {
    // 知识框架树基于当前筛选的文档生成；无筛选时用全库
    const targetDocIds = graphFilterDocIds.length > 0
      ? graphFilterDocIds
      : (graphFilterLibIds.length > 0
          ? documents.filter(d => graphFilterLibIds.includes(d.libraryId)).map(d => d.id)
          : undefined);
    setTreeLoading(true);
    setTreeError("");
    try {
      const resp = await fetch("/api/knowledge-tree/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_ids: targetDocIds || null,
          max_depth: 3,
          focus_area: undefined,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        setTreeError(`生成失败: ${err.detail || `HTTP ${resp.status}`}`);
        return;
      }
      const data = await resp.json();
      const root = data.tree?.root;
      if (!root) {
        setTreeError("后端返回的框架树为空");
        return;
      }
      setTreeData(toMindMapNode(root));
      setTreeTitle(data.title || "知识框架树");
      setTreeInsights(data.insights || []);
      setTreeMeta(data.meta || null);
    } catch (e: any) {
      setTreeError(`生成失败: ${e.message || e}`);
    } finally {
      setTreeLoading(false);
    }
  };

  // Sync mindmaps changes
  useEffect(() => {
    localStorage.setItem("kb_mindmaps_v2", JSON.stringify(mindmaps));
    localStorage.setItem("kb_mindmaps_ver", MINDSNAP_CACHE_VERSION);
  }, [mindmaps]);

  // T11：进入图谱页时后台预热 live 图谱缓存（fire-and-forget，不阻塞 UI，失败静默）
  useEffect(() => {
    fetch("/api/graph/prewarm", { method: "POST" }).catch(() => {});
  }, []);

  // ⛔ 2026-08-19：清理历史遗留垃圾 key kb_graph_sessions_v2（旧版删除逻辑误写，无人读取）
  useEffect(() => {
    localStorage.removeItem("kb_graph_sessions_v2");
  }, []);

  // Click outside dropdowns
  useEffect(() => {
    if (!showLibDropdown && !showDocDropdown) return;
    const handler = (e: MouseEvent) => {
      const el = e.target as HTMLElement;
      if (!showLibDropdown && showDocDropdown && !el.closest("[data-doc-dropdown]")) setShowDocDropdown(false);
      if (showLibDropdown && !showDocDropdown && !el.closest("[data-lib-dropdown]")) setShowLibDropdown(false);
      if (showLibDropdown && showDocDropdown) {
        if (!el.closest("[data-lib-dropdown]")) setShowLibDropdown(false);
        if (!el.closest("[data-doc-dropdown]")) setShowDocDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showLibDropdown, showDocDropdown]);

  const handleLibToggle = (libId: string) => {
    toggleLib(libId);
  };


  const [isLoadingKBGraph, setIsLoadingKBGraph] = useState(false);
  // 图谱提取模式：llm-first / rules-fallback；降级时顶栏显示醒目提示，避免再次“静默降级”无人发现
  const [graphExtractMode, setGraphExtractMode] = useState<string>("");
  const handleLoadFromKB = async (forceLLM = false) => {
    setIsLoadingKBGraph(true);
    try {
      // ── 关键：根据用户在右侧筛选器勾选的文档，传 single_file 给后端 ──
      const targetDocIds = graphFilterDocIds.length > 0
        ? graphFilterDocIds
        : (graphFilterLibIds.length > 0
            ? documents.filter(d => graphFilterLibIds.includes(d.libraryId)).map(d => d.id)
            : []);  // 空 = 后端走全库混合
      const singleFile = targetDocIds.length === 1 ? targetDocIds[0] : null;
      // 通用型修复：全库加载时 effectiveDocId 也要有值（用第一个 doc 或 first doc of first lib），
      // 避免后续 filter 全部清空边和勾选
      let effectiveDocId = singleFile || "";
      if (!effectiveDocId && targetDocIds.length > 1) {
        effectiveDocId = targetDocIds[0] || "";
      }
      // 单文档（尤其长文档）需要更多节点才能覆盖全书内容；全库混合保持 20 防拥挤
      const maxNodes = singleFile ? 36 : 20;
      const forceParam = forceLLM ? "&force_llm=1" : "";
      const url = singleFile
        ? `/api/graph/data?source_mode=auto&max_nodes=${maxNodes}&selection_profile=balanced&single_file=${encodeURIComponent(singleFile)}${forceParam}`
        : `/api/graph/data?source_mode=auto&max_nodes=${maxNodes}&selection_profile=balanced${forceParam}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.detail) throw new Error(data.detail);
      // 记录提取模式：降级时界面可见（可观测性修复 2026-08-06）
      setGraphExtractMode(data?.meta?.extractor_mode || "");
      // 通用型修复：从真实 document 推导 libraryId，避免硬编码 "lib-ai" 导致 filter 不匹配
      const singleDoc = singleFile ? documents.find(d => d.id === singleFile) : null;
      const resolvedLibraryId = singleDoc?.libraryId || (documents[0]?.libraryId ?? "all");
      const kbNodes = (data.nodes || [])
        .filter((n: any) => n && typeof n.label === "string" && n.label.trim())
        .map((n: any, i: number) => ({
          id: `kb-${n.id || `n${i}`}`,
          label: n.label.trim(),
          category: (n.category || n.type || "concept") as NodeCategory,
          docId: effectiveDocId,
          libraryId: resolvedLibraryId,
          // ⛔ 2026-08-13：透传提取端 god score（weight），否则前端只能靠图内度猜核心
          weight: typeof n.weight === "number" ? n.weight : undefined,
        }));
      if (kbNodes.length === 0) {
        alert("知识库暂无数据，请先导入文档");
        return;
      }
      const kbEdges = (data.edges || [])
        .filter((e: any) => e && (e.source ?? e.from) && (e.target ?? e.to))
        .map((e: any, i: number) => {
          let label = (e.label || "").trim();
          const VAGUE = new Set(["相关", "关联", "有关系", "联系", "有关", "有关联", "relation", "related", ""]);
          if (VAGUE.has(label.toLowerCase()) || VAGUE.has(label)) {
            const srcNode = kbNodes.find((n: any) => n.id === `kb-${e.source ?? e.from}`);
            const tgtNode = kbNodes.find((n: any) => n.id === `kb-${e.target ?? e.to}`);
            const src = srcNode?.label ?? e.source ?? e.from;
            const tgt = tgtNode?.label ?? e.target ?? e.to;
            label = `${src}→${tgt}`.slice(0, 14);
          }
          return {
            id: `kb-${e.id || `e${i}`}`,
            source: `kb-${e.source ?? e.from}`,
            target: `kb-${e.target ?? e.to}`,
            label,
            docId: effectiveDocId,  // 通用型修复：edges 也要带 docId，否则 filter 会清掉所有边
            libraryId: resolvedLibraryId,
          };
        });
      const validIds = new Set(kbNodes.map((n: any) => n.id));
      const filteredEdges = kbEdges.filter((e: any) => validIds.has(e.source) && validIds.has(e.target));
      // ⛔ 2026-08-12：这里【不能】先 setNodes/setEdges——它们会更新"当前激活的旧会话"，
      // 导致旧生成历史被新图谱覆盖。新会话自带 nodes/edges，append 后直接切换激活即可。
      const gsId = `gs-kb-${Date.now()}`;
      // 通用型修复：标题反映实际数据源
      // - 单文档：显示"《文档名》图谱"
      // - 多文档/全库：显示"知识库全局图谱"
      let title: string;
      if (singleFile) {
        const doc = documents.find(d => d.id === singleFile);
        title = doc ? `《${doc.title || doc.id}》图谱` : "单文档图谱";
      } else {
        title = "知识库全局图谱";
      }
      setGraphSessions(prev => {
        const s: GraphSession = {
          id: gsId,
          docTitle: title,
          docId: effectiveDocId,
          timestamp: new Date().toISOString(),
          nodeCount: kbNodes.length,
          edgeCount: filteredEdges.length,
          nodes: kbNodes,
          edges: filteredEdges,
        };
        return [s, ...prev.filter(p => p.id !== gsId)].slice(0, 30); // 新 session 放最前，保留所有历史
      });
      setActiveSessionId(gsId);
      // 通用型修复：加载后自动同步 filter。
      // 全库加载（effectiveDocId=""）：不设任何 filter，让"无筛选=全显"路径生效
      // 单文档加载：同时设 docIds 和 libIds，让目录和文档都显示勾选
      if (effectiveDocId) {
        const doc = documents.find(d => d.id === effectiveDocId);
        if (doc) {
          setSingleDoc(doc.id);  // 原子操作：同时设文档+目录
        }
      }
    } catch (err: any) {
      console.error("KB graph load failed:", err);
      alert("图谱加载失败: " + (err.message || err));
    } finally {
      setIsLoadingKBGraph(false);
    }
  };

  const handleGenerateMindmap = async (docId: string) => {
    const docToProcess = documents.find(d => d.id === docId);
    if (!docToProcess) {
      alert("请先选择一篇文档！");
      return;
    }

    // 确保文档正文已加载（用户可能从未打开过该文档）
    if (!docToProcess.content || docToProcess.content.trim() === "") {
      try {
        const resp = await fetch(`/api/kb/files/${encodeURIComponent(docToProcess.id)}/text`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.text) {
            docToProcess.content = data.text;
          }
        }
      } catch (e) {
        console.warn(`[mindmap] 加载正文失败 ${docToProcess.id}:`, e);
      }
    }

    if (!docToProcess.content || docToProcess.content.trim() === "") {
      alert("无法获取文档正文，请先打开该文档再生成脑图。");
      return;
    }

    setIsGeneratingMindmap(true);
    // ⛔ 2026-08-19：启动进度轮询（2.5s 间隔）——后端长文档多章任务会推进
    // done/total；缓存命中时主请求秒回，轮询最多跑 1 次，无感停止。
    const progressKey = docToProcess.id || docToProcess.title;
    setMindmapProgress("正在分析文档结构...");
    const pollId = setInterval(async () => {
      try {
        const pr = await fetch(`/api/gemini/mindmap/progress?file_id=${encodeURIComponent(progressKey)}`);
        const pd = await pr.json();
        if (pd.finished) {
          setMindmapProgress("");
        } else if (pd.phase === "chapters" && pd.total > 0) {
          setMindmapProgress(`正在生成脑图... 第 ${pd.done}/${pd.total} 章（${pd.pct}%）`);
        } else if (pd.phase === "structure") {
          setMindmapProgress("正在分析文档结构...");
        }
      } catch { /* 轮询失败静默，不打扰主流程 */ }
    }, 2500);

    try {
      // 超长文档传全文，由后端分层处理（章节切片 + 并行提取），不再前端截断
      // 2026-08-06 修复：此前截断 30000 字导致超长文档脑图只覆盖开头
      const rawContent = docToProcess.content || "";

      const ctrl = new AbortController();
      // ⛔ 2026-08-19：3 分钟 → 10 分钟超时——长文档最多 48 章任务 × 6 并发 ×
      // 单次 LLM 调用 30s ≈ 4-5 分钟；旧 180s 超时把新电脑首次生成（无缓存）直接掐断，
      // 用户看到的是“等待 3 分钟 → 报错”，误以为功能特别慢/卡死。
      const timeoutId = setTimeout(() => ctrl.abort(), 600000);

      const response = await fetch("/api/gemini/generate-mindmap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: docToProcess.title,
          content: rawContent,
          fileId: docToProcess.id,
        }),
        signal: ctrl.signal,
      });
      clearTimeout(timeoutId);

      if (!response.ok) throw new Error(`服务响应异常 (${response.status})`);
      const data = await response.json();

      if (data.mindmap && data.mindmap.topic) {
        let nodeCounter = 0;
        const addNodeIds = (node: any): any => {
          if (!node) return null;
          nodeCounter++;
          const childrenMapped = (node.children || []).filter(Boolean).map((c: any) => addNodeIds(c)).filter(Boolean);
          return {
            id: `mm-node-${nodeCounter}-${Date.now()}`,
            topic: node.topic || "重点要素",
            isCollapsed: false,
            children: childrenMapped
          };
        };

        const fullyMappedMindmap = addNodeIds(data.mindmap);
        
        setMindmaps(prev => ({
          ...prev,
          [docId]: fullyMappedMindmap
        }));
        handleSaveMindmapSession(docToProcess.title, fullyMappedMindmap, docToProcess.id);
      } else {
        console.error("[mindmap] Invalid response structure:", data);
        throw new Error("大模型返回空解构，请确认该文档含有充沛的篇幅。");
      }
    } catch (err: any) {
      console.error(err);
      if (err?.name === "AbortError") {
        // ⛔ 2026-08-19：10 分钟仍未完成（极端超长文档/API 慢）——后端线程仍在跑，
        // 完成后会写缓存；提示用户稍后再点一次即可秒读缓存结果。
        alert("生成超时（超过 10 分钟）：文档很长且未命中缓存，后端仍在后台继续生成，\n请稍后再点一次「生成脑图」——完成时会直接读取缓存结果，无需重新等待。");
      } else {
        alert(`无法成功完成文档知识拆解: ${err.message || err}`);
      }
    } finally {
      clearInterval(pollId);
      setMindmapProgress("");
      setIsGeneratingMindmap(false);
    }
  };

  const handleSaveMindmapSession = (title: string, data: any, docId?: string) => {
    setMindmapSessions(prev => {
      const s: MindMapSession = { id: `ms-${Date.now()}`, docTitle: title, timestamp: new Date().toISOString(), data, docId };
      const next = [s, ...prev].slice(0, 20);
      localStorage.setItem("kb_mindmap_sessions_v2", JSON.stringify(next));
      return next;
    });
  };

  const handleDeleteMindmapSession = (id: string) => {
    setMindmapSessions(prev => {
      const next = prev.filter(s => s.id !== id);
      localStorage.setItem("kb_mindmap_sessions_v2", JSON.stringify(next));
      return next;
    });
  };

  const handleLoadMindmapSession = (data: any, docId?: string) => {
    // 防御：确保 data 是合法的 mindmap 格式
    if (data && typeof data === "object" && data.topic && Array.isArray(data.children)) {
      // ⛔ 2026-08-12：按会话原始 docId 归位显示，避免"切到文档B后点历史A → 显示错内容"
      const targetDocId = docId || mindmapSelectedDocId;
      if (docId) setMindmapSelectedDocId(docId);
      setMindmaps(prev => ({ ...prev, [targetDocId]: data }));
    }
  };

  const handleLoadGraphSession = (sessionId: string) => {
    const session = graphSessions.find(s => s.id === sessionId);
    if (!session) return;
    // ⛔ 2026-08-12：不能先 setNodes/setEdges（会覆盖当前激活会话）；会话自带数据，直接切换激活
    setActiveSessionId(session.id);
    // 通用型修复：用 setSingleDoc 原子操作，同时恢复文档和目录勾选
    if (session.docId && documents.find(d => d.id === session.docId)) {
      setSingleDoc(session.docId);
    } else {
      clearFilterAll();
    }
  };

  const handleDeleteGraphSession = (sessionId: string) => {
    // ⛔ 2026-08-19：删除只更新 state（App 的 useEffect 负责写 kb_graph_sessions + 同步清备份）；
    // 原多余写入的 kb_graph_sessions_v2 无人读取，已移除并在组件挂载时清理。
    setGraphSessions(prev => prev.filter(s => s.id !== sessionId));
  };

  const fullScreenGraphNodes = useMemo(() => {
    if (graphFilterDocIds.length === 0 && graphFilterLibIds.length === 0) return nodes;
    let filtered = nodes;
    if (graphFilterDocIds.length > 0) {
      filtered = filtered.filter(n => graphFilterDocIds.includes(n.docId));
    } else if (graphFilterLibIds.length > 0) {
      filtered = filtered.filter(n => graphFilterLibIds.includes(n.libraryId));
    }
    return filtered;
  }, [nodes, graphFilterDocIds, graphFilterLibIds]);

  const fullScreenGraphEdges = useMemo(() => {
    if (graphFilterDocIds.length === 0 && graphFilterLibIds.length === 0) return edges;
    let filtered = edges;
    if (graphFilterDocIds.length > 0) {
      filtered = filtered.filter(e => graphFilterDocIds.includes(e.docId));
    } else if (graphFilterLibIds.length > 0) {
      filtered = filtered.filter(e => graphFilterLibIds.includes(e.libraryId));
    }
    return filtered;
  }, [edges, graphFilterDocIds, graphFilterLibIds]);

  // 知识框架树来源文档数：与 handleGenerateTree 同规则（选中文档→选中目录→全库）
  const treeSrcCount = graphFilterDocIds.length > 0
    ? graphFilterDocIds.length
    : (graphFilterLibIds.length > 0
        ? documents.filter(d => graphFilterLibIds.includes(d.libraryId)).length
        : 0);

  return (
    <div className="flex flex-col h-full w-full p-3 bg-zinc-100/30 overflow-hidden animate-fadeIn">
      {/* Top View Selector Panel */}
      <div className="bg-white border border-zinc-200 rounded-xl px-4 py-2 shrink-0 flex flex-wrap justify-between items-center gap-3 shadow-sm select-none">
        <div className="flex items-center gap-4">
          <div className="flex bg-zinc-100/80 p-0.5 rounded-lg border border-zinc-200">
            <button
              onClick={() => setGraphSubTab("network")}
              type="button"
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all flex items-center gap-1.5 cursor-pointer ${
                graphSubTab === "network"
                  ? "bg-white text-emerald-700 shadow-xs"
                  : "text-zinc-500 hover:text-zinc-800"
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              知识网络图谱
            </button>
            <button
              onClick={() => {
                setGraphSubTab("mindmap");
                if (!mindmapSelectedDocId && documents.length > 0) {
                  setMindmapSelectedDocId(documents[0].id);
                }
              }}
              type="button"
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all flex items-center gap-1.5 cursor-pointer ${
                graphSubTab === "mindmap"
                  ? "bg-white text-emerald-700 shadow-xs"
                  : "text-zinc-500 hover:text-zinc-800"
              }`}
            >
              <Brain className="w-3.5 h-3.5 text-emerald-500" />
              知识大纲脑图
            </button>
            {/* 知识框架树入口已下线（2026-08-06 用户决定，功能代码完整保留，见 TODO T41）：
                恢复时取消注释即可，tree 状态/生成逻辑/内容面板均保留可用 */}
            {/* <button
              onClick={() => setGraphSubTab("tree")}
              type="button"
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all flex items-center gap-1.5 cursor-pointer ${
                graphSubTab === "tree"
                  ? "bg-white text-emerald-700 shadow-xs"
                  : "text-zinc-500 hover:text-zinc-800"
              }`}
            >
              <Sparkles className="w-3.5 h-3.5 text-emerald-500" />
              知识框架树
            </button> */}
          </div>
        </div>

        {/* 右侧控件统一包在一个容器里：否则父级 justify-between 会把多个条件块撑到两端（用户反馈“挪太开”） */}
        <div className="flex items-center gap-3">
        {/* Sub-tab: tree */}
        {graphSubTab === "tree" && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-zinc-400 shrink-0">
              来源：{treeSrcCount > 0 ? `已勾选 ${treeSrcCount} 篇文档` : "全库文档（至多前 10 篇）"}
            </span>
            <button
              onClick={handleGenerateTree}
              disabled={treeLoading}
              className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:bg-zinc-200 text-white text-[10px] font-bold rounded-lg flex items-center gap-1 transition-all shrink-0"
            >
              {treeLoading ? (
                <><Loader2 className="w-3 h-3 animate-spin" /> 生成中...</>
              ) : (
                <><Sparkles className="w-3 h-3" /> 生成知识框架树</>
              )}
            </button>
            {treeTitle && (
              <span className="text-[10px] text-zinc-400 truncate max-w-[280px]">
                {treeTitle}{treeMeta ? ` · 基于 ${treeMeta.file_count} 篇/${treeMeta.chunk_count} 段` : ""}
              </span>
            )}
          </div>
        )}

        {/* Sub-tab: network（实体统计 + 加载按钮） */}
        {graphSubTab === "network" && (
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-zinc-400 shrink-0">
              {fullScreenGraphNodes.length > 0
                ? `${fullScreenGraphNodes.length}实体·${fullScreenGraphEdges.length}关系`
                : "暂无图谱"}
            </span>
            {graphExtractMode === "rules-fallback" && (
              <>
                <span className="px-2 py-0.5 bg-amber-100 border border-amber-300 text-amber-700 text-[10px] font-bold rounded-md shrink-0">
                  ⚠ 降级模式（LLM 未成功，当前为规则提取）
                </span>
                <button
                  onClick={() => handleLoadFromKB(true)}
                  disabled={isLoadingKBGraph}
                  className="px-2 py-0.5 bg-orange-500 hover:bg-orange-600 disabled:bg-zinc-200 text-white text-[10px] font-bold rounded-md shrink-0 transition-all"
                >
                  {isLoadingKBGraph ? "重试中..." : "重试 LLM"}
                </button>
              </>
            )}
            <button
              onClick={() => handleLoadFromKB()}
              disabled={isLoadingKBGraph}
              className="px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:bg-zinc-200 text-white text-[10px] font-bold rounded-lg flex items-center gap-1 transition-all shrink-0"
            >
              {isLoadingKBGraph ? (
                <><Loader2 className="w-3 h-3 animate-spin" /> 加载中...</>
              ) : (
                <><Layers className="w-3 h-3" /> 知识库图谱</>
              )}
            </button>
          </div>
        )}

        {/* 目录/文档筛选：网络图谱与知识框架树共用（框架树生成来源 = 当前勾选，不勾默认全库） */}
        {(graphSubTab === "network" || graphSubTab === "tree") && (
          <div className="flex items-center gap-3">
            {/* Library Dropdown Panel */}
            <div className="relative" data-lib-dropdown>
              <button
                onClick={() => { setShowLibDropdown(!showLibDropdown); setShowDocDropdown(false); }}
                type="button"
                className={`min-w-[105px] px-2.5 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1 border cursor-pointer whitespace-nowrap ${
                  graphFilterLibIds.length > 0
                    ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                    : "bg-white border-zinc-250 hover:bg-zinc-50 text-zinc-600"
                }`}
              >
                <span className="text-[10px] text-zinc-400 font-mono shrink-0">目录:</span>
                <span className="shrink-0">
                  {graphFilterLibIds.length === 0
                    ? (graphFilterDocIds.length > 0 ? "按文档" : "未筛选")
                    : `${graphFilterLibIds.length} 个`}
                </span>
                <span className="text-[9px] text-zinc-400 ml-0.5">{showLibDropdown ? "▲" : "▼"}</span>
              </button>
              {showLibDropdown && (
                <div className="absolute top-full mt-1 left-0 bg-white border border-zinc-200 rounded-lg shadow-lg z-50 w-56 max-h-60 overflow-y-auto py-1">
                  <div className="flex gap-1 px-2 py-1 border-b border-zinc-100">
                    <button onClick={() => selectAllFilter()}
                      className="flex-1 text-[10px] py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 text-zinc-600 font-bold cursor-pointer">全选</button>
                    <button onClick={() => clearFilterAll()}
                      className="flex-1 text-[10px] py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 text-zinc-500 font-bold cursor-pointer">清除</button>
                  </div>
                  {libraries.map(lib => {
                    const count = documents.filter(d => d.libraryId === lib.id).length;
                    return (
                      <label key={lib.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-50 cursor-pointer text-xs">
                        <input
                          type="checkbox"
                          checked={graphFilterLibIds.includes(lib.id)}
                          onChange={() => toggleLib(lib.id)}
                          className="shrink-0"
                        />
                        <span className="flex-1">{lib.name}</span>
                        <span className="text-[10px] text-zinc-400 font-mono">{count}篇</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Document Dropdown Panel */}
            <div className="relative" data-doc-dropdown>
              <button
                onClick={() => { setShowDocDropdown(!showDocDropdown); setShowLibDropdown(false); }}
                type="button"
                className={`min-w-[105px] px-2.5 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-1 border cursor-pointer whitespace-nowrap ${
                  graphFilterDocIds.length > 0
                    ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                    : "bg-white border-zinc-250 hover:bg-zinc-50 text-zinc-600"
                }`}
              >
                <span className="text-[10px] text-zinc-400 font-mono shrink-0">文档:</span>
                <span className="shrink-0">
                  {graphFilterDocIds.length === 0 ? "未筛选" : `${graphFilterDocIds.length} 篇`}
                </span>
                <span className="text-[9px] text-zinc-400 ml-0.5">{showDocDropdown ? "▲" : "▼"}</span>
              </button>
              {showDocDropdown && (
                <div className="absolute top-full mt-1 right-0 bg-white border border-zinc-200 rounded-lg shadow-lg z-50 w-64 max-h-72 overflow-y-auto py-1">
                  <div className="flex gap-1 px-2 py-1 border-b border-zinc-100">
                    <button onClick={() => selectAllFilter()}
                      className="flex-1 text-[10px] py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 text-zinc-600 font-bold cursor-pointer">全选</button>
                    <button onClick={() => clearFilterAll()}
                      className="flex-1 text-[10px] py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 text-zinc-500 font-bold cursor-pointer">清除</button>
                  </div>
                  {documents
                    .filter(d => graphFilterLibIds.length === 0 || graphFilterLibIds.includes(d.libraryId))
                    .map(doc => {
                      const libName = libraries.find(l => l.id === doc.libraryId)?.name || "";
                      return (
                        <label key={doc.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-50 cursor-pointer text-xs">
                          <input
                            type="checkbox"
                            checked={graphFilterDocIds.includes(doc.id)}
                            onChange={() => toggleDoc(doc.id)}
                            className="shrink-0"
                          />
                          <span className="flex-1 truncate">{doc.title}</span>
                          <span className="text-[10px] text-zinc-400 font-mono shrink-0">{libName}</span>
                        </label>
                      );
                    })}
                </div>
              )}
            </div>

          </div>
        )}

        {/* Mindmap tab */}
        {graphSubTab === "mindmap" && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-450 font-bold font-mono">选择要拆解的文档:</span>
            <select
              value={mindmapSelectedDocId}
              onChange={(e) => setMindmapSelectedDocId(e.target.value)}
              className="px-2.5 py-1.5 bg-white border border-zinc-250 text-zinc-805 text-xs rounded-lg focus:outline-none focus:border-emerald-500 font-semibold shadow-sm max-w-[200px] truncate"
            >
              {documents.map(d => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </div>
        )}
        </div>
      </div>

      {/* Tab A Content Panel (Cytoscape network topology graph) */}
      {graphSubTab === "network" && (
        <div className="flex-1 bg-white border border-zinc-200 p-1.5 rounded-xl shadow-sm min-h-0 relative flex flex-col">
          <KnowledgeGraph
            nodes={fullScreenGraphNodes}
            edges={fullScreenGraphEdges}
            libraryId={graphFilterLibIds.length > 0 ? graphFilterLibIds[0] : "all"}
            onAddNode={handleAddNode}
            onDeleteNode={handleDeleteNode}
            onAddEdge={handleAddEdge}
            onDeleteEdge={handleDeleteEdge}
            deletedNodes={deletedNodes}
            graphSessions={graphSessions}
            onLoadSession={handleLoadGraphSession}
            onDeleteSession={handleDeleteGraphSession}
            onRestoreNode={handleRestoreNode}
            onSelectNode={(_node) => {}}
          />
        </div>
      )}

      {/* Tab B Content Panel (D3 mind map) */}
      {graphSubTab === "mindmap" && (
        <div className="flex-1 min-h-0 flex flex-col">
          {(() => {
            const activeMMDoc = documents.find(d => d.id === mindmapSelectedDocId);
            if (!activeMMDoc) {
              return (
                <div className="w-full h-full bg-white border border-zinc-200 rounded-xl flex items-center justify-center p-6 text-zinc-400 text-xs">
                  请在上方选择一篇文档进行拆解研究。
                </div>
              );
            }
            // 诊断: 显示当前数据状态
            const existingMM = mindmaps[activeMMDoc.id];
            if (!existingMM) {
              return (
                <div className="w-full h-full bg-white border border-zinc-200 rounded-xl flex flex-col items-center justify-center p-6 gap-3">
                  <p className="text-zinc-500 text-sm">未找到《{activeMMDoc.title}》的脑图缓存</p>
                  <p className="text-zinc-400 text-xs">
                    文档ID: {activeMMDoc.id} | 
                    已缓存文档数: {Object.keys(mindmaps).length} |
                    内容长度: {activeMMDoc.content?.length || 0}
                  </p>
                  <button
                    onClick={() => handleGenerateMindmap(activeMMDoc.id)}
                    disabled={isGeneratingMindmap}
                    className="px-4 py-2 bg-emerald-500 text-white text-xs rounded hover:bg-emerald-600 disabled:opacity-50"
                  >
                    {isGeneratingMindmap ? "生成中..." : "生成脑图"}
                  </button>
                </div>
              );
            }
            return (
              <React.Suspense fallback={
                <div className="w-full h-full bg-white border border-zinc-200 rounded-xl flex items-center justify-center text-zinc-400 text-xs">
                  加载脑图组件…
                </div>
              }>
                <MindMap
                  documentTitle={activeMMDoc.title}
                  documentContent={activeMMDoc.content}
                  mindmapData={mindmaps[activeMMDoc.id] || null}
                  isLoading={isGeneratingMindmap}
                  progressText={isGeneratingMindmap ? mindmapProgress : undefined}
                  onGenerate={() => handleGenerateMindmap(activeMMDoc.id)}
                  mindmapSessions={mindmapSessions}
                  onLoadMindmapSession={handleLoadMindmapSession}
                  onDeleteMindmapSession={handleDeleteMindmapSession}
                  onUpdateMindmap={(newMM) => {
                    setMindmaps(prev => ({
                      ...prev,
                      [activeMMDoc.id]: newMM
                    }));
                  }}
                />
              </React.Suspense>
            );
          })()}
        </div>
      )}

      {/* Tab C Content Panel (知识框架树, markmap 渲染) */}
      {graphSubTab === "tree" && (
        <div className="flex-1 min-h-0 flex flex-col">
          {treeError ? (
            <div className="w-full h-full bg-white border border-zinc-200 rounded-xl flex flex-col items-center justify-center p-6 gap-2 text-zinc-400 text-xs">
              <p className="text-rose-500 text-sm">{treeError}</p>
              <p>可尝试在右侧筛选器勾选文档后重新生成</p>
            </div>
          ) : !treeData ? (
            <div className="w-full h-full bg-white border border-zinc-200 rounded-xl flex flex-col items-center justify-center p-6 gap-3">
              <p className="text-zinc-500 text-sm">知识框架树</p>
              <p className="text-zinc-400 text-xs">
                先用右上角“目录/文档”筛选器勾选要分析的文档（不勾选默认全库至多前 10 篇），再点击“生成知识框架树”
              </p>
              {graphFilterDocIds.length === 0 && graphFilterLibIds.length === 0 && (
                <p className="text-zinc-400 text-[10px]">未筛选文档时将基于全库生成</p>
              )}
            </div>
          ) : (
            <div className="flex-1 min-h-0 flex flex-col">
              <React.Suspense fallback={
                <div className="w-full h-full bg-white border border-zinc-200 rounded-xl flex items-center justify-center text-zinc-400 text-xs">
                  加载脑图组件…
                </div>
              }>
                <MindMap
                  documentTitle={treeTitle || "知识框架树"}
                  documentContent=""
                  mindmapData={treeData}
                  isLoading={treeLoading}
                  onGenerate={handleGenerateTree}
                  mindmapSessions={[]}
                  onUpdateMindmap={() => {}}
                />
              </React.Suspense>
              {treeInsights.length > 0 && (
                <div className="px-4 py-2 border-t border-zinc-100 bg-white/60">
                  <p className="text-[10px] font-bold text-zinc-500 mb-1">洞察</p>
                  <ul className="text-[10px] text-zinc-500 space-y-0.5">
                    {treeInsights.map((ins, i) => <li key={i}>• {ins}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
