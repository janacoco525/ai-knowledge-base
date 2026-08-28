/**
 * 高性能客户端多格式文档解析引擎
 * 支持 PDF、DOCX Word 档案、EPUB/MOBI 电子书、XLSX/XLS 电子表格、HTML 文章、CSV 以及各类原生文本/代码
 */
import JSZip from "jszip";

declare global {
  interface Window {
    pdfjsLib: any;
    mammoth: any;
    XLSX: any;
  }
}

// 异步载入 CDN 库以确保稳定性并提供优雅回显
export function ensureLibraryLoaded(libName: "pdf" | "mammoth" | "xlsx"): Promise<void> {
  return new Promise((resolve, reject) => {
    if (libName === "pdf" && window.pdfjsLib) return resolve();
    if (libName === "mammoth" && window.mammoth) return resolve();
    if (libName === "xlsx" && window.XLSX) return resolve();

    let src = "";
    if (libName === "pdf") {
      src = "/lib/pdf.min.js";
    } else if (libName === "mammoth") {
      src = "https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.6.0/mammoth.browser.min.js";
    } else if (libName === "xlsx") {
      src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
    }

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = src;
    script.async = true;
    script.onload = () => {
      if (libName === "pdf" && window.pdfjsLib) {
        // pdf.js 主库文件本身包含完整 worker 代码，指向同一文件即可
        // 无需单独的 pdf.worker.min.js
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/lib/pdf.min.js";
      }
      resolve();
    };
    script.onerror = () => {
      reject(new Error(`加载辅助解析组件库 ${libName} 失败，请检查网络连接`));
    };
    document.head.appendChild(script);
  });
}

/**
 * 解析 PDF 转换为排版良好的文本
 */
export async function parsePdf(file: File): Promise<string> {
  await ensureLibraryLoaded("pdf");
  const arrayBuffer = await file.arrayBuffer();
  const pdfjsLib = window.pdfjsLib;
  if (!pdfjsLib) {
    throw new Error("PDF 核心解析引擎尚未就绪");
  }

  const loadingTask = pdfjsLib.getDocument({ data: new Uint8Array(arrayBuffer) });
  const pdf = await loadingTask.promise;
  const parts: string[] = [];
  const totalPages = pdf.numPages;

  for (let i = 1; i <= totalPages; i++) {
    const page = await pdf.getPage(i);
    const textContent = await page.getTextContent();
    const pageText = textContent.items.map((item: any) => item.str).join(" ");
    parts.push(`--- 第 ${i} 页 / 共 ${totalPages} 页 ---\n${pageText}\n`);
  }

  return parts.join("\n\n");
}

/**
 * 将 PDF 页面渲染为图片 data URI（按需调用，不存入文档内容）
 */
export async function renderPdfPage(file: File, pageNum: number, scale: number = 2.0): Promise<string> {
  await ensureLibraryLoaded("pdf");
  const arrayBuffer = await file.arrayBuffer();
  const pdfjsLib = window.pdfjsLib;
  if (!pdfjsLib) throw new Error("PDF 引擎未就绪");

  const loadingTask = pdfjsLib.getDocument({ data: new Uint8Array(arrayBuffer) });
  const pdf = await loadingTask.promise;
  const page = await pdf.getPage(pageNum);
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement("canvas");
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 不可用");
  await page.render({ canvasContext: ctx, viewport }).promise;
  return canvas.toDataURL("image/jpeg", 0.85);
}

/**
 * 解析 Word DOCX 为原生纯文本
 */
export async function parseDocx(file: File): Promise<string> {
  await ensureLibraryLoaded("mammoth");
  const arrayBuffer = await file.arrayBuffer();
  const mammoth = window.mammoth;
  if (!mammoth) {
    throw new Error("Word 核心解析引擎 (Mammoth.js) 尚未就绪");
  }

  const result = await mammoth.extractRawText({ arrayBuffer });
  return result.value || "";
}

