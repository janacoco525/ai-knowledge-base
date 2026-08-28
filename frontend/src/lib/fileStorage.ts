/**
 * IndexedDB 文件存储 — 保存原始 PDF/EPUB 文件，支持按需渲染
 * localStorage 限制约 5MB，IndexedDB 可达 50MB+
 */

const DB_NAME = "ai_kb_files";
const DB_VERSION = 1;
const STORE_NAME = "original_files";

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "docId" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** 存储原始文件 */
export async function saveOriginalFile(docId: string, file: File): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    store.put({ docId, file, name: file.name, type: file.type, savedAt: Date.now() });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/** 读取原始文件 */
export async function getOriginalFile(docId: string): Promise<File | null> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.get(docId);
    req.onsuccess = () => resolve(req.result?.file || null);
    req.onerror = () => reject(req.error);
  });
}

/** 删除原始文件 */
export async function removeOriginalFile(docId: string): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    store.delete(docId);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/** 检查是否有原始文件 */
export async function hasOriginalFile(docId: string): Promise<boolean> {
  const file = await getOriginalFile(docId);
  return file !== null;
}
