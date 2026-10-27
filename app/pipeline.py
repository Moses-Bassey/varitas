from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from app.config import Settings
from app.logging_config import RunTrace, Stopwatch
from app.schemas import (
    AgentExecution,
    BiasIndicator,
    Claim,
    ClaimAssessment,
    Evidence,
    InvestigateRequest,
    InvestigateResponse,
    InvestigationOutput,
    MediaResult,
    MemoryRecall,
    ReviewFinding,
)
from app.services import evidence as evidence_svc
from app.services import reporting
from app.services.assessment import RULE_ASSESSOR, assess_claim, raise_uncertainty
from app.services.bias_analysis import LOCAL_DETECTOR, LOCAL_LIMITATION, detect_bias_locally
from app.services.claims import LOCAL_EXTRACTOR, extract_claims_locally
from app.services.http_client import Budget, ProviderError
from app.services.media_forensics import analyze_media
from app.services.meterless_engine import PROVIDER as HMEM_PROVIDER
from app.services.meterless_engine import HMemClient
from app.services.nightshift import PROVIDER as NIGHTSHIFT_PROVIDER
from app.services.nightshift import NightshiftClient
from app.services.nvidia_review import PROVIDER as NVIDIA_PROVIDER
from app.services.nvidia_review import NvidiaReviewer
from app.services.provenance import build_graph
from app.services.text_fetch import FetchError, fetch_text
from app.services.zetaris import PROVIDER as ZETARIS_PROVIDER
from app.services.zetaris import ZetarisClient

