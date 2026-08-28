export interface Library {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  color: string;
}

export interface Highlight {
  id: string;
  text: string;
  color: 'yellow' | 'green' | 'pink' | 'blue';
  comment?: string;
  createdAt: string;
}

export interface Document {
  id: string;
  libraryId: string;
  title: string;
  content: string;
  sourceUrl?: string;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  summary?: string;
  entitiesProcessed?: boolean;
  fileType?: string;  // 文件扩展名，如 ".pdf" / ".epub"
  charCount?: number;  // 后端元数据的字符数（正文懒加载，content 未加载时用此展示）
  highlights?: Highlight[];
  bookmarks?: { id: string; label: string; scrollTop: number; ratio: number; createdAt: string }[];
}

export type NodeCategory = 'person' | 'event' | 'concept' | 'organization' | 'system' | 'tool' | 'process' | 'location';

export interface GraphNode {
  id: string;
  label: string;
  category: NodeCategory;
  libraryId: string;
  docId?: string;
  scopeDocIds?: string[];
  sourceCount?: number;
  sourceRemoved?: boolean;
  weight?: number;  // 提取端 god score 映射（2026-08-13）：关键节点放大/高亮用
  x?: number;
  y?: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  libraryId: string;
  docId?: string;
  scopeDocIds?: string[];
  sourceRemoved?: boolean;
}

// API contract layer: backend graph payloads do not match the frontend's editable graph model 1:1.
// Keep these types separate so consumer code must perform an explicit adaptation step.
export interface GraphApiNode {
  id?: string;
  label?: string;
  category?: string;
  type?: string;
  source_file?: string;
  source_chunk_ids?: string[];
  weight?: number;
}

export interface GraphApiEdge {
  id?: string;
  source?: string;
  target?: string;
  from?: string;
  to?: string;
  label?: string;
  relation_type?: string;
  source_file?: string;
  source_chunk_ids?: string[];
  weight?: number;
}

export interface GraphApiPayload {
  nodes?: GraphApiNode[];
  edges?: GraphApiEdge[];
  meta?: Record<string, unknown>;
  error?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  citations?: Array<{ docId: string; docTitle: string; snippet: string }>;
  isSearchingWeb?: boolean;
  groundingSources?: Array<{ title: string; uri: string }>;
  evidence?: EvidenceItem[];
  followUps?: string[];
  webSupplemented?: boolean;  // 2026-08-13：知识库未覆盖时自动联网补充的回答
  scope?: "all" | "local" | "web" | "custom";
  customDocIds?: string[];
  streaming?: boolean;
}

export interface EvidenceItem {
  text: string;
  file_name?: string;
  physical_name?: string;
  page_number?: number;
  chunk_index?: number;
}

export interface ChatSession {
  id: string;
  libraryId: string; // "all" or specific
  title: string;
  messages: ChatMessage[];
  createdAt: string;
}

export interface KnowledgeCard {
  id: string;
  docId: string;
  front: string; // concept, question, cue
  back: string;  // explanation, detailed answer, key takeaways
  tags: string[];
  // ⛔ 2026-08-14（任务十六）：增加 "new" 未学态——新卡默认未学，
  // 只有评分后才进入 easy/medium/hard（对标 Anki New→Learning→Review 状态机）
  difficulty?: "new" | "easy" | "medium" | "hard";
  createdAt: string;
  sourceRemoved?: boolean;
}

export interface VocabularyTerm {
  id: string;
  docId?: string;
  term: string;
  definition: string;
  contextSnippet?: string;
  createdAt: string;
  status: "learning" | "mastered";
}
