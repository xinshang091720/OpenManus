import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

def setup_logger():
    """
    配置增强型日志器：
    1. 支持按天滚动备份，避免磁盘爆满。
    2. 包含进程、线程和详细代码位置，便于排查异步/并发问题。
    3. 统一控制台与文件输出格式。
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # 避免重复添加处理器（在某些热重载环境下很重要）
    if logger.handlers:
        return logger

    # --- 屏蔽第三方库的详细日志 (新增部分) ---
    for logger_name in ["unstructured", "unstructured_inference", "pdfminer", "transformers", "huggingface_hub", "torch", "urllib3", "httpx", "httpcore"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # --- 路径配置 ---
    current_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(current_dir, "logs")
    log_file = "app.log"
    log_path = os.path.join(log_dir, log_file)
    os.makedirs(log_dir, exist_ok=True)

    # --- 日志格式设置 ---
    log_format = (
        '%(asctime)s - %(filename)s:%(lineno)d - [%(levelname)s] - %(message)s'
    )
    formatter = logging.Formatter(log_format)

    # --- 处理器 1: 控制台 (Stdout) ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # --- 处理器 2: 定时滚动文件 (TimedRotatingFileHandler) ---
    # when='D' 表示按天切割，interval=1 表示 1 天一个文件
    # backupCount=30 表示保留最近 30 天的日志
    file_handler = TimedRotatingFileHandler(
        log_path,
        when='D',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    # --- 添加到主 Logger ---
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

# 预实例化一个 logger 方便直接引用
logger = setup_logger()

if __name__ == "__main__":
    # 测试代码
    logger.info("日志系统启动成功")
    logger.debug("这是调试信息")
    try:
        1 / 0
    except Exception as e:
        # exc_info=True 会自动记录完整的堆栈信息
        logger.error("捕获到异常示例", exc_info=True)