# Workflow UI acceptance test design

Design date: 2026-09-17. Status: **DESIGNED / 待执行；以下案例均为测试设计，不代表已通过验证。**

核心规则：**连线只表示执行依赖，不自动传值**。A→B 时，B 可以选择无输入、常量、工作流参数或明确的上游字段；不使用 A 的数据时仍须等待 A，默认 A 失败会阻止 B。若无需依赖顺序，用户应删除连线。可选字段缺失时的默认值，不等于忽略上游失败。

Sources: [PRD](../PRD.md), [API contract](../WORKFLOW-API.md), [implementation plan](../superpowers/plans/2026-09-17-visual-workflows.md). The PRD defines the intended product; the API is an integration proposal and is not independent evidence of support.

## Test conventions and fixtures

- **P0**: safety, correctness or first-use blocker. **P1**: required authoring/inspection behavior. **P2**: robustness or secondary usability.
- **Model**: deterministic state/contract test without a browser. **Browser**: browser automation against the actual application and API. **Integration**: real workers, database, persistence and n8n orchestration. **Manual**: keyboard, assistive-technology or visual assessment. A mocked API is suitable for isolated UI rendering tests but cannot satisfy an Integration case.
- Use a new isolated test installation, English locale, admin author, separate operator, UTC-controlled clock, and managed synthetic SQLite connection. Do not import predecessor business code, live credentials or personal datasets.
- Fixture **G1 / F-ORDERS**: `q_orders` SQL receiver → `p_summary` Python → `j_report` JavaScript. SQL returns exactly `[{order_id:'A001',amount:'10.50',region:'华东'},{order_id:'A002',amount:'20.25',region:null},{order_id:'A003',amount:'0.00',region:'西部'}]` in deterministic order. Python input `orders` maps to SQL `output.data.rows` (binding `{source:'node',node_id:'q_orders',path:'rows'}`), output `summary={count:3,total:'30.75'}`. JS input `summary` maps to Python `output.data.summary` (binding path `summary`), writes a UTF-8 report artifact. Each node increments a fixture-local execution counter exactly once. Inputs/outputs use the API's named data object, never implicit per-row execution. Schema declares amounts as decimal strings and rows as an array.
- Fixture **G2**: independent nodes `a`, `b`, `c`, with canvas positions deliberately out of dependency order: `c` leftmost, `a` rightmost, `b` between. Names are unique except where a case explicitly tests duplicates. No edges or bindings initially.
- Fixture **G3**: branch `a → b` with condition `summary.count > 0`; `a → c`; explicit `b,c → d` merge. `d.config.join` and merge behavior are set explicitly. Include a variant where `b` is skipped and a variant where `b` fails.
- Save exact graph JSON, node IDs, selected timezone, form values, publication ID, run IDs and counters for assertions. Browser screenshots supplement, rather than replace, state/output evidence.
- The phrase **no upstream data** means a node has zero input mappings from other nodes. It does not imply a dependency edge is absent or that the node has no constant/parameter inputs. A dependency edge alone is ordering/activation and never injects an implicit payload. The target still waits for that predecessor and a predecessor failure blocks it under the default policy.
- The phrase **optional fallback** means an explicitly configured mapping `{source:'node',node_id,path,optional:true,default:value}`. It is distinct from no upstream mapping, a constant, or a parameter. A historical preview must never supply fallback implicitly.

## Canvas authoring and dependency graph

### UI-G01 · Add a language node by drag and drop
- **Priority:** P0
- **Preconditions:** Empty editable workflow; library expanded; viewport 1440×900; zoom 100%.
- **Input:** Python library item; canvas point (480,260).
- **Steps:** Pointer-drag the item into the canvas; drop; save; reload the same workflow.
- **Expected results:** Exactly one Python node with a new stable ID exists; its position corresponds to the drop point in canvas coordinates; inspector opens for that node; source/runtime fields are editable; save/reload retains node and position. No execution or publication occurs.
- **Automation level:** Browser + persistence assertion.

### UI-G02 · SQL subtype remains inside SQL receiver
- **Priority:** P0
- **Preconditions:** Blank workflow; SQL receiver available.
- **Input:** SQLite, PostgreSQL, MySQL, Oracle in turn.
- **Steps:** Add one SQL receiver; change its dialect; inspect matching connection options and query/write mode.
- **Expected results:** Node kind remains `sql`; dialect is persisted under config; only compatible connections are offered; changing dialect does not silently keep an incompatible connection. Library top-level entries contain only SQL/language categories, without business-model, reconciliation, notification or report filler nodes.
- **Automation level:** Browser + API readback.

### UI-G03 · Move nodes without changing execution semantics
- **Priority:** P0
- **Preconditions:** G1 saved; bindings and IDs captured.
- **Input:** Move Python left of SQL; move JS above both; positions include fractional zoom.
- **Steps:** Drag card body at 75%, 100% and 150% zoom; release; save/reload; compare graph excluding position.
- **Expected results:** Only intended positions change; edges follow cards; IDs, names, source, bindings, runtime and dependency direction remain identical. Order on screen never becomes execution order.
- **Automation level:** Browser + Model.

