// 用户本地数据键清单与打包/解包（2026-08-14，任务二十八）
// 与设置页"数据迁移"导出共用；服务端同步（user_state.json）基于同一份 bundle。

export const LOCAL_DATA_KEYS = [
  "kb_knowledge_cards",
  "kb_vocabulary_terms",
  "kb_nodes",
  "kb_edges",
  "kb_deleted_nodes",
  "kb_deleted_edges",
  "kb_graph_sessions",
  "kb_graph_sessions_backup",
  "kb_graph_ver",
  "kb_graph_migrated_v2",
  "kb_libraries",
  "kb_annotations",
  "kb_left_sidebar_width",
  "kb_selected_doc",
] as const;

export interface LocalDataBundle {
  version: number;
  updatedAt: string;
  data: Record<string, unknown>;
}

/** 从 localStorage 打包当前全部用户数据（无任何清空/重置操作）。 */
export function buildLocalDataBundle(): LocalDataBundle {
  const data: Record<string, unknown> = {};
  for (const k of LOCAL_DATA_KEYS) {
    const raw = localStorage.getItem(k);
    if (raw === null) continue;
    try { data[k] = JSON.parse(raw); } catch { data[k] = raw; }
  }
  return { version: 1, updatedAt: new Date().toISOString(), data };
}

/** 将 bundle.data 写回 localStorage；返回写入的键数量。 */
export function applyLocalDataBundle(data: Record<string, unknown>): number {
  let applied = 0;
  for (const k of LOCAL_DATA_KEYS) {
    if (k in data) {
      const v = data[k];
      localStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v));
      applied++;
    }
  }
  return applied;
}
