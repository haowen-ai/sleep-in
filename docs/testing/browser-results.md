# Browser verification — local workflow preview

Date: 2026-09-17. Chrome, isolated localhost:8766 state, synthetic data only. These are actual UI actions, separate from Node model tests. The existing localhost:8765 installation was not reset.

| Interaction | Observed result |
| --- | --- |
| Fresh login | English initial screen; visible local credentials; Use default account and Sign in open Workflows |
| Template | Morning report creates a saved SQL → Python → JavaScript draft |
| Canvas layout | Three distinct cards and connections; initial CSP positioning bug reproduced and fixed; browser error log empty after reload |
| Unsaved source + language switch | Added a harmless Python comment, changed to Chinese; comment retained. Stale English names/placeholders/status found and corrected |
| Input source | Added an independent constant `note`; original SQL rows mapping remained intact |
| Weekly form | Monday 07:00, America/Chicago; preview displayed September 21/28, October 5/12/19 at 07:00; no Cron expression entry |
| Move and undo | Python card moved by x=20/y=145, Undo restored the exact original coordinates |
| Published execution | Three-node report completed through the running native n8n worker; each card displayed success |
| Port connection | In a separate synthetic draft, dragged Python output to JavaScript input; one edge appeared, both node inputs stayed empty |
| Cycle prevention | Reverse port drag rejected with “该连接会形成循环。”; existing connection preserved |
| No upstream data consumption | Tested that two-node graph from the UI; both succeeded and JavaScript output was `{message:"Hello",received:{}}` |
| Status | Workflow header changed to ready using the workflow worker heartbeat, separately from the legacy scheduler |
| Library drag | After replacing HTML5 drag with pointer handling, dragged Python from the library onto the canvas; exactly one third node appeared and Undo removed it |

The first HTML5 library-to-canvas drag did not add a node through browser automation. The pointer-based fix was subsequently verified with the actual drag gesture; the click fallback is separately functional.

This session did not verify every viewport, keyboard path, long-running schedule, external database, native macOS permission or physical sleep/power condition. Backend integration tests separately check genuine n8n execution and a real-clock scheduled occurrence. An apparent extra node edit in the original QA tab was left untouched; its unsaved draft was not discarded.
