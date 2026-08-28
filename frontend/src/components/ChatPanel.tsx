import React, { useState, useRef, useEffect, useMemo } from "react";
import { ChatMessage, Document, Library, EvidenceItem } from "../types";
import { 
  Send, Sparkles, MessageSquare, RefreshCcw, Loader2,
  BookOpen, Globe, User, HelpCircle, CheckSquare, Check, FileText, Copy, History 
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type QueryScope = "all" | "local" | "web" | "custom";

// 四种问答模式的提问引导（2026-08-13）：示例提问跟随当前模式，且必须是该模式下可回答的问题
const MODE_GUIDES: Record<QueryScope, {
  label: string;
  emptyTitle: string;
  placeholder: (docTitle?: string, docCount?: number) => string;
  hint: (docTitle?: string, docCount?: number) => string;
  suggestions: string[];
}> = {
  local: {
    label: "当前文档",
    emptyTitle: "当前文档问答",
    placeholder: (t) => `对「${t || "当前文档"}」提问，如：总结本文档的核心观点`,
    hint: (t) => `仅检索「${t || "当前文档"}」内容，回答标注文档内位置。`,
    suggestions: ["总结本文档的核心观点", "提取本文档的关键概念与术语", "本文档的主要结论是什么？"],
  },
  all: {
    label: "全库",
    emptyTitle: "全库问答",
    placeholder: () => "对全库提问，如：对比不同文档的观点异同",
    hint: () => "对全库档案联合检索，可跨文档对比观点。",
    suggestions: [
      "总结知识库中所有文档的核心主题",
      "对比不同文档对同一话题的观点",
      "找出知识库中观点相互矛盾或互补的地方",
      "知识库中有哪些书、各是什么格式？",
    ],
  },
  custom: {
    label: "跨文档",
    emptyTitle: "跨文档对比问答",
    placeholder: (_t, n) => `对选定的 ${n || 0} 篇文档联合提问，如：对比它们的观点差异`,
    hint: (_t, n) => `已选 ${n || 0} 篇重点文献，跨文件融合审计问答。`,
    suggestions: ["对比所选文档的观点异同", "找出所选文档的共同主题", "综合所选文档给出结论与建议"],
  },
  web: {
    label: "联网",
    emptyTitle: "联网实时问答",
    placeholder: () => "联网实时搜索提问，如：查一下某话题的最新进展",
    hint: () => "实时联网搜索 + 本地知识库补充，回答附网页来源。",
    suggestions: ["查一下「主题」的最新进展", "「主题」的权威定义或官方来源是什么？", "检索「主题」的最新新闻或报告"],
  },
};

interface ChatPanelProps {
  documents: Document[];
  libraries: Library[];
  selectedLibId: string | null;
  selectedDocId: string | null;
  activeChatMessages: ChatMessage[];
  onSendMessage: (query: string, scope: "all" | "local" | "web" | "custom", customDocIds?: string[]) => Promise<void>;
  onClearHistory: () => void;
  onDeleteMessage?: (msgId: string) => void;
  onGenerateFollowups?: (msgId: string) => void;
  onAskFollowUp?: (text: string, msgId: string) => void;
  onLocateEvidence?: (evidence: EvidenceItem) => void;
  generatingFollowupsFor?: string | null;
  onNavigateToDoc: (docId: string) => void;
  isSendingMessage: boolean;
  isChatCoolingDown?: boolean;
  chatCooldownSeconds?: number;
}

export default function ChatPanel({
  documents,
  libraries,
  selectedLibId,
  selectedDocId,
  activeChatMessages,
  onSendMessage,
  onClearHistory,
  onDeleteMessage,
  onGenerateFollowups,
  onAskFollowUp,
  onLocateEvidence,
  generatingFollowupsFor = null,
  onNavigateToDoc,
  isSendingMessage,
  isChatCoolingDown = false,
  chatCooldownSeconds = 0,
}: ChatPanelProps) {
  const [query, setQuery] = useState("");
  const [queryScope, setQueryScope] = useState<QueryScope>(() => {
    return selectedDocId ? "local" : "all";
  });
  const [customSelectedIds, setCustomSelectedIds] = useState<string[]>([]);
  const [showCustomPanel, setShowCustomPanel] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const activeLibrary = libraries.find(l => l.id === selectedLibId);
  const activeDoc = documents.find(d => d.id === selectedDocId);
  const guide = MODE_GUIDES[queryScope];

  // Auto scroll to message bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeChatMessages, isSendingMessage]);

  // Sync state scope when selectedDocId is changed
  useEffect(() => {
    if (selectedDocId) {
      setQueryScope("local");
    }
  }, [selectedDocId]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isSendingMessage) return;
    
    if (queryScope === "custom" && customSelectedIds.length === 0) {
      alert("请至少勾选一个核心文档，以便我们精确提取并联合交叉检索其内容！");
      return;
    }

    onSendMessage(query.trim(), queryScope, customSelectedIds);
    setQuery("");
    // Auto-collapse custom selection panel after sending
    setShowCustomPanel(false);
  };

  // Citation matching and tag extraction
  const findCitationsInText = (text: string) => {
    const matchedDocs: Array<{ id: string; title: string }> = [];
    documents.forEach(doc => {
      if (text.toLowerCase().includes(doc.title.toLowerCase()) || text.includes(`[${doc.title}]`)) {
        if (!matchedDocs.some(d => d.id === doc.id)) {
          matchedDocs.push({ id: doc.id, title: doc.title });
        }
      }
    });
    return matchedDocs;
  };

  const handleToggleCustomDoc = (docId: string) => {
    setCustomSelectedIds(prev => 
      prev.includes(docId)
        ? prev.filter(id => id !== docId)
        : [...prev, docId]
    );
  };

  const copyMessage = async (text: string, msgId: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(msgId);
      window.setTimeout(() => setCopiedId(cur => (cur === msgId ? null : cur)), 1600);
    } catch {
      /* 剪贴板不可用时静默忽略 */
    }
  };

  // 历史问答列表（按时间倒序）：user 问题 + 紧随其后的 assistant 回答摘要
  const historyPairs = useMemo(() => {
    const pairs: { time: string; q: string; a: string }[] = [];
    for (let i = 0; i < activeChatMessages.length; i++) {
      const m = activeChatMessages[i];
      if (m.role !== "user") continue;
      const next = activeChatMessages[i + 1];
      pairs.push({
        time: m.timestamp,
        q: m.content,
        a: next && next.role === "assistant" ? next.content : "",
      });
    }
    return pairs.reverse();
  }, [activeChatMessages]);

  return (
    <div id="chat-panel" className="flex flex-col bg-white border border-zinc-200 rounded-xl overflow-hidden h-full shadow-sm relative">
      {/* Compact header: title + scope selector in one row */}
      <div className="px-4 py-2 border-b border-zinc-200 flex items-center gap-4 bg-white select-none">
        <h3 className="text-xs font-black text-zinc-700 tracking-tight shrink-0">智能问答</h3>
        <div className="flex rounded-lg border border-zinc-200 p-0.5 bg-zinc-50/60 gap-0.5 flex-1 min-w-0">
          <button
            onClick={() => setQueryScope("local")}
            disabled={!selectedDocId}
            type="button"
            className={`flex-1 px-2 py-1 rounded-md text-[10px] font-bold tracking-wide flex items-center justify-center gap-1 transition-all ${
              queryScope === "local"
                ? "bg-white text-zinc-900 border border-zinc-200 shadow-sm"
                : "text-zinc-500 hover:text-zinc-800 disabled:opacity-40"
            }`}
            title={!selectedDocId ? "请先在侧边栏选择一个文档" : `对「${activeDoc?.title}」进行 RAG 问答`}
          >
            <FileText className="w-3 h-3" />
            {activeDoc ? activeDoc.title.slice(0, 8) : "当前文档"}
          </button>

          <button
            onClick={() => setQueryScope("all")}
            type="button"
            className={`flex-1 px-2 py-1 rounded-md text-[10px] font-bold tracking-wide flex items-center justify-center gap-1 transition-all ${
              queryScope === "all"
                ? "bg-zinc-100 text-zinc-900 shadow-sm"
                : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            <BookOpen className="w-3 h-3" />
            全库
          </button>

          <button
            onClick={() => {
              setQueryScope("custom");
              setShowCustomPanel(!showCustomPanel);
              if (customSelectedIds.length === 0 && documents.length > 0) {
                setCustomSelectedIds([documents[0].id]);
              }
            }}
            type="button"
            className={`flex-1 px-2 py-1 rounded-md text-[10px] font-bold tracking-wide flex items-center justify-center gap-1 transition-all ${
              queryScope === "custom"
                ? "bg-emerald-100 text-emerald-800 shadow-sm"
                : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            <CheckSquare className="w-3 h-3" />
            自定义
          </button>

          <button
            onClick={() => setQueryScope("web")}
            type="button"
            className={`flex-1 px-2 py-1 rounded-md text-[10px] font-bold tracking-wide flex items-center justify-center gap-1 transition-all ${
              queryScope === "web"
                ? "bg-emerald-100 text-emerald-800 shadow-sm"
                : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            <Globe className="w-3 h-3" />
            联网
          </button>
        </div>
        <button
          onClick={() => setShowHistory(v => !v)}
          className={`shrink-0 text-xs font-semibold flex items-center gap-1 transition-colors hover:bg-zinc-100 px-1.5 py-1 rounded ${showHistory ? "text-emerald-600" : "text-zinc-400 hover:text-zinc-600"}`}
          title="历史问答"
        >
          <History className="w-3 h-3" />
        </button>
        <button
          onClick={onClearHistory}
          className="shrink-0 text-zinc-400 hover:text-zinc-600 text-xs font-semibold flex items-center gap-1 transition-colors hover:bg-zinc-100 px-1.5 py-1 rounded"
        >
          <RefreshCcw className="w-3 h-3" />
        </button>
      </div>

      {/* 历史问答列表（按时间倒序） */}
      {showHistory && (
        <div className="absolute top-12 right-3 z-30 w-80 max-h-[70%] bg-white border border-zinc-200 rounded-xl shadow-xl flex flex-col overflow-hidden animate-fadeIn">
          <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-100">
            <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 font-mono">
              历史问答（{historyPairs.length}）
            </span>
            <button onClick={() => setShowHistory(false)} className="text-zinc-400 hover:text-zinc-600 text-xs px-1">✕</button>
          </div>
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            {historyPairs.length === 0 ? (
              <p className="px-3 py-4 text-[11px] text-zinc-400 italic">还没有历史问答。</p>
            ) : (
              historyPairs.slice(0, 60).map((p, i) => (
                <button
                  key={`${p.time}-${i}`}
                  onClick={() => {
                    setQuery(p.q);
                    setShowHistory(false);
                    inputRef.current?.focus();
                  }}
                  className="w-full text-left px-3 py-2 border-b border-zinc-50 hover:bg-emerald-50/60 transition-colors flex flex-col gap-1"
                >
                  <span className="text-[9px] text-zinc-400 font-mono">
                    {new Date(p.time).toLocaleString([], { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                  </span>
                  <span className="text-[11px] font-semibold text-zinc-800 line-clamp-2">{p.q}</span>
                  {p.a && <span className="text-[10px] text-zinc-500 line-clamp-2">{p.a}</span>}
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {/* Accordion panel for custom selection (checklist) */}
      {queryScope === "custom" && showCustomPanel && (
        <div className="bg-zinc-50/50 border-b border-zinc-200 px-4 py-3 text-left space-y-2 animate-fadeIn max-h-48 overflow-y-auto select-none">
          <div className="flex justify-between items-center pb-1 border-b border-zinc-100">
            <span className="text-[10px] text-zinc-450 font-bold font-mono">
              勾选参与联合检索的文档
            </span>
            <div className="flex gap-2 items-center">
              <button
                onClick={() => setCustomSelectedIds([])}
                className="text-[9px] font-bold text-zinc-400 hover:text-zinc-600 transition-colors"
              >
                清除
              </button>
              <button
                onClick={() => setCustomSelectedIds(documents.map(d => d.id))}
                className="text-[9px] font-bold text-emerald-600 hover:text-emerald-700 transition-colors"
              >
                全选 ({documents.length})
              </button>
              <button
                onClick={() => setShowCustomPanel(false)}
                className="text-[9px] font-bold text-zinc-400 hover:text-zinc-600 ml-1"
                title="收起面板"
              >
                ✕
              </button>
            </div>
          </div>

          {documents.length === 0 ? (
            <p className="text-[10.5px] text-zinc-400 italic">您必须先创建或上传文档才能开启联合检索对话。</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 pt-0.5">
              {documents.map((doc) => {
                const isChecked = customSelectedIds.includes(doc.id);
                return (
                  <div
                    key={doc.id}
                    onClick={() => handleToggleCustomDoc(doc.id)}
                    className={`flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer transition-all ${
                      isChecked
                        ? "bg-emerald-50 border border-emerald-200 text-emerald-800 font-bold"
                        : "bg-white border border-zinc-150 hover:border-zinc-300 hover:bg-zinc-50 text-zinc-600"
                    }`}
                  >
                    <span className="shrink-0 flex items-center justify-center w-4 h-4 rounded border transition-colors"
                      style={{ 
                        backgroundColor: isChecked ? '#10b981' : 'transparent',
                        borderColor: isChecked ? '#10b981' : '#d4d4d8'
                      }}
                    >
                      {isChecked && <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />}
                    </span>
                    <span className="truncate text-[11px] leading-snug">{doc.title}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Scrolling Chat history workspace */}
      <div className="flex-1 overflow-y-auto p-5 space-y-5 bg-zinc-50/20">
        {activeChatMessages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-6 py-6 select-none animate-fadeIn">
            <span className="p-4 bg-white border border-zinc-200 text-emerald-600 rounded-full shadow-sm">
              <Sparkles className="w-5 h-5 text-emerald-400" />
            </span>
            <div className="max-w-xs space-y-1.5">
              <h4 className="text-zinc-800 font-bold text-sm">{guide.emptyTitle}</h4>
              <p className="text-xs text-zinc-500 leading-relaxed">
                {guide.hint(activeDoc?.title, customSelectedIds.length)}
              </p>
            </div>

          </div>
        ) : (
          <div className="space-y-4">
            {activeChatMessages.map((msg) => {
              const isUser = msg.role === "user";
              const detectedCitations = !isUser ? findCitationsInText(msg.content) : [];

              return (
                <div
                  key={msg.id}
                  className={`flex gap-3 max-w-[90%] ${
                    isUser ? "ml-auto flex-row-reverse" : "mr-auto"
                  } animate-fadeIn`}
                >
                  {/* Avatar Icon */}
                  <span className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 border uppercase font-mono text-[10px] ${
                    isUser 
                      ? "bg-zinc-100 border-zinc-200 text-zinc-650" 
                      : "bg-emerald-50 border-emerald-200 text-emerald-700"
                  }`}>
                    {isUser ? <User className="w-4 h-4" /> : <Sparkles className="w-4 h-4" />}
                  </span>

                  {/* Message Bubble box */}
                  <div className={`p-4 rounded-xl space-y-3 shadow-sm ${
                    isUser 
                      ? "bg-zinc-100 border border-zinc-200 text-zinc-800 rounded-tr-none" 
                      : "bg-white border border-zinc-200 text-zinc-850 rounded-tl-none animate-fadeIn"
                  }`}>
                    <div className="text-xs leading-relaxed font-sans prose prose-neutral max-w-none">
                      {isUser ? (
                        <p className="whitespace-pre-wrap">{msg.content}</p>
                      ) : (
                        <div className="markdown-body text-zinc-800">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                        </div>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-2.5 pt-2 border-t border-zinc-105 text-[9px] text-zinc-400 select-none">
                      <span>{new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                      <div className="flex items-center gap-2">
                        {!isUser && msg.content && !msg.streaming && (
                          <button
                            onClick={() => copyMessage(msg.content, msg.id)}
                            className="text-zinc-400 hover:text-emerald-600 transition-colors flex items-center gap-1"
                            title="复制回答"
                          >
                            {copiedId === msg.id ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                            {copiedId === msg.id ? "已复制" : "复制"}
                          </button>
                        )}
                        {onDeleteMessage && (
                          <button onClick={() => onDeleteMessage(msg.id)}
                            className="text-zinc-300 hover:text-rose-500 transition-colors" title="删除此消息">✕</button>
                        )}
                      </div>
                      {msg.isSearchingWeb && (
                        <span className="text-emerald-600 font-bold uppercase tracking-wider flex items-center gap-1 font-mono animate-pulse">
                          <Globe className="w-2.5 h-2.5 animate-spin" />
                          联网检索中
                        </span>
                      )}
                    </div>

                    {/* Grounding Web Links Results citations */}
                    {msg.groundingSources && msg.groundingSources.length > 0 && (
                      <div className="mt-2.5 pt-2.5 border-t border-zinc-150 space-y-1.5 select-none text-left">
                        <span className="text-[9px] uppercase font-bold tracking-widest text-zinc-400 font-mono block">
                          {msg.webSupplemented ? "联网补充来源:" : "引用的搜索网页源:"}
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {msg.groundingSources.slice(0, 3).map((src, idx) => (
                            <a
                              key={idx}
                              href={src.uri}
                              target="_blank"
                              rel="noreferrer"
                              className="px-2 py-1 bg-zinc-50 hover:bg-zinc-100 border border-zinc-200 text-zinc-650 hover:text-emerald-600 rounded-md transition-all text-[10px] flex items-center gap-1 shadow-sm"
                            >
                              <Globe className="w-2.5 h-2.5 text-zinc-400 shrink-0" />
                              <span className="truncate max-w-[120px]">{src.title}</span>
                            </a>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Local Document Citations linkages */}
                    {detectedCitations.length > 0 && (
                      <div className="mt-2.5 pt-2.5 border-t border-zinc-150 space-y-1.5 select-none text-left">
                        <span className="text-[9px] uppercase font-bold tracking-widest text-zinc-400 font-mono block">
                          关联本地文档定位:
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {detectedCitations.map((doc) => (
                            <button
                              key={doc.id}
                              onClick={() => {
                                onNavigateToDoc(doc.id);
                              }}
                              className="px-2 py-1 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/10 hover:border-emerald-500/30 text-emerald-700 rounded-md transition-all text-[10px] flex items-center gap-1 font-semibold shadow-sm"
                            >
                              <BookOpen className="w-2.5 h-2.5 shrink-0" />
                              <span className="truncate max-w-[140px]">{doc.title}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 原文证据定位：点击跳转文档并高亮证据句（2026-08-13） */}
                    {!isUser && msg.evidence && msg.evidence.length > 0 && !msg.streaming && (
                      <div className="mt-2.5 pt-2.5 border-t border-zinc-150 space-y-1.5 select-none text-left">
                        <span className="text-[9px] uppercase font-bold tracking-widest text-zinc-400 font-mono block">
                          原文证据定位（点击跳转高亮）:
                        </span>
                        <div className="flex flex-col gap-1.5">
                          {msg.evidence.slice(0, 5).map((ev, i) => (
                            <button
                              key={i}
                              onClick={() => onLocateEvidence?.(ev)}
                              className="w-full text-left px-2.5 py-1.5 bg-amber-50/70 hover:bg-amber-100 border border-amber-200/70 text-zinc-700 rounded-lg transition-all text-[10px] flex flex-col gap-0.5"
                              title={`《${ev.file_name || ev.physical_name || "?"}》${ev.page_number ? `第${ev.page_number}页` : ""}${ev.chunk_index !== undefined ? `·第${ev.chunk_index + 1}段` : ""}`}
                            >
                              <span className="font-bold text-amber-700 truncate">
                                《{ev.file_name || ev.physical_name}》{ev.page_number ? ` 第${ev.page_number}页` : ""}{ev.chunk_index !== undefined ? `·第${ev.chunk_index + 1}段` : ""}
                              </span>
                              <span className="line-clamp-2 text-zinc-600">「{ev.text}」</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* 追问建议：点击直接发送；未生成时可点击生成（2026-08-13） */}
                    {!isUser && msg.content && !msg.streaming && !msg.content.startsWith("模型调用失败") && (
                      <div className="mt-2.5 pt-2.5 border-t border-zinc-150 space-y-1.5 select-none text-left">
                        <span className="text-[9px] uppercase font-bold tracking-widest text-zinc-400 font-mono block">
                          追问建议:
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {msg.followUps && msg.followUps.length > 0 ? (
                            msg.followUps.map((f, i) => (
                              <button
                                key={i}
                                onClick={() => onAskFollowUp?.(f, msg.id)}
                                className="px-2 py-1 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/15 text-emerald-700 rounded-full text-[10px] transition-all"
                              >
                                ↪ {f}
                              </button>
                            ))
                          ) : onGenerateFollowups ? (
                            <button
                              onClick={() => onGenerateFollowups(msg.id)}
                              disabled={generatingFollowupsFor === msg.id}
                              className="px-2 py-1 bg-zinc-50 hover:bg-emerald-50 border border-zinc-200 text-zinc-500 hover:text-emerald-700 rounded-full text-[10px] transition-all disabled:opacity-50"
                            >
                              {generatingFollowupsFor === msg.id ? "生成中..." : "生成追问建议"}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {isSendingMessage && (
              <div className="flex gap-3 max-w-[85%] animate-fadeIn">
                <span className="w-7 h-7 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-700 flex items-center justify-center shrink-0">
                  <Sparkles className="w-4 h-4 animate-spin text-emerald-600" />
                </span>
                <div className="p-4 bg-white border border-zinc-200 rounded-xl rounded-tl-none flex items-center gap-2 shadow-sm">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-600" />
                  <span className="text-xs text-zinc-500 italic">正在通过向量距离搜索和关联本体库推理中...</span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Footer input form controller */}
      <div className="p-4 bg-white border-t border-zinc-200">
        {!query.trim() && (
          <div className="flex flex-wrap items-center gap-1.5 mb-2 select-none">
            <span className="text-[9px] font-bold uppercase tracking-widest text-zinc-400 font-mono shrink-0">
              {guide.label}提问示例
            </span>
            {guide.suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setQuery(s);
                  inputRef.current?.focus();
                }}
                className="px-2 py-1 bg-zinc-50 hover:bg-emerald-50 border border-zinc-200 hover:border-emerald-200 text-zinc-600 hover:text-emerald-700 rounded-full text-[10px] transition-colors select-none"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            ref={inputRef}
            required
            value={query}
            disabled={isSendingMessage || isChatCoolingDown}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              isChatCoolingDown
                ? `API 限流冷却中，${chatCooldownSeconds} 秒后可继续提问`
                : guide.placeholder(activeDoc?.title, customSelectedIds.length)
            }
            className="flex-1 px-4 py-2 bg-zinc-50/50 border border-zinc-200 text-zinc-800 text-xs rounded-lg focus:outline-none focus:border-emerald-500 placeholder-zinc-400 transition-colors"
          />
          <button
            type="submit"
            disabled={isSendingMessage || isChatCoolingDown || !query.trim()}
            className="p-2 bg-zinc-900 hover:bg-zinc-850 disabled:bg-zinc-100 disabled:text-zinc-400 text-white shadow-sm rounded-lg transition-colors select-none shrink-0 cursor-pointer"
            title={isChatCoolingDown ? `冷却中，${chatCooldownSeconds} 秒后可发送` : "发送"}
          >
            <Send className="w-4 h-4" />
          </button>
        </form>
        <div className="flex items-center gap-1 mt-2 text-[10px] text-zinc-400 select-none">
          <HelpCircle className="w-3.5 h-3.5 text-zinc-350 shrink-0" />
          <span>{guide.hint(activeDoc?.title, customSelectedIds.length)}</span>
        </div>
      </div>
    </div>
  );
}
