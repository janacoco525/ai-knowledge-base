"""
LLM 图谱提取管线（LLM-first，渐进替换）
主路径：DeepSeek API 提取实体+关系
Fallback：规则提取（concept_extractor）降级为只出节点不出边
并行化：Fan-out/Fan-in 模式（参考 Graph Engineering §6）
"""
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 缓存目录
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "graph", "llm_cache")

# ⛔ 受约束关系 schema（2026-08-12，对标 KGGen arXiv 2502.09956）
# 关系类型固定为有限集合，禁止 LLM 自由生成动词 → 避免"关系类型爆炸"（966 类型 vs 981 边）
RELATION_SCHEMA = {
    "包含": ["包含", "包括", "涵盖", "由...组成", "由...构成", "分成", "分为", "由...产生"],
    "属于": ["属于", "隶属", "归属于", "归入", "是...一部分", "位列", "位于", "出生于", "创办于"],
    "导致": ["导致", "引起", "引发", "造成", "使得", "促成", "带来", "催生", "触发"],
    "支持": ["支持", "支撑", "佐证", "证明", "依据", "有助于", "促进", "强化"],
    "反对": ["反对", "反驳", "批判", "否定", "质疑", "挑战", "对立"],
    "实例": ["实例", "例如", "比如", "案例", "举例", "典型", "示范"],
    "提出": ["提出", "发明", "创建", "创立", "建立", "设计", "主张", "倡导", "建议", "首创", "创办"],
    "应用于": ["应用于", "适用于", "用于", "运用", "应用", "采用", "实施"],
    "影响": ["影响", "塑造", "改变", "决定", "制约", "驱动", "推动"],
    "相关": ["相关", "涉及", "提及", "联系", "关联", "有关", "关系到", "关于"],
}

# 实体同义映射（实体解析用，2026-08-12）：规范化后命中 → 合并为一个节点
LABEL_SYNONYMS = {
    "瑞达利欧": "达利欧",
    "雷达里奥": "达利欧",
    "raydalio": "达利欧",
}


# 并行提取支持
try:
    from core.parallel import parallel_map, TokenBucketRateLimiter
    _PARALLEL_AVAILABLE = True
except ImportError:
    _PARALLEL_AVAILABLE = False


def _get_llm_client():
    from app.rag_app.llm_client_factory import create_llm_client
    return create_llm_client()


