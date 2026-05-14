from __future__ import annotations

import sys

from loguru import logger

from .config import get_settings


_INITIALIZED = False


def init_logging(level: str | None = None) -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    settings = get_settings()
    logger.remove()
    logger.add(
        sys.stderr,
        level=level or settings.pifinder_log_level,
        format=(
            "<dim>{time:HH:mm:ss}</dim> <level>{level:<7}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<dim>{line}</dim> | {message}"
        ),
        colorize=True,
        enqueue=False,
    )
    _INITIALIZED = True
