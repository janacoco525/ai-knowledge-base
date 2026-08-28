# AI知识库 — 产品版本管理

> ⛔ **STOP. READ THIS FIRST. DO NOT SKIP.** ⛔  
> 本文档是 AI知识库 产品版本的 **唯一权威来源**。所有贡献者都必须遵守以下规则。

---

## ⚠️ 防漂移屏障

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  规则 1 — 版本号「只升不降」                                   │
│  当前主版本号是 v3.x。不得将版本号改回 v2.x 或更低。        │
│  不得删除历史版本记录。不得将"当前版本"字段改成低于最后一条记录的版本。 │
│                                                             │
│  规则 2 — 改版本必须写理由                                     │
│  每次修改版本号、增加变更记录，必须在本文件末尾追加一条，            │
│  写清楚：日期、改动摘要、动了哪些文件、为什么改。                    │
│                                                             │
│  规则 3 — 本文档优先级高于记忆                                  │
│  如果你的印象中某个版本号与此不同，                   │
│  以本文档为准。本文档是此项目的事实裁判。                          │
│                                                             │
│  规则 4 — 禁止删除历史                                       │
│  历史版本记录只追加不删除。即使是"合并重复条目"也不许删。              │
│                                                             │
│  规则 5 — 读取即约束                                          │
│  读到这些规则即受其约束，                          │
│  不得声称"没看到"或"不理解"。                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 当前版本

| 字段 | 值 |
|------|-----|
| **主版本号** | `v3.x` |
| **当前修订** | `v3.8.1` — 累积修复收口：脑图生成进度接口与轮询 + 联网问答上下文保持 + 知识卡片轮询防卡死 + 图谱分类/语义边增强 + 新机迁移配套 |
| **发布日期** | 2026-08-20 |
| **代号** | 累积修复收口 |

---

## 版本历史

### v3.8.1 (2026-08-20) — 累积修复收口：脑图进度可观察 + 联网问答上下文 + 卡片轮询 + 新机迁移配套

> 背景：新电脑迁移后用户实测暴露三类体验问题（长文档脑图生成 3-5 分钟被 180s 超时掐断误判卡死、联网问答丢对话上下文、知识卡片生成卡死），本轮系统性修复并补齐治理产物。

| 改动 | 涉及文件 |
|------|---------|
| 脑图生成进度可观察：模块级线程安全进度注册表 + `GET /api/gemini/mindmap/progress` 端点 + 前端 2.5s 轮询显示「第 N/M 章」+ 超时 180s→600s + 缓存兜底 | `app/rag_app/routes/llm_ops.py`, `frontend/src/components/GraphPanel.tsx`, `MindMap.tsx` |
| 联网问答上下文保持：`_ddgs_search` 重构，对话历史传入检索 | `app/rag_app/routes/chat_ops.py` |
| 知识卡片生成轮询防卡死 | `frontend/src/components/KnowledgeCards.tsx` |
| 图谱分类与语义边增强 | `app/rag_app/routes/graph.py`, `app/rag_app/llm_graph_extractor.py`, `frontend/src/components/KnowledgeGraph.tsx` |
| 新机迁移配套：依赖清单入库（除僵尸依赖 aiofiles/tqdm + 传递依赖用途注释）+ 迁移脚本 + 新机部署指南 | `requirements.txt`, `app/__init__.py`, `scripts/migrate.ps1`, `scripts/migrate_collection.ps1`, `新机部署指南.md` |
| 验证：pytest **285/285** + 冒烟 **118/118** + health **22/22** + 脑图进度接口生命周期实测（空/未注册/进行中/完成态） | — |

### v3.8.0 (2026-08-06) — 质量大修复：图谱分类/语义边 + 全文翻译 + 超长文档渲染 + 上传0字节根治 + PDF/侧窗修复

> 背景：用户连续反馈多个质量问题（图谱全 concept/边"同句"、上传 0 字节、超长书渲染慢、PDF 模糊被截、侧窗收起打不开），本轮系统修复并沉淀机制。

