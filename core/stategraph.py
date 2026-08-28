#!/usr/bin/env python3
"""
StateGraph — 显式图编排层
参考《Graph Engineering》§5 G=(V,E,S,P) + LangGraph StateGraph 设计

⚠️ 接入状态（2026-08-10 审计标记）：当前仅 tests/test_stategraph_flow.py、
tests/test_stategraph_simple.py 使用，业务代码（app/rag_app）未接入。如需启用须先接线。

核心概念：
- State: 图执行过程中的共享状态（字典，支持类型注解）
- Node: 处理单元，接收 State 返回 State 更新（可并行）
- Edge: 节点间连接，支持条件路由
- Policy: 执行策略（并行度、超时、重试、检查点间隔）

设计目标：
- 让图结构显式化、可视化、可审计
- 支持节点级并行（Fan-out/Fan-in）
- 内置检查点、条件路由、人工介入点
- 可序列化为 JSON（存储、版本控制、跨进程传递）
"""

import json
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Set, Union, TypeVar, Generic
from enum import Enum
import logging

from .checkpointer import Checkpoint, BaseCheckpointer, InMemoryCheckpointer, SqliteCheckpointer

logger = logging.getLogger("ai_kb.stategraph")

T = TypeVar("T")


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    SKIPPED = "skipped"


@dataclass
class NodeResult:
    """单节点执行结果"""
    node_id: str
    status: NodeStatus
    output: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    """图边定义"""
    source: str
    target: str
    condition: Optional[str] = None  # 条件表达式，如 "state.get('approved') == True"
    condition_fn: Optional[Callable[[Dict], bool]] = None  # 或可调用对象
    weight: int = 1


@dataclass
class Node:
    """图节点定义"""
    id: str
    type: str  # "llm" | "tool" | "verifier" | "human" | "generic" | "parallel_group"
    handler: Optional[Callable[[Dict], Dict]] = None  # 同步处理函数
    async_handler: Optional[Callable[[Dict], Any]] = None  # 异步处理函数
    config: Dict[str, Any] = field(default_factory=dict)  # 节点配置
    timeout: float = 300.0
    retries: int = 0
    parallel: bool = False  # 是否并行执行（与其他 parallel=True 节点并行）
    pause_before: bool = False  # 执行前暂停等人工
    pause_after: bool = False   # 执行后暂停等人工
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.handler and not self.async_handler:
            raise ValueError(f"Node {self.id} must have handler or async_handler")


