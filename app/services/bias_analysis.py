from __future__ import annotations

import re

from app.schemas import BiasIndicator, Claim, TextSpan

LOCAL_DETECTOR = "local_pattern_detector"

LOCAL_LIMITATION = (
    "The local pattern detector only finds surface wording. It cannot detect cherry-picking or misleading framing, "
    "which need contextual reasoning by the Nightshift model."
)

_LOADED_WORDS = (
    "destroying|destroyed|devastat\\w*|catastroph\\w*|disaster\\w*|outrageous|evil|slams?|toxic|poison\\w*|"
    "scandal\\w*|shocking|horrif\\w*|radical|corrupt\\w*|rigged|plague"
)

_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "loaded_language",
        re.compile(rf"\b({_LOADED_WORDS})\b", re.I),
        "Emotionally charged wording that asserts a strong judgement without supporting detail.",
    ),
    (
        "loaded_language",
        re.compile(r"\b[A-Z]{4,}\b"),
        "Emphatic capitalization raises emotional intensity without adding information.",
    ),
    (
        "false_dilemma",
        re.compile(r"\beither\b[^.!?]{1,140}?\bor\b[^.!?]*", re.I),
        "Presents two options as the only possibilities, excluding alternatives such as monitoring or mitigation.",
    ),
    (
        "unsupported_causation",
        re.compile(r"\b(is to blame|proves?|caused by|is responsible for|because of)\b", re.I),
        "Asserts a cause without citing the evidence that would establish it.",
    ),
    (
        "overgeneralization",
        re.compile(r"\b(every(?:one|body)?|nobody|no one|always|never)\s+\w+", re.I),
        "Generalizes to an entire group or to all cases from what is at most partial information.",
    ),
    (
        "fear_or_urgency",
        re.compile(r"\b(act now|before it'?s too late|breaking|urgent|forever|immediately|last chance)\b", re.I),
        "Urgency or fear cues that discourage pausing to check the claim.",
    ),
    (
        "appeal_to_authority",
        re.compile(
            r"\b(?:an?|one|the)\s+(?:leading|top|renowned|prominent|famous|respected)\s+"
            r"(?:scientist|expert|doctor|researcher|professor|official)s?\b|\bexperts\s+(?:say|agree|confirm)\b",
            re.I,
        ),
        "Cites an unnamed or unverifiable authority in place of evidence.",
    ),
    (
        "correlation_causation",
        re.compile(r"\bsince\b[^.!?]{1,160}?\b(which proves|which shows|proves|so|therefore|means)\b", re.I),
        "Treats two things happening in the same period as proof that one caused the other.",
    ),
]

_ALLCAPS_EXEMPT = {"NASA", "NATO", "COVID", "UNESCO", "UNICEF", "FIFA", "OPEC"}


def detect_bias_locally(content: str, claims: list[Claim]) -> list[BiasIndicator]:
    seen: set[tuple[str, int, int]] = set()
    found: list[BiasIndicator] = []
    for technique, pattern, explanation in _RULES:
        for match in pattern.finditer(content):
            text = match.group(0)
            if technique == "loaded_language" and text.isupper() and text in _ALLCAPS_EXEMPT:
                continue
            key = (technique, match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            found.append(
                BiasIndicator(
                    id="",
                    technique=technique,  # type: ignore[arg-type]
                    explanation=explanation,
                    span=TextSpan(start=match.start(), end=match.end(), text=text),
                    claim_ids=claims_overlapping(claims, match.start(), match.end()),
                    detected_by=LOCAL_DETECTOR,
                )
            )
    found.sort(key=lambda item: (item.span.start, item.technique))
    for index, indicator in enumerate(found, start=1):
        indicator.id = f"bias-{index}"
    return found


def claims_overlapping(claims: list[Claim], start: int, end: int) -> list[str]:
    return [c.id for c in claims if c.span and c.span.start < end and start < c.span.end]
