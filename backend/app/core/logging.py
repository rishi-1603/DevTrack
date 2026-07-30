"""Application-wide logging configuration.

Sets up three handlers:
  - console handler (all levels, for local dev visibility)
  - RotatingFileHandler -> logs/app.log (all levels)
  - RotatingFileHandler -> logs/error.log (ERROR and above)
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from app.core.config import settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 5

_configured = False


def setup_logging() -> None:
    """Configure the root application logger. Idempotent."""
    global _configured
    if _configured:
        return

    os.makedirs(settings.LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT)

    root_logger = logging.getLogger("app")
    root_logger.setLevel(settings.LOG_LEVEL)
    root_logger.propagate = False

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(settings.LOG_LEVEL)

    # All-logs file handler
    app_log_path = os.path.join(settings.LOG_DIR, "app.log")
    app_file_handler = RotatingFileHandler(app_log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    app_file_handler.setFormatter(formatter)
    app_file_handler.setLevel(settings.LOG_LEVEL)

    # Errors-only file handler
    error_log_path = os.path.join(settings.LOG_DIR, "error.log")
    error_file_handler = RotatingFileHandler(error_log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    error_file_handler.setFormatter(formatter)
    error_file_handler.setLevel(logging.ERROR)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(app_file_handler)
    root_logger.addHandler(error_file_handler)

    _configured = True


def get_access_logger() -> logging.Logger:
    """Return a dedicated logger that writes HTTP access logs to logs/access.log."""
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    access_logger = logging.getLogger("app.access")
    if not access_logger.handlers:
        access_logger.setLevel(logging.INFO)
        access_logger.propagate = False
        access_log_path = os.path.join(settings.LOG_DIR, "access.log")
        handler = RotatingFileHandler(access_log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        access_logger.addHandler(handler)
    return access_logger


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger namespaced under 'app'."""
    return logging.getLogger(f"app.{name}")
