#!/usr/bin/env python3
"""
AI知识库 — 熵减审计脚本 (E1~E9)
检查项目结构健康度，识别熵增信号（冗余文件、孤儿文档、版本漂移、老化产物等）

用法:
  python scripts/entropy_audit.py          # 人类可读输出
  python scripts/entropy_audit.py --json   # JSON 输出（供 start.py --health 调用）
"""

import sys
import json
import re
import io
from pathlib import Path

# Windows GBK 编码兼容
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

PROJECT_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_DIR / "docs"
MANIFEST_PATH = DOCS_DIR / "foundation.manifest.json"
ARCHIVE_DIR = DOCS_DIR / "_archive"
FRONTEND_DIR = PROJECT_DIR / "frontend"
FRONTEND_SCAN_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css"}
FRONTEND_SKIP_PARTS = {"node_modules", "dist", "_bak_archive", "archive", "legacy", "public"}
NODE_BUILTINS = {"assert", "buffer", "canvas", "crypto", "fs", "http", "https", "path", "stream", "url", "util", "zlib"}

# Project-specific paths
RAG_APP_DIR = PROJECT_DIR / "app" / "rag_app"
ARCHIVE_MARKER_RE = re.compile(
    r"ARCHIVED|归档|已归档|历史参考|禁止作为执行依据|不作为当前执行依据",
    re.IGNORECASE,
)
ARCHIVE_POINTER_RE = re.compile(r"活跃版本(?:在|见)\s*`([^`]+)`")


def _read(path: Path) -> str:
    """读取文件，统一 UTF-8"""
    return path.read_text(encoding="utf-8")


def _load_allowed_root_md_docs() -> set[str]:
    """读取根目录白名单，和 foundation_audit 保持同一真值。"""
    defaults = {
        "VERSION.md",
        "项目真值.md",
        "术语表.md",
        "数据迁移指南.md",
    }

    if not MANIFEST_PATH.exists():
        return defaults

    try:
        manifest = json.loads(_read(MANIFEST_PATH))
    except json.JSONDecodeError:
        return defaults

    allowed = {
        Path(name).name
        for name in manifest.get("allowedRootDocs", [])
        if str(name).lower().endswith(".md")
    }
    return allowed or defaults


def _normalize_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 2:
        parts.append("0")
    return ".".join(parts[:3])


def _iter_frontend_scan_files():
    """遍历前端源文件与构建配置，排除归档/产物目录。"""
    if not FRONTEND_DIR.exists():
        return

    for path in FRONTEND_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in FRONTEND_SCAN_SUFFIXES:
            continue
        if path.name.endswith(".bak"):
            continue

        relative_parts = set(path.relative_to(FRONTEND_DIR).parts)
        if relative_parts & FRONTEND_SKIP_PARTS:
            continue
        yield path


def _normalize_package_name(specifier: str) -> str | None:
    """把 import specifier 归一到 npm 包名级别。"""
    if not specifier:
        return None

    if specifier.startswith((".", "/", "node:", "http:", "https:", "data:", "virtual:")):
        return None
    if specifier == "@" or specifier.startswith("@/"):
        return None
    if specifier in NODE_BUILTINS:
        return None

    if specifier.startswith("@"):
        parts = specifier.split("/")
        if len(parts) >= 2:
            return "/".join(parts[:2])
        return specifier

    return specifier.split("/")[0]


def _extract_external_imports(content: str) -> set[str]:
    """从 TS/JS 文本中提取外部依赖包名。"""
    patterns = [
        r'(?:import|export)\s+(?:[^"\']+?\s+from\s+)?["\']([^"\']+)["\']',
        r'import\(\s*["\']([^"\']+)["\']\s*\)',
        r'require\(\s*["\']([^"\']+)["\']\s*\)',
        r'@import\s+["\']([^"\']+)["\']',
    ]

    packages: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, content):
            normalized = _normalize_package_name(match)
            if normalized:
                packages.add(normalized)
    return packages


def _iter_active_docs():
    """遍历 active docs 层，排除 _archive。"""
    for path in DOCS_DIR.rglob("*.md"):
        if "_archive" in path.relative_to(DOCS_DIR).parts:
            continue
        yield path


# ══════════════════════════════════════════════════
# E1: 版本一致性
# ══════════════════════════════════════════════════

