import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler


def setup_logger(name: str = "cad_agent"):
    """
    配置增强型日志器：
    1. 支持按天滚动备份，保存至 ohresult/logs/app.log。
    2. 包含控制台与文件双通道输出。
    3. 屏蔽底层第三方库的冗余警告。
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 检查是否已经添加过 FileHandler，若已配置则直接返回，避免重复打印
    has_file_handler = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    if has_file_handler:
        return logger

    # --- 屏蔽第三方库的冗余日志 ---
    for lib_name in [
        "unstructured", "unstructured_inference", "pdfminer",
        "transformers", "huggingface_hub", "torch", "urllib3",
        "ezdxf", "httpx", "httpcore", "openai"
    ]:
        logging.getLogger(lib_name).setLevel(logging.WARNING)

    # --- 路径配置：保存到 ohresult/logs/app.log ---
    current_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(current_dir, "logs")
    log_path = os.path.join(log_dir, "app.log")
    os.makedirs(log_dir, exist_ok=True)

    # --- 日志格式设置 ---
    log_format = (
        '%(asctime)s - %(filename)s:%(lineno)d - [%(levelname)s] - %(message)s'
    )
    formatter = logging.Formatter(log_format)

    # 清除旧的未命名 handler 避免重复
    logger.handlers.clear()

    # --- 处理器 1: 控制台 (Stdout) ---
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    class FlushStreamHandler(logging.StreamHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()

    class FlushTimedRotatingFileHandler(TimedRotatingFileHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()

    console_handler = FlushStreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # --- 处理器 2: 定时滚动文件 (FlushTimedRotatingFileHandler) ---
    # when='D' 表示按天切割，interval=1 表示 1 天一个文件，保留 30 天，且每行写入立即刷盘
    file_handler = FlushTimedRotatingFileHandler(
        log_path,
        when='D',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False

    return logger


# 预实例化一个 logger 方便直接引用
logger = setup_logger()

if __name__ == "__main__":
    logger.info("日志系统启动成功，正在写入文件 app.log")
    logger.debug("这是调试信息")
    try:
        1 / 0
    except Exception as e:
        logger.error("捕获到异常示例", exc_info=True)