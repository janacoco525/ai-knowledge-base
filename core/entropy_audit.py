#!/usr/bin/env python3
"""
Generic Entropy Audit Framework — 通用熵减审计框架
可跨项目复用的审计基类，具体检查项由子类实现

⚠️ 未接入状态（2026-08-10 审计标记）：本项目实际审计逻辑在 app/entropy_audit.py
（E1~E9 独立实现，未继承本基类）。本文件仅作跨项目框架示例，勿作为本项目审计入口。
"""
import sys
import json
import re
import io
from pathlib import Path
from typing import Dict, List, Tuple, Callable, Any, Optional
from abc import ABC, abstractmethod

# Windows GBK 编码兼容
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


class EntropyAuditBase(ABC):
    """熵减审计基类 - 子类需实现具体检查项"""
    
    def __init__(self, project_dir: Path, config: Optional[Dict] = None):
        self.project_dir = Path(project_dir).resolve()
        self.config = config or {}
        self.checks: List[Tuple[str, str, Callable[[], Tuple[bool, str]]]] = []
        self._register_checks()
    
    @abstractmethod
    def _register_checks(self):
        """子类实现：注册具体检查项"""
        pass
    
    def add_check(self, code: str, name: str, func: Callable[[], Tuple[bool, str]]):
        """添加检查项"""
        self.checks.append((code, name, func))
    
    def run_audit(self, json_output: bool = False) -> Dict[str, Any]:
        """运行全部审计"""
        results = {}
        all_pass = True
        
        if json_output:
            for code, name, func in self.checks:
                passed, detail = func()
                results[code] = {"name": name, "passed": passed, "detail": detail}
                if not passed:
                    all_pass = False
            return {"results": results, "all_pass": all_pass}
        else:
            print("=" * 50)
            print(f"  熵减审计 ({self.__class__.__name__})")
            print("=" * 50)
            for code, name, func in self.checks:
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
            return {"all_pass": all_pass, "results": {code: {"name": name, "passed": func()[0]} for code, name, func in self.checks}}


# 便捷基类：文件系统审计
class FileSystemAuditBase(EntropyAuditBase):
    """文件系统审计基类 - 提供常用路径访问"""
    
    def __init__(self, project_dir: Path, config: Optional[Dict] = None):
        super().__init__(project_dir, config)
        self.docs_dir = self.project_dir / "docs"
        self.frontend_dir = self.project_dir / "frontend"
        self.scripts_dir = self.project_dir / "scripts"
    
    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")
    
    def _load_json(self, path: Path) -> Dict:
        try:
            return json.loads(self._read(path))
        except (json.JSONDecodeError, FileNotFoundError):
            return {}


# 使用示例
if __name__ == "__main__":
    # 示例：创建一个简单的审计
    class DemoAudit(FileSystemAuditBase):
        def _register_checks(self):
            self.add_check("E1", "示例检查", lambda: (True, "演示通过"))
    
    audit = DemoAudit(Path("."))
    audit.run_audit()