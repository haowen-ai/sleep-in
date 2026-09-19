#!/usr/bin/env python3
"""Validate release metadata and extract one version's GitHub release notes."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def extract_release_notes(changelog: str, version: str) -> str:
    header = re.compile(rf"^## \[{re.escape(version)}\](?:\s+-\s+\d{{4}}-\d{{2}}-\d{{2}})?\s*$", re.M)
    match = header.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no section for {version}")
    next_header = re.search(r"^## \[", changelog[match.end() :], re.M)
    end = match.end() + next_header.start() if next_header else len(changelog)
    notes = changelog[match.end() : end].strip()
    if not notes:
        raise ValueError(f"CHANGELOG.md section for {version} is empty")
    return notes


def validate_tag(tag: str, version: str) -> None:
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"release tag {tag!r} does not match repository version {expected!r}")


def repository_version(root: Path = ROOT) -> str:
    version = (root / "VERSION").read_text().strip()
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    project_version = metadata["project"]["version"]
    init_text = (root / "taskconsole/__init__.py").read_text()
    package_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.M)
    package_version = package_match.group(1) if package_match else None
    versions = {"VERSION": version, "pyproject.toml": project_version, "taskconsole": package_version}
    if len(set(versions.values())) != 1:
        rendered = ", ".join(f"{name}={value!r}" for name, value in versions.items())
        raise ValueError(f"release versions are inconsistent: {rendered}")
    extract_release_notes((root / "CHANGELOG.md").read_text(), version)
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate repository release metadata")
    parser.add_argument("--tag", help="require this Git tag to match the repository version")
    parser.add_argument("--write", type=Path, help="write the current version's release notes")
    args = parser.parse_args()

    if not (args.check or args.write):
        parser.error("choose --check and/or --write")
    version = repository_version()
    if args.tag:
        validate_tag(args.tag, version)
    if args.write:
        notes = extract_release_notes((ROOT / "CHANGELOG.md").read_text(), version)
        args.write.write_text(notes + "\n")
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
