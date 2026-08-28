#!/usr/bin/env python3
"""
Orchestrator-Workers — 主管智能体动态分解任务，子智能体并行执行
参考《Graph Engineering》§7 主管-工人模式 + Anthropic《Building Effective Agents》

⚠️ 未接入状态（2026-08-10 审计标记）：本项目生产代码未 import 本模块（仅 core/MODULE_REGISTRY.md、
ARCHITECTURE.md 文档宣传）。业务实际使用的并发/编排来自 core/parallel.py、core/verifier.py、
core/checkpointer.py。如需启用须先补齐业务接线与测试，否则勿修改/勿依赖。

核心能力：
- Orchestrator: 接收目标 -> 规划分解 -> 生成子任务图 -> 分派 Workers
- Workers: 接收子任务 -> 执行 -> 返回结构化结果
- 动态分解: 根据任务复杂度自动决定分解粒度
- 并行执行: Workers 并行工作，Orchestrator 聚合结果
- 迭代优化: 支持多轮规划-执行-评审循环
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from enum import Enum

from .stategraph import StateGraph, CompiledGraph, GraphExecutor, Node, Edge
from .checkpointer import BaseCheckpointer, InMemoryCheckpointer
from .parallel import parallel_map, FanOutFanIn, TokenBucketRateLimiter

logger = logging.getLogger("ai_kb.orchestrator")


class TaskComplexity(Enum):
    SIMPLE = "simple"      # 单步骤，直接执行
    MODERATE = "moderate"  # 2-4 步骤，固定流程
    COMPLEX = "complex"    # 5+ 步骤，需动态规划
    OPEN_ENDED = "open"    # 目标模糊，需探索式分解


@dataclass
class SubTask:
    """子任务定义"""
    id: str
    description: str
    assigned_worker: str  # worker type
    dependencies: List[str] = field(default_factory=list)  # 依赖的前置子任务 ID
    input_data: Dict = field(default_factory=dict)
    output_schema: Optional[Dict] = None  # 期望输出结构
    priority: int = 0
    timeout: float = 300.0
    retries: int = 1


@dataclass
class WorkerSpec:
    """Worker 规格"""
    type: str  # "research", "code", "analysis", "writing", "verification", "generic"
    capabilities: List[str] = field(default_factory=list)
    handler: Optional[Callable] = None
    config: Dict = field(default_factory=dict)


@dataclass
class OrchestrationPlan:
    """编排计划"""
    goal: str
    complexity: TaskComplexity
    subtasks: List[SubTask] = field(default_factory=list)
    workers: Dict[str, WorkerSpec] = field(default_factory=dict)
    estimated_steps: int = 0


class Orchestrator:
    """
    主管智能体：负责目标分解、任务规划、Worker 调度、结果聚合
    
    用法：
        orchestrator = Orchestrator(llm_client, workers)
        result = orchestrator.execute("为 AI 知识库设计 Graph Engineering 落地方案")
    """
    
    def __init__(
        self,
        llm_client: Any,
        workers: Dict[str, WorkerSpec],
        checkpointer: Optional[BaseCheckpointer] = None,
        max_workers: int = 4,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ):
        self.llm = llm_client
        self.workers = workers
        self.checkpointer = checkpointer
        self.max_workers = max_workers
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(rate=5.0, burst=3)
        self.execution_history: List[Dict] = []
    
    def execute(
        self,
        goal: str,
        context: Optional[Dict] = None,
        thread_id: Optional[str] = None,
        max_iterations: int = 3,
    ) -> Dict[str, Any]:
        """
        执行编排循环：规划 -> 分派 -> 执行 -> 评审 -> （可选）重规划
        """
        thread_id = thread_id or f"orch-{uuid.uuid4().hex[:8]}"
        context = context or {}
        
        # 1. 初始规划
        plan = self._plan(goal, context)
        logger.info(f"Orchestrator [{thread_id}] 规划完成: {len(plan.subtasks)} 子任务, 复杂度 {plan.complexity.value}")
        
        # 2. 迭代执行
        for iteration in range(max_iterations):
            logger.info(f"Orchestrator [{thread_id}] 第 {iteration+1}/{max_iterations} 轮执行")
            
            # 执行当前计划
            result = self._execute_plan(plan, thread_id, iteration)
            
            # 3. 评审结果
            review = self._review(goal, plan, result, context)
            
            if review.get("satisfied", False):
                logger.info(f"Orchestrator [{thread_id}] 目标达成，在第 {iteration+1} 轮完成")
                return {
                    "status": "completed",
                    "goal": goal,
                    "iterations": iteration + 1,
                    "final_result": result,
                    "review": review,
                    "plan": plan,
                }
            
            # 4. 重规划
            if iteration < max_iterations - 1:
                feedback = review.get("feedback", "")
                plan = self._replan(goal, plan, result, feedback, context)
                logger.info(f"Orchestrator [{thread_id}] 重规划: {len(plan.subtasks)} 子任务")
        
        return {
            "status": "max_iterations_reached",
            "goal": goal,
            "iterations": max_iterations,
            "final_result": result,
            "review": review,
            "plan": plan,
        }
    
    def _plan(self, goal: str, context: Dict) -> OrchestrationPlan:
        """生成初始执行计划"""
        # 1. 评估复杂度
        complexity = self._assess_complexity(goal, context)
        
        # 2. 生成子任务分解
        subtasks = self._decompose(goal, complexity, context)
        
        # 3. 分配 Worker
        workers = self._assign_workers(subtasks)
        
        return OrchestrationPlan(
            goal=goal,
            complexity=complexity,
            subtasks=subtasks,
            workers=workers,
            estimated_steps=len(subtasks),
        )
    
    def _assess_complexity(self, goal: str, context: Dict) -> TaskComplexity:
        """评估任务复杂度"""
        # 简单启发式：基于目标长度、关键词、上下文
        prompt = f"""评估以下目标的复杂度，只返回: simple/moderate/complex/open