| 改动 | 涉及文件 |
|------|---------|
| 上传 0 字节根治：`tfidf_svd.pkl` SVD 维度漂移（pickle 无法反序列化临时 tokenizer → 加载失败 → 单文件 fit 10 维 vs 库内 132 维 vstack 崩溃）→ `scripts/fix_tfidf_svd.py` 重建 132 维 SVD + `knowledge_base.py` 维度一致性护栏 | `scripts/fix_tfidf_svd.py`, `app/rag_app/knowledge_base.py` |
| 全文翻译功能（新增）：后端 `/api/translate`（中文跳过 + LRU 缓存），前端 `TranslatePanel` 组件 + 工具栏"翻译"按钮 + 全文翻译弹窗 | `app/rag_app/routes/analysis.py`, `app/rag_app/api_server.py`, `frontend/src/components/TranslatePanel.tsx`, `DocEditor.tsx` |
| 超长文档渲染优化：后端超长跳过 `render_to_html`（前端不用，白耗 3s）；前端分块渲染（每 8 万字符独立 ReactMarkdown + 进度） | `app/rag_app/routes/knowledge.py`, `DocEditor.tsx` |
| 图谱质量重构：接回死代码 `_normalize_llm_graph`（分类映射 + from/to→source/target）；修复 fallback 反向边过滤（保留语义边）；`_infer_category` 增强（地点/人物/事件 + 抽象词排除）；LLM 采样段上限 6000 字 | `app/rag_app/routes/graph.py`, `llm_graph_extractor.py`, `concept_extractor.py` |
| 脑图质量重构：前端传全文（去掉 30000 截断）；后端按真实章节边界切片 | `frontend/src/components/GraphPanel.tsx`, `app/rag_app/routes/llm_ops.py` |
| PDF 修复：DPR 高清适配 + fit 宽高适配整页可见 | `frontend/src/components/PdfViewer.tsx` |
| 侧窗按钮修复：`setShowSidebar(false)` → toggle；工具栏按钮 font-sans 修复汉字空格 | `frontend/src/components/DocEditor.tsx` |
| 验证：pytest 80/80 + health 21/21 + lint/build 通过 | — |

### v3.7.5 (2026-08-02) — 系统性体检修复 + 门禁假失败根治 + 真值追平

> 背景：系统性体检发现 `start.py --test` 门禁在冷启动下假失败（20s 超时 vs 服务实际需 7~30s 就绪），以及 E2 依赖漂移的 2 个 watch 项。

| 改动 | 涉及文件 |
|------|---------|
| 门禁修复：`start.py --test` 临时服务健康等待超时 20s→60s，消除冷启动假失败；冒烟实测 **118/118** | `start.py` |
| 依赖清理：移除 `frontend/package.json` 中从未引用的 `markmap-lib`（MindMap 只依赖 markmap-view，构建产物无引用），同步 lockfile 并 `npm install` 清理 38 个传递依赖 | `frontend/package.json`, `frontend/package-lock.json` |
| 熵审计：E2 豁免 `typescript`（构建/类型依赖，lint 脚本 `tsc --noEmit` 使用），不再误报"建议复核" | `app/entropy_audit.py` |
| 验证：health **21/21** + 冒烟 **118/118** + pytest **80/80** + lint 通过 + E2 全绿 | — |

> 背景：多模型/多工具轮替后出现"文档声明与实测脱节"。本轮按「验证基建 → 真值追平 → 清理 → 复验」四步收口，全部结论为实测。

