# 我的知识库

> 一个面向普通人的本地知识助手——上传文档，用自然语言提问，发现知识之间的关联。

## 选择你的使用方式

|  | **方式一：免安装版**（推荐给普通用户） | **方式二：源码部署**（开发者） |
|---|---|---|
| 需要装 Python 吗 | **不需要**，已内置 Python 3.12.8 | 需要，**Python 3.12**（实测 3.12.8） |
| 需要装依赖吗 | 不需要，全部预装 | 需要 `pip install -r requirements.txt` |
| 需要构建前端吗 | 不需要，已编译好 | 需要 `npm run build` |
| 上手耗时 | 解压即用，约 1 分钟 | 10～20 分钟 |

### 方式一：免安装版（不装 Python，解压即用）

1. 到 [Releases 页面](https://github.com/janacoco525/ai-knowledge-base/releases) 下载
   `AI知识库-免安装版-v3.8.1-Windows-x64.zip`（132 MB，已内置 Python 3.12.8）
2. 解压到任意位置（路径建议不含空格和中文，例如 `D:\AIKB`）
3. 双击 `启动.bat`，浏览器会自动打开 http://127.0.0.1:8501
4. 关闭黑色命令行窗口即停止服务

> 内置的 Python 3.12.8 只在程序文件夹内生效，不会改动系统环境变量，
> 也不会影响你电脑上已有的 Python。整个文件夹就是全部数据，复制文件夹即完成备份。

### 方式二：源码部署

**环境要求**

- **Python 3.12**（本项目实测 3.12.8。不建议用 3.13，部分依赖尚无兼容版本）
- Node.js 18 及以上（仅用于构建前端）

```bash
# 1. 创建虚拟环境并使用 Python 3.12
python -m venv .venv

# 2. 激活（Windows）
.venv\Scripts\activate
#   激活（macOS / Linux）
source .venv/bin/activate

# 3. 确认版本
python --version      # 应显示 Python 3.12.x

# 4. 安装依赖
pip install -r requirements.txt

# 5. 构建前端
cd frontend && npm install && npm run build && cd ..

# 6. 启动服务
python start.py
```

浏览器打开 http://127.0.0.1:8501

> ⚠️ 修改 `frontend/src/` 下任何文件后，必须重新执行 `npm run build`，
> 否则浏览器仍显示旧版本。`scripts/启动.bat` 会自动处理构建。

## 为什么存在

我在学习 AI/机器学习的过程中，积累了大量的 PDF 论文、电子书、笔记。我需要一个工具能：
1. **不需要联网上传**——文件都是本地的，隐私优先，不想传到云端
2. **能用自然语言提问**——不是搜索关键词，而是像问人一样"这本书讲了什么？"
3. **能看到知识之间的关联**——不同文件里的概念是如何联系起来的
4. **不需要复杂配置**——开箱即用，双击打开就能用

市面上的工具要么要联网（ChatGPT/NotebookLM），要么是开发者导向（LangChain），要么太复杂（Obsidian + 插件）。所以我做了这个——一个**纯本地、一键启动、普通人也能用的 AI 知识库**。

## 当前状态 (2026-08-02)

- 版本: v3.8.1（累积修复收口：脑图生成进度接口与轮询 + 联网问答上下文保持 + 知识卡片轮询防卡死 + 图谱分类/语义边增强 + 新机迁移配套）
- 验证基线（2026-08-20 实测）: `python start.py --health` 22/22 · `pytest tests` 285/285 · `python start.py --test` 冒烟 118/118
- 服务端口: `http://127.0.0.1:8501`
- 支持格式: PDF, DOCX, TXT, MD, EPUB, MOBI, PY, JS, JSON, CSV（10 种）
- 核心功能: 文档入库 / 智能问答 / 知识图谱 / 知识脑图 / 知识卡片 / 分析总结 / AI Diff / 阅读记录 / 推荐阅读
- 当前重点: 图谱 live 预生成异步化、知识框架树前端接入、检索质量优化
- Python 版本要求: **3.12**（实测 3.12.8）
- 启动方式: 免安装版双击 `启动.bat`；源码部署用 `.venv\Scripts\python.exe start.py`（⚠️ 必须用 venv Python，系统 Python 缺依赖）

## 日常使用与自检

```bash
# 快速体检（约 20 秒，跳过全量测试，适合日常）
python start.py --health

# 完整体检（约 100 秒，含全量 pytest，适合发布前 / 提交前）
python start.py --health --full

# 冒烟测试
python start.py --test
```

## 项目结构

```
├── app/rag_app/      # FastAPI 后端（项目专属业务）
│   ├── api_server.py # 服务入口
│   ├── config.py     # 配置唯一真值
│   ├── routes/       # API 路由（knowledge/chat/graph/analysis/...）
│   └── shared_engine.py # 共享引擎单例（统一 KB 与 RAG）
├── core/             # 通用核心包（stategraph/orchestrator/verifier 等，跨项目复用）
├── frontend/         # React SPA 当前前端源码（前端改动优先改这里）
├── docs/             # 项目文档（产品需求 / API 契约 / 术语 / 版本记录）
├── tests/            # 单元测试与冒烟测试
├── scripts/          # 运维脚本（启动 / 安装服务 / 前端构建 / 数据迁移）
└── start.py          # 启动入口
```

## 文档导航

| 文档 | 用途 |
|------|------|
| `docs/项目真值.md` | 技术硬事实：端口 / 技术栈 / API 清单 |
| `docs/VERSION.md` | 版本历史与变更记录 |
| `docs/术语表.md` | 核心术语定义（图谱 / 脑图 / 卡片） |
| `docs/数据迁移指南.md` | 换电脑 / 换浏览器时的数据迁移 |
| `docs/contracts/` | 各功能的 API 数据契约（图谱 / 问答 / 卡片 / 脑图等） |
| `docs/product/` | 产品需求文档与功能蓝图 |
| `ARCHITECTURE.md` | 架构说明（`core` 通用包 + `app/rag_app` 业务包） |

## 技术栈

FastAPI 后端 + React/Vite SPA 前端 + BM25+TF-IDF 混合检索 + OpenAI-compatible LLM providers + SSE 流式。

文档按用途分层：`docs/product/`（产品需求与蓝图）、`docs/contracts/`（API 接口契约），另有术语表 / 版本记录 / 数据迁移指南。

## 许可证

本项目采用 [MIT License](LICENSE)。你可以自由使用、修改、分发，包括商用，但需保留版权声明。
