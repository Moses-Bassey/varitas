from __future__ import annotations

from app.schemas import Claim, ClaimAssessment, Evidence

RULE_ASSESSOR = "evidence_rule_engine"

_UNCERTAINTY_ORDER = ["low", "moderate", "high", "very_high"]


def raise_uncertainty(level: str) -> str:
    index = _UNCERTAINTY_ORDER.index(level)
    return _UNCERTAINTY_ORDER[min(index + 1, len(_UNCERTAINTY_ORDER) - 1)]


def _strength(evidence: list[Evidence]) -> str:
    primaries = sum(1 for e in evidence if e.source_kind == "primary")
    if len(evidence) >= 2 and primaries >= 1:
        return "low"
    if primaries >= 1:
        return "moderate"
    return "high"


def assess_claim(claim: Claim, evidence: list[Evidence]) -> ClaimAssessment:
    """Verdict and uncertainty are derived only from the recorded evidence relationships."""
    linked = [e for e in evidence if claim.id in e.relationships]
    supports = [e for e in linked if e.relationships[claim.id] == "supports"]
    contradicts = [e for e in linked if e.relationships[claim.id] == "contradicts"]
    context = [e for e in linked if e.relationships[claim.id] == "contextualizes"]
    gaps: list[str] = []

    if not claim.verifiable:
        verdict, uncertainty = "unverified", "very_high"
        reasoning = "This statement is an opinion, prediction or call to action, so it cannot be checked against evidence."
    elif not linked:
        verdict, uncertainty = "unverified", "very_high"
        reasoning = "No evidence was retrieved for this claim."
        gaps.append("No source addressing this claim was found in the configured data sources.")
    elif supports and contradicts:
        verdict, uncertainty = "mixed_evidence", "high"
        reasoning = f"{len(supports)} source excerpt(s) support and {len(contradicts)} contradict this claim."
    elif supports:
        verdict, uncertainty = "supported", _strength(supports)
        reasoning = f"Supported by {len(supports)} source excerpt(s); none contradict it."
    elif contradicts:
        verdict, uncertainty = "contradicted", _strength(contradicts)
        reasoning = f"Contradicted by {len(contradicts)} source excerpt(s); none support it."
    else:
        verdict, uncertainty = "insufficient_evidence", "high"
        reasoning = "Retrieved sources provide context but do not establish or refute the claim."

    decisive = supports or contradicts
    if claim.verifiable and linked:
        if decisive and not any(e.source_kind == "primary" for e in decisive):
            gaps.append("No primary source directly addresses this claim.")
        if len({e.source_id for e in linked}) == 1:
            gaps.append("Only one independent source was found.")
        if any(lim for e in decisive for lim in e.limitations if "Fictional" not in lim):
            uncertainty_note = "; ".join(sorted({lim for e in decisive for lim in e.limitations if "Fictional" not in lim}))
            reasoning += f" Source limitations: {uncertainty_note}"

    alternatives = [e.relationship_basis.get(claim.id, "") for e in context if e.relationship_basis.get(claim.id)]

    return ClaimAssessment(
        claim_id=claim.id,
        verdict=verdict,  # type: ignore[arg-type]
        uncertainty=uncertainty,  # type: ignore[arg-type]
        supporting_evidence=[e.id for e in supports],
        contradicting_evidence=[e.id for e in contradicts],
        context_evidence=[e.id for e in context],
        reasoning=reasoning,
        evidence_gaps=gaps,
        alternative_explanations=alternatives,
        assessed_by=RULE_ASSESSOR,
    )
