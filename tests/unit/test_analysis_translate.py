"""T6 补充：analysis.py 段落分组与翻译辅助函数单元测试（2026-08-06）
覆盖：段落分组 / 超长组切子块 / 语言判定 / 缓存键 / 断点续传复用 / 磁盘缓存落盘。
"""
import sys
import os
import json
import time
import tempfile

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

import app.rag_app.routes.analysis as analysis
from app.rag_app.routes.analysis import (
    _split_paragraph_groups,
    _split_oversized_group,
    _build_translate_units,
    _resume_from_cached,
    _prepare_translate_task,
    _is_chinese,
    _translate_cache_key,
    _translate_para_cache_key,
    _ensure_translate_cache_loaded,
    _save_translate_cache,
    _para_cache_get,
    _para_cache_save_complete,
    _para_cache_delete,
    _translate_cache,
    _translate_tasks,
    _tasks_lock,
    start_translate_task,
    cancel_translate_task,
    _run_translate_task,
    TranslateRequest,
)


# ── _split_paragraph_groups：段落分组 ──

def test_split_short_text_single_group():
    # 短文本（≤ max_chars）原样返回单组，不做拆分
    assert _split_paragraph_groups("Hello world.") == ["Hello world."]


def test_split_empty_input():
    assert _split_paragraph_groups("") == []


def test_split_multiple_groups_by_chars():
    # 3 段（2000/2000/100 字符），max_chars=3500 → 前两段超限拆开，第三段并入后组
    p1 = "a" * 2000
    p2 = "b" * 2000
    p3 = "c" * 100
    groups = _split_paragraph_groups(f"{p1}\n\n{p2}\n\n{p3}", max_chars=3500)
    assert len(groups) == 2
    assert groups[0] == p1
    assert groups[1] == f"{p2}\n\n{p3}"


def test_split_code_block_isolated():
    # 总长 > max_chars 时，含代码围栏的段落强制独立成组，避免拆散代码块
    code = "```python\ndef f():\n    pass\n```"
    filler = "x" * 2000
    text = f"{filler}\n\n{code}\n\n{filler}"
    groups = _split_paragraph_groups(text, max_chars=3500)
    assert len(groups) == 3
    assert groups[1] == code


def test_split_blank_paragraphs_skipped():
    # 总长 > max_chars 时，空白段落被跳过且不产生空组
    filler = "y" * 2000
    groups = _split_paragraph_groups(f"{filler}\n\n\n\n\n{filler}", max_chars=3500)
    assert len(groups) == 2
    assert groups[0] == filler


# ── _is_chinese：语言粗判（汉字占比 > 30%） ──

def test_is_chinese():
    assert _is_chinese("这是中文内容") is True
    assert _is_chinese("Hello world") is False
    assert _is_chinese("") is True
    # 汉字占比低于 30% 判为非中文（混合短句）
    assert _is_chinese("Mix 中文 mixed text") is False
    # 汉字占比高 → 中文
    assert _is_chinese("中文中文中文 abc") is True


# ── 缓存键隔离 ──

def test_para_cache_key_distinct_from_single():
    # 段落级缓存键与单段接口缓存键前缀不同，避免同文本缓存类型冲突
    assert _translate_para_cache_key("hello") != _translate_cache_key("hello")
    assert _translate_para_cache_key("hello").startswith("trp:")


# ── _split_oversized_group：超长组二次切分（长文档提速） ──

def test_oversized_short_group_unchanged():
    # 短组（≤ max_chars）原样返回单块
    assert _split_oversized_group("hello world") == ["hello world"]


def test_oversized_split_by_lines():
    # 超长组按行切子块，每块 ≤ max_chars，且不切断行内文字
    g = "\n".join(f"line{i}" + "x" * 200 for i in range(50))  # 总长约 1 万字符
    subs = _split_oversized_group(g, max_chars=3500)
    assert len(subs) >= 2
    assert all(len(s) <= 3500 for s in subs)
    # 每块是完整行的拼接（子块不含半行）
    joined = "\n".join(subs)
    assert joined == g


def test_oversized_empty():
    assert _split_oversized_group("") == [""]


# ── _build_translate_units：单位展开 ──

