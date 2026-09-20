# Acceptance audit — 2026-09-18

Audited source: `47f6e905a142315c0c26267f635d69354bf1949f`.

**Conclusion: the latest configured CI jobs succeeded; all designed cases and the complete PRD are not yet accepted.** This audit retrieved the current run and decoded job logs. It did not rerun hardware trials or infer acceptance from test counts.

## Current automated evidence

[GitHub Actions run 35373110451](https://github.com/haowen-ai/sleep-in/actions/runs/35373110451), all four jobs completed successfully:

| Job | Observed result |
| --- | --- |
| test | 396 passed, 96 skipped, 2 dependency warnings |
| workflow-integration | 100 passed, 2 dependency warnings |
| external-sql | 29 passed |
| compose-smoke | Actual scheduled n8n SQL → Python → JavaScript graph and exact artifact observed; repeated bootstrap retained one heartbeat workflow |

The suites overlap. Do not add their counts or interpret fixture skips as passes. Separate integration/database jobs cover scenarios skipped by the default test job; this does not establish that every skipped case was executed elsewhere on this source revision.

## Test design traceability gap

`case-index.json` contains 364 designed cases: 180 data, 42 scheduling, 50 UI, 56 local lifecycle, and 36 publication/security. All 364 entries still say `DESIGNED`; they do not carry per-case execution evidence. Tests do exercise many of these behaviors, but a count of passing pytest instances cannot establish one-to-one coverage of the design.

Required closure: map each design ID to executable test(s) or a manual protocol, record source revision/platform/fixture and result, and retain explicit blocked/not-run reasons. No coverage percentage is asserted by this audit.

## PRD release gaps

The implementation and existing automated/browser evidence support the principal workflow features. The following acceptance gates remain open in `COMPLETE-RESULTS.md`:

- **A17:** clean supported Mac installation and sample completion without preinstalled developer tools.
- **A19:** physical locked-screen/display-off scheduling on AC and battery.
- **A20:** consecutive daily runs, physical unplug/replug during execution, login and intentional-stop recovery.
- **A21:** Developer ID signing/notarization, real permission approval/denial/revocation and advertised-platform login/update trials.
- **A23:** physical overnight AC soak and measured battery sessions spanning multiple occurrences.

The Mac package's historical build/self-test is not equivalent to these gates. Local SMTP/HTTP fixtures also do not prove delivery through an owner's external notification provider. A powered-off, critically depleted or system-sleeping Mac cannot be promised uninterrupted execution.

The PRD needs editorial consolidation: its definition still says drag-and-drop, the reference table says horizontal canvas, the first journey says Compose/create administrator, and A22 still says dragging nodes. Later revisions correctly require fixed downward placement and Mac-first guided setup. The historical interaction amendment must be clearly archived instead of competing with current requirements. These documentation inconsistencies are not evidence of separate missing free-drag features.

## Public project discovery

Live repository metadata at audit time: 0 stars, 0 forks, no published releases, blank homepage; topics are docker-compose, n8n, python and scheduler. The About text still describes Python scheduling, while the README describes multilingual visual workflows. The README has a conceptual Mermaid diagram but no actual product demonstration image/video.

Recommended order:

1. Finish the acceptance trace and Mac release gates; distribute an honestly labelled preview until then.
2. Align About with the workflow product, add relevant topics, and show a short real SQL → Python → JavaScript demonstration near the top of both READMEs.
3. Publish a versioned release with installation instructions and synthetic templates; make the Monday-report example reproducible.
4. Share a concrete problem/solution demonstration in relevant communities and invite real trial feedback. Ask interested readers to star for future updates. Do not promise star counts or treat publishing a repository as automatic distribution.

Suggested English About: “A Mac-first visual workflow platform for SQL and multilingual scripts. Schedule your Monday reports, enjoy Sunday night, and sleep in.”

No public promotion, repository-setting change, release publication, power-setting change or OS permission grant was performed by this audit.
