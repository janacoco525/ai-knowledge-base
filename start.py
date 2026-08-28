"""
AI知识库 - 统一启动脚本
一键启动 FastAPI 后端服务，支持测试和健康检查
"""
import os
import sys
import json
import subprocess
import argparse
import socket
import time
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
RAG_APP_DIR = PROJECT_DIR / "app" / "rag_app"


def check_config():
    env_path = PROJECT_DIR / ".env"
    if not env_path.exists():
        print("[WARN] 未找到 .env 文件（项目根目录）")
        print("       请创建 .env 并填入 STEP_API_KEY=你的密钥")
        return False
    with open(env_path, encoding="utf-8") as f:
        content = f.read()
    if "STEP_API_KEY" not in content or "=" not in content.split("STEP_API_KEY")[1].split("\n")[0]:
        print("[WARN] .env 中未找到 STEP_API_KEY")
        return False
    print("[OK] 配置文件检查通过（.env）")
    return True


def check_dependencies():
    try:
        # Try project venv Python first, then fallback to sys.executable
        venv_python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
        python = str(venv_python) if venv_python.exists() else sys.executable
        result = subprocess.run(
            [python, "-c", "import fastapi, uvicorn"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10
        )
        if result.returncode == 0:
            print("[OK] 核心依赖已安装（venv Python）")
            return True
    except Exception:
        pass
    print("[WARN] 核心依赖可能未安装（fastapi/uvicorn），请检查 venv")
    print("       如果看到此警告但服务正常运行，请忽略")
    return False


def start_server(port: int = 8501, host: str = "127.0.0.1", auto_restart: bool = False, max_restarts: int = 5):
    os.chdir(RAG_APP_DIR)
    print(f"[INFO] 启动 AI知识库 服务...")
    print(f"[INFO] 访问地址: http://127.0.0.1:{port}")
    print(f"[INFO] API文档: http://127.0.0.1:{port}/docs")
    if auto_restart:
        print(f"[INFO] 自动重启已启用 (最多 {max_restarts} 次)")
    print("-" * 50)
    cmd = [sys.executable, "api_server.py"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(RAG_APP_DIR)
    env["PORT"] = str(port)
    env["HOST"] = host

    restart_count = 0
    backoff = 1  # seconds, doubles each restart

    while True:
        process = subprocess.Popen(cmd, cwd=str(RAG_APP_DIR), env=env,
                                   creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0)
        try:
            exit_code = process.wait()
            if not auto_restart:
                break
            restart_count += 1
            if restart_count > max_restarts:
                print(f"[WARN] 已达到最大重启次数 ({max_restarts})，停止自动重启")
                break
            if exit_code == 0 or exit_code == -2:  # -2 = SIGINT
                print(f"[INFO] 服务正常退出 (code={exit_code})，不自动重启")
                break
            print(f"[WARN] 服务异常退出 (code={exit_code})，{backoff}s 后第 {restart_count} 次重启...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)  # 指数退避，最多30秒
        except KeyboardInterrupt:
            print("\n[INFO] 服务已停止")
            process.terminate()
            process.wait()
            break


def _is_port_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


def _wait_for_health(base_url: str, timeout: int = 20) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def run_test(port: int = 8501, test_timeout: int = 180):
    smoke_script = PROJECT_DIR / "tests" / "smoke_test.py"
    if not smoke_script.exists():
        print(f"[ERROR] 未找到测试脚本: {smoke_script}")
        sys.exit(1)
    base_url = f"http://127.0.0.1:{port}"
    temp_process = None
    if not _is_port_listening("127.0.0.1", port):
        print(f"[INFO] 未检测到 {base_url} listener，启动临时测试服务...")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(RAG_APP_DIR)
        env["PORT"] = str(port)
        temp_process = subprocess.Popen(
            [sys.executable, "api_server.py"],
            cwd=str(RAG_APP_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0,
        )
        if not _wait_for_health(base_url, timeout=60):
            print(f"[ERROR] 临时测试服务启动失败: {base_url}/health 未在 20s 内就绪")
            if temp_process:
                temp_process.terminate()
                temp_process.wait(timeout=10)
            sys.exit(1)
    print("[INFO] 运行冒烟测试...")
    print("-" * 50)
    env = os.environ.copy()
    env["AI_KB_BASE_URL"] = base_url
    try:
        result = subprocess.run(
            [sys.executable, str(smoke_script)],
            cwd=str(PROJECT_DIR),
            env=env,
            timeout=test_timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[ERROR] 冒烟测试超过 {test_timeout}s 仍未结束，已按失败处理。")
        result = subprocess.CompletedProcess([sys.executable, str(smoke_script)], returncode=124)
    finally:
        if temp_process:
            print("[INFO] 冒烟测试结束，关闭临时测试服务...")
            temp_process.terminate()
            try:
                temp_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                temp_process.kill()
    sys.exit(result.returncode)


def _check_todo_staleness():
    """检查 TODO.md 逾期项目和更新频率 (2026-08-02)"""
    import re
    from datetime import date
    todo_path = PROJECT_DIR / "docs" / "TODO.md"
    if not todo_path.exists():
        print("    [WARN] TODO.md 文件不存在")
        return

    content = todo_path.read_text(encoding="utf-8", errors="replace")
    today = date.today()
    overdue = 0
    for m in re.finditer(r'\|\s*(T\d+)\s*\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|', content):
        deadline = m.group(2).strip()
        if deadline and deadline != "TBD" and deadline != "—":
            try:
                if date.fromisoformat(deadline) < today:
                    overdue += 1
            except ValueError:
                pass

    if overdue:
        print(f"    [WARN] {overdue} 个待办已逾期")
    else:
        print("    [OK] 无逾期待办")

    mtime = todo_path.stat().st_mtime
    age_days = (today - date.fromtimestamp(mtime)).days
    if age_days > 14:
        print(f"    [INFO] TODO.md {age_days} 天未更新，可能需刷新")
    else:
        print(f"    [OK] TODO.md {age_days} 天前更新")


def _check_llm_connectivity():
    """验证LLM API是否可连通——健康检查最关键的盲区"""
    # 确保使用 venv Python (系统 Python 可能缺依赖)
    venv_py = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists() and sys.executable != str(venv_py):
        try:
            result = subprocess.run(
                [str(venv_py), "-c", "from config import Config; from llm_client_factory import create_llm_client; c=Config(); client=create_llm_client(api_key=c.STEP_API_KEY, base_url=c.STEP_API_BASE); r=client.chat.completions.create(model=c.STEP_MODEL,messages=[{'role':'user','content':'ping'}],max_tokens=5,timeout=8); print(r.choices[0].message.content or 'ok')"],
                capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
                cwd=str(PROJECT_DIR / "app" / "rag_app"),
                env={**dict(os.environ), "PYTHONPATH": str(PROJECT_DIR)},
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False
    
    try:
        sys.path.insert(0, str(RAG_APP_DIR))
        from config import Config
        from llm_client_factory import create_llm_client
        cfg = Config()
        if not cfg.STEP_API_KEY:
            return False
        client = create_llm_client(api_key=cfg.STEP_API_KEY, base_url=cfg.STEP_API_BASE)
        response = client.chat.completions.create(
            model=cfg.STEP_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=8
        )
        return response.choices[0].message.content is not None
    except Exception:
        return False


def _check_frontend_consistency():
    """检查 React SPA 前端构建一致性（rag_app/web/ v2 原型已迁移至 legacy/）"""
    dist_dir = PROJECT_DIR / "frontend" / "dist"
    if not dist_dir.exists():
        return False, "frontend/dist/ 不存在（需要 npm run build）"
    # 检查构建产物完整性
    has_index = (dist_dir / "index.html").exists()
    assets = list((dist_dir / "assets").glob("*")) if (dist_dir / "assets").exists() else []
    has_js = any(f.suffix == ".js" for f in assets)
    has_css = any(f.suffix == ".css" for f in assets)
    ok = has_index and has_js and has_css
    detail = f"index.html={'OK' if has_index else 'MISSING'}, JS={'OK' if has_js else 'MISSING'}, CSS={'OK' if has_css else 'MISSING'}"
    return ok, detail


def _check_vite_dev_server() -> tuple[bool, str]:
    """检查 Vite 开发服务器 (端口 5173/5174)"""
    for port in [5173, 5174]:
        if _is_port_listening("127.0.0.1", port):
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2) as resp:
                    if resp.status == 200:
                        return True, f"Vite dev server running on port {port}"
            except Exception:
                pass
    return False, "No Vite dev server on 5173/5174"


def _run_pytest_check() -> tuple[bool, str]:
    """全量 pytest 门禁（2026-08-02 新增）：health 必须真实跑测试，不能只看文件存在。"""
    python = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    exe = str(python) if python.exists() else sys.executable
    try:
        result = subprocess.run(
            [exe, "-m", "pytest", "tests", "-q", "--no-header"],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
            cwd=str(PROJECT_DIR),
        )
        tail = (result.stdout or "").strip().splitlines()
        summary = tail[-1].strip() if tail else f"exit={result.returncode}"
        ok = result.returncode == 0
        return ok, summary
    except subprocess.TimeoutExpired:
        return False, "pytest 超时(180s)"
    except Exception as e:
        return False, f"pytest 运行失败: {e}"


def _check_doc_freshness() -> tuple[bool, str]:
    """关键状态文档定期更新保鲜（超过阈值提示刷新）"""
    import time as _time
    freshness = {
        "docs/项目真值.md": 30,
        "docs/VERSION.md": 30,
    }
    now = _time.time()
    stale = []
    for rel, max_days in freshness.items():
        p = PROJECT_DIR / rel
        if not p.exists():
            stale.append(f"{rel}(缺失)")
            continue
        try:
            age_days = (now - p.stat().st_mtime) / 86400
        except OSError:
            continue
        if age_days > max_days:
            stale.append(f"{rel}({int(age_days)}天>{max_days}天)")
    if stale:
        return False, "过期: " + "; ".join(stale[:5])
    return True, "关键状态文件均在保鲜期内"


def run_health_check(mode: str = "prod"):
    print("AI知识库 项目健康自检")
    print("=" * 50)
    print(f"模式: {mode.upper()}")


    score = 0
    max_score = 0

    def check_item(name, passed, weight=1):
        nonlocal score, max_score
        max_score += weight
        status = "[OK]" if passed else "[FAIL]"
        print(f"  {status} {name}")
        if passed:
            score += weight

    print("\n[配置]")
    env_path = PROJECT_DIR / ".env"
    check_item(".env 文件存在（项目根目录）", env_path.exists())
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            content = f.read()
        has_api_key = any(k in content and len(content.split(f"{k}=")[-1].split("\n")[0].strip()) > 10
                         for k in ["STEP_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "KIMI_API_KEY", "QWEN_API_KEY", "GLM_API_KEY"])
        check_item("至少一个 LLM API Key 已配置", has_api_key)
    print("\n[依赖]")
    check_item("fastapi 已安装", check_dependencies())
    print("\n[文件结构]")
    check_item("README.md 存在", (PROJECT_DIR / "README.md").exists())
    check_item("start.py 存在", (PROJECT_DIR / "start.py").exists())
    check_item("api_server.py 存在", (RAG_APP_DIR / "api_server.py").exists())
    check_item("config.py 存在", (RAG_APP_DIR / "config.py").exists())
    print("\n[文档保鲜]")
    fresh_ok, fresh_detail = _check_doc_freshness()
    check_item(f"关键状态文件定期更新保鲜 ({fresh_detail})", fresh_ok)
    # 深度体检提醒（不占健康分，避免污染评分；>30 天提示执行 scripts/deep_check.py）
    deep_marker = PROJECT_DIR / ".aikb" / "deep_check_last.json"
    if deep_marker.exists():
        try:
            deep_ts = float(json.loads(deep_marker.read_text(encoding="utf-8")).get("timestamp", 0))
            deep_age = (time.time() - deep_ts) / 86400
            if deep_age > 30:
                print(f"  [WARN] 深度体检已 {int(deep_age)} 天未执行（>30 天）→ 运行 python scripts/deep_check.py")
            else:
                print(f"  [INFO] 上次深度体检 {int(deep_age)} 天前")
        except Exception:
            print("  [WARN] 深度体检时间戳损坏 → 运行 python scripts/deep_check.py")
    else:
        print("  [WARN] 从未执行深度体检 → 运行 python scripts/deep_check.py 初始化")
    print("\n[测试]")
    check_item("smoke_test.py 存在", (PROJECT_DIR / "tests" / "smoke_test.py").exists())
    pytest_ok, pytest_summary = _run_pytest_check()
    check_item(f"pytest 全量通过 ({pytest_summary})", pytest_ok)
    check_item("HANDOVER.md 存在", (PROJECT_DIR / "docs" / "HANDOVER.md").exists())
    print("\n[前端构建]")
    if mode == "dev":
        vite_ok, vite_detail = _check_vite_dev_server()
        check_item(f"Vite Dev Server 可达 ({vite_detail})", vite_ok)
        # dev 模式也检查 dist 是否存在（以便快速切换 prod）
        dist_dir = PROJECT_DIR / "frontend" / "dist"
        has_dist = dist_dir.exists() and (dist_dir / "index.html").exists()
        check_item("前端生产构建产物存在 (fallback)", has_dist)
    else:
        fe_ok, fe_detail = _check_frontend_consistency()
        check_item(f"React SPA 构建产物完整 ({fe_detail})", fe_ok)
    print("\n[熵减审计 E1~E9]")
    try:
        result = subprocess.run(
            [sys.executable, str(PROJECT_DIR / "app" / "entropy_audit.py"), "--json"],
            capture_output=True, text=True, timeout=30, encoding="utf-8"
        )
        audit = json.loads(result.stdout)
        for code in ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9"]:
            entry = audit.get(code, {})
            passed = entry.get("passed", False)
            # E9 老化清理建议不强制 BLOCK（仅提醒）
            check_item(f"{code} {entry.get('name', code)}", passed)
    except Exception as e:
        check_item("熵减审计脚本可执行", False)
        print(f"    [WARN] entropy_audit.py 执行失败: {e}")
    print("\n[LLM]")
    check_item("LLM API 连通性", _check_llm_connectivity())
    print("\n[TODO 待办]")
    _check_todo_staleness()
    pct = int(score / max_score * 100) if max_score > 0 else 0
    print("\n" + "=" * 50)
    print(f"健康度: {score}/{max_score} ({pct}%)")
    if pct >= 80:
        print("[OK] 项目状态良好")
    elif pct >= 60:
        print("[WARN] 项目状态一般")
    else:
        print("[FAIL] 项目状态较差")
    print("=" * 50)

    # 写入健康自检报告缓存
    health_file = PROJECT_DIR / ".aikb" / "health_last.json"
    try:
        health_file.parent.mkdir(parents=True, exist_ok=True)
        health_data = {
            "timestamp": int(time.time()),
            "datetime": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "score": score,
            "max": max_score,
            "pct": pct,
            "mode": mode,
        }
        with open(health_file, "w", encoding="utf-8") as f:
            json.dump(health_data, f, ensure_ascii=False)
    except Exception as e:
        print(f"  [WARN] 无法写入 health_last.json: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI知识库 启动工具")
    parser.add_argument("--test", action="store_true", help="运行冒烟测试")
    parser.add_argument("--health", action="store_true", help="检查项目健康状态")
    parser.add_argument("--mode", choices=["dev", "prod"], default="prod", help="健康检查模式：dev(开发模式,检查Vite) / prod(生产模式,检查SPA构建)")
    parser.add_argument("--port", type=int, default=8501, help="服务端口（默认 8501）")
    parser.add_argument("--test-timeout", type=int, default=180, help="冒烟测试总超时秒数（默认 180）")
    parser.add_argument("--restart", action="store_true", help="启用异常退出自动重启")
    parser.add_argument("--max-restarts", type=int, default=5, help="最多自动重启次数（默认 5）")
    args = parser.parse_args()
    if args.health:
        run_health_check(mode=args.mode)
        sys.exit(0)
    if args.test:
        if not check_config():
            print("[WARN] 配置校验未通过，仍继续测试...")
        run_test(port=args.port, test_timeout=args.test_timeout)
        sys.exit(0)
    print("[INFO] AI知识库 启动检查...")
    if not check_config():
        print("[ERROR] 配置校验失败")
        sys.exit(1)
    if not check_dependencies():
        sys.exit(1)
    start_server(port=args.port, auto_restart=args.restart, max_restarts=args.max_restarts)
