from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import settings


_LOGGING_CONFIGURED = False
_DEBUGGER_STARTED = False


def setup_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    level = getattr(logging, settings.log_level, logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = settings.log_dir / "backend.log"
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
        )
    except OSError:
        # Console logging still gives us useful diagnostics if the log path is unavailable.
        pass

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "apscheduler"):
        logging.getLogger(logger_name).setLevel(level)
    _LOGGING_CONFIGURED = True


def maybe_start_debugger() -> None:
    global _DEBUGGER_STARTED
    logger = logging.getLogger(__name__)
    if not settings.debugpy_enabled or _DEBUGGER_STARTED:
        return
    try:
        import debugpy

        debugpy.listen((settings.debugpy_host, settings.debugpy_port))
        _DEBUGGER_STARTED = True
        logger.info("debugpy listening on %s:%s", settings.debugpy_host, settings.debugpy_port)
        if settings.debugpy_wait_for_client:
            logger.info("waiting for debugger client to attach")
            debugpy.wait_for_client()
    except Exception:
        logger.exception("failed to start debugpy")
