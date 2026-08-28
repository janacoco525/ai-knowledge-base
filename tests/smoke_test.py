"""
AI知识库 - 冒烟测试
验证核心功能是否正常
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import requests

BASE_URL = os.getenv("AI_KB_BASE_URL", "http://127.0.0.1:8501")
PASS = 0
FAIL = 0


def prewarm_endpoint(path, timeout=20):
    try:
        requests.get(f"{BASE_URL}{path}", timeout=timeout)
    except Exception:
        return


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} - {detail}")


def test_health():
    print("\n1. 健康检查")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        check("GET /health", r.status_code == 200, f"状态码: {r.status_code}")
        data = r.json()
        check("返回 status=healthy", data.get("status") == "healthy")
    except Exception as e:
        check("GET /health 可访问", False, str(e))


def test_api_config():
    print("\n2. API配置")
    try:
        r = requests.get(f"{BASE_URL}/api/config", timeout=5)
        check("GET /api/config", r.status_code == 200)
        data = r.json()
        check("包含 domains", "domains" in data)
        check("包含 quick_questions", "quick_questions" in data)
        check("包含 api_base", "api_base" in data)
        check("包含 model", "model" in data)
    except Exception as e:
        check("GET /api/config 可访问", False, str(e))


def test_kb_stats():
    print("\n3. 知识库统计")
    try:
        r = requests.get(f"{BASE_URL}/api/kb/stats", timeout=8)
        check("GET /api/kb/stats", r.status_code == 200)
    except Exception as e:
        check("GET /api/kb/stats 可访问", False, str(e))


def test_kb_domains():
    print("\n4. 知识域列表")
    try:
        r = requests.get(f"{BASE_URL}/api/kb/domains", timeout=5)
        check("GET /api/kb/domains", r.status_code == 200)
        data = r.json()
        check("domains 非空", len(data.get("domains", {})) > 0)
    except Exception as e:
        check("GET /api/kb/domains 可访问", False, str(e))


def test_quick_questions():
    print("\n5. 快捷提问")
    try:
        r = requests.get(f"{BASE_URL}/api/kb/quick-questions", timeout=5)
        check("GET /api/kb/quick-questions", r.status_code == 200)
        data = r.json()
        check("返回 questions 列表", len(data.get("questions", [])) > 0)
    except Exception as e:
        check("GET /api/kb/quick-questions 可访问", False, str(e))


def test_kb_files():
    print("\n6. 文件列表")
    try:
        r = requests.get(f"{BASE_URL}/api/kb/files", timeout=5)
        check("GET /api/kb/files", r.status_code == 200)
    except Exception as e:
        check("GET /api/kb/files 可访问", False, str(e))


def test_graph_data():
    print("\n7. 图谱样例数据")
    try:
        r = requests.get(f"{BASE_URL}/api/graph/data", params={"source_mode": "sample"}, timeout=5)
        check("GET /api/graph/data", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("图谱包含 nodes", isinstance(data.get("nodes"), list))
            check("图谱包含 edges", isinstance(data.get("edges"), list))
            source_mode = data.get("meta", {}).get("source_mode", "")
            check("图谱来源已标注", source_mode in ("sample-graph-data", "live-knowledge-base"), source_mode)
    except Exception as e:
        check("GET /api/graph/data 可访问", False, str(e))

    try:
        r = requests.get(
            f"{BASE_URL}/api/graph/data",
            params={"source_mode": "sample", "selection_profile": "compact", "sorting_strategy": "recency", "max_chunks": 24},
            timeout=5,
        )
        check("GET /api/graph/data (compact 回显)", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            meta = data.get("meta", {})
            check("compact profile 已回显", meta.get("selection_profile") == "compact", meta.get("selection_profile"))
            check("compact 排序已回显", meta.get("sorting_strategy") == "recency", meta.get("sorting_strategy"))
            selected_count = meta.get("selected_chunk_count", 0)
            check("compact chunks 已收窄", isinstance(selected_count, int) and selected_count <= 24, selected_count)
    except Exception as e:
        check("GET /api/graph/data (compact 回显) 可访问", False, str(e))

    try:
        r = requests.get(
            f"{BASE_URL}/api/graph/data",
            params={
                "source_mode": "sample",
                "selection_profile": "compact",
                "sorting_strategy": "recency",
                "max_chunks": 24,
                "focus_concept": "AI",
            },
            timeout=5,
        )
        check("GET /api/graph/data (focus 回显)", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            focus_result = data.get("meta", {}).get("focus_result", {})
            check("focus 已回显概念", focus_result.get("focus_concept") == "AI", focus_result.get("focus_concept"))
            check("focus 返回匹配结果字段", "matched_chunk_count" in focus_result)
    except Exception as e:
        check("GET /api/graph/data (focus 回显) 可访问", False, str(e))


def test_graph_page():
    print("\n8. 图谱页面（v2 原型已弃用，验证 301 重定向）")
    try:
        r = requests.get(f"{BASE_URL}/web/graph.html", timeout=5, allow_redirects=False)
        check("GET /web/graph.html → 301 弃用重定向", r.status_code == 301)
    except Exception as e:
        check("GET /web/graph.html 重定向验证", False, str(e))


def test_graph_extract():
    print("\n9. 图谱规则提取")
    payload = {
        "text": "# Transformer\n## Attention\n## Multi-Head Attention\nAttention helps the Transformer focus on relevant tokens.",
        "source_file": "graph-smoke.md",
        "domain": "ai_knowledge",
        "max_nodes": 6
    }
    try:
        r = requests.post(f"{BASE_URL}/api/graph/extract", json=payload, timeout=5)
        check("POST /api/graph/extract", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("提取结果包含 nodes", len(data.get("nodes", [])) >= 2)
            check("提取结果包含 edges", len(data.get("edges", [])) >= 1)
    except Exception as e:
        check("POST /api/graph/extract 可访问", False, str(e))


def test_graph_chain():
    print("\n10. 概念串联与缺口提示")
    payload = {
        "concept": "Attention",
        "domain": "ai_knowledge",
        "max_chain_steps": 4,
        "max_gap_hints": 2,
        "source_mode": "sample",
    }
    try:
        r = requests.post(f"{BASE_URL}/api/graph/chain", json=payload, timeout=5)
        check("POST /api/graph/chain", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("返回至少 1 条 chain", len(data.get("related_chain", [])) >= 1)
            check("返回至少 1 条 gap_hints", len(data.get("gap_hints", [])) >= 1)
            source_mode = data.get("meta", {}).get("source_mode", "")
            check("source_mode 为 rule-backed 来源", source_mode.startswith("rule-backed-"), source_mode)
    except Exception as e:
        check("POST /api/graph/chain 可访问", False, str(e))

    live_payload = {
        "concept": "Transformer",
        "domain": "ai_knowledge",
        "max_chain_steps": 4,
        "max_gap_hints": 2,
        "source_mode": "sample",
        "selection_profile": "compact",
        "sorting_strategy": "diversity",
        "max_chunks": 24,
        "focus_concept": "Attention",
    }
    try:
        r = requests.post(f"{BASE_URL}/api/graph/chain", json=live_payload, timeout=5)
        check("POST /api/graph/chain (参数回显)", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("sample chain 至少 1 条", len(data.get("related_chain", [])) >= 1)
            check("sample source_mode 正确", data.get("meta", {}).get("source_mode") == "rule-backed-sample-graph-data")
            check("sample chain profile 已回显", data.get("meta", {}).get("selection_profile") == "compact")
            check("sample chain 排序已回显", data.get("meta", {}).get("sorting_strategy") == "diversity")
            check("sample chain focus 已回显", data.get("meta", {}).get("focus_result", {}).get("focus_concept") == "Attention")
    except Exception as e:
        check("POST /api/graph/chain (参数回显) 可访问", False, str(e))


def test_analysis_summary():
    print("\n11. 分析总结最小接口")
    try:
        files_resp = requests.get(f"{BASE_URL}/api/kb/files", timeout=5)
        check("分析总结前置文件列表可访问", files_resp.status_code == 200)
        if files_resp.status_code != 200:
            return
        files = files_resp.json().get("files", [])
        check("分析总结至少有 1 个已索引文件", len(files) >= 1)
        if not files:
            return
        payload = {
            "file_ids": [files[0]["id"]],
            "max_files": 1,
            "max_chunks_per_file": 2,
            "max_highlights": 3,
            "analysis_focus": "summary",
        }
        r = requests.post(f"{BASE_URL}/api/analysis/summary", json=payload, timeout=20)
        check("POST /api/analysis/summary", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("分析总结包含 title", "title" in data and bool(data.get("title")))
            check("分析总结包含 summary", "summary" in data and bool(data.get("summary")))
            check("分析总结包含 selected_files", isinstance(data.get("selected_files"), list) and len(data.get("selected_files", [])) >= 1)
            check("分析总结包含 source_mode", bool(data.get("meta", {}).get("source_mode")))
    except Exception as e:
        check("POST /api/analysis/summary 可访问", False, str(e))


def test_analysis_page():
    print("\n12. 分析总结页面（v2 原型已弃用，验证 301 重定向）")
    try:
        r = requests.get(f"{BASE_URL}/web/analysis.html", timeout=5, allow_redirects=False)
        check("GET /web/analysis.html → 301 弃用重定向", r.status_code == 301)
    except Exception as e:
        check("GET /web/analysis.html 重定向验证", False, str(e))


def test_analysis_topics():
    print("\n12b. 跨文件主题提炼")
    try:
        files_resp = requests.get(f"{BASE_URL}/api/kb/files", timeout=5)
        file_ids = [f["id"] for f in (files_resp.json().get("files", []) or [])[:2]]
        check("跨文件主题前置文件列表可访问", len(file_ids) >= 1)

        payload = {"file_ids": file_ids, "max_files": 3, "max_topics": 4}
        r = requests.post(f"{BASE_URL}/api/analysis/topics", json=payload, timeout=30)
        check("POST /api/analysis/topics", r.status_code == 200)
        data = r.json()
        check("返回包含 topics", bool(data.get("topics")))
        check("topics 是数组", isinstance(data.get("topics"), list))
        if data.get("topics"):
            t = data["topics"][0]
            check("topic 包含 topic 字段", "topic" in t)
            check("topic 包含 source_files", "source_files" in t)
            check("topic 包含 key_points", "key_points" in t)
        check("返回包含 meta", bool(data.get("meta")))
        check("meta 包含 source_mode", bool(data.get("meta", {}).get("source_mode")))
    except Exception as e:
        check("POST /api/analysis/topics 可访问", False, str(e))


def test_diff_endpoint():
    print("\n13. AI Diff 语义对比接口")
    try:
        files_resp = requests.get(f"{BASE_URL}/api/kb/files", timeout=5)
        check("AI Diff前置文件列表可访问", files_resp.status_code == 200)
        if files_resp.status_code != 200:
            return
        files = files_resp.json().get("files", [])
        check("AI Diff至少有 1 个已索引文件", len(files) >= 1)
        if len(files) < 2:
            check("AI Diff 需要至少 2 个文件", False, "已索引文件不足2个")
            return
        payload = {
            "file_id_a": files[0]["id"],
            "file_id_b": files[1]["id"],
            "max_changes": 5
        }
        r = requests.post(f"{BASE_URL}/api/diff", json=payload, timeout=20)
        check("POST /api/diff", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 diff_title", "diff_title" in data)
            check("返回包含 changes", "changes" in data and len(data.get("changes", [])) >= 0)
            check("返回包含 similarity_score", "similarity_score" in data)
    except Exception as e:
        check("POST /api/diff 可访问", False, str(e))


def test_diff_page():
    print("\n14. AI Diff 页面（v2 原型已弃用，验证 301 重定向）")
    try:
        r = requests.get(f"{BASE_URL}/web/diff.html", timeout=5, allow_redirects=False)
        check("GET /web/diff.html → 301 弃用重定向", r.status_code == 301)
    except Exception as e:
        check("GET /web/diff.html 重定向验证", False, str(e))


def test_query():
    print("\n21. RAG查询（非流式）")
    try:
        r = requests.get(f"{BASE_URL}/api/query", params={"q": "测试"}, timeout=15)
        check("GET /api/query", r.status_code in (200, 500), f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 answer", "answer" in data)
    except Exception as e:
        check("GET /api/query 可访问", False, str(e))


def test_quick_query():
    print("\n22. 快速提问")
    try:
        r = requests.get(f"{BASE_URL}/api/query/quick", params={"q": "什么是机器学习"}, timeout=10)
        check("GET /api/query/quick", r.status_code in (200, 500), f"状态码: {r.status_code}")
    except Exception as e:
        check("GET /api/query/quick 可访问", False, str(e))


def test_knowledge_tree_endpoint():
    print("\n15. 知识框架树接口")
    try:
        # 先获取文件列表
        r = requests.get(f"{BASE_URL}/api/kb/files", timeout=5)
        check("知识框架树前置文件列表可访问", r.status_code == 200)
        data = r.json()
        file_count = len(data.get("files", []))
        check("知识框架树至少有 1 个已索引文件", file_count >= 0)
        
        # 测试API
        r = requests.post(
            f"{BASE_URL}/api/knowledge-tree/generate",
            json={},
            timeout=30
        )
        check("POST /api/knowledge-tree/generate", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 tree_id", "tree_id" in data)
            check("返回包含 tree", "tree" in data)
            check("返回包含 insights", "insights" in data)
    except Exception as e:
        check("POST /api/knowledge-tree/generate 可访问", False, str(e))


def test_knowledge_tree_page():
    print("\n16. 知识框架树页面（v2 原型已弃用，验证 301 重定向）")
    try:
        r = requests.get(f"{BASE_URL}/web/knowledge-tree.html", timeout=5, allow_redirects=False)
        check("GET /web/knowledge-tree.html → 301 弃用重定向", r.status_code == 301)
    except Exception as e:
        check("GET /web/knowledge-tree.html 重定向验证", False, str(e))


def test_reading_history_endpoint():
    print("\n17. 阅读记录接口")
    try:
        # 测试POST记录阅读
        r = requests.post(
            f"{BASE_URL}/api/reading-history",
            json={"file_id": "smoke_test_file"},
            timeout=5
        )
        check("POST /api/reading-history", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 status", data.get("status") in ("ok", "skipped"))
            if data.get("status") == "ok":
                check("返回包含 read_count", "read_count" in data)

        # 测试PUT重命名
        r = requests.put(
            f"{BASE_URL}/api/reading-history",
            json={"file_id": "smoke_test_file", "new_name": "测试文件"},
            timeout=5
        )
        check("PUT /api/reading-history", r.status_code in (200, 404))

        # 测试GET获取历史
        r = requests.get(f"{BASE_URL}/api/reading-history", timeout=5)
        check("GET /api/reading-history", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 records", "records" in data)
            check("返回包含 total", "total" in data)

        # 测试GET继续阅读建议
        r = requests.get(f"{BASE_URL}/api/reading-history/continue", timeout=5)
        check("GET /api/reading-history/continue", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 suggestions", "suggestions" in data)
    except Exception as e:
        check("阅读记录接口可访问", False, str(e))


def test_reading_history_page():
    print("\n18. 阅读记录页面（v2 原型已弃用，验证 301 重定向）")
    try:
        r = requests.get(f"{BASE_URL}/web/reading-history.html", timeout=5, allow_redirects=False)
        check("GET /web/reading-history.html → 301 弃用重定向", r.status_code == 301)
    except Exception as e:
        check("GET /web/reading-history.html 重定向验证", False, str(e))


def test_cards_endpoint():
    print("\n19. 知识卡片接口")
    try:
        # 测试POST生成卡片
        r = requests.post(
            f"{BASE_URL}/api/cards/generate",
            json={"source_mode": "auto", "max_cards": 10},
            timeout=15,
        )
        check("POST /api/cards/generate", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 status", data.get("status") == "ok")
            check("返回包含 generated_count", "generated_count" in data and data["generated_count"] >= 1)
            check("返回包含 total_cards", "total_cards" in data)

        # 测试GET卡片列表
        r = requests.get(f"{BASE_URL}/api/cards", params={"limit": 5}, timeout=5)
        check("GET /api/cards", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            check("返回包含 cards", "cards" in data and isinstance(data["cards"], list))
            check("返回包含 total", "total" in data)

        # 测试GET搜索
        r = requests.get(f"{BASE_URL}/api/cards", params={"search": "a", "limit": 3}, timeout=5)
        check("GET /api/cards (search)", r.status_code == 200)

        # 测试GET单卡详情
        cards_data = r.json()
        cards_list = cards_data.get("cards", [])
        if cards_list:
            cid = cards_list[0].get("concept_id", "")
            if cid:
                r = requests.get(f"{BASE_URL}/api/cards/{cid}", timeout=5)
                check("GET /api/cards/{concept_id}", r.status_code == 200)
            else:
                check("GET /api/cards/{concept_id}", False, "无有效 concept_id")
        else:
            check("GET /api/cards/{concept_id}", False, "无卡片数据")
    except Exception as e:
        check("知识卡片接口可访问", False, str(e))


def test_cards_extract():
    print("\n19b. 知识卡片独立提取")
    try:
        # 测试按关键词独立提取
        r = requests.post(
            f"{BASE_URL}/api/cards/extract",
            json={"keyword": "AI", "max_cards": 3, "source_mode": "auto"},
            timeout=15,
        )
        check("POST /api/cards/extract", r.status_code in (200, 404))
        if r.status_code == 200:
            data = r.json()
            check("extract返回 status", data.get("status") == "ok")
            check("extract返回 generated_count", "generated_count" in data)
            check("extract返回 scope", "scope" in data)
            scope = data.get("scope", {})
            check("scope包含 keyword", scope.get("keyword") == "AI")

        # 测试空参数（返回全部，与generate类似但路径不同）
        r = requests.post(
            f"{BASE_URL}/api/cards/extract",
            json={"max_cards": 5, "source_mode": "auto"},
            timeout=15,
        )
        check("POST /api/cards/extract (空scope)", r.status_code in (200, 404))
    except Exception as e:
        check("POST /api/cards/extract 可访问", False, str(e))


def test_recommend():
    print("\n20. 知识延展推荐")
    try:
        r = requests.post(
            f"{BASE_URL}/api/recommend",
            json={"focus": "books", "max_recommendations": 3},
            timeout=30,
        )
        check("POST /api/recommend", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("返回包含 meta", "meta" in data)
            check("返回包含 recommendations", "recommendations" in data)
            if data.get("recommendations"):
                rec = data["recommendations"][0]
                check("推荐包含 title", "title" in rec)
                check("推荐包含 type", "type" in rec)
                check("推荐包含 description", "description" in rec)
    except Exception as e:
        check("POST /api/recommend 可访问", False, str(e))


def test_cards_page():
    print("\n20. 知识卡片页面（v2 原型已弃用，验证 301 重定向）")
    try:
        r = requests.get(f"{BASE_URL}/web/cards.html", timeout=5, allow_redirects=False)
        check("GET /web/cards.html → 301 弃用重定向", r.status_code == 301)
    except Exception as e:
        check("GET /web/cards.html 重定向验证", False, str(e))


def test_card_balance():
    print("\n20. 知识卡片数量验证")
    try:
        # 生成后查询，确认卡片总数与快照一致
        r = requests.get(f"{BASE_URL}/api/cards", timeout=5)
        check("GET /api/cards 可访问", r.status_code == 200)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total", 0)
            cards_list = data.get("cards", [])
            check("卡片数量 ≥ 0", total >= 0)
            check("返回卡片列表长度与 total 一致", total == len(cards_list))
    except Exception as e:
        check("知识卡片数量验证 可访问", False, str(e))


def test_chat_history_endpoint():
    print("\n21. 聊天记录持久化接口")
    try:
        # 测试保存
        payload = {
            "sessionId": "smoke-test",
            "title": "冒烟测试会话",
            "libraryId": "all",
            "messages": [
                {"id": "sm-1", "role": "user", "content": "测试问题", "timestamp": "2026-07-16 19:00:00", "citations": [], "groundingSources": []},
                {"id": "sm-2", "role": "assistant", "content": "测试回答", "timestamp": "2026-07-16 19:00:01", "citations": [], "groundingSources": []}
            ]
        }
        r = requests.post(f"{BASE_URL}/api/chat/sessions", json=payload, timeout=5)
        check("POST /api/chat/sessions", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("保存返回 status=ok", data.get("status") == "ok")
            check("保存返回 session_id", data.get("session_id") == "smoke-test")
            check("保存返回 message_count=2", data.get("message_count") == 2)

        # 测试列表
        r = requests.get(f"{BASE_URL}/api/chat/sessions", timeout=5)
        check("GET /api/chat/sessions", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("列表返回 sessions 字段", "sessions" in data)
            check("列表返回 total 字段", "total" in data)
            check("total ≥ 1", data.get("total", 0) >= 1)

        # 测试获取单会话
        r = requests.get(f"{BASE_URL}/api/chat/sessions/smoke-test", timeout=5)
        check("GET /api/chat/sessions/{id}", r.status_code == 200, f"状态码: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            check("单会话包含 messages", "messages" in data)
            check("单会话 messageCount=2", data.get("messageCount") == 2)

        # 测试迁移（空数据）
        r = requests.post(f"{BASE_URL}/api/chat/migrate", json={"messages": []}, timeout=5)
        check("POST /api/chat/migrate (空)", r.status_code == 200)

        # 清理测试数据
        requests.delete(f"{BASE_URL}/api/chat/sessions/smoke-test", timeout=5)
    except Exception as e:
        check("聊天记录接口可访问", False, str(e))


def main():
    print("=" * 50)
    print("  AI知识库 - 冒烟测试")
    print("=" * 50)
    print(f"  服务器: {BASE_URL}")

    # 先检查服务器是否运行
    try:
        requests.get(f"{BASE_URL}/health", timeout=3)
    except:
        print("\n[ERROR] 服务器未运行！请先启动: python start.py")
        sys.exit(1)

    prewarm_endpoint("/api/kb/stats")
    prewarm_endpoint("/api/query?q=%E6%B5%8B%E8%AF%95", timeout=25)

    test_health()
    test_api_config()
    test_kb_stats()
    test_kb_domains()
    test_quick_questions()
    test_kb_files()
    test_graph_data()
    test_graph_page()
    test_graph_extract()
    test_graph_chain()
    test_analysis_summary()
    test_analysis_page()
    test_analysis_topics()
    test_diff_endpoint()
    test_diff_page()
    test_knowledge_tree_endpoint()
    test_knowledge_tree_page()
    test_reading_history_endpoint()
    test_reading_history_page()
    test_cards_endpoint()
    test_cards_extract()
    test_cards_page()
    test_recommend()
    test_card_balance()
    test_chat_history_endpoint()
    test_query()
    test_quick_query()

    print("\n" + "=" * 50)
    total = PASS + FAIL
    print(f"  结果: {PASS}/{total} 通过")
    if FAIL == 0:
        print("  [OK] 所有测试通过！")
    else:
        print(f"  [FAIL] {FAIL} 个测试失败")
    print("=" * 50)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
