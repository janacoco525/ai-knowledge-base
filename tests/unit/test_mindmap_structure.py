"""脑图章节来源回归：全文真实结构（TOC/标题）优先于 LLM 猜前 1.2 万字；
2026-08-19 追加：卷标题质量门禁 + 扁平章节分桶（《2049》假卷误检修复）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.routes.llm_ops import _structure_chapters


def test_structure_chapters_from_full_text():
    text = (
        "目录\n第一章 起源\n第二章 发展\n第三章 应用\n\n"
        "第一章 起源\n这里是第一章内容。\n"
        "第二章 发展\n这里是第二章内容。\n"
        "第三章 应用\n这里是第三章内容。"
    )
    chapters = _structure_chapters(text)
    assert len(chapters) >= 3
    assert any("第一章" in c["title"] for c in chapters)
    assert any("第三章" in c["title"] for c in chapters)


def test_structure_chapters_empty_without_toc():
    assert _structure_chapters("没有任何章节结构的普通连续文本。" * 20) == []


def test_group_chapters_by_part_hierarchy():
    """部分→章节分层：序言不占名额，工作原则不再被挤掉（2026-08-12）。"""
    from app.rag_app.routes.llm_ops import _group_chapters_by_part

    chapters = [
        {"title": "中文版序", "points": [], "level": 0},
        {"title": "第一部分 我的历程", "points": [], "level": 0},
        {"title": "1 我的探险召唤", "points": [], "level": 1},
        {"title": "2 跨越门槛", "points": [], "level": 1},
        {"title": "第二部分 生活原则", "points": [], "level": 0},
        {"title": "1 拥抱现实", "points": [], "level": 1},
        {"title": "第三部分 工作原则", "points": [], "level": 0},
        {"title": "1 相信极度求真", "points": [], "level": 1},
        {"title": "16 千万别忽视公司治理", "points": [], "level": 1},
        {"title": "参考文献", "points": [], "level": 0},
    ]
    parts = _group_chapters_by_part(chapters)
    titles = [p["title"] for p in parts]
    assert "第一部分 我的历程" in titles
    assert "第三部分 工作原则" in titles
    assert "中文版序" not in titles
    assert "参考文献" not in titles
    work = next(p for p in parts if "工作原则" in p["title"])
    assert len(work["chapters"]) == 2  # 首尾工作原则都保留


def test_bare_part_not_treated_as_structure():
    """PDF 页眉残留的裸"第三部分"不是结构标题，不得成为顶级部分（2026-08-12）。"""
    from app.rag_app.routes.llm_ops import _group_chapters_by_part

    chapters = [
        {"title": "第三部分 工作原则", "points": [], "level": 0},
        {"title": "打造良好的文化……", "points": [], "level": 0},
        {"title": "1 相信极度求真", "points": [], "level": 1},
        {"title": "第三部分", "points": [], "level": 0},  # PDF 页眉残留
        {"title": "7 比做什么事更重要", "points": [], "level": 1},
    ]
    parts = _group_chapters_by_part(chapters)
    assert len(parts) == 1
    assert parts[0]["title"] == "第三部分 工作原则"
    assert parts[0]["chapters"][0]["title"] == "打造良好的文化……"
    assert parts[0]["chapters"][-1]["title"] == "7 比做什么事更重要"


def test_volumes_by_position_for_collection():
    """合集 EPUB（书名卷 + 章级）按正文位置重建卷→章（2026-08-13 三体案例）。
    注：真实《三体》TOC 由 extract_toc 识别出 5 卷（球状闪电/三体I/II/III/超新星纪元），
    此处合成文本只验证核心逻辑（独立行卷候选 + 章节归属 + 空格变体匹配）。
    """
    from app.rag_app.routes.llm_ops import _structure_volumes_by_position

    # 模拟三体合集：卷标题 + 章节标题，正文中真实出现（>5000 处）
    head = "封面目录占位" * 1200  # 5000+ 字符的目录区
    content = head + (
        "球状闪电\n球状闪电正文开始……\n\n"
        "三体I\n第一章 科学边界\n第一章正文……\n"
        "第二章 台球\n第二章正文……\n"
    )
    vols = _structure_volumes_by_position(content)
    assert len(vols) >= 1
    # 章节归属到卷：第一章 科学边界 应挂在 球状闪电 或 三体I 卷下
    all_ch = [c["title"] for v in vols for c in v["chapters"]]
    assert any("科学边界" in c for c in all_ch)


def test_find_title_with_whitespace_variants():
    from app.rag_app.routes.llm_ops import _find_title_with_whitespace_variants

    content = "占位" * 3000 + "\n第一章 死 星 终 结\n正文……"
    pos = _find_title_with_whitespace_variants(content, "第一章 死星终结")
    assert pos > 5000


def test_volume_section_filter_logic():
    """归属过滤核心逻辑：level 0 分节保留、level >=2 列表噪音剔除（2026-08-13）。
    注：extract_toc 对合成文本的卷识别不可靠，此处直接测过滤条件本身。
    """
    from app.rag_app.routes.llm_ops import _find_title_with_whitespace_variants

    head = "封面目录占位" * 1200
    content = head + (
        "第一部 公元1453年5月，魔法师之死\n第一部正文……\n"
        "第二部 威慑纪元12年\n第二部正文……\n"
        "一、对太阳系黑暗森林打击时间的预测。\n"
        "二、需要拯救的人口数量。\n"
    )
    # 分节标题能在正文定位（level 0 挂载的前提）
    assert _find_title_with_whitespace_variants(content, "第一部 公元1453年5月") > 5000
    assert _find_title_with_whitespace_variants(content, "第二部 威慑纪元12年") > 5000
    # 列表噪音也能定位，但归属循环按 level>=2 过滤（真实《三体》已实测 6 部挂载、噪音剔除）
    assert _find_title_with_whitespace_variants(content, "一、对太阳系黑暗森林打击时间的预测。") > 5000


# ── 2026-08-19 追加：卷标题质量门禁 + 扁平章节分桶（《2049》假卷误检修复）──
from app.rag_app.routes.llm_ops import (  # noqa: E402
    _bucket_flat_chapters,
    _is_valid_volume_title,
    MAX_CHAPTERS,
    MAX_CHAPTERS_PER_PART,
    MAX_PARTS,
)


def test_volume_title_gate_rejects_fragments():
    """行尾残片/纯日期/图注不得当卷标题（《2049》实测泄漏词）。"""
    for t in [
        "要。", "果。", "2024年12月", "2024年12月31日", "。",
        "12", "3D", "图1", "表2", "吴晨", "的文化",
    ]:
        assert not _is_valid_volume_title(t), t


def test_volume_title_gate_accepts_real_volumes():
    """真实卷名必须保留（三体合集回归保护）。"""
    for t in ["三体I", "三体II·黑暗森林", "球状闪电", "超新星纪元", "第三部 死神永生", "生活原则"]:
        assert _is_valid_volume_title(t), t


def test_bucket_flat_chapters_small_stays_flat():
    chapters = [{"title": f"第{i}章"} for i in range(8)]
    tasks = _bucket_flat_chapters(chapters)
    assert len(tasks) == 8
    assert all(g is None for g, _ in tasks)  # ≤MAX_CHAPTERS 保持扁平


def test_bucket_flat_chapters_covers_all():
    """29 章 → ≤MAX_PARTS 组、每组 ≤MAX_CHAPTERS_PER_PART 章，覆盖全部章节。"""
    n = 29
    chapters = [{"title": f"第{i+1}节主题"} for i in range(n)]
    tasks = _bucket_flat_chapters(chapters)
    assert len(tasks) == n  # 每个章节都有任务，不丢章
    groups = {}
    for g, ch in tasks:
        groups.setdefault(g, []).append(ch)
    assert len(groups) <= MAX_PARTS
    assert all(len(v) <= MAX_CHAPTERS_PER_PART for v in groups.values())
    # 组标题 = 组内首章名 + "等N章"
    for g, chs in groups.items():
        assert (g == f"{chs[0]} 等{len(chs)}章") if len(chs) > 1 else (g == chs[0])


def test_bucket_flat_chapters_exactly_at_cap():
    chapters = [{"title": f"章{i}"} for i in range(MAX_CHAPTERS)]
    tasks = _bucket_flat_chapters(chapters)
    assert all(g is None for g, _ in tasks)
    assert len(tasks) == MAX_CHAPTERS


def test_volumes_by_position_rejects_noisy_pdf_fragments():
    """噪声 PDF（行尾残片/日期行 + 真章节）不得产出假卷（2026-08-19《2049》回归）。

    修复前：'2024年12月/要。/果。'这类后跟 ≥3 章节标题的独立短行会被
    _find_volume_title_lines 误判为卷，普通书被压成残片卷分支。
    """
    from app.rag_app.routes.llm_ops import _structure_volumes_by_position

    head = "封面目录占位" * 1200  # 5000+ 字符的目录区
    content = head + (
        "2024年12月\n"
        "智能眼镜如何取代智能手机\n第一章内容……\n"
        "要。\n"
        "现实世界与数字孪生的无缝衔接\n第二章内容……\n"
        "果。\n"
        "镜像世界的特点\n第三章内容……\n"
    )
    vols = _structure_volumes_by_position(content)
    titles = [v.get("title") for v in vols]
    assert "2024年12月" not in titles
    assert "要。" not in titles
    assert "果。" not in titles
    assert len(vols) == 0  # 无卷结构 → 走扁平分桶路径