目标: {goal}
上下文: {json.dumps(context, ensure_ascii=False)[:500]}

判断标准:
- simple: 单一明确动作，如"查询文档"、"生成摘要"
- moderate: 2-4 个明确步骤，如"研究对比并生成报告"
- complex: 5+ 步骤，需协调多个专业领域，如"设计完整系统架构"
- open: 目标模糊，需探索，如"改进用户体验"
"""
        try:
            if self.rate_limiter:
                self.rate_limiter.acquire()
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=20,
            )
            result = (resp.choices[0].message.content or "").strip().lower()
            for c in TaskComplexity:
                if c.value in result:
                    return c
        except Exception as e:
            logger.warning(f"Complexity assessment failed: {e}")
        return TaskComplexity.MODERATE
    
    def _decompose(self, goal: str, complexity: TaskComplexity, context: Dict) -> List[SubTask]:
        """将目标分解为子任务"""
        num_steps = {
            TaskComplexity.SIMPLE: 1,
            TaskComplexity.MODERATE: 3,
            TaskComplexity.COMPLEX: 6,
            TaskComplexity.OPEN_ENDED: 8,
        }[complexity]
        
        prompt = f"""将目标分解为 {num_steps} 个具体子任务，返回 JSON 列表：

目标: {goal}
复杂度: {complexity.value}
上下文: {json.dumps(context, ensure_ascii=False)[:500]}

每个子任务格式：
{{
  "description": "具体做什么",
  "worker_type": "research|code|analysis|writing|verification|generic",
  "dependencies": ["前置任务ID列表"],
  "output_schema": {{"key": "type描述"}},
  "priority": 0-10,
  "timeout": 300
}}

