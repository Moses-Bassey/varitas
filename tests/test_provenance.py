from __future__ import annotations

import pytest

from app.schemas import ProvenanceEdge, ProvenanceGraph, ProvenanceNode
from app.services.provenance import GraphIntegrityError, validate_graph

NODE_TYPES = {"input_media", "claim", "text_span", "evidence_source", "excerpt", "analysis_finding", "media_detector_result", "hypothesis", "conclusion", "recommendation"}
EDGE_TYPES = {"contains", "derived_from", "supports", "contradicts", "contextualizes", "evaluates", "indicates_possible_manipulation", "leads_to"}


@pytest.fixture
def result(sample_client, block_network):
    return sample_client.post("/api/v1/investigate", json={"input": {"query": "q", "media_url": "https://example.com/v.mp4"}, "options": {"sample_mode": True}}).json()


def test_graph_structure(result):
    graph = result["output"]["provenance"]
    ids = {n["id"] for n in graph["nodes"]}
    assert len(ids) == len(graph["nodes"])
    assert {n["type"] for n in graph["nodes"]} <= NODE_TYPES
    assert {e["type"] for e in graph["edges"]} <= EDGE_TYPES
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
        assert edge["basis"].strip()


def test_graph_is_connected_to_input(result):
    graph = result["output"]["provenance"]
    neighbours: dict[str, set[str]] = {n["id"]: set() for n in graph["nodes"]}
    for edge in graph["edges"]:
        neighbours[edge["source"]].add(edge["target"])
        neighbours[edge["target"]].add(edge["source"])
    seen, stack = {"input-0"}, ["input-0"]
    while stack:
        for nxt in neighbours[stack.pop()] - seen:
            seen.add(nxt)
            stack.append(nxt)
    assert seen == set(neighbours)


def test_graph_covers_required_node_types(result):
    types = {n["type"] for n in result["output"]["provenance"]["nodes"]}
    assert {"input_media", "claim", "text_span", "evidence_source", "excerpt", "analysis_finding", "media_detector_result", "hypothesis", "conclusion", "recommendation"} <= types


def test_every_claim_traces_to_input(result):
    graph = result["output"]["provenance"]
    edges = graph["edges"]
    for claim in result["output"]["claims"]:
        node = f"claim:{claim['id']}"
        span = next(e["target"] for e in edges if e["source"] == node and e["type"] == "derived_from")
        assert any(e["source"] == "input-0" and e["target"] == span and e["type"] == "contains" for e in edges)
        assert any(e["source"] == node and e["target"] == f"conclusion:{claim['id']}" for e in edges)


def test_evidence_relationships_match_graph(result):
    edges = result["output"]["provenance"]["edges"]
    mapping = {"supports": "supports", "contradicts": "contradicts", "contextualizes": "contextualizes", "does_not_establish": "evaluates"}
    for record in result["output"]["evidence"]:
        for claim_id, relation in record["relationships"].items():
            assert any(e["source"] == f"excerpt:{record['id']}" and e["target"] == f"claim:{claim_id}" and e["type"] == mapping[relation] for e in edges)


def test_citations_resolve(result):
    output = result["output"]
    evidence = {e["id"]: e for e in output["evidence"]}
    claim_ids = {c["id"] for c in output["claims"]}
    for assessment in output["assessments"]:
        assert assessment["claim_id"] in claim_ids
        for evidence_id in assessment["supporting_evidence"] + assessment["contradicting_evidence"] + assessment["context_evidence"]:
            assert assessment["claim_id"] in evidence[evidence_id]["claim_ids"]
        if assessment["verdict"] in {"supported", "contradicted"}:
            assert assessment["supporting_evidence"] or assessment["contradicting_evidence"]
    for record in evidence.values():
        assert set(record["claim_ids"]) <= claim_ids
        assert record["excerpt"] and record["retrieved_at"] and record["retrieval_method"]


def test_media_not_analyzed_is_not_flagged(result):
    edges = result["output"]["provenance"]["edges"]
    assert not any(e["type"] == "indicates_possible_manipulation" for e in edges)


def test_spans_point_to_real_text(sample_client, block_network):
    content = "Experts say the reform is a DISASTER. Either we act or we fail."
    body = sample_client.post("/api/v1/investigate", json={"input": {"query": "q", "content": content}, "options": {"sample_mode": True}}).json()
    for item in body["output"]["claims"] + body["output"]["bias_indicators"]:
        span = item["span"]
        assert content[span["start"] : span["end"]] == span["text"]


def test_provenance_can_be_omitted(sample_client, block_network):
    body = sample_client.post("/api/v1/investigate", json={"input": {"query": "q"}, "options": {"sample_mode": True, "include_provenance": False}}).json()
    assert body["output"]["provenance"] is None


def test_validate_graph_rejects_dangling_edge():
    graph = ProvenanceGraph(nodes=[ProvenanceNode(id="a", type="claim", label="a")], edges=[ProvenanceEdge(id="e1", source="a", target="missing", type="supports", basis="x")])
    with pytest.raises(GraphIntegrityError):
        validate_graph(graph)


def test_edge_requires_basis():
    with pytest.raises(ValueError):
        ProvenanceEdge(id="e1", source="a", target="b", type="supports", basis="")
