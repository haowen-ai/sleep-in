# Workflow data and orchestration test design

Status: **fixed test design; these cases have not been executed**.

本文件是测试设计，不是测试通过报告。先确定每个节点应该收到什么、输出什么，再验证真实运行。连线只决定执行顺序；输入映射才决定使用哪些数据。A→B 时，B 可以完全不用 A 的输出，但仍等待 A，且 A 默认失败时阻止 B。若 B 连顺序也不依赖 A，应删除连线。 This design was prepared after the instruction to pause feature implementation. It follows the fixed acceptance contract before adding more implementation or automated tests. An installed compiler, a passing unit test, or an HTTP 202 is not evidence that an end-to-end workflow succeeds.

Scope: node input/output; SQL receivers; every ordered pair of the seven initial node languages; mappings versus dependencies; artifacts; conditions and merges; failure/retry/concurrency boundaries; immutable execution; actual n8n orchestration. Scheduling, UI, local account, and macOS lifecycle have separate matrices; only their data-admission intersections are included here.

References: `docs/PRD.md` sections 7–11 and A03–A12/A16; `docs/WORKFLOW-API.md` is a provisional implementation contract, not a statement of delivered behavior. Where they disagree, the approved PRD takes precedence. Core semantics follow `docs/testing/contract-decisions.md` C01–C12 plus the parent task’s explicit path, envelope and SQL statement-list rulings. They are fixed design, not implementation evidence.

## 1. Execution levels, priority, and result recording

- **P0**: publication/execution integrity or first-use acceptance blocker; must pass before a usable workflow release.
- **P1**: supported language/dialect/feature acceptance; a release may explicitly exclude an unverified optional runtime or database, but cannot label it supported.
- **P2**: stress, robustness, or optional behavior; record boundaries and exact exclusions.
- **U**: pure validator/mapper/schema tests, no workers.
- **W**: actual native child process or managed SQLite worker.
- **A**: real authenticated API against isolated persisted state, with workers as specified.
- **N**: actual pinned n8n invocation, graph, callbacks, workers, persisted results; no Python DAG substitute or mocked n8n.
- **D**: real database server and matching driver, using a disposable schema and explicitly permitted credentials.
- **F**: deterministic failure injection of only the named transport/process/storage boundary; business worker execution remains real.
- **M**: manual evidence where independent environment provisioning or observation is required.

Every result records case ID, commit, operating system/architecture, runtime/compiler/driver/database versions, graph/publication hash, run and node IDs, expected/actual result, and evidence location. Outcome is PASS, FAIL, BLOCKED_ENV (missing environment prerequisite), or NOT RUN. A BLOCKED_ENV/NOT RUN pair never counts as PASS. Redact credentials and callback tokens from evidence.

## 2. Fixed semantics for implementation and tests

| Decision | Fixed meaning and reason | Cases |
|---|---|---|
| S01 Edge versus mapping | An edge orders completion; it does not automatically inject any upstream fields. Removing a binding (or an explicitly supported `source:none`) intentionally omits that target field; it is not a missing required upstream value. A required target schema can still forbid that omission. A binding selects data. A node may depend on A and choose to ignore all A outputs. The editor may add a dependency when selecting a source, but API validation must reject a dangling binding. | MAP01–08 |
| S02 Optional input | `optional:true` requires an explicit typed `default` property, which may be null. Missing/skipped source data uses that default; present null/false/0/empty values remain unchanged. A failed source can only be tolerated through `edge.required:false`; any mapping referencing it must independently be optional with a default. Optional data alone never weakens a required dependency. | MAP09–12, BR07–11 |
| S03 Control flow | False condition makes an edge inactive; unselected paths emit skipped completion markers. Joins wait for all direct predecessor terminal markers. Only activated successful sources supply values; no automatic injection occurs without mapping/merge configuration. If all predecessors are skipped, the node is skipped. Nodes without incoming edges are roots and can run with empty inputs. | BR01–06, BR19 |
| S04 Join policy | `edge.required` defaults true. `join:all` requires all activated required predecessors to succeed; `join:any` requires at least one activated predecessor to succeed and never bypasses a failed required predecessor. Both wait for all predecessor terminal markers before deciding. Only `required:false` tolerates predecessor failure; missing required mapped data still blocks. Multiple incoming edges require explicit merge configuration. | BR04–12 |
| S05 Failure states | Failed necessary chains make workflow `failed`; blocked descendants use `not_run` with `blocked_by_failed_dependency`. Unselected paths use `skipped`. An explicitly configured optional dependency may let its consumer run using typed defaults or no reference; after such tolerated failure the workflow is `partial`, never `succeeded`. The failed source retains failed status. Whole-run cancellation/timeouts retain their own terminal priority. | BR07–12, FAIL01–05 |
| S06 Types and absence | No implicit string↔number, scalar↔array, null↔missing or bool↔integer conversion. Initial schemas support object/array/string/number/integer/boolean/null plus properties/items/required; nullable alternatives express present null explicitly. Unsupported validation keywords must be rejected rather than silently ignored. Logical decimal/date metadata explains portable strings. Empty arrays remain successful whole-array inputs, with no implicit fan-out. | DATA01–19 |
| S07 Paths | Canonical paths are token arrays relative to output.data: string tokens address exact object keys and nonnegative integer tokens address array indexes, e.g. `["rows",1,"id"]` and `["a.b"]`. Existing dot shorthand `rows.1.id` remains compatible for unambiguous paths. Literal-dot/numeric object keys use string tokens, no wildcards or negative indexes. A well-formed out-of-range index is missing data and follows required/default policy. Arrays transfer whole unless an explicit element path is chosen; never implicit per-row execution. | MAP13–17 |
| S08 Portable numeric/date values | Decimal and integer values beyond ±(2^53−1) are schema-marked strings, including driver conversion. Time values are timezone-qualified ISO strings. No NaN/Infinity or lossy automatic rounding. | DATA10–15 |
| S09 Output envelope | In helper mode Python/JS `main(inputs)` always returns plain business data; the SDK wraps it as `{schemaVersion:1,data:<return>,artifacts:[]}`. A returned business field named schemaVersion, data or artifacts does not select envelope mode. The separate explicit file-protocol entrypoint writes the envelope to SLEEP_IN_OUTPUT_FILE directly and validates version1/data-object/artifacts-array. Required output schema applies to data; absent file is accepted only when no required outputs exist. | OUT01–14 |
| S10 Artifacts | Downstream receives artifact identity and an authorized local materialization/reference, never an upstream absolute host path. Bytes and checksum are authoritative; preview truncation never changes downstream content. Canonical path resolution must reject symlink escape before access. | ART01–13 |
| S11 Retrying writes | Default node attempt count is one. Retry only after explicit policy and a known retry-safe boundary. After uncertain SQL commit/external side effect, mark failed/needs review without automatic replay. Full rerun is an explicit separate run. Exactly-once effects across arbitrary systems are not promised. | RET01–08, N13–16 |
| S12 SQL | Logical bindings use native driver parameters, never identifier/fragment interpolation. Query mode has database/connection read-only enforcement. Explicit write mode supports an ordered statements list executed in one node-local transaction: commit all on success, rollback all on failure subject to documented database implicit-commit behavior. Do not split raw SQL text on semicolons. A workflow never promises cross-node/database rollback. Stored procedure capabilities require separate tested support. | SQL01–25 |
| S13 Concurrent branches | Parallel edges authorize independence; they do not promise actual overlap. Show separate evidence for fan-out correctness versus measured simultaneous execution. A serialization result is acceptable for first-release correctness if documented; a concurrency claim needs measured overlap. | CON01–08 |
| S14 Publication | Freeze source, graph, input schema, runtime/dependency/build identity and non-secret parameters at admission. Resolve authorized credential revision at execution; record its identifier without the secret. Changing draft/source/runtime must not mutate historical or queued snapshots. | VER01–08 |
| S15 API errors | HTTP errors use `{detail:{code,message,node_id?,field?}}`; draft validation_errors use corresponding code/message/node_id?/field? objects. Node/field/source diagnostics must be actionable without disclosing secrets. | API01–03 |
| S16 Condition errors | Unknown operator, wrong value type and missing condition path are validation/runtime errors, not false. `truthy` accepts boolean only; true activates and false skips. Numeric/string comparison never coerces, and unsupported compound condition syntax is rejected. | BR13–17 |

