"""Guards on release.yml: publishing stays on OIDC trusted publishing, never a stored token."""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    return [ln for ln in _text().splitlines() if not ln.lstrip().startswith("#")]


def test_workflow_requests_an_oidc_token():
    assert re.search(r"^\s+id-token: write\b", _text(), re.M)


def test_every_publish_uses_trusted_publishing():
    body = "\n".join(_code_lines())
    publishes = body.count("uv publish")
    assert publishes >= 2  # PyPI and TestPyPI
    assert body.count("--trusted-publishing always") == publishes


def test_no_stored_credentials():
    body = "\n".join(_code_lines())
    assert "secrets." not in body
    for word in ("password", "api_token", "api-token", "UV_PUBLISH_TOKEN", "UV_PUBLISH_PASSWORD"):
        assert word.lower() not in body.lower()
    assert not re.search(r"--(token|password|username)\b", body)


def test_testpypi_is_a_manual_choice_with_its_own_environment():
    text = _text()
    assert "workflow_dispatch" in text
    assert "https://test.pypi.org/legacy/" in text
    assert "environment: ${{" in text and "'testpypi'" in text and "'pypi'" in text
    # The TestPyPI step runs only for a manual dispatch, so a release can never reach it.
    step = text.split("name: Publish to TestPyPI", 1)[1]
    assert "github.event_name == 'workflow_dispatch'" in step.split("run:", 1)[0]


def test_gates_run_before_publishing():
    text = _text()
    first_publish = text.index("uv publish")
    for gate in ("ruff check", "pytest", "sync_version.py --check", "wheel_smoke.sh"):
        assert 0 <= text.index(gate) < first_publish