def _call_llm_extract(text: str, max_nodes: int = 6, thesis: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """调 LLM 提取实体和关系，可用主旨引导"""
    client = _get_llm_client()

    thesis_context = ""
    if thesis and thesis.get("thesis"):
        frameworks = ", ".join(thesis.get("key_frameworks", []))
        thesis_context = f"""
文档背景信息：
- 领域：{thesis.get('domain', '未知')}
- 核心论点：{thesis.get('thesis', '未知')}
- 关键框架：{frameworks}

请基于以上背景，提取与此文档核心论点最相关的实体和关系。
优先提取核心概念、关键人物、重要组织，忽略边缘性示例和背景铺垫中的次要实体。
"""

    prompt = f"""请从以下中文文本中提取知识图谱信息。
{thesis_context}
要求：
1. 识别最重要的实体（人物、地点、概念、组织、事件、系统、工具），不超过{max_nodes}个
2. 每个实体必须指定精确分类：person（人名）/ location（地名）/ organization（机构组织）/ event（事件）/ concept（抽象概念）/ system（系统）/ tool（工具）
3. 必须识别实体之间的关系，生成尽可能多的边（至少{max_nodes}条），用具体动词描述（如"发明了"、"位于"、"属于"、"导致"、"出生于"、"创立了"），禁止用"相关""涉及"等模糊词
4. 只返回严格 JSON，格式：{{"nodes": [{{"label": "实体名", "category": "person"}}], "edges": [{{"from": "实体A", "to": "实体B", "label": "关系"}}]}}
5. category 必须用英文：person / location / organization / event / concept / system / tool
6. ⛔ 分类纪律（2026-08-10）：person 只能是人名（含中文人名或外文人名）。抽象概念、行为名词、方法论、原则、流程（如"原则""独立思考""极度透明""五步流程"）必须归为 concept，严禁标成 person。实体分类要多样：至少出现 3 种不同 category，不要全部归为 person。
7. ⛔ 分块级提炼（2026-08-18）：输入是全书/长文档的一个**顺序分块**（超长块内已含前/中/后三点采样）。只提取本块承载核心概念的实体与关系；若本块包含序言、译者序、致谢、出版信息等，忽略署名、人名罗列、出版社等噪音实体，除非它们承载核心概念。
8. ⛔ 概念优先（2026-08-12）：对书籍/方法论类文档，优先提炼核心概念、原则、框架、方法、流程（如"极度透明""五步流程""可信度加权"）；人物/机构节点合计占比不超过节点数的 30%，避免图谱变成"人物关系图"。
9. ⛔ 受约束关系类型（2026-08-12，对标 KGGen）：边 label **只能**从以下 10 个固定类型中选择：包含、属于、导致、支持、反对、实例、提出、应用于、影响、相关。禁止自造动词（如"发明了""出生于""创立了"），统一归入最近类型；禁止用"相关/涉及/提及"作为主要关系，仅在无法归入其他类型时用"相关"。
10. ⛔ 例证排除（2026-08-19）：仅作为举例/罗列出现的公司、产品、作品名（如"谷歌、亚马逊、微软……"式列举）一律不提取，除非该实体是本段论述的核心载体；organization/tool 类实体每段最多 2 个。

文本（分块内容，最长 12000 字）：
{text[:12000]}"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,   # 需容纳 max_nodes 个节点+同量级边的 JSON（修复：原 1500 易截断）
            timeout=90,        # 并行批处理时 30s 容易超时
        )
        raw = resp.choices[0].message.content or ""
        if getattr(resp.choices[0], "finish_reason", None) == "length":
            logger.warning("LLM graph output truncated (finish_reason=length), parse may degrade")
        # 提取 JSON —— 多重修复策略（按顺序尝试）
        result = _parse_llm_response(raw, max_nodes)
        if result:
            return result
        logger.warning("LLM graph extract: response unparseable (len=%d), tail=%r", len(raw), raw[-120:])
        return None
    except Exception as e:
        logger.warning("LLM graph extract failed: %s", e)
        return None


def _parse_llm_response(raw: str, max_nodes: int) -> Optional[Dict[str, Any]]:
    """从 LLM 响应中提取 JSON，4 重降级"""
    import re

    # 策略 1：直接匹配 {...} 块
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data.get("nodes"), list) and isinstance(data.get("edges"), list):
                return data
        except json.JSONDecodeError:
            pass

    # 策略 2：尝试修复常见 JSON 错误
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        fixed = _try_fix_json(m.group())
        if fixed:
            try:
                data = json.loads(fixed)
                if isinstance(data.get("nodes"), list) and isinstance(data.get("edges"), list):
                    return data
            except json.JSONDecodeError:
                pass

    # 策略 3：暴力抢救——从文本中正则提取 label: 字段
    nodes = _extract_labels_from_text(raw, max_nodes)
    edges = _extract_edges_from_text(raw)
    if nodes:
        return {"nodes": nodes, "edges": edges}

    return None


def _try_fix_json(broken: str) -> Optional[str]:
    """尝试修复常见 JSON 错误：尾逗号、缺引号、缺括号"""
    import re
    s = broken
    # 1. 移除对象/数组末尾的逗号
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # 2. 修复未转义的引号（把英文双引号在字符串内换为中文引号）
    #    简单方法：跳过此步
    # 3. 补齐未闭合的括号
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")
    if open_braces > 0:
        s += "}" * open_braces
    if open_brackets > 0:
        s += "]" * open_brackets
    return s


def _extract_labels_from_text(raw: str, max_nodes: int) -> list:
    """从 LLM 响应中正则提取 'label': 'xxx' 字段"""
    import re
    nodes = []
    # 匹配 "label": "实体名" 或 "label": '实体名'
    pattern = re.compile(r'"label"\s*:\s*["\']([^"\'}]+)["\']')
    seen = set()
    for m in pattern.finditer(raw):
        label = m.group(1).strip()
        if label and label not in seen and len(label) <= 30:
            seen.add(label)
            nodes.append({"label": label, "category": "concept"})
            if len(nodes) >= max_nodes:
                break
    return nodes


def _extract_edges_from_text(raw: str) -> list:
    """从 LLM 响应中正则提取 'from'/'to'/'label' 边"""
    import re
    edges = []
    # 尝试匹配 {from: 'A', to: 'B', label: 'X'} 模式
    pattern = re.compile(
        r'"from"\s*:\s*["\']([^"\'}]+)["\'].*?'
        r'"to"\s*:\s*["\']([^"\'}]+)["\'].*?'
        r'"label"\s*:\s*["\']([^"\'}]+)["\']',
        re.DOTALL
    )
    for m in pattern.finditer(raw):
        from_label, to_label, rel = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if from_label and to_label and rel and from_label != to_label:
            edges.append({"from": from_label, "to": to_label, "label": rel})
    return edges[:8]


def _canonical_label(label: str) -> str:
    """实体解析：NFKC 归一 + 去括号注释 + 去空白/间隔/连字符 + 同义映射。
    用于跨批合并（neo4j-graphrag entity resolution 思路），避免
    "达利欧 / 瑞·达利欧 / Ray Dalio" 变成三个孤立节点。
    """
    import re
    import unicodedata

    s = unicodedata.normalize("NFKC", label or "")
    s = re.sub(r"[（(][^）)]*[)）]", "", s)          # 去括号注释（瑞·达利欧(Ray Dalio) → 瑞·达利欧）
    s = re.sub(r"[\s·•_\-—　]+", "", s)              # 去空白/间隔号/连字符
    s = s.strip().lower()
    return LABEL_SYNONYMS.get(s, s)


def _normalize_relation(label: str) -> str | None:
    """把 LLM 自由关系动词映射到 schema 类型；无法映射返回 None（丢弃弱边）。"""
    if not label:
        return None
    s = _canonical_label(label)
    if not s:
        return None
    for canon, aliases in RELATION_SCHEMA.items():
        if s == canon or s in aliases:
            return canon
    # 子串匹配：别名包含于 label（如"可以导致""最终造成" → 导致）
    for canon, aliases in RELATION_SCHEMA.items():
        for alias in aliases:
            if alias and alias in s:
                return canon
    return None


def _apply_rank_weight(label: str, category: str, raw: float) -> float:
    """⛔ 2026-08-19：god node 排序类别修正——机构/例证实体降权，抽象概念加权。
    书籍类文档的核心是论点/框架/趋势（抽象概念），而非实体名单；
    此前英伟达/谷歌等例证公司靠出现频次霸榜，把核心概念挤出 36 节点。
    """
    if not label:
        return raw
    if category in ("organization", "机构", "组织", "公司") or _infer_category(label) == "organization":
        return raw * 0.7
    if _looks_like_abstract_concept(label):
        return raw * 1.15
    return raw


def _dedupe_and_rank(raw_nodes: list, raw_edges: list, max_nodes: int) -> tuple:
    """B 阶段：逐块提取 → 全局合并去重 + god nodes 排序。
    - 实体解析：canonical label 合并同名/别名节点，保留出现最多的展示名
    - 关系归一：schema 类型 + 同 (from,to) 计数取多数
    - god nodes：节点频次 + 边度加权排序，截断到 max_nodes（graphify 关键节点思路）
    """
    node_by_canon: dict = {}
    for n in raw_nodes:
        label = str(n.get("label", "")).strip()
        if not label:
            continue
        canon = _canonical_label(label)
        if canon not in node_by_canon:
            node_by_canon[canon] = {
                "label": label,
                "category": n.get("category", "concept"),
                "count": 0,
                "degree": 0,
            }
        node_by_canon[canon]["count"] += 1

    edge_tally: dict = {}
    for e in raw_edges:
        f = str(e.get("from", "") or "").strip()
        t = str(e.get("to", "") or "").strip()
        rel = str(e.get("label", "") or "").strip()
        if not f or not t or f == t:
            continue
        fk, tk = _canonical_label(f), _canonical_label(t)
        rn = _normalize_relation(rel)
        if not rn:
            continue
        key = (fk, tk, rn)
        edge_tally[key] = edge_tally.get(key, 0) + 1

    for (fk, tk, _rn), cnt in edge_tally.items():
        if fk in node_by_canon:
            node_by_canon[fk]["degree"] += cnt
        if tk in node_by_canon:
            node_by_canon[tk]["degree"] += cnt

    # god nodes：出现频次 + 半度加权排序，只保留最重要的前 max_nodes 个
    # ⛔ 2026-08-13：score 必须随节点输出，否则前端看不到"谁是关键点"。
    # 归一化到 0~1：raw = count + 0.5*degree，最大值归一为 1.0。
    # ⛔ 2026-08-19：类别修正——机构 ×0.7 降权（例证公司易霸榜），
    # 抽象概念 ×1.15 加权（书籍核心是论点框架而非实体名单）。
    max_raw = max(
        (_apply_rank_weight(v["label"], v["category"], v["count"] + 0.5 * v["degree"]) for v in node_by_canon.values()),
        default=1.0,
    )
    for v in node_by_canon.values():
        raw = _apply_rank_weight(v["label"], v["category"], v["count"] + 0.5 * v["degree"])
        v["score"] = round(raw / max_raw, 3) if max_raw > 0 else 0.0

    ranked = sorted(
        node_by_canon.values(),
        key=lambda v: (_apply_rank_weight(v["label"], v["category"], v["count"] + 0.5 * v["degree"]), v["count"]),
        reverse=True,
    )[:max_nodes]
    kept = {_canonical_label(r["label"]): r for r in ranked}

    # 边：两端必须在保留节点内；同 (from,to) 取出现最多的 schema 类型
    edge_best: dict = {}
    for (fk, tk, rn), cnt in edge_tally.items():
        if fk not in kept or tk not in kept:
            continue
        pair = (fk, tk)
        if pair not in edge_best or cnt > edge_best[pair][1]:
            edge_best[pair] = (rn, cnt)
    edges = [
        {
            "from": kept[fk]["label"],
            "to": kept[tk]["label"],
            "label": rn,
            "weight": min(1.0, 0.4 + 0.15 * cnt),
        }
        for (fk, tk), (rn, cnt) in edge_best.items()
    ]
    return list(kept.values()), edges


def _infer_category(label: str) -> str:
    """从实体名推断分类 — LLM 全返回 concept 时的兜底。
    2026-08-12 修复：原逻辑把所有 2-4 字纯中文都判成 person，
    导致"机器/设计/员工/用对人/塑造者"等明显非人名被标成人物。
    改为：仅【姓氏开头 或 带间隔号 或 外文转写特征】才算 person。
    """
    import re
    # 外文转写：带间隔号（瑞·达利欧）
    if "·" in label or "•" in label:
        return "person"
    try:
        from app.rag_app.concept_extractor import _COMMON_SURNAMES, _NOUN_SUFFIXES
        # 中文人名：姓氏开头 + 2-3 字 + 末字非名词后缀
        if (
            len(label) in (2, 3)
            and label[0] in _COMMON_SURNAMES
            and label[-1] not in _NOUN_SUFFIXES
        ):
            return "person"
    except Exception:
        pass
    # 外文音译常见字（格雷格/麦修/芭芭拉/乔布斯等 2-3 字转写）
    translit_chars = "格麦芭拉布斯乔修珍娜妮卡夫德赫金森顿雷凯利"
    if len(label) in (2, 3) and all(ch in translit_chars for ch in label):
        return "person"
    # 地名
    if re.search(r'(国|省|市|县|城|河|山|海|洋|洲|港|岛|峰|谷|路|街)', label):
        return "location"
    # 组织
    if re.search(r'(公司|集团|学会|协会|联盟|学院|大学|研究院|基金会|委员会|机构|组织|院|所|局|部|社)', label):
        return "organization"
    # 事件
    if re.search(r'(战争|革命|运动|事件|会议|条约|协议|计划|危机|灾难|洪水)', label):
        return "event"
    # 系统/工具
    if re.search(r'(系统|平台|工具|软件|引擎|框架|协议|模型|算法|机器)', label):
        return "tool"
    return "concept"


def _looks_like_abstract_concept(label: str) -> bool:
    """⛔ 2026-08-10 修复：判断 label 是否更像抽象概念而非人物。
    LLM 常把"原则/独立思考/极度透明"等抽象名词误标成 person。
    命中即应纠偏回 concept。
    2026-08-11：词表统一复用 concept_extractor 的模块级常量（单一来源，避免双词表漂移）。
    """
    if not label:
        return False
    # 含 · 的外国人名（如"瑞·达利欧"）不是抽象概念
    if "·" in label or "•" in label:
        return False
    try:
        from app.rag_app.concept_extractor import (
            _ABSTRACT_CONCEPT_SUFFIXES, _ABSTRACT_CONCEPT_WORDS,
        )
        # 精确命中抽象概念词（如"独立思考""极度透明"）
        if label in _ABSTRACT_CONCEPT_WORDS:
            return True
        # 命中抽象词尾（如"原则""方法""流程"）
        if any(suf in label for suf in _ABSTRACT_CONCEPT_SUFFIXES):
            return True
    except Exception:
        pass  # import 失败时退化到保守规则
    # 4 字以上纯中文非人名惯用 → 保守判为抽象（人物多为 2-3 字或带·）
    import re as _re
    is_cn = _re.fullmatch(r"[一-鿿]{2,8}", label) is not None
    if is_cn and len(label) >= 4 and label not in ("乔布斯", "巴菲特"):
        return True
    return False


def _looks_like_person_name(label: str) -> bool:
    """2026-08-12：判断 label 是否真的像人名（LLM 把概念标成 person 时的二次纠偏）。
    规则：带间隔号外文名 / 常见姓氏开头 2-3 字 / 外文音译用字。其余（机器、设计、
    员工、用对人、塑造者）一律不算人物 → 降级回 concept。
    """
    if not label:
        return False
    if "·" in label or "•" in label:
        return True
    try:
        from app.rag_app.concept_extractor import _COMMON_SURNAMES, _NOUN_SUFFIXES
        if (
            len(label) in (2, 3)
            and label[0] in _COMMON_SURNAMES
            and label[-1] not in _NOUN_SUFFIXES
        ):
            return True
    except Exception:
        pass
    translit_chars = "格麦芭拉布斯乔修珍娜妮卡夫德赫金森顿雷凯利"
    if len(label) in (2, 3) and all(ch in translit_chars for ch in label):
        return True
    return False


def _cache_path(text_hash: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{text_hash}.json")


def _compact_segment(seg: str, cap: int = 12000) -> str:
    """段内超长压缩（2026-08-18）：前 40% + 中间 30% + 后 30% 三点采样。
    保证每段头/中/尾都有代表内容，避免只取段首。"""
    if len(seg) <= cap:
        return seg
    # 三个分隔符本身占长度，先从 cap 里扣除，保证输出总长 ≤ cap
    separator = "\n\n……[中略]……\n\n"
    budget = cap - 3 * len(separator)
    head_n = int(budget * 0.4)
    mid_n = int(budget * 0.3)
    tail_n = budget - head_n - mid_n
    head = seg[:head_n]
    mid_start = (len(seg) - mid_n) // 2
    mid = seg[mid_start:mid_start + mid_n]
    tail = seg[-tail_n:]
    return head + separator + mid + separator + tail


def _build_long_text_sample(full_text: str) -> List[str]:
    """全书顺序分块采样（2026-08-18，替代旧 4 窗口采样）。

    - ≤12000 字：1 段（原文直出）
    - ≤30 万字：4 段；≤80 万字：6 段；更大：8 段
    每段 ≤12000 字（超长段内前/中/后 3 点采样），顺序均匀覆盖全书，
    保证中间章节的核心节点不再被跳过（《三体》128 万字旧版仅覆盖 3.7%）。
    """
    total = len(full_text)
    if total <= 12000:
        return [full_text]
    if total <= 300000:
        k = 4
    elif total <= 800000:
        k = 6
    else:
        k = 8
    seg_len = total / k
    parts = []
    for i in range(k):
        start = int(i * seg_len)
        end = total if i == k - 1 else int((i + 1) * seg_len)
        parts.append(_compact_segment(full_text[start:end]))
    return parts


def _call_llm_consolidate(nodes: list, edges: list, max_nodes: int, thesis: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """C 阶段：规则归并后再做 LLM 级"再总结"（2026-08-18）。

    解决分块提取的碎片化：同义/近义实体合并、边随实体改名、schema 类型统一、
    弱边清洗、跨全书保留最重要 max_nodes 节点（对标 docling-graph 的
    normalize→merge 与 GraphRAG map-reduce 社区归并思路）。
    失败/不可解析 → 返回 None，调用方保留规则归并结果（不阻塞、不降级）。
    """
    if not nodes:
        return None
    client = _get_llm_client()

    thesis_context = ""
    if thesis and thesis.get("thesis"):
        frameworks = ", ".join(thesis.get("key_frameworks", []))
        thesis_context = f"""
文档背景信息：
- 领域：{thesis.get('domain', '未知')}
- 核心论点：{thesis.get('thesis', '未知')}
- 关键框架：{frameworks}
"""
    payload_nodes = [
        {
            "label": str(n.get("label", "")).strip(),
            "category": n.get("category", "concept"),
            "count": int(n.get("count", 0)),
            "degree": int(n.get("degree", 0)),
        }
        for n in nodes[:max_nodes * 3]
        if str(n.get("label", "")).strip()
    ]
    payload_edges = [
        {"from": e.get("from", ""), "to": e.get("to", ""), "label": e.get("label", "")}
        for e in edges[:max_nodes * 6]
        if e.get("from") and e.get("to") and e.get("label")
    ]

    prompt = f"""下面是同一本书/长文档的多个分块提取结果合并后的实体与关系，请做最后的"再总结"。
{thesis_context}
要求：
1. 合并同义/近义实体为一个节点（如"叶文洁"与"叶文洁(红岸基地)"、同一人物的不同译名/写法），选择最规范、通用的展示名，category 取最贴切的一个。
2. ⛔ 去例证化（2026-08-19）：删除仅作为举例/罗列出现的公司、产品、作品名（如科技公司名单、产品清单），除非该实体承载全书核心论点；机构/人物节点合计不得超过 4 个，其中作者或核心理论提出者最多保留 2 个（如凯文·凯利提出镜像世界是锚点边，应保留）；若多个同类机构仅是例证，可合并为一个聚合节点（如"科技巨头"）。
3. 所有边跟随实体改名；边 label 只能从以下 10 个固定类型中选择：包含、属于、导致、支持、反对、实例、提出、应用于、影响、相关。删除重复边和弱边；"支持""相关"两类合计不得超过总边数的 25%，优先保留 包含/属于/导致/应用于/影响/提出/反对 等结构性关系。
4. 节点最多保留 {max_nodes} 个：优先保留跨分块重复出现、度最高、对全书核心论点最重要的节点；书的主题性核心概念（论点、框架、趋势）必须保留，不得被例证实体挤掉。
5. 合并后的图谱要覆盖全书各主要章节/部分的代表性核心概念，不得偏重开头。
6. 只返回严格 JSON，格式：{{"nodes": [{{"label": "实体名", "category": "person"}}], "edges": [{{"from": "实体A", "to": "实体B", "label": "关系"}}]}}
7. category 必须用英文：person / location / organization / event / concept / system / tool。

合并前的实体与关系：
{json.dumps({"nodes": payload_nodes, "edges": payload_edges}, ensure_ascii=False)}"""

    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4000,
            timeout=90,
        )
        raw = resp.choices[0].message.content or ""
        if getattr(resp.choices[0], "finish_reason", None) == "length":
            logger.warning("LLM graph consolidate output truncated (finish_reason=length)")
        result = _parse_llm_response(raw, max_nodes)
        if result and result.get("nodes"):
            return result
        logger.warning("LLM graph consolidate: response unparseable (len=%d)", len(raw))
        return None
    except Exception as e:
        logger.warning("LLM graph consolidate failed: %s", e)
        return None


def extract_llm_graph(chunks: List[Dict[str, Any]], max_nodes: int = 6, thesis: Optional[Dict[str, Any]] = None, use_parallel: bool = True) -> Optional[Dict[str, Any]]:
    """
    主入口：LLM 提取图谱。
    thesis: 文档主旨（可选），有则用主旨引导提取，无则降级到现有逻辑。
    use_parallel: 是否启用并行提取（Fan-out/Fan-in）
    """
    if not chunks:
        return None

    # 用全部 chunks 的文本拼接
    full_text = "\n".join(c.get("text", "") for c in chunks if c.get("text", ""))
    if len(full_text) < 50:
        return None

    # 全书顺序分块（2026-08-18 升级）：均匀分 K 段覆盖整本书，段内超长用前/中/后 3 点采样。
    # 旧版只采 4 个固定窗口（《三体》128 万字仅覆盖 3.7%），中间章节核心节点全被跳过。
    # 新流程：分段提取 → 规则归并（_dedupe_and_rank）→ LLM 级再总结（_call_llm_consolidate），
    # 对标 docling-graph（IBM）chunk→merge、GraphRAG map-reduce 递归归并。
    total_len = len(full_text)
    segments = _build_long_text_sample(full_text)
    logger.info("Graph extraction: %d chars → %d sequential segments", total_len, len(segments))

    # 缓存 key（修复：hash() 受 PYTHONHASHSEED 随机化影响，跨进程不一致→缓存永远 miss；
    # 改用 md5 保证稳定命中；key 含 max_nodes，避免不同节点规模的图谱复用同一缓存；
    # 2026-08-10 加 CAT_V2：category 纠偏 + prompt 分类纪律变更后旧缓存失效）
    # 2026-08-12 升 CAT_V3：缓存 key 从"前 200 字符"改为"整段采样文本"——
    # 否则全书均匀采样与旧"前 48 块"采样开头相同 → 永远命中旧序言结果，全书覆盖修复无效
    # 2026-08-13 CAT_V6：god score/degree/count 写入节点输出（旧缓存 score 全为 1.0）→ 旧缓存全失效
    # 2026-08-18 CAT_V7：全书顺序分块 + 归并 + LLM 再总结（采样方式与产出语义均变）→ 旧缓存全失效
    # 2026-08-19 CAT_V8：prompt 去例证化（机构≤4/弱边≤25%/例证排除）+ god node 类别加权 → 旧缓存全失效
    text_hash = hashlib.md5(f"CAT_V8|{max_nodes}|{full_text}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    cache_file = _cache_path(text_hash)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
            logger.info("LLM graph cache hit: %s", text_hash)
            return cached
        except Exception:
            pass

    # 分段提取 → 规则归并 → LLM 再总结（Fan-out/Fan-in）
    def _merge_segment_results(presults: list) -> Optional[Dict[str, Any]]:
        raw_nodes = []
        raw_edges = []
        for presult in presults:
            if presult and presult.get("nodes"):
                raw_nodes.extend(presult["nodes"])
            if presult and presult.get("edges"):
                raw_edges.extend(presult["edges"])
        if not (raw_nodes or raw_edges):
            return None
        # B 阶段：实体解析 + schema 归一 + god nodes。
        # 保留 2 倍 max_nodes 给 C 阶段再总结留判断余地，避免频次截断过早丢节点。
        d_nodes, d_edges = _dedupe_and_rank(raw_nodes, raw_edges, max_nodes * 2)
        logger.info(
            "Segment extraction merged: %d raw nodes → %d deduped, %d raw edges → %d schema edges",
            len(raw_nodes), len(d_nodes), len(raw_edges), len(d_edges),
        )
        # C 阶段：LLM 级再总结（仅多段文档；失败保留规则归并结果，不阻塞）
        if len(segments) > 1:
            consolidated = _call_llm_consolidate(d_nodes, d_edges, max_nodes, thesis)
            if consolidated:
                c_nodes, c_edges = _dedupe_and_rank(
                    consolidated.get("nodes", []), consolidated.get("edges", []), max_nodes
                )
                if c_nodes:
                    logger.info(
                        "LLM consolidate: %d nodes → %d, %d edges → %d",
                        len(d_nodes), len(c_nodes), len(d_edges), len(c_edges),
                    )
                    return {"nodes": c_nodes, "edges": c_edges}
            logger.info("LLM consolidate skipped/failed, keep rule-merged graph")
        return {"nodes": d_nodes, "edges": d_edges}

    if use_parallel and _PARALLEL_AVAILABLE and len(segments) > 1:
        logger.info("Using parallel LLM extraction (%d segments)", len(segments))
        # 限流器：保护 LLM API 配额
        rate_limiter = TokenBucketRateLimiter(rate=3.0, burst=2)

        def _extract_seg(seg_text: str):
            return _call_llm_extract(seg_text, max_nodes=max_nodes, thesis=thesis)

        try:
            par_result = parallel_map(
                _extract_seg,
                segments,
                max_workers=2,
                rate_limiter=rate_limiter,
            )
            # 可观测性：任何段失败都记 warning，全灭时记 error 并列出首批异常类型
            if par_result.errors:
                err_summary = "; ".join(f"seg{idx}: {type(exc).__name__}: {exc}" for idx, exc in par_result.errors[:3])
                if par_result.success_count == 0:
                    logger.error("Parallel graph extraction ALL %d segments failed: %s", len(par_result.errors), err_summary)
                else:
                    logger.warning("Parallel graph extraction partial failure (%d/%d): %s", len(par_result.errors), len(segments), err_summary)
            result = _merge_segment_results(par_result.results)
        except Exception as e:
            logger.warning("Parallel extraction failed, falling back to sequential: %s", e)
            result = _merge_segment_results(
                [_call_llm_extract(s, max_nodes=max_nodes, thesis=thesis) for s in segments]
            )
    else:
        # 串行调用
        result = _merge_segment_results(
            [_call_llm_extract(s, max_nodes=max_nodes, thesis=thesis) for s in segments]
        )

    # 兜底：全部分段都失败时，最后用原文头部再试一次（避免直接降级规则提取）
    if not result and len(segments) > 1:
        logger.warning("All segments failed, last-resort single call on text head")
        result = _call_llm_extract(full_text, max_nodes=max_nodes, thesis=thesis)
    
    if not result:
        return None

    # 串行路径同样做实体解析 + schema 归一 + god nodes（与并行路径统一）
    result["nodes"], result["edges"] = _dedupe_and_rank(
        result.get("nodes", []), result.get("edges", []), max_nodes
    )

    # 标准化
    _CAT_MAP = {
        "人物": "person", "地点": "location", "概念": "concept",
        "组织": "organization", "事件": "event", "系统": "system",
        "工具": "tool", "流程": "process", "技术": "tool",
        "理论": "concept", "作品": "concept", "机构": "organization",
        "国家": "location", "城市": "location", "公司": "organization",
    }

    nodes = []
    edges = []
    label_to_id = {}
    for i, n in enumerate(result.get("nodes", [])[:max_nodes]):
        label = str(n.get("label", "")).strip()
        if not label:
            continue
        nid = f"llm-node-{i}"
        label_to_id[label] = nid
        raw_cat = str(n.get("category", "concept")).strip().lower()
        category = _CAT_MAP.get(raw_cat, raw_cat)  # 中文→英文，已有英文则直通
        if category not in ("person", "event", "concept", "organization", "system", "tool", "process", "location"):
            category = "concept"
        # 如果 LLM 全返回 concept，用启发式推断
        if category == "concept":
            category = _infer_category(label)
        # ⛔ 2026-08-10 修复：LLM 可能把抽象概念误标为 person/event 等合法值，
        # 用 _looks_like_abstract_concept 精准纠偏（如"原则""极度透明"被 LLM 标成 person）
        if category in ("person", "event", "organization", "location", "process"):
            if _looks_like_abstract_concept(label):
                category = "concept"
        # ⛔ 2026-08-12：LLM 把"机器/设计/员工/用对人/塑造者"标成 person 时的兜底，
        # 不满足"像人名"规则 → 降级 concept（避免图谱变"人物关系图"）
        if category == "person" and not _looks_like_person_name(label):
            category = "concept"
        nodes.append({
            "id": nid,
            "label": label,
            "category": category,
            # ⛔ 2026-08-13：god score 贯穿链路（不再硬编码 1.0）
            "score": float(n.get("score", 1.0)),
            "degree": int(n.get("degree", 0)),
            "count": int(n.get("count", 0)),
            "source_file": chunks[0].get("source_file", ""),
            "source_chunk_ids": [c.get("source_chunk_id", "") for c in chunks],
        })

    for e in result.get("edges", [])[:max_nodes * 2]:  # 修复：原硬编码 [:20]，边数随节点数扩展
        from_label = str(e.get("from", "")).strip()
        to_label = str(e.get("to", "")).strip()
        rel = str(e.get("label", "")).strip()
        if not from_label or not to_label or not rel:
            continue
        edges.append({
            "id": f"llm-edge-{len(edges)}",
            "from": label_to_id.get(from_label, from_label),
            "to": label_to_id.get(to_label, to_label),
            "label": rel,
            "relation_type": "llm_extracted",
            "weight": float(e.get("weight", 0.8)),
            "source_file": chunks[0].get("source_file", ""),
            "source_chunk_ids": [c.get("source_chunk_id", "") for c in chunks],
        })

    payload = {
        "version": "graph-llm.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "extractor_mode": "llm-first",
            "chunk_count": len(chunks),
            "segment_count": len(segments),
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }

    # 写缓存
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Cache write failed: %s", e)

    return payload


def extract_graph_fallback(chunks: List[Dict[str, Any]], max_nodes: int = 6) -> Dict[str, Any]:
    """
    降级方案：规则提取（只出节点，不出语义边）。
    当 LLM 不可用时保证系统不崩。
    """
    from app.rag_app.concept_extractor import ConceptExtractor
    ext = ConceptExtractor()
    result = ext.extract_from_chunks(chunks, max_nodes=max_nodes, graph_mode="auto")
    # 修复（2026-08-06）：保留语义边（是/属于/和/在/交互等），
    # 只丢弃"同句"共现兜底边——此前反向过滤导致只剩无语义的"同句"边。
    edges = result.get("edges", [])
    semantic = [e for e in edges if e.get("relation_type") not in ("co_occurrence", None)]
    vague = [e for e in edges if e.get("relation_type") in ("co_occurrence", None) and e.get("label") not in (None, "", "同句")]
    result["edges"] = semantic + vague[:10]
    result["meta"]["extractor_mode"] = "rules-fallback"
    return result
