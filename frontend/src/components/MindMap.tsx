import React, { useEffect, useRef, useState } from "react";
import { FileText, RotateCcw, Loader2 } from "lucide-react";
import { Markmap } from "markmap-view";

interface MindMapNode {
  id: string;
  topic: string;
  isCollapsed?: boolean;
  children?: MindMapNode[];
}

interface MindMapSession { id: string; docTitle: string; timestamp: string; data: MindMapNode; docId?: string; }

interface MindMapProps {
  documentTitle: string;
  documentContent: string;
  mindmapData: MindMapNode | null;
  onGenerate: () => void;
  isLoading: boolean;
  // ⛔ 2026-08-19：生成进度文案（如“第 12/48 章 · 25%”），无则显示默认文案
  progressText?: string;
  onUpdateMindmap: (updatedData: MindMapNode) => void;
  mindmapSessions?: MindMapSession[];
  onLoadMindmapSession?: (data: MindMapNode, docId?: string) => void;
  onDeleteMindmapSession?: (id: string) => void;
}

// markmap-view 0.18 节点格式：{content, children}（content 走 innerHTML，需 HTML 转义）；
// 旧 {d, c} 格式是 markmap-lib 废弃约定，0.18 下 walkTree 读不到 children/content 直接崩溃
function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function toMarkmapNode(node: MindMapNode): any {
  return {
    content: escapeHtml(node.topic || ""),
    children: (node.children || []).filter(Boolean).map(toMarkmapNode),
  };
}

export default function MindMap({
  documentTitle,
  mindmapData,
  onGenerate,
  isLoading,
  progressText,
  mindmapSessions = [],
  onLoadMindmapSession,
  onDeleteMindmapSession,
}: MindMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mmRef = useRef<Markmap | null>(null);
  const [error, setError] = useState("");

  // ===== 数据变化：销毁 + 重建 markmap =====
  useEffect(() => {
    if (!containerRef.current || !mindmapData) return;

    try {
      // 销毁旧实例
      if (mmRef.current) {
        mmRef.current.destroy();
        mmRef.current = null;
      }

      // 清空旧 SVG
      const oldSvg = containerRef.current.querySelector("svg");
      if (oldSvg) oldSvg.remove();

      // 创建新 SVG
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.style.width = "100%";
      svg.style.height = "100%";
      containerRef.current.appendChild(svg);

      // 直接带数据创建
      const root = toMarkmapNode(mindmapData);
      const mm = Markmap.create(svg, {
        autoFit: true,
        // colorFreezeLevel: 3, // removed: not in IMarkmapOptions
        duration: 300,
        initialExpandLevel: 4,
        maxWidth: 280,
        paddingX: 16,
        spacingHorizontal: 80,
        spacingVertical: 8,
      }, root);

      mmRef.current = mm;
      setError("");
    } catch (e: any) {
      setError(`渲染失败: ${e.message || e}`);
    }

    return () => {
      if (mmRef.current) {
        mmRef.current.destroy();
        mmRef.current = null;
      }
      if (containerRef.current) {
        const svg = containerRef.current.querySelector("svg");
        if (svg) svg.remove();
      }
    };
  }, [mindmapData]);

  // ===== 空状态 =====
  if (isLoading) {
    return (
      <div className="flex-1 bg-white border border-zinc-200 rounded-xl flex items-center justify-center gap-3 shadow-sm">
        <Loader2 className="w-5 h-5 animate-spin text-emerald-500" />
        <span className="text-sm text-zinc-500">{progressText || "正在生成脑图..."}</span>
      </div>
    );
  }

  if (!mindmapData) {
    return (
      <div className="flex-1 bg-white border border-zinc-200 rounded-xl flex flex-col items-center justify-center gap-4 p-6 shadow-sm">
        <FileText className="w-8 h-8 text-zinc-300" />
        <p className="text-sm text-zinc-500">尚未生成脑图</p>
        <button onClick={onGenerate} disabled={isLoading}
          className="flex items-center gap-2 px-4 py-2 bg-emerald-500 text-white text-xs rounded-lg hover:bg-emerald-600">
          <RotateCcw className="w-3.5 h-3.5" /> 生成脑图
        </button>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm">
      <div className="px-4 py-2.5 border-b border-zinc-200 flex items-center justify-between gap-3 bg-zinc-50/55 select-none shrink-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <FileText className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
          <span className="text-sm font-medium text-zinc-700 truncate">{documentTitle}</span>
        </div>
        <button onClick={onGenerate} disabled={isLoading}
          className="flex items-center gap-1 px-2.5 py-1 text-xs text-zinc-500 hover:text-emerald-600 hover:bg-emerald-50 rounded">
          <RotateCcw className="w-3 h-3" /> 重新生成
        </button>
      </div>

      {mindmapSessions.length > 0 && (
        <div className="px-3 py-1.5 border-b border-zinc-100 bg-zinc-50/30 flex items-center gap-2 overflow-x-auto shrink-0">
          <span className="text-xs text-zinc-400 shrink-0">历史:</span>
          {mindmapSessions.map(s => (
            <div key={s.id} className="flex items-center gap-1 shrink-0">
              <button onClick={() => onLoadMindmapSession?.(s.data, s.docId)}
                className="text-xs text-zinc-500 hover:text-emerald-600 px-2 py-0.5 rounded hover:bg-emerald-50"
                title={new Date(s.timestamp).toLocaleString()}>
                {s.docTitle?.slice(0, 12)}
              </button>
              <button onClick={() => onDeleteMindmapSession?.(s.id)}
                className="text-xs text-zinc-300 hover:text-rose-500 px-1">×</button>
            </div>
          ))}
        </div>
      )}

      <div ref={containerRef} className="flex-1 w-full" style={{ minHeight: 400 }} />
      {error && <p className="text-xs text-rose-500 px-4 pb-2">{error}</p>}
    </div>
  );
}
