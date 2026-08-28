"""
AI知识库 - 文件夹扫描模块
自动扫描指定目录，解析文件并入库
支持多文件夹、增量检测(mtime+size)、diff对比、refresh刷新
并行化：Fan-out/Fan-in 模式（参考 Graph Engineering §6）
"""
import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Callable, Set
from app.rag_app.config import Config
from app.rag_app.knowledge_base import KnowledgeBase
from app.rag_app.parser import DocumentParser
from core.parallel import parallel_map

logger = logging.getLogger("ai_kb.scanner")


def make_path_identity(dir_path: Path, file_path: Path) -> tuple[str, str, str]:
    """统一文档路径身份三件套 (rel_path, rel_dir, source_root)
    - rel_path: 相对扫描根的 POSIX 路径（唯一身份，physical_name 统一用此）
    - rel_dir:  目录层级（POSIX，'.' = 根目录），供后续树形分组/统计/图谱聚合
    - source_root: 扫描根规范化绝对路径
    （2026-08-20 结构化 v2：身份唯一化，替代绝对路径/纯文件名混存）
    """
    source_root = str(dir_path.resolve())
    try:
        rel_path = str(file_path.relative_to(dir_path)).replace("\\", "/")
    except ValueError:
        rel_path = file_path.name
    rel_dir = rel_path.rsplit("/", 1)[0] if "/" in rel_path else "."
    return rel_path, rel_dir, source_root