@dataclass
class StateGraph:
    """
    显式状态图定义
    
    用法：
        graph = StateGraph()
        graph.add_node("research", research_handler, type="llm", parallel=True)
        graph.add_node("write", write_handler, type="llm", parallel=True)
        graph.add_node("review", human_review_handler, type="human", pause_before=True)
        graph.add_edge("research", "write")
        graph.add_edge("write", "review")
        graph.add_edge("review", "write", condition="state.get('revision_needed')")
        
        # 编译后可执行
        compiled = graph.compile()
        result = compiled.invoke({"topic": "AI"})
    """
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    entry_point: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_node(
        self,
        node_id: str,
        handler: Callable[[Dict], Dict],
        *,
        type: str = "generic",
        async_handler: Optional[Callable] = None,
        config: Optional[Dict] = None,
        timeout: float = 300.0,
        retries: int = 0,
        parallel: bool = False,
        pause_before: bool = False,
        pause_after: bool = False,
        **metadata,
    ) -> "StateGraph":
        """添加节点"""
        if node_id in self.nodes:
            raise ValueError(f"Node {node_id} already exists")
        
        node = Node(
            id=node_id,
            type=type,
            handler=handler,
            async_handler=async_handler,
            config=config or {},
            timeout=timeout,
            retries=retries,
            parallel=parallel,
            pause_before=pause_before,
            pause_after=pause_after,
            metadata=metadata,
        )
        self.nodes[node_id] = node
        
        if self.entry_point is None:
            self.entry_point = node_id
        return self

    def add_edge(
        self,
        source: str,
        target: str,
        condition: Optional[str] = None,
        condition_fn: Optional[Callable[[Dict], bool]] = None,
        weight: int = 1,
    ) -> "StateGraph":
        """添加边（支持条件路由）"""
        if source not in self.nodes:
            raise ValueError(f"Source node {source} not found")
        if target not in self.nodes:
            raise ValueError(f"Target node {target} not found")
        
        edge = Edge(
            source=source,
            target=target,
            condition=condition,
            condition_fn=condition_fn,
            weight=weight,
        )
        self.edges.append(edge)
        return self

    def add_conditional_edge(
        self,
        source: str,
        targets: Dict[str, str],  # condition_name -> target_node
        default: Optional[str] = None,
    ) -> "StateGraph":
        """添加条件分支边（简化语法）"""
        # 实际条件评估在运行时通过 state 决定
        for cond_name, target in targets.items():
            self.add_edge(source, target, condition=cond_name)
        if default:
            self.add_edge(source, default)  # 无条件匹配时走默认
        return self

    def set_entry(self, node_id: str) -> "StateGraph":
        if node_id not in self.nodes:
            raise ValueError(f"Entry node {node_id} not found")
        self.entry_point = node_id
        return self

    def to_dict(self) -> Dict:
        """序列化为可存储的字典"""
        return {
            "nodes": {
                nid: {
                    "id": n.id,
                    "type": n.type,
                    "config": n.config,
                    "timeout": n.timeout,
                    "retries": n.retries,
                    "parallel": n.parallel,
                    "pause_before": n.pause_before,
                    "pause_after": n.pause_after,
                    "metadata": n.metadata,
                }
                for nid, n in self.nodes.items()
            },
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "condition": e.condition,
                    "weight": e.weight,
                }
                for e in self.edges
            ],
            "entry_point": self.entry_point,
            "config": self.config,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Dict) -> "StateGraph":
        """反序列化"""
        graph = cls()
        graph.entry_point = data.get("entry_point")
        graph.config = data.get("config", {})
        graph.metadata = data.get("metadata", {})
        
        for nid, ndata in data.get("nodes", {}).items():
            graph.nodes[nid] = Node(
                id=ndata["id"],
                type=ndata.get("type", "generic"),
                config=ndata.get("config", {}),
                timeout=ndata.get("timeout", 300.0),
                retries=ndata.get("retries", 0),
                parallel=ndata.get("parallel", False),
                pause_before=ndata.get("pause_before", False),
                pause_after=ndata.get("pause_after", False),
                metadata=ndata.get("metadata", {}),
                # handler 需要在运行时注入
            )
        
        for edata in data.get("edges", []):
            graph.edges.append(Edge(
                source=edata["source"],
                target=edata["target"],
                condition=edata.get("condition"),
                weight=edata.get("weight", 1),
            ))
        
        return graph

    @classmethod
    def from_json(cls, json_str: str) -> "StateGraph":
        return cls.from_dict(json.loads(json_str))

    def compile(self, checkpointer=None, state_schema: Optional[Dict] = None) -> "CompiledGraph":
        """编译为可执行图"""
        return CompiledGraph(self, checkpointer=checkpointer, state_schema=state_schema)


