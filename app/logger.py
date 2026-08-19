import logging
import sys
import re
from datetime import datetime

from loguru import logger as _logger

from app.runtime_paths import runtime_paths


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


_print_level = "INFO"


def _redact_runtime_record(record) -> bool:
    """Redact secrets while retaining local paths needed for desktop delivery."""
    message = str(record["message"])
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,]+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)((?:api[_-]?key|token|password)\s*[:=]\s*[\"']?)[^\s,\"']+",
        r"\1[REDACTED]",
        message,
    )
    record["message"] = message
    return True


def define_log_level(print_level="INFO", logfile_level="DEBUG", name: str = None):
    """Adjust the log level to above level"""
    global _print_level
    _print_level = print_level

    current_date = datetime.now()
    formatted_date = current_date.strftime("%Y%m%d%H%M%S")
    log_name = (
        f"{name}_{formatted_date}" if name else formatted_date
    )  # name a log with prefix name

    _logger.remove()
    _logger.add(sys.stderr, level=print_level, filter=_redact_runtime_record)
    runtime_paths.ensure_user_directories()
    _logger.add(
        runtime_paths.log_dir / f"{log_name}.log",
        level=logfile_level,
        rotation="20 MB",
        retention="14 days",
        compression="zip",
        filter=_redact_runtime_record,
    )
    return _logger


logger = define_log_level()


if __name__ == "__main__":
    logger.info("Starting application")
    logger.debug("Debug message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")

    try:
        raise ValueError("Test error")
    except Exception as e:
        logger.exception(f"An error occurred: {e}")
