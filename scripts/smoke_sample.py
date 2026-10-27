"""Print a compact sample-mode result. Usage: python scripts/smoke_sample.py"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("SAMPLE_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

client = TestClient(create_app())
response = client.post("/api/v1/investigate", json={"input": {"query": "Is the plant destroying the harbor?"}, "options": {"sample_mode": True}})
body = response.json()
print("HTTP", response.status_code, "status", body["status"])
for claim, assessment in zip(body["output"]["claims"], body["output"]["assessments"]):
    print(f"  {claim['id']:9} {assessment['verdict']:22} {assessment['uncertainty']:9} {claim['text'][:70]}")
print("bias:", sorted({b["technique"] for b in body["output"]["bias_indicators"]}))
graph = body["output"]["provenance"]
print("graph:", len(graph["nodes"]), "nodes", len(graph["edges"]), "edges")
print(json.dumps([{k: a[k] for k in ("name", "status")} for a in body["agents"]]))