def check_e1_version_consistency() -> tuple[bool, str]:
    """检查 VERSION.md / config.py / 项目真值.md 三者版本一致"""
    results = {}

    # 从 VERSION.md 提取产品版本
    version_md = DOCS_DIR / "VERSION.md"
    if not version_md.exists():
        return False, "VERSION.md 不存在"
    content = _read(version_md)
    m = re.search(r'\|\s*\*\*当前修订\*\*\s*\|\s*`v?(\d+\.\d+(?:\.\d+)?)`', content)
    if not m:
        return False, "无法从 VERSION.md 解析当前版本号"
    product_version = m.group(1)
    results["VERSION.md"] = f"v{product_version}"

    # 从 config.py 提取 PRODUCT_VERSION
    config_py = RAG_APP_DIR / "config.py"
    if not config_py.exists():
        return False, "config.py 不存在"
    content = _read(config_py)
    m = re.search(r'PRODUCT_VERSION\s*:\s*str\s*=\s*["\'](\d+\.\d+\.\d+)["\']', content)
    if not m:
        return False, "无法从 config.py 解析 PRODUCT_VERSION"
    config_version_full = m.group(1)
    results["config.py"] = config_version_full

    # 从 项目真值.md 提取产品版本引用
    truth_md = DOCS_DIR / "项目真值.md"
    if not truth_md.exists():
        return False, "项目真值.md 不存在"
    content = _read(truth_md)
    m = re.search(r'（v?(\d+\.\d+(?:\.\d+)?)\s*[·•—-]', content)
    if not m:
        m = re.search(r'v(\d+\.\d+(?:\.\d+)?)\s*[·•—-]', content)
    if not m:
        return False, "无法从 项目真值.md 解析产品版本引用"
    truth_version = m.group(1)
    results["项目真值.md"] = f"v{truth_version}"

    # 交叉比对
    versions = {
        _normalize_version(product_version),
        _normalize_version(config_version_full),
        _normalize_version(truth_version),
    }
    if len(versions) == 1:
        normalized = next(iter(versions))
        return True, f"三者一致: v{normalized}"
    else:
        detail = " | ".join(f"{k}={v}" for k, v in results.items())
        return False, f"版本不一致: {detail}"


# ══════════════════════════════════════════════════
# E2: 依赖漂移
# ══════════════════════════════════════════════════

def check_e2_dependency_drift() -> tuple[bool, str]:
    """检查前端 import/config 使用的依赖是否都已在 package.json 声明。"""
    package_json = FRONTEND_DIR / "package.json"
    if not package_json.exists():
        return False, "frontend/package.json 不存在"

    try:
        manifest = json.loads(_read(package_json))
    except json.JSONDecodeError as exc:
        return False, f"frontend/package.json 解析失败: {exc}"

    declared = set(manifest.get("dependencies", {})) | set(manifest.get("devDependencies", {}))
    if not declared:
        return False, "frontend/package.json 未声明 dependencies/devDependencies"

    imported: set[str] = set()
    scanned_files = 0
    for path in _iter_frontend_scan_files():
        imported.update(_extract_external_imports(_read(path)))
        scanned_files += 1

    missing = sorted(imported - declared)

    # 类型包和部分构建依赖不一定会出现在 import 扫描中，这里只作为提示项。
    # 构建工具（如 typescript 被 lint 脚本 tsc --noEmit 使用）经构建工具豁免规则豁免。
    allowed_unscanned = {name for name in declared if name.startswith("@types/")} | {"typescript"}
    unused = sorted(name for name in declared - imported if name not in allowed_unscanned)

    # ⛔ 2026-08-12 结构熵：core/ 模块生产引用监控（预留库提示，防"牛刀"无声积灰）
    core_ref_text = ""
    try:
        core_modules = sorted(
            p.stem for p in (PROJECT_DIR / "core").glob("*.py") if p.stem != "__init__"
        )
        app_src = [p for p in (PROJECT_DIR / "app").rglob("*.py") if "test" not in str(p).lower()]
        app_src.append(PROJECT_DIR / "start.py")
        combined = "\n".join(
            p.read_text(encoding="utf-8", errors="replace") for p in app_src if p.exists()
        )
        zero_ref = [
            m for m in core_modules
            if f"core.{m}" not in combined and f"from core import {m}" not in combined
        ]
        if zero_ref:
            core_ref_text = (
                "；core 零生产引用模块（预留库，一年无复用计划应归档）: "
                + ", ".join(zero_ref[:5])
            )
    except Exception:
        pass

    if missing:
        detail = f"未声明依赖: {', '.join(missing[:6])}"
        if unused:
            detail += f"；未扫描到的声明包: {', '.join(unused[:6])}"
        detail += core_ref_text
        return False, detail

    if unused:
        return True, (
            f"{scanned_files} 个文件扫描完成，{len(imported)} 个外部依赖已声明；"
            f"{len(unused)} 个声明包未在 import/config 扫描中出现"
            f"（可能是构建/类型依赖，建议复核）: {', '.join(unused[:6])}"
            + core_ref_text
        )

    return True, (
        f"{scanned_files} 个文件扫描完成，{len(imported)} 个外部依赖全部已声明"
        + core_ref_text
    )


