"""One version everywhere: pyproject.toml, the package, and every agent manifest."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sync():
    spec = importlib.util.spec_from_file_location("sync_version", ROOT / "scripts" / "sync_version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifests_in_sync():
    assert _sync().drift() == [], "run `uv run scripts/sync_version.py`"


def test_versions_agree():
    import showntell

    v = _sync().pyproject_version()
    assert showntell.__version__ == v
    assert json.loads((ROOT / "claude-plugin/.claude-plugin/plugin.json").read_text())["version"] == v
    assert json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())["metadata"]["version"] == v
    assert json.loads((ROOT / "gemini-extension.json").read_text())["version"] == v


def test_marketplace_points_at_plugin():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    plugin = json.loads((ROOT / "claude-plugin/.claude-plugin/plugin.json").read_text())
    (entry,) = market["plugins"]
    assert entry["name"] == plugin["name"] == market["name"] == "showntell"
    assert (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").is_file()


def test_changelog_rule(tmp_path, monkeypatch):
    mod = _sync()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    log = tmp_path / "CHANGELOG.md"
    log.write_text("# Changelog\n\n## 1.2.0 (unreleased)\n\n- thing\n")
    assert "unreleased" in mod.changelog_problem("1.2.0")
    log.write_text("# Changelog\n\n## 1.2.0 (2026-11-01)\n\n## 1.1.0\n\n- old\n")
    assert "empty" in mod.changelog_problem("1.2.0")
    assert "no '## 1.3.0'" in mod.changelog_problem("1.3.0")
    log.write_text("# Changelog\n\n## 1.2.0 (2026-11-01)\n\n- thing\n\n## 1.1.0\n")
    assert mod.changelog_problem("1.2.0") is None
