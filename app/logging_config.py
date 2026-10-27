from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("veritas")
_LOCK = threading.Lock()
_SECRET_KEYS = re.compile(r"authorization|proxy-authorization|cookie|set-cookie|token|.*[_-]token|.*api[_-]?key.*|.*secret.*|.*password.*", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_KEYLIKE = re.compile(r"\b(nvapi-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,})")
_MAX_DETAIL_STRING = 300


def configure_stdout_logging() -> None:
    if _LOGGER.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def redact(value: Any, secrets: list[str] | None = None) -> Any:
    secrets = [s for s in (secrets or []) if len(s) >= 4]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out[key] = "[REDACTED]" if _SECRET_KEYS.fullmatch(str(key)) else redact(item, secrets)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        text = value
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _BEARER.sub("Bearer [REDACTED]", text)
        text = _KEYLIKE.sub("[REDACTED]", text)
        if len(text) > _MAX_DETAIL_STRING:
            text = text[:_MAX_DETAIL_STRING] + f"...[{len(text) - _MAX_DETAIL_STRING} chars truncated]"
        return text
    return value


class RunTrace:
    """Structured per-run audit trail written to stdout and logs/<run_id>.jsonl."""

    def __init__(self, run_id: str, trace_id: str, log_dir: Path, secrets: list[str]):
        self.run_id = run_id
        self.trace_id = trace_id
        self.secrets = secrets
        self.events: list[dict[str, Any]] = []
        self.log_file: Path | None = None
        self.file_error: str | None = None
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            candidate = log_dir / f"{run_id}.jsonl"
            with candidate.open("a", encoding="utf-8"):
                pass
            self.log_file = candidate
        except OSError as error:
            self.file_error = type(error).__name__

    def emit(
        self,
        stage: str,
        event: str,
        status: str = "ok",
        provider: str | None = None,
        model: str | None = None,
        duration_ms: int | None = None,
        **details: Any,
    ) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "stage": stage,
            "event": event,
            "provider": provider,
            "model": model,
            "duration_ms": duration_ms,
            "status": status,
            "details": redact(details, self.secrets),
        }
        self.events.append(record)
        line = json.dumps(record, ensure_ascii=False, default=str)
        _LOGGER.info(line)
        if self.log_file is not None:
            try:
                with _LOCK, self.log_file.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as error:
                self.file_error = type(error).__name__
                self.log_file = None
                _LOGGER.warning(json.dumps({"event": "file_logging_disabled", "trace_id": self.trace_id, "reason": self.file_error}))
        return record


class Stopwatch:
    def __init__(self) -> None:
        self.start = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)
