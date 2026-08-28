import { FileText, List } from "lucide-react";

/**
 * T9：DocEditor 工具栏面板（2026-08-06 从 DocEditor 提取）
 * 职责：文档标题编辑 + 侧窗开关 + API 状态横幅，纯受控组件
 */
interface DocToolBarProps {
  title: string;
  onTitleChange: (value: string) => void;
  showSidebar: boolean;
  onToggleSidebar: () => void;
  aiResponseStatus: string;
}

export default function DocToolBar({
  title,
  onTitleChange,
  showSidebar,
  onToggleSidebar,
  aiResponseStatus,
}: DocToolBarProps) {
  return (
    <>
      {/* Workspace Header Panel (Standardized & Symmetrical) */}
      <div className="px-4 py-2 border-b border-zinc-200 bg-zinc-50/50 flex items-center justify-between gap-2 min-w-0">
        <div className="flex items-center gap-1.5 min-w-0 flex-1">
          <FileText className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
          <input
            type="text"
            value={title}
            onChange={(e) => onTitleChange(e.target.value)}
            className="bg-transparent border-0 text-zinc-900 font-bold text-sm focus:outline-none focus:ring-1 focus:ring-zinc-200 rounded px-1 truncate min-w-0 flex-1"
            title="修改文档标题 (修改自动保存)"
          />
        </div>

        {/* Action controllers — compact row, no wrap */}
        <div className="flex items-center gap-2 select-none shrink-0">
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-100 whitespace-nowrap">
            <span className="w-1 h-1 rounded-full bg-emerald-500" />
            阅读模式
          </span>
          <button
            onClick={onToggleSidebar}
            type="button"
            className={`px-2 py-1 border rounded text-[10px] font-semibold flex items-center gap-1 transition-all cursor-pointer whitespace-nowrap ${
              showSidebar
                ? "bg-emerald-50 text-emerald-850 border-emerald-200"
                : "bg-white text-zinc-650 border-zinc-200 hover:text-zinc-800"
            }`}
          >
            <List className="w-3 h-3" />
            侧窗
          </button>
        </div>
      </div>

      {/* API status display ribbon */}
      {aiResponseStatus && (
        <div className="px-5 py-2 bg-emerald-50/10 border-b border-zinc-200 text-emerald-800 text-xs font-semibold flex items-center gap-2 animate-fadeIn select-none border-dashed">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping shrink-0" />
          <span>系统广播: {aiResponseStatus}</span>
        </div>
      )}
    </>
  );
}