要求：
1. 任务间依赖关系清晰（DAG）
2. 优先级：关键路径高
2. 输出可验证
"""
        try:
            if self.rate_limiter:
                self.rate_limiter.acquire()
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or ""
            # 提取 JSON
            import re
            m = re.search(r"\[[\s\S]*\]", raw)
            if m:
                tasks_data = json.loads(m.group())
                subtasks = []
                for i, td in enumerate(tasks_data):
                    subtasks.append(SubTask(
                        id=f"task-{i}",
                        description=td.get("description", ""),
                        assigned_worker=td.get("worker_type", "generic"),
                        dependencies=td.get("dependencies", []),
                        input_data={},
                        output_schema=td.get("output_schema"),
                        priority=td.get("priority", 0),
                        timeout=td.get("timeout", 300),
                        retries=td.get("retries", 1),
                    ))
                return subtasks
        except Exception as e:
            logger.warning(f"Decomposition failed: {e}")
        
        # Fallback: 简单线性分解
        return [
            SubTask(
                id=f"task-{i}",
                description=f"步骤 {i+1}: {goal}",
                assigned_worker="generic",
                dependencies=[f"task-{i-1}"] if i > 0 else [],
            )
            for i in range(num_steps)
        ]
    
    def _assign_workers(self, subtasks: List[SubTask]) -> Dict[str, WorkerSpec]:
        """为子任务分配可用 Worker"""
        workers = {}
        for task in subtasks:
            wtype = task.assigned_worker
            if wtype not in workers and wtype in self.workers:
                workers[wtype] = self.workers[wtype]
            elif wtype not in workers:
                # 创建默认 Worker
                workers[wtype] = WorkerSpec(type=wtype)
        return workers
    
    def _execute_plan(self, plan: OrchestrationPlan, thread_id: str, iteration: int) -> Dict[str, Any]:
        """执行编排计划（拓扑顺序 + 并行）"""
        # 构建 StateGraph
        graph = StateGraph()
        
        # 添加子任务节点
        for task in plan.subtasks:
            worker_spec = plan.workers.get(task.assigned_worker)
            handler = self._make_task_handler(task, worker_spec)
            
            graph.add_node(
                task.id,
                handler,
                type=task.assigned_worker,
                parallel=len([t for t in plan.subtasks if not t.dependencies]) > 1,
                timeout=task.timeout,
                retries=task.retries,
                config={"subtask": task.__dict__},
            )
        
        # 添加依赖边
        for task in plan.subtasks:
            for dep in task.dependencies:
                graph.add_edge(dep, task.id)
        
        # 入口点：无依赖的任务
        entry_tasks = [t.id for t in plan.subtasks if not t.dependencies]
        if entry_tasks:
            graph.set_entry(entry_tasks[0])
        
        # 编译并执行
        compiled = graph.compile(checkpointer=self.checkpointer)
        executor = GraphExecutor(compiled, checkpointer=self.checkpointer)
        
        # 注册默认 handler
        for wtype, wspec in plan.workers.items():
            if wspec.handler:
                executor.register_handler(wtype, wspec.handler)
        
        result = executor.invoke({}, thread_id=f"{thread_id}-iter{iteration}")
        
        # 整理结果
        final_state = result.get("final_state") or result.get("state") or {}
        task_results = {k: v for k, v in final_state.items() if k.startswith("task-")}
        
        return {
            "status": result["status"],
            "task_results": task_results,
            "final_state": final_state,
        }
    
    def _make_task_handler(self, task: SubTask, worker_spec: Optional[WorkerSpec]) -> Callable:
        """创建子任务处理器"""
        def handler(state: Dict) -> Dict:
            # 构建任务输入
            task_input = {
                "goal": task.description,
                "context": state,
                "input_data": task.input_data,
                "output_schema": task.output_schema,
            }
            
            # 使用 Worker handler 或 LLM 直接执行
            if worker_spec and worker_spec.handler:
                return worker_spec.handler(task_input)
            
            # 默认：用 LLM 执行
            return self._execute_with_llm(task, state)
        
        return handler
    
    def _execute_with_llm(self, task: SubTask, state: Dict) -> Dict:
        """用 LLM 执行通用任务"""
        prompt = f"""执行以下子任务：

任务: {task.description}
上下文: {json.dumps(state, ensure_ascii=False)[:1000]}
期望输出: {json.dumps(task.output_schema, ensure_ascii=False) if task.output_schema else "自由格式"}

请直接返回 JSON 格式的结果。
"""
        try:
            if self.rate_limiter:
                self.rate_limiter.acquire()
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or ""
            # 尝试解析 JSON
            import re
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                return json.loads(m.group())
        except Exception as e:
            logger.warning(f"LLM task execution failed: {e}")
        return {"_error": "执行失败", "raw": raw}
    
    def _review(self, goal: str, plan: OrchestrationPlan, result: Dict, context: Dict) -> Dict:
        """评审执行结果"""
        prompt = f"""评审执行结果是否满足目标：

原始目标: {goal}
执行结果: {json.dumps(result, ensure_ascii=False)[:2000]}

判断标准：
1. 核心目标是否达成
2. 关键交付物是否完整
3. 是否有明显遗漏或错误