### UI-G04 · Connect output port to input port
- **Priority:** P0
- **Preconditions:** G2; no edges.
- **Input:** `a` output port → `b` input port.
- **Steps:** Drag from output to input; inspect graph; repeat the identical gesture; drop a separate gesture on blank canvas.
- **Expected results:** First gesture creates one directed edge; repeated gesture does not duplicate it; blank-canvas drop creates no edge. No input mapping is silently selected and no run starts. Port interaction does not also move a node. The editor explains that connections determine execution order and explicit input mappings determine data; it never forces the previous node’s output into a new mapping.
- **Automation level:** Browser + Model.

### UI-G05 · Reject self-loops and cycles atomically
- **Priority:** P0
- **Preconditions:** Edges `a → b → c`; graph snapshot captured.
- **Input:** `a → a` and `c → a`, via both ports and input-source picker.
- **Steps:** Attempt each connection; dismiss the error; inspect graph and undo history.
- **Expected results:** A clear localized cycle error identifies the attempted connection; no partial edge or binding survives; saved graph stays unchanged; undo does not unexpectedly erase a previously valid edit.
- **Automation level:** Model + Browser + API rejection.

### UI-G06 · Source selection is unrelated to preceding canvas position
- **Priority:** P0
- **Preconditions:** G2 with `a` rightmost and `c` leftmost; `a` declares `rows`.
- **Input:** Target `c.orders`, source `a.rows`.
- **Steps:** Open `c` input picker; select `a.rows`; then move `b` between `a` and `c`; inspect/run the graph.
- **Expected results:** Picker permits a valid acyclic dependency regardless of canvas position or creation order; selecting the source establishes `a → c`; `b` is not implicitly selected or executed as a prerequisite; actual runtime input comes from `a`.
- **Automation level:** Browser + Model + Integration.

### UI-G07 · Duplicate gets independent identity
- **Priority:** P1
- **Preconditions:** G1; selected Python node has nested constant data and incoming binding.
- **Input:** Duplicate action.
- **Steps:** Duplicate; edit copy name, source and nested constant; inspect original; undo and redo.
- **Expected results:** New node ID and visible offset; original content is unchanged; copy preserves configured bindings only according to the explicit duplication rule, with valid corresponding dependencies; no outgoing edges are reassigned silently. Undo/redo yields deterministic graph states. Apply C07: deep-copy content and incoming edges, create no outgoing edges.
- **Automation level:** Model + Browser.

### UI-G08 · Undo/redo covers complete graph edits
- **Priority:** P1
- **Preconditions:** Saved G2.
- **Input:** Add node; move it; connect; map input; rename; delete.
- **Steps:** Perform sequence; undo every committed edit; redo; undo once and perform a different edit.
- **Expected results:** Every undo restores the complete prior node/edge/binding state; a drag is one undoable move, not one per pointer event; redo reproduces states; a new edit clears redo; controls expose unavailable history accessibly. History must not mutate immutable publications.
- **Automation level:** Model + Browser.

### UI-G09 · Layout, zoom and fit preserve data
- **Priority:** P1
- **Preconditions:** Branched graph with 10 nodes, then 25 and 50; captured logical graph.
- **Input:** Auto layout; zoom limits; fit.
- **Steps:** Run layout, zoom in/out repeatedly, fit, select farthest node through outline; save/reload.
- **Expected results:** No node/edge/binding loss; acyclic dependencies remain unchanged; nodes can be reached without page-wide overflow; fit includes full graph; overlay controls remain usable. Record render/interaction timings as measurements, not a predeclared performance guarantee.
- **Automation level:** Browser + Model + Manual visual review.

### UI-G10 · Disconnected edge reveals affected mappings
- **Priority:** P0
- **Preconditions:** G1; `p_summary.orders` references SQL.
- **Input:** Remove only SQL→Python edge.
- **Steps:** Delete edge; inspect mapping validation; attempt draft save and publish; undo removal.
- **Expected results:** Mapping is visibly unreachable unless another valid upstream path remains; draft can save incomplete state; publish is blocked with a link to Python; no constant or prior sample replaces the mapping. Undo restores edge and resolves the corresponding error.
- **Automation level:** Model + Browser + API.

## Input mapping and optional-data semantics

### UI-M01 · SQL rows map intact through the visual field picker
- **Priority:** P0
- **Preconditions:** G1 source nodes exist; outputs declared; no Python/JS bindings yet.
- **Input:** `orders ← Order query → rows`; `summary ← Summary → summary`.
- **Steps:** Select source node then field using labeled controls; save; run test; inspect admitted snapshot and all node inputs/outputs.
- **Expected results:** Stable IDs, not display names, are stored; dependency edges exist; Python receives one array of exactly three rows in declared order; Python and JS each execute once; total is `30.75`; JS receives the summary object; no per-row fan-out occurs.
- **Automation level:** Browser + Integration with counters and real n8n evidence.