| 改动 | 涉及文件 |
|------|---------|
| 验证基建：pytest 全量从收集失败修复为 80/80 全绿（tests/ 裸导入 `from routes.*` / `from config` 全部改为 `app.rag_app.*` 包导入；sys.path 统一项目根）；诊断脚本（bench/diag/test_qwen37）从 tests/ 迁出到 scripts/diagnostics/ | `tests/test_refactored_routes.py`, `tests/test_graph_payload_filter.py`, `tests/test_core_logic.py`, `tests/test_shared_engine.py`, `tests/test_scanner_diff.py`, `tests/smoke_test.py`, `scripts/diagnostics/` |
| 冒烟图谱超时根治：冒烟测试 live 图谱用例改 sample 模式（5s 超时 vs TTL 600s 互斥）；重建运行期夹具 `output/graph/graph-data-sample.v1.json`；冒烟实测 **118/118** | `tests/smoke_test.py`, `output/graph/graph-data-sample.v1.json` |
| 路径 bug 根治：`graph.py` PROJECT_ROOT 从 `parents[2]`（指向 app/）改为 `Config.PROJECT_DIR`（样例图/chain 样例/checkpoint/verifier 路径全部错位）；`providers.py`/`domains.py` 5+1 处 `.env` 路径同样从 app/ 修正到根目录；`scripts/setup_frontend.py` `.env` 路径修正 | `app/rag_app/routes/graph.py`, `app/rag_app/routes/providers.py`, `app/rag_app/routes/domains.py`, `scripts/setup_frontend.py` |
| 熵审计升级：E7 双格式解析（兼容 `## NNN:` 与 `## 经验 #NNN：`）+ 编号缺口检测；E9 增加 `--clean`（安全护栏：跳过 git 跟踪文件与运行期夹具，backups 仅提示不判 FAIL），output/logs 老化已清理 | `app/entropy_audit.py`, `output/` |
| 清理：删除 `app/rag_app/config.py.tmp`、`app/rag_app/parallel.py.bak`（git rm）、`scripts/check_pdf.py`、`scripts/debug_fallback.py`、`scripts/parse_resp.py`、`scripts/scan_sizes.py`；`.gitignore` 补 `*.py.bak`；`foundation.manifest.json` 白名单补 `stable-features.json` | 上述文件 + `.gitignore` |
| 验证：pytest **80/80** + 冒烟 **118/118** + health **21/21** + npm lint 通过 + guard 全量通过 | — |

### v3.7.4 (2026-07-31) — core/app 架构迁移闭环 + 真值追平 + 机制熔断

| 改动 | 涉及文件 |
|------|---------|
| 架构闭环：`api_server.py` sys.path 修正为项目根（迁移残留修复）；`scripts/启动.bat` 指向 `app/rag_app/api_server.py` + PYTHONPATH 双保险 | `app/rag_app/api_server.py`, `scripts/启动.bat` |
| 裸导入根治：`core/stategraph.py` 延迟导入改相对导入；api_server 17 处 `from routes.x` → `from app.rag_app.routes.x`；routes 内 7 处同包裸导入全部改包路径；tests/unit 3 个测试导入追平 | `core/stategraph.py`, `app/rag_app/api_server.py`, `app/rag_app/routes/`, `tests/unit/` |
| 根目录清理：删除 6 个调试残留（check_line.txt / debug_out.txt / response.json / test_guard.txt / test_guard_pass.md / qa-index.yaml）；散测试迁入 `tests/` 改造为 pytest 用例 | `tests/` |
| 验证：health 17/17 + entropy E1-E8 PASS + pytest 11/11 + 裸导入扫描 0 残留 | — |


| 改动 | 涉及文件 |
|------|---------|
| UI 修复：Sidebar 全部操作图标从 hover 隐藏改为始终可见 + 删除确认 UI 重做（图标按钮+提示条） | `frontend/src/components/Sidebar.tsx` |
| 启动脚本修复：bat 编码（chcp 65001）+ 纯英文输出 | `scripts/启动.bat` |
| 项目体检：全量文档审计 + 经验事实校验（发现 2 个严重谎报并修复） | — |
| WorkBuddyGuide 报告分析 + 知识精馏 vs RAG 评估 | — |

