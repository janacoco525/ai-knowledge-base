/**
 * useGraphFilters — 图谱筛选原子管理 hook
 *
 * 设计原则（React 最佳实践 — 状态提升 + 单向数据流）：
 * 1. graphFilterDocIds 和 graphFilterLibIds 不再独立管理——它们永远同步
 * 2. 选中文档 → 自动勾选对应目录
 * 3. 选中目录 → 自动勾选该目录下所有文档
 * 4. 统一持久化到 localStorage(key: kb_graph_filters_v2)
 * 5. 刷新/切标签后完整恢复
 */

import { useState, useEffect, useCallback } from "react";
import type { Library, Document } from "../types";

const STORAGE_KEY = "kb_graph_filters_v2";

interface GraphFilterState {
  docIds: string[];
  libIds: string[];
}

function loadState(): GraphFilterState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return { docIds: [], libIds: [] };
}

function saveState(state: GraphFilterState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

/**
 * 原子过滤 hook
 * @param documents - 所有文档列表
 * @param libraries - 所有目录列表
 */
export function useGraphFilters(documents: Document[], libraries: Library[]) {
  const [state, setState] = useState<GraphFilterState>(loadState);

  // 任何变化自动持久化
  useEffect(() => {
    saveState(state);
  }, [state]);

  // 从文档列表推算目录列表
  const computeLibsFromDocs = useCallback(
    (docIds: string[]): string[] => {
      const libSet = new Set<string>();
      docIds.forEach(id => {
        const doc = documents.find(d => d.id === id);
        if (doc) libSet.add(doc.libraryId);
      });
      return Array.from(libSet);
    },
    [documents]
  );

  // 从目录列表推算文档列表
  const computeDocsFromLibs = useCallback(
    (libIds: string[]): string[] => {
      if (libIds.length === 0) return [];
      return documents
        .filter(d => libIds.includes(d.libraryId))
        .map(d => d.id);
    },
    [documents]
  );

  /** 单文档加载 - 同时设文档和目录 */
  const setSingleDoc = useCallback(
    (docId: string) => {
      const doc = documents.find(d => d.id === docId);
      setState({
        docIds: doc ? [docId] : [],
        libIds: doc ? [doc.libraryId] : [],
      });
    },
    [documents]
  );

  /** 全库加载 - 清空所有筛选 */
  const clearAll = useCallback(() => {
    setState({ docIds: [], libIds: [] });
  }, []);

  /** 勾选所有文档（全选） */
  const selectAll = useCallback(() => {
    setState({
      docIds: documents.map(d => d.id),
      libIds: libraries.map(l => l.id),
    });
  }, [documents, libraries]);

  /** 勾选单个目录的所有文档 */
  const selectLib = useCallback(
    (libId: string) => {
      const libDocs = documents.filter(d => d.libraryId === libId);
      setState(prev => ({
        docIds: [
          ...new Set([...prev.docIds, ...libDocs.map(d => d.id)]),
        ],
        libIds: [...new Set([...prev.libIds, libId])],
      }));
    },
    [documents]
  );

  /** 切换单个文档——同步更新目录 */
  const toggleDoc = useCallback(
    (docId: string) => {
      setState(prev => {
        const wasChecked = prev.docIds.includes(docId);
        let nextDocIds: string[];
        if (wasChecked) {
          nextDocIds = prev.docIds.filter(id => id !== docId);
        } else {
          nextDocIds = [...prev.docIds, docId];
        }
        const nextLibIds = computeLibsFromDocs(nextDocIds);
        return { docIds: nextDocIds, libIds: nextLibIds };
      });
    },
    [computeLibsFromDocs]
  );

  /** 切换单个目录——同步更新文档 */
  const toggleLib = useCallback(
    (libId: string) => {
      setState(prev => {
        const wasChecked = prev.libIds.includes(libId);
        let nextLibIds: string[];
        if (wasChecked) {
          nextLibIds = prev.libIds.filter(id => id !== libId);
        } else {
          nextLibIds = [...prev.libIds, libId];
        }
        const nextDocIds = computeDocsFromLibs(nextLibIds);
        return { docIds: nextDocIds, libIds: nextLibIds };
      });
    },
    [computeDocsFromLibs]
  );

  return {
    docIds: state.docIds,
    libIds: state.libIds,
    setSingleDoc,
    clearAll,
    selectAll,
    selectLib,
    toggleDoc,
    toggleLib,
  };
}
