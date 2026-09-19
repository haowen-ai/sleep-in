# Releasing Sleep In

Sleep In uses Semantic Versioning. A release has one version in `VERSION`, `pyproject.toml`, `taskconsole/__init__.py` and `CHANGELOG.md`; `python tools/release_notes.py --check` rejects drift.

## Version meaning

- `MAJOR`: an incompatible workflow, API or storage change.
- `MINOR`: backward-compatible user-visible capability.
- `PATCH`: backward-compatible correction.
- `-beta.N`: a preview with documented release gates still open.

## Close a version

1. Move finished entries from `Unreleased` into `[VERSION] - YYYY-MM-DD` in `CHANGELOG.md`, grouped under `Added`, `Changed`, `Fixed` and `Known limitations`.
2. Set the same version in `VERSION`, `pyproject.toml` and `taskconsole/__init__.py`.
3. Run `python tools/release_notes.py --check` and the full test suite.
4. Merge the reviewed change into `main` only after required CI checks pass.
5. From the verified `main` commit, create and push the annotated tag: `git tag -a vVERSION -m "Sleep In vVERSION"` and `git push origin vVERSION`.
6. The tag workflow reruns CI, publishes the versioned multi-architecture container and creates the GitHub Release from that version's changelog section. A version containing `-` is published as a pre-release.

Never move an existing release tag. If a published version is wrong, fix it in a new patch or pre-release version.

## Start the next cycle

Keep `Unreleased` at the top of `CHANGELOG.md` and update its comparison link after each release. Do not describe unsigned, unnotarized or untested hardware behavior as released support.