class CompiledGraph:
    """
    编译后的可执行图
    - 拓扑排序分层（识别可并行节点组）
    - 运行时状态管理
    - 检查点集成
    """
    
    def __init__(
        self,
        graph: StateGraph,
        checkpointer=None,
        state_schema: Optional[Dict] = None,
    ):
        self.graph = graph
        self.checkpointer = checkpointer
        self.state_schema = state_schema
        self._layers: List[List[str]] = []
        self._adj: Dict[str, List[Edge]] = defaultdict(list)
        self._reverse_adj: Dict[str, List[Edge]] = defaultdict(list)
        self._compile()

    def _compile(self):
        """编译：拓扑排序 + 分层识别并行组"""
        # 构建邻接表（仅无条件边用于拓扑排序）
        for edge in self.graph.edges:
            # 只有无条件边参与拓扑排序，条件边运行时动态评估
            if not edge.condition and not edge.condition_fn:
                self._adj[edge.source].append(edge)
                self._reverse_adj[edge.target].append(edge)
        
        # Kahn 算法分层
        in_degree = defaultdict(int)
        for edge in self.graph.edges:
            if not edge.condition and not edge.condition_fn:
                in_degree[edge.target] += 1
        
        # 初始层：入度为 0 的节点
        current_layer = [nid for nid in self.graph.nodes if in_degree[nid] == 0]
        if not current_layer and self.graph.entry_point:
            current_layer = [self.graph.entry_point]
        
        while current_layer:
            self._layers.append(current_layer)
            next_layer_candidates = set()
            for nid in current_layer:
                for edge in self._adj[nid]:
                    in_degree[edge.target] -= 1
                    if in_degree[edge.target] == 0:
                        next_layer_candidates.add(edge.target)
            current_layer = list(next_layer_candidates)
        
        # 检查所有节点是否被覆盖
        covered = {nid for layer in self._layers for nid in layer}
        missing = set(self.graph.nodes.keys()) - covered
        if missing:
            # 条件边可能导致某些节点入度非零，作为兜底加入最后一层
            for nid in missing:
                self._layers.append([nid])
            logger.warning(f"Graph has conditionally reachable nodes added to extra layer: {missing}")

    def get_layers(self) -> List[List[str]]:
        """获取执行层（每层内节点可并行）"""
        return self._layers

    def get_node(self, node_id: str) -> Optional[Node]:
        return self.graph.nodes.get(node_id)

    def get_outgoing_edges(self, node_id: str) -> List[Edge]:
        return self._adj.get(node_id, [])

    def to_dict(self) -> Dict:
        return {
            "layers": self._layers,
            "nodes": {nid: n.id for nid, n in self.graph.nodes.items()},
            "edges": [{"source": e.source, "target": e.target, "condition": e.condition} for e in self.graph.edges],
        }