## 3. Shared exact fixtures and oracles

All fixtures are synthetic and use a disposable managed location. No predecessor code, account, real endpoint, private document or user data is imported.

### F-ORDERS: three rows, stable order

```json
[
  {"order_id":"A001","amount":"10.50","region":"华东"},
  {"order_id":"A002","amount":"20.25","region":null},
  {"order_id":"A003","amount":"0.00","region":"西部"}
]
```

SQLite setup schema is `orders(order_id TEXT PRIMARY KEY NOT NULL, amount TEXT NOT NULL, region TEXT NULL)`; populate exactly the three records above. Query: `SELECT order_id, amount, region FROM orders ORDER BY order_id`. Other dialects use equivalent explicit text/decimal schema as appropriate and record the actual column type. Do not rely on unspecified row order.

SQL receiver expected data is `{rows:F-ORDERS,columns:[...],rowCount:3}`. The column descriptor oracle is three ordered names above, logical string type, and nullable only for `region`. Write-specific `affectedRows` is absent for a query or consistently zero if adopted in the final contract; choose one before writing snapshots.

Summary script's exact input is `{orders:F-ORDERS}`. It uses decimal arithmetic and returns `{"summary":{"count":3,"total":"30.75"}}`. Empty input returns `{"summary":{"count":0,"total":"0.00"}}`.

Report script's exact input is `{summary:{count:3,total:"30.75"}}`. Expected data is `{"message":"3 orders • 30.75"}`; artifact `report.txt` contains exactly UTF-8 bytes for `3 orders • 30.75\n`. One business invocation per node is measured by a dedicated per-attempt counter in isolated test storage; process/module import is not counted as a business call.

### SQL → Python 核心操作步骤（中文）

1. 在隔离的受管 SQLite 中建立上述三行数据，保存并发布 SQL→Python→JavaScript 图。使用真实 n8n 调度三个实际节点。
2. Python 输入面板配置目标字段 `orders`，来源选择 SQL 节点的 `rows`。路径相对于 `output.data`，不是 `output.data.rows` 字符串重复嵌套。
3. 运行后逐项检查 Python 的完整输入：必须恰好是 `{orders:[上述三行]}`；`region:null` 保留为 null。Python 只运行一次，不能按 SQL 行隐式循环三次。
4. Python 用 Decimal 求和，输出必须是 `{"summary":{"count":3,"total":"30.75"}}`。JavaScript 的 `report` 输入绑定 Python 的 `summary`，生成 `3 orders • 30.75`。
5. 改为 Python 不配置任何上游数据输入，仅保留连线并输出固定结果。此时 Python 输入为 `{}`，仍等待 SQL 成功，不应自动注入 rows。删除输入映射与缺失必填字段是两种不同情况。
6. 将 SQL 改为零行查询，再运行：Python 收到 `{orders:[]}` 并输出 count=0、total="0.00"，不是缺失输入，不是跳过 Python。
7. SQL 改为失败查询：即使 Python 只有常量输入，只要连线仍是必需依赖，Python 就不能运行。`optional:true` 的输入映射本身不能把这条依赖改成可忽略。

### F-TYPES: portable nested data

```json
{
  "text":"中文 / café / 🙂 / quote:\" / slash:\\ / newline:\n",
  "emptyText":"",
  "zero":0,
  "negative":-7,
  "fraction":1.25,
  "flag":false,
  "nil":null,
  "emptyArray":[],
  "emptyObject":{},
  "decimal":"9007199254740993.01",
  "largeInteger":"9007199254740993",
  "timestamp":"2026-09-17T07:30:00+08:00",
  "rows":[{"id":1,"value":null},{"id":2,"value":""}]
}
```

Object property ordering is not significant; array order, Unicode code points, all scalar values and null/absence are significant. Compare decoded JSON deeply, never a pretty-print screenshot. Schema annotates `decimal` and `largeInteger` as string logical types and `timestamp` as timezone-qualified date-time.

### F-PAIR: all ordered language pairs

Source `S` emits the F-ORDERS data above under `data.rows`. For SQL this is the ordered query; for each script language it is emitted through its actual native runtime/file protocol. Source output envelopes may contain columns/metadata; only declared paths are mapped.

For a **script target**, bind `orders ← S.rows`; target calculates the exact summary oracle above. For a **SQL target**, bind `id1 ← ["rows",0,"order_id"]`, `id2 ← ["rows",1,"order_id"]`, `id3 ← ["rows",2,"order_id"]` from S and query the same disposable table using three driver-bound parameters: `SELECT order_id,amount,region FROM orders WHERE order_id IN (:id1,:id2,:id3) ORDER BY order_id`, adapting placeholders via the driver. Expected target rows equal F-ORDERS and rowCount equals 3. SQL does not receive an arbitrary native array as a scalar SQL parameter.

Each pair runs with genuine processes and an actual n8n two-node graph, one source invocation, one target invocation, successful terminal callbacks, exact input/output inspection, and publication/runtime evidence. A second order-only variant leaves the target's mappings empty and produces its fixed independent sentinel (`{ignored:true}` for script, one-row `SELECT 1 AS ignored` dialect equivalent for SQL); it must succeed without consuming source output.

### F-BRANCH and F-EFFECT

F-BRANCH: A emits `{flag:true,amount:7}`; A→B condition flag eq true; A→C condition flag eq false; B emits `{label:"B",values:[1,2]}`; C emits `{label:"C",values:[3]}`; B/C→J explicit merge; J records exact named inputs/markers. Flip flag for the complementary case.

