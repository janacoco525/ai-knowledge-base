"""AI 知识库启动器 — PyInstaller 入口"""
import os
import sys
import time
import threading
import webbrowser


def get_base_dir():
    """获取程序根目录（兼容开发模式和打包模式）"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包模式
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return base


def get_data_dir():
    """获取用户数据目录（.env、vector_data 等）"""
    if getattr(sys, "frozen", False):
        # 打包模式：exe 同目录下的 data/ 文件夹
        exe_dir = os.path.dirname(sys.executable)
        data_dir = os.path.join(exe_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    else:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main():
    # 设置基础目录
    base_dir = get_base_dir()
    data_dir = get_data_dir()

    # 将 base_dir 加入 sys.path（让 app 包可被导入）
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    # 也将 app/rag_app 目录加入 path（兼容旧式导入）
    rag_dir = os.path.join(base_dir, "app", "rag_app")
    if os.path.isdir(rag_dir) and rag_dir not in sys.path:
        sys.path.insert(0, rag_dir)

    # 设置环境变量，供 config.py 读取
    env_path = os.path.join(data_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

    # 覆盖关键路径
    os.environ.setdefault("VECTOR_DATA_DIR", os.path.join(data_dir, "data", "vector"))
    os.environ.setdefault("ROUTES_DATA_DIR", os.path.join(data_dir, "data", "routes"))

    # 延迟导入，确保路径设置完成
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8501"))

    # 自动打开浏览器（延迟 2 秒等服务器就绪）
    def open_browser():
        time.sleep(2)
        url = f"http://{host}:{port}"
        print(f"Opening browser: {url}")
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print(f"=" * 50)
    print(f"  本地 AI 知识库 v1.0")
    print(f"  地址: http://{host}:{port}")
    print(f"  数据目录: {data_dir}")
    print(f"=" * 50)

    # 使用字符串导入，兼容 PyInstaller 打包
    uvicorn.run("app.rag_app.api_server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
