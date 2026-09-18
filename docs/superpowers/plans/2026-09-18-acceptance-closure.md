# Acceptance Closure Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans and test-driven-development; independent case families may use dispatching-parallel-agents. Keep acceptance evidence distinct from implementation.

**Goal:** Establish verifiable results for all 364 original and 310 expanded designed cases, close software defects, and expose genuine external release gates before promotion.

**Architecture:** Review case oracles against exact executable tests, store reviewed mappings per family, and derive results from fresh JUnit reports. Never convert a fixture skip, partial assertion or simulated hardware test into a full case pass.

**Tech Stack:** Python/pytest, JavaScript/Node, actual n8n and language tools, native macOS launcher, Markdown/JSON evidence.

**Spec:** docs/PRD.md, docs/testing/*-cases.md, docs/testing/ACCEPTANCE-AUDIT-2026-09-18.md.

## Global Constraints

- English first; explicit Simplified Chinese switch.
- Fixed downward graph placement with editable structure, no free node dragging.
- No production accounts, external notifications or changes to the user's existing workflows.
- No automatic system power/permission changes, logout, reboot or signing identity creation during tests.
- Missing real hardware/platform evidence remains blocked rather than passed.

## 1. Review independent case families

- [x] Read all case oracles and exact test assertions. Store one entry per case in docs/testing/traceability/{data,schedule-security,ui,lifecycle}.json.
- [x] Entry format: {"id":"GOLD01","tests":["tests/test_example.py::test_name"],"coverage":"full|partial|manual|superseded","rationale":"specific assertion or missing oracle","remaining":[]}.
- [x] Full means every required oracle and execution level is covered. Parameterized test selectors explicitly select all variants only when all variants are required.
- [x] For software gaps, add real behavioral tests before changing production code; observe the failure and fix only its cause. Preserve stricter design assertions.

## 2. Generate trustworthy case results

- [x] Add tests/test_acceptance_report.py. Verify an unresolved test selector, skipped result, missing evidence, incomplete coverage and duplicate case ID never produce PASSED.
- [x] Implement tools/acceptance_report.py with build_report(cases, mappings, junit_paths), mapping exact pytest file/function selectors to JUnit cases. Emit per-case evidence and summary; require an explicit release gate to return success only when all non-superseded cases pass.
- [ ] Validate completeness against the 364-case index and produce machine-readable and Markdown results without editing historical design oracles.

## 3. Align current PRD

- [x] Replace obsolete horizontal/free-drag and Compose-first introductory instructions in both PRDs with fixed downward and Mac-first setup. Mark superseded interactions as historical. Do not erase real hardware gates.
- [x] Compare acceptance definitions with implementation evidence, retaining all unresolved requirements.

## 4. Execute and review

- [ ] Run isolated regression with actual local n8n/Node/JDK/npm/Maven/Gradle and prepared installation fixtures, preserving JUnit results. External databases need independent real fixture evidence.
- [ ] Run the case-report validator and review every remaining non-pass reason. Continue fixing local software gaps while external gates remain unavailable.
- [ ] Rebuild and self-test the current native package when source changes affect its contents. Never call an ad-hoc signed build a notarized release.
- [ ] Report exact passing/skipped/blocked counts and required external actions. Promotion assessment starts only after complete acceptance.
