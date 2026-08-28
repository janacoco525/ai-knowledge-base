#!/usr/bin/env python3
"""
AI知识库 — 自动化版本发布脚本

用法:
  python scripts/release.py              # 交互式发布
  python scripts/release.py patch        # 直接发布 patch 版本
  python scripts/release.py minor        # 直接发布 minor 版本
  python scripts/release.py major        # 直接发布 major 版本
  python scripts/release.py --dry-run    # 预演模式（不写文件、不提交）
"""

import sys
import re
import subprocess
from pathlib import Path
from datetime import date
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
VERSION_MD = PROJECT_DIR / "docs" / "VERSION.md"
CONFIG_PY = PROJECT_DIR / "app" / "rag_app" / "config.py"
API_SERVER_PY = PROJECT_DIR / "app" / "rag_app" / "api_server.py"
HANDOVER_MD = PROJECT_DIR / "docs" / "HANDOVER.md"
README_MD = PROJECT_DIR / "README.md"


def run_cmd(cmd: list[str], cwd: Optional[Path] = None, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or PROJECT_DIR), capture_output=capture, text=True, encoding="utf-8", errors="replace")


def get_current_version() -> str:
    """从 VERSION.md 解析当前版本"""
    content = VERSION_MD.read_text(encoding="utf-8")
    m = re.search(r'\|\s*\*\*当前修订\*\*\s*\|\s*`v?(\d+\.\d+\.\d+)`', content)
    if not m:
        m = re.search(r'### v(\d+\.\d+\.\d+)', content)
    if not m:
        raise ValueError("无法从 VERSION.md 解析当前版本")
    return m.group(1)


def parse_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def bump_version(version: str, level: str) -> str:
    major, minor, patch = parse_version(version)
    if level == "major":
        return f"{major + 1}.0.0"
    elif level == "minor":
        return f"{major}.{minor + 1}.0"
    elif level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        raise ValueError(f"未知版本级别: {level}")


def generate_changelog(from_version: str, to_version: str) -> str:
    """从 git log 生成变更摘要"""
    # 获取上一个版本 tag 对应的 commit
    result = run_cmd(["git", "log", "--oneline", "--grep", from_version, "-1"])
    if result.returncode == 0 and result.stdout.strip():
        last_commit = result.stdout.strip().split()[0]
        log_result = run_cmd(["git", "log", f"{last_commit}..HEAD", "--oneline", "--no-merges"])
    else:
        # 兜底：最近 30 次提交
        log_result = run_cmd(["git", "log", "-30", "--oneline", "--no-merges"])
    
    commits = log_result.stdout.strip().splitlines()
    if not commits:
        return "- 无新提交"
    
    # 分类汇总
    categories = {"feat": [], "fix": [], "refactor": [], "docs": [], "chore": [], "other": []}
    for c in commits:
        c_lower = c.lower()
        if c_lower.startswith("feat:") or "新增" in c or "添加" in c:
            categories["feat"].append(c)
        elif c_lower.startswith("fix:") or "修复" in c or "解决" in c or "修正" in c:
            categories["fix"].append(c)
        elif c_lower.startswith("refactor:") or "重构" in c:
            categories["refactor"].append(c)
        elif c_lower.startswith("docs:") or "文档" in c:
            categories["docs"].append(c)
        elif c_lower.startswith("chore:") or "构建" in c or "配置" in c:
            categories["chore"].append(c)
        else:
            categories["other"].append(c)
    
    lines = []
    for cat, items in categories.items():
        if items:
            cat_name = {"feat": "新功能", "fix": "修复", "refactor": "重构", "docs": "文档", "chore": "工程", "other": "其他"}[cat]
            lines.append(f"### {cat_name}")
            for item in items[:5]:  # 每类最多 5 条
                lines.append(f"- {item}")
            if len(items) > 5:
                lines.append(f"- ... 等共 {len(items)} 项")
            lines.append("")
    return "\n".join(lines) if lines else "- 无显著变更"


def update_version_md(new_version: str, changelog: str, dry_run: bool = False) -> bool:
    """更新 VERSION.md"""
    content = VERSION_MD.read_text(encoding="utf-8")
    today = date.today().isoformat()
    
    # 更新当前版本表格
    content = re.sub(
        r'\|\s*\*\*当前修订\*\*\s*\|\s*`v?\d+\.\d+\.\d+`',
        f"| **当前修订** | `v{new_version}` — 自动发布",
        content
    )
    content = re.sub(
        r'\|\s*\*\*发布日期\*\*\s*\|\s*\d{4}-\d{2}-\d{2}',
        f"| **发布日期** | {today}",
        content
    )
    
    # 插入新版本历史条目（在版本历史表格后、第一个 ### v... 前）
    new_entry = f"""### v{new_version} ({today}) — 自动发布

| 改动 | 涉及文件 |
|------|---------|
| 自动化版本发布 | `VERSION.md`, `config.py`, `api_server.py`, `HANDOVER.md`, `README.md` |

{changelog}

"""
    # 找到第一个 ### v... 的位置插入
    m = re.search(r'(### v\d+\.\d+\.\d+)', content)
    if m:
        insert_pos = m.start()
        content = content[:insert_pos] + new_entry + content[insert_pos:]
    else:
        # 兜底：追加到文件末尾
        content += "\n" + new_entry
    
    if not dry_run:
        VERSION_MD.write_text(content, encoding="utf-8")
    return True


