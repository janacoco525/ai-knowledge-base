import React, { useState, useRef } from "react";
import { 
  Folder, FileText, Search, Plus, Trash2, Heart, Database, 
  Edit3, Check, X, ArrowRightLeft, FileUp, FolderInput 
} from "lucide-react";
import { Library, Document } from "../types";
import { saveOriginalFile } from "../lib/fileStorage";

interface SidebarProps {
  width?: number;
  libraries: Library[];
  documents: Document[];
  selectedLibId: string | null;
  selectedDocId: string | null;
  onSelectLibrary: (id: string | null) => void;
  onSelectDocument: (id: string | null) => void;
  onAddLibrary: (name: string, description: string, color: string) => void;
  onDeleteLibrary: (id: string) => void;
  
  // Upgraded Folder/Document management props
  onUpdateLibrary: (id: string, updates: Partial<Library>) => void;
  onUpdateDocument: (id: string, updates: Partial<Document>) => void;
  onDeleteDocument: (id: string) => void;
  onGoToScanning?: () => void;
  fetchDocuments: () => void;
}

export default function Sidebar({
  width,
  libraries,
  documents,
  selectedLibId,
  selectedDocId,
  onSelectLibrary,
  onSelectDocument,
  onAddLibrary,
  onDeleteLibrary,
  onUpdateLibrary,
  onUpdateDocument,
  onDeleteDocument,
  onGoToScanning,
  fetchDocuments,
}: SidebarProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [newLibName, setNewLibName] = useState("");
  const [newLibDesc, setNewLibDesc] = useState("");
  const [newLibColor, setNewLibColor] = useState("emerald");
  const [showLibForm, setShowLibForm] = useState(false);
  // Editing folder states
  const [editingLibId, setEditingLibId] = useState<string | null>(null);
  const [editLibName, setEditLibName] = useState("");
  const [editLibDesc, setEditLibDesc] = useState("");
  const [editLibColor, setEditLibColor] = useState("emerald");

  // Editing document states
  const [editingDocId, setEditingDocId] = useState<string | null>(null);
  const [editDocTitle, setEditDocTitle] = useState("");
  const [movingDocId, setMovingDocId] = useState<string | null>(null);

  // Non-blocking delete confirmations states
  const [libraryDeleteConfirmId, setLibraryDeleteConfirmId] = useState<string | null>(null);
  const [documentDeleteConfirmId, setDocumentDeleteConfirmId] = useState<string | null>(null);
  const [groupDeleteConfirmKey, setGroupDeleteConfirmKey] = useState<string | null>(null);

  // File import states
  const [importingLibId, setImportingLibId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filteredDocs = documents.filter(doc => 
    doc.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
    doc.content.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getLibCount = (libId: string) => {
    return documents.filter(doc => doc.libraryId === libId).length;
  };

  const getLibColorHex = (colorName: string) => {
    switch (colorName) {
      case "emerald": return "bg-emerald-500";
      case "violet": return "bg-purple-500";
      case "sky": return "bg-blue-500";
      case "amber": return "bg-amber-500";
      case "rose": return "bg-rose-500";
      default: return "bg-zinc-400";
    }
  };

  const handleCreateLib = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newLibName.trim()) return;
    onAddLibrary(newLibName.trim(), newLibDesc.trim() || `配置的 ${newLibName} 参考文件夹。`, newLibColor);
    setNewLibName("");
    setNewLibDesc("");
    setShowLibForm(false);
  };

  // Upgraded Library Operations
  const handleStartEditLib = (e: React.MouseEvent, lib: Library) => {
    e.stopPropagation();
    setEditingLibId(lib.id);
    setEditLibName(lib.name);
    setEditLibDesc(lib.description);
    setEditLibColor(lib.color);
  };

  const handleSaveEditLib = (e: React.FormEvent, libId: string) => {
    e.preventDefault();
    if (!editLibName.trim()) return;
    onUpdateLibrary(libId, {
      name: editLibName.trim(),
      description: editLibDesc.trim(),
      color: editLibColor
    });
    setEditingLibId(null);
  };

  // Upgraded Document Operations
  const handleStartEditDoc = (e: React.MouseEvent, doc: Document) => {
    e.stopPropagation();
    setEditingDocId(doc.id);
    setEditDocTitle(doc.title);
  };

  const handleSaveEditDoc = (docId: string) => {
    if (!editDocTitle.trim()) return;
    onUpdateDocument(docId, { title: editDocTitle.trim() });
    setEditingDocId(null);
  };

  const handleMoveDoc = (docId: string, targetLibId: string) => {
    onUpdateDocument(docId, { libraryId: targetLibId });
    setMovingDocId(null);
  };

  // Local drag-and-drop / file input parsing inside sidebar
  const [uploadingLibIds, setUploadingLibIds] = useState<Set<string>>(new Set());

  const handleSidebarFileUpload = async (e: React.ChangeEvent<HTMLInputElement>, libId: string) => {
    const files = e.target.files;
    if (!files) return;

    const allowedExts = [".txt", ".md", ".json", ".pdf", ".docx", ".xlsx", ".xls", ".html", ".htm", ".csv", ".epub", ".mobi"];
    const validFiles = Array.from(files).filter(f => allowedExts.some(ext => f.name.toLowerCase().endsWith(ext)));

    if (validFiles.length === 0) {
      alert("当前支持导入常见文档格式：\nPDF (.pdf)\nEPUB (.epub)\nMOBI (.mobi)\nWord (.docx)\nExcel (.xlsx, .xls)\nHTML (.html, .htm)\nCSV (.csv)\n以及纯文本 Markdown (.md, .txt, .json)");
      return;
    }

    // 查找 library name 作为 domain（而不是用 libId）
    const lib = libraries.find(l => l.id === libId);
    if (!lib) {
      alert("找不到对应文件夹，请刷新后重试");
      return;
    }
    const domainName = lib.name;

    setUploadingLibIds(prev => {
      const next = new Set(prev);
      next.add(libId);
      return next;
    });

    try {
      const formData = new FormData();
      validFiles.forEach(f => formData.append("files", f));

      const resp = await fetch(`/api/kb/upload?domain=${encodeURIComponent(domainName)}`, {
        method: "POST",
        body: formData,
      });
      const result = await resp.json();

      if (result.uploaded > 0) {
        for (const file of validFiles) {
          if (file.name.toLowerCase().endsWith(".pdf")) {
            await saveOriginalFile(file.name.replace(/\.[^/.]+$/, ""), file);
          }
        }
        fetchDocuments();
      }

      // 上传失败的详细反馈
      if (result.failed > 0) {
        const errDetail = result.errors?.length ? `\n错误详情:\n${result.errors.join('\n')}` : '';
        alert(`上传完成：${result.uploaded} 个成功，${result.failed} 个失败${errDetail}`);
      } else if (result.uploaded === 0 && result.skipped > 0) {
        alert(`所有文件均被跳过（不支持的文件格式或无法解析）。已跳过 ${result.skipped} 个文件。`);
      } else if (result.uploaded === 0 && result.failed === 0) {
        alert(`上传失败：服务器未接受任何文件。请检查文件格式是否支持。`);
      }
    } catch (err: any) {
      console.error("Failed to upload files", err);
      alert(`上传失败: ${err.message || "未知错误"}`);
    } finally {
      setUploadingLibIds(prev => {
        const next = new Set(prev);
        next.delete(libId);
        return next;
      });
      // 清空 input
      e.target.value = "";
    }
  };

  return (
    <aside id="sidebar-panel" style={width ? { width: `${width}px`, willChange: 'width' } : undefined} className="bg-white border-r border-zinc-200 flex flex-col h-full overflow-hidden select-none shrink-0">
      {/* Brand Workspace Header */}
      <div className="px-5 py-3 flex items-center justify-between border-b border-zinc-100 bg-zinc-50/20">
        <div className="flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          <span className="text-zinc-800 font-bold text-xs">文献数据库</span>
        </div>
      </div>
      <div className="p-3 border-b border-zinc-100 bg-zinc-50/10">
        <div className="relative">
          <span className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
            <Search className="w-3.5 h-3.5 text-zinc-400" />
          </span>
          <input
            type="text"
            placeholder="搜索当前档案内容..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-8.5 pr-3 py-1 bg-zinc-100/50 border border-zinc-200 text-zinc-800 text-xs rounded-md focus:outline-none focus:border-zinc-500 hover:bg-zinc-100/80 transition-all placeholder-zinc-400 font-medium"
          />
        </div>
      </div>

      {/* Scrollable Folder Lists */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3.5">
        
        {/* Help Banner linking directly to path scanners */}
        <div className="bg-gradient-to-br from-emerald-50 to-teal-50/50 border border-emerald-100/80 rounded-lg px-2.5 py-2 mx-1 select-none text-left shadow-2xs">
          <div className="flex items-center justify-between gap-1.5">
            <h4 className="text-[11px] font-bold text-emerald-800 flex items-center gap-1">
              <FolderInput className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
              路径扫描&文档导入
            </h4>
            <button
              type="button"
              onClick={() => onGoToScanning?.()}
              className="text-[9px] font-bold text-emerald-700 hover:text-emerald-950 flex items-center gap-0.5 group cursor-pointer bg-white px-2 py-0.5 border border-emerald-200/50 rounded shadow-3xs"
            >
              扫描 <span className="group-hover:translate-x-0.5 transition-transform duration-150">→</span>
            </button>
          </div>
        </div>

        {/* Categories Section */}
        <div>
          <div className="flex items-center justify-between px-3 mb-2 text-zinc-400 select-none">
            <span className="flex items-center gap-1.5 text-zinc-805 font-bold font-sans text-[13px]">文档管理</span>
            <span className="text-[10px] text-zinc-400 font-mono">{documents.length}篇</span>
          </div>

          {/* 文档列表 - 按目录分组 */}
          <div className="space-y-2 px-1">
            {filteredDocs.length === 0 ? (
              <p className="text-[10px] text-zinc-400 px-2 py-3 text-center">暂无文档，请先扫描入库</p>
            ) : (
              (() => {
                // 按 libraryId (目录) 分组 — 但与 libraries 去重，避免下方 Libraries Directory list 重复渲染
                const groups: Record<string, typeof filteredDocs> = {};
                const libNames = new Set(libraries.map(l => l.name));
                filteredDocs.forEach(doc => {
                  const key = doc.libraryId || "未分类";
                  // 跳过已在 libraries 中存在的名称（由下方 Libraries Directory list 渲染）
                  if (key !== "未分类" && libNames.has(key)) return;
                  if (!groups[key]) groups[key] = [];
                  groups[key].push(doc);
                });
                if (Object.keys(groups).length === 0) return null;
                return Object.entries(groups).map(([groupKey, docs]) => (
                  <div key={groupKey}>
                    <div className="text-[11px] text-zinc-600 font-semibold px-2 py-1.5 flex items-center gap-1.5 group rounded hover:bg-zinc-50">
                      <span className="w-1.5 h-1.5 shrink-0 rounded-full bg-zinc-400" />
                      <span className="truncate">{groupKey}</span>
                      <span className="text-[10px] text-zinc-400 font-mono ml-auto">{docs.length}</span>
                      {groupDeleteConfirmKey === groupKey ? (
                        <div className="mx-2 mt-0.5 px-2 py-1 bg-rose-50/80 border border-rose-200/60 rounded text-[10px] text-rose-600 font-medium animate-fadeIn select-none">
                          <div className="flex items-center justify-between">
                            <span>删除「{groupKey}」下 {docs.length} 篇文档？</span>
                            <div className="flex items-center gap-1 shrink-0 ml-2">
                              <button
                                type="button"
                                onClick={async (e) => {
                                  e.stopPropagation();
                                  for (const doc of docs) {
                                    await onDeleteDocument(doc.id);
                                  }
                                  setGroupDeleteConfirmKey(null);
                                }}
                                className="text-[9px] px-1.5 py-0.5 bg-rose-500 text-white rounded font-bold hover:bg-rose-600"
                              >
                                确认
                              </button>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setGroupDeleteConfirmKey(null);
                                }}
                                className="text-[9px] px-1.5 py-0.5 bg-zinc-200 text-zinc-600 rounded hover:bg-zinc-300"
                              >
                                取消
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setGroupDeleteConfirmKey(groupKey);
                          }}
                          className="p-0.5 text-zinc-400 hover:text-rose-500 rounded transition-colors shrink-0"
                          title={`删除「${groupKey}」分组下所有文档`}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                    <div className="space-y-0.5 ml-2 border-l border-zinc-200 pl-2">
                      {docs.map(doc => (
                        <div key={doc.id} className="group/doc">
                          <div className="flex items-center group/row">
                            <button
                              type="button"
                              onClick={() => onSelectDocument(doc.id)}
                              className={`flex-1 text-left px-2 py-1.5 rounded-md text-[11px] flex items-center gap-1.5 transition-all truncate ${
                                selectedDocId === doc.id
                                  ? "text-zinc-950 font-semibold bg-zinc-100"
                                  : "text-zinc-600 hover:text-zinc-900 hover:bg-zinc-50"
                              }`}
                            >
                              <FileText className="w-3 h-3 text-zinc-400 shrink-0" />
                              <span className="truncate">{doc.title}</span>
                            </button>
                            {documentDeleteConfirmId !== doc.id && (
                              <button
                                type="button"
                                onClick={(e) => { e.stopPropagation(); setDocumentDeleteConfirmId(doc.id); }}
                                className="p-0.5 text-zinc-400 hover:text-rose-500 transition-colors shrink-0"
                                title="删除文档"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            )}
                          </div>
                          {/* 删除确认提示条 */}
                          {documentDeleteConfirmId === doc.id && (
                            <div className="mx-2 mt-0.5 px-2 py-1 bg-rose-50/80 border border-rose-200/60 rounded text-[10px] text-rose-600 font-medium animate-fadeIn select-none">
                              <div className="flex items-center justify-between">
                                <span>删除「{doc.title.length > 12 ? doc.title.slice(0, 12) + '…' : doc.title}」？</span>
                                <div className="flex items-center gap-1 shrink-0 ml-2">
                                  <button
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); onDeleteDocument(doc.id); setDocumentDeleteConfirmId(null); }}
                                    className="text-[9px] px-1.5 py-0.5 bg-rose-500 text-white rounded font-bold hover:bg-rose-600"
                                  >
                                    确认
                                  </button>
                                  <button
                                    type="button"
                                    onClick={(e) => { e.stopPropagation(); setDocumentDeleteConfirmId(null); }}
                                    className="text-[9px] px-1.5 py-0.5 bg-zinc-200 text-zinc-600 rounded hover:bg-zinc-300"
                                  >
                                    取消
                                  </button>
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ));
              })()
            )}
          </div>
        </div>

        {/* Library creation button */}
        <div className="px-3 mb-2">
          <button
            onClick={() => setShowLibForm(!showLibForm)}
            className="text-zinc-500 hover:text-zinc-800 p-0.5 rounded transition-colors cursor-pointer"
            title="创建新知识夹"
            type="button"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>

          {/* New Library Garden Inline Creation Form */}
          {showLibForm && (
            <form onSubmit={handleCreateLib} className="m-2 p-3 bg-zinc-50 border border-zinc-200 rounded-lg space-y-3 shadow-sm">
              <input
                type="text"
                required
                placeholder="文件夹名称..."
                value={newLibName}
                onChange={(e) => setNewLibName(e.target.value)}
                className="w-full px-2.5 py-1.5 bg-white border border-zinc-200 text-zinc-800 text-xs rounded focus:outline-none focus:border-emerald-500"
              />
              <input
                type="text"
                placeholder="文件夹描述（选填）"
                value={newLibDesc}
                onChange={(e) => setNewLibDesc(e.target.value)}
                className="w-full px-2.5 py-1.5 bg-white border border-zinc-200 text-zinc-550 text-[11px] rounded focus:outline-none focus:border-emerald-500"
              />
              <div className="flex items-center justify-between">
                <div className="flex gap-1.55">
                  {(["emerald", "violet", "sky", "amber", "rose"] as const).map(color => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setNewLibColor(color)}
                      className={`w-3.5 h-3.5 rounded-full ${getLibColorHex(color)} border-2 ${
                        newLibColor === color ? "border-zinc-800" : "border-transparent"
                      }`}
                    />
                  ))}
                </div>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    onClick={() => setShowLibForm(false)}
                    className="px-2 py-1 text-[10px] bg-zinc-200 text-zinc-650 rounded hover:bg-zinc-300 transition-colors"
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    className="px-2 py-1 text-[10px] bg-zinc-900 text-white font-semibold rounded hover:bg-zinc-800 transition-colors"
                  >
                    创建
                  </button>
                </div>
              </div>
            </form>
          )}

          {/* Libraries Directory list */}
          <div className="space-y-1">
            {libraries.map((lib) => {
              const isActive = selectedLibId === lib.id;
              const isEditing = editingLibId === lib.id;
              const count = getLibCount(lib.id);

              return (
                <div key={lib.id} className="group">
                  {isEditing ? (
                    <form onSubmit={(e) => handleSaveEditLib(e, lib.id)} className="p-3 bg-zinc-50 border border-zinc-200 rounded-lg m-1 space-y-2 select-text">
                      <input
                        type="text"
                        required
                        value={editLibName}
                        onChange={(e) => setEditLibName(e.target.value)}
                        className="w-full px-2 py-1 text-xs border border-zinc-350 focus:border-emerald-500 rounded bg-white"
                      />
                      <input
                        type="text"
                        value={editLibDesc}
                        onChange={(e) => setEditLibDesc(e.target.value)}
                        placeholder="描述..."
                        className="w-full px-2 py-1 text-[10px] text-zinc-550 border border-zinc-350 focus:border-emerald-500 rounded bg-white"
                      />
                      <div className="flex items-center justify-between pt-1">
                        <div className="flex gap-1.5">
                          {(["emerald", "violet", "sky", "amber", "rose"] as const).map(color => (
                            <button
                              key={color}
                              type="button"
                              onClick={() => setEditLibColor(color)}
                              className={`w-3 h-3 rounded-full ${getLibColorHex(color)} border ${
                                editLibColor === color ? "border-zinc-850" : "border-transparent"
                              }`}
                            />
                          ))}
                        </div>
                        <div className="flex gap-1">
                          <button
                            type="button"
                            onClick={() => setEditingLibId(null)}
                            className="bg-zinc-100 border border-zinc-200 px-1.5 py-0.5 text-[9px] font-medium rounded hover:bg-zinc-200 text-zinc-700"
                          >
                            取消
                          </button>
                          <button
                            type="submit"
                            className="bg-zinc-950 text-white px-1.5 py-0.5 text-[9px] font-medium rounded hover:bg-zinc-900"
                          >
                            保存
                          </button>
                        </div>
                      </div>
                    </form>
                  ) : (
                    <div>
                      <div
                        onClick={() => onSelectLibrary(lib.id)}
                        className={`px-3 py-2 rounded-md text-xs font-semibold flex items-center justify-between cursor-pointer transition-colors ${
                          isActive
                            ? "bg-zinc-100 text-zinc-900 font-bold"
                            : "text-zinc-650 hover:bg-zinc-50 hover:text-zinc-900"
                        }`}
                      >
                        <div className="flex items-center gap-2 overflow-hidden truncate">
                          <span className={`w-1.5 h-1.5 shrink-0 rounded-full ${lib.color === "emerald" ? "bg-zinc-600" : getLibColorHex(lib.color)}`} />
                          <span className="truncate" title={lib.description}>{lib.name}</span>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0 ml-2">
                          <span className="text-[10px] text-zinc-400 hover:text-zinc-700 font-mono">
                            {count}
                          </span>
                          
                          {libraryDeleteConfirmId !== lib.id && (
                            <div className="flex items-center gap-0.5 shrink-0 transition-opacity">
                              {/* Folder Management: Import files */}
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setImportingLibId(lib.id);
                                  fileInputRef.current?.click();
                                }}
                                className="p-0.5 text-zinc-400 hover:text-emerald-500 rounded transition-colors"
                                title="导入文档到此文件夹"
                              >
                                <FileUp className="w-3 h-3" />
                              </button>

                              {/* Folder Management: Edit pencil */}
                              <button
                                onClick={(e) => handleStartEditLib(e, lib)}
                                className="p-0.5 text-zinc-400 hover:text-zinc-700 rounded transition-colors"
                                title="编辑文件夹"
                              >
                                <Edit3 className="w-3 h-3" />
                              </button>

                              {/* Folder Management: Delete Trash */}
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setLibraryDeleteConfirmId(lib.id);
                                }}
                                className="p-0.5 text-zinc-400 hover:text-rose-500 rounded transition-colors"
                                title="删除整个文件夹"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                      {/* 文件夹删除确认提示条 */}
                      {libraryDeleteConfirmId === lib.id && (
                        <div className="mx-3 mt-0.5 px-2 py-1 bg-rose-50/80 border border-rose-200/60 rounded text-[10px] text-rose-600 font-medium animate-fadeIn select-none">
                          <div className="flex items-center justify-between">
                            <span>删除「{lib.name}」及 {count} 篇文档？</span>
                            <div className="flex items-center gap-1 shrink-0 ml-2">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onDeleteLibrary(lib.id);
                                  setLibraryDeleteConfirmId(null);
                                }}
                                className="text-[9px] px-1.5 py-0.5 bg-rose-500 text-white rounded font-bold hover:bg-rose-600"
                              >
                                确认
                              </button>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setLibraryDeleteConfirmId(null);
                                }}
                                className="text-[9px] px-1.5 py-0.5 bg-zinc-200 text-zinc-600 rounded hover:bg-zinc-300"
                              >
                                取消
                              </button>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Documents aligned inside this library — 默认展开 */}
                  {!isEditing && (
                    <div className="mt-1 ml-3 px-1.5 py-1 bg-zinc-50/50 rounded-lg border-l border-zinc-200 space-y-0.5">
                      
                      {/* Documents listing inside library */}
                      {filteredDocs
                        .filter(doc => doc.libraryId === lib.id)
                        .map(doc => {
                          const isDocEditing = editingDocId === doc.id;
                          const isMoving = movingDocId === doc.id;

                          return (
                            <div key={doc.id} className="group/doc pb-0.5 last:border-none">
                              {isDocEditing ? (
                                <div className="flex items-center gap-1 p-1 select-text">
                                  <input
                                    type="text"
                                    value={editDocTitle}
                                    onChange={(e) => setEditDocTitle(e.target.value)}
                                    className="flex-1 px-1.5 py-0.5 text-[10px] border border-zinc-250 focus:border-zinc-500 rounded bg-white font-medium"
                                  />
                                  <button
                                    onClick={() => handleSaveEditDoc(doc.id)}
                                    className="p-1 bg-zinc-900 text-white rounded hover:bg-zinc-800"
                                  >
                                    <Check className="w-2.5 h-2.5" />
                                  </button>
                                  <button
                                    onClick={() => setEditingDocId(null)}
                                    className="p-1 bg-zinc-100 text-zinc-600 rounded hover:bg-zinc-200"
                                  >
                                    <X className="w-2.5 h-2.5" />
                                  </button>
                                </div>
                              ) : isMoving ? (
                                <div className="p-1 bg-zinc-50 rounded border border-zinc-200 select-text">
                                  <span className="text-[9px] text-zinc-400 font-medium block mb-1">移动至目标架:</span>
                                  <select
                                    onChange={(e) => handleMoveDoc(doc.id, e.target.value)}
                                    defaultValue=""
                                    className="w-full text-[9px] font-bold py-0.5 px-1 bg-white border border-zinc-300 rounded focus:outline-none"
                                  >
                                    <option value="" disabled>-- 选择文件夹 --</option>
                                    {libraries.filter(l => l.id !== lib.id).map(l => (
                                      <option key={l.id} value={l.id}>{l.name}</option>
                                    ))}
                                  </select>
                                  <button
                                    type="button"
                                    onClick={() => setMovingDocId(null)}
                                    className="w-full text-center hover:bg-zinc-200 text-[8px] text-zinc-500 font-bold mt-1 py-0.5 rounded transition-colors"
                                  >
                                    取消
                                  </button>
                                </div>
                              ) : (
                                <div className="flex items-center justify-between rounded hover:bg-zinc-100/30 transition-all select-none">
                                  <button
                                    type="button"
                                    onClick={() => onSelectDocument(doc.id)}
                                    className={`flex-1 text-left px-2 py-1.5 rounded-md text-[11px] flex items-center gap-1.5 transition-all truncate ${
                                      selectedDocId === doc.id
                                        ? "text-zinc-950 font-semibold bg-zinc-100"
                                        : "text-zinc-550 hover:text-zinc-900"
                                    }`}
                                  >
                                    <FileText className="w-3 h-3 text-zinc-400 shrink-0" />
                                    <span className="truncate">{doc.title}</span>
                                  </button>

                                  {/* Document Action overlay */}
                                  <div className="flex items-center gap-0.5 pr-1 transition-all select-none shrink-0 min-w-0">
                                    {documentDeleteConfirmId === doc.id ? (
                                      <div className="flex items-center gap-0.5 bg-red-50/90 border border-red-200/50 px-1 py-0.5 rounded animate-fadeIn select-text shrink-0">
                                        <span className="text-[8px] font-bold text-red-600 shrink-0">删?</span>
                                        <button
                                          type="button"
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            onDeleteDocument(doc.id);
                                            setDocumentDeleteConfirmId(null);
                                          }}
                                          className="text-[8px] px-1 bg-red-655 hover:bg-red-700 text-white rounded font-bold cursor-pointer"
                                        >
                                          是
                                        </button>
                                        <button
                                          type="button"
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setDocumentDeleteConfirmId(null);
                                          }}
                                          className="text-[8px] px-1 bg-zinc-200 hover:bg-zinc-300 text-zinc-700 rounded font-medium cursor-pointer"
                                        >
                                          否
                                        </button>
                                      </div>
                                    ) : (
                                      <>
                                        {/* Rename */}
                                        <button
                                          type="button"
                                          onClick={(e) => handleStartEditDoc(e, doc)}
                                          className="p-0.5 hover:bg-zinc-250 text-zinc-400 hover:text-zinc-800 rounded animate-fadeIn"
                                          title="重命名该文档"
                                        >
                                          <Edit3 className="w-2.5 h-2.5" />
                                        </button>

                                        {/* Move Folder link */}
                                        <button
                                          type="button"
                                          onClick={(e) => { e.stopPropagation(); setMovingDocId(doc.id); }}
                                          className="p-0.5 hover:bg-zinc-250 text-zinc-400 hover:text-zinc-800 rounded animate-fadeIn"
                                          title="迁移至其他文件夹"
                                        >
                                          <FolderInput className="w-2.5 h-2.5" />
                                        </button>

                                        {/* Delete Document */}
                                        <button
                                          type="button"
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setDocumentDeleteConfirmId(doc.id);
                                          }}
                                          className="p-0.5 hover:bg-rose-50 text-zinc-400 hover:text-rose-600 rounded animate-fadeIn"
                                          title="注销文档"
                                        >
                                          <Trash2 className="w-2.5 h-2.5" />
                                        </button>
                                      </>
                                    )}
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}

                      {filteredDocs.filter(doc => doc.libraryId === lib.id).length === 0 && (
                        <p className="text-[10px] text-zinc-400 px-3 py-1 italic">未含有文档</p>
                      )}

                      {/* Add document + local file uploaders area nested */}
                      <div className="pt-2 border-t border-zinc-200 mt-1.5 space-y-1">
                        
                        {/* 上传中状态指示（仅当前 lib 在上传时显示） */}
                        {uploadingLibIds.has(lib.id) && (
                          <span className="px-1 py-0.5 text-[9px] text-emerald-600 flex items-center gap-0.5 rounded font-mono animate-pulse">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                            解析中...
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
      </div>

      {/* Hidden file input for library import */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".txt,.md,.json,.pdf,.docx,.xlsx,.xls,.html,.htm,.csv,.epub,.mobi"
        onChange={(e) => {
          if (importingLibId) {
            handleSidebarFileUpload(e, importingLibId);
            setImportingLibId(null);
          }
        }}
        className="hidden"
      />
    </aside>
  );
}