F-EFFECT: disposable effect table/file with one unique operation key `effect-001`; actions append or insert that key. Count effects independently from run statuses. Fault injection may disconnect after commit or kill a worker after insertion. Do not use external email, payments or production data.

## 4. First-use SQL → Python → JavaScript acceptance

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| GOLD01 | P0 | Managed SQLite F-ORDERS, real Python/Node/n8n ready; immutable three-node graph and mappings above | Publish; manually admit; execute actual graph; inspect all node records and artifact download | SQL rowCount 3; Python summary `{count:3,total:"30.75"}`; JS message `3 orders • 30.75`; report bytes exact; all 3 succeeded; workflow succeeded; each count 1 | N+W+A |
| GOLD02 | P0 | Same graph; SQL `WHERE 1=0` | Publish and run | SQL rows `[]`, rowCount 0; Python summary count 0/total `0.00`; JS message `0 orders • 0.00`; all nodes once and succeeded | N |
| GOLD03 | P0 | GOLD01; SQL returns 3 rows but Python expects one object containing `orders` | Inspect process input and invocation counter | Python input exactly `{orders:F-ORDERS}`, never three invocations or first-row-only input | N+W |
| GOLD04 | P0 | GOLD01; rename node display names and reorder draft node array | Save, publish new version, run | Same stable-ID mappings and exact output; actual dependency order SQL→Python→JS unaffected by array position | A+N |
| GOLD05 | P0 | GOLD01; report maps constant `{count:99,total:"1.00"}` and retains Python→JS ordering edge | Run | Python succeeds first; report receives the constant and emits `99 orders • 1.00`; no implicit summary injection or overwrite | N |
| GOLD06 | P0 | GOLD01; SQL same; Python has `inputs:{}` and returns `{summary:{count:42,total:"2.00"}}` | Run | Python input exactly `{}`; output is independent sentinel; JS receives summary and emits `42 orders • 2.00`; SQL result remains inspectable | N |
| GOLD07 | P0 | GOLD01; mapped source path `summary.missing` required | Publish if statically detectable; otherwise run with declared dynamic schema | Publication rejected with node/field/path; if unknowable at publish, report node fails input validation without starting its process; workflow failed | U+A+N |
| GOLD08 | P0 | GOLD01; Python prints `NOT JSON` and debug table to stdout | Run and inspect structured output/logs | Same exact report; stdout contains debug text but is never parsed as data | W+N |
| GOLD09 | P0 | GOLD01; change draft SQL to zero rows after admission but before SQL callback | Continue admitted execution | Admitted run still sees original F-ORDERS/source snapshot and reports 3; draft change does not mutate run | A+N+F |
| GOLD10 | P1 | GOLD01 plus scheduled form occurrence with stable operation key | Trigger same due occurrence twice through two scheduler ticks | One run and one effect per node; same exact GOLD01 data and real n8n evidence | A+N |

## 5. Complete ordered-pair plan

`PAIR-S-T` is the case ID; all 49 IDs below expand the F-PAIR steps/oracles without exceptions. Diagonal pairs are intentional. `S` means SQL/SQLite, `P` Python, `J` JavaScript, `H` Shell, `V` Java, `C` C, `X` C++. Every cell also has the order-only suffix `-O` using the order-only F-PAIR variant. Baseline pair priority is P0 for {S,P,J,H}×{S,P,J,H}; P1 for any pair containing V/C/X. Automation level for every pair is N+W; SQL is a real SQLite connection. Missing optional toolchains yield BLOCKED_ENV and an accurate unavailable UI status.

| Source \ Target | SQL | Python | JavaScript | Shell | Java | C | C++ |
|---|---|---|---|---|---|---|---|
| SQL | PAIR-S-S | PAIR-S-P | PAIR-S-J | PAIR-S-H | PAIR-S-V | PAIR-S-C | PAIR-S-X |
| Python | PAIR-P-S | PAIR-P-P | PAIR-P-J | PAIR-P-H | PAIR-P-V | PAIR-P-C | PAIR-P-X |
| JavaScript | PAIR-J-S | PAIR-J-P | PAIR-J-J | PAIR-J-H | PAIR-J-V | PAIR-J-C | PAIR-J-X |
| Shell | PAIR-H-S | PAIR-H-P | PAIR-H-J | PAIR-H-H | PAIR-H-V | PAIR-H-C | PAIR-H-X |
| Java | PAIR-V-S | PAIR-V-P | PAIR-V-J | PAIR-V-H | PAIR-V-V | PAIR-V-C | PAIR-V-X |
| C | PAIR-C-S | PAIR-C-P | PAIR-C-J | PAIR-C-H | PAIR-C-V | PAIR-C-C | PAIR-C-X |
| C++ | PAIR-X-S | PAIR-X-P | PAIR-X-J | PAIR-X-H | PAIR-X-V | PAIR-X-C | PAIR-X-X |

Additional parameterized pair suites (concrete input/steps and oracle fixed here):

| IDs | Pri | Preconditions / input | Steps | Exact expected | Level |
|---|---|---|---|---|---|
| PAIR-EMPTY-S-T (all 49) | As baseline | Replace F-ORDERS with `[]`; script summaries handle empty arrays; SQL target uses explicit zero-row query instead of nonexistent scalar bindings | Run source→target | Source rows `[]`; script target count 0/total `0.00`; SQL target rows `[]` and rowCount 0; no implicit invocation omission | N+W |
| PAIR-TYPES-S-T (36 script pairs) | As baseline | Every source/target among P/J/H/V/C/X; source emits F-TYPES; target echoes named `payload` | Run and deeply compare | Target output `{received:F-TYPES}` exactly, including strings, false, null, nested arrays and Unicode | N+W |
| PAIR-FAIL-S-T (all 49) | As baseline | Source fails before producing output; required source binding; target sentinel would append `effect-001` | Run | Source failed; target not_run with failed-dependency reason; effect count 0; workflow failed | N+W |
| PAIR-ART-S-T (36 script pairs) | P1 | Source writes binary bytes `00 ff 0a 41`; target receives authorized artifact reference and reads bytes | Run; inspect materialized paths and checksums | Same 4 bytes; output `{size:4,hex:"00ff0a41"}`; no cross-run path access | N+W+A |
| CHAIN01 | P1 | SQLite→Python→JS→Shell→Java→C→C++ with F-TYPES in script segment | Publish and run actual full chain | Deep equality at every script boundary; every logical node once; SQL bootstrap input preserved; no language silently substituted | N+W |

Shell roundtrips may use declared `jq` or another explicitly registered JSON utility. Do not count a shell wrapper that invokes Python for all parsing/transformation as independent Shell protocol proof without recording that dependency. Java/C/C++ must use a real compiler and declared JSON library or a constrained valid fixture implementation; never substitute a Python interpreter behind their labels. Full F-TYPES coverage requires correct JSON parsing/serialization, including escapes and nested data. An unsupported JSON utility dependency is BLOCKED_ENV, not a successful Shell/C/Java pair.

