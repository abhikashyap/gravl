"""structlog setup — JSON in prod, console-pretty when a TTY is attached."""

from __future__ import annotations

import logging
import sys

import structlog


def configure() -> None:
    if getattr(configure, "_done", False):
        return
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if sys.stdout.isatty():
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())
    structlog.configure(processors=processors, wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    configure._done = True  # type: ignore[attr-defined]


def get_logger(name: str) -> structlog.BoundLogger:
    configure()
    return structlog.get_logger(name)