class Scanner:
    """文件夹扫描器：递归扫描目录，批量入库（支持并行 Fan-out/Fan-in）"""

    def __init__(self, config: Optional[Config] = None, kb: Optional[KnowledgeBase] = None, max_workers: int = 4):
        self.config = config or Config()
        self.kb = kb or KnowledgeBase(self.config)
        self.parser = DocumentParser(self.config)
        self.max_workers = max_workers
        self._scanning = False
        self._progress = {"total": 0, "indexed": 0, "skipped": 0, "failed": 0, "current_file": ""}

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    @property
    def progress(self) -> dict:
        return self._progress.copy()

    def scan_directory(self, directory: str, domain: str = "default",
                       on_progress: Optional[Callable] = None,
                       force: bool = False,
                       target_files: Optional[Set] = None) -> dict:
        """
        扫描指定目录，将所有支持的文件入库（并行 Fan-out/Fan-in）

        Args:
            directory: 要扫描的目录路径
            domain: 知识域标签
            on_progress: 进度回调函数(progress_dict)
            force: True=跳过增量检测全量重扫
            target_files: 限定只扫描这些文件名集合（用于 refresh diff）

        Returns:
            扫描结果统计 {total, indexed, skipped, failed}
        """
        if self._scanning:
            return {"error": "扫描正在进行中"}

        dir_path = Path(directory)
        if not dir_path.exists():
            return {"error": f"目录不存在: {directory}"}
        if not dir_path.is_dir():
            return {"error": f"不是目录: {directory}"}

        self._scanning = True
        self._progress = {"total": 0, "indexed": 0, "skipped": 0, "failed": 0, "current_file": ""}

        try:
            # 1. 收集所有支持的文件
            files = self._collect_files(dir_path)
            self._progress["total"] = len(files)

            if not files:
                return {"total": 0, "indexed": 0, "skipped": 0, "failed": 0, "message": "目录中没有支持的文件"}

            # 2. 构建已索引文件清单 {physical_name -> {mtime, size}}
            indexed_map = {}
            if not force:
                existing_files = self.kb.list_files()
                for f in existing_files:
                    indexed_map[f.get("physical_name", f.get("name", ""))] = {
                        "mtime": f.get("file_mtime", 0),
                        "size": f.get("file_size", 0),
                    }

            # 3. 预过滤：增量检测 + target_files 限定
            #    先在主线程做轻量级过滤（stat 元数据），避免并行任务中重复 stat
            files_to_process = []
            skipped_count = 0
            for file_path in files:
                if not self._scanning:
                    break

                rel_path = str(file_path.relative_to(dir_path)).replace("\\", "/")

                # 限定模式：只处理 target_files 中的文件
                if target_files is not None and rel_path not in target_files and file_path.name not in target_files:
                    skipped_count += 1
                    continue

                # 增量检测：相对路径或文件名+大小+mtime 相同则跳过
                if not force and target_files is None:
                    existing = indexed_map.get(rel_path) or indexed_map.get(file_path.name)
                    if existing:
                        current_stat = file_path.stat()
                        if (current_stat.st_mtime == existing["mtime"] and
                                current_stat.st_size == existing["size"]):
                            skipped_count += 1
                            continue

                files_to_process.append(file_path)

            self._progress["skipped"] = skipped_count
            self._progress["total"] = len(files_to_process)

            if not files_to_process:
                return {"total": 0, "indexed": 0, "skipped": skipped_count, "failed": 0, "message": "无需处理文件"}

            # 4. 并行处理文件（Fan-out: 并行入库，Fan-in: 聚合进度）
            def _index_one(file_path: Path):
                rel_path, rel_dir, source_root = make_path_identity(dir_path, file_path)
                stat = file_path.stat()

                # 分类用父目录名（兼容存量 domain 语义）；完整层级由 rel_dir 结构化承载
                auto_domain = file_path.parent.name
                if auto_domain in (".", "..", ""):
                    auto_domain = domain

                chunk_count = self.kb.index_file_with_metadata(
                    file_path=str(file_path),
                    file_name=file_path.name,
                    uploaded_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    file_size=stat.st_size,
                    domain=auto_domain,
                    physical_name=rel_path,
                    file_mtime=stat.st_mtime,
                    source_root=source_root,
                    rel_path=rel_path,
                    rel_dir=rel_dir,
                )
                return rel_path, chunk_count

            # 进度回调聚合
            progress_lock = threading.Lock()
            completed = 0

            def on_progress(completed_count: int, total: int):
                nonlocal completed
                with progress_lock:
                    completed = completed_count
                    self._progress["indexed"] = sum(1 for r in results if r is not None and r[1] > 0)
                    self._progress["failed"] = sum(1 for r in results if r is None)
                    self._progress["current_file"] = files_to_process[completed_count - 1].name if completed_count > 0 else ""
                    if on_progress:
                        on_progress(self._progress)

            # 并行执行
            from core.parallel import parallel_map
            results = parallel_map(
                _index_one,
                files_to_process,
                max_workers=self.max_workers,
                on_progress=on_progress,
            )

            # 聚合结果
            indexed = sum(1 for r in results.results if r[1] > 0)
            failed = len(results.errors)
            self._progress["indexed"] = indexed
            self._progress["failed"] = failed

            return self._progress.copy()

        finally:
            self._scanning = False
            self._progress["current_file"] = ""

    def diff(self) -> dict:
        """
        对比所有扫描路径下文件与已索引文件，返回变更统计

        Returns:
            {new: [{name, path, size, mtime}], modified: [...], deleted: [...],
             new_count, modified_count, deleted_count, scan_paths: [...], total_indexed: N,
             last_scan_time: str}
        """
        scan_paths = self.config.get_scan_paths()
        if not scan_paths:
            return {"new": [], "modified": [], "deleted": [],
                    "new_count": 0, "modified_count": 0, "deleted_count": 0,
                    "scan_paths": [], "total_indexed": 0, "last_scan_time": ""}

        # 构建已索引文件映射（2026-08-20 结构化 v2：三张表分层匹配）
        # indexed_map:   (source_root, rel_path) 精确身份（新数据）
        # indexed_by_rel:   rel_path 兜底（旧数据：无 source_root 的相对路径）
        # indexed_by_name:  纯文件名兜底（最旧数据）
        indexed_map = {}
        indexed_by_rel = {}
        indexed_by_name = {}
        existing_files = self.kb.list_files()
        for f in existing_files:
            pname = f.get("physical_name", f.get("name", ""))
            info = {
                "mtime": f.get("file_mtime", 0),
                "size": f.get("file_size", 0),
                "domain": f.get("domain", ""),
                "uploaded_at": f.get("uploaded_at", ""),
                "source_root": f.get("source_root", ""),
                "rel_dir": f.get("rel_dir", ""),
            }
            sr = f.get("source_root") or ""
            rel = f.get("rel_path") or (pname if pname and not os.path.isabs(pname) else "")
            if sr and rel:
                indexed_map[(sr, rel)] = info
            elif rel:
                indexed_by_rel.setdefault(rel, info)
            else:
                indexed_by_name.setdefault(f.get("file_name") or pname, info)

        # 遍历所有扫描路径收集磁盘文件（身份 = (source_root, rel_path)，跨根同名不冲突）
        disk_files = {}  # {(source_root, rel_path): {name, path, size, mtime, source_root, rel_dir}}
        for sp in scan_paths:
            sp_path = Path(sp)
            if not sp_path.is_dir():
                continue
            root_abs = str(sp_path.resolve())
            for root, dirs, filenames in os.walk(sp_path):
                for fname in filenames:
                    fpath = Path(root) / fname
                    if fpath.suffix.lower() not in self.config.SUPPORTED_EXTENSIONS:
                        continue
                    rel = str(fpath.relative_to(sp_path)).replace("\\", "/")
                    key = (root_abs, rel)
                    if key not in disk_files:
                        disk_files[key] = {
                            "name": fname,
                            "path": str(fpath),
                            "size": fpath.stat().st_size,
                            "mtime": fpath.stat().st_mtime,
                            "source_root": root_abs,
                            "rel_dir": rel.rsplit("/", 1)[0] if "/" in rel else ".",
                        }

        new_files = []
        modified_files = []

        for key, info in disk_files.items():
            sr, rel = key
            # 匹配优先级：精确身份 → 相对路径兜底（旧数据） → 纯文件名兜底（最旧数据）
            idx = (indexed_map.get(key)
                   or indexed_by_rel.get(rel)
                   or indexed_by_name.get(info["name"]))
            if not idx:
                new_files.append(info)
            else:
                if (abs(info["mtime"] - idx["mtime"]) > 1.0 or
                        info["size"] != idx["size"]):
                    modified_files.append(info)

        # 已索引但磁盘不存在的文件 → deleted（按身份表分层判定，兼容旧格式）
        disk_rel_paths = {k[1] for k in disk_files}
        disk_names = {info["name"] for info in disk_files.values()}
        deleted_files = []
        for (sr, rel), idx in indexed_map.items():
            if rel not in disk_rel_paths:
                deleted_files.append({"name": rel, **idx})
        for rel, idx in indexed_by_rel.items():
            if rel not in disk_rel_paths:
                deleted_files.append({"name": rel, **idx})
        for name, idx in indexed_by_name.items():
            if name not in disk_names:
                deleted_files.append({"name": name, **idx})

        # 获取最近扫描时间
        last_scan = ""
        if existing_files:
            uploaded_times = [f.get("uploaded_at", "") for f in existing_files if f.get("uploaded_at")]
            if uploaded_times:
                last_scan = max(uploaded_times)

        return {
            "new": new_files,
            "modified": modified_files,
            "deleted": deleted_files,
            "new_count": len(new_files),
            "modified_count": len(modified_files),
            "deleted_count": len(deleted_files),
            "scan_paths": scan_paths,
            "total_indexed": len(existing_files),
            "last_scan_time": last_scan,
        }

    def scan_refresh(self, on_progress: Optional[Callable] = None) -> dict:
        """
        仅重索引变更文件（diff → 扫描 new + modified）
        每个文件按其来源路径的域名入库

        Returns:
            {diff: diff_result, scan_result: scan result}
        """
        diff_result = self.diff()
        changed = diff_result["new"] + diff_result["modified"]
        if not changed:
            return {"diff": diff_result, "scan_result": {"total": 0, "indexed": 0, "skipped": 0, "failed": 0,
                    "message": "所有文件已是最新"}}

        # 按目录分组，对每组扫描
        aggregated = {"total": len(changed), "indexed": 0, "skipped": 0, "failed": 0}
        # changed 中的 name 可能是相对路径或纯文件名，scan_directory 两种都支持
        changed_names = {f["name"] for f in changed}

        for sp in diff_result["scan_paths"]:
            if not self._scanning:
                break
            result = self.scan_directory(
                directory=sp,
                domain="default",
                on_progress=on_progress,
                force=True,
                target_files=changed_names,
            )
            if "error" in result:
                continue
            aggregated["indexed"] += result.get("indexed", 0)
            aggregated["skipped"] += result.get("skipped", 0)
            aggregated["failed"] += result.get("failed", 0)

        return {"diff": diff_result, "scan_result": aggregated}

    def stop(self):
        """停止扫描"""
        self._scanning = False

    def _collect_files(self, directory: Path) -> list[Path]:
        """递归收集目录下所有支持的文件"""
        supported = self.config.SUPPORTED_EXTENSIONS
        files = []
        for root, dirs, filenames in os.walk(directory):
            for fname in filenames:
                fpath = Path(root) / fname
                if fpath.suffix.lower() in supported:
                    files.append(fpath)
        files.sort(key=lambda p: p.name.lower())
        return files
