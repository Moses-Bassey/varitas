from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

Verdict = Literal["supported", "contradicted", "mixed_evidence", "unverified", "insufficient_evidence"]
Uncertainty = Literal["low", "moderate", "high", "very_high"]
Relationship = Literal["supports", "contradicts", "contextualizes", "does_not_establish"]
RunStatus = Literal["success", "partial", "error"]
ServiceState = Literal["completed", "failed", "skipped", "not_configured", "disabled_sample_mode", "unsupported"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigationInput(Strict):
    query: str = Field(min_length=1, max_length=2000)
    content: str | None = Field(default=None, max_length=200_000)
    url: str | None = Field(default=None, max_length=2048)
    media_url: str | None = Field(default=None, max_length=2048)
    transcript: str | None = Field(default=None, max_length=200_000)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class InvestigationOptions(Strict):
    sample_mode: bool | None = None
    include_provenance: bool = True
    include_media_analysis: bool = False
    include_nvidia_review: bool = True
    use_memory: bool = True


class InvestigateRequest(Strict):
    run_id: str | None = None
    input: InvestigationInput
    options: InvestigationOptions = Field(default_factory=InvestigationOptions)

    @field_validator("run_id")
    @classmethod
    def run_id_format(cls, value: str | None) -> str | None:
        if value is not None and not RUN_ID_PATTERN.match(value):
            raise ValueError("run_id must be 1-64 characters: letters, digits, dot, dash or underscore")
        return value


class TextSpan(BaseModel):
    start: int
    end: int
    text: str


class Claim(BaseModel):
    id: str
    text: str
    claim_type: Literal["statistical", "causal", "event", "attribution", "comparative", "predictive", "opinion", "other"]
    verifiable: bool
    span: TextSpan | None = None
    initial_uncertainty: Uncertainty
    extracted_by: str


class BiasIndicator(BaseModel):
    id: str
    technique: Literal[
        "loaded_language",
        "false_dilemma",
        "unsupported_causation",
        "overgeneralization",
        "cherry_picking",
        "fear_or_urgency",
        "misleading_framing",
        "appeal_to_authority",
        "correlation_causation",
    ]
    explanation: str
    span: TextSpan
    claim_ids: list[str] = Field(default_factory=list)
    detected_by: str
    caution: str = "An indicator of a persuasive technique, not proof of falsity or intent."


class Evidence(BaseModel):
    id: str
    title: str
    url: str | None = None
    source_id: str
    publisher: str | None = None
    published_at: str | None = None
    retrieved_at: str
    excerpt: str
    claim_ids: list[str] = Field(default_factory=list)
    relationships: dict[str, Relationship] = Field(default_factory=dict)
    relationship_basis: dict[str, str] = Field(default_factory=dict)
    relationship_assessed_by: str | None = None
    retrieval_method: str
    source_kind: Literal["primary", "secondary", "unknown"] = "unknown"
    limitations: list[str] = Field(default_factory=list)
    sample_data: bool = False


class MemoryRecall(BaseModel):
    memory_id: str
    content: str
    source: str
    relevance: float
    confidence: float
    recalled_from: str
    note: str = "Remembered finding. Not evidence for this case until its sources are rechecked."


class ClaimAssessment(BaseModel):
    claim_id: str
    verdict: Verdict
    uncertainty: Uncertainty
    supporting_evidence: list[str]
    contradicting_evidence: list[str]
    context_evidence: list[str]
    reasoning: str
    evidence_gaps: list[str]
    alternative_explanations: list[str]
    assessed_by: str


class ReviewFinding(BaseModel):
    claim_id: str | None = None
    concern: str
    severity: Literal["info", "minor", "major"]
    reviewer: str


class MediaResult(BaseModel):
    media_ref: str
    status: ServiceState
    detector: str | None = None
    findings: list[str] = Field(default_factory=list)
    note: str


class ProvenanceNode(BaseModel):
    id: str
    type: Literal[
        "input_media",
        "claim",
        "text_span",
        "evidence_source",
        "excerpt",
        "analysis_finding",
        "media_detector_result",
        "hypothesis",
        "conclusion",
        "recommendation",
    ]
    label: str
    data: dict[str, Any] = Field(default_factory=dict)


class ProvenanceEdge(BaseModel):
    id: str
    source: str
    target: str
    type: Literal[
        "contains",
        "derived_from",
        "supports",
        "contradicts",
        "contextualizes",
        "evaluates",
        "indicates_possible_manipulation",
        "leads_to",
    ]
    basis: str = Field(min_length=1)


class ProvenanceGraph(BaseModel):
    nodes: list[ProvenanceNode] = Field(default_factory=list)
    edges: list[ProvenanceEdge] = Field(default_factory=list)


class AgentExecution(BaseModel):
    name: str
    service: str
    status: ServiceState
    provider: str | None = None
    model: str | None = None
    duration_ms: int = 0
    detail: str = ""


class InvestigationOutput(BaseModel):
    summary: str
    overall_assessment: str
    sample_mode: bool
    claims: list[Claim]
    bias_indicators: list[BiasIndicator]
    evidence: list[Evidence]
    assessments: list[ClaimAssessment]
    memory: list[MemoryRecall]
    review: list[ReviewFinding]
    media: list[MediaResult]
    provenance: ProvenanceGraph | None
    unresolved_questions: list[str]
    next_steps: list[str]
    limitations: list[str]


class InvestigateResponse(BaseModel):
    status: RunStatus
    run_id: str
    output: InvestigationOutput
    agents: list[AgentExecution]
    trace_id: str
    log_file: str | None
    execution_time_seconds: float


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    run_id: str | None = None
    error: ErrorBody
    trace_id: str
