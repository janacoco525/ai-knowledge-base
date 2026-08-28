// 用户状态服务端同步（2026-08-14，任务二十八）
// 落盘到 app/rag_app/data/user_state.json，复制数据文件夹即可整机迁移。
import type { LocalDataBundle } from "../lib/userData";

export async function getServerUserState(): Promise<LocalDataBundle | null> {
  try {
    const resp = await fetch("/api/user-state");
    if (!resp.ok) return null;
    const d = await resp.json();
    if (!d.exists || typeof d.data !== "object" || d.data === null) return null;
    return { version: d.version || 1, updatedAt: d.updatedAt || "", data: d.data };
  } catch {
    return null;
  }
}

export async function saveServerUserState(bundle: LocalDataBundle): Promise<boolean> {
  try {
    const resp = await fetch("/api/user-state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bundle),
    });
    return resp.ok;
  } catch {
    return false;
  }
}
