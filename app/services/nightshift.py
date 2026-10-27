from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.logging_config import RunTrace
from app.schemas import BiasIndicator, Claim, Evidence
from app.security import INJECTION_GUARD, wrap_untrusted
from app.services.bias_analysis import claims_overlapping
from app.services.claims import MAX_CLAIMS, locate_span
from app.services.evidence import keywords
from app.services.http_client import Budget, ProviderError, request_json
from app.services.llm import chat_completion_json

PROVIDER = "meterless-nightshift"

CLAIM_TYPES = {"statistical", "causal", "event", "attribution", "comparative", "predictive", "opinion", "other"}
UNCERTAINTY = {"low", "moderate", "high", "very_high"}
TECHNIQUES = {
    "loaded_language", "false_dilemma", "unsupported_causation", "overgeneralization", "cherry_picking",
    "fear_or_urgency", "misleading_framing", "appeal_to_authority", "correlation_causation",
}
RELATIONSHIPS = {"supports", "contradicts", "contextualizes", "does_not_establish"}

_ROLE = (
    "You are an evidence analyst inside VERITAS, a fact-checking tool. Respond with a single JSON object only, no prose. "
    + INJECTION_GUARD
)


class NightshiftClient:
    """Meterless Nightshift local runtime (OpenAI-compatible /v1/chat/completions)."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, trace: RunTrace):
        self.settings = settings
        self.client = client
        self.trace = trace
        self.models_used: set[str] = set()
        self.last_model = "unreported_by_provider"

    def _headers(self, chat: bool = True) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.nightshift_token}",
            "meterless-app": self.settings.nightshift_app_id,
            "Content-Type": "application/json",
        }
        if chat:
            headers["meterless-scope"] = "user"
        return headers

    async def status(self, budget: Budget) -> str:
        """Official readiness probe: GET /v1/models. 401/403 = unauthorized, 200 with meterless ids = online."""
        try:
            result = await request_json(
                self.client, PROVIDER, "GET", f"{self.settings.nightshift_base_url}/models",
                budget=budget, timeout=min(10.0, self.settings.nightshift_timeout), retries=0, headers=self._headers(chat=False),
            )
        except ProviderError as error:
            return "unauthorized" if error.code == "unauthorized" else "offline"
        data = result.body.get("data") if isinstance(result.body, dict) else None
        ids = [str(m.get("id", "")) for m in data or [] if isinstance(m, dict)]
        return "online" if any(i.startswith("meterless/") for i in ids) else "offline"

    async def _ask(self, stage: str, task: str, material: str, budget: Budget, max_tokens: int = 1800) -> dict[str, Any]:
        parsed, model = await chat_completion_json(
            self.client,
            provider=PROVIDER,
            url=f"{self.settings.nightshift_base_url}/chat/completions",
            headers=self._headers(),
            model=self.settings.nightshift_model,
            system=_ROLE,
            user=f"{task}\n\n{material}",
            budget=budget,
            timeout=self.settings.nightshift_timeout,
            retries=self.settings.retry_attempts,
            trace=self.trace,
            stage=stage,
            max_tokens=max_tokens,
        )
        self.models_used.add(model)
        self.last_model = model
        return parsed

    async def extract_claims(self, content: str, budget: Budget) -> list[Claim]:
        task = (
            "Extract the distinct factual claims from the untrusted text. Return "
            '{"claims":[{"quote":"exact substring of the text","text":"the claim restated neutrally",'
            '"type":"statistical|causal|event|attribution|comparative|predictive|opinion|other",'
            '"verifiable":true,"uncertainty":"low|moderate|high|very_high"}]}. '
            f"At most {MAX_CLAIMS} claims. quote must be copied verbatim from the text."
        )
        data = await self._ask("claim_extraction", task, wrap_untrusted(content), budget)
        claims: list[Claim] = []
        dropped = 0
        for item in data.get("claims", [])[:MAX_CLAIMS] if isinstance(data.get("claims"), list) else []:
            if not isinstance(item, dict):
                dropped += 1
                continue
            span = locate_span(content, str(item.get("quote", "")))
            text = str(item.get("text") or item.get("quote") or "").strip()[:1000]
            claim_type = item.get("type") if item.get("type") in CLAIM_TYPES else "other"
            if span is None or not text:
                dropped += 1
                continue
            claims.append(
                Claim(
                    id=f"claim-{len(claims) + 1}",
                    text=text,
                    claim_type=claim_type,
                    verifiable=bool(item.get("verifiable", True)) and claim_type not in {"opinion"},
                    span=span,
                    initial_uncertainty=item.get("uncertainty") if item.get("uncertainty") in UNCERTAINTY else "high",
                    extracted_by=f"nightshift:{self.last_model}",
                )
            )
        self.trace.emit("claim_extraction", "claims_validated", provider=PROVIDER, model=self.last_model, accepted=len(claims), dropped_unanchored=dropped)
        if not claims:
            raise ProviderError(PROVIDER, "invalid_response", "Nightshift returned no claims anchored in the text")
        return claims

    async def detect_bias(self, content: str, claims: list[Claim], budget: Budget) -> list[BiasIndicator]:
        claim_list = json.dumps([{"id": c.id, "text": c.text} for c in claims], ensure_ascii=False)
        task = (
            "Identify persuasion techniques in the untrusted text. Allowed techniques: "
            + ", ".join(sorted(TECHNIQUES))
            + '. Return {"indicators":[{"technique":"...","quote":"exact substring","explanation":"why this wording fits",'
            '"claim_ids":["claim-1"]}]}. Return an empty list if the text is neutral. Describe wording only; never infer intent. '
            f"Claims: {claim_list}"
        )
        data = await self._ask("bias_analysis", task, wrap_untrusted(content), budget)
        found: list[BiasIndicator] = []
        dropped = 0
        valid_ids = {c.id for c in claims}
        for item in data.get("indicators", []) if isinstance(data.get("indicators"), list) else []:
            if not isinstance(item, dict) or item.get("technique") not in TECHNIQUES:
                dropped += 1
                continue
            span = locate_span(content, str(item.get("quote", "")))
            explanation = str(item.get("explanation", "")).strip()[:600]
            if span is None or not explanation:
                dropped += 1
                continue
            ids = [i for i in item.get("claim_ids", []) if i in valid_ids] if isinstance(item.get("claim_ids"), list) else []
            found.append(
                BiasIndicator(
                    id=f"bias-{len(found) + 1}",
                    technique=item["technique"],
                    explanation=explanation,
                    span=span,
                    claim_ids=ids or claims_overlapping(claims, span.start, span.end),
                    detected_by=f"nightshift:{self.last_model}",
                )
            )
        self.trace.emit("bias_analysis", "indicators_validated", provider=PROVIDER, model=self.last_model, accepted=len(found), dropped_unanchored=dropped)
        return found

    async def compare_evidence(self, claims: list[Claim], evidence: list[Evidence], budget: Budget) -> int:
        pairs = [
            {"evidence_id": e.id, "claim_id": cid, "claim": next(c.text for c in claims if c.id == cid), "excerpt": e.excerpt, "source": e.title}
            for e in evidence
            for cid in e.claim_ids
            if any(c.id == cid for c in claims)
        ]
        if not pairs:
            return 0
        task = (
            "For each claim/excerpt pair, decide how the excerpt relates to the claim. Use only the excerpt. Return "
            '{"relationships":[{"evidence_id":"ev-1","claim_id":"claim-1","relationship":"supports|contradicts|contextualizes|does_not_establish",'
            '"basis":"one sentence citing the excerpt"}]}.'
        )
        data = await self._ask("evidence_comparison", task, wrap_untrusted(json.dumps(pairs[:60], ensure_ascii=False)), budget, max_tokens=2500)
        by_id = {e.id: e for e in evidence}
        applied = 0
        for item in data.get("relationships", []) if isinstance(data.get("relationships"), list) else []:
            if not isinstance(item, dict):
                continue
            record = by_id.get(str(item.get("evidence_id")))
            claim_id = str(item.get("claim_id"))
            relation = item.get("relationship")
            basis = str(item.get("basis", "")).strip()[:500]
            if record is None or claim_id not in record.claim_ids or relation not in RELATIONSHIPS or not basis:
                continue
            record.relationships[claim_id] = relation
            record.relationship_basis[claim_id] = basis
            record.relationship_assessed_by = f"nightshift:{self.last_model}"
            applied += 1
        self.trace.emit("evidence_comparison", "relationships_validated", provider=PROVIDER, model=self.last_model, pairs=len(pairs), applied=applied)
        return applied

    async def refine_search_terms(self, claims: list[Claim], budget: Budget) -> dict[str, list[str]]:
        task = (
            "Suggest search keywords to find documents that would confirm or refute each claim. Return "
            '{"queries":[{"claim_id":"claim-1","keywords":["word"]}]}. Single lowercase words only, at most 6 per claim.'
        )
        material = wrap_untrusted(json.dumps([{"claim_id": c.id, "text": c.text} for c in claims], ensure_ascii=False))
        data = await self._ask("evidence_retrieval", task, material, budget, max_tokens=800)
        valid = {c.id for c in claims}
        out: dict[str, list[str]] = {}
        for item in data.get("queries", []) if isinstance(data.get("queries"), list) else []:
            if isinstance(item, dict) and item.get("claim_id") in valid and isinstance(item.get("keywords"), list):
                terms = keywords(" ".join(str(k) for k in item["keywords"]), limit=6)
                if terms:
                    out[item["claim_id"]] = terms
        return out

    async def synthesize(self, query: str, structured: dict[str, Any], budget: Budget) -> str:
        task = (
            "Write a neutral 3-5 sentence summary of this investigation for a general reader. Use only the verdicts and "
            "evidence provided; do not add facts, do not infer anyone's intent, and mention uncertainty. Return "
            '{"summary":"..."}.'
        )
        material = wrap_untrusted(json.dumps({"question": query, **structured}, ensure_ascii=False)[:20000])
        data = await self._ask("report_synthesis", task, material, budget, max_tokens=700)
        summary = str(data.get("summary", "")).strip()
        if not summary:
            raise ProviderError(PROVIDER, "invalid_response", "Nightshift returned an empty summary")
        return summary[:1500]
