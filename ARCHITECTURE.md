# 项目结构重构总结：通用核心 vs 项目专属

## 目录结构

```
AI知识库/
├── core/                          # 🟢 通用核心包 - 跨项目复用
│   ├── __init__.py
│   ├── checkpointer.py            # 状态持久化/耐久执行
│   ├── parallel.py                # 并行执行工具
│   ├── stategraph.py              # 显式图编排层
│   ├── orchestrator.py            # 编排器-工人模式
│   ├── verifier.py                # 确定性验证器
│   ├── logger.py                  # 统一日志
│   └── entropy_audit.py           # 通用熵减审计基类
│
│   ⛔ 预留库决策（2026-08-12）：orchestrator / stategraph / checkpointer 属跨项目可复用备件；
│   当前生产代码仅 checkpointer（图谱耐久执行）在用，orchestrator / stategraph 由测试保护但零生产引用。
│   E2 熵减审计持续提示零引用模块；若一年内无新项目复用计划，应将 orchestrator / stategraph
│   移入 scripts/_archive/ 或独立仓库，避免"牛刀"无声积灰。
│
├── app/                           # 🔵 项目专属包 - 仅限 AI知识库
│   ├── __init__.py
│   ├── entropy_audit.py           # 项目专属熵减审计 (E1-E8)
│   └── rag_app/                   # AI知识库核心业务（完整路径 app/rag_app/）
│       ├── __init__.py
│       ├── api_server.py          # FastAPI 入口
│       ├── config.py              # 配置管理
│       ├── scanner.py             # 文件夹扫描
│       ├── routes/                # API 路由
│       ├── knowledge_base.py      # 知识库引擎
│       ├── llm_graph_extractor.py # 图谱提取
│       ├── orchestrator.py        # (已移至 core/)
│       ├── ...                    # 其他业务模块
│
├── start.py                       # 统一启动脚本
├── docs/                          # 文档
├── frontend/                      # React SPA
└── tests/                         # 测试
```

## 通用核心包 使用指南

### 1. 导入方式
```python
from core.checkpointer import InMemoryCheckpointer, SqliteCheckpointer, Checkpoint
from core.parallel import parallel_map, FanOutFanIn, TokenBucketRateLimiter
from core.stategraph import StateGraph, CompiledGraph, GraphExecutor
from core.orchestrator import Orchestrator, create_orchestrator, create_default_workers
from core.verifier import VerifierManager, GraphQualityVerifier, CodeVerifier, SummaryVerifier
from core.entropy_audit import EntropyAuditBase, FileSystemAuditBase
```

### 2. 核心能力

| 模块 | 核心能力 | 适用场景 |
|-----|---------|---------|
| `checkpointer` | 暂停/恢复/时间旅行/分叉/pending writes | 长任务、需人工审批、需回滚 |
| `parallel` | 并行映射、扇出扇入、限流 | 批量处理、多域并行、LLM 并行 |
| `stategraph` | 显式图定义、拓扑分层、条件路由、暂停/恢复 | 复杂工作流、需可视化/审计 |
| `orchestrator` | 目标分解→动态规划→Worker分派→评审→重规划 | 复杂目标、需多轮迭代 |
| `verifier` | 图谱质量、代码单测、摘要一致性 | 需确定性验证的输出 |
| `entropy_audit` | 可扩展审计框架 | 项目级质量门禁 |

### 3. 最小依赖
- Python 3.10+
- 标准库 + `openai` + `pydantic` + `fastapi` + `pydantic` (可选)
- 无业务逻辑耦合

## 项目专属包 使用指南

### 1. 导入方式
```python
from app.rag_app.config import Config
from app.rag_app.scanner import Scanner
from app.rag_app.routes.graph import graph_runner
from app.rag_app.llm_graph_extractor import extract_llm_graph
from app.entropy_audit import run_audit
```

### 2. 启动方式
```bash
# 健康检查
python start.py --health

# 运行测试
python start.py --test

# 启动服务 (prod)
python start.py --mode prod

# 启动服务 (dev, with auto-restart)
python start.py --restart --max-restarts 5
```

## 迁移清单

已完成：
- [x] core/ 通用核心包建立
- [x] app/ 项目专属包建立
- [x] 所有导入路径修正 (core.* 相对导入)
- [x] config.py .env 加载路径修正
- [x] start.py 脚本路径修正
- [x] entropy_audit.py 路径修正
- [x] 所有导入验证通过
- [x] 熵减审计 E1-E8 全部通过
- [x] Pre-commit guard 通过
- [x] 健康检查 17/17 通过

## 新项目接入 core/ 指南

```bash
# 1. 复制 core/ 到新项目
cp -r core/ /path/to/new-project/

# 2. 在新项目中安装依赖
pip install openai pydantic fastapi uvicorn python-dotenv

# 3. 创建项目专属审计类
# new_project/audit.py
from core.entropy_audit import FileSystemAuditBase

class MyProjectAudit(FileSystemAuditBase):
    def _register_checks(self):
        self.add_check("E1", "版本一致性", self._check_version)
        # ...

# 4. 使用 core 模块
from core.parallel import parallel_map
from core.stategraph import StateGraph
from core.orchestrator import create_orchestrator
```

## 架构决策记录

| 决策 | 理由 |
|-----|------|
| core/ 无业务依赖 | 保证跨项目复用，避免循环依赖 |
| app/ 包含所有业务逻辑 | 项目特有逻辑隔离，便于维护 |
| core.entropy_audit 基类 | 提供框架，项目实现具体检查 |
| start.py 在根目录 | 统一入口，不依赖包结构 |
| .env 在项目根目录 | 配置与代码分离，多环境友好 |