### UI-M01B · Large SQL result preview never truncates Python input
- **Priority:** P0
- **Preconditions:** F-ORDERS passes; second synthetic SQL fixture has 205 ordered rows, with a distinct final-row sentinel and a declared complete row count. UI preview page limit is 100 or another explicit smaller number.
- **Input:** `orders ← SQL output.data.rows`; Python computes count and includes the final-row sentinel in its summary.
- **Steps:** Test; open SQL table preview and page to final row; open Python input preview; inspect Python summary and retained full payload/artifact. Also rerun with zero SQL rows.
- **Expected results:** Preview explicitly reports displayed range and total; Python receives all 205 rows exactly once, not the first preview page, and finds the last sentinel. If inline quota requires dataset/artifact transfer, a complete reference follows the declared contract; the system never silently substitutes a truncated array. Empty query passes `orders:[]` successfully and executes Python once. G1/F-ORDERS continues to show all three precise rows and total `30.75`.
- **Automation level:** Browser + Integration with full-data assertion.

### UI-M02 · Choose not to use previous-node output
- **Priority:** P0
- **Preconditions:** SQL→Python dependency edge exists; SQL produces rows; Python has no node-source mappings and source returns the complete input object for inspection.
- **Input:** Explicitly choose no upstream mapping; keep empty input map, then constant `message='hello'` only.
- **Steps:** Remove any existing `orders` mapping; test; add only constant `message`; test again.
- **Expected results:** First input is `{}` and second is `{message:'hello'}` under the confirmed explicit-mapping rule; SQL rows are not injected based on edge, node position or historical sample. Dependency ordering remains if edge remains. Neither run is described as optional fallback. Repeat with a slow SQL predecessor: Python does not start before SQL completes. Repeat with SQL failure: Python stays blocked under default policy despite having no upstream-data mapping. Remove the edge: Python becomes independent and no ordering is promised.
- **Automation level:** Browser + Integration.

### UI-M03 · Independent downstream-looking node has no implicit dependency
- **Priority:** P0
- **Preconditions:** G2; `b` visually immediately after `a`; no edges or mappings; each returns its input and a marker.
- **Input:** `a` emits `{token:'must-not-leak'}`; `b` receives constant `{label:'independent'}`.
- **Steps:** Run graph; inspect `b` inputs, dependency snapshot and invocation count.
- **Expected results:** `b` receives only its declared inputs, never `a.token`; there is no implicit ordering promise; each admitted node runs once as allowed by orchestration. Do not assert parallel execution solely because nodes are independent.
- **Automation level:** Integration + Browser inspection.

### UI-M04 · Optional mapping uses explicit fallback for a missing field
- **Priority:** P0
- **Preconditions:** Source succeeds with `{}`; target binding declares `optional:true,default:[]`, expected type array.
- **Input:** `orders ← source.rows`, optional fallback `[]`.
- **Steps:** Configure optional mapping in UI; save/reload; test; inspect actual target input.
- **Expected results:** Optional/default remain explicit in saved contract; target receives `orders:[]`; no stale sample is substituted; UI distinguishes missing field from actual empty array. Behavior is not presented as “do not use previous output.”
- **Automation level:** Browser + API + Integration. UI controls currently unspecified; see C02.

### UI-M05 · Existing empty, null, zero and false values are not missing
- **Priority:** P0
- **Preconditions:** Optional binding with fallback chosen to differ visibly from actual value.
- **Input:** Source value cases `[]`, `{}`, `null`, `0`, `false`, `''`; schema permits tested value.
- **Steps:** Run each source value; inspect target input and status.
- **Expected results:** Existing permitted values pass through unchanged; fallback applies only to the agreed missing/skipped condition, not JavaScript/Python truthiness. A null incompatible with declared type fails validation rather than silently replacing data.
- **Automation level:** Contract/Integration + representative Browser case.

### UI-M06 · Required missing output blocks dependent execution
- **Priority:** P0
- **Preconditions:** Source output has no `summary`; target requires `summary` without fallback; target side-effect counter starts at zero.
- **Input:** Required node binding to absent field.
- **Steps:** Test; open failed node/error; try publication when declared schemas already prove mismatch.
- **Expected results:** Missing declared field is caught at validation where determinable; otherwise runtime reports precise missing input and target does not execute; counter remains zero; no empty object, previous output or constant is fabricated.
- **Automation level:** API + Integration + Browser.

### UI-M07 · Optional mapping from skipped or failed predecessor
- **Priority:** P0
- **Preconditions:** G3; target input to `b.result` optional with explicit default; independent required input from `c` valid.
- **Input:** Variant 1 `b` skipped; variant 2 `b` fails; variant 3 required `c` fails; explicit `join` policy.
- **Steps:** Run each variant; inspect completion markers, target admission and final status.
- **Expected results:** Skipped branch cannot deadlock merge; configured missing-field/skipped-input fallback is visible when the agreed activation policy admits the target; required failed input blocks target. A mapping marked optional does **not** ignore its predecessor’s failed execution: variant 2 remains blocked under the default policy. Explicit required=false dependency tolerance uses the separate control and fixed C03 join truth table; it cannot be inferred from optional fallback.
- **Automation level:** Integration + Browser status inspection.