## 6. Mapping, ordering, and input validation

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| MAP01 | P0 | A→B, A returns `{x:7}`, B inputs `{}` | Execute; B echoes input | B receives `{}` and succeeds after A | U+N |
| MAP02 | P0 | A→B, B binds `v` to A.x | Execute | B receives `{v:7}`, no other fields | U+N |
| MAP03 | P0 | A,B disconnected; B binds A.x | Save then publish | Draft saves with error; publish fails `unreachable_source`; no run admitted | U+A |
| MAP04 | P0 | A→B→C, C binds A.x | Execute | C receives 7 after B terminal success; indirect ancestor binding allowed | U+N |
| MAP05 | P0 | A→B, A binds future B.x | Publish | Fails with source-not-upstream error; no worker launched | U+A |
| MAP06 | P0 | A→B→A | Publish | Cycle rejected with affected IDs; no n8n graph dispatched | U+A |
| MAP07 | P0 | A renamed, ID unchanged | Publish/run B with old ID binding | Binding succeeds, v=7 | U+A+N |
| MAP08 | P0 | Delete A without removing B's input binding | Save/publish | Draft preserves visible broken reference; publish fails missing source, rather than deleting mapping silently | U+A |
| MAP09 | P0 | A output `{x:null}`; B v optional default 9 | Execute | B v is null, not 9; nullable schema passes, integer-only schema fails | U+W |
| MAP10 | P0 | A output `{}`; B v optional default 9 | Execute | B input `{v:9}`, explicit default provenance recorded | U+N |
| MAP11 | P0 | A output `{}`; B v required | Execute | B process invocation count 0; input validation error references A.x and B.v | U+N |
| MAP12 | P0 | Optional binding omits `default` property | Publish | Reject ambiguity; `default:null` is accepted if target schema permits null | U+A |
| MAP13 | P1 | A data `{rows:[{id:1},{id:2}]}`; canonical path `["rows",1,"id"]` and shorthand `rows.1.id` | Map and execute both | v=2 integer in both; array remains whole when only `["rows"]` selected; no fan-out | U+W |
| MAP14 | P1 | Same; paths `rows.-1.id`, `rows.2.id` | Validate/map | Negative index rejected; out-of-range follows missing required/default policy, never Python negative-index behavior | U |
| MAP15 | P1 | A data `{"a.b":7,"a":{"b":8},"0":9}` | Map paths `["a.b"]`, `["a","b"]`, `["0"]` | Values7,8,9 respectively; string key0 differs from array index0; no literal-dot ambiguity | U+A |
| MAP16 | P1 | Path contains `__proto__`, `constructor`, or code text | Map into JS target object | Plain data property safely handled or explicitly rejected; no prototype mutation/expression evaluation | U+W |
| MAP17 | P0 | Binding `source:parameter,path:customer.name`; run params `{customer:{name:"Ada"}}` | Admit and run | Target exactly `{name:"Ada"}`; constants/params are separate from historic sample data | U+A+N |
| MAP18 | P0 | Constant `{a:[1,2]}` fan-out to B,C; B mutates local a[0] | Execute B,C | C still receives `{a:[1,2]}`; persisted input snapshot unchanged | W+N |
| MAP19 | P0 | B input schema requires number; A output string `"7"` | Publish with known schemas; also test dynamic source | Static incompatibility blocks publish; dynamic mismatch blocks B process; never automatic `7` coercion | U+A+N |
| MAP20 | P1 | Duplicate node A | Save copied node and bindings | Duplicate has new ID; source/config/inputs deep-copied; incoming edges copied; outgoing edges not copied; original consumers retain original source ID; edits to duplicate leave original unchanged | U+A |
| MAP21 | P0 | Graph saved with duplicate IDs or edge to nonexistent node | Publish | Error identifies duplicate/missing ID; no graph compilation | U+A |
| MAP22 | P1 | Two bindings attempt same target name | Import/publish | Reject duplicate target definition rather than last-write-wins data loss | U+A |
| MAP23 | P0 | Input source type credential/context | Author allowed reference; execute with authorized user | Only explicit supported references resolve; unauthorized/missing refs fail; unknown source type never defaults to constant | U+A+N |

