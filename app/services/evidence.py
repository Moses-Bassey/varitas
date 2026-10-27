from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas import Claim, Evidence

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
SAMPLE_METHOD = "sample_fixture: bundled fictional corpus, matched offline (no network)"
SAMPLE_RELATION_ASSESSOR = "sample_fixture_annotations"
UNASSESSED_BASIS = "Retrieved by keyword match only; relationship to the claim was not assessed because no reasoning model was available."

_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "have", "has", "had", "are", "was", "were", "been", "from", "since",
    "which", "into", "than", "then", "they", "their", "there", "its", "it's", "our", "your", "you", "will", "would",
    "every", "knows", "now", "before", "after", "also", "new", "city", "proves", "about", "what", "when", "where",
    "who", "why", "how", "not", "but", "can", "could", "should", "must", "all", "any", "some", "more", "less",
}
_TOKEN = re.compile(r"[a-z0-9]{3,30}")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


@lru_cache(maxsize=1)
def load_sample_case() -> dict[str, Any]:
    return json.loads((SAMPLE_DIR / "sample_case.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_sample_sources() -> list[dict[str, Any]]:
    return json.loads((SAMPLE_DIR / "sample_sources.json").read_text(encoding="utf-8"))["sources"]


def keywords(text: str, limit: int = 8) -> list[str]:
    """Lowercase alphanumeric tokens only; safe to embed in SQL string literals."""
    out: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token in _STOPWORDS or token.isdigit() or token in out:
            continue
        out.append(token)
        if len(out) >= limit:
            break
    return out


def best_excerpt(content: str, terms: list[str], max_chars: int = 500) -> str:
    scored: list[tuple[int, int, str]] = []
    for index, match in enumerate(_SENTENCE.finditer(content)):
        sentence = match.group(0).strip()
        lowered = sentence.lower()
        score = sum(1 for term in terms if term in lowered)
        if score:
            scored.append((score, -index, sentence))
    if not scored:
        return content[:max_chars].strip()
    scored.sort(reverse=True)
    return scored[0][2][:max_chars]


def sample_evidence(claims: list[Claim], retrieved_at: str) -> list[Evidence]:
    records: list[Evidence] = []
    for source in load_sample_sources():
        for assertion in source["assertions"]:
            terms = [t.lower() for t in assertion["claim_terms"]]
            matched = [c for c in claims if all(t in c.text.lower() for t in terms)]
            if not matched:
                continue
            records.append(
                Evidence(
                    id=f"ev-{len(records) + 1}",
                    title=source["title"],
                    url=source["url"],
                    source_id=source["source_id"],
                    publisher=source["publisher"],
                    published_at=source["published_at"],
                    retrieved_at=retrieved_at,
                    excerpt=assertion["excerpt"],
                    claim_ids=[c.id for c in matched],
                    relationships={c.id: assertion["relationship"] for c in matched},
                    relationship_basis={c.id: assertion["basis"] for c in matched},
                    relationship_assessed_by=SAMPLE_RELATION_ASSESSOR,
                    retrieval_method=SAMPLE_METHOD,
                    source_kind=source["source_kind"],
                    limitations=["Fictional demonstration source.", *assertion.get("limitations", [])],
                    sample_data=True,
                )
            )
    return records


def candidates_for_claims(
    sources: list[dict[str, Any]],
    claims: list[Claim],
    retrieved_at: str,
    method: str,
    per_claim: int = 3,
    extra_terms: dict[str, list[str]] | None = None,
) -> list[Evidence]:
    """Turn normalized source rows into evidence candidates whose relationship is not yet assessed."""
    by_source: dict[str, Evidence] = {}
    for claim in claims:
        if not claim.verifiable:
            continue
        terms = keywords(claim.text)
        terms += [t for t in (extra_terms or {}).get(claim.id, []) if t not in terms]
        ranked = []
        for source in sources:
            lowered = source["content"].lower()
            score = sum(1 for term in terms if term in lowered)
            if score >= max(1, min(2, len(terms))):
                ranked.append((score, source))
        ranked.sort(key=lambda item: -item[0])
        for _, source in ranked[:per_claim]:
            record = by_source.get(source["source_id"])
            if record is None:
                record = Evidence(
                    id=f"ev-{len(by_source) + 1}",
                    title=source["title"] or source["source_id"],
                    url=source.get("url") or None,
                    source_id=source["source_id"],
                    publisher=source.get("publisher") or None,
                    published_at=source.get("published_at") or None,
                    retrieved_at=retrieved_at,
                    excerpt=best_excerpt(source["content"], terms),
                    retrieval_method=method,
                    source_kind=source.get("source_kind") if source.get("source_kind") in {"primary", "secondary"} else "unknown",
                    limitations=[],
                )
                by_source[source["source_id"]] = record
            if claim.id not in record.claim_ids:
                record.claim_ids.append(claim.id)
                record.relationships[claim.id] = "does_not_establish"
                record.relationship_basis[claim.id] = UNASSESSED_BASIS
    return list(by_source.values())