### UI-M08 · Constant, parameter and upstream inputs coexist
- **Priority:** P0
- **Preconditions:** G1; workflow params `{region:'East',threshold:20}`.
- **Input:** Python mappings: `orders ← q_orders.rows`, `region ← parameter.region`, `limit ← constant 2`, `enabled ← constant false`, `tags ← constant ['demo']`.
- **Steps:** Configure through input table; save/reload; run with invocation override `{region:'West',threshold:25}`; inspect inputs.
- **Expected results:** All five input keys preserve types; only bound parameter `region` changes to `West`; unbound `threshold` is not injected implicitly; constants and source rows retain exact values; one row in the UI does not overwrite another.
- **Automation level:** Browser + Integration.

### UI-M09 · Duplicate/blank target fields and invalid values
- **Priority:** P1
- **Preconditions:** Input editor open with existing `orders` mapping.
- **Input:** Duplicate `orders`; whitespace-only target; malformed JSON constant `[1,`; target names containing dots.
- **Steps:** Add/save each case; switch source kind after a validation error.
- **Expected results:** No silent overwrite or orphan mapping; field-specific error is localized; valid unsaved rows remain intact. If dotted target names are unsupported or represent literal keys, UI states and enforces that policy consistently with API.
- **Automation level:** Model + Browser + API.

### UI-M10 · Renaming nodes preserves references
- **Priority:** P0
- **Preconditions:** G1 saved and published once; IDs captured.
- **Input:** Rename SQL to a Unicode name; rename Python to same display name as another node.
- **Steps:** Save draft; inspect mapper labels and stored bindings; test; open prior run.
- **Expected results:** Bindings retain original stable IDs and execute correctly; picker disambiguates duplicate names; displayed draft labels update; immutable publication and prior run retain their captured names/source rather than being rewritten.
- **Automation level:** Model + Browser + Integration.

### UI-M11 · Deleting a source exposes broken references
- **Priority:** P0
- **Preconditions:** G1; downstream mapping references SQL.
- **Input:** Delete SQL node.
- **Steps:** Delete; inspect Python input row; save draft; attempt publish; undo; then rebind to a different SQL node.
- **Expected results:** Source card and its edges are removed; downstream mapping remains visibly broken for repair; publish blocked with target and field information; draft save allowed; undo restores original IDs/references; explicit rebind changes only selected mapping. Do not silently erase user intent or substitute a same-named node.
- **Automation level:** Model + Browser + API.

### UI-M12 · Samples and declared output schema stay distinct
- **Priority:** P1
- **Preconditions:** Last successful run exposes `summary.count=3`; edit source/schema to rename summary field without rerunning.
- **Input:** Open output picker and example preview.
- **Steps:** Inspect run attribution/stale indication; map declared nested field; run new snapshot; expire old sample in fixture.
- **Expected results:** Samples show run ID and stale/expired state; declared fields remain inspectable without a sample; arrays are selected whole; no numeric example becomes a production constant unless explicitly chosen. Expired samples do not cause substitution from an unrelated run.
- **Automation level:** Browser + Integration retention fixture.

### UI-M13 · Type-incompatible mapping
- **Priority:** P0
- **Preconditions:** SQL `rows` declares array; Python input `orders` requires array.
- **Input:** Map `rowCount` number instead of `rows` array; optional fallback string instead of array.
- **Steps:** Select/save mappings; validate/publish; attempt draft test if invalid drafts are admitted for testing.
- **Expected results:** Known incompatibilities block publication and identify target/source types; invalid fallback is rejected; runtime repeats type validation for uncertain source schemas. UI must not coerce count to a one-element array.
- **Automation level:** API + Browser + Integration.

## Language, localization and keyboard access

### UI-L01 · English first, explicit Chinese switch
- **Priority:** P0
- **Preconditions:** Fresh browser storage and user without saved locale; OS/browser locale Chinese.
- **Input:** Open login; switch to Chinese; sign in; switch to English.
- **Steps:** Inspect login/default-account card, navigation, catalog, editor, inspector, schedules and run detail.
- **Expected results:** Initial application language English; explicit switch controls are discoverable before and after login; all application labels, statuses, errors, tooltips and empty states translate. User code, node names, data and credentials are not translated.
- **Automation level:** Browser + catalog contract test + Manual text review.

### UI-L02 · Locale switch preserves unsaved graph and editor buffers
- **Priority:** P0
- **Preconditions:** Saved G1; create unsaved node/edge/mapping edits; source textarea has unblurred edits; node name contains Unicode.
- **Input:** Switch EN→中文→EN without saving.
- **Steps:** Capture graph, textarea value, selection, binding draft and undo state; switch twice; save and reload.
- **Expected results:** No API reload overwrites graph; IDs, source byte content, bindings, positions, in-progress input fields and dirty state survive; no auto-publication/run occurs; selection remains usable; save persists exactly the authored draft. Undo history stays valid across language switch.
- **Automation level:** Browser + Model serialization assertion.

