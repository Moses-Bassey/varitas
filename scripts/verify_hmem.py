"""Verify the H-MEM integration against a running sidecar (real engine calls, no test doubles).

Usage: HMEM_URL=http://127.0.0.1:8787 python scripts/verify_hmem.py
Stores a memory in the real engine, then runs a live investigation (only H-MEM enabled) that must recall it.
"""

import dataclasses
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402

hmem_url = os.environ.get("HMEM_URL", "http://127.0.0.1:8787")
print("sidecar health:", httpx.get(f"{hmem_url}/health", timeout=5).json())

marker = uuid.uuid4().hex[:6]
stored = httpx.post(
    f"{hmem_url}/v1/memories",
    json={
        "content": f"Prior VERITAS check: the claim that Varnholm bridge {marker} cost 40 million was supported by an audit.",
        "source": f"veritas:verify:{marker}",
        "type": "factual",
        "provenance": {"origin": "document", "label": "verify_hmem.py"},
    },
    timeout=5,
).json()
memory_id = stored["memory"]["id"]
print("stored:", memory_id, "engine:", stored["engine"])

settings = dataclasses.replace(
    load_settings(), sample_mode=False, hmem_url=hmem_url, nightshift_base_url="", nvidia_api_key="", zetaris_rest_url="",
    log_dir=Path(os.environ.get("LOG_DIR", "logs")),
)
client = TestClient(create_app(settings))
body = client.post("/api/v1/investigate", json={"input": {"query": f"Did Varnholm bridge {marker} cost 40 million?", "content": f"Officials said the Varnholm bridge {marker} cost 40 million."}}).json()
recall = next(a for a in body["agents"] if a["name"] == "memory_recall")
print("recall agent:", recall["status"], recall["model"], recall["detail"])
for m in body["output"]["memory"][:3]:
    print(f"  recalled {m['memory_id']} relevance={m['relevance']:.3f} from={m['recalled_from']}: {m['content'][:80]}")
ledger = httpx.get(f"{hmem_url}/v1/ledger", params={"memoryId": memory_id}, timeout=5).json()
print("ledger actions for stored memory:", [e["action"] for e in ledger["entries"]])
ok = recall["status"] == "completed" and any(m["memory_id"] == memory_id for m in body["output"]["memory"])
print("H-MEM VERIFIED" if ok else "H-MEM VERIFICATION FAILED")
sys.exit(0 if ok else 1)