def test_build_units_skips_chinese_and_splits_oversized():
    groups = ["Hello world.", "中文段落不需要翻译", "x" * 8000]
    units = _build_translate_units(groups)
    # 中文组被跳过；超长组被切成 ≥3 个子块
    assert all(u[0] != 1 for u in units)
    assert len([u for u in units if u[0] == 2]) >= 3
    # 单元结构 (组下标, 子块下标, 文本)
    gi, si, text = units[0]
    assert gi == 0 and si == 0 and text == "Hello world."


# ── _resume_from_cached：断点续传复用 ──

def test_resume_reuses_cached_groups():
    groups = [{"src": "a", "tgt": "", "skipped": False}, {"src": "b", "tgt": "", "skipped": False}]
    cached = [{"src": "a", "tgt": "甲", "skipped": False}, {"src": "b", "tgt": "", "skipped": False}]
    reused = _resume_from_cached(groups, cached)
    assert reused == 1
    assert groups[0]["tgt"] == "甲"
    assert groups[1]["tgt"] == ""


def test_resume_no_cache():
    groups = [{"src": "a", "tgt": "", "skipped": False}]
    assert _resume_from_cached(groups, None) == 0
    assert groups[0]["tgt"] == ""


# ── 磁盘持久化缓存（临时文件隔离） ──

def _isolate_cache_file(tmpdir: str):
    """把缓存文件重定向到临时路径，测试后恢复"""
    old_file = analysis._TRANSLATE_CACHE_FILE
    analysis._TRANSLATE_CACHE_FILE = os.path.join(tmpdir, "translate_cache.json")
    return old_file


def test_cache_persist_roundtrip(tmp_path):
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True  # 跳过真实文件加载
        analysis._translate_cache.clear()
        key = "trp:test1"
        groups = [{"src": "hello", "tgt": "你好", "skipped": False}]
        _para_cache_save_complete(key, groups)
        # 文件已落盘
        assert os.path.exists(analysis._TRANSLATE_CACHE_FILE)
        with open(analysis._TRANSLATE_CACHE_FILE, "r", encoding="utf-8") as f:
            disk = json.load(f)
        assert disk[key]["complete"] is True
        assert disk[key]["groups"][0]["tgt"] == "你好"
        # 内存可读回（complete 语义）
        cached_groups, complete = _para_cache_get(key)
        assert complete is True
        assert cached_groups[0]["tgt"] == "你好"
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_cache_corrupt_file_tolerated(tmp_path):
    # 损坏的缓存文件不阻断翻译（静默重建）
    old_file = _isolate_cache_file(str(tmp_path))
    cache_path = analysis._TRANSLATE_CACHE_FILE
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write("{broken json")
        analysis._translate_cache_loaded = False
        _ensure_translate_cache_loaded()
        assert analysis._translate_cache_loaded is True
        assert len(_translate_cache) == 0
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_prepare_task_resume_after_interruption(tmp_path):
    # 断点续传：缓存有部分组 → _prepare_translate_task 复用已翻译组，units 只含剩余
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        # 构造两段英文（各 3000 字符）+ 一段中文
        p1 = "a" * 3000
        p2 = "b" * 3000
        text = f"{p1}\n\n{p2}\n\n中文段落"
        key = _translate_para_cache_key(text)
        # 模拟第一次任务只完成了第 1 组（部分缓存）
        partial = [
            {"src": p1, "tgt": "甲", "skipped": False},
            {"src": p2, "tgt": "", "skipped": False},
            {"src": "中文段落", "tgt": "", "skipped": True},
        ]
        analysis._translate_cache[key] = {"groups": partial, "complete": False}
        prep = _prepare_translate_task(text)
        assert prep.get("skipped") is None  # 正常路径无 skipped 键
        assert prep["reused"] == 1
        assert prep["groups"][0]["tgt"] == "甲"
        # units 只含剩余英文组（中文组跳过）
        assert len(prep["units"]) == 1
        assert prep["units"][0][0] == 1
        # 全部完成后 → 直接 cached 命中
        complete = [dict(g) for g in prep["groups"]]
        complete[1]["tgt"] = "乙"
        analysis._translate_cache[key] = {"groups": complete, "complete": True}
        prep2 = _prepare_translate_task(text)
        assert prep2.get("cached") is True
        assert prep2["groups"][1]["tgt"] == "乙"
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_prepare_task_chinese_skipped():
    assert _prepare_translate_task("全是中文的文档").get("skipped") is True
    assert _prepare_translate_task("").get("skipped") is True