## 7. Portable data and output envelopes

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| DATA01 | P0 | F-TYPES full payload | Script-pair echo | Deep equality across boundary; valid JSON only | W+N |
| DATA02 | P0 | `{rows:[]}` with schema array | Echo | `[]` remains `[]`; succeeded; process once | U+W |
| DATA03 | P0 | `{nil:null}` schema `type:["string","null"]` | Validate/echo | Pass; null preserved | U+W |
| DATA04 | P0 | `{}` schema requires `nil` | Validate | Missing required error; different from DATA03 | U |
| DATA05 | P0 | `0`, `false`, `""`, `[]`, `{}` | Map each as required value | None treated as absent or replaced by default | U+W |
| DATA06 | P0 | true for schema integer, false for number | Validate | Reject both; booleans are not numbers | U |
| DATA07 | P1 | Nested array items schema integer, input `[1,"2"]` | Validate | Error at index 1, no coercion | U |
| DATA08 | P1 | Object with undeclared `extra`, `additionalProperties:false` | Validate | Initial subset rejects unsupported additionalProperties keyword at publication rather than silently ignoring it; expanding schema support later requires its own tests | U+A |
| DATA09 | P1 | Enum `["a","b"]`, input `"c"`; min/max 0/10 input 11 | Validate | Initial subset rejects unsupported enum/range validation keywords at publication with field-level error; never silently accepts invalid constrained data | U+A |
| DATA10 | P0 | Decimal `"9007199254740993.01"` as marked string | Python→JS→Java echo | Exact text unchanged; no floating-point rounding | W+N |
| DATA11 | P0 | Large integer `"9007199254740993"` as marked string | All pair echo | Exact text; type string remains string | W+N |
| DATA12 | P0 | Unmarked JSON numeric 9007199254740993 destined for JS | Publish/runtime validation | Reject unsafe portability with actionable string-schema instruction; never silent 9007199254740992 | U+W |
| DATA13 | P0 | Output JSON NaN/Infinity/-Infinity | Parse/validate | Failed output validation, no downstream process | W+N |
| DATA14 | P1 | Timestamp `2026-09-17T07:30:00+08:00` | Echo | Exact offset-qualified string or documented UTC canonical equivalent representing 2026-09-16T23:30:00Z; no local-time drift | W+N |
| DATA15 | P1 | Timestamp `2026-09-17T07:30:00` missing zone | Validate date-time contract | Reject where timezone-qualified schema required | U+W |
| DATA16 | P1 | Dedicated dialect fixture has SQL NULL and empty string in separate text rows | Query then Python echo | null and empty string remain distinct for SQLite/Postgres/MySQL; record Oracle empty-string-is-NULL difference, do not claim identical behavior | D+W |
| DATA17 | P1 | Binary buffer/DataFrame/native Java object output | Produce via helper | Clear nonportable output error; recommend JSON or artifact, never opaque stringification | W |
| DATA18 | P1 | Unicode combining form and emoji from F-TYPES | Echo/download | Code points unchanged; byte encoding UTF-8; no replacement characters | W+N |
| DATA19 | P2 | 100-level nested object and huge property count within size limit | Validate under configured limits | Deterministic documented depth/count acceptance or explicit limit error; no crash/hang | U+W |
| OUT01 | P0 | Worker writes `{schemaVersion:1,data:{x:7},artifacts:[]}` | Execute | Succeeded with x=7 | W |
| OUT02 | P0 | Python/JS main returns `{x:7}` | Execute helper | Wrapped exact envelope v1/data x7/artifacts[] | W |
| OUT03 | P0 | Business data `{schemaVersion:42,label:"business"}` returned from main | Execute helper | Exact SDK envelope `{schemaVersion:1,data:{schemaVersion:42,label:"business"},artifacts:[]}`; returning an object never selects envelope mode | W |
| OUT04 | P0 | Exit 0, no output file, required output x | Execute | Failed output validation; downstream blocked | W+N |
| OUT05 | P0 | Exit 0, no file, no required outputs | Execute order-only node | Succeeded with empty data and artifacts; dependent order-only node allowed | W+N |
| OUT06 | P0 | Output file partial JSON `{` or invalid UTF-8 | Execute | Failed output parsing, logs/error preserved, no downstream process | W+N |
| OUT07 | P0 | Envelope data array/string/null | Execute | Failed: node data must be named object | W |
| OUT08 | P0 | Envelope missing version or unsupported version 2 | Execute | Clear protocol/version error; no silent interpretation | W |
| OUT09 | P1 | Artifacts is object instead of array | Execute | Failed envelope validation, no iteration over accidental strings | W |
| OUT10 | P0 | Worker emits valid output then exits 1 | Execute | Node failed despite valid file; downstream required path blocked | W+N |
| OUT11 | P0 | Worker logs JSON unrelated to output file | Execute | Structured result determined only by output file | W |
| OUT12 | P1 | Concurrent runs/attempts use same source and filenames | Execute twice isolated | Separate input/output/artifact directories; no stale prior-attempt file accepted | W+N |
| OUT13 | P0 | Python and JS explicit file-protocol entrypoints each write `{schemaVersion:1,data:{x:7},artifacts:[]}` to SLEEP_IN_OUTPUT_FILE | Execute in declared file-protocol mode, not helper main-return mode | Exact validated envelope retained; no second SDK wrapping or file overwrite | W |
| OUT14 | P0 | Helper main returns `{data:{x:7},artifacts:["business"],schemaVersion:2}` | Execute Python and JS helper mode | SDK output data is exactly that complete business object; protocol version remains1 and protocol artifacts[]; business keys never interpreted as envelope | W |

## 8. Complete data and artifact handling

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| ART01 | P0 | Report artifact from GOLD01 | Download through run API | Exact UTF-8 bytes, size/checksum match; authorized media type/name | W+A+N |
| ART02 | P0 | Structured payload serialized to exactly 1 MiB and 1 MiB+1 bytes (measure UTF-8 file bytes) | Return inline | Boundary accepted according to documented inclusive limit; +1 fails with artifact guidance; never silently truncates data | W |
| ART03 | P0 | 2 MiB complete dataset in node-local file, preview limited to first 100 rows | Downstream consumes dataset reference; UI fetches preview | Downstream sees all rows and checksum; preview metadata explicitly partial with total count; run succeeds | W+A+N |
| ART04 | P0 | Output artifact path `../../outside.txt` | Collect | Reject before opening outside file; node fails; no outside bytes in API/log | W+A |
| ART05 | P0 | Artifact symlink inside directory points outside | Collect/download | Reject symlink escape; no outside bytes exposed | W+A |
| ART06 | P1 | Symlink points to another node's artifact under same run | Collect | Reject cross-node access unless authorized materialization mechanism explicitly used | W+A |
| ART07 | P0 | Artifact declared but missing; worker exit 0 | Collect | Node failed, workflow cannot succeed with missing required artifact | W+N |
| ART08 | P1 | Two files same display name in different nodes | Collect/list/download by artifact ID | Unique IDs; correct bytes returned for each; display name never file authorization key | W+A |
| ART09 | P0 | Actor lacks workflow access; knows artifact/run ID | Request download | 403 or nondisclosing 404; never file bytes or absolute path | A |
| ART10 | P1 | 100 artifacts vs 101; total exactly 100 MiB vs +1 byte | Collect | Defined inclusive limits accepted; overage fails explicitly; cleanup policy recorded | W |
| ART11 | P1 | File modified after collection | Download/materialize | Detect checksum/version mismatch or serve immutable collected copy; never claim old checksum for changed bytes | W+A |
| ART12 | P0 | Artifact expired/deleted before rerun/resume | Request reuse | Explicit expired error; never replace with another run's similarly named file | A+N |
| ART13 | P1 | Artifact name includes newline, quote, Unicode, path separators | Download | Safe Content-Disposition filename; original safe display name retained; no header/path injection | A |

## 9. SQL receiver and real database gates

SQLite is mandatory for the first-use path. Each external dialect case below expands with suffix `-PG`, `-MY`, `-OR` for PostgreSQL, MySQL, Oracle; all are P1 until that dialect is claimed supported, then release-blocking for that claim. Required real prerequisites:

- PostgreSQL: reachable disposable PostgreSQL instance, server/version recorded, supported Python driver and version, separate read-only/read-write test roles, TLS endpoint/certificate when testing TLS, schema creation permission only for fixture preparation.
- MySQL: reachable disposable MySQL instance and declared storage engine/transaction settings, driver/version, read-only and write roles, character set/collation/SQL mode recorded. Do not generalize transactional behavior to nontransactional tables.
- Oracle: reachable disposable Oracle service, supported server/version/platform, `python-oracledb` actual Thin mode, service name and allowed authentication/TLS configuration; isolated schema with synthetic rows and read/write privileges. No bundled restricted client, wallet, Thick mode or older-server capability inferred from Thin tests. Oracle empty text/NULL and DDL implicit commits require explicit dialect expectations.
- No current real-database credentials or instances are assumed available. Missing prerequisites mark BLOCKED_ENV; mocks cover adapter translation only and cannot satisfy these rows.

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| SQL01 | P0 | SQLite F-ORDERS | Ordered query | rows F-ORDERS; ordered column metadata; rowCount 3 | W+N |
| SQL02 | P0 | Query bound order_id `A002` | Execute | Exactly second row, rowCount1 | W/D |
| SQL03 | P0 | Bound order_id `A001' OR 1=1 --` | Execute same SQL template | rows[]; table unchanged; no interpolation | W/D |
| SQL04 | P0 | Bound null with `WHERE region IS NULL`/dialect-appropriate null predicate | Execute | A002 selected; Oracle empty-string normalization is covered by a separate dedicated row in DATA16 | W/D |
| SQL05 | P0 | Missing named binding `order_id` | Validate/run | Input/bind error before SQL execution; no side effects | U+W/D |
| SQL06 | P0 | Identifier supplied as a value placeholder | Execute `SELECT ... FROM :table` | Clear unsupported identifier binding/database error; no raw concatenation fallback | W/D |
| SQL07 | P0 | Query mode; attempt INSERT/UPDATE/DELETE including CTE/tricky comment form | Execute using read-only enforcement | Database rejects writes; effect count0, regardless of first SQL token | W/D |
| SQL08 | P0 | SQLite query mode; PRAGMA/ATTACH/extension escape attempts | Execute in disposable connection | No writable external attachment, unsafe extension load, or escape from managed storage; record supported read-only statements | W |
| SQL09 | P0 | Write-capable connection and explicit write node inserts `effect-001` | Execute and read independently | Commit once; affectedRows1; effect count1 | W/D+N |
| SQL10 | P0 | Explicit write mode; statements list has parameterized INSERT effect-001 followed by a deliberately failing statement | Execute ordered list in one node transaction; read independently | Node failed; effect count0 after rollback; no raw-string splitting or intermediate commit | W/D |
| SQL11 | P1 | Node A commits; node B write fails | Run graph | A remains committed; B rolled back; workflow failed; no false cross-node rollback claim | W/D+N |
| SQL12 | P1 | Write mode with read-only credentials | Execute | Permission failure; no effect; UI does not report connection readiness as write permission | D |
| SQL13 | P1 | Slow cancellable query and timeout1s | Execute | timed_out; connection cancellation/rollback behavior proven per driver; downstream blocked | D+N |
| SQL14 | P1 | Read 20,000 rows, exceeds preview limit but within dataset policy | Run SQL→Python | Python exact count20,000 and checksum; preview paginated; no first-page-only downstream data | W/D+N |
| SQL15 | P1 | Decimal column `9007199254740993.01`, BIGINT beyond JS safe range | Query→JS | Schema-marked exact strings; no precision loss | D+N |
| SQL16 | P1 | Nulls, Unicode, date/time and timezone-aware columns | Query→Python/JS | Documented portable logical types; values match fixture including timezone rules | D+N |
| SQL17 | P1 | Connection password changed between publish and execution | Run with authorized new revision | Records credential revision; new credentials used; no secret in graph/run/export/log; stale credential failure actionable | D+A |
| SQL18 | P0 | Connection allowed only for workflow A; B references it | Publish/run B | Authorization error, no database connect attempt | A+F |
| SQL19 | P1 | PostgreSQL/MySQL/Oracle driver absent | Inspect runtime/connection and publish node | Explicit unavailable/blocked status, never generic SQL ready presented as that dialect ready | A+W |
| SQL20 | P1 | TLS certificate invalid/wrong host | Test connection | Failure; no silent disable-verification fallback | D |
| SQL21 | P1 | Database disconnect before statement vs after uncertain commit | Inject separately | Before statement: safe failure/retry if explicit. After uncertain commit: no automatic replay; operation remains one or unknown with review reason | D+F+N |
| SQL22 | P1 | Oracle Thin query, bind, null, insert/update rollback/commit | Execute actual F-ORDERS Oracle variant and SQL02/04/09/10-OR | Real recorded Oracle Thin versions/results; no claim about wallets/Thick/other platforms | D+M |
| SQL23 | P1 | Oracle DDL followed by intentional failure | Execute only in disposable schema if DDL allowed | Explicit documented implicit commit outcome; never promise full transaction rollback of DDL | D+M |
| SQL24 | P0 | Managed SQLite path config attempts absolute user file/relative traversal | Create/test connection | Reject unmanaged path; no arbitrary host database opened | A+W |
| SQL25 | P0 | Explicit write mode, raw SQL contains two semicolon-separated INSERT statements instead of statements list; separate valid single statement has literal text `a;b` | Validate/execute each variant | Raw multi-statement input rejected before effects; single bound/literal semicolon value executes unchanged once; no naive string splitting | U+W/D |

External-dialect pair expansion: run SQL→each of the six scripts and each script→SQL for PG/MY/OR (12 directions × 3 dialects), plus PG→MY, PG→OR, MY→PG, MY→OR, OR→PG, OR→MY with explicit separate connections. These supplement, not replace, the 49 SQLite/language pairs. Exact input and target oracle remain F-PAIR with documented driver normalization. A dialect cannot be marked complete from only `SELECT 1`.

## 10. Conditions, joins and failure propagation

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| BR01 | P0 | F-BRANCH flag true | Run | A/B/J succeeded; C skipped; J named sources contains B only and C skipped marker; workflow succeeded; each executes at most once | N |
| BR02 | P0 | F-BRANCH flag false | Run | A/C/J succeeded; B skipped; J contains C only; no deadlock | N |
| BR03 | P0 | Both outgoing conditions false; workflow has no separate required final-output contract | Run | B,C,J skipped; A succeeded; workflow succeeded; no skipped branch treated as failed | N |
| BR04 | P0 | Both branches activated and succeeded; explicit named merge | Run | J waits both markers; input preserves `B:{label:"B",values:[1,2]}` and `C:{label:"C",values:[3]}` without key overwrite/row joins | N |
| BR05 | P0 | Append merge configured on B.values and C.values | Run with reversed completion orders | J receives `[1,2,3]` in stable declared source order both times, not completion order | N+F |
| BR06 | P0 | Two incoming edges but config.join omitted | Publish | Reject with node-level explicit-merge requirement | U+A |
| BR07 | P0 | A→B required; A fails; B has only constants | Run | B not_run `blocked_by_failed_dependency`; constant inputs do not override required ordering failure; workflow failed | N |
| BR08 | P0 | Two activated required sources, B fails and C succeeds | Run join all | J not_run, workflow failed; no missing data disguised as success | N |
| BR09 | P0 | B→J edge.required=false; B fails; J B-field optional default[]; C→J required and succeeds; join all | Run; inspect J inputs and all states | J receives B default[] plus mapped C; J succeeded; B remains failed; workflow partial; never succeeded | U+N |
| BR10 | P0 | B unselected/skipped; B→J required=false; J B-field optional default0; C succeeds and activates J | Run | J receives0 and C data; B remains skipped; no failure means workflow succeeded; no deadlock | N |
| BR11 | P0 | join any; B fails, C succeeds; test B.required=true then false; B-field optional default[] | Run both variants, hold C until B terminal to inspect waiting | Required=true: J not_run and workflow failed despite default. Required=false: J waits both markers, succeeds with default[] plus C, workflow partial | N+F |
| BR12 | P0 | join:any, B and C both fail | Run | J not_run, workflow failed; never success with empty unacknowledged inputs | N |
| BR13 | P0 | Condition x gt 7 and x values 6/7/8 | Run three variants | Inactive/inactive/active respectively | U+N |
| BR14 | P0 | Condition operator unknown or compare number7 to string"7" | Publish/run | Validation/type error; no language-dependent coercion | U+A+N |
| BR15 | P0 | Condition path absent | Run | Source/edge condition error recorded, downstream blocked; not quietly unselected | U+N |
| BR16 | P1 | Condition truthy with booleans true/false | Run | Active/inactive respectively; null/string/object/0/1 rejected as type errors, never converted to false | U+N |
| BR17 | P1 | Nested conditions needed AND/OR but only single predicate supported | Save/publish | Unsupported structure flagged; do not accept and ignore extra conditions | U+A |
| BR18 | P0 | Nested diamond A→B/C→J→D/E→K with different selected paths | Run all four selections | Every reachable join terminates; only activated steps run; no duplicate invocations | N |
| BR19 | P1 | Disconnected roots A/B both feed J | Run | Both roots admitted once; J waits both and receives named outputs | N |
| BR20 | P1 | One branch cancelled while another finishes | Cancel entire run | Join never starts business work after cancel; workflow cancelled only after workers stop | N+F |