/**
 * 解析 Excel XLSX/XLS 为表格段落
 */
export async function parseXlsx(file: File): Promise<string> {
  await ensureLibraryLoaded("xlsx");
  const arrayBuffer = await file.arrayBuffer();
  const XLSX = window.XLSX;
  if (!XLSX) {
    throw new Error("Excel 核心解析引擎 (SheetJS) 尚未就绪");
  }

  const workbook = XLSX.read(new Uint8Array(arrayBuffer), { type: "array" });
  let fullText = "";

  workbook.SheetNames.forEach((sheetName: string) => {
    const sheet = workbook.Sheets[sheetName];
    const csv = XLSX.utils.sheet_to_csv(sheet);
    if (csv && csv.trim()) {
      fullText += `### 电子数据表: ${sheetName}\n${csv}\n\n`;
    }
  });

  return fullText.trim();
}

/**
 * 解析 HTML 文档，剥离不必要的网页样式与脚本代码
 */
export async function parseHtml(file: File): Promise<string> {
  const htmlText = await file.text();
  const parser = new DOMParser();
  const doc = parser.parseFromString(htmlText, "text/html");
  
  // 过滤脚本、CSS 样式、头部元信息等噪声
  doc.querySelectorAll("script, style, head, iframe, svg, meta, link, noscript").forEach(el => el.remove());
  
  return doc.body.innerText || doc.documentElement.textContent || "";
}

/**
 * EPUB 辅助：将 HTML 内容转换为排版优雅的纯文本，保留章节标题结构
 */
function parseEpubHtml(htmlText: string, imageMap?: Map<string, string>): string {
  // 先用 DOMParser 解析，XHTML 回退 text/xml
  const parser = new DOMParser();
  let doc = parser.parseFromString(htmlText, "text/html");
  if (!doc.body || doc.body.innerHTML.trim() === "" || doc.querySelector("parsererror")) {
    doc = parser.parseFromString(htmlText, "application/xhtml+xml");
    if (!doc.body && !doc.documentElement) {
      doc = parser.parseFromString(htmlText, "text/xml");
    }
  }

  // 过滤无用标签，但保留 img
  doc.querySelectorAll("script, style, head, iframe, svg, meta, link, noscript").forEach((el: any) => el.remove());

  // 图片转 Markdown：匹配 EPUB 中的相对路径
  if (imageMap && imageMap.size > 0) {
    doc.querySelectorAll("img").forEach((img: any) => {
      let src = img.getAttribute("src") || "";
      // 尝试多种路径匹配
      const fileName = src.split("/").pop() || src;
      let dataUri = imageMap.get(src) || imageMap.get(fileName);
      if (!dataUri) {
        // 模糊匹配：找包含文件名的 key
        for (const [k, v] of imageMap) {
          if (k.endsWith(fileName) || k.endsWith(src)) { dataUri = v; break; }
        }
      }
      if (dataUri) {
        const alt = img.getAttribute("alt") || "图片";
        img.outerHTML = `\n\n![${alt}](${dataUri})\n\n`;
      }
    });
  }

  // 标题转 Markdown
  doc.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((el: any) => {
    const text = el.textContent?.trim();
    if (text) el.textContent = `\n\n## ${text}\n\n`;
  });

  // 段落后加换行
  doc.querySelectorAll("p, div").forEach((el: any) => {
    const text = el.textContent?.trim();
    if (text) el.textContent = `${text}\n`;
  });

  let result = (doc.body?.innerText || doc.documentElement?.textContent || "");

  // 兜底：如果结果仍然含大量 HTML 标签，用正则暴力清洗
  if (result && /<[a-zA-Z][^>]*>/.test(result)) {
    result = result
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/?p[^>]*>/gi, "\n")
      .replace(/<\/?div[^>]*>/gi, "\n")
      .replace(/<\/?h[1-6][^>]*>/gi, "\n\n")
      .replace(/<\/?li[^>]*>/gi, "\n• ")
      .replace(/<[^>]+>/g, "")
      .replace(/&nbsp;/g, " ")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&")
      .replace(/&quot;/g, '"')
      .replace(/\n{3,}/g, "\n\n")
      .replace(/^\s*\n/gm, "")
      .trim();
  }

  return result.replace(/\n{3,}/g, "\n\n").trim();
}

