from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.logging_config import RunTrace
from app.schemas import Claim, ClaimAssessment, MemoryRecall
from app.services.http_client import Budget, ProviderError, request_json

PROVIDER = "meterless-hmem"

_CONFIDENCE = {"low": 0.85, "moderate": 0.7, "high": 0.5, "very_high": 0.3}


class HMemClient:
    """Client for the H-MEM sidecar (engines/hmem-sidecar), which runs the official Meterless H-MEM reference engine."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, trace: RunTrace):
        self.settings = settings
        self.client = client
        self.trace = trace
        self.engine: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.hmem_token:
            headers["Authorization"] = f"Bearer {self.settings.hmem_token}"
        return headers

    async def _call(self, stage: str, method: str, path: str, budget: Budget, body: Any = None) -> dict[str, Any]:
        try:
            result = await request_json(
                self.client, PROVIDER, method, f"{self.settings.hmem_url}{path}",
                budget=budget, timeout=self.settings.hmem_timeout, retries=self.settings.retry_attempts,
                headers=self._headers(), json_body=body,
            )
        except ProviderError as error:
            self.trace.emit(stage, "hmem_call", "failed", provider=PROVIDER, path=path, error_code=error.code, message=str(error))
            raise
        data = result.body if isinstance(result.body, dict) else {}
        self.engine = str(data.get("engine") or self.engine or "unreported")
        self.trace.emit(stage, "hmem_call", "ok", provider=PROVIDER, model=self.engine, duration_ms=result.duration_ms, path=path)
        return data

    async def health(self, budget: Budget) -> dict[str, Any]:
        return await self._call("health", "GET", "/health", budget)

    async def recall(self, text: str, budget: Budget, top_n: int = 5) -> list[MemoryRecall]:
        data = await self._call(
            "memory_recall", "POST", "/v1/query", budget, {"text": text[:4000], "topN": top_n, "threshold": self.settings.hmem_recall_threshold}
        )
        recalls: list[MemoryRecall] = []
        for item in data.get("memories", []) if isinstance(data.get("memories"), list) else []:
            memory = item.get("memory") if isinstance(item, dict) else None
            if not isinstance(memory, dict) or not memory.get("id"):
                continue
            recalls.append(
                MemoryRecall(
                    memory_id=str(memory["id"]),
                    content=str(memory.get("content", ""))[:1000],
                    source=str(memory.get("source", "")),
                    relevance=float(item.get("relevance", 0.0)),
                    confidence=float(memory.get("confidence", 0.0)),
                    recalled_from=f"H-MEM ({self.engine})",
                )
            )
        trace_info = data.get("trace") if isinstance(data.get("trace"), dict) else {}
        self.trace.emit("memory_recall", "hmem_recall", provider=PROVIDER, model=self.engine, recalled=len(recalls), strategy=trace_info.get("strategy"))
        return recalls

    async def remember(self, run_id: str, claims: list[Claim], assessments: list[ClaimAssessment], relation_origin: str, budget: Budget) -> list[str]:
        by_id = {c.id: c for c in claims}
        stored: list[str] = []
        for assessment in assessments:
            if assessment.verdict not in {"supported", "contradicted", "mixed_evidence"}:
                continue
            claim = by_id[assessment.claim_id]
            evidence_ids = assessment.supporting_evidence + assessment.contradicting_evidence
            data = await self._call(
                "memory_store",
                "POST",
                "/v1/memories",
                budget,
                {
                    "content": f"Claim \"{claim.text[:500]}\" was assessed as {assessment.verdict} ({assessment.uncertainty} uncertainty) in VERITAS run {run_id}.",
                    "type": "factual",
                    "layer": "working",
                    "tags": ["veritas", assessment.verdict, claim.claim_type],
                    "confidence": _CONFIDENCE[assessment.uncertainty],
                    "source": f"veritas:run:{run_id}:{assessment.claim_id}",
                    "provenance": {"origin": relation_origin, "label": "VERITAS claim assessment", "refs": evidence_ids},
                },
            )
            memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
            if memory.get("id"):
                stored.append(str(memory["id"]))
        self.trace.emit("memory_store", "hmem_stored", provider=PROVIDER, model=self.engine, stored=len(stored))
        return stored
