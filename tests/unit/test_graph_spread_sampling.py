"""单文件全书采样回归：图谱必须跨全书取块，不能只取开头前 N 块。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag_app.graph_selector import select_graph_source_chunks


def _make_chunks(n: int):
    return [
        {"text": f"chunk-{i}", "source_file": "book.md", "chunk_index": i, "source_chunk_id": f"c{i}"}
        for i in range(n)
    ]


def test_spread_single_file_covers_whole_book():
    chunks = _make_chunks(100)
    selected = select_graph_source_chunks(
        chunks,
        max_chunks=48,
        selection_profile="balanced",
        sorting_strategy="relevance",
        spread_single_file=True,
    )
    idxs = sorted(int(c["chunk_index"]) for c in selected)
    assert len(idxs) <= 36  # 数量受控
    assert idxs[0] <= 2      # 覆盖开头
    assert idxs[-1] >= 97    # 覆盖结尾（不再只取前 12 块）
    assert len(idxs) >= 20   # 且不是稀疏到失真


def test_no_spread_keeps_original_front_behavior():
    chunks = _make_chunks(100)
    selected = select_graph_source_chunks(
        chunks,
        max_chunks=48,
        selection_profile="balanced",
        sorting_strategy="relevance",
    )
    idxs = [int(c["chunk_index"]) for c in selected]
    # 原行为：单文件只取"最靠前的连续块"（前 48 块，全在开头区）——这正是序言噪音的来源
    assert idxs == list(range(48))


def test_spread_respects_cap_and_small_files():
    small = _make_chunks(5)
    selected = select_graph_source_chunks(
        small, max_chunks=48, selection_profile="balanced", spread_single_file=True
    )
    assert len(selected) == 5


def test_spread_by_chapters_covers_each_chapter():
    """章节分层采样：每章至少取到块，不再纯字符均匀（2026-08-12 轻量C）。"""
    from app.rag_app.graph_selector import _spread_by_chapters

    titles = ["第一章", "第二章", "第三章", "第四章", "第五章"]
    chunks = []
    for ch_i, title in enumerate(titles):
        # 每章 12 块，章首块带标题，标题在正文只出现一次
        chunks.append({
            "text": f"{title}\n本章开头内容。",
            "source_file": "book.md",
            "chunk_index": ch_i * 12,
            "source_chunk_id": f"ch{ch_i}",
        })
        for j in range(1, 12):
            chunks.append({
                "text": f"第{ch_i+1}章第{j}段内容。",
                "source_file": "book.md",
                "chunk_index": ch_i * 12 + j,
                "source_chunk_id": f"ch{ch_i}-{j}",
            })
    full_text = "\n".join(c["text"] for c in chunks)
    selected = _spread_by_chapters(chunks, full_text, titles, cap=20)
    assert len(selected) >= 5  # 至少覆盖 5 个章节区间
    assert len(selected) <= 20
    # 每章都有代表块：章首标题块所在 chunk 应被选中
    selected_titles = [c["text"].split("\n")[0] for c in selected]
    for title in titles:
        assert title in selected_titles
