"""structlog configuration.

Two hard rules, both of which are tested:

* Every line carries the correlation ID, tenant and user from the context vars.
* **No PII and no document content is ever logged.** ``tests/security`` seeds canary
  strings and asserts they never appear in captured output.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from insightlm.shared.observability.correlation import (
    current_correlation_id,
    current_tenant_id,
    current_user_id,
)


def _bind_request_context(
    _logger: object, _name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    if (cid := current_correlation_id()) is not None:
        event_dict.setdefault("correlation_id", cid)
    if (tid := current_tenant_id()) is not None:
        event_dict.setdefault("tenant_id", str(tid))
    if (uid := current_user_id()) is not None:
        event_dict.setdefault("user_id", str(uid))
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _bind_request_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
