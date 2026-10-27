from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path

from app.schemas import InvestigateResponse


class RunStore:
    """Keeps recent results in memory and mirrors them to <log_dir>/runs/<run_id>.json when writable."""

    def __init__(self, log_dir: Path, capacity: int = 200):
        self.capacity = capacity
        self.items: OrderedDict[str, InvestigateResponse] = OrderedDict()
        self.reserved: set[str] = set()
        self.lock = threading.Lock()
        self.dir: Path | None = log_dir / "runs"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.dir = None

    def _path(self, run_id: str) -> Path | None:
        return self.dir / f"{run_id}.json" if self.dir else None

    def reserve(self, run_id: str) -> bool:
        with self.lock:
            path = self._path(run_id)
            if run_id in self.items or run_id in self.reserved or (path is not None and path.exists()):
                return False
            self.reserved.add(run_id)
            return True

    def save(self, result: InvestigateResponse) -> None:
        with self.lock:
            self.reserved.discard(result.run_id)
            self.items[result.run_id] = result
            self.items.move_to_end(result.run_id)
            while len(self.items) > self.capacity:
                self.items.popitem(last=False)
        path = self._path(result.run_id)
        if path is not None:
            try:
                path.write_text(result.model_dump_json(), encoding="utf-8")
            except OSError:
                pass

    def release(self, run_id: str) -> None:
        with self.lock:
            self.reserved.discard(run_id)

    def get(self, run_id: str) -> InvestigateResponse | None:
        with self.lock:
            if run_id in self.items:
                return self.items[run_id]
        path = self._path(run_id)
        if path is not None and path.exists():
            try:
                return InvestigateResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return None
        return None

    def delete(self, run_id: str) -> bool:
        removed = False
        with self.lock:
            removed = self.items.pop(run_id, None) is not None
        path = self._path(run_id)
        if path is not None and path.exists():
            path.unlink(missing_ok=True)
            removed = True
        return removed
