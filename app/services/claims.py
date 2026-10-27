from __future__ import annotations

import re

from app.schemas import Claim, TextSpan

LOCAL_EXTRACTOR = "local_heuristic_extractor"

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_CLAUSE_BREAK = re.compile(r",\s+(?=which\s+(?:proves|shows|means|confirms)\b)", re.I)
_LABEL_PREFIX = re.compile(r"^\s*[A-Z]{3,}(?:\s+[A-Z]{3,})*:\s*")

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("attribution", re.compile(r"\b(confirmed|said|says|according to|claims?|reported|knows|stated|admitted)\b", re.I)),
    ("causal", re.compile(r"\b(caus\w*|because|due to|to blame|proves?|destroy\w*|leads? to|result(?:s|ed)? in|draining|responsible for)\b", re.I)),
    ("statistical", re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent|per cent)|\b\d{2,}\b", re.I)),
    ("comparative", re.compile(r"\b(more|less|fewer|higher|lower|worse|better)\s+than\b", re.I)),
    ("predictive", re.compile(r"\b(will|forever|going to|either\b.+\bor)\b", re.I)),
    ("opinion", re.compile(r"\b(act now|should|must|we need|too late|i think|i believe)\b", re.I)),
    ("event", re.compile(r"\b(opened|happened|occurred|announced|launched|began|arrested|died)\b", re.I)),
]

_MIN_CLAIM_CHARS = 12
MAX_CLAIMS = 25


def _segments(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _SENTENCE.finditer(content):
        start, end = match.start(), match.end()
        sentence = content[start:end]
        prefix = _LABEL_PREFIX.match(sentence)
        if prefix:
            start += prefix.end()
        cursor = start
        for brk in _CLAUSE_BREAK.finditer(content, start, end):
            spans.append((cursor, brk.start()))
            cursor = brk.end()
        spans.append((cursor, end))
    cleaned: list[tuple[int, int]] = []
    for start, end in spans:
        while start < end and content[start].isspace():
            start += 1
        while end > start and content[end - 1].isspace():
            end -= 1
        if end - start >= _MIN_CLAIM_CHARS:
            cleaned.append((start, end))
    return cleaned


def classify(text: str) -> str:
    for claim_type, pattern in _RULES:
        if pattern.search(text):
            return claim_type
    return "other"


def extract_claims_locally(content: str) -> list[Claim]:
    claims: list[Claim] = []
    for start, end in _segments(content)[:MAX_CLAIMS]:
        text = content[start:end]
        claim_type = classify(text)
        verifiable = claim_type not in {"opinion", "predictive"}
        claims.append(
            Claim(
                id=f"claim-{len(claims) + 1}",
                text=text,
                claim_type=claim_type,  # type: ignore[arg-type]
                verifiable=verifiable,
                span=TextSpan(start=start, end=end, text=text),
                initial_uncertainty="high" if verifiable else "very_high",
                extracted_by=LOCAL_EXTRACTOR,
            )
        )
    return claims


def locate_span(content: str, quote: str) -> TextSpan | None:
    """Find a model-supplied quote in the source so every span points at real text."""
    quote = quote.strip()
    if not quote:
        return None
    index = content.find(quote)
    if index < 0:
        index = content.lower().find(quote.lower())
    if index < 0:
        return None
    return TextSpan(start=index, end=index + len(quote), text=content[index : index + len(quote)])