# ══════════════════════════════════════════════════
# E3: 引用有效性
# ══════════════════════════════════════════════════

def check_e3_reference_validity() -> tuple[bool, str]:
    """文档注册/引用双向校验（2026-08-12 扩展）：
    ① 正向：DOCS_INDEX 引用 + manifest requiredDocs 必须指向真实存在的文件
    ② 反向：docs/ 活跃 .md（除 audit/ 与 _archive/）必须已登记
       （在 manifest requiredDocs ∪ DOCS_INDEX 引用 ∪ allowedRootDocs 中），防止"出生即孤儿"
    """
    index_md = DOCS_DIR / "DOCS_INDEX.md"
    if not index_md.exists():
        # 发布版不含内部文档索引（DOCS_INDEX 属治理文档），跳过该项检查
        return True, "DOCS_INDEX.md 不存在（发布版无治理文档，跳过引用校验）"
    content = _read(index_md)
    refs = re.findall(r'docs/[^\s)\]]+\.md', content)
    missing_refs = []
    for ref in set(refs):
        full_path = PROJECT_DIR / ref
        if not full_path.exists():
            missing_refs.append(ref)

    # manifest requiredDocs 必须存在
    manifest_path = DOCS_DIR / "foundation.manifest.json"
    registered = set()
    if manifest_path.exists():
        try:
            manifest = json.loads(_read(manifest_path))
            registered = set(manifest.get("requiredDocs", []))
        except (json.JSONDecodeError, KeyError):
            pass
    missing_manifest = [
        d for d in registered
        if not (DOCS_DIR / d).exists()
    ]

    # 反向：活跃 .md 必须已登记（manifest ∪ DOCS_INDEX 引用 ∪ allowedRootDocs）
    allowed_root = set()
    if manifest_path.exists():
        try:
            allowed_root = set(json.loads(_read(manifest_path)).get("allowedRootDocs", []))
        except (json.JSONDecodeError, KeyError):
            pass
    registry = set(registered) | {
        r.removeprefix("docs/") for r in refs if r.startswith("docs/")
    } | {r for r in allowed_root if r.endswith(".md")}

    unregistered = []
    for p in sorted(DOCS_DIR.rglob("*.md")):
        rel = str(p.relative_to(DOCS_DIR)).replace("\\", "/")
        if rel.startswith(("_archive/", "audit/")):
            continue  # 归档与审计证据层豁免
        if rel not in registry:
            unregistered.append(rel)

    problems = []
    if missing_refs:
        problems.append(f"{len(missing_refs)} 条僵尸引用: {', '.join(missing_refs[:5])}")
    if missing_manifest:
        problems.append(f"{len(missing_manifest)} 条 manifest requiredDocs 缺失: {', '.join(missing_manifest[:5])}")
    if unregistered:
        problems.append(f"{len(unregistered)} 个活跃文档未登记: {', '.join(unregistered[:5])}")
    if problems:
        return False, "；".join(problems)
    return True, (
        f"引用 {len(set(refs))} 条有效 | requiredDocs {len(registered)} 条存在 | "
        f"活跃文档 {len(unregistered)} 未登记（全登记）"
    )


# ══════════════════════════════════════════════════
# E4: _archive 标记完整性
# ══════════════════════════════════════════════════

