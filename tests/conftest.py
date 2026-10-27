from __future__ import annotations

import dataclasses
import socket
import sys
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings, load_settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def make_settings(tmp_path, monkeypatch):
    for name in ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN", "ZETARIS_REST_URL", "ZETARIS_API_KEY", "ZETARIS_ORG_ID",
                 "ZETARIS_EVIDENCE_TABLE", "HMEM_URL", "HMEM_SIDECAR_TOKEN", "NVIDIA_API_KEY", "SAMPLE_MODE"):
        monkeypatch.delenv(name, raising=False)

    def build(**overrides) -> Settings:
        base = dataclasses.replace(load_settings(), log_dir=tmp_path / "logs", retry_attempts=0)
        return dataclasses.replace(base, **overrides)

    return build


@pytest.fixture
def make_client(make_settings):
    def build(transport: httpx.AsyncBaseTransport | None = None, **overrides) -> TestClient:
        return TestClient(create_app(make_settings(**overrides), transport=transport), raise_server_exceptions=False)

    return build


@pytest.fixture
def sample_client(make_client):
    return make_client(sample_mode=True)


@pytest.fixture
def block_network(monkeypatch):
    """Fail the test on any attempt to open a socket or resolve a hostname."""
    attempts: list[str] = []
    local = threading.local()
    real_connect = socket.socket.connect
    real_socketpair = socket.socketpair

    def refuse(*args, **kwargs):
        attempts.append(repr(args[1:2]))
        raise AssertionError(f"network access attempted in offline test: {args[1:2]!r}")

    def guarded_connect(self, address):
        if getattr(local, "socketpair", False):
            return real_connect(self, address)
        return refuse(self, address)

    def socketpair(*args, **kwargs):
        # Windows event loops build their internal self-pipe from a loopback socketpair; that is not network I/O.
        local.socketpair = True
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            local.socketpair = False

    monkeypatch.setattr(socket, "socketpair", socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return attempts


LIVE = dict(
    sample_mode=False,
    nightshift_base_url="http://nightshift.test/v1",
    nightshift_token="ns-secret-token-123456",
    nightshift_model="meterless/auto",
    zetaris_rest_url="http://zetaris.test/api/v1.0",
    zetaris_api_key="zt-secret-key-abcdef",
    zetaris_org_id="org-42",
    zetaris_evidence_table="veritas.evidence_sources",
    hmem_url="http://hmem.test",
    hmem_token="",
    nvidia_base_url="http://nvidia.test/v1",
    nvidia_api_key="nvapi-secret-0987654321",
    nvidia_model="nvidia/llama-3.3-nemotron-super-49b-v1.5",
)

CONTENT = (
    "The new bridge caused traffic deaths to double in 2025. Every driver knows it is dangerous. "
    "City officials said the bridge cost 40 million."
)