## 11. Failure, retries, cancellation, and concurrency

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| FAIL01 | P0 | Worker exit1 before output | Run | Node failed, exit code/log/error preserved; required descendants not_run; run failed | W+N |
| FAIL02 | P0 | Worker exit0 but invalid required output | Run | Same failed propagation; success exit code is insufficient | W+N |
| FAIL03 | P0 | Python exception/JS rejected async Promise | Run | failed with stderr traceback/message; no unhandled service crash | W+N |
| FAIL04 | P0 | C/Java compile error at publication | Publish | No new published version; old publication remains usable; node build log accessible | W+A |
| FAIL05 | P0 | Node timeout1s, process sleeps10s and spawns child | Run | Node timed_out, descendant process stopped, run timed_out or explicit failure policy; no later output accepted | W+N |
| FAIL06 | P0 | Workflow timeout2s across individually long-allowed nodes | Run | Admission deadline enforced across graph; cancellation cascade; no node starts after deadline | N |
| FAIL07 | P1 | Worker output file unwritable/disk full injection | Run | failed collection/write with actionable reason, not succeeded; partial evidence retained safely | W+F |
| FAIL08 | P1 | Output/log growth beyond quota | Run | Process stopped or logs bounded per declared policy; no unbounded disk growth; truncation marked and structured data not truncated | W |
| RET01 | P0 | Failure default policy, F-EFFECT counter | Run | Exactly1 attempt and at most1 effect; no hidden n8n/HTTP retry | N |
| RET02 | P1 | Explicit maxAttempts3, retry-safe transient failure twice then success | Run | Attempt records1 failed/2 failed/3 succeeded with distinct logs/times; one accepted final output; downstream once | W+N+F |
| RET03 | P1 | maxAttempts3 but permanent schema error | Run | No retry for deterministic validation/build error; clear policy reason | U+N |
| RET04 | P0 | HTTP callback response lost after node successfully completes | Repeat authenticated callback with same node/attempt identity | Returns persisted result; no second business invocation/effect | A+N+F |
| RET05 | P0 | Side effect commits; worker dies before success record | Restart/retry dispatch | Never automatic replay of uncertain effect; run failed/needs review, effect count1; uncertainty visible | W/D+N+F |
| RET06 | P1 | Retry allowed, first attempt leaves stale output file | Run successful second attempt | Separate attempt dirs; old output not read as second result; attempts inspectable | W+N |
| RET07 | P0 | User full rerun of failed run | Admit explicit new run | New run ID with pinned version policy, fresh input snapshot and effect key policy; original remains immutable | A+N |
| RET08 | P1 | API requests unsupported resume-from-node | Request | Explicit unsupported error; never silently full-runs upstream write nodes | A |
| CAN01 | P0 | Queued run no nodes started | Cancel then dispatch callback | Run cancelled; invocation/effect counts0 | A+N |
| CAN02 | P0 | Long-running node with child process | Cancel during execution | cancelling while processes live; cancelled only after termination; no downstream starts | W+N |
| CAN03 | P0 | Node writes valid output just as cancel requested | Race repeatedly with barrier | One consistent terminal state; cancellation cannot become success after confirmed stop; no duplicate output/effect | W+N+F |
| CAN04 | P1 | Noncancellable external query already committed | Cancel | Report boundary accurately; cancellation does not claim rollback; no retry | D+N |
| CON01 | P0 | Two independent branches read same A.rows; B mutates its local input | Execute | C and persisted A.rows unchanged; copy semantics verified | W+N |
| CON02 | P1 | Independent B/C each sleep2s with monotonic start/end evidence | Run actual n8n | Record actual overlap or serialization; if advertised concurrency, intervals overlap and total shorter than serial baseline | N |
| CON03 | P0 | 20 simultaneous admission requests same idempotency key | Send concurrently | One run ID, one n8n graph, one effect per node | A+N |
| CON04 | P0 | 20 duplicate callbacks for same node | Send concurrently | One business invocation; others observe running/final result, never start another process | A+W+F |
| CON05 | P0 | Two scheduler processes dispatch same queued record | Race claim | Exactly one owning lease/n8n execution; second records/no-ops duplicate | A+N+F |
| CON06 | P1 | Parallel runs same workflow allowed only explicit bounded queue | Admit >limit | Default overlapping trigger skipped with reason; queue policy bounded/sequential and preserves parameter snapshots | A+N |
| CON07 | P1 | Different runs artifact same filename | Execute concurrently | Isolation by run/node/attempt; exact bytes never cross-contaminate | W+N |
| CON08 | P2 | 10,25,50-node chain/diamond graph | Execute and record timings/resources | Correct counts/results; bounded resource use measured; no scale/concurrency claim from graph compilation alone | N |

## 12. Real n8n adapter and publication boundaries