def check_e4_archive_marking() -> tuple[bool, str]:
    """检查 archive 警示文件、同名归档标记与头部活跃路径指向是否真实。"""
    if not ARCHIVE_DIR.exists():
        return True, "_archive/ 不存在"

    missing_guard_files = []
    if not (ARCHIVE_DIR / "README.md").exists():
        missing_guard_files.append("docs/_archive/README.md")
    if not (ARCHIVE_DIR / ".WARNING-ARCHIVED.md").exists():
        missing_guard_files.append("docs/_archive/.WARNING-ARCHIVED.md")

    for path in ARCHIVE_DIR.rglob("*"):
        if not path.is_dir() or path == ARCHIVE_DIR:
            continue
        has_files = any(child.is_file() for child in path.iterdir())
        if has_files and not (path / ".WARNING-ARCHIVED.md").exists():
            missing_guard_files.append(path.relative_to(PROJECT_DIR).as_posix())

    active_names = {path.name for path in _iter_active_docs()}
    twin_without_marker = []
    broken_pointers = []

    for path in ARCHIVE_DIR.rglob("*.md"):
        if path.name in {"README.md", ".WARNING-ARCHIVED.md"}:
            continue

        content = _read(path)
        header = "\n".join(content.splitlines()[:5])
        relative = path.relative_to(PROJECT_DIR).as_posix()

        if path.name in active_names and not ARCHIVE_MARKER_RE.search(header):
            twin_without_marker.append(relative)

        for pointer in ARCHIVE_POINTER_RE.findall(header):
            candidate = PROJECT_DIR / pointer.lstrip("./")
            if not candidate.exists():
                broken_pointers.append(f"{relative} -> {pointer}")

    problems = []
    if missing_guard_files:
        problems.append(f"缺少归档警示文件 {len(missing_guard_files)} 处")
    if twin_without_marker:
        problems.append(f"同名归档未标记 {len(twin_without_marker)} 处")
    if broken_pointers:
        problems.append(f"归档头部活跃路径失效 {len(broken_pointers)} 处")

    if problems:
        samples = []
        if missing_guard_files:
            samples.append(f"guard={missing_guard_files[0]}")
        if twin_without_marker:
            samples.append(f"marker={twin_without_marker[0]}")
        if broken_pointers:
            samples.append(f"pointer={broken_pointers[0]}")
        return False, "；".join(problems + samples)

    twin_count = sum(1 for path in ARCHIVE_DIR.rglob("*.md") if path.name in active_names)
    return True, f"_archive guard 完整；{twin_count} 份同名归档已标记；头部活跃路径全部有效"


# ══════════════════════════════════════════════════
# E5: 根目录 hygiene
# ══════════════════════════════════════════════════

def check_e5_root_hygiene() -> tuple[bool, str]:
    """检查 docs/ 根目录 .md 文件与 manifest 白名单一致"""
    md_files = sorted(f.name for f in DOCS_DIR.glob("*.md"))
    allowed = sorted(_load_allowed_root_md_docs())
    extra = [f for f in md_files if f not in allowed]
    missing = [f for f in allowed if f not in md_files]

    if extra or missing:
        problems = []
        if extra:
            problems.append(f"多余: {extra}")
        if missing:
            problems.append(f"缺失: {missing}")
        return False, (
            f"docs/ 根目录当前 {len(md_files)} 个 .md，"
            f"白名单要求 {len(allowed)} 个；" + "；".join(problems)
        )

    return True, f"docs/ 根目录白名单匹配: {md_files}"


# ══════════════════════════════════════════════════
# E6: 构建产物清洁
# ══════════════════════════════════════════════════

def check_e6_build_hygiene() -> tuple[bool, str]:
    """检查 frontend/dist/ 文件数 ≤ 25（代码分割后 chunk 数增加）"""
    dist_dir = PROJECT_DIR / "frontend" / "dist"
    if not dist_dir.exists():
        return True, "frontend/dist/ 不存在（未构建）"
    all_files = list(dist_dir.rglob("*"))
    file_count = len([f for f in all_files if f.is_file()])
    if file_count > 25:
        return False, f"frontend/dist/ {file_count} 个文件（上限25），存在旧构建残留"
    return True, f"frontend/dist/ {file_count} 个文件"


# ══════════════════════════════════════════════════
# E7: 经验沉淀健康度
# ══════════════════════════════════════════════════