CORE_LIVE_SERVICES = {NIGHTSHIFT_PROVIDER, ZETARIS_PROVIDER}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Investigation:
    def __init__(self, request: InvestigateRequest, settings: Settings, run_id: str, trace: RunTrace, transport: httpx.AsyncBaseTransport | None = None):
        self.request = request
        self.settings = settings
        self.run_id = run_id
        self.trace = trace
        self.transport = transport
        options = request.options
        self.sample_mode = settings.sample_mode if options.sample_mode is None else options.sample_mode
        self.budget = Budget(settings.budget_seconds, settings.max_iterations)
        self.started = time.perf_counter()

        self.content = ""
        self.input_kind = "text"
        self.input_label = request.input.query
        self.claims: list[Claim] = []
        self.bias: list[BiasIndicator] = []
        self.evidence: list[Evidence] = []
        self.assessments: list[ClaimAssessment] | None = None
        self.memory: list[MemoryRecall] = []
        self.review: list[ReviewFinding] = []
        self.media: list[MediaResult] = []
        self.summary: str | None = None
        self.agents: list[AgentExecution] = []
        self.extra_limitations: list[str] = []
        self.budget_hit = False
        self.relations_by: str | None = None
        self.nightshift: NightshiftClient | None = None
        self.nightshift_ok = False

    def agent(self, name: str, service: str, status: str, clock: Stopwatch, provider: str | None = None, model: str | None = None, detail: str = "") -> None:
        self.agents.append(
            AgentExecution(name=name, service=service, status=status, provider=provider, model=model, duration_ms=clock.ms(), detail=detail)  # type: ignore[arg-type]
        )
        self.trace.emit(name, "stage_complete", status, provider=provider, model=model, duration_ms=clock.ms(), detail=detail)

    def _out_of_time(self, name: str, service: str) -> bool:
        if self.budget.exhausted():
            self.budget_hit = True
            self.agent(name, service, "skipped", Stopwatch(), detail="Skipped: investigation time budget exhausted.")
            return True
        return False

    async def run(self) -> InvestigateResponse:
        self.trace.emit("run", "started", sample_mode=self.sample_mode, budget_seconds=self.settings.budget_seconds, max_iterations=self.settings.max_iterations)
        if self.sample_mode:
            await self._run_stages(None)
        else:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False) as client:
                await self._run_stages(client)
        return self.finalize()

    async def _run_stages(self, client: httpx.AsyncClient | None) -> None:
        if client is not None and self.settings.nightshift_configured:
            self.nightshift = NightshiftClient(self.settings, client, self.trace)
        await self._ingest(client)
        await self._extract_claims()
        await self._detect_bias()
        await self._recall_memory(client)
        await self._retrieve_evidence(client)
        await self._compare_evidence()
        self.assessments = self._assess()
        await self._review(client)
        await self._store_memory(client)
        self._analyze_media()
        await self._synthesize()

    async def _ingest(self, client: httpx.AsyncClient | None) -> None:
        clock = Stopwatch()
        data = self.request.input
        parts = [p for p in (data.content, data.transcript) if p and p.strip()]
        if data.transcript and data.transcript.strip():
            self.input_kind = "transcript" if not data.content else "text_and_transcript"
        if data.url:
            if self.sample_mode:
                self.agent("url_fetch", "web", "disabled_sample_mode", clock, detail="URL not fetched: sample mode makes no network requests.")
            elif not self.settings.url_fetch_enabled:
                self.agent("url_fetch", "web", "skipped", clock, detail="URL fetching is disabled by URL_FETCH_ENABLED=false.")
            else:
                try:
                    text, title = await fetch_text(client, data.url, self.budget, self.settings.max_fetch_bytes, self.trace)  # type: ignore[arg-type]
                    parts.append(text)
                    self.input_kind = "url" if len(parts) == 1 else "text_and_url"
                    self.input_label = f"{title} ({data.url})"
                    self.agent("url_fetch", "web", "completed", clock, detail=f"Fetched {len(text)} characters.")
                except FetchError as error:
                    self.agent("url_fetch", "web", "failed", clock, detail=f"{error.code}: {error}")
        if not parts and self.sample_mode:
            case = evidence_svc.load_sample_case()
            parts = [case["content"]]
            self.input_kind = "sample_document"
            self.input_label = case["title"]
        if not parts:
            parts = [data.query]
            self.input_kind = "query"
        content = "\n\n".join(parts)
        if len(content) > self.settings.max_content_chars:
            content = content[: self.settings.max_content_chars]
            self.extra_limitations.append(f"Input was truncated to {self.settings.max_content_chars} characters for analysis.")
        self.content = content
        self.trace.emit("input_ingestion", "content_ready", input_kind=self.input_kind, chars=len(content))

    async def _with_nightshift(
        self, name: str, call: Callable[[], Awaitable[Any]], fallback: Callable[[], Any], local_label: str
    ) -> Any:
        clock = Stopwatch()
        if self.sample_mode:
            result = fallback()
            self.agent(name, NIGHTSHIFT_PROVIDER, "disabled_sample_mode", clock, provider=local_label, detail=f"Sample mode: deterministic {local_label} used; Nightshift not called.")
            return result
        if self.nightshift is None:
            result = fallback()
            self.agent(name, NIGHTSHIFT_PROVIDER, "not_configured", clock, provider=local_label, detail=f"Nightshift not configured; explicit fallback to {local_label}.")
            return result
        if self._out_of_time(name, NIGHTSHIFT_PROVIDER):
            return fallback()
        try:
            result = await call()
            self.nightshift_ok = True
            self.agent(name, NIGHTSHIFT_PROVIDER, "completed", clock, provider=NIGHTSHIFT_PROVIDER, model=self.nightshift.last_model)
            return result
        except ProviderError as error:
            self.trace.emit(name, "fallback", "degraded", provider=local_label, reason=error.code)
            result = fallback()
            self.agent(name, NIGHTSHIFT_PROVIDER, "failed", clock, provider=NIGHTSHIFT_PROVIDER, model=self.nightshift.last_model, detail=f"{error.code}: {error}. Explicit fallback to {local_label}.")
            return result

    async def _extract_claims(self) -> None:
        ns = self.nightshift
        self.claims = await self._with_nightshift(
            "claim_extraction",
            lambda: ns.extract_claims(self.content, self.budget),  # type: ignore[union-attr]
            lambda: extract_claims_locally(self.content),
            LOCAL_EXTRACTOR,
        )

    async def _detect_bias(self) -> None:
        ns = self.nightshift
        self.bias = await self._with_nightshift(
            "bias_analysis",
            lambda: ns.detect_bias(self.content, self.claims, self.budget),  # type: ignore[union-attr]
            lambda: detect_bias_locally(self.content, self.claims),
            LOCAL_DETECTOR,
        )
        if any(b.detected_by == LOCAL_DETECTOR for b in self.bias) or (not self.bias and not self.nightshift_ok):
            self.extra_limitations.append(LOCAL_LIMITATION)

    async def _recall_memory(self, client: httpx.AsyncClient | None) -> None:
        clock = Stopwatch()
        if not self.request.options.use_memory:
            self.agent("memory_recall", HMEM_PROVIDER, "skipped", clock, detail="Memory disabled by request option.")
            return
        if self.sample_mode:
            self.memory = [
                MemoryRecall(**{k: m[k] for k in ("memory_id", "content", "source", "relevance", "confidence")}, recalled_from="sample fixture (H-MEM not called)")
                for m in evidence_svc.load_sample_case().get("memory_fixture", [])
            ]
            self.agent("memory_recall", HMEM_PROVIDER, "disabled_sample_mode", clock, detail="Sample mode: bundled memory fixture shown; H-MEM engine not called.")
            return
        if not self.settings.hmem_configured:
            self.agent("memory_recall", HMEM_PROVIDER, "not_configured", clock, detail="HMEM_URL not set; no investigative memory used.")
            return
        if self._out_of_time("memory_recall", HMEM_PROVIDER):
            return
        hmem = HMemClient(self.settings, client, self.trace)  # type: ignore[arg-type]
        try:
            text = " ".join([self.request.input.query, *[c.text for c in self.claims[:10]]])
            self.memory = await hmem.recall(text, self.budget)
            self.agent("memory_recall", HMEM_PROVIDER, "completed", clock, provider=HMEM_PROVIDER, model=hmem.engine, detail=f"{len(self.memory)} memories recalled.")
        except ProviderError as error:
            self.agent("memory_recall", HMEM_PROVIDER, "failed", clock, provider=HMEM_PROVIDER, detail=f"{error.code}: {error}")

    async def _retrieve_evidence(self, client: httpx.AsyncClient | None) -> None:
        clock = Stopwatch()
        if self.sample_mode:
            self.budget.next_iteration()
            self.evidence = evidence_svc.sample_evidence(self.claims, _now())
            self.relations_by = evidence_svc.SAMPLE_RELATION_ASSESSOR
            self.agent("evidence_retrieval", ZETARIS_PROVIDER, "disabled_sample_mode", clock, provider="sample_fixture", detail=f"Sample mode: {len(self.evidence)} excerpts from the bundled fictional corpus; Zetaris not called.")
            return
        if not self.settings.zetaris_configured:
            self.agent("evidence_retrieval", ZETARIS_PROVIDER, "not_configured", clock, detail="Zetaris not configured; no evidence retrieved.")
            return
        verifiable = [c for c in self.claims if c.verifiable]
        if not verifiable:
            self.agent("evidence_retrieval", ZETARIS_PROVIDER, "skipped", clock, detail="No verifiable claims to search for.")
            return
        try:
            zetaris = ZetarisClient(self.settings, client, self.trace)  # type: ignore[arg-type]
        except ValueError as error:
            self.agent("evidence_retrieval", ZETARIS_PROVIDER, "failed", clock, provider=ZETARIS_PROVIDER, detail=str(error))
            return
        sources: dict[str, dict[str, str]] = {}
        extra_terms: dict[str, list[str]] = {}
        failures: list[str] = []
        while self.budget.can_iterate():
            first = self.budget.iterations == 0
            if first:
                terms: list[str] = []
                for claim in verifiable:
                    terms += [t for t in evidence_svc.keywords(claim.text, limit=4) if t not in terms]
            else:
                uncovered = [c for c in verifiable if not any(c.id in e.claim_ids for e in self.evidence)]
                if not uncovered or self.nightshift is None:
                    break
                try:
                    refined = await self.nightshift.refine_search_terms(uncovered, self.budget)
                except ProviderError as error:
                    failures.append(f"search refinement failed ({error.code})")
                    break
                if not refined:
                    break
                extra_terms.update(refined)
                terms = sorted({t for ts in refined.values() for t in ts})
            if not self.budget.next_iteration():
                break
            iteration = self.budget.iterations
            self.trace.emit("evidence_retrieval", "iteration", iteration=iteration, terms=len(terms))
            try:
                for row in await zetaris.search(terms[:12], self.budget):
                    sources.setdefault(row["source_id"], row)
            except ProviderError as error:
                failures.append(f"iteration {iteration}: {error.code}")
                break
            except ValueError:
                break
            self.evidence = evidence_svc.candidates_for_claims(
                list(sources.values()), self.claims, _now(), f"zetaris:{self.settings.zetaris_evidence_table}", extra_terms=extra_terms
            )
        if self.budget.iterations >= self.budget.max_iterations:
            self.trace.emit("evidence_retrieval", "max_iterations_reached", "degraded", iterations=self.budget.iterations)
        status = "failed" if failures or any(q["status"] == "failed" for q in zetaris.queries) else "completed"
        detail = f"{len(zetaris.queries)} queries, {len(sources)} sources, {len(self.evidence)} evidence candidates, {self.budget.iterations} iteration(s)."
        if failures:
            detail += " Issues: " + "; ".join(failures)
        self.agent("evidence_retrieval", ZETARIS_PROVIDER, status, clock, provider=ZETARIS_PROVIDER, detail=detail)

    async def _compare_evidence(self) -> None:
        clock = Stopwatch()
        if self.sample_mode:
            self.agent("evidence_comparison", NIGHTSHIFT_PROVIDER, "disabled_sample_mode", clock, provider="sample_fixture", detail="Sample mode: relationships come from bundled fixture annotations.")
            return
        if not self.evidence:
            self.agent("evidence_comparison", NIGHTSHIFT_PROVIDER, "skipped", clock, detail="No evidence to compare.")
            return
        if self.nightshift is None:
            self.agent("evidence_comparison", NIGHTSHIFT_PROVIDER, "not_configured", clock, detail="Relationships not assessed: Nightshift not configured.")
            return
        if self._out_of_time("evidence_comparison", NIGHTSHIFT_PROVIDER):
            return
        try:
            applied = await self.nightshift.compare_evidence(self.claims, self.evidence, self.budget)
            self.relations_by = f"nightshift:{self.nightshift.last_model}"
            self.agent("evidence_comparison", NIGHTSHIFT_PROVIDER, "completed", clock, provider=NIGHTSHIFT_PROVIDER, model=self.nightshift.last_model, detail=f"{applied} relationships assessed.")
        except ProviderError as error:
            self.agent("evidence_comparison", NIGHTSHIFT_PROVIDER, "failed", clock, provider=NIGHTSHIFT_PROVIDER, detail=f"{error.code}: {error}. Relationships left unassessed.")

    def _assess(self) -> list[ClaimAssessment]:
        results = [assess_claim(c, self.evidence) for c in self.claims]
        label = f"{RULE_ASSESSOR} (relationships: {self.relations_by or 'not assessed'})"
        for a in results:
            a.assessed_by = label
        return results

    async def _review(self, client: httpx.AsyncClient | None) -> None:
        clock = Stopwatch()
        if self.sample_mode:
            self.agent("nvidia_review", NVIDIA_PROVIDER, "disabled_sample_mode", clock, detail="Sample mode: NVIDIA second-pass review not called.")
            return
        if not self.request.options.include_nvidia_review:
            self.agent("nvidia_review", NVIDIA_PROVIDER, "skipped", clock, detail="Review disabled by request option.")
            return
        if not self.settings.nvidia_configured:
            self.agent("nvidia_review", NVIDIA_PROVIDER, "skipped", clock, detail="NVIDIA_API_KEY not set; second-pass review skipped.")
            return
        if not self.claims or self._out_of_time("nvidia_review", NVIDIA_PROVIDER):
            return
        reviewer = NvidiaReviewer(self.settings, client, self.trace)  # type: ignore[arg-type]
        try:
            self.review, disagreements = await reviewer.review(self.claims, self.evidence, self.assessments or [], self.budget)
        except ProviderError as error:
            self.agent("nvidia_review", NVIDIA_PROVIDER, "failed", clock, provider=NVIDIA_PROVIDER, detail=f"{error.code}: {error}. Review skipped.")
            return
        by_id = {a.claim_id: a for a in self.assessments or []}
        for item in disagreements:
            target = by_id.get(item["claim_id"])
            if target:
                target.uncertainty = raise_uncertainty(target.uncertainty)  # type: ignore[assignment]
                target.evidence_gaps.append(f"Independent reviewer ({reviewer.model_used}) suggested '{item['reviewer_verdict']}'; verdict kept, uncertainty raised.")
        self.agent("nvidia_review", NVIDIA_PROVIDER, "completed", clock, provider=NVIDIA_PROVIDER, model=reviewer.model_used, detail=f"{len(self.review)} findings, {len(disagreements)} disagreements.")

    async def _store_memory(self, client: httpx.AsyncClient | None) -> None:
        if self.sample_mode or not self.request.options.use_memory or not self.settings.hmem_configured:
            return
        clock = Stopwatch()
        if self._out_of_time("memory_store", HMEM_PROVIDER):
            return
        hmem = HMemClient(self.settings, client, self.trace)  # type: ignore[arg-type]
        origin = "model_response" if self.relations_by and self.relations_by.startswith("nightshift") else "document"
        try:
            stored = await hmem.remember(self.run_id, self.claims, self.assessments or [], origin, self.budget)
            self.agent("memory_store", HMEM_PROVIDER, "completed", clock, provider=HMEM_PROVIDER, model=hmem.engine, detail=f"{len(stored)} conclusions stored.")
        except ProviderError as error:
            self.agent("memory_store", HMEM_PROVIDER, "failed", clock, provider=HMEM_PROVIDER, detail=f"{error.code}: {error}")

    def _analyze_media(self) -> None:
        clock = Stopwatch()
        self.media = analyze_media(self.request.input.media_url, self.request.options.include_media_analysis, self.sample_mode)
        if self.media:
            self.agent("media_forensics", "media-detector", self.media[0].status, clock, detail=self.media[0].note)

    async def _synthesize(self) -> None:
        if self.sample_mode or self.nightshift is None or not self.nightshift_ok or self.budget.exhausted():
            return
        clock = Stopwatch()
        structured = {
            "claims": [{"id": c.id, "text": c.text} for c in self.claims],
            "verdicts": [{"claim_id": a.claim_id, "verdict": a.verdict, "uncertainty": a.uncertainty} for a in self.assessments or []],
            "bias_techniques": sorted({b.technique for b in self.bias}),
        }
        try:
            self.summary = await self.nightshift.synthesize(self.request.input.query, structured, self.budget)
            self.agent("report_synthesis", NIGHTSHIFT_PROVIDER, "completed", clock, provider=NIGHTSHIFT_PROVIDER, model=self.nightshift.last_model)
        except ProviderError as error:
            self.agent("report_synthesis", NIGHTSHIFT_PROVIDER, "failed", clock, provider=NIGHTSHIFT_PROVIDER, detail=f"{error.code}: deterministic summary used.")

    def _status(self) -> str:
        if self.sample_mode:
            return "partial" if self.budget_hit else "success"
        degraded = self.budget_hit or any(a.status == "failed" for a in self.agents)
        missing_core = any(a.service in CORE_LIVE_SERVICES and a.status == "not_configured" for a in self.agents)
        return "partial" if degraded or missing_core else "success"

    def finalize(self, hard_timeout: bool = False) -> InvestigateResponse:
        if hard_timeout:
            self.budget_hit = True
            self.extra_limitations.append("The investigation hit its hard time limit; results are partial.")
            self.trace.emit("run", "hard_timeout", "degraded")
        if self.assessments is None:
            self.assessments = self._assess()
        deterministic = reporting.build_summary(self.request.input.query, self.claims, self.bias, self.evidence, self.assessments, self.sample_mode)
        summary = f"{self.summary} [Model-written summary; verdicts above are rule-derived from cited evidence.]" if self.summary else deterministic
        live_sources = not self.sample_mode and any(a.service == ZETARIS_PROVIDER and a.status == "completed" for a in self.agents)
        steps = reporting.next_steps(self.assessments, self.evidence, live_sources)
        graph = None
        if self.request.options.include_provenance:
            graph = build_graph(self.input_label, self.input_kind, self.claims, self.bias, self.evidence, self.assessments, self.memory, self.review, self.media, steps)
        output = InvestigationOutput(
            summary=summary,
            overall_assessment=reporting.overall_assessment(self.assessments),
            sample_mode=self.sample_mode,
            claims=self.claims,
            bias_indicators=self.bias,
            evidence=self.evidence,
            assessments=self.assessments,
            memory=self.memory,
            review=self.review,
            media=self.media,
            provenance=graph,
            unresolved_questions=reporting.unresolved_questions(self.claims, self.assessments),
            next_steps=steps,
            limitations=reporting.limitations(self.sample_mode, self.agents, self.extra_limitations),
        )
        status = self._status()
        elapsed = round(time.perf_counter() - self.started, 3)
        self.trace.emit("run", "finished", status, duration_ms=int(elapsed * 1000), claims=len(self.claims), evidence=len(self.evidence))
        return InvestigateResponse(
            status=status,  # type: ignore[arg-type]
            run_id=self.run_id,
            output=output,
            agents=self.agents,
            trace_id=self.trace.trace_id,
            log_file=str(self.trace.log_file) if self.trace.log_file else None,
            execution_time_seconds=elapsed,
        )
