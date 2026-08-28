"""
AI知识库 - 图谱选材与排序辅助模块
把 live 图状态的 chunk 选材 / 排序逻辑从 KnowledgeBase 中抽离，
避免知识库类持续膨胀成图谱调度器。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List


def normalize_focus_value(text: str | None) -> str:
    return (text or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def score_focus_match(text: str | None, focus_concept: str | None) -> int:
    raw_text = text or ""
    raw_focus = (focus_concept or "").strip()
    if not raw_text or not raw_focus:
        return 0

    ascii_focus = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,31}", raw_focus) is not None
    if ascii_focus:
        lowered_text = raw_text.lower()
        lowered_focus = raw_focus.lower().strip()
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(lowered_focus)}(?![a-z0-9])")
        return len(pattern.findall(lowered_text))

    normalized_text = normalize_focus_value(raw_text)
    normalized_focus = normalize_focus_value(raw_focus)
    if not normalized_text or not normalized_focus:
        return 0
    return normalized_text.count(normalized_focus)


def parse_uploaded_at_timestamp(value: str | None) -> float:
    raw_value = (value or "").strip()
    if not raw_value:
        return 0.0
    try:
        return datetime.fromisoformat(raw_value).timestamp()
    except ValueError:
        return 0.0


def select_graph_source_chunks(
    chunks: List[Dict[str, Any]],
    *,
    max_chunks: int,
    selection_profile: str = "balanced",
    sorting_strategy: str = "relevance",
    focus_concept: str | None = None,
    spread_single_file: bool = False,
) -> List[Dict[str, Any]]:
    profile = (selection_profile or "balanced").strip().lower()
    if profile not in {"compact", "balanced", "wide"}:
        profile = "balanced"
    strategy = (sorting_strategy or "relevance").strip().lower()
    if strategy not in {"relevance", "recency", "diversity"}:
        strategy = "relevance"
    normalized_focus = normalize_focus_value(focus_concept)

    profile_config = {
        "compact": {"default_max": 24, "per_file_cap": 4, "diversity_cap": 2},
        "balanced": {"default_max": 48, "per_file_cap": 12, "diversity_cap": 4},
        "wide": {"default_max": 96, "per_file_cap": 24, "diversity_cap": 8},
    }[profile]

    effective_max = max(1, min(int(max_chunks or profile_config["default_max"]), 200))
    per_file_cap = profile_config["per_file_cap"]
    if strategy == "diversity":
        per_file_cap = profile_config["diversity_cap"]

    # ⛔ 2026-08-12 单文件全书展开：整本书图谱必须跨全书采样，
    # 否则按 chunk_index 升序只取前 per_file_cap 块（12 块），
    # LLM 只见序言 → 提炼出译者/人名/机构噪音，主体全丢。
    if spread_single_file and not normalized_focus:
        return _spread_single_file_chunks(chunks, cap=min(effective_max, 36))

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    ordered_files: List[str] = []
    for chunk in chunks:
        enriched = dict(chunk)
        focus_score = 0
        if normalized_focus:
            focus_score = score_focus_match(
                f"{enriched.get('source_file', '')} {enriched.get('text', '')}",
                focus_concept,
            )
        enriched["focus_score"] = focus_score
        enriched["uploaded_at_ts"] = parse_uploaded_at_timestamp(str(enriched.get("uploaded_at") or ""))
        source_file = str(enriched.get("source_file") or "unknown")
        if source_file not in grouped:
            grouped[source_file] = []
            ordered_files.append(source_file)
        grouped[source_file].append(enriched)

    for source_file in ordered_files:
        grouped[source_file].sort(
            key=lambda item: (
                -int(item.get("focus_score", 0)),
                -float(item.get("uploaded_at_ts", 0.0)),
                int(item.get("chunk_index", 0)),
                str(item.get("source_chunk_id", "")),
            )
        )

    if strategy == "recency":
        ordered_files.sort(
            key=lambda file_name: (
                -max((float(item.get("uploaded_at_ts", 0.0)) for item in grouped[file_name]), default=0.0),
                -max((int(item.get("focus_score", 0)) for item in grouped[file_name]), default=0),
                file_name,
            )
        )
    elif strategy == "diversity":
        ordered_files.sort(
            key=lambda file_name: (
                -max((int(item.get("focus_score", 0)) for item in grouped[file_name]), default=0),
                len(grouped[file_name]),
                -max((float(item.get("uploaded_at_ts", 0.0)) for item in grouped[file_name]), default=0.0),
                file_name,
            )
        )
    elif normalized_focus:
        ordered_files.sort(
            key=lambda file_name: (
                -max((int(item.get("focus_score", 0)) for item in grouped[file_name]), default=0),
                -max((float(item.get("uploaded_at_ts", 0.0)) for item in grouped[file_name]), default=0.0),
                file_name,
            )
        )
    else:
        ordered_files.sort(
            key=lambda file_name: (
                -max((float(item.get("uploaded_at_ts", 0.0)) for item in grouped[file_name]), default=0.0),
                file_name,
            )
        )

    selected: List[Dict[str, Any]] = []
    selected_per_file: Dict[str, int] = {source_file: 0 for source_file in ordered_files}
    cursor = 0
    while len(selected) < effective_max and ordered_files:
        source_file = ordered_files[cursor % len(ordered_files)]
        file_chunks = grouped[source_file]
        if file_chunks and selected_per_file[source_file] < per_file_cap:
            selected.append(file_chunks.pop(0))
            selected_per_file[source_file] += 1
        cursor += 1

        no_more_chunks = all(not file_chunks for file_chunks in grouped.values())
        all_caps_hit = all(
            selected_per_file[file_name] >= per_file_cap or not grouped[file_name]
            for file_name in ordered_files
        )
        if no_more_chunks or all_caps_hit:
            break

    if len(selected) < effective_max:
        remaining: List[Dict[str, Any]] = []
        for source_file in ordered_files:
            remaining.extend(grouped[source_file])
        for chunk in remaining:
            if len(selected) >= effective_max:
                break
            selected.append(chunk)

    return selected


def _spread_single_file_chunks(chunks: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """单文件全书均匀采样：按文档顺序跨全书取最多 cap 块（默认上限 36）。
    返回块覆盖 开头→中段→结尾，控制数量与时间。

    2026-08-12 升级（轻量 C，对标 Docling-Graph 结构感知分块）：
    优先按真实章节（TOC 标题）分层取块——每章至少覆盖，避免"章节多但某章零采样"；
    TOC 不可用时回退到纯字符均匀 spread。
    """
    if not chunks:
        return []
    ordered = sorted(
        chunks,
        key=lambda c: (
            int(c.get("chunk_index", 0) or 0),
            str(c.get("source_chunk_id", "") or ""),
        ),
    )
    n = len(ordered)
    cap = max(1, min(int(cap), n))
    if n <= cap:
        return ordered

    # ⛔ 结构感知分层（2026-08-12）：用项目已有 toc_extractor 提取章节标题，
    # 按"章节区间"为单位取块，保证每章有代表块（对标 Docling-Graph 结构解析在前）。
    try:
        from app.rag_app.toc_extractor import extract_toc

        full_text = "\n".join(c.get("text", "") or "" for c in ordered)
        toc_items = extract_toc(full_text)
        titles = [it.get("text", "") for it in toc_items if it.get("text", "")]
        chapter_selected = _spread_by_chapters(ordered, full_text, titles, cap)
        if chapter_selected:
            return chapter_selected
    except Exception:
        pass  # TOC 失败 → 回退均匀 spread

    step = (n - 1) / (cap - 1)
    indices = sorted({round(i * step) for i in range(cap)})
    return [ordered[i] for i in indices]


def _spread_by_chapters(
    ordered: List[Dict[str, Any]], full_text: str, titles: List[str], cap: int
) -> List[Dict[str, Any]]:
    """按章节区间分层取块：每章均匀取 per_chapter 块，未满 cap 用全局均匀补齐。
    标题定位用**最后一次出现**（跳过正文前的"目录"区，避免所有标题都命中前几块）。
    """
    if not titles or len(titles) < 2:
        return []

    # 每块在 full_text 中的字符区间（用于把标题位置映射到 chunk 序号）
    # ⚠️ full_text 由 "\n".join 构造：偏移量必须计入每个分隔符，否则后续章节整体错位
    offsets = []
    pos = 0
    for i, c in enumerate(ordered):
        t = c.get("text", "") or ""
        offsets.append((pos, pos + len(t)))
        pos += len(t) + 1  # +1 = 与 "\n".join 对齐的分隔符

    boundaries = []
    seen = set()
    for title in titles:
        idx = full_text.rfind(title)
        if idx < 0:
            continue
        ci = next((i for i, (s, e) in enumerate(offsets) if s <= idx < e), None)
        if ci is not None and ci not in seen:
            seen.add(ci)
            boundaries.append(ci)
    boundaries.sort()
    if len(boundaries) < 2:
        return []

    # 章节区间：前缀 + 各标题区间 + 后缀
    starts = [0] + boundaries
    ends = boundaries + [len(ordered)]
    spans = [(s, e) for s, e in zip(starts, ends) if e - s >= 1]

    per_chapter = max(1, cap // max(1, len(spans)))
    selected_idx: List[int] = []
    for s, e in spans:
        if len(selected_idx) >= cap:
            break
        m = e - s
        take = min(per_chapter, m)
        if take >= m:
            picked = list(range(s, e))
        elif take <= 1:
            picked = [s]
        else:
            step = (m - 1) / (take - 1)
            picked = sorted({s + round(i * step) for i in range(take)})
        for i in picked:
            if len(selected_idx) >= cap:
                break
            if i not in selected_idx:
                selected_idx.append(i)

    # 未满 cap：用全局均匀补齐
    if len(selected_idx) < cap:
        remaining = [i for i in range(len(ordered)) if i not in selected_idx]
        if remaining:
            need = cap - len(selected_idx)
            if need >= len(remaining):
                fill = remaining
            elif need <= 1:
                fill = [remaining[0]]
            else:
                step = (len(remaining) - 1) / (need - 1)
                fill = [remaining[round(i * step)] for i in range(need)]
            selected_idx.extend(fill)

    picked_sorted = sorted(set(selected_idx))[:cap]
    return [ordered[i] for i in picked_sorted]
