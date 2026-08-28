import React, { useState, useEffect } from "react";
import { Document, Library } from "../types";
import { 
  Sparkles, FileText, ArrowLeftRight, Check, HelpCircle, 
  Copy, Download, AlertCircle, RefreshCw, Folder, CheckSquare, 
  Square, FileUp, FolderOpen, Eye, BookOpen, Terminal, CheckCircle, Trash2, ChevronDown, ChevronRight
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { parseFileToText } from "../lib/documentParser";
import { saveOriginalFile } from "../lib/fileStorage";
import remarkGfm from "remark-gfm";

interface DocComparisonProps {
  documents: Document[];
  libraries: Library[];
  onAddDocument: (title: string, libraryId: string, content: string, tags: string[]) => void;
  onAddLibrary: (name: string, description: string, color: string, customId?: string) => void;
  defaultSubView?: "compare" | "scan";
  fetchDocuments?: () => void;
}

export default function DocComparison({
  documents,
  libraries,
  onAddDocument,
  onAddLibrary,
  defaultSubView = "compare",
  fetchDocuments,
}: DocComparisonProps) {
  // Navigation inside Comparison & Scanner tab
  const [activeSubView, setActiveSubView] = useState<"import" | "compare">(defaultSubView === "scan" ? "import" : defaultSubView);

  useEffect(() => {
    // 兼容旧 defaultSubView 值："scan" → "import"
    setActiveSubView(defaultSubView === "scan" ? "import" : defaultSubView as "import" | "compare");
  }, [defaultSubView]);

  // --- CROSS DOCUMENT COMPARISON MODULE STATES ---
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [activeFolderId, setActiveFolderId] = useState<string>("all");
  const [comparisonResult, setComparisonResult] = useState<string>("");
  const [isComparing, setIsComparing] = useState<boolean>(false);
  const [copySuccess, setCopySuccess] = useState<boolean>(false);

  // --- SINGLE DOCUMENT PREVIEWER DRAWER/POP STATES ---
  const [readingDocTitle, setReadingDocTitle] = useState<string>("");
  const [readingDocContent, setReadingDocContent] = useState<string>("");
  const [readingFontSize, setReadingFontSize] = useState<number>(13); // text-xs
  const [readingHighlightQuery, setReadingHighlightQuery] = useState<string>("");

  // --- LOCAL SCANNING & DISCOVERY STATES ---
  const [isBrowserScanning, setIsBrowserScanning] = useState(false);
  const [scannedFilesList, setScannedFilesList] = useState<Array<{
    title: string;
    folder: string;
    folderPath: string[];
    content: string;
    path: string;
    ext: string;
    size?: number;
  }>>([]);
  const [selectedScanPaths, setSelectedScanPaths] = useState<string[]>([]);
  const [scanStatusMsg, setScanStatusMsg] = useState("");
  const [collapsedFolders, setCollapsedFolders] = useState<Record<string, boolean>>({});

  // Drag and Drop State for fast single upload
  const [dragActive, setDragActive] = useState(false);
  const [uploadTargetFolder, setUploadTargetFolder] = useState<string>(
    libraries.length > 0 ? libraries[0].id : "lib-ai"
  );
  const [uploadMessage, setUploadMessage] = useState({ text: "", type: "" });

  const currentFolderDocs = activeFolderId === "all" 
    ? documents 
    : documents.filter(doc => doc.libraryId === activeFolderId);

  // --- UTILS FOR CORE COMPARISON ---
  const handleToggleDoc = (docId: string) => {
    setSelectedDocIds(prev => {
      if (prev.includes(docId)) {
        return prev.filter(id => id !== docId);
      } else {
        if (prev.length >= 5) {
          alert("为了交叉提防精度，单次联合比对上限为 5 篇。");
          return prev;
        }
        return [...prev, docId];
      }
    });
  };

  const handleSelectAllInFolder = () => {
    const folderDocIds = currentFolderDocs.map(d => d.id);
    const allSelected = folderDocIds.every(id => selectedDocIds.includes(id));
    
    if (allSelected) {
      setSelectedDocIds(prev => prev.filter(id => !folderDocIds.includes(id)));
    } else {
      setSelectedDocIds(prev => {
        const newlyAdded = [...prev];
        for (const id of folderDocIds) {
          if (!newlyAdded.includes(id)) {
            if (newlyAdded.length < 5) {
              newlyAdded.push(id);
            } else {
              alert("联合比对上限 5 篇，剩余文件已被排除。");
              break;
            }
          }
        }
        return newlyAdded;
      });
    }
  };

  // 兜底归一化（2026-08-07）：LLM 若未遵守提示词约束、把多个对比维度用 " | " 堆在一行，
  // 渲染前拆成列表——避免密集无层级的纯文本段落（表格行/标题/已有列表项不碰）
  const normalizeComparisonMarkdown = (md: string): string => {
    return md.split("\n").map(line => {
      const t = line.trim();
      if (!t || t.startsWith("|") || t.startsWith("#") || t.startsWith("-") || t.startsWith("*") || t.startsWith(">")) return line;
      if (!t.includes(" | ")) return line;
      const parts = t.split(" | ").map(p => p.trim()).filter(Boolean);
      if (parts.length < 2) return line;
      return parts.map(p => `- ${p}`).join("\n");
    }).join("\n");
  };

  const handleTriggerCompare = async () => {
    if (selectedDocIds.length < 2) return;
    setIsComparing(true);
    setComparisonResult("");
    
    const selectedDocs = documents.filter(doc => selectedDocIds.includes(doc.id));

    try {
      // 只传 id/title + 截断正文：后端会按 id 从知识库自取全文，避免超大 JSON 上传
      const payload = selectedDocs.map(doc => ({ id: doc.id, title: doc.title, content: (doc.content || "").slice(0, 6000) }));
      const resp = await fetch("/api/gemini/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ documents: payload }),
      });

      if (!resp.ok) {
        // 读后端 detail，让用户看到真实原因（如“有效内容不足 2 篇”）而非笼统报错
        let detail = "比对服务通信异常";
        try { const e = await resp.json(); if (e?.detail) detail = e.detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const data = await resp.json();
      if (!data.comparison) throw new Error("比对服务返回空结果");
      setComparisonResult(normalizeComparisonMarkdown(data.comparison));
    } catch (err: any) {
      console.error(err);
      setComparisonResult(`🛑 **比对服务异常**: ${err.message || "请求失败"}\n\n若提示内容不足，请确认所选文档已入库且有正文（列表字符数 > 0）。`);
    } finally {
      setIsComparing(false);
    }
  };

  // --- HIGH FIDELITY DOCUMENT SCAN CONTENT VIEW MODULE ---
  const handleOpenEmbeddedReader = (title: string, content: string) => {
    setReadingDocTitle(title);
    setReadingDocContent(content);
    setReadingHighlightQuery("");
  };

  const renderHighlightedContent = (text: string, query: string) => {
    if (!query.trim()) return <div className="whitespace-pre-wrap">{text}</div>;
    const regex = new RegExp(`(${query.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')})`, 'gi');
    const parts = text.split(regex);
    return (
      <div className="whitespace-pre-wrap">
        {parts.map((part, i) => 
          regex.test(part) 
            ? <mark key={i} className="bg-yellow-250 text-zinc-900 font-bold font-mono px-0.5 rounded">{part}</mark>
            : part
        )}
      </div>
    );
  };

  // --- DIRECTORY RECURSION DISCOVERY DRIVERS ---
  
  // 1. 文件夹扫描（浏览器选文件夹 → 上传到后端）
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{done: number; total: number} | null>(null);

  const handleFolderSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    const files = Array.from(e.target.files);
    const allowedExts = [".pdf", ".epub", ".mobi", ".docx", ".xlsx", ".xls", ".html", ".htm", ".csv", ".txt", ".md", ".json"];
    const validFiles = files.filter(f => allowedExts.some(ext => f.name.toLowerCase().endsWith(ext)));

    if (validFiles.length === 0) {
      setScanStatusMsg("未找到支持的文件格式");
      return;
    }

    setIsUploading(true);
    setUploadProgress({ done: 0, total: validFiles.length });
    setScanStatusMsg(`正在上传 ${validFiles.length} 个文件到知识库...`);

    // 从第一个文件的 webkitRelativePath 提取文件夹名作为 domain
    const firstPath = validFiles[0].webkitRelativePath || "";
    const folderName = firstPath.split("/")[0] || "default";

    const formData = new FormData();
    validFiles.forEach(f => formData.append("files", f));

    try {
      const resp = await fetch(`/api/kb/upload?domain=${encodeURIComponent(folderName)}`, {
        method: "POST",
        body: formData,
      });
      const result = await resp.json();
      setScanStatusMsg(`上传完成：${result.uploaded} 个已索引，${result.skipped} 个跳过，${result.failed} 个失败`);
      fetchDocuments();
    } catch (err: any) {
      setScanStatusMsg(`上传失败: ${err.message}`);
    } finally {
      setIsUploading(false);
      setUploadProgress(null);
      e.target.value = "";
    }
  };

  const handleStopScan = async () => {
    try {
      await fetch("/api/scan/stop", { method: "POST" });
      setScanStatusMsg("扫描已停止");
    } catch {}
  };

  // 2. Browser recursive upload scanning (webkitdirectory)
  const handleBrowserFolderScan = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    setIsBrowserScanning(true);
    setScanStatusMsg("游览器正在递归建立并解析多格式关联目录树...");
    
    const scanned: typeof scannedFilesList = [];
    const files = e.target.files;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const name = file.name;
      const ext = name.split(".").pop()?.toLowerCase() || "";
      const allowedExts = ["md", "txt", "json", "pdf", "docx", "xlsx", "xls", "html", "htm", "csv", "epub", "mobi"];
      
      if (allowedExts.includes(ext)) {
        try {
          // PDF 存原始文件到 IndexedDB，供后续 PDF 浏览模式使用
          if (ext === "pdf") {
            await saveOriginalFile(name.replace(/\.[^/.]+$/, ""), file);
          }
          const content = await parseFileToText(file);
          // Extract nested directory name from webkitRelativePath (e.g. root/folder/sub/file.md)
          const relPath = file.webkitRelativePath || name;
          const parts = relPath.split("/");
          // 结构化目录层级（2026-08-20）：folderPath 保留完整层级数组，
          // 供后续树形分组/折叠/统计直接使用，不再靠字符串拼接表达层级
          let folderPath: string[] = [];
          if (parts.length > 2) {
            folderPath = parts.slice(1, -1);
          } else if (parts.length === 2) {
            folderPath = [parts[0]]; // main wrapper dir
          }
          // 显示串由结构化层级派生，保持与旧版展示一致
          const subFolderName = folderPath.length > 0 ? folderPath.join(" -> ") : "Workspace Root";

          scanned.push({
            title: name.replace(/\.[^/.]+$/, ""),
            folder: subFolderName,
            folderPath,
            content,
            path: relPath,
            ext,
            size: file.size
          });
        } catch (err) {
          console.error("Skipped loading unreadable file:", name, err);
        }
      }
    }

    if (scanned.length > 0) {
      setScannedFilesList(scanned);
      // Auto-check all scanned files path for importing
      setSelectedScanPaths(scanned.map(s => s.path));
      setScanStatusMsg(`成功扫描到 ${scanned.length} 个本地文档。可在下方预览或导入：`);
    } else {
      setScanStatusMsg("未检索到符合条件的文档（支持 .pdf, .docx, .xlsx, .xls, .csv, .html, .txt, .md, .json, .epub, .mobi）。");
    }
    setIsBrowserScanning(false);
  };

  const handleToggleScanPath = (path: string) => {
    setSelectedScanPaths(prev => 
      prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path]
    );
  };

  const handleToggleScanFolder = (folderName: string, pathsInFolder: string[]) => {
    const allChecked = pathsInFolder.every(p => selectedScanPaths.includes(p));
    if (allChecked) {
      setSelectedScanPaths(prev => prev.filter(p => !pathsInFolder.includes(p)));
    } else {
      setSelectedScanPaths(prev => {
        const added = [...prev];
        pathsInFolder.forEach(p => {
          if (!added.includes(p)) added.push(p);
        });
        return added;
      });
    }
  };

  // 3. Batch install scanned elements into main application state
  const handleBatchImportScanResult = () => {
    const importItems = scannedFilesList.filter(s => selectedScanPaths.includes(s.path));
    if (importItems.length === 0) {
      alert("❌ 请在目录树中勾选需要发现并导入的文档路径！");
      return;
    }

    let libraryInstallCount = 0;
    let fileInstallCount = 0;
    
    // Memory map tracking duplicate libraries created within the single loop execution
    const tempCreatedLibraries = new Map<string, string>();

    // Fast-map folder directory logic
    importItems.forEach((item, idx) => {
      // Find if Library folder already exists in global libraries state
      const matchedLib = libraries.find(l => l.name.toLowerCase() === item.folder.toLowerCase());
      let currentLibId = matchedLib?.id;

      // If library is not in global state, check if we've registered it in this batch
      if (!currentLibId && tempCreatedLibraries.has(item.folder.toLowerCase())) {
        currentLibId = tempCreatedLibraries.get(item.folder.toLowerCase());
      }

      if (!currentLibId) {
        // 直接用 folder 名当 ID，确保前后端 domain=libraryId 统一
        currentLibId = item.folder;
        tempCreatedLibraries.set(item.folder.toLowerCase(), item.folder);
        
        onAddLibrary(
          item.folder,
          `通过本地目录扫描器全自动发现挂载的「${item.folder}」关联库`,
          "zinc"
        );
        libraryInstallCount++;
      }

      // Add actual Document to this library folder
      onAddDocument(
        item.title,
        currentLibId || "lib-ai",
        item.content,
        ["本地扫描", item.ext.toUpperCase()]
      );
      fileInstallCount++;
    });

    alert(`🎉 物理导入成功！\n- 新增思维文件夹 (Library): ${libraryInstallCount} 个\n- 自动补全且入库文档 (Document): ${fileInstallCount} 篇`);
    setScannedFilesList([]);
    setSelectedScanPaths([]);
    setScanStatusMsg("");
  };

  // Render scan results grouped by folders
  const groupedScanned = scannedFilesList.reduce((acc, current) => {
    acc[current.folder] = acc[current.folder] || [];
    acc[current.folder].push(current);
    return acc;
  }, {} as Record<string, typeof scannedFilesList>);

  // File drag & upload processors
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") setDragActive(true);
    else if (e.type === "dragleave") setDragActive(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      await processUploadedFiles(e.dataTransfer.files);
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      await processUploadedFiles(e.target.files);
    }
  };

  const processUploadedFiles = async (files: FileList) => {
    const allowedExts = [".txt", ".md", ".json", ".pdf", ".docx", ".xlsx", ".xls", ".html", ".htm", ".csv", ".epub", ".mobi"];
    const validFiles = Array.from(files).filter(f => allowedExts.some(ext => f.name.toLowerCase().endsWith(ext)));

    if (validFiles.length === 0) {
      setUploadMessage({ text: "不支持的文件格式", type: "error" });
      return;
    }

    const formData = new FormData();
    validFiles.forEach(f => formData.append("files", f));

    try {
      const resp = await fetch(`/api/kb/upload?domain=${encodeURIComponent(uploadTargetFolder)}`, {
        method: "POST",
        body: formData,
      });
      const result = await resp.json();
      if (result.uploaded > 0) {
        setScanStatusMsg(`上传完成：${result.uploaded} 个已索引，${result.skipped} 个跳过，${result.failed} 个失败`);
        fetchDocuments?.();
      } else {
        const errMsg = result.errors?.join("; ") || `${result.failed} 个文件出错`;
        setScanStatusMsg(`上传完成：${result.uploaded} 个已索引，${result.skipped} 个跳过，${result.failed} 个失败 — ${errMsg}`);
      }
      setTimeout(() => setUploadMessage({ text: "", type: "" }), 4000);
    } catch (err: any) {
      setUploadMessage({ text: `上传失败: ${err.message}`, type: "error" });
    }
  };

  return (
    <div className="flex flex-col lg:flex-row h-full w-full gap-5 overflow-hidden select-text text-left">

      {/* LEFT PANEL */}
      <div className="w-full lg:w-[410px] shrink-0 flex flex-col bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm h-full select-none">

        {/* Tab buttons */}
        <div className="grid grid-cols-2 p-1 bg-zinc-100/50 border-b border-zinc-200">
          <button onClick={() => setActiveSubView("import")}
            className={`py-1.5 px-3 text-xs font-semibold rounded-md flex items-center justify-center gap-1.5 transition-all ${
              activeSubView === "import" ? "bg-white text-zinc-900 shadow-sm" : "text-zinc-500 hover:text-zinc-800"}`}>
            <FileUp className="w-3.5 h-3.5 text-zinc-400" /> 导入文档
          </button>
          <button onClick={() => setActiveSubView("compare")}
            className={`py-1.5 px-3 text-xs font-semibold rounded-md flex items-center justify-center gap-1.5 transition-all ${
              activeSubView === "compare" ? "bg-white text-zinc-900 shadow-sm" : "text-zinc-500 hover:text-zinc-800"}`}>
            <ArrowLeftRight className="w-3.5 h-3.5 text-zinc-400" /> 交叉对比
          </button>
        </div>

        {/* ===== IMPORT TAB — LEFT ===== */}
        {activeSubView === "import" && (
          <div className="flex-1 flex flex-col min-h-0">
            {/* 统一导入区域 */}
            <div className="p-3 border-b border-zinc-200 bg-zinc-50/30 shrink-0 space-y-3">
              {isUploading ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin text-emerald-500" />
                    <span className="text-[11px] text-zinc-600">{scanStatusMsg || "上传中..."}</span>
                  </div>
                  {uploadProgress && (
                    <div className="w-full bg-zinc-200 rounded-full h-1.5">
                      <div className="bg-emerald-500 h-1.5 rounded-full transition-all" style={{ width: `${(uploadProgress.done / uploadProgress.total) * 100}%` }} />
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  {/* 文件夹选择 */}
                  <label htmlFor="folder-picker"
                    className="w-full py-2 bg-white border border-zinc-200 hover:bg-zinc-50 hover:border-zinc-300 text-zinc-700 rounded-lg text-xs font-semibold block text-center cursor-pointer transition-all flex items-center justify-center gap-1.5">
                    <FolderOpen className="w-4 h-4 text-zinc-500" />
                    选择文件夹
                  </label>
                  <input type="file" id="folder-picker" {...({webkitdirectory: "true", directory: "true"} as any)} multiple onChange={handleFolderSelect} className="hidden" />

                  {/* 文件选择 */}
                  <label htmlFor="file-picker"
                    className="w-full py-2 bg-white border border-zinc-200 hover:bg-zinc-50 hover:border-zinc-300 text-zinc-700 rounded-lg text-xs font-semibold block text-center cursor-pointer transition-all flex items-center justify-center gap-1.5">
                    <FileText className="w-4 h-4 text-zinc-500" />
                    选择文件
                  </label>
                  <input type="file" id="file-picker" multiple accept=".pdf,.epub,.mobi,.docx,.xlsx,.xls,.html,.htm,.csv,.txt,.md,.json" onChange={handleFolderSelect} className="hidden" />

                  {/* 拖拽区域 */}
                  <div onDragEnter={handleDrag} onDragOver={handleDrag} onDragLeave={handleDrag} onDrop={handleDrop}
                    className={`border border-dashed rounded-lg text-center transition-all text-[10px] py-3 ${
                      dragActive ? "border-emerald-500 bg-emerald-50 text-emerald-700" : "border-zinc-200 text-zinc-400 hover:border-zinc-300 hover:bg-zinc-50"}`}>
                    {dragActive ? "松开即可导入" : "或拖拽文件到此处"}
                  </div>
                </div>
              )}

              {/* 状态消息 */}
              {scanStatusMsg && !isUploading && (
                <p className={`text-[10px] ${scanStatusMsg.includes("失败") ? "text-rose-500" : "text-emerald-600"}`}>{scanStatusMsg}</p>
              )}
              {uploadMessage.text && (
                <p className={`text-[10px] ${uploadMessage.type === "error" ? "text-rose-500" : "text-emerald-600"}`}>{uploadMessage.text}</p>
              )}
            </div>

            {/* 已有文档列表 */}
            <div className="flex-1 overflow-y-auto p-3">
              <span className="text-[9px] font-bold uppercase text-zinc-400 font-mono tracking-wider block mb-2">已有文档 ({documents.length})</span>
              {documents.length === 0 ? (
                <div className="text-center py-8">
                  <FolderOpen className="w-8 h-8 text-zinc-300 mx-auto mb-2" />
                  <p className="text-[11px] text-zinc-400">暂无文档</p>
                  <p className="text-[9px] text-zinc-300 mt-1">选择文件夹或拖拽文件开始导入</p>
                </div>
              ) : (
                <div className="space-y-0.5">
                  {documents.slice(0, 30).map(doc => (
                    <div key={doc.id} className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-zinc-50 transition-colors">
                      <FileText className="w-3 h-3 text-zinc-400 shrink-0" />
                      <span className="text-[11px] text-zinc-600 truncate flex-1">{doc.title}</span>
                    </div>
                  ))}
                  {documents.length > 30 && (
                    <p className="text-[9px] text-zinc-400 text-center pt-2">还有 {documents.length - 30} 个文档...</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ===== COMPARE TAB — LEFT ===== */}
        {activeSubView === "compare" && (
          <div className="flex-1 flex flex-col min-h-0">
            <div className="p-3 border-b border-zinc-150 flex items-center justify-between bg-zinc-50/20 shrink-0">
              <span className="text-[9px] uppercase font-bold text-zinc-450 font-mono tracking-wider">比对主体库 (最多 5 篇)</span>
              <select value={activeFolderId} onChange={(e) => setActiveFolderId(e.target.value)}
                className="text-[11px] bg-white border border-zinc-250 py-0.5 px-1.5 rounded text-zinc-700 font-medium focus:outline-none focus:border-zinc-500">
                <option value="all">全部文档 ({documents.length})</option>
                {libraries.map(lib => (<option key={lib.id} value={lib.id}>{lib.name}</option>))}
              </select>
            </div>
            <div className="flex justify-between items-center px-4 py-1.5 bg-zinc-50 border-b border-zinc-200 shrink-0 text-[10px] text-zinc-450 select-none">
              <button onClick={handleSelectAllInFolder} className="font-semibold text-zinc-600 hover:text-zinc-900" disabled={currentFolderDocs.length === 0}>
                {currentFolderDocs.every(d => selectedDocIds.includes(d.id)) ? "全部取消" : "全选当前目录"}
              </button>
              <span>已勾选 <strong>{selectedDocIds.length}</strong> / 5</span>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-2">
              {currentFolderDocs.length === 0 ? (
                <p className="text-xs text-zinc-400 italic text-center py-10 select-none">该目录暂无知识文档</p>
              ) : (
                currentFolderDocs.map(doc => {
                  const checked = selectedDocIds.includes(doc.id);
                  const docLib = libraries.find(l => l.id === doc.libraryId);
                  return (
                    <div key={doc.id} onClick={() => handleToggleDoc(doc.id)}
                      className={`p-2.5 border rounded-lg flex items-start gap-2 cursor-pointer transition-all ${
                        checked ? "bg-zinc-900 border-zinc-900 text-white shadow-sm" : "bg-white border-zinc-200 hover:border-zinc-300 text-zinc-700 hover:bg-zinc-50/50"}`}>
                      <span className="mt-0.5 shrink-0">{checked ? <CheckSquare className="w-3.5 h-3.5 text-zinc-100" /> : <Square className="w-3.5 h-3.5 text-zinc-300" />}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex justify-between items-start gap-2">
                          <h4 className={`text-xs font-medium truncate leading-relaxed ${checked ? "text-white" : "text-zinc-800"}`}>{doc.title}</h4>
                          <button type="button" onClick={(e) => { e.stopPropagation(); handleOpenEmbeddedReader(doc.title, doc.content); }}
                            className={`p-0.5 rounded transition-colors ${checked ? "text-zinc-400 hover:bg-zinc-800" : "text-zinc-400 hover:bg-zinc-100"}`} title="查看正文"><Eye className="w-3 h-3" /></button>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5 select-none font-mono text-[9px]">
                          <span className={`px-1 rounded-sm ${checked ? "bg-white/10 text-white" : "bg-zinc-100 text-zinc-500"}`}>{docLib?.name || "未知"}</span>
                          {/* 字符数：正文懒加载未打开时 content 为空，优先用后端元数据 charCount（修复 0 字符误显示） */}
                          <span className={checked ? "text-zinc-400" : "text-zinc-400"}>{doc.content.length || doc.charCount || 0} 字符</span>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

      </div>

      {/* RIGHT PANEL */}
      <div className="flex-1 flex flex-col min-w-0 bg-white border border-zinc-200 rounded-xl overflow-hidden shadow-sm h-full relative">

        {/* ===== IMPORT TAB — RIGHT ===== */}
        {activeSubView === "import" && (
          <div className="flex-1 flex flex-col items-center justify-center text-center p-8 space-y-4 select-none">
            <div className="p-4 bg-zinc-50 border border-zinc-200 rounded-full">
              <FolderOpen className="w-8 h-8 text-emerald-500" />
            </div>
            <div className="max-w-md space-y-2">
              <h3 className="text-sm font-bold text-zinc-800">导入文档到知识库</h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                选择<strong>本地文件夹</strong>批量导入，或<strong>拖拽/选择文件</strong>单独导入。
              </p>
            </div>
            <div className="text-[10px] text-zinc-400 space-y-1 text-left">
              <p>• 支持 PDF / EPUB / MOBI / DOCX / XLSX / HTML / CSV / TXT / MD / JSON</p>
              <p>• 文件夹导入自动按子目录分类</p>
              <p>• 文件上传后永久保存在服务器</p>
            </div>
          </div>
        )}

        {/* ===== COMPARE TAB — RIGHT ===== */}
        {activeSubView === "compare" && (
          <>
            {/* Status message */}
            {scanStatusMsg && (
              <div className="px-5 py-2 bg-zinc-50 border-b border-zinc-200 text-zinc-600 text-xs font-semibold flex items-center gap-1.5 select-none shrink-0 animate-fadeIn border-dashed">
                <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 shrink-0" /><span className="font-mono">{scanStatusMsg}</span>
              </div>
            )}

            {/* Document viewer overlay */}
            {readingDocTitle ? (
              <div className="absolute inset-0 z-20 flex flex-col bg-white overflow-hidden animate-fadeIn text-left select-text">
                <div className="px-5 py-3.5 border-b border-zinc-200 bg-zinc-50 flex wrap justify-between items-center gap-3 select-none">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="p-1.5 bg-zinc-900 text-emerald-400 rounded-lg shrink-0"><BookOpen className="w-3.5 h-3.5" /></span>
                    <div className="min-w-0">
                      <h3 className="text-zinc-905 font-black text-xs truncate">查看: 《{readingDocTitle}》</h3>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center rounded-lg border border-zinc-200 p-0.5 bg-white text-[10px] font-mono font-bold text-zinc-500">
                      <button onClick={() => setReadingFontSize(p => Math.max(10, p - 1))} type="button" className="px-2 py-1 hover:bg-zinc-150 rounded">A-</button>
                      <span className="px-2 border-x border-zinc-100 font-extrabold text-zinc-800">{readingFontSize}px</span>
                      <button onClick={() => setReadingFontSize(p => Math.min(22, p + 1))} type="button" className="px-2 py-1 hover:bg-zinc-150 rounded">A+</button>
                    </div>
                    <button onClick={() => setReadingDocTitle("")} className="px-3 py-1.5 bg-rose-500 hover:bg-rose-600 text-white font-extrabold text-xs rounded-lg">关闭</button>
                  </div>
                </div>
                <div className="px-5 py-2 border-b border-zinc-150 bg-zinc-100/20 flex items-center gap-4 select-none">
                  <div className="flex items-center gap-2 flex-1 max-w-sm">
                    <span className="text-[10px] font-black uppercase text-zinc-400 font-mono shrink-0">查找:</span>
                    <input type="text" placeholder="输入关键词高亮匹配..."
                      value={readingHighlightQuery} onChange={(e) => setReadingHighlightQuery(e.target.value)}
                      className="w-full text-[11px] border border-zinc-200 bg-white px-2 py-0.5 rounded focus:outline-none focus:border-emerald-500 font-semibold" />
                  </div>
                  <div className="text-[10px] font-mono text-zinc-400">字数: <strong className="text-zinc-650">{readingDocContent.length}</strong></div>
                </div>
                <div className="flex-1 overflow-y-auto p-6 md:p-8 bg-zinc-50/20">
                  <div className="bg-white border border-zinc-150 p-6 md:p-8 max-w-3xl mx-auto rounded-2xl shadow-sm text-zinc-850">
                    <h1 className="text-base md:text-lg font-black text-zinc-950 border-b border-zinc-150 pb-3 mb-5">{readingDocTitle}</h1>
                    {readingDocContent.trim().startsWith("{") || readingDocContent.trim().startsWith("[") ? (
                      <pre className="font-mono text-[10.5px] bg-zinc-50 p-4 border rounded-xl overflow-x-auto">{readingDocContent}</pre>
                    ) : (
                      <div className="leading-relaxed" style={{ fontSize: `${readingFontSize}px` }}>
                        {renderHighlightedContent(readingDocContent, readingHighlightQuery)}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : null}

            {/* Comparison results or empty state */}
            {selectedDocIds.length < 2 ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-8 space-y-5 select-none h-full bg-zinc-50/20">
                <div className="p-4 bg-white border border-zinc-150 rounded-full text-zinc-400"><ArrowLeftRight className="w-8 h-8 text-zinc-400 animate-pulse" /></div>
                <div className="max-w-md space-y-2">
                  <h3 className="text-sm font-bold text-zinc-800">多文档横向比对</h3>
                  <p className="text-xs text-zinc-500 leading-relaxed">在左侧勾选 <strong>2 篇或更多</strong> 文档，然后点击"开始对比"。</p>
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col min-h-0 overflow-hidden bg-white">
                <div className="px-4 py-2 border-b border-zinc-200 bg-zinc-50/30 shrink-0 select-none flex items-center gap-3 flex-wrap">
                  <div className="flex items-center gap-1.5 flex-1 min-w-0 flex-wrap">
                    {documents.filter(doc => selectedDocIds.includes(doc.id)).map((doc, idx) => (
                      <span key={doc.id} className="inline-flex items-center gap-1 px-2 py-0.5 bg-white border border-zinc-200 rounded text-[10px] font-medium text-zinc-700 truncate max-w-[180px]">
                        <span className="text-[9px] font-mono text-zinc-400">#{idx + 1}</span> {doc.title}
                      </span>
                    ))}
                  </div>
                  <button onClick={handleTriggerCompare} disabled={isComparing}
                    className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:bg-zinc-200 text-white font-bold text-[10px] rounded-lg flex items-center gap-1.5 shrink-0 transition-all">
                    {isComparing ? <><RefreshCw className="w-3 h-3 animate-spin" /> 对比中...</> : <><ArrowLeftRight className="w-3 h-3" /> 开始对比</>}
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto p-5 relative min-h-0 bg-zinc-50/15">
                  {isComparing ? (
                    <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/85 select-none animate-fadeIn space-y-4">
                      <div className="w-9 h-9 border-4 border-emerald-100 border-t-emerald-500 rounded-full animate-spin" />
                      <div className="text-center space-y-1"><h5 className="text-xs font-bold text-zinc-800">分析中...</h5></div>
                    </div>
                  ) : comparisonResult ? (
                    <div className="bg-white border border-zinc-200 rounded-xl max-w-4xl mx-auto text-left overflow-hidden">
                      <div className="flex justify-between items-center px-5 py-2 bg-zinc-50 border-b border-zinc-200 select-none">
                        <span className="text-[10px] font-bold text-zinc-600">对比分析报告</span>
                        <div className="flex items-center gap-1">
                          <button onClick={() => { navigator.clipboard.writeText(comparisonResult); setCopySuccess(true); setTimeout(() => setCopySuccess(false), 2000); }}
                            className="px-2 py-1 text-[10px] font-medium text-zinc-500 hover:text-zinc-700 hover:bg-zinc-100 rounded">复制</button>
                          <button onClick={() => { const blob = new Blob([comparisonResult], { type: "text/markdown;charset=utf-8" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `Compare-Report-${new Date().toISOString().split("T")[0]}.md`; link.click(); URL.revokeObjectURL(url); }}
                            className="px-2.5 py-1.5 bg-zinc-900 hover:bg-zinc-950 text-white text-xs font-bold rounded-lg flex items-center gap-1">保存</button>
                        </div>
                      </div>
                      <div className="p-6 text-zinc-800 space-y-1">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                          h2: ({children}) => <h2 className="text-sm font-bold text-emerald-800 mt-6 mb-3 pl-3 border-l-2 border-emerald-500 py-1 bg-emerald-50/50 rounded-r">{children}</h2>,
                          h3: ({children}) => <h3 className="text-xs font-bold text-zinc-800 mt-5 mb-2 pb-1 border-b border-zinc-200">{children}</h3>,
                          p: ({children}) => <p className="text-[12px] leading-7 text-zinc-700 my-2">{children}</p>,
                          li: ({children}) => <li className="text-[12px] leading-6 text-zinc-700 ml-4 list-disc my-0.5">{children}</li>,
                          table: ({children}) => <div className="overflow-x-auto my-4 rounded-lg border border-zinc-200"><table className="min-w-full text-[11px] border-collapse">{children}</table></div>,
                          thead: ({children}) => <thead className="bg-zinc-100">{children}</thead>,
                          tbody: ({children}) => <tbody>{children}</tbody>,
                          th: ({children}) => <th className="border border-zinc-200 bg-zinc-100 px-3 py-2 text-left font-bold text-zinc-700 text-[10px] uppercase tracking-wider">{children}</th>,
                          td: ({children}) => <td className="border border-zinc-200 px-3 py-2 text-zinc-600 even:bg-zinc-50/50">{children}</td>,
                          tr: ({children}) => <tr className="border-b border-zinc-100 last:border-0">{children}</tr>,
                          strong: ({children}) => <strong className="font-bold text-emerald-700 bg-emerald-50/50 px-0.5 rounded">{children}</strong>,
                          em: ({children}) => <em className="text-zinc-500 not-italic">{children}</em>,
                          code: ({children}) => <code className="bg-zinc-100 px-1.5 py-0.5 rounded text-[10px] font-mono text-emerald-700">{children}</code>,
                          blockquote: ({children}) => <blockquote className="border-l-2 border-amber-300 bg-amber-50/30 pl-3 py-1.5 my-3 text-[11px] text-zinc-600 rounded-r">{children}</blockquote>,
                          hr: () => <hr className="my-4 border-zinc-150" />,
                        }}>{comparisonResult}</ReactMarkdown>
                      </div>
                    </div>
                  ) : (
                    <div className="h-full flex flex-col items-center justify-center text-center p-8 select-none">
                      <AlertCircle className="w-6 h-6 text-zinc-300 animate-pulse mb-1.5" />
                      <p className="text-xs text-zinc-400">选择文档后点击"开始对比"</p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}

      </div>

    </div>
  );
}