/**
 * EPUB 辅助：解析相对路径（处理 .. 和 URL 编码）
 */
function resolveEpubPath(parentDir: string, href: string): string {
  if (!parentDir) return href;
  const decoded = decodeURIComponent(href);
  const parts = `${parentDir}/${decoded}`.split("/");
  const normalized: string[] = [];
  for (const part of parts) {
    if (part === ".." && normalized.length > 0) {
      normalized.pop();
    } else if (part !== "." && part !== "") {
      normalized.push(part);
    }
  }
  return normalized.join("/");
}

/**
 * 解析 EPUB/MOBI 电子书，按 Spine 顺序提取章节并保留结构
 */
export async function parseEpub(file: File): Promise<string> {
  const zip = await JSZip.loadAsync(file);

  // 提取所有图片为 base64 data URI，建立路径→data映射
  const imageMap = new Map<string, string>();
  const imgExts = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"];
  const imgFiles = Object.keys(zip.files).filter(k => imgExts.some(ext => k.toLowerCase().endsWith(ext)));
  await Promise.all(imgFiles.map(async (imgPath) => {
    try {
      const entry = zip.file(imgPath);
      if (!entry) return;
      const data = await entry.async("uint8array");
      const ext = (imgPath.split(".").pop() || "jpg").toLowerCase().replace("jpeg", "jpg");
      const mime = ext === "svg" ? "image/svg+xml" : `image/${ext}`;
      const base64 = btoa(String.fromCharCode(...data));
      imageMap.set(imgPath, `data:${mime};base64,${base64}`);
      // 也存短文件名
      const short = imgPath.split("/").pop() || imgPath;
      if (short !== imgPath) imageMap.set(short, `data:${mime};base64,${base64}`);
    } catch { /* skip broken images */ }
  }));
  
  // Step 1: container.xml → OPF 路径
  const containerFile = zip.file("META-INF/container.xml");
  if (!containerFile) throw new Error("EPUB: 未找到 META-INF/container.xml");
  const containerXml = await containerFile.async("text");
  const containerDoc = new DOMParser().parseFromString(containerXml, "text/xml");
  const rootfile = containerDoc.querySelector("rootfile");
  if (!rootfile) throw new Error("EPUB: container.xml 中未找到 rootfile");
  const opfPath = rootfile.getAttribute("full-path") || "";
  if (!opfPath) throw new Error("EPUB: 无法确定 OPF 路径");
  
  // Step 2: 解析 OPF → manifest + spine
  let opfFile = zip.file(opfPath);
  if (!opfFile) {
    const opfName = opfPath.split("/").pop() || opfPath;
    opfFile = zip.file(opfName) || Object.values(zip.files).find(f => f.name.endsWith(opfName)) as any;
  }
  if (!opfFile) {
    // 回退：直接提取所有 HTML 章节
    return fallbackEpubExtract(zip, imageMap);
  }

  const opfXml = await opfFile.async("text");
  const opfDoc = new DOMParser().parseFromString(opfXml, "text/xml");

  // manifest id→href
  const manifestMap = new Map<string, string>();
  const items = opfDoc.querySelectorAll("manifest > item, manifest item, item");
  (items.length > 0 ? Array.from(items) : Array.from(opfDoc.getElementsByTagName("item")))
    .forEach(item => {
      const id = item.getAttribute("id");
      const href = item.getAttribute("href");
      if (id && href) manifestMap.set(id, href);
    });

  // spine 顺序
  const spineRefs = opfDoc.querySelectorAll("spine > itemref, spine itemref, itemref");
  const itemrefs = spineRefs.length > 0 ? Array.from(spineRefs) : Array.from(opfDoc.getElementsByTagName("itemref"));

  // Step 3: 按 spine 顺序提取各章节
  const parentDir = opfPath.includes("/") ? opfPath.substring(0, opfPath.lastIndexOf("/")) : "";
  const chapterTexts: string[] = [];

  for (const ref of itemrefs) {
    const idref = ref.getAttribute("idref");
    if (!idref) continue;
    const href = manifestMap.get(idref);
    if (!href) continue;

    const resolvedPath = resolveEpubPath(parentDir, href);
    const entry = zip.file(resolvedPath)
      || (() => {
          const keys = Object.keys(zip.files);
          const match = keys.find(k => k.toLowerCase() === resolvedPath.toLowerCase() || k.endsWith(`/${href}`));
          return match ? zip.file(match) : null;
        })();

    if (!entry) continue;
    try {
      const html = await entry.async("text");
      const text = parseEpubHtml(html, imageMap);
      if (text) chapterTexts.push(text);
    } catch { /* skip broken chapters */ }
  }

  if (chapterTexts.length > 0) {
    return chapterTexts.join("\n\n---\n\n");
  }

  return fallbackEpubExtract(zip, imageMap);
}