### v3.7.2 (2026-06-14) — 图谱回归 + 文档真值追平

| 改动 | 涉及文件 |
|------|---------|
| 图谱引擎：`cytoscape.js` 回收为纯 SVG React 组件（辐射式 BFS 分环布局、8色配色、hover 边标签、连线动态流动效果；当前版本节点/连线仅可添加，不可删除） | `frontend/src/components/KnowledgeGraph.tsx`, `frontend/src/components/GraphPanel.tsx` |
| 项目真值追平：图谱当前实现、禁止回退项、产品版本引用统一到 v3.7.2 | `docs/项目真值.md`, `docs/VERSION.md` |

### v3.7.1 (2026-06-12) — 文档治理收口

| 改动 | 涉及文件 |
|------|---------|
| P0修复：provider真值链 + start.py client factory + AI Diff契约路径 + 版本号统一到v3.7.0 + 图谱类型显式化 + 熵审计加固 | `providers.py`, `start.py`, `AI Diff契约.md`, `VERSION.md`, `types.ts`, `entropy_audit.py` |

### v3.7.0 (2026-06-10) — 引擎重构 + 文档治理收口

| 改动 | 涉及文件 |
|------|---------|
| 图谱引擎：vis-network → cytoscape.js（cose力导向，hover邻居高亮，双击连线，右键删除） | `KnowledgeGraph.tsx` |
| 脑图引擎：markmap → D3 tree（左到右树形，teal梯度色，d3-zoom，折叠/展开） | `MindMap.tsx` |
| 数据架构 V4：GraphSession 存完整 nodes+edges，废弃全局池 | `graph.py`, `concept_extractor.py`, localStorage |
| 后端：脑图prompt深度重写 + 图谱双源提取(LLM+jieba规则) + 分段合并去重 | `graph.py`, `concept_extractor.py` |
| 模型配置系统：providers/switch/clear 端点 + /health 增强 + 设置页切换 | `gemini_adapter.py`, `providers.py`, `api_server.py` |
| PDF浏览模式：IndexedDB存原文件 + PdfViewer Canvas按需渲染 | `PdfViewer.tsx`, `fileStorage.ts`, `Sidebar.tsx` |
| 前端Bug修复(12处)：导入/图谱/脑图/问答/编码检测/滚动性能 | 全站 `frontend/src/components/` |
| 构建优化：Vite clean+build，产物 2文件 ~1.2MB（之前95文件72MB） | `frontend/package.json` |
| 旧依赖清理：vis-network/vis-data/@types/vis/markmap-lib/markmap-view 移除 | `frontend/package.json` |
| 项目真值追平：技术栈/图谱/脑图/禁止回退项更新 | `docs/项目真值.md` |

### v3.6 (2026-06-03) — 体验打磨

