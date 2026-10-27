from __future__ import annotations

from app.schemas import (
    BiasIndicator,
    Claim,
    ClaimAssessment,
    Evidence,
    MediaResult,
    MemoryRecall,
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceNode,
    ReviewFinding,
)

_RELATION_EDGE = {"supports": "supports", "contradicts": "contradicts", "contextualizes": "contextualizes", "does_not_establish": "evaluates"}


class GraphIntegrityError(ValueError):
    pass


class _Builder:
    def __init__(self) -> None:
        self.nodes: dict[str, ProvenanceNode] = {}
        self.edges: list[ProvenanceEdge] = []

    def node(self, node_id: str, node_type: str, label: str, **data: object) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = ProvenanceNode(id=node_id, type=node_type, label=label[:200], data=data)  # type: ignore[arg-type]
        return node_id

    def edge(self, source: str, target: str, edge_type: str, basis: str) -> None:
        self.edges.append(ProvenanceEdge(id=f"e{len(self.edges) + 1}", source=source, target=target, type=edge_type, basis=basis))  # type: ignore[arg-type]


def build_graph(
    input_label: str,
    input_kind: str,
    claims: list[Claim],
    bias: list[BiasIndicator],
    evidence: list[Evidence],
    assessments: list[ClaimAssessment],
    memory: list[MemoryRecall],
    review: list[ReviewFinding],
    media: list[MediaResult],
    next_steps: list[str],
) -> ProvenanceGraph:
    g = _Builder()
    root = g.node("input-0", "input_media", input_label, kind=input_kind)

    for claim in claims:
        claim_node = g.node(f"claim:{claim.id}", "claim", claim.text, claim_type=claim.claim_type, verifiable=claim.verifiable)
        if claim.span:
            span_node = g.node(f"span:{claim.id}", "text_span", claim.span.text, start=claim.span.start, end=claim.span.end)
            g.edge(root, span_node, "contains", f"Characters {claim.span.start}-{claim.span.end} of the analyzed text.")
            g.edge(claim_node, span_node, "derived_from", f"Claim text extracted from this span by {claim.extracted_by}.")
        else:
            g.edge(claim_node, root, "derived_from", f"Claim extracted from the input by {claim.extracted_by}; no exact span located.")

    for indicator in bias:
        finding = g.node(
            f"finding:{indicator.id}",
            "analysis_finding",
            f"{indicator.technique}: \"{indicator.span.text}\"",
            technique=indicator.technique,
            detected_by=indicator.detected_by,
            caution=indicator.caution,
        )
        basis = f"{indicator.explanation} Matched text at characters {indicator.span.start}-{indicator.span.end}."
        targets = [f"claim:{cid}" for cid in indicator.claim_ids if f"claim:{cid}" in g.nodes] or [root]
        for target in targets:
            g.edge(finding, target, "evaluates", basis)

    for record in evidence:
        source_node = g.node(
            f"source:{record.source_id}",
            "evidence_source",
            record.title,
            publisher=record.publisher,
            url=record.url,
            published_at=record.published_at,
            source_kind=record.source_kind,
            sample_data=record.sample_data,
        )
        excerpt_node = g.node(f"excerpt:{record.id}", "excerpt", record.excerpt, evidence_id=record.id)
        g.edge(source_node, excerpt_node, "contains", f"Excerpt retrieved at {record.retrieved_at} via {record.retrieval_method}.")
        for claim_id, relation in record.relationships.items():
            if f"claim:{claim_id}" not in g.nodes:
                continue
            basis = record.relationship_basis.get(claim_id) or f"Relationship '{relation}' recorded by {record.relationship_assessed_by}."
            if relation == "does_not_establish":
                basis = f"Does not establish the claim: {basis}"
            g.edge(excerpt_node, f"claim:{claim_id}", _RELATION_EDGE[relation], basis)

    for recall in memory:
        mem_node = g.node(
            f"memory:{recall.memory_id}",
            "analysis_finding",
            recall.content,
            origin="remembered",
            recalled_from=recall.recalled_from,
            relevance=recall.relevance,
            note=recall.note,
        )
        g.edge(mem_node, root, "contextualizes", f"Recalled from {recall.recalled_from} (relevance {recall.relevance:.2f}). Prior finding, not evidence for this case.")

    for assessment in assessments:
        claim_node = f"claim:{assessment.claim_id}"
        if claim_node not in g.nodes:
            continue
        conclusion = g.node(
            f"conclusion:{assessment.claim_id}",
            "conclusion",
            f"{assessment.verdict} ({assessment.uncertainty} uncertainty)",
            verdict=assessment.verdict,
            uncertainty=assessment.uncertainty,
            assessed_by=assessment.assessed_by,
        )
        g.edge(claim_node, conclusion, "leads_to", assessment.reasoning)
        for evidence_id in assessment.supporting_evidence + assessment.contradicting_evidence:
            g.edge(f"excerpt:{evidence_id}", conclusion, "leads_to", f"Evidence {evidence_id} was counted in this verdict.")
        for index, alternative in enumerate(assessment.alternative_explanations, start=1):
            hypothesis = g.node(f"hypothesis:{assessment.claim_id}:{index}", "hypothesis", alternative, kind="alternative_explanation")
            for evidence_id in assessment.context_evidence:
                record = next((e for e in evidence if e.id == evidence_id), None)
                if record and record.relationship_basis.get(assessment.claim_id) == alternative:
                    g.edge(f"excerpt:{evidence_id}", hypothesis, "leads_to", "Contextual evidence suggests this alternative explanation.")
            g.edge(hypothesis, claim_node, "contextualizes", "Alternative explanation the claim does not address.")

    for index, finding in enumerate(review, start=1):
        node = g.node(f"review:{index}", "analysis_finding", finding.concern, reviewer=finding.reviewer, severity=finding.severity)
        target = f"conclusion:{finding.claim_id}" if finding.claim_id and f"conclusion:{finding.claim_id}" in g.nodes else root
        g.edge(node, target, "evaluates", f"Second-pass review by {finding.reviewer} ({finding.severity}).")

    for index, result in enumerate(media, start=1):
        media_node = g.node(f"media:{index}", "input_media", result.media_ref, kind="media")
        g.edge(root, media_node, "contains", "Media reference supplied with the request.")
        detector = g.node(f"detector:{index}", "media_detector_result", f"{result.status}: {result.note}", status=result.status, detector=result.detector)
        edge_type = "indicates_possible_manipulation" if result.status == "completed" and result.findings else "evaluates"
        g.edge(detector, media_node, edge_type, result.note)

    uncertain = [
        node_id
        for node_id, node in g.nodes.items()
        if node.type == "conclusion" and node.data.get("verdict") in {"unverified", "insufficient_evidence", "mixed_evidence"}
    ]
    for index, step in enumerate(next_steps, start=1):
        rec = g.node(f"recommendation:{index}", "recommendation", step)
        if uncertain:
            for conclusion in uncertain:
                g.edge(conclusion, rec, "leads_to", "Unresolved or uncertain conclusion motivates this next step.")
        else:
            g.edge(root, rec, "leads_to", "General verification step for this investigation.")

    graph = ProvenanceGraph(nodes=list(g.nodes.values()), edges=g.edges)
    validate_graph(graph)
    return graph


def validate_graph(graph: ProvenanceGraph) -> None:
    ids = [n.id for n in graph.nodes]
    if len(ids) != len(set(ids)):
        raise GraphIntegrityError("duplicate node ids")
    known = set(ids)
    for edge in graph.edges:
        if edge.source not in known or edge.target not in known:
            raise GraphIntegrityError(f"edge {edge.id} references a missing node")
        if not edge.basis.strip():
            raise GraphIntegrityError(f"edge {edge.id} has no basis")
