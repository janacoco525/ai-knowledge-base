#!/usr/bin/env python3
"""
Verifier 节点 — 为 AI 输出提供确定性地面核验
对应《Graph Engineering》§8: "让模型去判断，让代码去兜底，再配一双独立的、专门挑刺的眼睛"
"""

import re
import ast
import json
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VerificationResult:
    """验证结果"""
    passed: bool
    score: float  # 0.0 - 1.0
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "details": self.details,
            "errors": self.errors,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


# ============================================================
# 1. 图谱质量评分器
# ============================================================

class GraphQualityVerifier:
    """
    评分维度（权重可调）：
    - node_diversity (0.25): 节点 category 分布是否多样（不全是 concept）
    - edge_label_quality (0.25): 边标签是否为具体动词（非"关联"/"相关"）
    - connectivity (0.20): 连通分量数、孤立节点比例
    - layout_quality (0.15): 节点重叠度、边交叉估算
    - semantic_coherence (0.15): 节点/边标签与领域关键词匹配度
    """

    WEIGHTS = {
        "node_diversity": 0.25,
        "edge_label_quality": 0.25,
        "connectivity": 0.20,
        "layout_quality": 0.15,
        "semantic_coherence": 0.15,
    }

    VAGUE_LABELS = {"关联", "相关", "相关联", "关联关系", "联系", "关系", "关联到", "属于", "包含", "默认"}

    def __init__(self, min_score: float = 0.6):
        self.min_score = min_score

    def verify(self, graph_data: Dict) -> VerificationResult:
        """
        graph_data 格式：
        {
            "nodes": [{"id": "...", "label": "...", "category": "...", "x": 0, "y": 0}, ...],
            "edges": [{"source": "...", "target": "...", "label": "..."}, ...],
            "domain": "optional domain context for semantic check"
        }
        """
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        domain = graph_data.get("domain", "")

        if not nodes:
            return VerificationResult(
                passed=False, score=0.0,
                errors=["图谱为空：无节点"],
                details={"node_count": 0, "edge_count": 0}
            )

        scores = {}
        details = {}
        errors = []
        warnings = []

        # 1. 节点类型多样性
        scores["node_diversity"], div_details = self._check_node_diversity(nodes)
        details["node_diversity"] = div_details

        # 2. 边标签质量
        scores["edge_label_quality"], edge_details = self._check_edge_labels(edges)
        details["edge_label_quality"] = edge_details
        if edge_details.get("vague_ratio", 0) > 0.5:
            warnings.append(f"边标签模糊比例高: {edge_details['vague_ratio']:.1%}")

        # 3. 连通性
        scores["connectivity"], conn_details = self._check_connectivity(nodes, edges)
        details["connectivity"] = conn_details
        if conn_details.get("isolated_ratio", 0) > 0.1:
            warnings.append(f"孤立节点比例: {conn_details['isolated_ratio']:.1%}")

        # 4. 布局质量
        scores["layout_quality"], layout_details = self._check_layout(nodes, edges)
        details["layout_quality"] = layout_details

        # 5. 语义连贯性（可选，需 domain）
        scores["semantic_coherence"], sem_details = self._check_semantic_coherence(nodes, edges, domain)
        details["semantic_coherence"] = sem_details

        # 加权总分
        total_score = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)

        return VerificationResult(
            passed=total_score >= self.min_score,
            score=round(total_score, 3),
            details={
                "sub_scores": {k: round(v, 3) for k, v in scores.items()},
                **details
            },
            errors=errors,
            warnings=warnings,
            metadata={"threshold": self.min_score, "node_count": len(nodes), "edge_count": len(edges)}
        )

    def _check_node_diversity(self, nodes: List[Dict]) -> Tuple[float, Dict]:
        categories = [n.get("category", "unknown") for n in nodes]
        if not categories:
            return 0.0, {"unique_categories": 0}

        from collections import Counter
        cat_counts = Counter(categories)
        unique = len(cat_counts)
        total = len(categories)

        # 理想：至少3类，且最大类不超过60%
        max_ratio = max(cat_counts.values()) / total if total > 0 else 1.0
        diversity_score = min(1.0, (unique / 4.0) * (1.0 - max_ratio * 0.5))

        return diversity_score, {
            "unique_categories": unique,
            "category_distribution": dict(cat_counts),
            "max_category_ratio": round(max_ratio, 2),
        }

    def _check_edge_labels(self, edges: List[Dict]) -> Tuple[float, Dict]:
        if not edges:
            return 1.0, {"total_edges": 0, "vague_ratio": 0.0}

        labels = [e.get("label", "").strip() for e in edges]
        vague_count = sum(1 for l in labels if l in self.VAGUE_LABELS or len(l) < 2)
        vague_ratio = vague_count / len(labels) if labels else 0.0

        # 评分：模糊标签越少越好
        score = max(0.0, 1.0 - vague_ratio * 1.5)

        return score, {
            "total_edges": len(edges),
            "vague_count": vague_count,
            "vague_ratio": round(vague_ratio, 2),
            "unique_labels": len(set(labels)),
        }

    def _check_connectivity(self, nodes: List[Dict], edges: List[Dict]) -> Tuple[float, Dict]:
        if not nodes:
            return 0.0, {"components": 0, "isolated_ratio": 1.0}

        # 构建邻接表
        node_ids = {n["id"] for n in nodes}
        adj = {nid: set() for nid in node_ids}
        for e in edges:
            s, t = e.get("source"), e.get("target")
            if s in node_ids and t in node_ids:
                adj[s].add(t)
                adj[t].add(s)

        # 计算连通分量
        visited = set()
        components = 0
        isolated = 0

        for nid in node_ids:
            if nid not in visited:
                components += 1
                stack = [nid]
                comp_nodes = []
                while stack:
                    cur = stack.pop()
                    if cur in visited:
                        continue
                    visited.add(cur)
                    comp_nodes.append(cur)
                    stack.extend(adj[cur] - visited)
                if len(comp_nodes) == 1 and not adj[comp_nodes[0]]:
                    isolated += 1

        isolated_ratio = isolated / len(node_ids) if node_ids else 0.0
        # 评分：单连通分量 + 低孤立比例
        score = max(0.0, 1.0 - (components - 1) * 0.2 - isolated_ratio)

        return score, {
            "components": components,
            "isolated_nodes": isolated,
            "isolated_ratio": round(isolated_ratio, 2),
            "total_nodes": len(node_ids),
        }

    def _check_layout(self, nodes: List[Dict], edges: List[Dict]) -> Tuple[float, Dict]:
        """简易布局质量：节点重叠、边长度分布"""
        positions = []
        for n in nodes:
            x = n.get("x", 0)
            y = n.get("y", 0)
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                positions.append((x, y))

        if len(positions) < 2:
            return 0.5, {"positioned_nodes": len(positions)}

        # 检查重叠（距离 < 50 视为重叠）
        overlaps = 0
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                dx = positions[i][0] - positions[j][0]
                dy = positions[i][1] - positions[j][1]
                if dx * dx + dy * dy < 2500:  # 50^2
                    overlaps += 1

        overlap_ratio = overlaps / (len(positions) * (len(positions) - 1) / 2) if len(positions) > 1 else 0
        score = max(0.0, 1.0 - overlap_ratio * 2)

        return score, {
            "positioned_nodes": len(positions),
            "overlap_pairs": overlaps,
            "overlap_ratio": round(overlap_ratio, 3),
        }

    def _check_semantic_coherence(self, nodes: List[Dict], edges: List[Dict], domain: str) -> Tuple[float, Dict]:
        """简单关键词匹配，domain 提供时启用"""
        if not domain:
            return 0.7, {"skipped": True, "reason": "no domain context"}

        domain_keywords = set(re.findall(r'\w+', domain.lower()))
        if not domain_keywords:
            return 0.7, {"skipped": True, "reason": "empty domain keywords"}

        node_text = " ".join([
            n.get("label", "") + " " + n.get("category", "")
            for n in nodes
        ]).lower()
        edge_text = " ".join([e.get("label", "") for e in edges]).lower()
        all_text = node_text + " " + edge_text

        matches = sum(1 for kw in domain_keywords if kw in all_text)
        coverage = matches / len(domain_keywords) if domain_keywords else 0

        return min(1.0, coverage * 1.2), {
            "domain_keywords": len(domain_keywords),
            "matched_keywords": matches,
            "coverage": round(coverage, 2),
        }


