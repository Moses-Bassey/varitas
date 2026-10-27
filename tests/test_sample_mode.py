from __future__ import annotations

import json

from app.services.reporting import SAMPLE_BANNER

PAYLOAD = {"input": {"query": "Is the plant destroying the harbor?"}, "options": {"sample_mode": True}}


def _strip_volatile(body: dict) -> dict:
    body = json.loads(json.dumps(body))
    for key in ("run_id", "trace_id", "log_file", "execution_time_seconds"):
        body.pop(key)
    for agent in body["agents"]:
        agent["duration_ms"] = 0
    for evidence in body["output"]["evidence"]:
        evidence["retrieved_at"] = ""
    for edge in body["output"]["provenance"]["edges"]:
        if edge["basis"].startswith("Excerpt retrieved at"):
            edge["basis"] = ""
    return body


def test_sample_mode_succeeds_offline(sample_client, block_network):
    response = sample_client.post("/api/v1/investigate", json=PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert block_network == []
    output = body["output"]
    assert output["sample_mode"] is True
    assert output["summary"].startswith(SAMPLE_BANNER)
    assert SAMPLE_BANNER in output["limitations"]
    assert output["claims"] and output["evidence"] and output["bias_indicators"]
    assert all(e["sample_data"] for e in output["evidence"])
    assert all("Fictional demonstration source." in e["limitations"] for e in output["evidence"])
    assert {a["status"] for a in body["agents"]} == {"disabled_sample_mode"}


def test_sample_mode_is_deterministic(sample_client, block_network):
    first = sample_client.post("/api/v1/investigate", json=PAYLOAD).json()
    second = sample_client.post("/api/v1/investigate", json=PAYLOAD).json()
    assert _strip_volatile(first) == _strip_volatile(second)


def test_sample_mode_expected_verdicts(sample_client, block_network):
    body = sample_client.post("/api/v1/investigate", json=PAYLOAD).json()
    claims = {c["id"]: c["text"] for c in body["output"]["claims"]}
    verdicts = {claims[a["claim_id"]]: a["verdict"] for a in body["output"]["assessments"]}
    assert verdicts["A leading scientist confirmed the brine is toxic."] == "contradicted"
    assert verdicts["Since the plant opened in March 2025, fish catches have fallen by 40%."] == "supported"
    assert verdicts["Varnholm's new desalination plant is DESTROYING the harbor."] == "insufficient_evidence"
    assert verdicts["Act now before it's too late."] == "unverified"


def test_sample_mode_from_environment(make_client, block_network):
    client = make_client(sample_mode=True)
    body = client.post("/api/v1/investigate", json={"input": {"query": "anything"}}).json()
    assert body["output"]["sample_mode"] is True


def test_sample_mode_does_not_fetch_urls_or_media(sample_client, block_network):
    payload = {
        "input": {"query": "check", "content": "Officials said the dam failed.", "url": "https://example.com/a", "media_url": "https://example.com/v.mp4"},
        "options": {"sample_mode": True, "include_media_analysis": True},
    }
    body = sample_client.post("/api/v1/investigate", json=payload).json()
    agents = {a["name"]: a["status"] for a in body["agents"]}
    assert agents["url_fetch"] == "disabled_sample_mode"
    assert body["output"]["media"][0]["status"] == "disabled_sample_mode"
    assert body["output"]["media"][0]["findings"] == []
    assert block_network == []


def test_sample_mode_custom_content_without_matching_evidence(sample_client, block_network):
    payload = {"input": {"query": "q", "content": "The moon base opened in 2031 and houses 500 people."}, "options": {"sample_mode": True}}
    body = sample_client.post("/api/v1/investigate", json=payload).json()
    assert body["output"]["evidence"] == []
    assert {a["verdict"] for a in body["output"]["assessments"]} == {"unverified"}


def test_sample_log_written(sample_client, block_network):
    body = sample_client.post("/api/v1/investigate", json=PAYLOAD).json()
    lines = open(body["log_file"], encoding="utf-8").read().splitlines()
    events = [json.loads(line) for line in lines]
    assert all(e["trace_id"] == body["trace_id"] for e in events)
    assert {"timestamp", "run_id", "trace_id", "stage", "event", "provider", "model", "duration_ms", "status", "details"} <= set(events[0])
    assert events[-1]["event"] == "finished"