def _find_experience_file() -> Path | None:
    """查找经验沉淀.md（处理中文文件名编码问题）"""
    exp_dir = DOCS_DIR / "governance" / "project-experience"
    if not exp_dir.exists():
        return None
    for f in exp_dir.glob("*沉淀*.md"):
        return f
    for f in exp_dir.glob("*经验*.md"):
        return f
    # 兜底：按大小找最大的 .md（通常是经验沉淀）
    md_files = list(exp_dir.glob("*.md"))
    if md_files:
        return max(md_files, key=lambda x: x.stat().st_size)
    return None

EXPERIENCE_PATH = _find_experience_file()

def _parse_experience_entries() -> list[dict]:
    """解析经验沉淀.md，返回结构化条目列表"""
    if not EXPERIENCE_PATH or not EXPERIENCE_PATH.exists():
        return []
    content = _read(EXPERIENCE_PATH)
    entries = []
    # 兼容两种标题格式：`## 001: 标题（date）` 与 `## 经验 #027：标题（date）`
    pattern = re.compile(r'^##\s+(?:经验\s*)?#?(\d{3})\s*[:：]\s*(.+?)\s*[（\(](\d{4}-\d{2}-\d{2})[）\)]', re.MULTILINE)
    for match in pattern.finditer(content):
        num, title, date_str = match.groups()
        # 提取该条目的内容范围（到下一个 ## 或 ---）
        start = match.end()
        next_match = pattern.search(content, start)
        end = next_match.start() if next_match else len(content)
        entry_content = content[start:end]
        
        # 提取标签
        tag_matches = re.findall(r'`#([^`]+)`', entry_content)
        tags = list(set(tag_matches))  # 去重
        
        # 提取改进措施表格中的落地位置
        impl_locations = re.findall(r'\|\s*\d+\s*\|\s*[^|]+\s*\|\s*([^|]+)\s*\|', entry_content)
        
        # 统计引用次数（在代码/文档中被引用）
        ref_count = 0
        for loc in impl_locations:
            # 简单估算：如果落地位置包含文件路径
            if any(ext in loc for ext in ['.py', '.tsx', '.ts', '.md', '.json']):
                ref_count += 1
        
        entries.append({
            "num": int(num),
            "title": title.strip(),
            "date": date_str,
            "tags": tags,
            "impl_locations": impl_locations,
            "ref_count": ref_count,
            "content_length": len(entry_content),
        })
    return entries


def check_e7_experience_health() -> tuple[bool, str]:
    """经验沉淀 Select/Maintain 治理：价值评分、衰减、低价值归档建议"""
    entries = _parse_experience_entries()
    if not entries:
        return True, "经验沉淀.md 不存在或无条目"
    
    from datetime import date, datetime
    today = date.today()
    
    results = []
    low_value = []
    stale = []
    high_value = []
    
    for e in entries:
        # 计算天数
        try:
            entry_date = datetime.strptime(e["date"], "%Y-%m-%d").date()
            days_old = (today - entry_date).days
        except:
            days_old = 999
        
        # 价值评分（简化版）：引用次数 + 标签数 + 落地位置数 - 天数衰减
        value_score = (
            e["ref_count"] * 3 + 
            len(e["tags"]) * 2 + 
            len(e["impl_locations"]) * 1 - 
            max(0, days_old - 30) * 0.1  # 30天后开始衰减
        )
        
        e["value_score"] = round(value_score, 1)
        e["days_old"] = days_old
        
        if value_score < 1.0:
            low_value.append(f"#{e['num']:03d} ({e['title'][:30]}) score={value_score:.1f}")
        elif value_score > 5.0:
            high_value.append(f"#{e['num']:03d} ({e['title'][:30]}) score={value_score:.1f}")
        
        if days_old > 180 and value_score < 3.0:
            stale.append(f"#{e['num']:03d} ({days_old}天前)")
        
        results.append(e)
    
    # 输出摘要
    summary = f"经验条目 {len(entries)} 个 | 高价值 {len(high_value)} | 低价值 {len(low_value)} | 陈旧待归档 {len(stale)}"

    # ⛔ 编号连续性检查：出现缺口说明有条目只写了日志没入文件（#030/#031 教训）
    nums = sorted(e["num"] for e in entries)
    missing = [f"#{n:03d}" for n in range(nums[0], nums[-1] + 1) if n not in set(nums)]
    if missing:
        detail_str = f"{summary} | ⛔ 经验编号缺口: {', '.join(missing)}（可能只写了日志未沉淀）"
        return False, detail_str
    
    details = []
    if high_value:
        details.append("⭐ 建议固化为规范: " + "; ".join(high_value[:3]))
    if low_value:
        details.append("🗑️  低价值建议归档: " + "; ".join(low_value[:3]))
    if stale:
        details.append("📦 陈旧低价值: " + "; ".join(stale[:3]))
    
    detail_str = summary + (" | " + " | ".join(details) if details else "")
    
    # 不阻塞，只提示
    return True, detail_str


