"""
Core - 通用基础设施包
可跨项目复用的核心机制，无业务耦合

包含：
- checkpointer: 状态持久化与耐久执行
- parallel: 并行执行工具
- stategraph: 显式图编排层
- orchestrator: 编排器-工人模式
- verifier: 确定性验证器
- entropy_audit: 熵减审计框架
- logger: 统一日志工具
"""