# ── force 重新翻译：忽略完整缓存与断点续传 ──

def test_prepare_task_force_skips_complete_cache(tmp_path):
    # 有完整缓存 + force=True → 不命中缓存，units 全量重建
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        text = "hello world\n\nsecond paragraph"
        key = _translate_para_cache_key(text)
        # 短文本不拆分：整段单组（与 _split_paragraph_groups 语义一致）
        groups = [{"src": text, "tgt": "你好世界，第二段", "skipped": False}]
        analysis._translate_cache[key] = {"groups": groups, "complete": True}
        # 普通调用命中缓存
        assert _prepare_translate_task(text).get("cached") is True
        # force 调用跳过缓存
        prep = _prepare_translate_task(text, force=True)
        assert prep.get("cached") is None or prep["cached"] is False
        assert prep["reused"] == 0
        assert len(prep["units"]) == 1
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_prepare_task_force_skips_resume(tmp_path):
    # 部分缓存（断点续传场景）+ force=True → 不复用已翻译组
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        p1 = "a" * 3000
        p2 = "b" * 3000
        text = f"{p1}\n\n{p2}"
        key = _translate_para_cache_key(text)
        analysis._translate_cache[key] = {"groups": [
            {"src": p1, "tgt": "甲", "skipped": False},
            {"src": p2, "tgt": "", "skipped": False},
        ], "complete": False}
        prep = _prepare_translate_task(text, force=True)
        assert prep["reused"] == 0
        assert len(prep["units"]) == 2
        assert prep["groups"][0]["tgt"] == ""
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_para_cache_delete(tmp_path):
    # 删除缓存条目并落盘（force 重翻前置清理）
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        key = "trp:del1"
        _para_cache_save_complete(key, [{"src": "a", "tgt": "甲", "skipped": False}])
        _para_cache_delete(key)
        assert key not in analysis._translate_cache
        # 磁盘同步删除
        with open(analysis._TRANSLATE_CACHE_FILE, "r", encoding="utf-8") as f:
            assert key not in json.load(f)
        _para_cache_delete("trp:not-exists")  # 不存在不抛错
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


# ── check_only：只查缓存/运行中任务，不启动新任务 ──

def test_start_check_only_idle_no_task_created(tmp_path):
    # 无缓存无运行任务 → idle，不创建任务不启动线程
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        with _tasks_lock:
            before = set(_translate_tasks.keys())
        resp = start_translate_task(TranslateRequest(text="hello world", check_only=True))
        assert resp["status"] == "idle"
        assert resp["cached"] is False
        with _tasks_lock:
            assert set(_translate_tasks.keys()) == before
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_start_check_only_cache_hit(tmp_path):
    # 有完整缓存 + check_only → 直接返回 done
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        text = "hello world"
        key = _translate_para_cache_key(text)
        _para_cache_save_complete(key, [{"src": "hello world", "tgt": "你好世界", "skipped": False}])
        resp = start_translate_task(TranslateRequest(text=text, check_only=True))
        assert resp["status"] == "done"
        assert resp["cached"] is True
        assert resp["groups"][0]["tgt"] == "你好世界"
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_start_check_only_running_task_resumed(tmp_path):
    # 无完整缓存但同文本有运行中任务 → 返回 task_id，前端可续轮询
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        text = "hello world"
        key = _translate_para_cache_key(text)
        with _tasks_lock:
            _translate_tasks["fake123"] = {
                "id": "fake123", "status": "running", "cache_key": key,
                "total": 3, "done_count": 1, "groups": [{"src": "a", "tgt": "", "skipped": False}],
            }
        try:
            resp = start_translate_task(TranslateRequest(text=text, check_only=True))
            assert resp["status"] == "running"
            assert resp["task_id"] == "fake123"
            assert resp["done_count"] == 1
        finally:
            with _tasks_lock:
                _translate_tasks.pop("fake123", None)
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


# ── 取消翻译 ──

