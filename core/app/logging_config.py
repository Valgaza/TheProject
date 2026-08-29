"""Logging setup.

The memory service declares module loggers but never configures logging, so its
``logger.info`` calls are invisible at uvicorn's defaults. Every service should
call :func:`configure_logging` once, during startup, before it logs anything.
"""

from __future__ import annotations

import logging
import os
from logging.config import dictConfig

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Install a console handler on the root logger.

    ``level`` falls back to the ``LOG_LEVEL`` environment variable, then INFO.
    Uvicorn's own loggers are left to propagate so everything shares a format.
    """
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {"format": _FORMAT, "datefmt": _DATEFMT},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "console",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"handlers": ["console"], "level": resolved},
            "loggers": {
                # uvicorn installs its own handlers; drop them so records reach
                # the root handler exactly once instead of being printed twice.
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": True},
                # httpx logs a line per request; graphiti makes many.
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                # The Neo4j driver reports "index already exists" as an INFO
                # notification on every startup after the first. CHECKPOINT_02
                # calls these out as expected noise; at INFO they bury
                # everything else.
                "neo4j.notifications": {"level": "WARNING"},
                "neo4j.pool": {"level": "WARNING"},
            },
        }
    )
    logging.getLogger(__name__).debug("Logging configured at %s", resolved)
