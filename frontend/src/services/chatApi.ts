/**
 * AI知识库 - 聊天记录API服务
 * 替代localStorage，对接后端 /api/chat/ 端点
 */
import type { ChatMessage } from "../types";

const API_BASE = "/api/chat";
const SESSION_ID = "default";

interface ChatSessionMeta {
  id: string;
  title: string;
  libraryId: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
  preview: string;
}

interface ChatSession {
  id: string;
  title: string;
  libraryId: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
}

/** 从后端加载聊天记录 */
export async function loadChatHistory(): Promise<ChatMessage[]> {
  try {
    const resp = await fetch(`${API_BASE}/sessions/${SESSION_ID}`);
    if (!resp.ok) return [];
    const session: ChatSession = await resp.json();
    return session.messages || [];
  } catch {
    return [];
  }
}

/** 序列化消息（含 evidence/followUps/webSupplemented/scope/customDocIds，刷新后不丢引用参考） */
export function buildSessionPayload(messages: ChatMessage[]): string {
  return JSON.stringify({
    sessionId: SESSION_ID,
    title: "对话记录",
    libraryId: "all",
    messages: messages.map(m => ({
      id: m.id,
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
      citations: m.citations || [],
      groundingSources: m.groundingSources || [],
      evidence: m.evidence || [],
      followUps: m.followUps || [],
      webSupplemented: m.webSupplemented || false,
      scope: m.scope,
      customDocIds: m.customDocIds || [],
    })),
  });
}

/** 保存聊天记录到后端（2026-08-14：检查 resp.ok，失败不再静默） */
export async function saveChatHistory(messages: ChatMessage[]): Promise<void> {
  try {
    const resp = await fetch(`${API_BASE}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: buildSessionPayload(messages),
    });
    if (!resp.ok) {
      console.warn(`[saveChatHistory] 保存失败 HTTP ${resp.status}`);
    }
  } catch (err) {
    console.warn("[saveChatHistory] 保存异常:", err);
  }
}

/** 清空聊天记录 */
export async function clearChatHistory(): Promise<void> {
  try {
    await fetch(`${API_BASE}/sessions/${SESSION_ID}`, { method: "DELETE" });
  } catch {
    // 静默失败
  }
}

/** 从localStorage迁移旧数据到后端（只执行一次） */
export async function migrateLocalStorageChat(): Promise<boolean> {
  const saved = localStorage.getItem("kb_chat");
  if (!saved) return false;

  try {
    const parsed = JSON.parse(saved);
    if (!Array.isArray(parsed) || parsed.length === 0) {
      localStorage.removeItem("kb_chat");
      return false;
    }

    // 检查后端是否已有数据
    const resp = await fetch(`${API_BASE}/sessions`);
    if (resp.ok) {
      const { total } = await resp.json();
      if (total > 0) return false; // 已有后端数据，不覆盖
    }

    // 执行迁移
    await fetch(`${API_BASE}/migrate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: parsed.map((m: any) => ({
          id: m.id || `msg-${Date.now()}`,
          role: m.role || "user",
          content: m.content || "",
          timestamp: m.timestamp || new Date().toISOString(),
          citations: m.citations || [],
          groundingSources: m.groundingSources || [],
        })),
      }),
    });

    // 迁移成功后清除 localStorage
    localStorage.removeItem("kb_chat");
    return true;
  } catch {
    return false;
  }
}