### UI-L03 · Locale switch preserves schedule/time semantics
- **Priority:** P0
- **Preconditions:** Unsaved weekdays 07:30 Asia/Shanghai schedule; preview obtained; params include numeric strings.
- **Input:** Switch locales while time/timezone controls are focused.
- **Steps:** Capture schedule JSON and UTC preview instants; switch; preview again.
- **Expected results:** Same schedule/timezone and UTC instants; localization may change display formatting only; `07:30` does not become browser-local time; numeric strings do not become locale-formatted numbers; enabled state is not changed.
- **Automation level:** Browser + schedule API.

### UI-L04 · Keyboard-only graph authoring
- **Priority:** P1
- **Preconditions:** Blank workflow; pointer unused.
- **Input:** Tab, Shift+Tab, Enter, Space, Escape, arrow keys where documented.
- **Steps:** Reach library; add SQL and Python; select via outline; edit names/source; map an upstream field through selectors; save; publish valid graph; open run detail.
- **Expected results:** Every core action has a keyboard alternative to drag; visible focus order is logical; source editor does not trap Tab without an escape method; port-only gestures are not required to map data; focused controls have accessible names; labels correctly reference inputs.
- **Automation level:** Browser keyboard automation + Manual assistive-technology review.

### UI-L05 · Inspector/modal focus and shortcut boundaries
- **Priority:** P1
- **Preconditions:** Selected node; mapper dialog or inspector open.
- **Input:** Escape, close button, Delete/Backspace, Ctrl/Cmd+Z in code input versus canvas.
- **Steps:** Close overlays; reopen from keyboard; type and undo inside source; select canvas and delete node.
- **Expected results:** Focus returns to the invoking control/selected node; modal focus remains contained only when actually modal; Delete while typing does not delete node; text undo does not accidentally undo graph changes; graph shortcuts are documented and affect selected graph item only.
- **Automation level:** Browser + Manual.

### UI-L06 · Responsive and bilingual layout
- **Priority:** P1
- **Preconditions:** English then Chinese, long workflow/node names, 50-node graph, run JSON/logs.
- **Input:** Viewports 1440, 1280, 1100, 1024, 768 and 390 CSS px wide.
- **Steps:** Inspect editor, inspector, menus, schedules and run detail; collapse navigation/panels; scroll logs/JSON.
- **Expected results:** At ≥1280 both authoring panels usable; 1024–1279 one panel at a time; narrow devices prioritize reading/schedule controls; no page-wide horizontal overflow; long code/data uses contained scrolling; no hidden primary controls or overlapping text. Do not claim full phone graph editing.
- **Automation level:** Browser screenshots/overflow assertions + Manual visual review.

## Schedule forms

### UI-S01 · Weekdays first-use journey
- **Priority:** P0
- **Preconditions:** G1 successfully published; schedule disabled.
- **Input:** Weekdays, 07:30, Asia/Shanghai, enabled.
- **Steps:** Choose ordinary form controls; preview next five; save; reload; inspect admitted scheduled run at controlled due time.
- **Expected results:** Natural-language schedule summary and five labeled occurrences; no Cron field or expression required; Monday–Friday semantics explicit and no holiday adjustment implied; saved values exact; only published version scheduled; actual run evidence uses n8n and captured publication.
- **Automation level:** Browser + Integration clock-controlled dispatch.

### UI-S02 · All supported schedule form variants
- **Priority:** P1
- **Preconditions:** Published workflow; schedule disabled while editing.
- **Input:** Manual; interval 15 minutes and 2 hours with explicit anchor; daily 07:30; weekly Monday/Thursday; monthly day 15 and last; once date/time.
- **Steps:** Select every kind; fill visible required controls; preview; save/reload; switch between kinds.
- **Expected results:** Relevant controls shown and required; stale fields from previous kinds do not affect payload; preview corresponds to saved canonical schedule; manual has no future occurrences; once cannot silently recur; no Cron input in new workflow UI. Multi-time controls are required if advertised, see C04.
- **Automation level:** Browser + schedule API.

### UI-S03 · Invalid form values and unpublished enablement
- **Priority:** P0
- **Preconditions:** Unpublished draft, then published workflow.
- **Input:** Interval 0/negative/noninteger; no weekly weekdays; day 32; invalid timezone; end before start; empty once date; enable unpublished workflow.
- **Steps:** Attempt preview/save/enable for each.
- **Expected results:** Specific accessible errors, no fake preview or enabled status; server validates independently of UI; invalid authoring state may save only according to draft policy but cannot enable; valid user fields survive correction.
- **Automation level:** Browser + API.

### UI-S04 · Month boundaries, leap years and schedule bounds
- **Priority:** P0
- **Preconditions:** Controlled date fixtures covering February 2028/2029 and April.
- **Input:** Monthly day 31 versus month-end; start after next candidate; end before fifth candidate; once in past.
- **Steps:** Preview and compare scheduler's next admitted occurrences.
- **Expected results:** Day 31 skips missing dates; month-end selects actual last day including leap day; bounds honored; fewer than five occurrences is displayed honestly where bounded; past once does not trigger an unannounced replay. Preview and dispatch use the same calculation.
- **Automation level:** Schedule unit/Integration + Browser presentation.

