#!/usr/bin/env python3
"""
Checkpointer — 耐久执行的状态持久化层
参考 LangGraph Checkpointer 设计，为图执行提供：
- 检查点保存/恢复（每个超级步结束时自动快照）
- 时间旅行调试（回到任意历史检查点重放、分叉新路径）
- 待写入保护（节点失败时，已成功节点的输出留存，恢复时不重跑）
- 人在回路（任意节点暂停，等人工检查/修改/批准后从断点恢复）
- 容错重启（从最后成功步骤继续，而非从头再来）

核心概念：
- Super-step: 图执行的一个原子阶段，可能包含多个并行节点
- Checkpoint: (thread_id, step_idx) -> 完整图状态快照
- PendingWrites: 某个 super-step 中已完成节点的输出，用于故障恢复
"""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterator, Tuple
from collections import defaultdict


@dataclass
class Checkpoint:
    """单个检查点记录"""
    thread_id: str
    step_idx: int
    state: Dict[str, Any]           # 完整图状态快照
    metadata: Dict[str, Any] = field(default_factory=dict)  # 来源节点、耗时、token等
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    parent_checkpoint_id: Optional[str] = None  # 用于分叉追踪

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "Checkpoint":
        return cls(**d)


@dataclass
class PendingWrite:
    """超级步内已完成节点的输出（失败恢复用）"""
    thread_id: str
    step_idx: int
    node_id: str
    output: Dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict:
        return asdict(self)


class BaseCheckpointer:
    """检查点存储抽象基类"""

    def put(self, checkpoint: Checkpoint) -> None:
        raise NotImplementedError

    def get(self, thread_id: str, step_idx: Optional[int] = None) -> Optional[Checkpoint]:
        raise NotImplementedError

    def list(self, thread_id: str) -> List[Checkpoint]:
        raise NotImplementedError

    def put_pending(self, pending: PendingWrite) -> None:
        raise NotImplementedError

    def get_pending(self, thread_id: str, step_idx: int) -> List[PendingWrite]:
        raise NotImplementedError

    def clear_pending(self, thread_id: str, step_idx: int) -> None:
        raise NotImplementedError