| 改动 | 涉及文件 |
|------|---------|
| 知识图谱支持单文档选择 + 内容架构/概念图谱双模式 | `knowledge_base.py`, `graph.py`, `concept_extractor.py`, `KnowledgeGraph.tsx` |
| 文档阅读器：字体大小调节 + 护眼背景切换 | `DocEditor.tsx` |
| 知识问答页：文档选择区默认收起 + "收起▲"按钮 | `ChatPanel.tsx`, `App.tsx` |
| 全站 emoji 图标清理（按钮/标题/标签去emoji化） | `DocEditor.tsx`, `ChatPanel.tsx`, `DocComparison.tsx`, `KnowledgeCards.tsx`, `KnowledgeGraph.tsx`, `MindMap.tsx`, `Sidebar.tsx`, `App.tsx` |
| 🔧 死代码回滚：rag_app/web/*.html 误改8文件恢复（两套前端陷阱） | `rag_app/web/*.html` — 已回滚 |
| 🔧 VERSION.md v3.6 文件名修正：html → React 组件名 | `docs/VERSION.md` |

### v3.5 (2026-06-02) — 配置收口

| 改动 | 涉及文件 |
|------|---------|
| DeepSeek API 端点修复 + 模型名更新 | `rag_engine.py`, `.env` |
| 动态模型供应商注册表（6家） | `providers.py` |
| UI 文案去夸大 / 去 Google 品牌 | `App.tsx`, `DocEditor.tsx`, `ChatPanel.tsx`, `DocComparison.tsx` |
| 死代码移除（IDE workspace scan） | `DocComparison.tsx` |
| 小说阅读体验优化（页码剥离+排版） | `parser.py`, `DocEditor.tsx` |

### v3.4 (2026-05-28) — 五帽角色 + 导航精简

| 改动 | 涉及文件 |
|------|---------|
| 导航精简 11→6 项 | `index.html` |
| 首页重构：搜索居中 + 文档列表 + 推荐 | `index.html` |
| 文案去技术化（索引→文档、片段→段落、AI知识库→我的知识库） | 全站 |
| EXE 打包（PyInstaller） | `launcher.py`, `AI知识库.spec`, `package.bat` |

### v3.3 (2026-05-21) — 知识卡片 + 框架树 + 阅读记录

| 改动 | 涉及文件 |
|------|---------|
| 知识卡片功能 | `cards.py`, `cards.html` |
| 知识框架树 | `knowledge_tree.py`, `knowledge-tree.html` |
| 阅读记录 | `reading.py`, `reading-history.html` |
| AI Diff 独立路由 | `diff.py`, `diff.html` |
| 路由解耦（所有路由单例引擎） | 全站路由文件 |

### v3.2 (2026-05-19) — 分析总结 + 域管理

| 改动 | 涉及文件 |
|------|---------|
| 分析总结（摘要/主题/风险） | `analysis.py`, `analysis.html` |
| 跨文件主题提炼 | `analysis.py` |
| 知识域管理（用户自主增删） | `knowledge.py` |

### v3.1 (2026-05-14) — 图谱交互 + 首页重做

| 改动 | 涉及文件 |
|------|---------|
| 图谱拖拽/hover高亮/双击 | `graph.html` |
| 首页重做（快速入口+最近阅读） | `index.html` |
| DELETE 闭环 | 全站路由 |

### v3.0 (2026-05-12) — L2 收口 + 技术债清理

| 改动 | 涉及文件 |
|------|---------|
| S2-01~S2-06 全部功能 | `query.py`, `knowledge.py`, `graph.py`, `analysis.py`, `diff.py`, `knowledge_tree.py`, `reading.py`, `cards.py`, `recommend.py` |
| 技术债清理（路由统一+XSS+Config统一+文档瘦身49→25） | 全站 |
| 冒烟测试 123/123 | smoke/ |

### v2.0 (2026-05-07) — 项目启动

| 改动 | 涉及文件 |
|------|---------|
| AI家办→AI知识库 项目转型 | 全站 |
| Git初始化 | `.git/` |
| BM25+pickle向量 + RRF混合检索 | `rag_engine.py` |
| FastAPI 后端 + 原生 HTML/CSS/JS 前端 | `api_server.py`, `web/` |

### v1.0 (2026-05-06) — AI家办原型

| 改动 | 涉及文件 |
|------|---------|
| 初始原型（已废弃） | 全站初始 |

---

## 关联文档

| 文档 | 关系 |
|------|------|
| `docs/项目真值.md` | 硬事实：端口、路由、技术栈 |

---

## 本文件修改规则

```
任何模型修改本文档前，必须：
1. 读完本文件顶部 ⛔ 防漂移屏障（五行规则）
2. 确认自己的改动是「追加新版本记录」而非「修改历史」
3. 在底部追加新条目，格式为：vX.Y (日期) — 标题 | 表格 | 改动摘要
4. 如果版本号必须升级，必须同时更新"当前版本"表格
5. 修改完成后，更新 docs/项目真值.md 中的版本引用（如有）
```
