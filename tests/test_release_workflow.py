"""Guards on release.yml: publishing stays on OIDC trusted publishing, never a stored token, and
a manual TestPyPI run can never also publish to PyPI."""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"
TESTPYPI = "github.event_name == 'workflow_dispatch' && inputs.target == 'testpypi'"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job() -> dict:
    return _workflow()["jobs"]["publish"]


def _steps() -> list[dict]:
    return _job()["steps"]


def _publish_steps() -> dict[str, dict]:
    return {s["name"]: s for s in _steps() if "uv publish" in s.get("run", "")}


def _code(text: str) -> str:
    """The workflow without comments, so a comment can neither satisfy nor trip a check."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", ln) for ln in text.splitlines())


def test_workflow_requests_an_oidc_token():
    assert _job()["permissions"]["id-token"] == "write"


def test_every_publish_uses_trusted_publishing():
    steps = _publish_steps()
    assert set(steps) == {"Publish to PyPI", "Publish to TestPyPI"}
    for s in steps.values():
        assert "--trusted-publishing always" in s["run"]


def test_no_stored_credentials():
    body = _code(WORKFLOW.read_text(encoding="utf-8"))
    assert "secrets." not in body
    for word in ("password", "api_token", "api-token", "UV_PUBLISH_TOKEN", "UV_PUBLISH_PASSWORD"):
        assert word.lower() not in body.lower()
    assert not re.search(r"--(token|password|username)\b", body)


def test_testpypi_is_a_manual_choice_with_its_own_environment():
    # PyYAML reads the bare `on:` key as True.
    dispatch = _workflow()[True]["workflow_dispatch"]["inputs"]["target"]
    assert dispatch["options"] == ["pypi", "testpypi"] and dispatch["default"] == "pypi"
    assert _job()["environment"] == "${{ " + TESTPYPI + " && 'testpypi' || 'pypi' }}"
    steps = _publish_steps()
    # Exactly one publish step runs for any event: TestPyPI only for that dispatch, PyPI otherwise.
    assert steps["Publish to TestPyPI"]["if"] == "${{ " + TESTPYPI + " }}"
    assert steps["Publish to PyPI"]["if"] == "${{ github.event_name != 'workflow_dispatch' || inputs.target != 'testpypi' }}"
    assert "--publish-url https://test.pypi.org/legacy/" in steps["Publish to TestPyPI"]["run"]
    assert "test.pypi.org" not in steps["Publish to PyPI"]["run"]


def test_gates_run_before_publishing():
    runs = [s.get("run", "") for s in _steps()]
    first_publish = next(i for i, r in enumerate(runs) if "uv publish" in r)
    for gate in ("ruff check", "pytest", "sync_version.py --check", "wheel_smoke.sh"):
        assert any(gate in r for r in runs[:first_publish]), gate