/** 回退：提取所有 XHTML/HTML 文件 */
async function fallbackEpubExtract(zip: any, imageMap?: Map<string, string>): Promise<string> {
  const htmlFiles = Object.keys(zip.files)
    .filter(k => /\.(xhtml|html|htm)$/i.test(k) && !k.includes("nav") && !k.includes("toc"))
    .sort()
    .slice(0, 60);

  const texts = (await Promise.all(htmlFiles.map(async (name) => {
    const entry = zip.file(name);
    if (!entry) return "";
    try {
      const html = await entry.async("text");
      return parseEpubHtml(html, imageMap);
    } catch { return ""; }
  }))).filter(Boolean);

  return texts.join("\n\n---\n\n") || "（无法解析 EPUB 内容）";
}

/**
 * 读取文本文件，自动检测编码（UTF-8 / GBK / GB2312 / Big5）
 * 防止中文非 UTF-8 文件导入后乱码
 */
async function readTextFileWithEncoding(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const uint8 = new Uint8Array(buffer);

  // 尝试 UTF-8 解码
  const utf8Text = new TextDecoder("utf-8", { fatal: true });
  try {
    return utf8Text.decode(uint8);
  } catch {
    // UTF-8 解码失败（非 UTF-8 编码），尝试其他编码
  }

  // 按优先级尝试常见中文编码
  const encodings = ["gbk", "gb2312", "big5", "shift_jis", "euc-kr", "windows-1252"];
  for (const enc of encodings) {
    try {
      const decoder = new TextDecoder(enc);
      const text = decoder.decode(uint8);
      // 简单启发式：如果包含常见中文字符，大概率是正确的
      if (/[一-鿿]/.test(text) || text.length > 10) {
        return text;
      }
    } catch {
      // 该编码不可用，继续尝试下一个
    }
  }

  // 全部失败，用 UTF-8 宽松模式兜底
  const fallback = new TextDecoder("utf-8");
  return fallback.decode(uint8);
}

/**
 * 统一分发的多格式解析调度中心
 */
export async function parseFileToText(file: File): Promise<string> {
  const name = file.name.toLowerCase();

  if (name.endsWith(".pdf")) {
    return await parsePdf(file);
  } else if (name.endsWith(".docx")) {
    return await parseDocx(file);
  } else if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
    return await parseXlsx(file);
  } else if (name.endsWith(".html") || name.endsWith(".htm")) {
    return await parseHtml(file);
  } else if (name.endsWith(".epub") || name.endsWith(".mobi")) {
    return await parseEpub(file);
  } else {
    // 纯文本/Markdown：使用编码检测读取
    return await readTextFileWithEncoding(file);
  }
}
