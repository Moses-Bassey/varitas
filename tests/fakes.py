"""Test doubles for the sponsor services. These are mocks used only by the test suite, never by the application."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

ZETARIS_ROW = [
    "src-traffic-2025",
    "City Transport Safety Report 2025",
    "https://transport.example.gov/report-2025",
    "City Transport Department",
    "2026-01-10",
    "primary",
    "Traffic deaths in 2025 rose from 12 to 14. The bridge opened in June 2025. The bridge cost 40 million.",
]


def _chat(content: Any, model: str) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"role": "assistant", "content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


@dataclass
class FakeServices:
    requests: list[httpx.Request] = field(default_factory=list)
    nightshift_model: str = "meterless/deep:resolved-test-model"
    nvidia_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    zetaris_rows: list[list[str]] = field(default_factory=lambda: [ZETARIS_ROW])
    overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = field(default_factory=dict)

    def calls(self, host: str, path_suffix: str = "") -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host and r.url.path.endswith(path_suffix)]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        override = self.overrides.get(request.url.host)
        if override:
            return override(request)
        if request.url.host == "nightshift.test":
            return self.nightshift(request)
        if request.url.host == "zetaris.test":
            return httpx.Response(200, json={"data": self.zetaris_rows})
        if request.url.host == "hmem.test":
            return self.hmem(request)
        if request.url.host == "nvidia.test":
            return self.nvidia(request)
        return httpx.Response(404, json={"error": "unknown host"})

    def nightshift(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "meterless/auto"}]})
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"]
        if "Extract the distinct factual claims" in prompt:
            return _chat({"claims": [
                {"quote": "The new bridge caused traffic deaths to double in 2025.", "text": "The new bridge caused traffic deaths to double in 2025.", "type": "causal", "verifiable": True, "uncertainty": "high"},
                {"quote": "City officials said the bridge cost 40 million.", "text": "The bridge cost 40 million.", "type": "statistical", "verifiable": True, "uncertainty": "moderate"},
                {"quote": "this sentence is not in the text", "text": "hallucinated", "type": "event", "verifiable": True},
            ]}, self.nightshift_model)
        if "persuasion techniques" in prompt:
            return _chat({"indicators": [
                {"technique": "overgeneralization", "quote": "Every driver knows", "explanation": "Generalizes to all drivers.", "claim_ids": []},
                {"technique": "made_up_technique", "quote": "bridge", "explanation": "x"},
            ]}, self.nightshift_model)
        if "For each claim/excerpt pair" in prompt:
            pairs = json.loads(prompt.split("<<<UNTRUSTED_CONTENT>>>\n", 1)[1].rsplit("\n<<<END_UNTRUSTED_CONTENT>>>", 1)[0])
            rels = []
            for pair in pairs:
                relation = "contradicts" if "double" in pair["claim"] else "supports"
                rels.append({"evidence_id": pair["evidence_id"], "claim_id": pair["claim_id"], "relationship": relation, "basis": f"Excerpt says: {pair['excerpt'][:60]}"})
            return _chat({"relationships": rels}, self.nightshift_model)
        if "Suggest search keywords" in prompt:
            return _chat({"queries": [{"claim_id": "claim-1", "keywords": ["fatalities", "collisions"]}]}, self.nightshift_model)
        if "neutral 3-5 sentence summary" in prompt:
            return _chat({"summary": "The bridge cost claim is supported; the deaths claim is contradicted."}, self.nightshift_model)
        return _chat({}, self.nightshift_model)

    def hmem(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/query":
            return httpx.Response(200, json={"engine": "meterless-hmem-reference", "memories": [
                {"memory": {"id": "mem-7", "content": "Prior run: bridge claims lacked baseline data.", "source": "veritas:run:old", "confidence": 0.6}, "relevance": 0.71}
            ], "context": "", "trace": {"strategy": "hybrid"}})
        if request.url.path == "/v1/memories":
            return httpx.Response(201, json={"engine": "meterless-hmem-reference", "memory": {"id": f"mem-new-{len(self.requests)}"}})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "engine": "meterless-hmem-reference"})
        return httpx.Response(404, json={"error": "not found"})

    def nvidia(self, request: httpx.Request) -> httpx.Response:
        return _chat({"findings": [
            {"claim_id": "claim-2", "concern": "Single source for the cost figure.", "severity": "minor", "suggested_verdict": "insufficient_evidence"},
            {"claim_id": None, "concern": "No independent counterevidence was sought.", "severity": "info", "suggested_verdict": None},
        ]}, self.nvidia_model)
