"""
AI知识库 - 配置管理
统一管理所有配置项
"""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent.parent.resolve()
load_dotenv(dotenv_path=PROJECT_DIR / ".env")


class Config:
    """应用配置 — 项目真值: docs/项目真值.md"""

    # 产品版本（唯一代码锚点，须与 docs/VERSION.md 保持同步）
    PRODUCT_VERSION: str = "3.8.1"

    # LLM配置
    # 注：变量名沿用 STEP_* 历史壳，但当前允许承载任意 OpenAI 兼容 provider。
    # 支持的模型包括：
    # - DeepSeek: deepseek-chat @ https://api.deepseek.com (不加/v1, OpenAI SDK自动补)
    # - Qwen3.7-Max: qwen3.7-max @ https://dashscope.aliyuncs.com/compatible-mode/v1
    STEP_API_KEY: str = os.getenv("STEP_API_KEY", "")
    STEP_API_BASE: str = os.getenv("STEP_API_BASE", "https://api.stepfun.com/v1")  # ⚠️ 仅作默认值；当前活跃 provider 见 providers.py 和 .env
    STEP_MODEL: str = os.getenv("STEP_MODEL", "step-2-16k")  # ⚠️ 仅作默认值；运行时以 .env 和 provider 系统为准

    # Embedding配置（离线TF-IDF+SVD，不下载模型）
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "tfidf-svd-offline")
    EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")

    # 向量数据目录（pickle格式：bm25_data.pkl, embeddings.pkl, tfidf_svd.pkl）
    VECTOR_DATA_DIR: str = os.getenv("VECTOR_DATA_DIR", "") or str((PROJECT_DIR / "chroma_db").resolve())
    # 注: chroma_db目录名为历史遗留，实际存的是pickle文件，暂不重命名

    # 用户数据目录（向下兼容：也作为 SCAN_PATHS 的单值后备）
    DATA_DIR: str = os.getenv("DATA_DIR", "")

    # 多文件夹扫描路径（逗号分隔，优先级高于 DATA_DIR）
    _scan_paths_raw: str = os.getenv("SCAN_PATHS", "")

    @classmethod
    def get_scan_paths(cls) -> list:
        """返回所有扫描路径，优先 SCAN_PATHS，其次 DATA_DIR
        （2026-08-20 结构化：resolve 规范化 + 去重保序，杜绝同一目录多种写法并存）"""
        raw = []
        if cls._scan_paths_raw:
            raw = [p.strip() for p in cls._scan_paths_raw.split(",") if p.strip()]
        elif cls.DATA_DIR:
            raw = [cls.DATA_DIR]
        seen, out = set(), []
        for p in raw:
            try:
                norm = str(Path(p).resolve())
            except Exception:
                norm = p
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out

    # 应用运行时数据目录（卡片、阅读记录等JSON持久化文件）
    ROUTES_DATA_DIR: str = os.getenv("ROUTES_DATA_DIR", str(Path(__file__).parent / "data"))

    # 文档解析参数
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))
    TOP_K: int = int(os.getenv("TOP_K", "5"))

    # RRF混合检索参数
    RRF_K: int = int(os.getenv("RRF_K", "60"))
    RRF_VEC_WEIGHT: float = float(os.getenv("RRF_VEC_WEIGHT", "0.7"))
    RRF_BM25_WEIGHT: float = float(os.getenv("RRF_BM25_WEIGHT", "0.3"))

    # 知识域配置
    KNOWLEDGE_DOMAINS: dict = {
        "default": "默认分类",  # 用户可按需通过设置页创建自己的分类
    }

    # 快捷提问
    QUICK_QUESTIONS: list = [
        {"title": "文档总结", "desc": "快速了解任意文档的核心内容", "question": "请总结我当前打开的这篇文档的核心要点"},
        {"title": "概念解释", "desc": "用通俗语言解释文档中的专业术语", "question": "请用通俗的语言解释文档中提到的关键概念"},
        {"title": "关联分析", "desc": "找出不同文档之间的关联", "question": "我知识库里的文档之间有哪些共同主题和关联？"},
        {"title": "知识问答", "desc": "基于你的文档内容回答具体问题", "question": "根据我已导入的文档，[在此输入你的问题]"},
    ]

    # 支持的文件格式
    SUPPORTED_EXTENSIONS: set = {
        ".pdf", ".docx", ".txt", ".md",
        ".py", ".js", ".json", ".csv",
        ".epub", ".mobi",
    }

    @classmethod
    def validate(cls) -> tuple[bool, str]:
        if not cls.STEP_API_KEY:
            return False, "STEP_API_KEY is required"
        return True, "OK"


config = Config()
