from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HARD_BUDGET_CEILING_SECONDS = 900.0


def apply_env_file(path: Path | None = None) -> None:
    """Apply KEY=VALUE lines from a dotenv file. File values win, so `run.py` follows `.env`."""
    file = path or (ROOT / ".env")
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


HARD_ITERATION_CEILING = 5


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = default
    return min(high, max(low, value))


def _int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        value = default
    return min(high, max(low, value))


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    port: int
    sample_mode: bool
    log_dir: Path
    max_content_chars: int
    max_upload_bytes: int
    max_fetch_bytes: int

    budget_seconds: float
    max_iterations: int
    retry_attempts: int

    nightshift_base_url: str
    nightshift_token: str = field(repr=False)
    nightshift_model: str
    nightshift_app_id: str
    nightshift_timeout: float

    zetaris_rest_url: str
    zetaris_api_key: str = field(repr=False)
    zetaris_org_id: str
    zetaris_evidence_table: str
    zetaris_query_limit: int
    zetaris_timeout: float

    hmem_url: str
    hmem_token: str = field(repr=False)
    hmem_timeout: float
    hmem_recall_threshold: float

    nvidia_base_url: str
    nvidia_api_key: str = field(repr=False)
    nvidia_model: str
    nvidia_timeout: float

    url_fetch_enabled: bool

    @property
    def nightshift_configured(self) -> bool:
        return bool(self.nightshift_base_url and self.nightshift_token and self.nightshift_model)

    @property
    def zetaris_configured(self) -> bool:
        return bool(self.zetaris_rest_url and self.zetaris_api_key and self.zetaris_org_id and self.zetaris_evidence_table)

    @property
    def hmem_configured(self) -> bool:
        return bool(self.hmem_url)

    @property
    def nvidia_configured(self) -> bool:
        return bool(self.nvidia_base_url and self.nvidia_api_key and self.nvidia_model)

    def secret_values(self) -> list[str]:
        return [s for s in (self.nightshift_token, self.zetaris_api_key, self.hmem_token, self.nvidia_api_key) if s]


def load_settings() -> Settings:
    return Settings(
        port=_int("PORT", 8000, 1, 65535),
        sample_mode=_bool("SAMPLE_MODE", False),
        log_dir=Path(_str("LOG_DIR", str(ROOT / "logs"))),
        max_content_chars=_int("MAX_CONTENT_CHARS", 50_000, 1_000, 200_000),
        max_upload_bytes=_int("MAX_UPLOAD_BYTES", 25 * 1024 * 1024, 1024, 200 * 1024 * 1024),
        max_fetch_bytes=_int("MAX_FETCH_BYTES", 2 * 1024 * 1024, 16 * 1024, 10 * 1024 * 1024),
        budget_seconds=_float("INVESTIGATION_BUDGET_SECONDS", 420.0, 10.0, HARD_BUDGET_CEILING_SECONDS),
        max_iterations=_int("MAX_REASONING_ITERATIONS", 3, 1, HARD_ITERATION_CEILING),
        retry_attempts=_int("PROVIDER_RETRY_ATTEMPTS", 2, 0, 4),
        nightshift_base_url=_str("METERLESS_BASE_URL").rstrip("/"),
        nightshift_token=_str("METERLESS_RUNTIME_TOKEN"),
        nightshift_model=_str("METERLESS_MODEL", "meterless/auto"),
        nightshift_app_id=_str("METERLESS_APP_ID", "veritas"),
        nightshift_timeout=_float("METERLESS_TIMEOUT_SECONDS", 60.0, 1.0, 300.0),
        zetaris_rest_url=_str("ZETARIS_REST_URL").rstrip("/"),
        zetaris_api_key=_str("ZETARIS_API_KEY"),
        zetaris_org_id=_str("ZETARIS_ORG_ID"),
        zetaris_evidence_table=_str("ZETARIS_EVIDENCE_TABLE"),
        zetaris_query_limit=_int("ZETARIS_QUERY_LIMIT", 50, 1, 1000),
        zetaris_timeout=_float("ZETARIS_TIMEOUT_SECONDS", 45.0, 1.0, 300.0),
        hmem_url=_str("HMEM_URL").rstrip("/"),
        hmem_token=_str("HMEM_SIDECAR_TOKEN"),
        hmem_timeout=_float("HMEM_TIMEOUT_SECONDS", 45.0, 1.0, 300.0),
        hmem_recall_threshold=_float("HMEM_RECALL_THRESHOLD", 0.2, 0.0, 1.0),
        nvidia_base_url=_str("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/"),
        nvidia_api_key=_str("NVIDIA_API_KEY"),
        nvidia_model=_str("NVIDIA_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5"),
        nvidia_timeout=_float("NVIDIA_TIMEOUT_SECONDS", 45.0, 1.0, 300.0),
        url_fetch_enabled=_bool("URL_FETCH_ENABLED", True),
    )
