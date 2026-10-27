from __future__ import annotations

import re
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.logging_config import RunTrace
from app.services.http_client import Budget, ProviderError, request_json

PROVIDER = "zetaris"
RUN_SQL_PATH = "/sql-editor/sqls/run"
COLUMNS = ("source_id", "title", "url", "publisher", "published_at", "source_kind", "content")

_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,2}$")
_TERM = re.compile(r"^[a-z0-9]{3,30}$")


class ZetarisClient:
    """Zetaris direct REST channel, as used by the official zetaris_sql.py RestChannel.

    POST {ZETARIS_REST_URL}/sql-editor/sqls/run with Bearer API key and X-Org-ID; rows are returned in "data".
    Queries are read-only SELECTs over a single configured evidence table; user text never reaches SQL except as
    validated lowercase alphanumeric search terms.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient, trace: RunTrace):
        if not _TABLE.match(settings.zetaris_evidence_table):
            raise ValueError("ZETARIS_EVIDENCE_TABLE must be a plain [catalog.]database.table identifier")
        self.settings = settings
        self.client = client
        self.trace = trace
        self.queries: list[dict[str, Any]] = []

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.zetaris_api_key}",
            "X-Org-ID": self.settings.zetaris_org_id,
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def build_search_sql(self, terms: list[str]) -> str:
        safe = [t for t in terms if _TERM.match(t)]
        if not safe:
            raise ValueError("no valid search terms")
        where = " OR ".join(f"lower(content) LIKE '%{t}%'" for t in safe[:12])
        return f"SELECT {', '.join(COLUMNS)} FROM {self.settings.zetaris_evidence_table} WHERE {where}"

    async def run_sql(self, sql: str, budget: Budget, stage: str = "evidence_retrieval") -> list[Any]:
        query_id = str(uuid.uuid4())
        body = {"queryId": query_id, "sql": sql, "source": "SqlEditor", "limit": self.settings.zetaris_query_limit}
        meta: dict[str, Any] = {"query_id": query_id, "endpoint": RUN_SQL_PATH, "table": self.settings.zetaris_evidence_table, "limit": body["limit"]}
        self.trace.emit(stage, "zetaris_query", "started", provider=PROVIDER, sql=sql, **meta)
        try:
            result = await request_json(
                self.client, PROVIDER, "POST", f"{self.settings.zetaris_rest_url}{RUN_SQL_PATH}",
                budget=budget, timeout=self.settings.zetaris_timeout, retries=self.settings.retry_attempts,
                headers=self._headers(), json_body=body,
            )
        except ProviderError as error:
            self.queries.append({**meta, "status": "failed", "error_code": error.code})
            self.trace.emit(stage, "zetaris_query", "failed", provider=PROVIDER, error_code=error.code, message=str(error), **meta)
            raise
        data = result.body.get("data") if isinstance(result.body, dict) else None
        if not isinstance(data, list):
            self.queries.append({**meta, "status": "failed", "error_code": "invalid_response"})
            self.trace.emit(stage, "zetaris_query", "failed", provider=PROVIDER, error_code="invalid_response", duration_ms=result.duration_ms, **meta)
            raise ProviderError(PROVIDER, "invalid_response", "Zetaris response had no 'data' rows")
        self.queries.append({**meta, "status": "ok", "rows": len(data), "duration_ms": result.duration_ms})
        self.trace.emit(stage, "zetaris_query", "ok", provider=PROVIDER, duration_ms=result.duration_ms, rows=len(data), attempts=result.attempts, **meta)
        return data

    async def health(self, budget: Budget) -> bool:
        rows = await self.run_sql("SELECT 1", budget, stage="health")
        return bool(rows)

    async def search(self, terms: list[str], budget: Budget) -> list[dict[str, str]]:
        rows = await self.run_sql(self.build_search_sql(terms), budget)
        sources = [s for s in (normalize_row(r) for r in rows) if s]
        self.trace.emit("evidence_retrieval", "zetaris_rows_normalized", provider=PROVIDER, rows=len(rows), usable=len(sources))
        return sources


def normalize_row(row: Any) -> dict[str, str] | None:
    """Rows arrive positionally (list) for the explicit column order selected above; dict rows are also accepted."""
    if isinstance(row, dict):
        values = {c: row.get(c) for c in COLUMNS}
    elif isinstance(row, (list, tuple)) and len(row) == len(COLUMNS):
        values = dict(zip(COLUMNS, row))
    else:
        return None
    clean = {k: ("" if v is None else str(v).strip()) for k, v in values.items()}
    if not clean["source_id"] or not clean["content"]:
        return None
    if clean["url"] and not clean["url"].lower().startswith(("http://", "https://")):
        clean["url"] = ""
    clean["content"] = clean["content"][:20000]
    return clean
