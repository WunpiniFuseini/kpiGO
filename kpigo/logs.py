"""Structured JSON log lines carrying ``request_id`` and ``action_name`` (TDD §11)."""

import json
import logging
from datetime import UTC, datetime

_FIELDS = ("request_id", "action_name", "caller", "outcome", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                line[field] = value
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)