class GraphExecutor:
    """
    图执行器：运行编译后的图
    - 支持检查点、暂停/恢复、条件路由
    - 并行执行同层节点
    """
    
    def __init__(
        self,
        compiled: CompiledGraph,
        checkpointer=None,
        max_workers: int = 4,
        rate_limiter=None,
    ):
        self.compiled = compiled
        self.checkpointer = checkpointer
        self.max_workers = max_workers
        self.rate_limiter = rate_limiter
        self._handlers: Dict[str, Callable] = {}

    def register_handler(self, node_type: str, handler: Callable):
        """注册节点类型处理器"""
        self._handlers[node_type] = handler

    def register_node_handler(self, node_id: str, handler: Callable):
        """注册特定节点的处理器（覆盖类型默认）"""
        node = self.compiled.get_node(node_id)
        if node:
            node.handler = handler

    def invoke(
        self,
        initial_state: Dict[str, Any],
        thread_id: Optional[str] = None,
        pause_nodes: Optional[Set[str]] = None,
        human_input: Optional[Dict] = None,
        resume_from_checkpoint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行图
        Returns: {"status": "completed"|"paused"|"failed", "state": ..., "thread_id": ..., "paused_at": ...}
        """
        thread_id = thread_id or f"thread-{uuid.uuid4().hex[:8]}"
        pause_nodes = pause_nodes or set()
        
        # 1. 确定初始状态和起始层
        if resume_from_checkpoint and self.checkpointer:
            cp = self.checkpointer.get(thread_id)
            if cp:
                state = cp.state
                start_layer_idx = cp.metadata.get("layer_idx", 0) + 1
            else:
                state = initial_state
                start_layer_idx = 0
        elif human_input:
            # 从暂停恢复
            if self.checkpointer:
                cp = self.checkpointer.get(thread_id)
                if cp:
                    state = {**cp.state, "human_input": human_input}
                    start_layer_idx = cp.metadata.get("layer_idx", 0)
                else:
                    state = {**initial_state, "human_input": human_input}
                    start_layer_idx = 0
            else:
                state = {**initial_state, "human_input": human_input}
                start_layer_idx = 0
        else:
            state = initial_state
            start_layer_idx = 0
        
        # 2. 逐层执行
        layers = self.compiled.get_layers()
        
        for layer_idx in range(start_layer_idx, len(layers)):
            layer = layers[layer_idx]
            
            # 检查是否有暂停节点
            pause_in_layer = [nid for nid in layer if nid in pause_nodes]
            if pause_in_layer and not human_input:
                # 需要暂停：保存检查点
                if self.checkpointer:
                    cp = Checkpoint(
                        thread_id=thread_id,
                        step_idx=layer_idx,
                        state=state,
                        metadata={"layer_idx": layer_idx, "paused_at": pause_in_layer[0], "awaiting_human": True},
                    )
                    self.checkpointer.put(cp)
                
                return {
                    "status": "paused",
                    "thread_id": thread_id,
                    "layer_idx": layer_idx,
                    "paused_at": pause_in_layer[0],
                    "state": state,
                    "message": f"执行暂停于层 {layer_idx}，节点 {pause_in_layer}，等待人工审批",
                }
            
            # 执行当前层（并行）
            layer_state = self._execute_layer(layer, state, thread_id, layer_idx)
            state.update(layer_state)
            
            # 保存检查点
            if self.checkpointer:
                cp = Checkpoint(
                    thread_id=thread_id,
                    step_idx=layer_idx,
                    state=state,
                    metadata={"layer_idx": layer_idx, "completed_layer": True},
                )
                self.checkpointer.put(cp)
        
        # 全部完成
        return {
            "status": "completed",
            "thread_id": thread_id,
            "final_state": state,
            "layers_executed": len(layers),
        }

    def resume(self, thread_id: str, human_input: Dict) -> Dict[str, Any]:
        """从暂停恢复"""
        return self.invoke({}, thread_id=thread_id, human_input=human_input)

    def _execute_layer(self, layer: List[str], state: Dict, thread_id: str, layer_idx: int) -> Dict:
        """执行单层（并行）"""
        # 分离可并行和串行节点
        parallel_nodes = []
        serial_nodes = []
        
        for nid in layer:
            node = self.compiled.get_node(nid)
            if node and node.parallel:
                parallel_nodes.append(nid)
            else:
                serial_nodes.append(nid)
        
        layer_updates = {}
        
        # 1. 并行执行 parallel=True 节点
        if parallel_nodes:
            updates = self._execute_parallel(parallel_nodes, state, thread_id)
            layer_updates.update(updates)
            state.update(updates)
        
        # 2. 串行执行其余节点
        for nid in serial_nodes:
            update = self._execute_node(nid, state, thread_id)
            layer_updates.update(update)
            state.update(update)
        
        return layer_updates

    def _execute_parallel(self, node_ids: List[str], state: Dict, thread_id: str) -> Dict:
        """并行执行节点组"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from .parallel import TokenBucketRateLimiter
        
        results = {}
        lock = threading.Lock()
        
        def exec_one(nid: str) -> tuple[str, Dict]:
            return nid, self._execute_node(nid, state, thread_id)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(exec_one, nid): nid for nid in node_ids}
            for future in as_completed(futures):
                nid, update = future.result()
                with lock:
                    results[nid] = update
        
        # 合并所有更新
        merged = {}
        for update in results.values():
            merged.update(update)
        return merged

    def _execute_node(self, node_id: str, state: Dict, thread_id: str) -> Dict:
        """执行单个节点"""
        node = self.compiled.get_node(node_id)
        if not node:
            return {}
        
        # 执行前暂停检查（已在层级处理）
        
        # 获取处理器
        handler = node.handler or self._handlers.get(node.type)
        if not handler:
            logger.warning(f"No handler for node {node_id} (type={node.type})")
            return {}
        
        # 执行（带重试）
        last_error = None
        for attempt in range(node.retries + 1):
            try:
                if self.rate_limiter:
                    self.rate_limiter.acquire()
                
                # 创建节点专用状态视图
                node_state = {**state, **node.config}
                
                # 执行
                start = time.monotonic()
                if node.async_handler:
                    # 异步处理器需在事件循环中运行
                    import asyncio
                    output = asyncio.run(node.async_handler(node_state))
                else:
                    output = handler(node_state)
                duration = time.monotonic() - start
                
                # 输出验证
                if not isinstance(output, dict):
                    logger.warning(f"Node {node_id} returned non-dict: {type(output)}")
                    output = {}
                
                return output
                
            except Exception as e:
                last_error = e
                logger.warning(f"Node {node_id} attempt {attempt+1} failed: {e}")
                if attempt < node.retries:
                    time.sleep(2 ** attempt)  # 指数退避
        
        # 全部重试失败
        logger.error(f"Node {node_id} failed after {node.retries + 1} attempts: {last_error}")
        return {"_node_error": f"{node_id}: {last_error}"}


