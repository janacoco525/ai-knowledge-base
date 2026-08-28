# AI 辅助功能契约（T42）

> **Owner**：本文档 | **更新日期**：2026-08-12
> **背景**：前端 3 个调用点原指向不存在路由（404）。2026-08-12 实现后端并补齐契约；实现位于 `app/rag_app/routes/llm_ops.py`（与 `/api/gemini/*` 旧端点共用核心逻辑）。

## 1. POST /api/ai/summarize — 文档结构化摘要

- 消费方：`frontend/src/components/DocEditor.tsx`（handleAISummarize）
- 请求：`{"title": string, "content": string}`（content ≤ 100_000）
- 响应：`{"summary": string}`（【核心主题】/【要点】/【结论/启示】结构化摘要；LLM 失败降级返回提示语）
- 缓存：按 title+content 前 4 万字 hash 落 `llm_cache`

## 2. POST /api/ai/generate-cards — 记忆闪卡提炼

- 消费方：`frontend/src/components/KnowledgeCards.tsx`（handleTriggerAICardGeneration）
- 请求：`{"docId": string, "title": string, "content": string}`
- 响应：`{"cards": [{"docId": string, "front": string, "back": string, "tags": string[]}]}`（3~8 张，LLM 输出经 JSON 容错解析，失败返回空数组）

## 3. POST /api/ai/define-term — 上下文术语定义

- 消费方：`frontend/src/App.tsx`（查词）
- 请求：`{"term": string, "context": string}`（term ≤ 500，context ≤ 20_000）
- 响应：`{"definition": string}`（≤150 字学术化定义 + 语境作用；失败降级提示）

## 通用规则

- 统一走 `create_llm_client()`（禁止裸调 OpenAI），`eng.llm_client` 未配置 → 503
- 全部结果写入 `eng.kb` LLM 缓存，重复调用不重算（Token 节省，）
- 回归测试：`tests/unit/test_ai_routes.py`（桩 LLM，无网络）