| ID | Pri | Preconditions / concrete input | Steps | Exact expected output/status | Level |
|---|---|---|---|---|---|
| N01 | P0 | Pinned n8n installed, GOLD01 publication | Inspect emitted graph; invoke actual supported import+execute CLI; run | Separate graph nodes and real connections reflect SQL→Python→JS; n8n callback trace and node outputs prove orchestration | N |
| N02 | P0 | Remove n8n command/config | Admit run | Actionable unavailable error/failed run; never execute hidden Python DAG while labeling engine n8n | A+N |
| N03 | P0 | n8n CLI returns exit0 but final callback absent | Inject missing/failed final callback | Run never marked succeeded solely from exit0; failure/timeout and adapter log recorded | N+F |
| N04 | P0 | Multi-input J compiled graph | Inspect and execute BR04 | Real n8n barrier accepts all completion markers; worker J invoked once, not once per merged item | N |
| N05 | P0 | Condition false/skipped node | Execute BR01/02 | Skipped completion marker traverses graph to merge; no deadlock or implicit success output | N |
| N06 | P0 | Missing/wrong dispatch authentication | Call internal node/finish/tick endpoints | 401/403; no node/run/effect mutation | A |
| N07 | P0 | Valid token but wrong run/node/version or expired execution capability | Call callback | Reject identity mismatch or return safe terminal result; never execute a different graph's source | A+N |
| N08 | P0 | Finish callback before required nodes terminal | Call early | No success; reject/defer with pending list | A+N |
| N09 | P0 | Duplicate finish callback after terminal success | Call twice | Same terminal state; no notifications or artifact collection duplication | A+N |
| N10 | P1 | Two runs plus an unrelated preexisting n8n installation | Run concurrently isolated | Distinct n8n user dirs/database/ports; no modifications to existing n8n state; no duplicate task brokers | N+M |
| N11 | P0 | Adapter import fails due invalid graph/schema | Execute | Run failed with meaningful import log; no false queued indefinitely/success | N+F |
| N12 | P0 | Worker unavailable before HTTP node callback | Run | n8n failure recorded; no node falsely succeeded; workflow fails/times out within deadline | N+F |
| N13 | P0 | Kill adapter/n8n after worker effect committed, before finish | Restart service | Interrupted/uncertain state preserved; no automatic repeat of effect; later scheduled future occurrences still possible | N+F |
| N14 | P0 | Kill web process while n8n is running | Restart | Leases reconciled with explicit terminal/uncertain state, no duplicate graph dispatch | N+F |
| N15 | P1 | n8n callback network timeout before worker starts | Retry according explicit safe callback policy | At most one admitted node attempt; final effect count1 or0 with clear failure, never2 | N+F |
| N16 | P0 | Change run ID or source through user-supplied callback payload | Submit malicious payload | Snapshot source chosen server-side; payload cannot replace source/version/runtime | A+N |
| VER01 | P0 | Published V1 then edit source draft | Run V1 | Exact original source/output/build; draft remains independent | A+N |
| VER02 | P0 | Admit V1, publish V2 before first callback | Continue run | Run stays V1; next default run uses V2; hashes/history intact | A+N |
| VER03 | P0 | Runtime/compiler/dependency update after publication | Execute old/new publication | Old identity remains pinned; if unavailable, old run blocked with clear missing-runtime error rather than silently rebased | W+A+N |
| VER04 | P1 | Same source, compiler upgraded in same executable path | Publish | Build cache invalidated by version/digest/flags/dependencies/architecture, not path alone | W+A |
| VER05 | P0 | Test unsaved/incomplete draft with explicit input | Admit test | Immutable test snapshot; marked test; formal notifications off; does not change publication | A+N |
| VER06 | P0 | Node test with historical sample | Execute only selected node | Upstream invocation counts0; sample provenance/staleness visible; no hidden live reads | A+W |
| VER07 | P0 | Operator attempts edit/publish or unavailable runtime override | Call author APIs | 403; cannot change source, runtime or secrets with run permission | A |
| VER08 | P1 | Export/import workflow with connections/secret refs | Export then import into fresh state | No secret values/endpoints/history; runtime/connection rebinding required; triggers disabled until validated | A |
| API01 | P0 | Invalid mapping publication | Call publish | Structured `{detail:{code,message}}`, suitable status; includes actionable node/field information | A |
| API02 | P0 | Valid async admission | POST run then GET status | 202 with persisted run ID means accepted only; UI initially queued/running, terminal success only after N08 prerequisites | A+N |
| API03 | P0 | Unauthenticated or missing-CSRF mutation | Create/edit/publish/run/cancel | 401/403 with no mutation; artifact reads also authenticated | A |

## 13. Suggested execution sequence and coverage exit gates

1. Implement the fixed S01–S16 semantics and C01–C12 canonical contract, including required=false optional edges, partial status, tokenized paths, helper/file envelope separation, and the explicit initial schema subset. Do not narrow tests to match incomplete implementation.
2. Implement U-level semantics; then real SQLite/Python/JS/Shell W tests and GOLD01–09; then actual n8n N01–09 with persistence and security checks. All P0 first-use/integrity cases must pass before a positive product demo claim.
3. Run all baseline/empty/order-only pairs for available runtimes. Java/C/C++ require actual compile/run plus per-version evidence; unavailable packs block publication. Do not silently count their cells as skipped passes.
4. Add branch/merge, cancellation, retry and duplicate/failure injection; verify side effects independently. Run scheduler admission intersection only after actual graph execution works.
5. Provision external databases explicitly and run their full dialect suites and directed pair expansion. Publish a matrix of exact verified server/driver/runtime/platform tuples; list BLOCKED_ENV combinations.
6. Keep separate test results for functional correctness, throughput/concurrency, sandbox/resource enforcement, and real Mac power/lockscreen behavior. None implies another.

Minimum report format: `Case ID | outcome | actual versions | run/publication IDs | exact assertion/evidence | limitation`. Release notes list remaining BLOCKED_ENV/NOT RUN cases relevant to advertised capabilities.

## 14. Current partial code observations to target, not verified conclusions

At design time, `taskconsole/workflows.py` was absent. `tests/test_workflows.py` contained four intentionally red seed tests. `workflows_runtime.py` and a form-schedule calculator had been newly written but not passed through their acceptance matrix. The following are concrete review targets, not claims of completed bugs or fixes:

- Schema helper currently recognizes a small subset and scalar `type`; nullable union, enum, ranges, additionalProperties and safe-integer rules need explicit supported-subset validation or implementation before corresponding acceptance claims.
- Python/JS wrapper currently guesses envelope shape from presence of `schemaVersion`; OUT03 prevents collision with ordinary business data.
- Artifact code currently resolves a path before checking `is_symlink`; ART05/06 must verify actual escape protection and no unexpected follow-before-check behavior. Recorded absolute artifact paths must not leak as downstream or public API contract.
- Native build cache currently includes source/compiler path/main-class but not full compiler version/dependency/flags/architecture identity; VER04 guards against stale binaries.
- Generic SQL runtime readiness currently reflects SQLite availability only; SQL19 prevents implying external drivers/servers were verified.
- Runtime JavaScript default locates `node` in PATH; a configured native pack must declare the actual executable and self-test it. Java launchers on macOS may be OS stubs without a JDK; path existence alone is insufficient.
- Per-node direct execution tests, when eventually passing, do not satisfy N01 or GOLD01 actual n8n orchestration evidence.

Only this test-design document is being changed during the test-design pause. Feature implementation and automated test additions remain paused pending the parent task's next instruction.