返回 JSON：
{{
  "satisfied": true/false,
  "score": 0-100,
  "feedback": "具体反馈，如有不满意请具体说明缺口",
  "missing": ["缺失项列表"],
  "suggestions": ["改进建议"]
}}
"""
        try:
            if self.rate_limiter:
                self.rate_limiter.acquire()
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1000,
            )
            raw = resp.choices[0].message.content or ""
            import re
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                return json.loads(m.group())
        except Exception as e:
            logger.warning(f"Review failed: {e}")
        return {"satisfied": False, "score": 0, "feedback": "评审失败", "missing": [], "suggestions": []}
    
    def _replan(self, goal: str, old_plan: OrchestrationPlan, result: Dict, feedback: str, context: Dict) -> OrchestrationPlan:
        """基于反馈重新规划"""
        prompt = f"""基于执行反馈重新规划：

原目标: {goal}
上一轮计划: {len(old_plan.subtasks)} 个子任务
执行结果摘要: {json.dumps({k: str(v)[:200] for k, v in result.get("task_results", {}).items()}, ensure_ascii=False)}
评审反馈: {feedback}

请生成改进后的子任务列表（JSON），重点解决反馈中提到的缺口。
保持相同 JSON 格式。
"""
        try:
            if self.rate_limiter:
                self.rate_limiter.acquire()
            resp = self.llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or ""
            import re
            m = re.search(r"\[[\s\S]*\]", raw)
            if m:
                tasks_data = json.loads(m.group())
                subtasks = []
                for i, td in enumerate(tasks_data):
                    subtasks.append(SubTask(
                        id=f"task-{i}-v2",
                        description=td.get("description", ""),
                        assigned_worker=td.get("worker_type", "generic"),
                        dependencies=td.get("dependencies", []),
                        input_data={},
                        output_schema=td.get("output_schema"),
                        priority=td.get("priority", 0),
                        timeout=td.get("timeout", 300),
                        retries=td.get("retries", 1),
                    ))
                workers = self._assign_workers(subtasks)
                return OrchestrationPlan(
                    goal=goal,
                    complexity=old_plan.complexity,
                    subtasks=subtasks,
                    workers=workers,
                    estimated_steps=len(subtasks),
                )
        except Exception as e:
            logger.warning(f"Replan failed: {e}")
        return old_plan


# ============================================================
# 预定义 Worker Specs
# ============================================================

def create_default_workers(llm_client: Any) -> Dict[str, WorkerSpec]:
    """创建默认 Worker 规格"""
    return {
        "research": WorkerSpec(
            type="research",
            capabilities=["search", "summarize", "fact_check", "source_verification"],
            config={"model": "deepseek-chat", "temperature": 0.2},
        ),
        "code": WorkerSpec(
            type="code",
            capabilities=["generate", "refactor", "debug", "test", "review"],
            config={"model": "deepseek-chat", "temperature": 0.1},
        ),
        "analysis": WorkerSpec(
            type="analysis",
            capabilities=["compare", "evaluate", "model", "statistics", "pattern_recognition"],
            config={"model": "deepseek-chat", "temperature": 0.2},
        ),
        "writing": WorkerSpec(
            type="writing",
            capabilities=["draft", "edit", "summarize", "translate", "format"],
            config={"model": "deepseek-chat", "temperature": 0.4},
        ),
        "verification": WorkerSpec(
            type="verification",
            capabilities=["test", "validate", "audit", "check_consistency", "regression_check"],
            config={"model": "deepseek-chat", "temperature": 0.1},
        ),
        "generic": WorkerSpec(
            type="generic",
            capabilities=["general"],
            config={"model": "deepseek-chat", "temperature": 0.3},
        ),
    }


# ============================================================
# 便捷入口
# ============================================================

def create_orchestrator(
    llm_client: Any,
    workers: Optional[Dict[str, WorkerSpec]] = None,
    checkpointer: Optional[BaseCheckpointer] = None,
) -> Orchestrator:
    """创建 Orchestrator 实例"""
    if workers is None:
        workers = create_default_workers(llm_client)
    return Orchestrator(
        llm_client=llm_client,
        workers=workers,
        checkpointer=checkpointer,
    )


# ============================================================
# 示例
# ============================================================

if __name__ == "__main__":
    # 示例：创建 Orchestrator（需真实 LLM client）
    from llm_client_factory import create_llm_client
    
    llm = create_llm_client()
    workers = create_default_workers(llm)
    orchestrator = create_orchestrator(llm, workers)
    
    # 执行（需真实 LLM 环境）
    # result = orchestrator.execute("为 AI 知识库设计 Graph Engineering 落地方案")
    # print(json.dumps(result, ensure_ascii=False, indent=2))
    
    print("Orchestrator module loaded. Ready to use.")