def update_config_py(new_version: str, dry_run: bool = False) -> bool:
    """更新 config.py 的 PRODUCT_VERSION"""
    content = CONFIG_PY.read_text(encoding="utf-8")
    content = re.sub(
        r'PRODUCT_VERSION: str = "[\d.]+',
        f'PRODUCT_VERSION: str = "{new_version}"',
        content
    )
    if not dry_run:
        CONFIG_PY.write_text(content, encoding="utf-8")
    return True


def update_api_server_py(new_version: str, dry_run: bool = False) -> bool:
    """更新 api_server.py 的 version（已使用 Config.PRODUCT_VERSION，仅作验证）"""
    content = API_SERVER_PY.read_text(encoding="utf-8")
    # 验证使用的是 Config.PRODUCT_VERSION
    if "Config.PRODUCT_VERSION" not in content:
        print(f"[WARN] api_server.py 未使用 Config.PRODUCT_VERSION，请手动检查")
    return True


def update_handover_md(new_version: str, dry_run: bool = False) -> bool:
    """更新 HANDOVER.md 的产品版本"""
    content = HANDOVER_MD.read_text(encoding="utf-8")
    content = re.sub(
        r'\*\*产品版本\*\*[：:]v[\d.]+',
        f'**产品版本**：v{new_version}',
        content
    )
    # 更新交接时间
    today = date.today().isoformat()
    content = re.sub(
        r'> \*\*交接时间\*\*[：:].+',
        f'> **交接时间**：{today}',
        content
    )
    if not dry_run:
        HANDOVER_MD.write_text(content, encoding="utf-8")
    return True


def update_readme_md(new_version: str, dry_run: bool = False) -> bool:
    """更新 README.md 的版本引用（如有）"""
    content = README_MD.read_text(encoding="utf-8")
    # README 目前无显式版本号，若后续添加则在此处理
    if not dry_run:
        README_MD.write_text(content, encoding="utf-8")
    return True


def git_commit_and_tag(new_version: str, dry_run: bool = False) -> bool:
    """Git 提交并打 tag"""
    files = [
        "docs/VERSION.md",
        "app/rag_app/config.py",
        "docs/HANDOVER.md",
        "README.md",
    ]
    # 只添加存在的文件
    existing_files = [f for f in files if (PROJECT_DIR / f).exists()]
    
    if not dry_run:
        run_cmd(["git", "add"] + existing_files, capture=False)
        commit_msg = f"chore: 发布 v{new_version}"
        run_cmd(["git", "commit", "-m", commit_msg], capture=False)
        run_cmd(["git", "tag", "-a", f"v{new_version}", "-m", f"Release v{new_version}"], capture=False)
        print(f"[OK] Git commit & tag v{new_version} 完成")
    else:
        print(f"[DRY-RUN] Would commit: {existing_files}")
        print(f"[DRY-RUN] Would tag: v{new_version}")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI知识库自动化版本发布")
    parser.add_argument("level", nargs="?", choices=["patch", "minor", "major"], help="版本级别")
    parser.add_argument("--dry-run", action="store_true", help="预演模式，不写文件、不提交")
    args = parser.parse_args()
    
    print("=" * 60)
    print("  AI知识库 — 自动化版本发布")
    print("=" * 60)
    
    # 检查 Git 状态
    status = run_cmd(["git", "status", "--porcelain"])
    if status.stdout.strip() and not args.dry_run:
        print("[FAIL] 工作区有未提交变更，请先 commit 或 stash")
        sys.exit(1)
    
    # 当前版本
    current = get_current_version()
    print(f"当前版本: v{current}")
    
    # 确定新版本
    if args.level:
        level = args.level
    else:
        print("\n选择版本级别:")
        print("  1. patch (修复)   -> v{}.{}.{}".format(*parse_version(current)[0:2], parse_version(current)[2] + 1))
        print("  2. minor (功能)   -> v{}.{}.0".format(parse_version(current)[0], parse_version(current)[1] + 1))
        print("  3. major (破坏)   -> v{}.0.0".format(parse_version(current)[0] + 1))
        choice = input("请选择 (1/2/3): ").strip()
        level = {"1": "patch", "2": "minor", "3": "major"}.get(choice, "patch")
    
    new_version = bump_version(current, level)
    print(f"\n新版本: v{new_version} ({level})")
    
    if not args.dry_run:
        confirm = input("确认发布? (y/N): ").strip().lower()
        if confirm != "y":
            print("已取消")
            sys.exit(0)
    
    # 生成 changelog
    print("\n生成变更日志...")
    changelog = generate_changelog(current, new_version)
    print(changelog[:500] + ("..." if len(changelog) > 500 else ""))
    
    # 更新文件
    print("\nUpdating files...")
    update_version_md(new_version, changelog, args.dry_run)
    print("  [OK] VERSION.md")
    update_config_py(new_version, args.dry_run)
    print("  [OK] config.py")
    update_api_server_py(new_version, args.dry_run)
    print("  [OK] api_server.py (verify)")
    update_handover_md(new_version, args.dry_run)
    print("  [OK] HANDOVER.md")
    update_readme_md(new_version, args.dry_run)
    print("  [OK] README.md")
    
    # Git 提交并打 tag
    print("\nGit 提交...")
    git_commit_and_tag(new_version, args.dry_run)
    
    print("\n" + "=" * 60)
    if args.dry_run:
        print("  [DRY-RUN] Preview complete, no files modified")
    else:
        print(f"  [OK] Release v{new_version} completed!")
        print(f"  Next: git push origin master --tags")
    print("=" * 60)


if __name__ == "__main__":
    main()