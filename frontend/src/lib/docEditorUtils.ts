// DocEditor 纯文本工具（2026-08-05 自 DocEditor.tsx 拆出）
// 纯函数，零组件依赖，可单测。不持有 React 状态。

// 从 MD heading children 数组安全提取原始文本
export const getInnerText = (node: any): string => {
  if (!node) return "";
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(getInnerText).join("");
  if (node.props && node.props.children) return getInnerText(node.props.children);
  return String(node);
};

// 生成 URL 安全、干净的 HTML ID（基于标题文本）
export const generateHeadingId = (text: string) => {
  return "heading-" + encodeURIComponent(
    text.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^一-龥\w-]/g, "")
  );
};

// 判断一行是否为结构标题（如 Chapter 1, Section 1, 第一章, 一、, 1.1 等）
export const isStructuralHeading = (line: string): { isHeading: boolean; level: number; cleanText: string } => {
  const text = line.trim();
  if (!text) return { isHeading: false, level: 0, cleanText: "" };

  // 1. 已是 # 列表
  if (text.startsWith("#")) {
    const match = text.match(/^(#{1,6})\s+(.+)$/);
    if (match) {
      return { isHeading: true, level: match[1].length, cleanText: match[2].replace(/[*_#`]/g, "").trim() };
    }
  }

  // 2. 典型书籍/PDF 章节（如"第一章 绪论"、"第1章 开发准备"、"Chapter 5 Neural Networks"）
  const chapterMatch = text.match(/^(Chapter\s+\d+|第\s*[一二三四五六七八九十百0-9]+\s*[章节回课分部篇卷])\s*[:：、\s]?\s*(.*)$/i);
  if (chapterMatch && text.length < 90) {
    const prefix = chapterMatch[1];
    const secondPart = chapterMatch[2]?.trim() || "";
    // 排除无意义后缀："完。"、"完"、"（完）"等
    if (/^[（(]?\s*完\s*[)）]?[。.！!]?\s*$/.test(secondPart)) {
      return { isHeading: false, level: 0, cleanText: "" };
    }
    // 排除正文引用/句式："第X章介绍了..."、"第X章曾经讨论过..."
    if (/^[，,]/.test(secondPart) || /(介绍了|阐述了|描述了|讨论了|探讨了|介绍过|阐述过|描述过|讨论过|探讨过|曾经|就已经|将会|可以|需要|应该|主要|首先|最后|接着|然后)\s*\S/.test(secondPart)) {
      return { isHeading: false, level: 0, cleanText: "" };
    }
    // 部/篇/卷 作为最高层级(level 0)，章/节为 level 1
    const isSuperHeading = /[部篇卷]/.test(prefix);
    const level = isSuperHeading ? 0 : 1;
    const headingText = secondPart ? `${prefix} ${secondPart}` : prefix;
    return { isHeading: true, level, cleanText: headingText.replace(/[*_`]/g, "").trim() };
  }

  // 3. 典型学术文章章节（如"一、 引言"、"二、 相关研究"）
  const chineseListMatch = text.match(/^([一二三四五六七八九十]+、)\s*(.+)$/);
  if (chineseListMatch && text.length < 40 && !/[。！？，,：:；;]/.test(text)) {
    return { isHeading: true, level: 2, cleanText: text.replace(/[*_`]/g, "").trim() };
  }

  // 4. 数字标题列表（如"1. Introduction"、"2.1 Background"、"3.2.1 Methodology"）
  const numericMatch = text.match(/^(\d+(\.\d+){1,3})\s+(.+)$/);
  if (numericMatch && text.length < 60 && !/[。！？，,：:；;]/.test(text)) {
    const level = Math.min(3, (numericMatch[1].match(/\./g) || []).length + 2);
    return { isHeading: true, level, cleanText: text.replace(/[*_`]/g, "").trim() };
  }

  // 单个数字列表——仅短标题如"1. 引言"，非"1. 所有的孩子都会..."句子
  const simpleIntMatch = text.match(/^(\d+)\.\s+([^。，,！!？?：:；;、—…~—\n]+)$/);
  if (simpleIntMatch && text.length < 30 && !/[告诉应当可以需要必须应该能够会要想让去].{3,}/.test(text)) {
    return { isHeading: true, level: 2, cleanText: text.replace(/[*_`]/g, "").trim() };
  }

  // 5. 特殊章节（如"前言"、"引言"、"序言"、"参考文献"、"致谢"、"目录"）
  const specialSections = ["前言", "引言", "序言", "导言", "绪论", "结语", "结论与展望", "参考文献", "致谢", "目录", "Abstract", "References", "Conclusion"];
  // 排除无意义碎片：单字、纯"完""终"等
  if (/^[（(]?\s*(?:全[书本]?\s*)?[完终結结]\s*[)）]?\s*$/.test(text) || text.length < 2) {
    return { isHeading: false, level: 0, cleanText: "" };
  }
  // 排除段落句式：包含句末标点的不是标题
  const hasSentenceEnd = /[。！？!?]/.test(text);
  if (specialSections.includes(text)) {
    return { isHeading: true, level: 1, cleanText: text };
  }
  // 短文本且以特殊标记开头（如"关于作者"、"致读者"），但不是段落开头句
  if (text.length < 25 && !hasSentenceEnd && specialSections.some(s => text.startsWith(s) || text.endsWith(s))) {
    return { isHeading: true, level: 1, cleanText: text };
  }
  // "关于xxx" 作为独立标题（短句、无句末标点）
  if (/^关于[一-龥]{2,15}$/.test(text) && !hasSentenceEnd) {
    return { isHeading: true, level: 1, cleanText: text };
  }

  return { isHeading: false, level: 0, cleanText: "" };
};

// 将纯文本标题转换为标准 Markdown 标题（用于目录索引与视觉美化）
export const preprocessContent = (rawText: string): string => {
  if (!rawText) return "";
  let text = rawText
    .replace(/---?\s*第\s*\d+\s*页\s*\/\s*共\s*\d+\s*页\s*-{0,3}/g, "")
    .replace(/第\s*\d+\s*页\s*\/\s*共\s*\d+\s*页/g, "")
    .replace(/\b(?:ISBN|ISBN-13|ISBN-10)\s*[：:]\s*[\d\-Xx]+/g, "")
    .replace(/^[（(]?\s*(?:全[书本]?\s*)?[完终結结]\s*[)）]?\s*$/gm, "")
    .replace(/^\s*\d{1,4}\s*\/\s*\d{1,4}\s*$/gm, "")
    .replace(/^\s*\d{4}[-\/]\d{1,2}[-\/]\d{1,2}\s*$/gm, "")
    // 清理 EPUB 脚注行（独立一行的 [N] 注释文本）
    .replace(/^\s*\[\d+\]\s+.+$/gm, "")
    // 清理纯页码行 "-2-" "-123-"
    .replace(/^\s*-\d+-\s*$/gm, "")
    // 清理纯脚注数字行 " [1] " 等
    .replace(/^\s*\[\d+\]\s*$/gm, "")
    .replace(/\x0A{4,}/g, "\x0A\x0A\x0A")
    .replace(/^ {4,}/gm, "  ");

  // ── 智能分段：保留原始双空行分隔，段内合并 PDF 软换行 ──
  const blocks = text.split(/\n\n+/);  // 以原始空行分块
  const processed: string[] = [];

  for (const block of blocks) {
    const blockTrimmed = block.trim();
    if (!blockTrimmed) continue;

    // 标题块保持原样
    if (blockTrimmed.startsWith("#") || /^第[一二三四五六七八九十百\d]+[章节部篇卷]/.test(blockTrimmed)) {
      processed.push(blockTrimmed);
      continue;
    }

    // 块内按行合并：积累至少 2 个句子、60 字以上再分段
    const blockLines = blockTrimmed.split("\n").map(l => l.trim()).filter(Boolean);
    const merged: string[] = [];
    let buf = blockLines[0] || "";
    let sentenceCount = 1;

    for (let i = 1; i < blockLines.length; i++) {
      const line = blockLines[i];
      // 句末标点 + 积累了至少 2 句话 + 60 字以上 → 新段落
      if (/[。！？」"」』\)）]$/.test(buf) && buf.length > 60 && sentenceCount >= 2) {
        merged.push(buf);
        buf = line;
        sentenceCount = 1;
      } else {
        buf += line;
        // 粗估句子数
        sentenceCount += (line.match(/[。！？」]/g) || []).length || 1;
      }
    }
    if (buf) merged.push(buf);

    // 每段之间用双空行分隔
    processed.push(merged.join("\n\n"));
  }

  // ── 清理脚注移除后残留的碎片块（合并短碎片到前面的段落） ──
  const cleaned: string[] = [];
  for (const p of processed) {
    const trimmed = p.trim();
    if (!trimmed) continue;
    // 如果当前块极短且不以标点开头，合并到上一个段落
    if (trimmed.length < 15 && !/^[#第\d]/.test(trimmed) && cleaned.length > 0) {
      cleaned[cleaned.length - 1] += trimmed;
    } else {
      cleaned.push(trimmed);
    }
  }

  text = cleaned.join("\n\n");

  // ── 标题识别 ──
  const lines = text.split("\n");
  let inCodeBlock = false;

  const processedLines = lines.map((line) => {
    const text = line.trim();
    if (text.startsWith("```")) {
      inCodeBlock = !inCodeBlock;
      return line;
    }
    if (inCodeBlock) return line;
    if (!text) return line;

    // 已有 Markdown hashes 则不处理
    if (text.startsWith("#")) return line;

    const struc = isStructuralHeading(line);
    if (struc.isHeading) {
      const hashes = "#".repeat(struc.level);
      return `${hashes} ${struc.cleanText}`;
    }

    return line;
  });

  return processedLines.join("\n");
};

/**
 * 按段落（\n\n）切分文本并聚合成不超过 maxChars 的组（2026-08-06）。
 * 与后端 /api/translate-paragraphs 的分组规则一致，保证译文能逐组对齐；
 * 含代码围栏（```）的段落强制独立成组，避免拆散代码块。
 */
export const splitParagraphGroups = (text: string, maxChars = 3500): string[] => {
  if (!text) return [];
  if (text.length <= maxChars) return [text];
  const groups: string[] = [];
  let current = "";
  for (const raw of text.split("\n\n")) {
    const p = raw.replace(/\n+$/g, "");
    if (!p) continue;
    if (p.includes("```")) {
      if (current) { groups.push(current); current = ""; }
      groups.push(p);
      continue;
    }
    if (current && current.length + p.length + 2 > maxChars) {
      groups.push(current);
      current = p;
    } else {
      current = current ? `${current}\n\n${p}` : p;
    }
  }
  if (current) groups.push(current);
  return groups;
};
