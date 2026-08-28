"""T40：知识框架树 LLM 脏树清洗逻辑的单元测试（tests/unit/ 约定：sys.path 注入 + pytest）"""
import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.rag_engine import RAGEngine


def _engine() -> RAGEngine:
    """绕过构造函数（需要 Config/KnowledgeBase/LLM 客户端），直接绑定方法"""
    return object.__new__(RAGEngine)


def test_sanitize_normal_tree_keeps_structure():
    tree = {"root": {"id": "root", "label": "知识库",
                     "children": [{"id": "n1", "label": "概念", "description": "解释",
                                   "children": [{"id": "n2", "label": "子项"}]}]}}
    result = _engine()._sanitize_tree(tree, 3)
    assert result is not None
    root = result["root"]
    assert root["id"] == "root"
    assert root["label"] == "知识库"
    assert len(root["children"]) == 1
    # 缺 description 的节点补默认值
    assert root["children"][0]["children"][0]["description"].startswith("关于「子项」")


def test_sanitize_empty_children_returns_none():
    """整树无 children → 返回 None，调用方应回退规则树"""
    tree = {"root": {"id": "root", "label": "知识库", "children": []}}
    assert _engine()._sanitize_tree(tree, 3) is None


def test_sanitize_missing_root_returns_none():
    assert _engine()._sanitize_tree({}, 3) is None
    assert _engine()._sanitize_tree({"root": "not-a-dict"}, 3) is None
    assert _engine()._sanitize_tree("not-a-dict", 3) is None
    assert _engine()._sanitize_tree(None, 3) is None


def test_sanitize_filters_dirty_nodes():
    """非 dict / 缺 label 的脏节点被过滤；全部无效时整树视为无效"""
    tree = {"root": {"id": "root", "label": "知识库",
                     "children": [
                         "dirty-string",
                         {"children": []},          # 缺 label
                         {"id": "n1", "label": "有效节点"},
                     ]}}
    result = _engine()._sanitize_tree(tree, 3)
    assert result is not None
    assert [c["label"] for c in result["root"]["children"]] == ["有效节点"]


def test_sanitize_all_dirty_children_returns_none():
    tree = {"root": {"id": "root", "label": "知识库", "children": ["x", {"children": []}]}}
    assert _engine()._sanitize_tree(tree, 3) is None


def test_sanitize_depth_limit():
    """深度超过 max_depth 的子树被截断（不再继续展开）"""
    deep = {"id": "a", "label": "A"}
    child = deep
    for _ in range(5):
        child = {"id": "x", "label": "X", "children": [child]}
    tree = {"root": {"id": "root", "label": "知识库", "children": [child]}}
    result = _engine()._sanitize_tree(tree, 2)
    assert result is not None
    # max_depth=2：root(0) → child(1) → 孙(2)，孙的 children 不再保留
    level1 = result["root"]["children"][0]
    assert level1["children"], "第1层应保留 children"
    assert level1["children"][0]["children"] == [], "第2层 children 应被截断"


def test_sanitize_children_cap_30():
    """单层 children 超过 30 个时截断"""
    children = [{"id": f"n{i}", "label": f"项{i}"} for i in range(40)]
    tree = {"root": {"id": "root", "label": "知识库", "children": children}}
    result = _engine()._sanitize_tree(tree, 3)
    assert result is not None
    assert len(result["root"]["children"]) == 30


# ⛔ 2026-08-18：LLM 空内容重试 + 知识框架树截断重试（便携版实测问题修复）

class _FakeMsg:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.message = _FakeMsg(content)
        self.finish_reason = finish_reason


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.kwargs_log = []

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs_log.append(dict(kwargs))
        choice = self._responses.pop(0)
        return type("R", (), {"choices": [choice]})()


class _FakeLLM:
    def __init__(self, responses):
        self.chat = type("C", (), {"completions": _FakeCompletions(responses)})()


def _llm_engine(responses):
    eng = _engine()
    eng.llm_client = _FakeLLM(responses)
    eng.model_name = "fake-model"
    return eng


def test_llm_chat_retry_empty_then_success():
    """空 content 自动重试：第 1 次空、第 2 次有内容 → 返回内容且调用 2 次。"""
    eng = _llm_engine([_FakeChoice(""), _FakeChoice("好答案")])
    content, reason = eng._llm_chat_retry("sys", "user")
    assert content == "好答案"
    assert reason == "stop"
    assert eng.llm_client.chat.completions.calls == 2


def test_llm_chat_retry_all_empty_exhausts():
    """3 次全空 → 返回空串，不抛异常。"""
    eng = _llm_engine([_FakeChoice(""), _FakeChoice(""), _FakeChoice("")])
    content, reason = eng._llm_chat_retry("sys", "user", attempts=3)
    assert content == ""
    assert eng.llm_client.chat.completions.calls == 3


def test_llm_chat_retry_truncation_bumps_tokens():
    """finish_reason=length 截断 → 加大 max_tokens 重试（知识框架树场景）。"""
    eng = _llm_engine([_FakeChoice("残缺JSON", "length"), _FakeChoice("完整JSON", "stop")])
    content, reason = eng._llm_chat_retry("sys", "user", max_tokens=6000,
                                          attempts=2, retry_on_truncation=True,
                                          truncation_boost=2000)
    assert content == "完整JSON"
    assert reason == "stop"
    log = eng.llm_client.chat.completions.kwargs_log
    assert log[0]["max_tokens"] == 6000
    assert log[1]["max_tokens"] == 8000


def test_sanitize_label_trim():
    """label 超过 20 字被截断；id 缺失自动补 n<序号>"""
    tree = {"root": {"id": "root", "label": "知识库",
                     "children": [{"label": "长" * 50}]}}
    result = _engine()._sanitize_tree(tree, 3)
    child = result["root"]["children"][0]
    assert len(child["label"]) == 20
    assert child["id"].startswith("n")
