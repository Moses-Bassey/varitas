from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.logging_config import RunTrace
from app.schemas import Claim, ClaimAssessment, Evidence, ReviewFinding
from app.security import INJECTION_GUARD, wrap_untrusted
from app.services.llm import chat_completion_json

PROVIDER = "nvidia-nim"
VERDICTS = {"supported", "contradicted", "mixed_evidence", "unverified", "insufficient_evidence"}
SEVERITIES = {"info", "minor", "major"}

_ROLE = (
    "You are an independent second reviewer auditing another analyst's fact-check. Look for weak or misread evidence, "
    "missing counterevidence, unsupported reasoning and overconfident verdicts. Respond with one JSON object only. "
    + INJECTION_GUARD
)


class NvidiaReviewer:
    """Second-pass critique on NVIDIA NIM (OpenAI-compatible), kept separate from the Nightshift analysis."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, trace: RunTrace):
        self.settings = settings
        self.client = client
        self.trace = trace
        self.model_used: str | None = None

    async def review(self, claims: list[Claim], evidence: list[Evidence], assessments: list[ClaimAssessment], budget) -> tuple[list[ReviewFinding], list[dict]]:
        material = {
            "claims": [{"id": c.id, "text": c.text} for c in claims],
            "evidence": [{"id": e.id, "source": e.title, "kind": e.source_kind, "excerpt": e.excerpt, "relationships": e.relationships} for e in evidence],
            "verdicts": [{"claim_id": a.claim_id, "verdict": a.verdict, "uncertainty": a.uncertainty, "reasoning": a.reasoning} for a in assessments],
        }
        task = (
            "Review the verdicts against the evidence. Return "
            '{"findings":[{"claim_id":"claim-1 or null","concern":"specific issue","severity":"info|minor|major",'
            '"suggested_verdict":"supported|contradicted|mixed_evidence|unverified|insufficient_evidence or null"}]}. '
            "Return an empty list if the verdicts are sound."
        )
        parsed, model = await chat_completion_json(
            self.client,
            provider=PROVIDER,
            url=f"{self.settings.nvidia_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.nvidia_api_key}", "Content-Type": "application/json", "Accept": "application/json"},
            model=self.settings.nvidia_model,
            system=_ROLE,
            user=f"{task}\n\n{wrap_untrusted(json.dumps(material, ensure_ascii=False)[:24000])}",
            budget=budget,
            timeout=self.settings.nvidia_timeout,
            retries=self.settings.retry_attempts,
            trace=self.trace,
            stage="nvidia_review",
            max_tokens=1500,
        )
        self.model_used = model
        reviewer = f"nvidia:{model}"
        current = {a.claim_id: a.verdict for a in assessments}
        findings: list[ReviewFinding] = []
        disagreements: list[dict] = []
        for item in parsed.get("findings", []) if isinstance(parsed.get("findings"), list) else []:
            if not isinstance(item, dict):
                continue
            concern = str(item.get("concern", "")).strip()[:600]
            if not concern:
                continue
            claim_id = item.get("claim_id") if item.get("claim_id") in current else None
            severity = item.get("severity") if item.get("severity") in SEVERITIES else "minor"
            suggested = item.get("suggested_verdict") if item.get("suggested_verdict") in VERDICTS else None
            if claim_id and suggested and suggested != current[claim_id]:
                disagreements.append({"claim_id": claim_id, "primary_verdict": current[claim_id], "reviewer_verdict": suggested})
                concern = f"Reviewer disagrees: suggests {suggested} instead of {current[claim_id]}. {concern}"
                severity = "major"
            findings.append(ReviewFinding(claim_id=claim_id, concern=concern, severity=severity, reviewer=reviewer))
        self.trace.emit("nvidia_review", "review_validated", provider=PROVIDER, model=model, findings=len(findings), disagreements=disagreements)
        return findings, disagreements