### UI-S05 · DST gap and fold
- **Priority:** P0
- **Preconditions:** America/New_York, controlled dates around 2027-03-14 and 2027-11-07.
- **Input:** Daily 02:30 at spring transition; daily 01:30 at autumn transition.
- **Steps:** Preview around transitions; dispatch controlled due occurrences; view timezone/offset and run count.
- **Expected results:** Spring missing wall time skipped; autumn repeated wall time first occurrence only; no duplicate run; UI timezone unambiguous; English/Chinese previews refer to identical instants.
- **Automation level:** Schedule unit/Integration + Browser.

### UI-S06 · Paused schedules and version changes
- **Priority:** P0
- **Preconditions:** Published v1 schedule enabled; v2 draft differs; an admitted v1 run exists.
- **Input:** Pause schedule; edit draft; publish v2; re-enable.
- **Steps:** Inspect next-run state and frozen admitted run; advance clock while paused; re-enable and advance to future due time.
- **Expected results:** Pause stops future admissions without cancelling admitted run; draft edit alone does not change published execution; admitted run keeps v1; future runs follow agreed current-publication policy; paused periods do not replay as a burst. Save controls cannot imply schedule is enabled before server confirmation.
- **Automation level:** Browser + Integration.

## Publication, actual execution and inspection

### UI-R01 · Save incomplete draft versus publish valid snapshot
- **Priority:** P0
- **Preconditions:** New workflow missing connection/runtime/required binding.
- **Input:** Save; publish; repair configuration; publish again.
- **Steps:** Save/reload invalid draft; inspect errors linked to nodes; repair; publish; edit source after publication; inspect version state.
- **Expected results:** Save succeeds as draft with honest validation; invalid publish does not create version; valid publication creates immutable version ID; later edits mark draft dirty and do not mutate published source. UI distinguishes saved, published and runtime-ready.
- **Automation level:** Browser + API + persistence assertions.

### UI-R02 · Draft test executes real frozen snapshot
- **Priority:** P0
- **Preconditions:** Published G1 v1; draft changed to produce count marker 4; test fixture effects confined locally.
- **Input:** Test workflow; immediately edit draft again before run completes.
- **Steps:** Submit test; inspect returned queued state, run snapshot and output; open history.
- **Expected results:** Test is clearly marked separately; API acceptance is not displayed as success; captured draft marker 4 executes despite later edit; published v1 unchanged; actual node statuses/logs drive UI; tests may have real side effects and are never called simulations; formal notifications off by default.
- **Automation level:** Browser + Integration.

### UI-R03 · Published run ignores unsaved draft changes
- **Priority:** P0
- **Preconditions:** Published v1 produces marker 3; unsaved draft marker 9.
- **Input:** Run published.
- **Steps:** Execute; inspect version ID, node output and local counter.
- **Expected results:** Marker 3 and v1 execute; one admitted invocation; UI names published action clearly; draft remains editable and unsaved; no implicit save/publish occurs.
- **Automation level:** Browser + Integration.

### UI-R04 · Actual n8n execution and missing-engine failure
- **Priority:** P0
- **Preconditions:** Variant A valid real n8n; variant B intentionally unavailable executable in isolated installation.
- **Input:** Run G1 once in each environment.
- **Steps:** Inspect engine identity, per-node outputs, run logs and run final state; compare counters.
- **Expected results:** A has actual n8n graph/worker evidence and one invocation per node; B reports an actionable failure, not succeeded or indefinitely fake running; no hidden local topological executor substitutes for n8n. A static “n8n” label alone is insufficient proof.
- **Automation level:** Integration + Browser.

### UI-R05 · Logs, outputs, artifacts and refresh
- **Priority:** P0
- **Preconditions:** G1 run emits distinct stdout/stderr and report artifact; at least one prior failed run.
- **Input:** Open history; filter/select exact run; expand each node; inspect input, output, stdout, stderr, attempts and files; download report.
- **Steps:** Refresh while run active; navigate away/back; download artifact; reload browser.
- **Expected results:** Data tied to correct run/node/attempt; structured output separate from logs; artifact bytes match expected report/checksum; statuses persist after reload; downloads authorized; no previous run's sample silently displayed as current output. Failure reason remains readable and selected failed node focus is possible.
- **Automation level:** Browser + Integration/download content assertion.

### UI-R06 · Failure, timeout and downstream blocking
- **Priority:** P0
- **Preconditions:** G1 variants: Python exception, invalid output schema, process exceeds timeout; JS counter zero.
- **Input:** Run each variant.
- **Steps:** Observe progression; inspect source node logs and dependent node status; open history after reload.
- **Expected results:** Final status accurately distinguishes failed/timed_out; error reason shown; JS not executed when required input fails; no success based solely on exit 0 for invalid/missing output; statuses have text/icons, not color only.
- **Automation level:** Integration + Browser.