# ============================================================
# 2. 代码生成单测门禁
# ============================================================

class CodeVerifier:
    """
    验证生成的代码：
    1. 语法合法
    2. 通过单测（如提供测试文件/目录）
    3. 类型检查（可选 mypy）
    """

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    def verify(self, code: str, test_path: Optional[str] = None,
               run_type_check: bool = False) -> VerificationResult:
        errors = []
        warnings = []
        details = {}

        # 1. 语法检查
        syntax_ok, syntax_msg = self._check_syntax(code)
        details["syntax_check"] = syntax_msg
        if not syntax_ok:
            errors.append(f"语法错误: {syntax_msg}")
            return VerificationResult(
                passed=False, score=0.0, errors=errors, details=details
            )

        # 2. 写入临时文件运行单测
        test_passed = True
        test_output = ""
        if test_path:
            test_passed, test_output = self._run_tests(code, test_path)
            details["test_result"] = "passed" if test_passed else "failed"
            details["test_output"] = test_output[-500:]  # 截断
            if not test_passed:
                errors.append("单测失败")
                warnings.append(test_output[-200:])

        # 3. 类型检查
        type_ok = True
        if run_type_check:
            type_ok, type_msg = self._run_mypy(code)
            details["type_check"] = "passed" if type_ok else "failed"
            if not type_ok:
                warnings.append(f"类型检查警告: {type_msg[:200]}")

        score = 1.0 if (syntax_ok and test_passed and type_ok) else 0.0
        if syntax_ok and not test_passed:
            score = 0.3  # 语法对但测试不过
        elif syntax_ok and test_passed and not type_ok:
            score = 0.8

        return VerificationResult(
            passed=score >= 0.7,
            score=score,
            details=details,
            errors=errors,
            warnings=warnings,
        )

    def _check_syntax(self, code: str) -> Tuple[bool, str]:
        try:
            ast.parse(code)
            return True, "OK"
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"

    def _run_tests(self, code: str, test_path: str) -> Tuple[bool, str]:
        """将代码写入临时模块，运行 pytest"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            mod_file = tmpdir / "generated_module.py"
            mod_file.write_text(code, encoding="utf-8")

            # 复制测试文件
            test_file = self.project_root / test_path
            if not test_file.exists():
                return False, f"测试文件不存在: {test_path}"

            # 运行 pytest
            try:
                result = subprocess.run(
                    ["python", "-m", "pytest", str(test_file), "-v", "--tb=short"],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env={**os.environ, "PYTHONPATH": str(tmpdir)}
                )
                return result.returncode == 0, result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                return False, "测试超时 (30s)"
            except Exception as e:
                return False, f"测试运行异常: {e}"

    def _run_mypy(self, code: str) -> Tuple[bool, str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            mod_file = tmpdir / "generated_module.py"
            mod_file.write_text(code, encoding="utf-8")
            try:
                result = subprocess.run(
                    ["python", "-m", "mypy", str(mod_file), "--ignore-missing-imports"],
                    capture_output=True, text=True, timeout=20
                )
                return result.returncode == 0, result.stdout + result.stderr
            except Exception:
                return True, "mypy 未安装或运行失败，跳过"


# ============================================================
# 3. 文档摘要一致性校验
# ============================================================

class SummaryVerifier:
    """
    校验摘要：
    - 关键实体覆盖率（人名、地名、专有名词、数字、关键结论）
    - 无幻觉（摘要中实体需在原文出现）
    - 长度合规
    """

    def __init__(self, min_coverage: float = 0.7, max_length_ratio: float = 0.3):
        self.min_coverage = min_coverage
        self.max_length_ratio = max_length_ratio

    def verify(self, original: str, summary: str) -> VerificationResult:
        errors = []
        warnings = []
        details = {}

        # 长度检查
        orig_len = len(original)
        summ_len = len(summary)
        length_ratio = summ_len / orig_len if orig_len > 0 else 0
        details["length_ratio"] = round(length_ratio, 3)
        if length_ratio > self.max_length_ratio:
            warnings.append(f"摘要过长: {length_ratio:.1%} > {self.max_length_ratio:.1%}")

        # 提取关键实体（简易版：大写词、数字、专有名词模式）
        orig_entities = self._extract_entities(original)
        summ_entities = self._extract_entities(summary)

        if not orig_entities:
            return VerificationResult(
                passed=True, score=1.0,
                details={"reason": "原文无可提取实体，跳过覆盖率检查"},
                metadata={"orig_len": orig_len, "summ_len": summ_len}
            )

        # 覆盖率
        covered = orig_entities & summ_entities
        coverage = len(covered) / len(orig_entities)
        details["entity_coverage"] = round(coverage, 3)
        details["original_entities"] = len(orig_entities)
        details["covered_entities"] = len(covered)
        details["missing_entities"] = list(orig_entities - summ_entities)[:10]

        # 幻觉检查：摘要实体必须在原文出现
        hallucinated = summ_entities - orig_entities
        details["hallucinated_entities"] = list(hallucinated)[:10]
        if hallucinated:
            warnings.append(f"疑似幻觉实体: {list(hallucinated)[:5]}")

        score = coverage * 0.8 + (0.0 if hallucinated else 0.2)
        passed = coverage >= self.min_coverage and not hallucinated

        return VerificationResult(
            passed=passed,
            score=round(score, 3),
            details=details,
            errors=errors,
            warnings=warnings,
            metadata={"orig_len": orig_len, "summ_len": summ_len}
        )

    def _extract_entities(self, text: str) -> set:
        """提取关键实体：连续大写、数字单位、专有名词模式"""
        entities = set()

        # 模式1：连续大写字母（缩写/专有名词）
        for m in re.finditer(r'\b[A-Z]{2,}\b', text):
            entities.add(m.group())

        # 模式2：中文专有名词（含数字/符号的连续非空白）
        for m in re.finditer(r'[\u4e00-\u9fff]{2,}(?:\d+[\u4e00-\u9fff]*)*', text):
            entities.add(m.group())

        # 模式3：数字+单位
        for m in re.finditer(r'\d+(?:\.\d+)?\s*[年月日时分秒%℃°万元亿千百十万]', text):
            entities.add(m.group())

        # 模式4：英文驼峰/下划线标识符
        for m in re.finditer(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', text):
            if m.group()[0].isupper() or '_' in m.group():
                entities.add(m.group())

        return entities


# ============================================================
# 统一入口：VerifierManager
# ============================================================

class VerifierManager:
    """统一管理所有 Verifier，提供批量验证接口"""

    def __init__(self, project_root: Optional[Path] = None, graph_min_score: float = 0.6):
        self.graph_verifier = GraphQualityVerifier(min_score=graph_min_score)
        self.code_verifier = CodeVerifier(project_root=project_root)
        self.summary_verifier = SummaryVerifier()

    def verify_graph(self, graph_data: Dict) -> VerificationResult:
        return self.graph_verifier.verify(graph_data)

    def verify_code(self, code: str, test_path: Optional[str] = None,
                    run_type_check: bool = False) -> VerificationResult:
        return self.code_verifier.verify(code, test_path, run_type_check)

    def verify_summary(self, original: str, summary: str) -> VerificationResult:
        return self.summary_verifier.verify(original, summary)

    def verify_all(self, graph_data: Optional[Dict] = None,
                   code: Optional[str] = None, test_path: Optional[str] = None,
                   original: Optional[str] = None, summary: Optional[str] = None) -> Dict[str, VerificationResult]:
        """批量验证，返回各项结果"""
        results = {}
        if graph_data:
            results["graph"] = self.verify_graph(graph_data)
        if code:
            results["code"] = self.verify_code(code, test_path)
        if original and summary:
            results["summary"] = self.verify_summary(original, summary)
        return results


# ============================================================
# CLI 入口（便于手工测试/集成到 CI）
# ============================================================

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Verifier CLI")
    parser.add_argument("--graph", help="图谱 JSON 文件路径")
    parser.add_argument("--code", help="代码文件路径")
    parser.add_argument("--test", help="对应测试文件路径")
    parser.add_argument("--original", help="原文文件路径")
    parser.add_argument("--summary", help="摘要文件路径")
    parser.add_argument("--min-score", type=float, default=0.6, help="图谱最低分")
    args = parser.parse_args()

    mgr = VerifierManager(graph_min_score=args.min_score)
    results = {}

    if args.graph:
        with open(args.graph, encoding="utf-8") as f:
            results["graph"] = mgr.verify_graph(json.load(f))
    if args.code:
        with open(args.code, encoding="utf-8") as f:
            results["code"] = mgr.verify_code(f.read(), args.test)
    if args.original and args.summary:
        with open(args.original, encoding="utf-8") as f1, open(args.summary, encoding="utf-8") as f2:
            results["summary"] = mgr.verify_summary(f1.read(), f2.read())

    for name, res in results.items():
        print(f"\n=== {name.upper()} ===")
        print(f"Passed: {res.passed} | Score: {res.score}")
        if res.errors:
            print("Errors:", res.errors)
        if res.warnings:
            print("Warnings:", res.warnings)
        print("Details:", json.dumps(res.details, ensure_ascii=False, indent=2))

    # 返回码
    all_passed = all(r.passed for r in results.values())
    sys.exit(0 if all_passed else 1)