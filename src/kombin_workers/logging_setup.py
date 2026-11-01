"""structlog setup. Logs go to stderr so stdout stays clean for `check`'s JSON."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import Processor


def configure_logging(*, json_logs: bool, level: int = logging.INFO) -> None:
    renderer: Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
