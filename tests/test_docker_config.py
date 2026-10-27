from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_contract():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert text.startswith("FROM python:3.11-slim")
    assert "EXPOSE 8000" in text
    assert 'CMD ["python", "run.py"]' in text
    assert not re.search(r"\.env\b", re.sub(r"#.*", "", text))
    assert "USER " in text
    assert "--gpus" not in text and "cuda" not in text.lower()


def test_dockerignore_excludes_secrets_and_local_state():
    entries = set((ROOT / ".dockerignore").read_text(encoding="utf-8").split())
    assert {".env", ".venv", "logs", ".git", "**/node_modules"} <= entries


def test_env_example_has_only_placeholders():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(word in key for word in ("KEY", "TOKEN", "SECRET")):
            assert value == "" or value.startswith("<"), key


def test_run_py_binds_all_interfaces_and_port():
    text = (ROOT / "run.py").read_text(encoding="utf-8")
    assert 'host="0.0.0.0"' in text and 'os.environ.get("PORT", "8000")' in text


def test_api_image_does_not_need_ui_dependencies():
    assert "streamlit" not in (ROOT / "requirements.txt").read_text(encoding="utf-8")
