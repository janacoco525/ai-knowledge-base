import React, { useState, useEffect, useRef, useCallback } from "react";
import { getOriginalFile } from "../lib/fileStorage";
import { Loader2, X, ZoomIn, ZoomOut } from "lucide-react";

interface PdfViewerProps {
  docTitle: string;
  docId?: string;      // 文档ID，用于从后端获取原始文件
  onClose: () => void;
}

type RenderMode = "native" | "canvas" | "loading";

export default function PdfViewer({ docTitle, docId, onClose }: PdfViewerProps) {
  const [mode, setMode] = useState<RenderMode>("loading");
  const [blobUrl, setBlobUrl] = useState("");
  const [error, setError] = useState("");

  // Canvas 模式状态
  const [pageNum, setPageNum] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [scale, setScale] = useState(1.5);
  const [canvasLoading, setCanvasLoading] = useState(false);
  const pdfDocRef = useRef<any>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const pdfFileRef = useRef<File | null>(null);
  const appliedScaleRef = useRef(0);
  // 渲染互斥：pdf.js 3.4.120 同一 canvas 并发 render 会抛 WeakSet 冲突，
  // 异常路径导致画面翻转（镜像）。用 busy + pending 合并突发请求，串行执行（2026-08-06 修复）
  const renderBusyRef = useRef(false);
  const renderPendingRef = useRef<{ num: number; s: number } | null>(null);

  // 加载文件 + 检测浏览器 PDF 支持
  useEffect(() => {
    let cancelled = false;
    const supportsPdf = navigator.pdfViewerEnabled ?? true;

    async function load() {
      try {
        if (docId) {
          pdfFileRef.current = null;
          if (!cancelled) {
            setBlobUrl(`/api/kb/files/${encodeURIComponent(docId)}/raw`);
            setMode("canvas");
          }
          return;
        }

        const file = await getOriginalFile(docTitle);
        if (!file) {
          if (!cancelled) { setError("未找到原始 PDF 文件，请重新导入该文档"); setMode("canvas"); }
          return;
        }
        pdfFileRef.current = file;
        const url = URL.createObjectURL(file);
        if (!cancelled) {
          setBlobUrl(url);
          setMode(supportsPdf ? "native" : "canvas");
        }
      } catch (e: any) {
        if (!cancelled) { setError("加载失败: " + (e.message || "")); setMode("canvas"); }
      }
    }
    load();
    return () => { cancelled = true; };
  }, [docTitle]);

  // 清理 blob URL
  useEffect(() => {
    return () => { if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [blobUrl]);

  const onIframeError = useCallback(() => {
    setMode("canvas");
  }, []);

  // 内部渲染：pdf.js 渲染一页到 canvas（含 DPR 高清适配）
  const doRenderPage = useCallback(async (doc: any, num: number, s: number) => {
    const page = await doc.getPage(num);
    const viewport = page.getViewport({ scale: s, rotation: page.rotate || 0 });
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    // 高 DPI 适配：canvas 位图按 devicePixelRatio 放大，避免模糊；CSS 尺寸保持逻辑尺寸
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = Math.floor(viewport.width) + "px";
    canvas.style.height = Math.floor(viewport.height) + "px";
    ctx.save();
    ctx.scale(dpr, dpr);
    try {
      await page.render({ canvasContext: ctx, viewport }).promise;
    } finally {
      ctx.restore();
    }
    appliedScaleRef.current = s;
  }, []);

  // 核心渲染函数：互斥串行执行（pdf.js 3.4.120 同一 canvas 并发 render 会抛
  // "Cannot use the same canvas during multiple render()" 并导致画面翻转镜像）
  const renderPageAtScale = useCallback(async (doc: any, num: number, s: number) => {
    // 当前有渲染未完成：只保留最新请求，待完成后执行（合并突发缩放/翻页）
    if (renderBusyRef.current) {
      renderPendingRef.current = { num, s };
      return;
    }
    renderBusyRef.current = true;
    try {
      for (;;) {
        const pending = renderPendingRef.current;
        renderPendingRef.current = null;
        if (pending) { num = pending.num; s = pending.s; }
        await doRenderPage(doc, num, s);
        if (!renderPendingRef.current) break;
      }
    } finally {
      renderBusyRef.current = false;
    }
  }, [doRenderPage]);

  // Canvas 模式：加载 pdf.js
  useEffect(() => {
    if (mode !== "canvas") return;
    let cancelled = false;

    async function renderWithPdfJs() {
      setCanvasLoading(true);
      try {
        if (!(window as any).pdfjsLib) {
          await new Promise<void>((resolve, reject) => {
            const s = document.createElement("script");
            s.src = "/lib/pdf.min.js";
            s.onload = () => resolve();
            s.onerror = () => reject(new Error("PDF 引擎加载失败"));
            document.head.appendChild(s);
          });
        }
        const pdfjsLib = (window as any).pdfjsLib;
        if (!pdfjsLib) throw new Error("PDF 引擎未就绪");

        pdfjsLib.GlobalWorkerOptions.workerSrc = "/lib/pdf.worker.min.js";

        let doc;
        if (docId) {
          const rawUrl = `/api/kb/files/${encodeURIComponent(docId)}/raw`;
          doc = await pdfjsLib.getDocument({ url: rawUrl }).promise;
        } else if (pdfFileRef.current) {
          const arrayBuffer = await pdfFileRef.current!.arrayBuffer();
          doc = await pdfjsLib.getDocument({ data: new Uint8Array(arrayBuffer) }).promise;
        } else {
          throw new Error("无法加载 PDF 文件");
        }
        if (cancelled) return;
        pdfDocRef.current = doc;
        setTotalPages(doc.numPages);
        // 自动 fit 页面：同时考虑宽度和高度，让整页可见（2026-08-06 修复：此前只适配宽度导致高度超视口被截）
        // 2026-08-06 再修：首屏直接用算出的 fitScale 渲染，不再先以初始 scale=1.5 渲染再等重绘（大页面会闪"只显示一半"）
        let fitScale = scale;
        if (!scaleInitializedRef.current) {
          try {
            const page1 = await doc.getPage(1);
            const naturalViewport = page1.getViewport({ scale: 1 });
            // 容器宽高（视口 - 工具栏 - padding）
            const containerWidth = window.innerWidth - 80;
            const containerHeight = window.innerHeight - 100; // 工具栏+padding
            // 取宽高适配的较小 scale，保证整页可见
            fitScale = Math.min(
              1.5,
              Math.max(0.4, containerWidth / naturalViewport.width),
              Math.max(0.4, containerHeight / naturalViewport.height),
            );
            setScale(fitScale);
          } catch {
            // fit 计算失败（如异常页面）：回退到 0.8，避免保持 1.5 导致大页面只显示一角
            fitScale = 0.8;
          }
          scaleInitializedRef.current = true;
        }
        await renderPageAtScale(doc, 1, fitScale);
      } catch (e: any) {
        if (!cancelled) setError("PDF 渲染失败: " + (e.message || ""));
      } finally {
        if (!cancelled) setCanvasLoading(false);
      }
    }
    renderWithPdfJs();
    return () => { cancelled = true; };
  }, [mode]);

  const canvasGoPage = useCallback(async (delta: number) => {
    const next = pageNum + delta;
    if (next < 1 || next > totalPages) return;
    setPageNum(next);
    setCanvasLoading(true);
    if (pdfDocRef.current) {
      await renderPageAtScale(pdfDocRef.current, next, scale);
    }
    setCanvasLoading(false);
  }, [pageNum, totalPages, scale, renderPageAtScale]);

  // 缩放：只改变 scale 状态，渲染由下面的 useEffect 自动驱动
  const canvasZoom = useCallback((delta: number) => {
    setScale(prev => {
      const s = Math.max(0.5, Math.min(4, prev + delta));
      return s;
    });
  }, []);

  // 当 scale 变化时自动重绘 canvas（解耦触发与渲染）
  const scaleInitializedRef = useRef(false);
  useEffect(() => {
    if (!pdfDocRef.current || mode !== "canvas") return;
    if (!scaleInitializedRef.current) {
      scaleInitializedRef.current = true;
      return;
    }
    if (appliedScaleRef.current === scale) return;
    let cancelled = false;
    // 用 rAF 延迟到 React commit 之后，避免 React 重置 canvas 尺寸；
    // rAF 回调内再查一次 appliedScaleRef：首屏渲染已完成同 scale 时直接跳过，消除并发窗口
    const rafId = requestAnimationFrame(() => {
      if (cancelled) return;
      if (appliedScaleRef.current === scale) return;
      renderPageAtScale(pdfDocRef.current, pageNum, scale);
    });
    return () => { cancelled = true; cancelAnimationFrame(rafId); };
  }, [scale, pageNum, mode, renderPageAtScale]);

  // 快捷键
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (mode === "canvas") {
        if (e.key === "ArrowLeft") canvasGoPage(-1);
        if (e.key === "ArrowRight") canvasGoPage(1);
        if (e.key === "-" || e.key === "_") { e.preventDefault(); canvasZoom(-0.25); }
        if (e.key === "=" || e.key === "+") { e.preventDefault(); canvasZoom(0.25); }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, mode, canvasGoPage, canvasZoom]);

  return (
    <div className="fixed inset-0 z-50 bg-black/85 flex flex-col" onClick={onClose}>
      {/* 工具栏 */}
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-900 text-white shrink-0 select-none"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-3">
          <button onClick={onClose}
            className="flex items-center gap-1 text-zinc-400 hover:text-white text-sm font-bold transition-colors">
            <X className="w-4 h-4" /> 关闭 (Esc)
          </button>
          <span className="text-xs text-zinc-500">|</span>
          <span className="text-xs text-zinc-300 truncate max-w-[300px]">{docTitle}</span>
        </div>

        {mode === "canvas" && totalPages > 0 && (
          <div className="flex items-center gap-2">
            <button onClick={() => canvasZoom(-0.25)} className="w-7 h-7 flex items-center justify-center bg-zinc-800 hover:bg-zinc-600 active:bg-zinc-500 rounded transition-colors" title="缩小">
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <span className="text-xs text-zinc-300 font-mono min-w-[42px] text-center font-bold">{Math.round(scale * 100)}%</span>
            <button onClick={() => canvasZoom(0.25)} className="w-7 h-7 flex items-center justify-center bg-zinc-800 hover:bg-zinc-600 active:bg-zinc-500 rounded transition-colors" title="放大">
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
            <span className="text-zinc-600 mx-1">|</span>
            <button onClick={() => canvasGoPage(-1)} disabled={pageNum <= 1}
              className="px-1.5 py-0.5 text-xs bg-zinc-800 hover:bg-zinc-700 rounded disabled:opacity-30">◂</button>
            <span className="text-[10px] text-zinc-300 min-w-[55px] text-center">{pageNum}/{totalPages}</span>
            <button onClick={() => canvasGoPage(1)} disabled={pageNum >= totalPages}
              className="px-1.5 py-0.5 text-xs bg-zinc-800 hover:bg-zinc-700 rounded disabled:opacity-30">▸</button>
          </div>
        )}
      </div>

      {/* 内容区：允许滚动，canvas 直接使用实际分辨率 */}
      <div className="flex-1 overflow-auto"
        onClick={e => e.stopPropagation()}>
        {error && mode === "canvas" && !blobUrl ? (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-6 py-4 text-red-400 text-sm max-w-md text-center mx-auto mt-20">
            {error}
          </div>
        ) : mode === "loading" ? (
          <div className="flex items-center justify-center gap-3 text-white/80 h-full">
            <Loader2 className="w-6 h-6 animate-spin" />
            <span className="text-sm">加载中...</span>
          </div>
        ) : mode === "native" ? (
          <iframe src={blobUrl} className="w-full h-full border-0" title={docTitle}
            onError={onIframeError} />
        ) : mode === "canvas" ? (
          <div className="flex justify-center p-4 min-h-full">
            {canvasLoading && !pdfDocRef.current && (
              <div className="flex items-center gap-3 text-white/80 mt-20">
                <Loader2 className="w-6 h-6 animate-spin" />
                <span className="text-sm">PDF 引擎加载中...</span>
              </div>
            )}
            {canvasLoading && pdfDocRef.current && (
              <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-10 bg-black/70 rounded-lg px-3 py-1.5">
                <span className="text-white/80 text-xs">渲染中…</span>
              </div>
            )}
            <canvas ref={canvasRef} className="shadow-2xl" />
          </div>
        ) : null}
      </div>
    </div>
  );
}