# ══════════════════════════════════════════════════
# E8: 回归率跟踪
# ══════════════════════════════════════════════════

TODO_PATH = DOCS_DIR / "TODO.md"
HANDOVER_PATH = DOCS_DIR / "HANDOVER.md"
ACTIVE_STATE_PATH = DOCS_DIR / "ACTIVE_STATE.md"

def _parse_done_tasks() -> list[str]:
    """解析 TODO.md 中已完成的任务"""
    if not TODO_PATH.exists():
        return []
    content = _read(TODO_PATH)
    # 匹配已完成表格中的任务
    done_tasks = re.findall(r'~\s*T(\d+)\s*~~', content)
    # 也匹配已完成表格中的行：| - | 任务名 | 日期 | commit |
    done_rows = re.findall(r'\|\s*-\s*\|\s*([^|]+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+)\s*\|', content)
    # 从已完成行中提取任务编号（如果有 T1, T2 等格式）
    for row in done_rows:
        task_name = row[0]
        t_match = re.search(r'T(\d+(?:\.\d+)?)', task_name)
        if t_match:
            done_tasks.append(t_match.group(1))
    # 简化：返回任务标识符
    return [f"T{t}" for t in done_tasks if t.isdigit()]


def _get_active_issues() -> set[str]:
    """从 HANDOVER.md 和 ACTIVE_STATE.md 提取当前活跃的问题/任务"""
    issues = set()
    for path in [HANDOVER_PATH, ACTIVE_STATE_PATH]:
        if not path.exists():
            continue
        content = _read(path)
        # 匹配 T1, T2.1 等任务 ID
        issues.update(re.findall(r'T\d+(?:\.\d+)?', content))
    return issues


def check_e8_regression_rate() -> tuple[bool, str]:
    """回归率：对比 TODO.md 中 done 任务，是否在后续文档中再次出现"""
    done_tasks = set(_parse_done_tasks())
    active_issues = _get_active_issues()
    
    if not done_tasks:
        return True, "无已完成任务可对比"
    
    regressed = done_tasks & active_issues
    regression_rate = len(regressed) / len(done_tasks) if done_tasks else 0.0
    
    detail = f"已完成任务 {len(done_tasks)} 个 | 当前活跃问题 {len(active_issues)} 个 | 回归 {len(regressed)} 个 | 回归率 {regression_rate:.1%}"
    
    if regressed:
        detail += f" | 回归任务: {', '.join(sorted(regressed)[:5])}"
    
    # 回归率 > 20% 警告，不阻塞
    if regression_rate > 0.2:
        return True, f"[WARN] {detail} —— 回归率偏高，建议排查"
    
    return True, detail


# ══════════════════════════════════════════════════
# E9: 老化清理建议 (2026-08-02)
# ══════════════════════════════════════════════════

AGING_DAYS = 30


def _scan_aging(dirs: dict[str, Path]) -> list[tuple[str, Path]]:
    """返回 (目录名, 文件) 列表中超过 AGING_DAYS 未修改的文件。"""
    import time
    now = time.time()
    stale = []
    for name, d in dirs.items():
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if f.is_file() and (now - f.stat().st_mtime) / 86400 > AGING_DAYS:
                stale.append((name, f))
    return stale


def check_e9_aging_cleanup() -> tuple[bool, str]:
    """扫描 backups/ output/ logs/ 中超过 30 天的文件。
    2026-08-02 修正：output/logs 老化 = FAIL（可再生垃圾）；backups 老化 = 仅提示（快照不可自动删）。
    """
    stale = _scan_aging({
        "backups": PROJECT_DIR / "backups",
        "output": PROJECT_DIR / "output",
        "logs": PROJECT_DIR / "logs",
    })
    if not stale:
        return True, "无超30天老化文件"

    by_dir: dict[str, list[Path]] = {}
    for name, f in stale:
        by_dir.setdefault(name, []).append(f)
    parts = []
    for name in ("output", "logs", "backups"):
        files = by_dir.get(name, [])
        if files:
            total = sum(f.stat().st_size for f in files)
            parts.append(f"{name}/: {len(files)} files ({total//1024}KB)")
    detail = "；".join(parts)

    hard = any(name in by_dir for name in ("output", "logs"))
    if hard:
        return False, f"可清理老化文件: {detail}（运行 python app/entropy_audit.py --clean）"
    return True, f"backups 老化（仅提示，快照保留）: {detail}"


