"""Release gates must reject stale reports and the wrong frozen entry point."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "build_release", Path(__file__).resolve().parents[1] / "scripts/build_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_stale_installed_version_blocks_build(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.importlib.metadata, "version", lambda _: "0.1.0")
    with pytest.raises(SystemExit, match="reinstall the project"):
        release.release_version()


def test_matching_installed_version_passes(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.importlib.metadata, "version", lambda _: "0.2.0")
    assert release.release_version() == "0.2.0"


def test_missing_new_report_cannot_reuse_previous_success(monkeypatch, tmp_path):
    report = tmp_path / "smoke.json"
    report.write_text(json.dumps({"ok": True, "edition": "steam"}))
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: None)
    with pytest.raises(FileNotFoundError):
        release.verify_frozen_app(tmp_path / "app", report, "steam")


@pytest.mark.parametrize("result", [
    {"ok": True, "edition": "browser"},
    {"ok": True},
    {"ok": False, "edition": "steam"},
    {"ok": "true", "edition": "steam"},
])
def test_invalid_report_blocks_release(monkeypatch, tmp_path, result):
    report = tmp_path / "smoke.json"
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: report.write_text(json.dumps(result)))
    with pytest.raises(SystemExit, match="Frozen smoke test failed"):
        release.verify_frozen_app(tmp_path / "app", report, "steam")


@pytest.mark.parametrize("edition", ["browser", "steam"])
def test_matching_fresh_report_passes(monkeypatch, tmp_path, edition):
    report = tmp_path / "smoke.json"
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: report.write_text(json.dumps({"ok": True, "edition": edition})))
    release.verify_frozen_app(tmp_path / "app", report, edition)


def test_process_failure_blocks_even_with_success_report(monkeypatch, tmp_path):
    report = tmp_path / "smoke.json"

    def failed_run(command, **kwargs):
        report.write_text(json.dumps({"ok": True, "edition": "steam"}))
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(release.subprocess, "run", failed_run)
    with pytest.raises(subprocess.CalledProcessError):
        release.verify_frozen_app(tmp_path / "app", report, "steam")
