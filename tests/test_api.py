from __future__ import annotations

ERROR_KEYS = {"status", "run_id", "error", "trace_id"}


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["status"] == "error"
    assert body["error"]["code"] == code
    assert set(body["error"]) == {"code", "message", "retryable", "details"}
    assert body["trace_id"] and response.headers["X-Trace-ID"] == body["trace_id"]
    assert "Traceback" not in response.text
    return body


def test_health(sample_client):
    response = sample_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["integrations_configured"] == {
        "zetaris": False, "meterless_nightshift": False, "meterless_hmem": False, "nvidia": False, "media_detector": False,
    }


def test_root_and_swagger(sample_client):
    assert "VERITAS" in sample_client.get("/").text
    assert sample_client.get("/docs").status_code == 200
    spec = sample_client.get("/openapi.json").json()
    assert {"/health", "/api/v1/investigate", "/api/v1/runs/{run_id}"} <= set(spec["paths"])


def test_investigate_response_contract(sample_client):
    response = sample_client.post("/api/v1/investigate", json={"run_id": "contract-1", "input": {"query": "Check this"}, "options": {"sample_mode": True}})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "run_id", "output", "agents", "trace_id", "log_file", "execution_time_seconds"}
    assert body["status"] == "success" and body["run_id"] == "contract-1"
    for key in ("summary", "claims", "bias_indicators", "evidence", "provenance", "limitations"):
        assert key in body["output"]
    assert set(body["output"]["provenance"]) == {"nodes", "edges"}
    assert body["execution_time_seconds"] >= 0


def test_get_run_roundtrip_and_report(sample_client):
    created = sample_client.post("/api/v1/investigate", json={"run_id": "roundtrip-1", "input": {"query": "q"}, "options": {"sample_mode": True}}).json()
    fetched = sample_client.get("/api/v1/runs/roundtrip-1")
    assert fetched.status_code == 200
    assert fetched.json()["output"] == created["output"]
    report = sample_client.get("/api/v1/runs/roundtrip-1/report.md")
    assert report.status_code == 200
    for n in range(1, 13):
        assert f"## {n}." in report.text


def test_unknown_run(sample_client):
    assert_error(sample_client.get("/api/v1/runs/does-not-exist"), 404, "run_not_found")


def test_invalid_run_id_lookup(sample_client):
    assert_error(sample_client.get("/api/v1/runs/bad$id"), 400, "invalid_run_id")


def test_duplicate_run_id(sample_client):
    payload = {"run_id": "dup-1", "input": {"query": "q"}, "options": {"sample_mode": True}}
    assert sample_client.post("/api/v1/investigate", json=payload).status_code == 200
    assert_error(sample_client.post("/api/v1/investigate", json=payload), 409, "run_id_conflict")


def test_delete_run(sample_client):
    sample_client.post("/api/v1/investigate", json={"run_id": "del-1", "input": {"query": "q"}, "options": {"sample_mode": True}})
    assert sample_client.delete("/api/v1/runs/del-1").status_code == 200
    assert_error(sample_client.get("/api/v1/runs/del-1"), 404, "run_not_found")


def test_missing_query(sample_client):
    body = assert_error(sample_client.post("/api/v1/investigate", json={"input": {}}), 422, "validation_error")
    assert any("query" in d["loc"] for d in body["error"]["details"])


def test_blank_query(sample_client):
    assert_error(sample_client.post("/api/v1/investigate", json={"input": {"query": "   "}}), 422, "validation_error")


def test_unknown_field_rejected(sample_client):
    assert_error(sample_client.post("/api/v1/investigate", json={"input": {"query": "q"}, "options": {"evil": True}}), 422, "validation_error")


def test_bad_run_id(sample_client):
    assert_error(sample_client.post("/api/v1/investigate", json={"run_id": "../etc/passwd", "input": {"query": "q"}}), 422, "validation_error")


def test_malformed_json(sample_client):
    response = sample_client.post("/api/v1/investigate", content=b"{not json", headers={"Content-Type": "application/json"})
    assert_error(response, 422, "validation_error")


def test_empty_body(sample_client):
    response = sample_client.post("/api/v1/investigate", content=b"", headers={"Content-Type": "application/json"})
    assert_error(response, 422, "validation_error")


def test_payload_too_large(make_client):
    client = make_client(sample_mode=True, max_content_chars=1000)
    response = client.post("/api/v1/investigate", content=b"x" * 80_000, headers={"Content-Type": "application/json"})
    assert_error(response, 413, "payload_too_large")


def test_unknown_route_uses_error_schema(sample_client):
    assert_error(sample_client.get("/nope"), 404, "not_found")


def test_internal_errors_hide_details(sample_client, monkeypatch):
    from app import pipeline

    def explode(self):
        raise RuntimeError("secret internal detail /etc/shadow")

    monkeypatch.setattr(pipeline.Investigation, "_assess", explode)
    response = sample_client.post("/api/v1/investigate", json={"input": {"query": "q"}, "options": {"sample_mode": True}})
    body = assert_error(response, 500, "investigation_failed")
    assert "secret internal detail" not in response.text
    assert body["error"]["retryable"] is True