### UI-R07 · Duplicate clicks and network uncertainty
- **Priority:** P0
- **Preconditions:** Valid published workflow; slow admission response; local counter zero.
- **Input:** Double-click Run, retry after response interruption, refresh page during queued state.
- **Steps:** Submit with one logical idempotency identity; simulate lost response; inspect durable runs/counter.
- **Expected results:** One logical admission and one side effect; button becomes pending until resolved; retry behavior is safe and explicit; UI never invents a new run because status fetch failed. Independent deliberate later runs remain possible.
- **Automation level:** Browser network fault injection + Integration.

### UI-R08 · Cancel request versus actual cancellation
- **Priority:** P0
- **Preconditions:** Active long-running fixture; second queued fixture; outputs recorded before cancellation.
- **Input:** Cancel active run and queued run.
- **Steps:** Submit cancel; inspect cancelling state; wait for runtime termination acknowledgement; reload history.
- **Expected results:** Request acknowledgement does not immediately claim process stopped; final status follows actual cancellation; no downstream admission after cancelled prerequisites; captured prior side effects are not claimed rolled back; controls reflect terminal state.
- **Automation level:** Browser + Integration process evidence.

### UI-R09 · Operator authorization and unavailable runtime
- **Priority:** P0
- **Preconditions:** Admin and operator accounts; published workflow; Java/C/C++ unavailable in one controlled host fixture.
- **Input:** Operator inspects/runs published workflow; attempts authoring/publish/runtime/connection mutations; admin chooses unavailable runtime.
- **Steps:** Check UI affordances and direct API attempts; try publication; open runtime center.
- **Expected results:** Operator mutation denied server-side; UI does not imply author permissions; runtime center reflects actual detection/reason; unavailable runtime blocks publish with repair guidance; no fake install progress or “Ready” purely from selecting language.
- **Automation level:** Browser + API + runtime Integration fixture.

### UI-R10 · Runtime selection is per language/node
- **Priority:** P1
- **Preconditions:** G1 plus Shell/Java/C/C++ nodes with distinct registered toolchains.
- **Input:** Change Python runtime only; choose JS runtime; inspect compiled-language entrypoint/build config.
- **Steps:** Save/reload; publish valid variants; inspect captured runtime IDs and build result; change runtime availability after publication.
- **Expected results:** Python setting does not become implicit environment for other languages; matching profiles only; compilation failure blocks publication; run snapshot retains selected version/identity; capability claims reflect native platform limits rather than container isolation.
- **Automation level:** Browser + API + Integration on supported toolchain fixtures.

### UI-R11 · Templates and connection replacement
- **Priority:** P1
- **Preconditions:** Fresh isolated installation with no external accounts.
- **Input:** Morning report template; later replace SQLite with another explicitly provisioned test connection.
- **Steps:** Instantiate; inspect graph; run; clone template twice; compare IDs; inspect export if available.
- **Expected results:** Synthetic SQLite→Python→JS works without external credentials; user workflow edits do not mutate catalog template or other instance; IDs safely scoped/unique; connections rebind explicitly; exports omit secrets, private endpoints and historical data. Never report external dialect tested solely from selectable subtype.
- **Automation level:** Browser + Integration + export contract test if endpoint exists.

### UI-R12 · Connection test is not publication/runtime guarantee
- **Priority:** P1
- **Preconditions:** Managed synthetic connection; separate external test connection available then disconnected.
- **Input:** Test connection; remove connectivity; execute workflow.
- **Steps:** Observe success response; inspect metadata; break fixture connectivity; rerun test/run.
- **Expected results:** Connection test results reflect actual request and timestamp; metadata never exposes credentials; prior successful test does not force runtime Ready/success after outage; failed execution displays database error safely; SQL source does not receive stored secret values.
- **Automation level:** Browser + API + Integration.

## Open product/contract questions and contradictions

