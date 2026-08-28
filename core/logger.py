"""
AI知识库 - 统一日志配置
所有模块通过 getLogger(__name__) 获取 logger，格式和级别在此统一控制
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 项目级格式
_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# 日志落盘位置：项目根 logs/ai_kb.log（2026-08-19 新增，控制台刷屏时可查文件）
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "ai_kb.log"


def _build_file_handler() -> logging.Handler:
    """滚动文件 handler：5MB × 3 份，全量 INFO 日志落盘"""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(str(_LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
    return handler


def get_logger(name: str = "ai_kb", *, file_only: bool = False) -> logging.Logger:
    """获取项目 logger：默认控制台+文件双输出；file_only=True 时只写文件（用于访问日志降噪）"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        if not file_only:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
            logger.addHandler(handler)
        logger.addHandler(_build_file_handler())
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