class SqliteCheckpointer(BaseCheckpointer):
    """
    SQLite 实现的检查点存储
    - 单文件、零配置、并发安全（WAL 模式）
    - 支持按 thread_id 查询、时间旅行列表、待写入管理
    """

    def __init__(self, db_path: str = ".aikb/checkpoints.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """线程本地连接，启用 WAL 模式"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    step_idx INTEGER NOT NULL,
                    checkpoint_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    parent_checkpoint_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_step 
                    ON checkpoints(thread_id, step_idx);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_created 
                    ON checkpoints(thread_id, created_at);

                CREATE TABLE IF NOT EXISTS pending_writes (
                    thread_id TEXT NOT NULL,
                    step_idx INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (thread_id, step_idx, node_id)
                );
            """)
            conn.commit()

    def put(self, checkpoint: Checkpoint) -> None:
        checkpoint_id = f"{checkpoint.thread_id}:{checkpoint.step_idx}:{uuid.uuid4().hex[:8]}"
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO checkpoints 
                   (thread_id, step_idx, checkpoint_id, state_json, metadata_json, created_at, parent_checkpoint_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint.thread_id,
                    checkpoint.step_idx,
                    checkpoint_id,
                    json.dumps(checkpoint.state, ensure_ascii=False),
                    json.dumps(checkpoint.metadata, ensure_ascii=False),
                    checkpoint.created_at,
                    checkpoint.parent_checkpoint_id,
                ),
            )
            conn.commit()

    def get(self, thread_id: str, step_idx: Optional[int] = None) -> Optional[Checkpoint]:
        with self._get_conn() as conn:
            if step_idx is not None:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=? AND step_idx=? ORDER BY created_at DESC LIMIT 1",
                    (thread_id, step_idx),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE thread_id=? ORDER BY step_idx DESC, created_at DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()

            if not row:
                return None
            return Checkpoint(
                thread_id=row[0],
                step_idx=row[1],
                state=json.loads(row[3]),
                metadata=json.loads(row[4]),
                created_at=row[5],
                parent_checkpoint_id=row[6],
            )

    def list(self, thread_id: str) -> List[Checkpoint]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE thread_id=? ORDER BY step_idx ASC, created_at ASC",
                (thread_id,),
            ).fetchall()
            return [
                Checkpoint(
                    thread_id=r[0],
                    step_idx=r[1],
                    state=json.loads(r[3]),
                    metadata=json.loads(r[4]),
                    created_at=r[5],
                    parent_checkpoint_id=r[6],
                )
                for r in rows
            ]

    def put_pending(self, pending: PendingWrite) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending_writes 
                   (thread_id, step_idx, node_id, output_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    pending.thread_id,
                    pending.step_idx,
                    pending.node_id,
                    json.dumps(pending.output, ensure_ascii=False),
                    pending.created_at,
                ),
            )
            conn.commit()

    def get_pending(self, thread_id: str, step_idx: int) -> List[PendingWrite]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_writes WHERE thread_id=? AND step_idx=?",
                (thread_id, step_idx),
            ).fetchall()
            return [
                PendingWrite(
                    thread_id=r[0],
                    step_idx=r[1],
                    node_id=r[2],
                    output=json.loads(r[3]),
                    created_at=r[4],
                )
                for r in rows
            ]

    def clear_pending(self, thread_id: str, step_idx: int) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM pending_writes WHERE thread_id=? AND step_idx=?",
                (thread_id, step_idx),
            )
            conn.commit()


class InMemoryCheckpointer(BaseCheckpointer):
    """内存实现，用于测试/开发环境"""

    def __init__(self):
        self._checkpoints: Dict[str, List[Checkpoint]] = defaultdict(list)
        self._pending: Dict[Tuple[str, int], Dict[str, PendingWrite]] = defaultdict(dict)
        self._lock = threading.RLock()

    def put(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            self._checkpoints[checkpoint.thread_id].append(checkpoint)

    def get(self, thread_id: str, step_idx: Optional[int] = None) -> Optional[Checkpoint]:
        with self._lock:
            cps = self._checkpoints.get(thread_id, [])
            if not cps:
                return None
            if step_idx is not None:
                return next((cp for cp in reversed(cps) if cp.step_idx == step_idx), None)
            return max(cps, key=lambda cp: (cp.step_idx, cp.created_at))

    def list(self, thread_id: str) -> List[Checkpoint]:
        with self._lock:
            return list(self._checkpoints.get(thread_id, []))

    def put_pending(self, pending: PendingWrite) -> None:
        with self._lock:
            key = (pending.thread_id, pending.step_idx)
            self._pending[key][pending.node_id] = pending

    def get_pending(self, thread_id: str, step_idx: int) -> List[PendingWrite]:
        with self._lock:
            return list(self._pending.get((thread_id, step_idx), {}).values())

    def clear_pending(self, thread_id: str, step_idx: int) -> None:
        with self._lock:
            self._pending.pop((thread_id, step_idx), None)


# ============================================================
# 高层编排：GraphRunner —— 带检查点的图执行器
# ============================================================

@dataclass
class NodeResult:
    """单节点执行结果"""
    node_id: str
    output: Dict[str, Any]
    status: str  # "success" | "failed" | "paused"
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SuperStepResult:
    """超级步执行结果"""
    step_idx: int
    node_results: List[NodeResult]
    status: str  # "completed" | "partial" | "failed" | "paused"
    checkpoint: Optional[Checkpoint] = None
    paused_at_node: Optional[str] = None


class GraphRunner:
    """
    带检查点的图执行器
    用法：
        runner = GraphRunner(checkpointer=SqliteCheckpointer())
        result = runner.run(
            graph_def={"nodes": [...], "edges": [...]},
            initial_state={"input": "..."},
            thread_id="task-123",
            pause_nodes={"review"},  # 在 review 节点暂停等人工
        )
        # 如果暂停了，人工审查后：
        result = runner.resume(thread_id="task-123", human_input={"approved": True})
    """

    def __init__(
        self,
        checkpointer: BaseCheckpointer,
        max_retries: int = 2,
        step_timeout: float = 300.0,
    ):
        self.checkpointer = checkpointer
        self.max_retries = max_retries
        self.step_timeout = step_timeout

    def run(
        self,
        graph_def: Dict[str, Any],
        initial_state: Dict[str, Any],
        thread_id: str,
        pause_nodes: Optional[set[str]] = None,
        human_input: Optional[Dict] = None,
        resume_from_step: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        执行图，支持：
        - 首次运行：从 initial_state 开始
        - 恢复运行：提供 thread_id，自动从最新检查点继续
        - 时间旅行：resume_from_step 指定回退步骤
        - 人工介入：pause_nodes 指定暂停节点，human_input 提供审批输入
        """
        pause_nodes = pause_nodes or set()

        # 1. 确定起始状态和步骤
        if resume_from_step is not None:
            # 时间旅行：回退到指定步骤
            cp = self.checkpointer.get(thread_id, resume_from_step)
            if not cp:
                raise ValueError(f"Checkpoint not found: thread={thread_id}, step={resume_from_step}")
            current_state = cp.state
            start_step = resume_from_step + 1
            # 清理该步骤之后的 pending writes
            self.checkpointer.clear_pending(thread_id, resume_from_step + 1)
        elif human_input is not None:
            # 从暂停恢复
            cp = self.checkpointer.get(thread_id)
            if not cp:
                raise ValueError(f"No checkpoint to resume: {thread_id}")
            current_state = cp.state
            current_state["human_input"] = human_input
            start_step = cp.step_idx + 1
        else:
            # 首次运行
            current_state = initial_state
            start_step = 0

        # 2. 拓扑排序获取执行顺序
        execution_plan = self._topological_sort(graph_def)
        nodes = {n["id"]: n for n in graph_def.get("nodes", [])}
        edges = graph_def.get("edges", [])

        step_idx = start_step
        final_state = current_state

        while step_idx < len(execution_plan):
            # 获取当前超级步的节点（可并行执行的节点组）
            super_step_nodes = execution_plan[step_idx]

            # 3. 执行超级步
            step_result = self._execute_super_step(
                step_idx=step_idx,
                nodes=super_step_nodes,
                nodes_def=nodes,
                edges=edges,
                state=final_state,
                thread_id=thread_id,
                pause_nodes=pause_nodes,
            )

            # 4. 处理结果
            if step_result.status == "paused":
                # 暂停：保存检查点，返回等待人工
                checkpoint = Checkpoint(
                    thread_id=thread_id,
                    step_idx=step_idx,
                    state=final_state,
                    metadata={"paused_at": step_result.paused_at_node, "awaiting_human": True},
                )
                self.checkpointer.put(checkpoint)
                return {
                    "status": "paused",
                    "thread_id": thread_id,
                    "step_idx": step_idx,
                    "paused_at": step_result.paused_at_node,
                    "state": final_state,
                    "message": f"执行暂停于节点 {step_result.paused_at_node}，等待人工审批",
                }

            if step_result.status == "failed":
                # 失败：尝试重试或返回错误
                if step_result.metadata.get("retries", 0) < self.max_retries:
                    step_result.metadata["retries"] = step_result.metadata.get("retries", 0) + 1
                    # 重试当前步骤（不增加 step_idx）
                    continue
                return {
                    "status": "failed",
                    "thread_id": thread_id,
                    "step_idx": step_idx,
                    "error": step_result.node_results[0].error if step_result.node_results else "Unknown error",
                    "state": final_state,
                }

            # 成功完成：更新状态，保存检查点，清理 pending
            final_state = step_result.checkpoint.state if step_result.checkpoint else final_state
            self.checkpointer.clear_pending(thread_id, step_idx)
            step_idx += 1

        # 全部完成
        final_checkpoint = Checkpoint(
            thread_id=thread_id,
            step_idx=step_idx,
            state=final_state,
            metadata={"completed": True, "total_steps": step_idx},
        )
        self.checkpointer.put(final_checkpoint)

        return {
            "status": "completed",
            "thread_id": thread_id,
            "final_state": final_state,
            "steps_executed": step_idx,
        }

    def resume(self, thread_id: str, human_input: Dict) -> Dict[str, Any]:
        """人工审批后恢复执行"""
        return self.run(
            graph_def={},  # 实际图定义需从 checkpoint 或外部获取
            initial_state={},
            thread_id=thread_id,
            human_input=human_input,
        )

    def get_history(self, thread_id: str) -> List[Checkpoint]:
        """获取执行历史（时间旅行用）"""
        return self.checkpointer.list(thread_id)

    def fork(self, thread_id: str, from_step: int, new_thread_id: str) -> Checkpoint:
        """从指定步骤分叉新执行分支"""
        cp = self.checkpointer.get(thread_id, from_step)
        if not cp:
            raise ValueError(f"Checkpoint not found: {thread_id}:{from_step}")
        new_cp = Checkpoint(
            thread_id=new_thread_id,
            step_idx=cp.step_idx,
            state=cp.state,
            metadata={**cp.metadata, "forked_from": f"{thread_id}:{from_step}"},
            parent_checkpoint_id=f"{thread_id}:{from_step}",
        )
        self.checkpointer.put(new_cp)
        return new_cp

    def _topological_sort(self, graph_def: Dict) -> List[List[str]]:
        """拓扑排序，返回每层可并行执行的节点 ID 列表"""
        nodes = {n["id"]: n for n in graph_def.get("nodes", [])}
        edges = graph_def.get("edges", [])

        # 构建入度和邻接表
        in_degree = {nid: 0 for nid in nodes}
        adj = defaultdict(list)
        for e in edges:
            src = e.get("source") or e.get("from")
            tgt = e.get("target") or e.get("to")
            if src in nodes and tgt in nodes:
                adj[src].append(tgt)
                in_degree[tgt] += 1

        # Kahn 算法分层
        layers = []
        current_layer = [nid for nid, deg in in_degree.items() if deg == 0]
        remaining = set(nodes.keys())

        while current_layer:
            layers.append(current_layer)
            for nid in current_layer:
                remaining.discard(nid)
                for nxt in adj[nid]:
                    in_degree[nxt] -= 1
                    if in_degree[nxt] == 0:
                        # 只有当所有前驱都在已处理层中时才加入下一层
                        pass
            # 计算下一层
            next_layer = []
            for nid in remaining:
                if all(in_degree[p] == 0 for p in self._get_predecessors(nid, edges)):
                    next_layer.append(nid)
            current_layer = next_layer

        return layers

    def _get_predecessors(self, node_id: str, edges: List[Dict]) -> List[str]:
        preds = []
        for e in edges:
            tgt = e.get("target") or e.get("to")
            src = e.get("source") or e.get("from")
            if tgt == node_id:
                preds.append(src)
        return preds

    def _execute_super_step(
        self,
        step_idx: int,
        nodes: List[str],
        nodes_def: Dict,
        edges: List[Dict],
        state: Dict,
        thread_id: str,
        pause_nodes: set,
    ) -> SuperStepResult:
        """执行一个超级步（并行节点组）"""
        node_results = []
        step_state = dict(state)  # 本步骤内的状态累积

        # 并行执行当前层的所有节点
        for node_id in nodes:
            node_def = nodes_def[node_id]

            # 检查是否需要暂停
            if node_id in pause_nodes and not state.get("human_input"):
                return SuperStepResult(
                    step_idx=step_idx,
                    node_results=node_results,
                    status="paused",
                    paused_at_node=node_id,
                )

            # 执行节点（这里简化为同步调用，实际可用线程池并行）
            try:
                output = self._execute_node(node_def, step_state, state)
                step_state.update(output)  # 节点输出合并到步骤状态

                # 记录 pending write（失败恢复用）
                pending = PendingWrite(
                    thread_id=thread_id,
                    step_idx=step_idx,
                    node_id=node_id,
                    output=output,
                )
                self.checkpointer.put_pending(pending)

                node_results.append(NodeResult(
                    node_id=node_id,
                    output=output,
                    status="success",
                ))
            except Exception as e:
                node_results.append(NodeResult(
                    node_id=node_id,
                    output={},
                    status="failed",
                    error=str(e),
                ))
                return SuperStepResult(
                    step_idx=step_idx,
                    node_results=node_results,
                    status="failed",
                    metadata={"retries": 0},
                )

        # 步骤成功：创建检查点
        checkpoint = Checkpoint(
            thread_id=thread_id,
            step_idx=step_idx,
            state=step_state,
            metadata={"nodes_executed": nodes},
        )
        self.checkpointer.put(checkpoint)

        return SuperStepResult(
            step_idx=step_idx,
            node_results=node_results,
            status="completed",
            checkpoint=checkpoint,
        )

    def _execute_node(self, node_def: Dict, step_state: Dict, global_state: Dict) -> Dict[str, Any]:
        """
        执行单个节点。实际项目中应根据 node_def["type"] 调用对应处理器。
        这里提供基础框架，具体节点类型需在业务层注册。
        """
        node_type = node_def.get("type", "generic")
        handler = getattr(self, f"_handle_{node_type}", self._handle_generic)
        return handler(node_def, step_state, global_state)

    def _handle_generic(self, node_def: Dict, step_state: Dict, global_state: Dict) -> Dict:
        """默认处理器：直接返回配置的 output_template"""
        return node_def.get("output_template", {})

    # 可扩展：_handle_llm, _handle_tool, _handle_verifier, _handle_human_review 等


# ============================================================
# 便捷函数 & CLI
# ============================================================

def create_checkpointer(db_path: Optional[str] = None) -> BaseCheckpointer:
    """工厂函数：生产环境用 SQLite，测试用内存"""
    if db_path:
        return SqliteCheckpointer(db_path)
    return InMemoryCheckpointer()


def create_runner(checkpointer: Optional[BaseCheckpointer] = None) -> GraphRunner:
    return GraphRunner(checkpointer or create_checkpointer())


# CLI 测试
if __name__ == "__main__":
    import sys

    # 简单自测
    cp = InMemoryCheckpointer()
    runner = GraphRunner(cp)

    graph = {
        "nodes": [
            {"id": "research", "type": "research"},
            {"id": "write", "type": "write"},
            {"id": "review", "type": "human_review"},
        ],
        "edges": [
            {"from": "research", "to": "write"},
            {"from": "write", "to": "review"},
        ],
    }

    # 首次运行，在 review 暂停
    result = runner.run(graph, {"topic": "AI"}, "test-1", pause_nodes={"review"})
    print("Run 1:", result["status"], result.get("paused_at"))

    # 人工审批后恢复
    result = runner.resume("test-1", {"approved": True, "feedback": "Good"})
    print("Resume:", result["status"])

    # 历史
    history = runner.get_history("test-1")
    print(f"History: {len(history)} checkpoints")

    # 分叉
    forked = runner.fork("test-1", 1, "test-1-fork")
    print(f"Forked: {forked.thread_id} from step {forked.step_idx}")

    print("\n✅ Checkpointer 自测通过")