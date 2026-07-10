from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_LOGGER_READY = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _LOGGER_READY
    logger = logging.getLogger("plutotrade")
    if _LOGGER_READY:
        return logger

    project_root = Path(__file__).resolve().parents[2]
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "plutotrade.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    _LOGGER_READY = True
    logger.info("Logging initialized.")
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    parent = setup_logging()
    if not name:
        return parent
    return parent.getChild(name)

