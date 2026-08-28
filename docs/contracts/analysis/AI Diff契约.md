# AI知识库 — AI Diff 契约

> **用途**：固定 `S2-03 AI Diff` 的最小输入、输出和诚实边界
> **更新日期**：2026-05-12
> **性质**：当前项目专用 Owner，不替代 `PRD` 与长需求清单

---

## 一、这份契约先解决什么

- 先把 `S2-03` 从"想做文件对比"变成一条可开工、可验证、可交接的最小闭环。
- 当前只承接 **已索引文件** 的语义级对比，不冒充完整Git Diff或版本控制系统。
- 当前最小接口定为：`POST /api/diff`

---

## 二、当前范围边界

### 当前支持

- 按 `file_id_a` 和 `file_id_b` 对 2 个 **已索引文件** 做语义级差异描述
- 输出人类可读的变更描述（不是逐行diff，是语义级变更）
- 输出统一结构：`diff_title / changes / similarity_score / meta`

### 当前不冒充已有

- 不直接吃任意本地文件路径
- 不冒充Git diff或版本控制系统
- 不把"语义级差异描述"说成"逐行代码对比"
- 不处理超过2个文件的N-way对比

---

## 三、请求契约

### Endpoint

`POST /api/diff`

### Request JSON

```json
{
  "file_id_a": "file_a.md",
  "file_id_b": "file_b.md",
  "diff_focus": "summary",
  "max_changes": 5
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_id_a` | `string` | 是 | 来自 `GET /api/kb/files` 的已索引文件 ID A |
| `file_id_b` | `string` | 是 | 来自 `GET /api/kb/files` 的已索引文件 ID B |
| `diff_focus` | `"summary" \| "details" \| "highlights"` | 否 | 当前差异描述关注面，默认 `"summary"` |
| `max_changes` | `number` | 否 | 最多返回多少条差异描述 |

---

## 四、响应契约

### Response JSON

```json
{
  "diff_title": "Transformer.md vs Attention.md 语义对比",
  "changes": [
    {
      "type": "added",
      "description": "新增了XX章节，详细介绍了..."
    },
    {
      "type": "removed", 
      "description": "删除了XX段落，原内容涉及..."
    },
    {
      "type": "modified",
      "description": "修改了XX概念的定义，从...变为..."
    }
  ],
  "similarity_score": 0.65,
  "meta": {
    "source_mode": "llm-backed-indexed-scope",
    "file_a": "Transformer.md",
    "file_b": "Attention.md",
    "chunk_count_a": 8,
    "chunk_count_b": 5,
    "diff_focus": "summary"
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `diff_title` | `string` | 当前差异对比标题 |
| `changes` | `object[]` | 语义级差异描述列表 |
| `changes[].type` | `string` | 差异类型：`added` / `removed` / `modified` |
| `changes[].description` | `string` | 人类可读的差异描述 |
| `similarity_score` | `number` | 相似度分数（0-1，1表示完全相同） |
| `meta` | `object` | 元信息 |
| `meta.source_mode` | `string` | `llm-backed-indexed-scope` 或 `extractive-fallback-indexed-scope` |
| `meta.file_a` | `string` | 文件A的名称 |
| `meta.file_b` | `string` | 文件B的名称 |
| `meta.chunk_count_a` | `number` | 文件A参与的chunk数量 |
| `meta.chunk_count_b` | `number` | 文件B参与的chunk数量 |
| `meta.diff_focus` | `string` | 当前差异描述关注面 |

---

## 五、最小验收

1. `POST /api/diff` 对 2 个已索引文件返回 `200`
2. 响应中存在 `diff_title / changes / similarity_score / meta`
3. `changes` 列表包含至少 1 条差异描述
4. 当 LLM 不可用时，仍会回退到 `extractive-fallback-indexed-scope`，不假装"对比失败就没有结果"
5. 不把语义级差异描述误报成"逐行代码对比"

---

## 六、当前后续读法

- 这份契约落下后，`S2-03` 就不再是 `active` 停放态，而是正式进入 **最小契约 + 最小接口** 阶段。
- 下一刀更适合做：
  - 把这条接口接进当前 React 消费面（如 `frontend/src/components/DocComparison.tsx`）
  - 或增加"多文件对比"的轻验证
- 不适合立刻跳到：
  - 完整版本控制系统
  - N-way文件对比
  - 知识框架树自动生成

---

*与 `分析总结契约.md` 并列，同属 S2 智能增强链*
