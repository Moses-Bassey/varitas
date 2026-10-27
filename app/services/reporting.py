from __future__ import annotations

from collections import Counter

from app.schemas import AgentExecution, BiasIndicator, Claim, ClaimAssessment, Evidence, InvestigateResponse, MemoryRecall, ReviewFinding

SAMPLE_BANNER = "DEMONSTRATION: sample mode used bundled fictional data. No live data source, model or engine was called."


def overall_assessment(assessments: list[ClaimAssessment]) -> str:
    counts = Counter(a.verdict for a in assessments)
    if not assessments:
        return "No checkable claims were found."
    checkable = sum(counts[v] for v in ("supported", "contradicted", "mixed_evidence"))
    parts = ", ".join(f"{n} {verdict.replace('_', ' ')}" for verdict, n in sorted(counts.items()))
    if checkable == 0:
        return f"The material could not be verified with the available evidence ({parts})."
    if counts["contradicted"] and counts["contradicted"] >= counts["supported"]:
        return f"Key claims are contradicted by the available evidence ({parts})."
    if counts["supported"] and not counts["contradicted"] and not counts["mixed_evidence"]:
        return f"Checked claims are supported by the available evidence ({parts})."
    return f"The evidence is mixed across claims ({parts})."


def build_summary(
    query: str, claims: list[Claim], bias: list[BiasIndicator], evidence: list[Evidence], assessments: list[ClaimAssessment], sample_mode: bool
) -> str:
    techniques = sorted({b.technique.replace("_", " ") for b in bias})
    lead = f"{SAMPLE_BANNER} " if sample_mode else ""
    bias_text = (
        f"Persuasion indicators found: {', '.join(techniques)}. These describe wording, not intent."
        if techniques
        else "No persuasion indicators were detected; the wording appears neutral."
    )
    return (
        f"{lead}Investigated: \"{query}\". Extracted {len(claims)} claim(s) and compared them with "
        f"{len({e.source_id for e in evidence})} source(s). {overall_assessment(assessments)} {bias_text}"
    )


def unresolved_questions(claims: list[Claim], assessments: list[ClaimAssessment]) -> list[str]:
    by_id = {c.id: c for c in claims}
    out: list[str] = []
    for a in assessments:
        claim = by_id.get(a.claim_id)
        if claim is None:
            continue
        if a.verdict in {"mixed_evidence", "insufficient_evidence"} or (claim.verifiable and a.verdict == "unverified"):
            out.append(f"What evidence would settle {a.claim_id}: \"{claim.text}\"?")
        for alt in a.alternative_explanations:
            out.append(f"Was {a.claim_id} affected by an alternative explanation? {alt}")
    return out[:12]


def next_steps(assessments: list[ClaimAssessment], evidence: list[Evidence], live_sources_used: bool) -> list[str]:
    steps: list[str] = []
    if any(a.verdict == "unverified" and a.evidence_gaps for a in assessments):
        steps.append("Locate primary sources for unverified claims, such as official statistics or the original statement.")
    if any(a.verdict in {"mixed_evidence", "insufficient_evidence"} for a in assessments):
        steps.append("Seek data that tests causation directly, such as before/after measurements with a comparison group.")
    if any("Only one independent source was found." in a.evidence_gaps for a in assessments):
        steps.append("Corroborate single-source findings with an independent second source.")
    if not live_sources_used:
        steps.append("Repeat with live data sources configured to replace demonstration or missing evidence.")
    steps.append("Have a human reviewer confirm conclusions before publishing or acting on them.")
    return steps


def limitations(sample_mode: bool, agents: list[AgentExecution], extra: list[str]) -> list[str]:
    out: list[str] = []
    if sample_mode:
        out.append(SAMPLE_BANNER)
    for agent in agents:
        if agent.status in {"failed", "not_configured", "skipped", "unsupported"}:
            out.append(f"{agent.name} ({agent.service}): {agent.status}. {agent.detail}".strip())
    out.extend(extra)
    out.append("Verdicts reflect only the evidence retrieved in this run; absence of evidence is not evidence of absence.")
    out.append("Bias indicators and manipulation signals must not be used to infer anyone's guilt, identity, affiliation or intent.")
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def render_markdown(result: InvestigateResponse) -> str:
    o = result.output
    lines = [f"# VERITAS report — run `{result.run_id}`", "", f"**Status:** {result.status}  ", f"**Trace:** `{result.trace_id}`", ""]
    lines += ["## 1. Executive summary", o.summary, "", "## 2. Overall assessment", o.overall_assessment, "", "## 3. Claims"]
    assessments = {a.claim_id: a for a in o.assessments}
    for c in o.claims:
        a = assessments.get(c.id)
        verdict = f"**{a.verdict}** ({a.uncertainty} uncertainty)" if a else "not assessed"
        cites = ", ".join(f"[{eid}]" for eid in (a.supporting_evidence + a.contradicting_evidence + a.context_evidence)) if a else ""
        lines.append(f"- `{c.id}` {c.text} — {verdict} {cites}".rstrip())
    lines += ["", "## 4. Persuasion and bias indicators"]
    lines += [f"- `{b.id}` **{b.technique}** — \"{b.span.text}\": {b.explanation}" for b in o.bias_indicators] or ["- None detected."]
    lines += ["", "## 5. Evidence"]
    for e in o.evidence:
        rel = ", ".join(f"{cid}: {r}" for cid, r in e.relationships.items())
        lines.append(f"- [{e.id}] {e.title} — {e.publisher or 'unknown publisher'}, {e.published_at or 'undated'}. \"{e.excerpt}\" ({rel})")
    lines += ["", "## 6. Claim-by-claim reasoning"]
    for a in o.assessments:
        lines.append(f"- `{a.claim_id}`: {a.reasoning}" + (f" Gaps: {'; '.join(a.evidence_gaps)}" if a.evidence_gaps else ""))
    lines += ["", "## 7. Remembered context (not evidence)"]
    lines += [f"- {m.content} (from {m.recalled_from}, relevance {m.relevance:.2f})" for m in o.memory] or ["- None."]
    lines += ["", "## 8. Independent review"]
    lines += [f"- [{r.severity}] {r.concern} ({r.reviewer})" for r in o.review] or ["- No review findings."]
    lines += ["", "## 9. Media analysis"]
    lines += [f"- {m.media_ref}: {m.status}. {m.note}" for m in o.media] or ["- No media supplied."]
    lines += ["", "## 10. Provenance", f"{len(o.provenance.nodes) if o.provenance else 0} nodes, {len(o.provenance.edges) if o.provenance else 0} edges."]
    lines += ["", "## 11. Unresolved questions and next steps"] + [f"- {q}" for q in o.unresolved_questions] + [f"- Next: {s}" for s in o.next_steps]
    lines += ["", "## 12. Limitations"] + [f"- {x}" for x in o.limitations]
    return "\n".join(lines) + "\n"
