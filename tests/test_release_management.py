from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

from taskconsole import __version__


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.2.0-beta.2"


def load_release_notes_module():
    path = ROOT / "tools/release_notes.py"
    spec = importlib.util.spec_from_file_location("release_notes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_version_is_consistent_across_package_metadata() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert (ROOT / "VERSION").read_text().strip() == EXPECTED_VERSION
    assert metadata["project"]["version"] == EXPECTED_VERSION
    assert __version__ == EXPECTED_VERSION


def test_release_notes_extract_the_exact_version_without_unreleased_content() -> None:
    release_notes = load_release_notes_module()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    notes = release_notes.extract_release_notes(changelog, EXPECTED_VERSION)

    assert "haowen-ai/sleep-in" in notes
    assert "clone, CI, release" in notes
    assert "Known limitations" in notes
    assert "Unreleased" not in notes
    assert "0.1.0" not in notes


def test_release_tag_must_match_the_repository_version() -> None:
    release_notes = load_release_notes_module()

    release_notes.validate_tag(f"v{EXPECTED_VERSION}", EXPECTED_VERSION)
    with pytest.raises(ValueError, match="does not match"):
        release_notes.validate_tag("v0.2.0", EXPECTED_VERSION)


def test_public_repository_links_use_the_current_owner() -> None:
    linked_files = [
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs/VERIFICATION.md",
        ROOT / "docs/testing/ACCEPTANCE-AUDIT-2026-09-18.md",
        ROOT / "docs/testing/COMPLETE-RESULTS.md",
        ROOT / "docs/testing/traceability/ui.json",
    ]

    for path in linked_files:
        text = path.read_text()
        stale_owner_url = "https://github.com/" + "haowenchen0811/sleep-in"
        assert stale_owner_url not in text
        assert "https://github.com/haowen-ai/sleep-in" in text


def test_release_workflow_creates_a_prerelease_after_verified_image() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "python tools/release_notes.py --check" in workflow
    assert "python tools/release_notes.py --write release-notes.md" in workflow
    assert "needs: image" in workflow
    assert "contents: write" in workflow
    assert "gh release create" in workflow
    assert "--prerelease" in workflow