| ID | Source conflict or omission | Required decision before exact test/implementation |
|---|---|---|
| C01 · RESOLVED | PRD explicit mappings versus possible implicit merge/input inheritance. Root/user steering confirms dependency and input independence. | Edges control ordering/activation only. `inputs` exclusively defines runtime input content. No mapping means no upstream data; a connected target still waits and is blocked by default predecessor failure. Remove edge if ordering unnecessary. Explicit merge format must not violate this rule. |
| C02 · FIXED | API accepts `optional` and `default`; PRD requires optional defaults visible. The planned input table does not yet specify optional/fallback controls or missing-vs-null semantics. | Provide explicit optional toggle plus typed fallback editor per mapping. Define missing property separately from present `null`, `false`, `0`, empty string and empty array. |
| C03 · FIXED | PRD allows optional failure to yield partial outcome and lists workflow `partial`; API run status list omits `partial`, and `join:any`/failed-predecessor optional semantics are incomplete. | Confirmed: optional binding fallback never ignores predecessor failure by itself. Default failed predecessor blocks target. Use the fixed join truth table and required=false policy in contract-decisions.md; never mark tolerated actual failure as full success. |
| C04 | PRD requires multiple named triggers and daily/weekly/monthly multiple times; shared object/API exposes one `schedule` plus `enabled`, no trigger object CRUD. API accepts `times` but frontend plan could show one time only. | Either deliver trigger collections/multi-time controls, or explicitly narrow initial slice and mark unimplemented acceptance scope. A single schedule cannot be advertised as multi-trigger support. |
| C05 | PRD says runtime profile versions are immutable and optional packs can be installed with real progress/self-test. API offers GET runtimes with native detected executable/version, no registration/build/install endpoints or immutable runtime version object. | Define initial supported scope: detected native runtimes with owner-managed install guidance versus full runtime center. Do not show working install/edit/version controls without backend contract. |
| C06 · FIXED | PRD requires target types, declared output schemas, reachable fields, nested picker and stale samples. API allows optional `outputs` JSON Schema and input `type` but does not specify a parameter schema, nested array path semantics, or sample provenance contract. | Agree supported schema subset and paths; define whether only object traversal is allowed and arrays passed whole; add sample run/version/timestamp/staleness metadata or compute it from explicit known snapshots. |
| C07 · FIXED | PRD requires duplicate new IDs but does not say whether incoming/outgoing edges duplicate. API lacks edit-operation semantics because whole graph is saved. | Establish one deterministic duplication rule; recommended preserve source/config/inputs and necessary incoming dependencies, create no outgoing consumers automatically. |
| C08 · FIXED | API draft errors `{node_id?,message}` differ from PRD field-linked validation and existing generic frontend API errors; backend generic errors are documented `{detail:string}`. | Standardize machine-readable code, node_id, field/path and message, or explicitly adapt current error shape without hiding server explanations. |
| C09 | PRD requires workflow tests and historical sample tests, retry attempts, retention, cancellation and output quota/dataset handling. Current API has workflow `/run test:true`, no node-test endpoint, retry/resume endpoint or paginated previews. | Separate implemented whole-workflow testing from later node testing/recovery/large dataset preview. Never offer controls that imply unavailable operations. |
| C10 | PRD first-use includes successful artifact download and all-language execution. Native runtime detection alone does not prove source contract, compilation or n8n execution. | Require real integration evidence for each advertised language/dialect; availability badges only assert toolchain detection until build/run validation occurs. |
| C11 · FIXED | PRD requires default fresh local credentials hidden after password change and no reset of existing accounts. API document only covers workflows. | Root integration must supply safe `bootstrap.local_account` only when eligible. Frontend displays supplied values only and never assumes credentials from literal defaults; invalid/stale bootstrap must not reintroduce card after password change. |
| C12 | PRD mentions settings/notifications, connection edit/secret rotation and authorization, multiple runtime versions, node source ZIP/project/import/export; API only covers limited create/read/test surface. | Maintain a release scope table and do not treat a selectable page or static setting as completed capability. Add contracts before implementing those controls. |

## Additional local-login cases owned by integration

### UI-A01 · Default local account display and fill
- **Priority:** P0
- **Preconditions:** Fresh local eligible installation returns `bootstrap.local_account={username,password}`; comparison installations return no local_account.
- **Input:** Open login; use default account; switch locale; sign in.
- **Steps:** Inspect card; click fill action; compare form fields with actual bootstrap values; sign in; test comparison installations.
- **Expected results:** Card only when server supplies it; exact credentials filled, no automatic submission; English/Chinese labels translate; no reset of existing accounts; no defaults guessed when absent; fresh credential login actually succeeds.
- **Automation level:** Browser + authentication Integration.

### UI-A02 · Password change removes public-default affordance
- **Priority:** P0
- **Preconditions:** Eligible local default account signed in, two sessions active.
- **Input:** Change password via Account.
- **Steps:** Submit change; revisit login in both sessions; refresh bootstrap; try old credentials and then new.
- **Expected results:** Existing sessions revoked according to policy; default card disappears including stale in-memory frontend state; old password rejected; new password never displayed; current/private password not stored in test artifacts or screenshots.
- **Automation level:** Browser + authentication Integration.

## Resolution

The identified ambiguities have fixed behavior in [contract decisions](contract-decisions.md). C04/C05/C09/C12 remain implementation/API coverage gaps, not a reduction of PRD scope. Use those decisions for exact assertions; the table above preserves what the review found.

## Execution and evidence plan

1. Apply the fixed C01–C12 decisions and join truth table before related feature changes. API coverage gaps remain tracked requirements, not ambiguous expected behavior.
2. Implement/test pure graph and mapping contracts against agreed semantics, then browser interactions against real persisted API. Test fixtures may intentionally represent unsupported states to verify errors.
3. Run P0 first-use and correctness flows using actual n8n and fixture-local side effects. Capture version/run IDs, node input/output, counters and downloaded artifact bytes. A browser screenshot or API 202 alone never passes a runtime case.
4. Run localization/keyboard/responsive checks independently; do not infer accessibility from translated labels or use screenshot-only tests to prove draft preservation.
5. Track each case in an execution ledger with result (not run/pass/fail/blocked), build commit, environment, evidence and defect links. Unresolved contract cases are **blocked**, not skipped-as-passed. Never prefill a passing status in this design document.
