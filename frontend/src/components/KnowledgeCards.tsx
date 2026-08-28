import React, { useEffect, useMemo, useRef, useState } from "react";
import { Document, KnowledgeCard, Library } from "../types";
import { 
  Sparkles, Brain, Plus, Trash2, Cpu, Loader2, BookOpen, 
  ChevronLeft, ChevronRight, Download, Upload, Lightbulb, 
  Check, RefreshCw, Layers, ThumbsUp, HelpCircle
} from "lucide-react";

interface KnowledgeCardsProps {
  documents: Document[];
  libraries: Library[];
  cards: KnowledgeCard[];
  onAddCard: (card: Omit<KnowledgeCard, "id" | "createdAt">) => void;
  onUpdateCard: (id: string, updates: Partial<KnowledgeCard>) => void;
  onDeleteCard: (id: string) => void;
  onImportCards: (imported: KnowledgeCard[]) => void;
  onResetCards: () => void;
}

export default function KnowledgeCards({
  documents,
  libraries,
  cards,
  onAddCard,
  onUpdateCard,
  onDeleteCard,
  onImportCards,
  onResetCards,
}: KnowledgeCardsProps) {
  // Navigation active tab
  const [activeTab, setActiveTab] = useState<"review" | "list">("review");
  
  // Selection/Filter state
  const [filterDocId, setFilterDocId] = useState<string>("all");
  // ⛔ 2026-08-14（任务十七）：默认只看"新卡"——复习队列聚焦未学，避免全部卡片混在一起列表过长
  const [filterDifficulty, setFilterDifficulty] = useState<string>("new");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Reviewing carousel index state
  const [carouselIndex, setCarouselIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);

  // Manual Card Creation toggle and forms
  const [isCreating, setIsCreating] = useState(false);
  const [newFront, setNewFront] = useState("");
  const [newBack, setNewBack] = useState("");
  const [newDocId, setNewDocId] = useState(documents.length > 0 ? documents[0].id : "");
  const [newTagsString, setNewTagsString] = useState("");
  const [formError, setFormError] = useState("");

  // AI-powered card generator states
  const [isGenerating, setIsGenerating] = useState(false);
  const [selectedGenDocId, setSelectedGenDocId] = useState(documents.length > 0 ? documents[0].id : "");
  const [aiStatus, setAiStatus] = useState("");

  // ⛔ 2026-08-14（任务十六/十七）：一轮复习状态——已评分集合、是否完成、
  // 以及本轮队列快照（passQueue：筛选/搜索变化时定格当前结果；评分不中途踢卡，
  // 完成总结统计才准确；"再复习"子集=模糊+遗忘 的队列）。
  const [ratedThisPass, setRatedThisPass] = useState<Set<string>>(new Set());
  const [passCompleted, setPassCompleted] = useState(false);
  const [passQueue, setPassQueue] = useState<string[] | null>(null);

  // Grid list item toggle expansion state
  const [expandedCardIds, setExpandedCardIds] = useState<Record<string, boolean>>({});

  // Filtered Cards list
  const filteredCards = cards.filter(card => {
    const matchDoc = filterDocId === "all" || card.docId === filterDocId;
    const matchDiff = filterDifficulty === "all" || card.difficulty === filterDifficulty;
    const matchSearch = searchQuery.trim() === "" || 
      card.front.toLowerCase().includes(searchQuery.toLowerCase()) ||
      card.back.toLowerCase().includes(searchQuery.toLowerCase()) ||
      card.tags.some(t => t.toLowerCase().includes(searchQuery.toLowerCase()));
    
    return matchDoc && matchDiff && matchSearch;
  });

  // ⛔ 2026-08-14（任务十八）：docId → 知识卡数量，生成器下拉标注"N 张/未生成"
  const cardCountByDoc = useMemo(() => {
    const m: Record<string, number> = {};
    cards.forEach(c => {
      m[c.docId] = (m[c.docId] || 0) + 1;
    });
    return m;
  }, [cards]);

  // 复习队列 = 本轮队列快照（若开启）否则当前筛选结果。
  // ⛔ 用 cards（全量）而非 filteredCards 匹配快照：评分改变难度后卡片被筛选剔除，
  // 但仍应留在本轮队列里直到评完，避免队列中途缩水、完成总结失真
  const reviewCards = passQueue
    ? cards.filter(c => passQueue.includes(c.id))
    : filteredCards;

  // Toggle helper
  const toggleCardExpanded = (id: string) => {
    setExpandedCardIds(prev => ({ ...prev, [id]: !prev[id] }));
  };

  // Bulk expand/collapse cards on the list screen
  const handleToggleAllExpanded = (expand: boolean) => {
    if (!expand) {
      setExpandedCardIds({});
    } else {
      const next: Record<string, boolean> = {};
      filteredCards.forEach(c => {
        next[c.id] = true;
      });
      setExpandedCardIds(next);
    }
  };

  // Export cards as JSON
  const handleExportCards = () => {
    if (cards.length === 0) return;
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(cards, null, 2));
    const dlAnchorElem = document.createElement("a");
    dlAnchorElem.setAttribute("href", dataStr);
    dlAnchorElem.setAttribute("download", `Knowledge-Flashcards-${new Date().toISOString().split("T")[0]}.json`);
    dlAnchorElem.click();
  };

  // Import cards from JSON
  const handleImportCards = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      const reader = new FileReader();
      reader.onload = (event) => {
        try {
          const imported = JSON.parse(event.target?.result as string);
          if (Array.isArray(imported)) {
            // Basic validation
            const valid = imported.every(c => c.front && c.back);
            if (valid) {
              onImportCards(imported);
              alert(`成功导入 ${imported.length} 张知识闪卡！`);
            } else {
              alert("❌ JSON 格式不兼容，必须是包含 front 和 back 属性的卡片数组。");
            }
          } else {
            alert("❌ 导入失败，该文件不是合规 of JSON 数组结构。");
          }
        } catch (err) {
          alert("❌ 解析 JSON 发生语法错误，请检查文件编码。");
        }
      };
      reader.readAsText(file);
    }
  };

  // Handle manual card addition
  const handleCreateCardSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newFront.trim() || !newBack.trim()) {
      setFormError("正面概念和背面精解均为必填项。");
      return;
    }
    
    const docId = newDocId || (documents.length > 0 ? documents[0].id : "manual-free");
    const tags = newTagsString
      ? newTagsString.split(",").map(t => t.trim()).filter(Boolean)
      : ["手动录入"];

    onAddCard({
      docId,
      front: newFront.trim(),
      back: newBack.trim(),
      tags,
      // ⛔ 2026-08-14（任务十六）：新卡默认"未学"，评分后才进入已记住/模糊/遗忘
      difficulty: "new"
    });

    setNewFront("");
    setNewBack("");
    setNewTagsString("");
    setFormError("");
    setIsCreating(false);
  };

  // Smart AI call to retrieve cards from document
  const handleTriggerAICardGeneration = async () => {
    const targetDocId = selectedGenDocId || (documents.length > 0 ? documents[0].id : null);
    if (!targetDocId) return;

    const doc = documents.find(d => d.id === targetDocId);
    if (!doc) return;

    setIsGenerating(true);
    setAiStatus("正在启动 AI 提取卡片...");

    try {
      // ⛔ 2026-08-14（v2）：不再拉全文/截断——长文档首部可能是自传/导言，
      // 首部截断会漏掉核心知识点（《原则》实测提炼 0 张）。后端按 docId 做
      // 全书等距采样（覆盖首/中/尾），前端只传文档标识，链路简化且规避 422。
      const resp = await fetch("/api/ai/generate-cards", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          docId: doc.id,
          title: doc.title,
          content: ""
        })
      });

      if (!resp.ok) throw new Error("向 AI 服务发起请求时发生网络异常。");
      
      const data = await resp.json();
      if (data.cards && Array.isArray(data.cards) && data.cards.length > 0) {
        setAiStatus(`智能提炼成功！已生成 ${data.cards.length} 张记忆闪卡。`);
        
        // Import generated cards to main state
        data.cards.forEach((c: any) => {
          onAddCard({
            docId: c.docId || doc.id,
            front: c.front,
            back: c.back,
            tags: c.tags || ["AI 生成"],
            difficulty: "new"
          });
        });
      } else {
        // 0 张给友好提示，不再显示迷惑的"生成 0 张"
        setAiStatus("未能提炼出有效知识点，请确认文档内容或换一份文档再试。");
      }
    } catch (err: any) {
      console.error(err);
      setAiStatus(`提取中断: ${err.message || "请求失败"}`);
    } finally {
      setIsGenerating(false);
      setTimeout(() => setAiStatus(""), 4000);
    }
  };

  // ⛔ 2026-08-14：reviewCards 变化（删除/筛选/再复习子集使当前卡消失）时 clamp 轮播索引，
  // 防止"当前卡片 N/M"越界、activeReviewCard 变 null
  useEffect(() => {
    setCarouselIndex(prev => {
      if (reviewCards.length === 0) return 0;
      if (prev >= reviewCards.length) return reviewCards.length - 1;
      return prev;
    });
  }, [reviewCards.length]);

  // ⛔ 2026-08-14（任务十七）：筛选/搜索变化 → 快照当前结果为本轮队列，
  // 并重置评分状态与完成标记
  useEffect(() => {
    setPassQueue(filteredCards.map(c => c.id));
    setRatedThisPass(new Set());
    setPassCompleted(false);
  }, [filterDocId, filterDifficulty, searchQuery]);

  // ⛔ 2026-08-14（任务十九）：选中的文档卡片被删光/清空后，筛选自动回落"全部文档"
  useEffect(() => {
    if (
      filterDocId !== "all" &&
      !documents.some(d => d.id === filterDocId && (cardCountByDoc[d.id] || 0) > 0)
    ) {
      setFilterDocId("all");
      setCarouselIndex(0);
    }
  }, [cardCountByDoc, documents, filterDocId]);

  // ⛔ 2026-08-19：新卡入队——AI 生成/手动添加/导入会改变 cards 长度，
  // 此时必须重建 passQueue 快照（原快照不含新卡 → review 视图"生成后为空"，
  // 需切换筛选才显示）；评分改难度/删除不改长度，不触发，复习队列不中断。
  const prevCardCountRef = useRef(cards.length);
  useEffect(() => {
    const added = cards.length > prevCardCountRef.current;
    prevCardCountRef.current = cards.length;
    if (added) {
      setPassQueue(filteredCards.map(c => c.id));
      setRatedThisPass(new Set());
      setPassCompleted(false);
      setCarouselIndex(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cards]);

  // Carousel boundary safety index check
  const activeReviewCard = reviewCards[carouselIndex] || null;

  const navigateCarousel = (direction: "prev" | "next") => {
    if (reviewCards.length === 0) return;
    setIsFlipped(false);
    // ⛔ 2026-08-14：去掉 setTimeout 延迟，立即切换（快速连点不再错乱）
    if (direction === "prev") {
      setCarouselIndex(prev => (prev === 0 ? reviewCards.length - 1 : prev - 1));
    } else {
      setCarouselIndex(prev => (prev === reviewCards.length - 1 ? 0 : prev + 1));
    }
  };

  // ⛔ 2026-08-14（任务十六）：评分后一律推进到下一张未评分卡；
  // 本轮全部评完 → 显示完成总结（不再无限绕圈）。"再复习"子集同样按此流转。
  const handleRateDifficulty = (id: string, difficulty: "new" | "easy" | "medium" | "hard") => {
    onUpdateCard(id, { difficulty });
    setIsFlipped(false);
    const nextRated = new Set(ratedThisPass);
    nextRated.add(id);
    setRatedThisPass(nextRated);
    const curIdx = reviewCards.findIndex(c => c.id === id);
    const after = reviewCards.slice(curIdx + 1).find(c => !nextRated.has(c.id));
    const target = after || reviewCards.find(c => !nextRated.has(c.id));
    if (!target) {
      setPassCompleted(true);
      return;
    }
    setCarouselIndex(reviewCards.findIndex(c => c.id === target.id));
  };

  // ⛔ 2026-08-14（任务十六）：完成总结 → 再复习一轮（仅模糊+遗忘）
  const handleRelearnPass = () => {
    const fuzzy = reviewCards
      .filter(c => c.difficulty === "medium" || c.difficulty === "hard")
      .map(c => c.id);
    setPassQueue(fuzzy.length > 0 ? fuzzy : null);
    setRatedThisPass(new Set());
    setPassCompleted(false);
    setCarouselIndex(0);
    setIsFlipped(false);
  };

  // ⛔ 2026-08-14（任务十六）：完成总结 → 返回浏览全部
  const handleBackToBrowse = () => {
    setPassQueue(null);
    setFilterDifficulty("all");
    setRatedThisPass(new Set());
    setPassCompleted(false);
    setCarouselIndex(0);
    setIsFlipped(false);
  };

  return (
    <div id="knowledge-flashcards-dashboard" className="flex flex-col lg:flex-row h-full w-full gap-4 lg:gap-5 overflow-y-auto lg:overflow-hidden select-none">
      
      {/* LEFT SIDEBAR: Setup automatic generator & manual builders */}
      <div className="w-full lg:w-[350px] lg:h-full shrink-0 flex flex-col gap-4 lg:overflow-y-auto lg:pr-1 custom-scrollbar">
        
        {/* Module 1: AI Cards Extractor */}
        <div className="bg-white border border-zinc-200 rounded-xl p-4 shadow-sm text-left relative overflow-hidden flex flex-col justify-between">
          <div className="absolute top-0 right-0 w-24 h-24 bg-zinc-50 rounded-full -mr-8 -mt-8 opacity-40 select-none pointer-events-none" />
          <div className="relative">
            <h3 className="text-sm font-black text-zinc-900 flex items-center gap-1.5">
              <Sparkles className="w-4 h-4 text-emerald-500 fill-emerald-500/10" />
              智能提炼闪卡
            </h3>
            <p className="text-[11px] text-zinc-500 leading-relaxed mt-1.5">
              选取目标文档，AI 会自动提取核心论断、学术命题，提炼为记忆卡，助力温故知新。
            </p>

            <div className="mt-4">
              <label className="text-[10px] text-zinc-450 font-black block mb-1">选择源解构文档:</label>
              {documents.length === 0 ? (
                <p className="text-[11px] text-rose-500 italic">请先去文档库建立或扫描一些文件。</p>
              ) : (
                <>
                  <select
                    value={selectedGenDocId}
                    onChange={(e) => setSelectedGenDocId(e.target.value)}
                    className="w-full text-xs font-semibold bg-white border border-zinc-200 p-2 rounded-lg focus:outline-none focus:border-emerald-500 shadow-sm cursor-pointer"
                    disabled={isGenerating}
                  >
                    {documents.map(d => (
                      <option key={d.id} value={d.id}>
                        {d.title} · {(cardCountByDoc[d.id] || 0) > 0 ? `${cardCountByDoc[d.id]} 张` : "未生成"}
                      </option>
                    ))}
                  </select>
                  {/* ⛔ 2026-08-14（任务十八）：已生成/未生成汇总，避免重复生成或漏生成 */}
                  <p className="text-[10px] text-zinc-400 mt-1 font-medium">
                    已生成卡片：{documents.filter(d => (cardCountByDoc[d.id] || 0) > 0).length} / {documents.length} 个文档
                  </p>
                </>
              )}
            </div>
          </div>

          <div className="mt-4">
            {aiStatus && (
              <div className="p-2 border border-emerald-100 bg-emerald-50/40 rounded-lg text-[10.5px] leading-relaxed text-zinc-700 font-medium mb-3 animate-fadeIn">
                <span className="inline-block w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping mr-1" />
                {aiStatus}
              </div>
            )}

            <button
              onClick={handleTriggerAICardGeneration}
              disabled={isGenerating || documents.length === 0}
              className="w-full py-2 bg-zinc-900 hover:bg-zinc-950 disabled:bg-zinc-100 text-white disabled:text-zinc-400 text-xs font-bold rounded-lg flex items-center justify-center gap-2 shadow-sm transition-all cursor-pointer"
            >
              {isGenerating ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-400" />
              ) : (
                <Brain className="w-3.5 h-3.5 text-emerald-400" />
              )}
              {isGenerating ? "提取精炼中..." : "提炼记忆闪卡"}
            </button>
          </div>
        </div>

        {/* Module 2: Manual Card Creation / Board Details */}
        <div className="bg-white border border-zinc-200 rounded-xl p-4 shadow-sm text-left flex-1 flex flex-col justify-between min-h-[220px]">
          <div>
            <div className="flex justify-between items-center mb-3">
              <span className="text-[10px] uppercase font-mono font-bold text-zinc-400 tracking-wider">
                闪卡数据管理
              </span>
              <button
                onClick={() => setIsCreating(!isCreating)}
                className="text-xs font-black text-emerald-600 hover:text-emerald-700 flex items-center gap-0.5 transition-all cursor-pointer"
              >
                {isCreating ? "取消添加" : "+ 手动录入"}
              </button>
            </div>

            {isCreating ? (
              <form onSubmit={handleCreateCardSubmit} className="space-y-3 animate-fadeIn">
                {formError && <p className="text-[10px] text-rose-500 font-bold italic">{formError}</p>}
                
                <div>
                  <label className="text-[10px] text-zinc-455 font-black block mb-0.5">正面概念 / 问题前瞻:</label>
                  <input
                    type="text"
                    required
                    placeholder="例如: 什么是 RAG 向量检索的多跳召回？"
                    value={newFront}
                    onChange={(e) => setNewFront(e.target.value)}
                    className="w-full p-2 bg-zinc-50 text-zinc-800 text-xs rounded-lg border border-zinc-200 focus:outline-none focus:bg-white focus:border-emerald-500"
                  />
                </div>

                <div>
                  <label className="text-[10px] text-zinc-455 font-black block mb-0.5">背面精解 / 主旨解释:</label>
                  <textarea
                    required
                    rows={3}
                    placeholder="专业释义、背景原理或核心公式..."
                    value={newBack}
                    onChange={(e) => setNewBack(e.target.value)}
                    className="w-full p-2 bg-zinc-50 text-zinc-800 text-xs rounded-lg border border-zinc-200 focus:outline-none focus:bg-white focus:border-emerald-500 resize-none"
                  />
                </div>

                <div>
                  <label className="text-[10px] text-zinc-455 font-black block mb-0.5">联结关联档案:</label>
                  <select
                    value={newDocId}
                    onChange={(e) => setNewDocId(e.target.value)}
                    className="w-full text-[11px] p-2 rounded bg-white border border-zinc-250 focus:outline-none cursor-pointer"
                  >
                    <option value="manual-free">跨篇独立概念卡</option>
                    {documents.map(d => (
                      <option key={d.id} value={d.id}>{d.title}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="text-[10px] text-zinc-455 font-black block mb-0.5">分类标签 (逗号隔开):</label>
                  <input
                    type="text"
                    placeholder="架构, 原理, 算法"
                    value={newTagsString}
                    onChange={(e) => setNewTagsString(e.target.value)}
                    className="w-full p-2 bg-zinc-50 text-zinc-800 text-xs rounded-lg border border-zinc-200 focus:outline-none focus:bg-white focus:border-emerald-500"
                  />
                </div>

                <button
                  type="submit"
                  className="w-full py-1.5 bg-zinc-900 hover:bg-zinc-800 text-white rounded-lg text-xs font-black shadow transition-all cursor-pointer"
                >
                  建立入库 (Add Card)
                </button>
              </form>
            ) : (
              <div className="space-y-4 font-mono text-[11px]">
                <div className="flex justify-between items-center py-1.5 border-b border-zinc-100">
                  <span className="text-zinc-400">总挂载卡片额度:</span>
                  <strong className="text-zinc-800 font-black">{cards.length} 枚</strong>
                </div>
                <div className="flex justify-between items-center py-1.5 border-b border-zinc-100">
                  {/* ⛔ 2026-08-14（任务二十）：不再标"新卡"，中性"未评分" */}
                  <span className="text-zinc-400">未评分:</span>
                  <strong className="text-zinc-500 font-bold">{cards.filter(c => c.difficulty === "new" || !c.difficulty).length} 枚</strong>
                </div>
                <div className="flex justify-between items-center py-1.5 border-b border-zinc-100">
                  <span className="text-zinc-400">已记住 (Easy):</span>
                  <strong className="text-emerald-500 font-bold">{cards.filter(c => c.difficulty === "easy").length} 枚</strong>
                </div>
                <div className="flex justify-between items-center py-1.5 border-b border-zinc-100">
                  <span className="text-zinc-400">模糊 (Medium):</span>
                  <strong className="text-amber-500 font-bold">{cards.filter(c => c.difficulty === "medium").length} 枚</strong>
                </div>
                <div className="flex justify-between items-center py-1.5">
                  <span className="text-zinc-400">忘记 (Hard):</span>
                  <strong className="text-rose-500 font-bold">{cards.filter(c => c.difficulty === "hard").length} 枚</strong>
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-zinc-100 pt-4 mt-6 flex gap-2">
            <button
              onClick={handleExportCards}
              disabled={cards.length === 0}
              className="flex-1 py-1.5 border border-zinc-200 hover:bg-zinc-50 disabled:bg-zinc-50 text-[10.5px] font-bold rounded-lg text-zinc-650 disabled:text-zinc-350 flex items-center justify-center gap-1 shadow-sm transition-all cursor-pointer"
              title="导出卡片包为 JSON"
            >
              <Download className="w-3.5 h-3.5 text-zinc-500" />
              备份备份卡
            </button>

            <label className="flex-1 py-1.5 border border-zinc-200 hover:bg-zinc-50 text-[10.5px] text-center font-bold rounded-lg text-zinc-650 cursor-pointer flex items-center justify-center gap-1 shadow-sm transition-all">
              <Upload className="w-3.5 h-3.5 text-zinc-500" />
              数据恢复
              <input
                type="file"
                accept=".json"
                onChange={handleImportCards}
                className="hidden"
              />
            </label>
            <button
              onClick={() => {
                if (window.confirm("确定清空全部知识闪卡吗？此操作不可撤销（可先导出备份）。")) {
                  onResetCards();
                }
              }}
              disabled={cards.length === 0}
              className="flex-1 py-1.5 border border-zinc-200 hover:bg-rose-50 disabled:bg-zinc-50 text-[10.5px] font-bold rounded-lg text-rose-500 disabled:text-zinc-350 flex items-center justify-center gap-1 shadow-sm transition-all cursor-pointer"
              title="重置卡箱到初始状态（清空全部卡片）"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-400" />
              清空全部
            </button>
          </div>
        </div>
      </div>

      {/* RIGHT AREA: Primary Card review board & lists */}
      <div className="flex-1 flex flex-col bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm h-auto lg:h-full select-text text-left">
        
        {/* Compact header: tabs + filters in one row */}
        <div className="px-4 py-2 border-b border-zinc-200 bg-white flex flex-wrap items-center gap-2 select-none shrink-0">
          {/* Tab selector */}
          <div className="flex rounded-lg border border-zinc-200 p-0.5 bg-zinc-50 shadow-sm">
            <button
              onClick={() => setActiveTab("review")}
              className={`px-2 py-1 rounded-md text-[10px] font-bold flex items-center gap-1 transition-all cursor-pointer ${
                activeTab === "review"
                  ? "bg-white text-zinc-900 shadow-sm border border-zinc-200"
                  : "text-zinc-500 hover:text-zinc-800"
              }`}
            >
              <BookOpen className="w-3 h-3 text-zinc-500" />
              闪卡自评
            </button>
            <button
              onClick={() => { setActiveTab("list"); setExpandedCardIds({}); }}
              className={`px-2 py-1 rounded-md text-[10px] font-bold flex items-center gap-1 transition-all cursor-pointer ${
                activeTab === "list"
                  ? "bg-white text-zinc-900 shadow-sm border border-zinc-200"
                  : "text-zinc-500 hover:text-zinc-800"
              }`}
            >
              <Layers className="w-3 h-3 text-zinc-500" />
              全部 [{cards.length}]
            </button>
          </div>

          <select
            value={filterDocId}
            onChange={(e) => { setFilterDocId(e.target.value); setCarouselIndex(0); }}
            className="text-[10px] border border-zinc-200 rounded px-2 py-1 bg-white font-bold text-zinc-700 cursor-pointer"
          >
            <option value="all">全部文档</option>
            {/* ⛔ 2026-08-14（任务十九）：只列有卡片的文档，无卡文档不展示 */}
            {documents
              .filter(d => (cardCountByDoc[d.id] || 0) > 0)
              .map(d => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>

          <select
            value={filterDifficulty}
            onChange={(e) => { setFilterDifficulty(e.target.value); setCarouselIndex(0); }}
            className="text-[10px] border border-zinc-200 rounded px-2 py-1 bg-white font-bold text-zinc-700 cursor-pointer"
          >
            <option value="all">全部</option>
            <option value="new">未评分</option>
            <option value="easy">已记住</option>
            <option value="medium">模糊</option>
            <option value="hard">遗忘</option>
          </select>

          <input
            type="search"
            placeholder="搜索..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="flex-1 min-w-[100px] text-[10px] border border-zinc-200 rounded px-2 py-1 bg-white focus:outline-none focus:border-zinc-400"
          />

          <button onClick={handleExportCards} className="text-[10px] border border-zinc-200 rounded px-2 py-1 bg-white text-zinc-600 font-bold hover:bg-zinc-50 cursor-pointer">导出</button>
          <label className="text-[10px] border border-zinc-200 rounded px-2 py-1 bg-white text-zinc-600 font-bold hover:bg-zinc-50 cursor-pointer">
            导入<input type="file" accept=".json" onChange={handleImportCards} className="hidden" />
          </label>
        </div>

{/* Dynamic active workspace */}
        <div className="flex-1 overflow-y-auto p-5 relative min-h-0 bg-zinc-50/10">
          
          {/* VIEW 1: Immersive 3D Carousel view WITH Glossary Term Directory */}
          {activeTab === "review" && (
            <div className="min-h-full flex flex-col md:flex-row gap-5 max-w-5xl mx-auto relative text-left">
              {reviewCards.length === 0 ? (
                <div className="flex-grow flex flex-col items-center justify-center p-8 space-y-4 select-none">
                  <div className="p-3 bg-zinc-50 border border-zinc-150 rounded-full text-zinc-400">
                    <HelpCircle className="w-8 h-8" />
                  </div>
                  <div className="text-center space-y-1 block">
                    <h4 className="text-xs font-bold text-zinc-700">没有符合筛选条件的知识闪卡</h4>
                    <p className="text-[11px] text-zinc-400 max-w-sm mt-1 mb-2">
                      {filterDifficulty === "new" && cards.length > 0
                        ? "当前没有未评分的卡了，可在筛选器切到「全部」继续浏览/复习。"
                        : "您可以通过左侧的 **AI 闪卡提取器** 让智能辅助模型自动帮您提炼，或直接手动录入闪卡进行管理。"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setIsCreating(true);
                    }}
                    className="px-3.5 py-1.5 bg-zinc-900 text-white rounded-lg text-xs font-bold shadow hover:bg-zinc-950 transition-all cursor-pointer"
                  >
                    手动新增一张卡片
                  </button>
                </div>
              ) : (
                <>
                  {/* Part A: Terms Glossary Index Directory Column */}
                  {/* ⛔ 2026-08-14（任务十七）：固定高度而非 min-h——卡片多时列不再
                      随词表无限长高把卡面顶到下方；列表在盒内滚动 */}
                  <div className="w-full md:w-[230px] bg-zinc-100/70 border border-zinc-200 rounded-xl p-3 flex flex-col min-h-[220px] md:h-[460px] select-none overflow-hidden shrink-0">
                    <div className="flex items-center justify-between mb-2 pb-2 border-b border-zinc-200 shrink-0">
                      <span className="text-[10.5px] font-bold text-zinc-700 uppercase font-mono flex items-center gap-1.5">
                        <Layers className="w-3.5 h-3.5 text-emerald-500" />
                        核心专业词表 ({reviewCards.length})
                      </span>
                    </div>

                    {/* Word indices list */}
                    <div className="flex-grow overflow-y-auto space-y-1.5 max-h-[180px] md:max-h-none text-left pr-1 custom-scrollbar">
                      {reviewCards.map((card, idx) => {
                        const isActive = idx === carouselIndex;
                        return (
                          <button
                            key={card.id}
                            onClick={() => {
                              setCarouselIndex(idx);
                              setIsFlipped(false);
                              setPassCompleted(false);
                            }}
                            type="button"
                            className={`w-full px-2.5 py-2 rounded-lg text-xs transition-all duration-150 text-left flex items-start gap-1.5 border cursor-pointer ${
                              isActive
                                ? "bg-zinc-900 text-white border-zinc-950 font-black shadow-sm"
                                : "bg-white text-zinc-650 border-zinc-150 hover:bg-zinc-150 hover:text-zinc-900"
                            }`}
                          >
                            <span className={`w-2 h-2 rounded-full mt-1 shrink-0 ${
                              card.difficulty === "easy"
                                ? "bg-emerald-500"
                                : card.difficulty === "medium"
                                  // ⛔ 2026-08-14（任务二十）：amber-450 非标准色值不生效，
                                  // 模糊圆点透明不可见——改标准 amber-500 实心
                                  ? "bg-amber-500"
                                  : card.difficulty === "hard"
                                    ? "bg-rose-500"
                                    // ⛔ 2026-08-14（任务二十一）：未评分=空心无色圈，
                                    // 与已评分实色（绿/黄/红）形成反差，促成补评分
                                    : "border border-zinc-300 bg-transparent"
                            }`} title={card.difficulty === "easy" ? "已记住" : card.difficulty === "medium" ? "模糊" : card.difficulty === "hard" ? "遗忘" : "未评分"} />
                            <span className="flex-grow font-sans pr-1 text-[11px] leading-snug break-words whitespace-normal normal-case">{card.front}</span>
                          </button>
                        );
                      })}
                    </div>
                    
                    <div className="mt-2 text-[9px] text-zinc-400 font-mono flex justify-between uppercase shrink-0 pt-1.5 border-t border-zinc-200">
                      <span>⚪ 未评</span>
                      <span>🟢 熟记</span>
                      <span>🟡 模糊</span>
                      <span>🔴 遗忘</span>
                    </div>
                  </div>

                  {/* Part B: Interactive 3D review desk drawer panel */}
                  {/* ⛔ 2026-08-14（任务十七）：顶部对齐（justify-start）让卡面靠上，
                      不再被垂直居中推到下方 */}
                  <div className="flex-1 flex flex-col justify-start space-y-6 min-w-0 shrink-0">
                    
                    {/* Carousel metrics status label */}
                    <div className="flex justify-between items-center select-none text-[11.2px] text-zinc-450 font-mono shrink-0">
                      {passCompleted ? (
                        <span className="text-emerald-600 font-black">
                          ✅ 本轮复习已完成
                        </span>
                      ) : (
                        <span>
                          当前卡片: <strong className="text-zinc-800 font-bold">{reviewCards.length > 0 ? carouselIndex + 1 : 0}</strong> / <strong className="text-zinc-800 font-bold">{reviewCards.length}</strong>
                          <span className="ml-2 text-zinc-400">
                            本轮已评 <strong className="text-zinc-600">{ratedThisPass.size}</strong> / {reviewCards.length}
                          </span>
                        </span>
                      )}
                      {activeReviewCard && !passCompleted && (
                        <span className={`px-2 py-0.5 rounded-full font-black text-[9.5px] uppercase border ${
                          activeReviewCard.difficulty === "easy" 
                            ? "bg-emerald-50 text-emerald-700 border-emerald-200" 
                            : activeReviewCard.difficulty === "medium" 
                              ? "bg-amber-50 text-amber-700 border-amber-200" 
                              : activeReviewCard.difficulty === "hard"
                                ? "bg-rose-50 text-rose-700 border-rose-200"
                                : "bg-white text-zinc-500 border-zinc-300"
                        }`}>
                          {activeReviewCard.difficulty === "easy" ? "🟢 已记住" : activeReviewCard.difficulty === "medium" ? "🟡 模糊" : activeReviewCard.difficulty === "hard" ? "🔴 遗忘" : "⚪ 未评分"}
                        </span>
                      )}
                    </div>

                    {/* ⛔ 2026-08-14（任务十六）：本轮完成总结——不再静默绕回开头 */}
                    {passCompleted && (
                      <div className="flex-1 flex flex-col items-center justify-center gap-3 bg-zinc-50 border border-zinc-200 rounded-2xl p-6 min-h-[250px] text-center shrink-0">
                        <div className="text-3xl">🎉</div>
                        <h3 className="text-sm font-black text-zinc-800">本轮复习完成</h3>
                        <p className="text-[11px] text-zinc-500">
                          本轮共 {reviewCards.length} 张卡的最终状态：
                        </p>
                        <div className="flex flex-wrap gap-2 justify-center text-[11px] font-bold">
                          <span className="px-2.5 py-1 rounded-lg bg-white text-zinc-500 border border-zinc-300">⚪ 未评分 {reviewCards.filter(c => c.difficulty === "new" || !c.difficulty).length}</span>
                          <span className="px-2.5 py-1 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-100">🟢 已记住 {reviewCards.filter(c => c.difficulty === "easy").length}</span>
                          <span className="px-2.5 py-1 rounded-lg bg-amber-50 text-amber-600 border border-amber-100">🟡 模糊 {reviewCards.filter(c => c.difficulty === "medium").length}</span>
                          <span className="px-2.5 py-1 rounded-lg bg-rose-50 text-rose-600 border border-rose-100">🔴 遗忘 {reviewCards.filter(c => c.difficulty === "hard").length}</span>
                        </div>
                        <div className="flex gap-2 mt-1">
                          {reviewCards.some(c => c.difficulty === "medium" || c.difficulty === "hard") && (
                            <button
                              onClick={handleRelearnPass}
                              className="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white text-xs font-black rounded-lg shadow-sm cursor-pointer"
                            >
                              再复习 模糊+遗忘（{reviewCards.filter(c => c.difficulty === "medium" || c.difficulty === "hard").length} 张）
                            </button>
                          )}
                          <button
                            onClick={handleBackToBrowse}
                            className="px-4 py-2 bg-zinc-900 hover:bg-zinc-950 text-white text-xs font-black rounded-lg shadow-sm cursor-pointer"
                          >
                            返回浏览全部
                          </button>
                        </div>
                      </div>
                    )}

                    {/* 3D Flip Card */}
                    {!passCompleted && (
                    <div className="relative h-[250px] w-full group perspective-1000 shrink-0">
                      <div
                        onClick={() => setIsFlipped(!isFlipped)}
                        className="relative w-full h-full duration-300 transform-style-3d cursor-pointer"
                        style={{
                          transform: isFlipped ? "rotateY(180deg)" : "none",
                          transformStyle: "preserve-3d"
                        }}
                      >
                        {/* FRONT FACE OF STUDY CARD */}
                        <div
                          className="absolute inset-0 w-full h-full bg-white border-2 border-zinc-200 hover:border-zinc-300 rounded-2xl p-6 flex flex-col justify-between shadow-xs select-none transition-all"
                          style={{
                            backfaceVisibility: "hidden",
                            WebkitBackfaceVisibility: "hidden"
                          }}
                        >
                          <div className="flex justify-between items-start">
                            <span className="p-1.5 bg-zinc-50 border border-zinc-150 rounded-lg text-zinc-400">
                              <Lightbulb className="w-4 h-4 text-amber-400 fill-amber-300/20" />
                            </span>
                            <span className="text-[9.5px] font-mono text-zinc-400 tracking-wider">
                              FRONT / 专业词汇与前瞻性提问 (点击翻面)
                            </span>
                          </div>

                          {activeReviewCard && (
                            <div className="my-auto text-center px-4">
                              <h3 className="text-sm font-extrabold text-zinc-900 leading-relaxed font-sans">
                                {activeReviewCard.front}
                              </h3>
                            </div>
                          )}

                          <div className="flex flex-wrap gap-1.5 items-center">
                            {activeReviewCard?.sourceRemoved && (
                              <span className="bg-rose-50 text-rose-600 text-[9px] font-bold px-1.5 py-0.5 rounded border border-rose-200 uppercase tracking-wide">
                                来源已删除
                              </span>
                            )}
                            {activeReviewCard?.tags.map((tag, idx) => (
                              <span key={idx} className="bg-zinc-50 text-zinc-450 text-[9px] font-bold px-1.5 py-0.5 rounded border border-zinc-150 uppercase tracking-wide">
                                {tag}
                              </span>
                            ))}
                          </div>
                        </div>

                        {/* BACK FACE OF STUDY CARD */}
                        <div
                          className="absolute inset-0 w-full h-full bg-zinc-900 border-2 border-zinc-950 rounded-2xl p-6 flex flex-col justify-between shadow-md select-text text-white overflow-y-auto"
                          style={{
                            backfaceVisibility: "hidden",
                            WebkitBackfaceVisibility: "hidden",
                            transform: "rotateY(180deg)"
                          }}
                        >
                          <div className="flex justify-between items-start select-none">
                            <span className="p-1.5 bg-zinc-800 rounded-lg text-emerald-400 border border-zinc-700">
                              <Check className="w-4 h-4 text-emerald-400" />
                            </span>
                            <span className="text-[9.5px] font-mono text-emerald-300 font-bold">
                              BACK / 释奥深度精解 (点击空白面翻回)
                            </span>
                          </div>

                          {activeReviewCard && (
                            <div className="my-auto px-1 py-4 text-zinc-200">
                              <div className="text-[12px] leading-relaxed whitespace-pre-wrap font-sans block text-left">
                                {activeReviewCard.back}
                              </div>
                            </div>
                          )}

                          <div className="flex justify-between items-center select-none border-t border-zinc-800 pt-3 mt-2 text-[10px] text-zinc-500 font-mono">
                            <span>创建于: {new Date(activeReviewCard?.createdAt || "").toLocaleDateString()}</span>
                            <span className="bg-emerald-950 text-emerald-300 font-bold px-1.5 py-0.2 rounded border border-emerald-800/50">
                              双向语义联通卡
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                    )}

                    {/* Manual trigger help slider bar and self-evaluation score trigger buttons */}
                    {activeReviewCard && !passCompleted && (
                      <div className="flex flex-col gap-3 select-none shrink-0 text-left">
                        
                        {/* Controller buttons */}
                        <div className="flex justify-between items-center shrink-0">
                          <button
                            onClick={() => navigateCarousel("prev")}
                            type="button"
                            className="p-2 border border-zinc-200 bg-white hover:bg-zinc-50 text-zinc-700 rounded-xl shadow-xs transition-all flex items-center gap-1 text-xs font-bold cursor-pointer"
                            title="前一词条"
                          >
                            <ChevronLeft className="w-4 h-4" />
                            <span>上个词条</span>
                          </button>

                          <button
                            onClick={() => setIsFlipped(!isFlipped)}
                            type="button"
                            className="px-5 py-2 bg-zinc-100 hover:bg-zinc-150 text-zinc-850 font-extrabold text-xs rounded-xl flex items-center gap-1.5 transition-all shadow-inner cursor-pointer"
                          >
                            <RefreshCw className="w-3.5 h-3.5 text-zinc-500 animate-spin" />
                            <span>点击翻转卡面</span>
                          </button>

                          <button
                            onClick={() => navigateCarousel("next")}
                            type="button"
                            className="p-2 border border-zinc-200 bg-white hover:bg-zinc-50 text-zinc-700 rounded-xl shadow-xs transition-all flex items-center gap-1 text-xs font-bold cursor-pointer"
                            title="后一词条"
                          >
                            <span>下个词条</span>
                            <ChevronRight className="w-4 h-4" />
                          </button>
                        </div>

                        {/* Spaced repetition evaluation score buttons */}
                        <div className="bg-zinc-50 border border-zinc-200 p-3 rounded-xl text-center shrink-0">
                          <span className="text-[10px] font-black uppercase text-zinc-400 font-mono tracking-widest block mb-1.5">
                            自适应记忆状态评分 (Memory Weight Evaluation)
                          </span>
                          
                          <div className="grid grid-cols-3 gap-2.5">
                            <button
                              onClick={() => handleRateDifficulty(activeReviewCard.id, "hard")}
                              type="button"
                              className={`py-1.5 px-3 text-xs font-black rounded-lg border transition-all cursor-pointer ${
                                activeReviewCard.difficulty === "hard"
                                  ? "bg-rose-500 border-rose-600 text-white shadow-xs"
                                  : "bg-white border-zinc-200 hover:border-rose-100 hover:bg-rose-50/20 text-rose-600"
                              }`}
                            >
                              🔴 忘记 (Hard)
                            </button>
                            
                            <button
                              onClick={() => handleRateDifficulty(activeReviewCard.id, "medium")}
                              type="button"
                              className={`py-1.5 px-3 text-xs font-black rounded-lg border transition-all cursor-pointer ${
                                activeReviewCard.difficulty === "medium"
                                  ? "bg-amber-500 border-amber-600 text-white shadow-xs"
                                  : "bg-white border-zinc-200 hover:border-amber-100 hover:bg-amber-50/20 text-amber-600"
                              }`}
                            >
                              🟡 模糊 (Medium)
                            </button>

                            <button
                              onClick={() => handleRateDifficulty(activeReviewCard.id, "easy")}
                              type="button"
                              className={`py-1.5 px-3 text-xs font-black rounded-lg border transition-all cursor-pointer ${
                                activeReviewCard.difficulty === "easy"
                                  ? "bg-emerald-500 border-emerald-600 text-white shadow-xs"
                                  : "bg-white border-zinc-200 hover:border-emerald-100 hover:bg-emerald-50/20 text-emerald-600"
                              }`}
                            >
                              🟢 记住 (Easy)
                            </button>
                          </div>
                          <p className="text-[10px] text-zinc-400 mt-1.5 font-sans font-medium">
                            提示: 评分（记住/模糊/遗忘）后自动进入下一张；本轮全部评完会显示复习总结，可一键再复习模糊与遗忘的词卡。
                          </p>
                        </div>

                      </div>
                    )}

                  </div>
                </>
              )}
            </div>
          )}

          {/* VIEW 2: Batch item list for details review / deletes (With Collapse-Drawer Design) */}
          {activeTab === "list" && (
            <div className="h-full flex flex-col space-y-4 text-left">
              
              {/* Collapsible bulk controller panel */}
              <div className="flex flex-wrap gap-3 justify-between items-center bg-zinc-50 border border-zinc-200 p-3 rounded-lg text-xs font-semibold select-none">
                <span className="text-zinc-620">
                  检索视窗: 已按照上述过滤指标为您检索到 {filteredCards.length} 枚相关词组。
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleToggleAllExpanded(true)}
                    type="button"
                    className="px-2.5 py-1 bg-white hover:bg-zinc-150 border border-zinc-200 text-zinc-700 text-[10.5px] font-bold rounded shadow-xs cursor-pointer transition-colors"
                  >
                    展开全部深度释义
                  </button>
                  <button
                    onClick={() => handleToggleAllExpanded(false)}
                    type="button"
                    className="px-2.5 py-1 bg-white hover:bg-zinc-150 border border-zinc-200 text-zinc-700 text-[10.5px] font-bold rounded shadow-xs cursor-pointer transition-colors"
                  >
                    折叠收起全部
                  </button>
                </div>
              </div>

              {filteredCards.length === 0 ? (
                <div className="text-center py-12 text-zinc-400 italic text-xs bg-white rounded-xl border border-zinc-150 border-dashed select-none">
                  暂无匹配当前筛选和词条检索的知识闪卡。
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {filteredCards.map((card) => {
                    const docInfo = documents.find(d => d.id === card.docId);
                    const isExpanded = !!expandedCardIds[card.id];
                    
                    return (
                      <div
                        key={card.id}
                        className="bg-white border border-zinc-200 hover:border-zinc-300 rounded-xl p-4 flex flex-col justify-between shadow-xs hover:shadow-md transition-all relative group cursor-pointer"
                        onClick={() => toggleCardExpanded(card.id)}
                        title="点击直接切换展开/折叠释义"
                      >
                        {/* Title front & delete combo */}
                        <div className="flex justify-between items-start gap-3">
                          <h4 className="text-xs font-bold text-zinc-900 leading-snug font-sans text-left flex items-center gap-1.5">
                            <span className="font-extrabold text-[12.5px]">{card.front}</span>
                          </h4>
                          
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onDeleteCard(card.id);
                            }}
                            type="button"
                            className="p-1 border border-zinc-150 rounded bg-white hover:bg-rose-50 text-rose-500 transition-colors opacity-0 group-hover:opacity-100 duration-150 cursor-pointer"
                            title="从卡包库彻底删除"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>

                        {/* Collapsible Back interpretation summary */}
                        {isExpanded ? (
                          <div 
                            className="text-[11.5px] text-zinc-700 leading-relaxed mt-2.5 bg-zinc-50 border border-zinc-150 rounded-lg p-3 whitespace-pre-wrap text-left animate-fadeIn hover:bg-zinc-105 duration-100"
                            title="回复正面卡片"
                          >
                            {card.back}
                          </div>
                        ) : (
                          <div 
                            className="text-[10.5px] text-zinc-400 font-semibold italic mt-2.5 bg-zinc-50/50 border border-zinc-100 hover:border-zinc-200 rounded-lg px-2.5 py-1.5 flex items-center justify-between text-left select-none transition-colors"
                          >
                            <span className="flex items-center gap-1 font-medium">
                              <BookOpen className="w-3 h-3 text-zinc-400" />
                              <span>点击即可看学术大模型深度释义与解析...</span>
                            </span>
                            <span className="text-[9.5px] bg-zinc-200 text-zinc-550 px-1.5 py-0.5 rounded font-mono font-bold shrink-0">点击展开</span>
                          </div>
                        )}

                        {/* Card metadata footer labels */}
                        <div className="flex flex-wrap items-center justify-between gap-1.5 border-t border-zinc-100 pt-2.5 mt-3 text-[9.5px] text-zinc-400 font-mono">
                          <div className="flex flex-wrap gap-1 items-center">
                            <span className="bg-zinc-50 text-zinc-500 border border-zinc-150 px-1 rounded">
                              {docInfo ? docInfo.title : (card.sourceRemoved ? "来源已删除" : "跨篇独立卡")}
                            </span>
                            {card.tags.map((tag, i) => (
                              <span key={i} className="bg-slate-50 text-md text-slate-505 border border-slate-150 px-1 rounded uppercase">
                                #{tag}
                              </span>
                            ))}
                          </div>

                          <span className={`px-1.5 rounded-sm font-semibold uppercase ${
                            card.difficulty === "hard" 
                              ? "bg-rose-100 text-rose-700" 
                              : card.difficulty === "medium" 
                                ? "bg-amber-100 text-amber-700" 
                                : card.difficulty === "new" || !card.difficulty
                                  ? "bg-zinc-100 text-zinc-500"
                                  : "bg-emerald-100 text-emerald-700"
                          }`}>
                            {card.difficulty === "hard" ? "遗忘" : card.difficulty === "medium" ? "模糊" : card.difficulty === "new" || !card.difficulty ? "未评分" : "已记住"}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

        </div>
      </div>

    </div>
  );
}