def test_cancel_translate_task_api(tmp_path):
    # 取消接口：running → cancelled；不存在 → 404
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        with _tasks_lock:
            _translate_tasks["task1"] = {"id": "task1", "status": "running"}
        try:
            resp = cancel_translate_task("task1")
            assert resp["status"] == "cancelled"
            # 重复取消幂等
            resp2 = cancel_translate_task("task1")
            assert resp2["status"] == "cancelled"
            # 任务存在但已完成 → 保持原状态
            with _tasks_lock:
                _translate_tasks["task2"] = {"id": "task2", "status": "done"}
            resp3 = cancel_translate_task("task2")
            assert resp3["status"] == "done"
        finally:
            with _tasks_lock:
                _translate_tasks.pop("task1", None)
                _translate_tasks.pop("task2", None)
        from fastapi import HTTPException
        try:
            cancel_translate_task("no-such-task")
            assert False, "应抛 404"
        except HTTPException as e:
            assert e.status_code == 404
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_run_task_cancelled_keeps_partial(tmp_path, monkeypatch):
    # 任务启动前已取消 → 线程立即停止，缓存保持 partial（不写 complete）
    old_file = _isolate_cache_file(str(tmp_path))
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        key = "trp:cancel-run"
        groups = [{"src": "a", "tgt": "", "skipped": False}, {"src": "b", "tgt": "", "skipped": False}]
        with _tasks_lock:
            _translate_tasks["t-cancel"] = {
                "id": "t-cancel", "status": "cancelled", "total": 2, "done_count": 0,
                "groups": groups, "_units": [(0, 0, "a"), (1, 0, "b")], "_results": {}, "_sub_need": {0: 1, 1: 1},
            }
        try:
            _run_translate_task("t-cancel", key)
            with _tasks_lock:
                assert _translate_tasks["t-cancel"]["status"] == "cancelled"
            cached, complete = _para_cache_get(key)
            assert complete is False
            assert cached is not None
        finally:
            with _tasks_lock:
                _translate_tasks.pop("t-cancel", None)
    finally:
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False


def test_run_task_completion_order_top_down(tmp_path):
    # 滚动窗口顺序保证：即使“底部组翻得更快”，首先完成的组也必须来自文档顶部窗口内
    #（旧的一次性全提交逻辑下，首个完成的必是最快的末组 g11）
    import app.rag_app.llm_client_factory as factory
    old_file = _isolate_cache_file(str(tmp_path))
    old_translate = analysis._translate_unit_with_retry
    old_conc = analysis._TRANSLATE_CONCURRENCY
    old_client = factory.create_llm_client
    try:
        analysis._translate_cache_loaded = True
        analysis._translate_cache.clear()
        n = 12
        factory.create_llm_client = lambda: None
        def fake_translate(client, text, is_long):
            idx = int(text[1:])
            time.sleep(0.02 * (n - idx))  # g0 最慢、g11 最快（模拟底部先翻完）
            return f"T{text}"
        analysis._translate_unit_with_retry = fake_translate
        analysis._TRANSLATE_CONCURRENCY = 3
        groups = [{"src": f"g{i}", "tgt": "", "skipped": False} for i in range(n)]
        with _tasks_lock:
            _translate_tasks["t-order"] = {
                "id": "t-order", "status": "running", "total": n, "done_count": 0,
                "groups": groups, "_units": [(i, 0, f"g{i}") for i in range(n)],
                "_results": {}, "_sub_need": {i: 1 for i in range(n)},
            }
        try:
            _run_translate_task("t-order", "trp:order-test")
            with _tasks_lock:
                assert _translate_tasks["t-order"]["status"] == "done"
            # 所有组都有译文
            assert all(g["tgt"] for g in groups)
            # 首个完成的组必须在前 3 组窗口内（旧逻辑下会是 11）
            first_done = next(i for i, g in enumerate(groups) if g["tgt"])
            assert first_done <= 2, f"首个完成组 {first_done} 不在顶部窗口内"
        finally:
            with _tasks_lock:
                _translate_tasks.pop("t-order", None)
    finally:
        analysis._translate_unit_with_retry = old_translate
        analysis._TRANSLATE_CONCURRENCY = old_conc
        factory.create_llm_client = old_client
        analysis._TRANSLATE_CACHE_FILE = old_file
        analysis._translate_cache.clear()
        analysis._translate_cache_loaded = False