# ============================================================
# 便捷构建器
# ============================================================

def create_research_graph(
    research_handler: Callable,
    write_handler: Callable,
    review_handler: Callable,
    checkpointer=None,
) -> tuple[CompiledGraph, GraphExecutor]:
    """创建标准的 研究-写作-审核 图"""
    graph = StateGraph()
    
    graph.add_node("research", research_handler, type="llm", parallel=True, timeout=120)
    graph.add_node("write", write_handler, type="llm", parallel=True, timeout=120)
    graph.add_node("review", review_handler, type="human", pause_before=True)
    
    graph.add_edge("research", "write")
    graph.add_edge("write", "review")
    graph.add_edge("review", "write", condition="revision_needed")
    
    graph.set_entry("research")
    
    compiled = graph.compile()
    executor = GraphExecutor(compiled, checkpointer=checkpointer)
    return compiled, executor


def create_parallel_domains_graph(
    domains: List[str],
    generate_fn: Callable[[str], Dict],
    merge_handler: Callable,
    checkpointer=None,
) -> tuple[CompiledGraph, GraphExecutor]:
    """创建多域并行生成 + 合并的图"""
    sg = StateGraph()
    
    # 为每个域添加并行节点
    for domain in domains:
        sg.add_node(
            f"gen_{domain}",
            lambda state, d=domain: generate_fn(d),
            type="llm",
            parallel=True,
            config={"domain": domain},
        )
    
    # 合并节点
    sg.add_node("merge", merge_handler, type="generic", pause_before=False)
    
    # 连接：所有生成节点 -> 合并
    for domain in domains:
        sg.add_edge(f"gen_{domain}", "merge")
    
    sg.set_entry(f"gen_{domains[0]}")
    
    compiled = sg.compile()
    executor = GraphExecutor(compiled, checkpointer=checkpointer)
    return compiled, executor


# ============================================================
# 示例
# ============================================================

if __name__ == "__main__":
    # 示例：创建并编译图
    def research(state):
        return {"notes": f"Research on {state['topic']}", "sources": ["src1", "src2"]}
    
    def write(state):
        return {"draft": f"Draft based on {state.get('notes')}"}
    
    def review(state):
        human = state.get("human_input", {})
        if human.get("approved"):
            return {"final": state.get("draft")}
        return {"revision_needed": True, "feedback": human.get("feedback")}
    
    compiled, executor = create_research_graph(research, write, review)
    
    print("Graph layers:", compiled.get_layers())
    print("Nodes:", list(compiled.graph.nodes.keys()))
    print("Edges:", [(e.source, e.target, e.condition) for e in compiled.graph.edges])
    
    # 序列化
    print("\nSerialized:")
    print(compiled.graph.to_json())