def clean_aging(include_backups: bool = False) -> int:
    """删除 output/logs（及可选 backups）中超过 30 天的文件与空目录。返回删除文件数。
    安全护栏：跳过 git 跟踪文件（如 output/prd/）与运行期夹具（如 graph-data-sample*.json）。
    """
    import time
    import subprocess
    now = time.time()
    target_dirs = {
        "output": (PROJECT_DIR / "output").resolve(),
        "logs": (PROJECT_DIR / "logs").resolve(),
    }
    if include_backups:
        target_dirs["backups"] = (PROJECT_DIR / "backups").resolve()

    # 预取 git 跟踪文件集合（防止误删已入库内容）
    tracked = set()
    try:
        r = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(PROJECT_DIR), capture_output=True,
        )
        tracked = {os.path.normpath(p) for p in r.stdout.decode("utf-8", "replace").split("\0") if p}
    except Exception:
        pass

    def _is_tracked(f: Path) -> bool:
        try:
            rel = os.path.normpath(str(f.relative_to(PROJECT_DIR)))
        except ValueError:
            return True  # 项目目录外，绝不删
        return rel in tracked

    removed = 0
    for name, d in target_dirs.items():
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            if f.name.startswith("graph-data-sample"):
                continue  # 运行期夹具：/api/graph/data sample 模式依赖
            if _is_tracked(f):
                continue  # git 跟踪文件不删
            try:
                resolved = f.resolve()
                if not resolved.is_relative_to(d):
                    continue
            except Exception:
                continue
            if (now - f.stat().st_mtime) / 86400 > AGING_DAYS:
                try:
                    f.unlink()
                    removed += 1
                except Exception as e:
                    print(f"  [WARN] 删除失败 {f}: {e}")

    # 清理遗留空目录（保留各根目录本身）
    for name, d in target_dirs.items():
        if d.exists():
            for sub in sorted(d.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if sub.is_dir():
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
    return removed


# ══════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════

CHECKS = [
    ("E1", "版本一致性", check_e1_version_consistency),
    ("E2", "依赖漂移", check_e2_dependency_drift),
    ("E3", "引用有效性", check_e3_reference_validity),
    ("E4", "_archive 标记完整性", check_e4_archive_marking),
    ("E5", "根目录 hygiene", check_e5_root_hygiene),
    ("E6", "构建产物清洁", check_e6_build_hygiene),
    ("E7", "经验沉淀健康度", check_e7_experience_health),
    ("E8", "回归率跟踪", check_e8_regression_rate),
    ("E9", "老化清理建议", check_e9_aging_cleanup),
]


def run_audit(json_output: bool = False) -> dict:
    """运行全部审计项，返回结果字典"""
    results = {}
    for code, name, func in CHECKS:
        passed, detail = func()
        results[code] = {"name": name, "passed": passed, "detail": detail}

    if json_output:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=" * 50)
        print("  熵减审计 E1~E9")
        print("=" * 50)
        all_pass = True
        for code, name, func in CHECKS:
            passed, detail = func()
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {code} {name}")
            print(f"         {detail}")
            if not passed:
                all_pass = False
        print("=" * 50)
        if all_pass:
            print("  全部通过 [PASS]")
        else:
            print("  存在失败项 [FAIL] - 请当轮修复")
        print("=" * 50)

    return results


if __name__ == "__main__":
    json_mode = "--json" in sys.argv
    clean_mode = "--clean" in sys.argv
    if clean_mode:
        include_backups = "--include-backups" in sys.argv
        scope = "output/logs" + (" + backups" if include_backups else "")
        print(f"老化清理: 删除 {scope} 中 >{AGING_DAYS} 天的文件")
        n = clean_aging(include_backups=include_backups)
        print(f"  已删除 {n} 个老化文件")
    run_audit(json_output=json_mode)
