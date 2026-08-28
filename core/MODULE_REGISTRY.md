# Core 模块注册表

> 自动生成的模块索引，用于跨项目复用时快速查找

| 模块 | 文件 | 核心类/函数 | 用途 | 依赖 |
|-----|------|-------------|------|------|
| **checkpointer** | `checkpointer.py` | `SqliteCheckpointer`, `InMemoryCheckpointer`, `GraphRunner` | 状态持久化、暂停/恢复、时间旅行、分叉 | `sqlite3`, `threading` |
| **parallel** | `parallel.py` | `parallel_map`, `FanOutFanIn`, `TokenBucketRateLimiter` | 并行映射、扇出扇入、限流 | `concurrent.futures`, `threading` |
| **stategraph** | `stategraph.py` | `StateGraph`, `CompiledGraph`, `GraphExecutor` | 显式图定义、拓扑分层、条件路由、暂停/恢复 | `checkpointer`, `parallel` |
| **orchestrator** | `orchestrator.py` | `Orchestrator`, `create_orchestrator`, `WorkerSpec` | 目标分解→动态规划→Worker分派→评审→重规划 | `stategraph`, `checkpointer`, `parallel` |
| **verifier** | `verifier.py` | `VerifierManager`, `GraphQualityVerifier`, `CodeVerifier`, `SummaryVerifier` | 图谱质量、代码单测、摘要一致性自动评分 | `ast`, `subprocess` |
| **logger** | `logger.py` | `get_logger` | 统一日志格式 | `logging` |
| **entropy_audit** | `entropy_audit.py` | `EntropyAuditBase`, `FileSystemAuditBase` | 通用熵减审计基类 | `pathlib`, `json` |

---

## 使用示例

### 新项目快速接入

```python
# 1. 复制 core/ 到项目
# 2. 安装依赖
pip install openai pydantic fastapi uvicorn python-dotenv

# 3. 创建项目专属审计
from core.entropy_audit import FileSystemAuditBase

class MyProjectAudit(FileSystemAuditBase):
    def _register_checks(self):
        self.add_check("E1", "版本一致性", self._check_version)
        self.add_check("E2", "依赖漂移", self._check_deps)

# 4. 使用核心模块
from core.parallel import parallel_map, FanOutFanIn
from core.stategraph import StateGraph, GraphExecutor
from core.orchestrator import create_orchestrator, create_default_workers
from core.verifier import VerifierManager
from core.checkpointer import SqliteCheckpointer
```

---

## 模块依赖图

```
core/
├── logger.py          (无依赖，最底层)
├── entropy_audit.py   (依赖 pathlib, json)
├── checkpointer.py    (依赖 sqlite3, threading)
├── parallel.py        (依赖 concurrent.futures, threading)
├── stategraph.py      (依赖 checkpointer, parallel)
├── verifier.py        (依赖 ast, subprocess)
└── orchestrator.py    (依赖 stategraph, checkpointer, parallel)
```

---

## 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-07-31 | 初始版本：从 AI知识库 项目提取通用核心 |

---

> 注：此文档由重构脚本自动生成，请在添加新模块时同步更新