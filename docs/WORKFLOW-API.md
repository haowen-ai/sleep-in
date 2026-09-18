# Workflow API v2

All `/api` routes use the existing session cookie and CSRF header. Administrators author sources, connections and publications. Authenticated operators may inspect/run published workflows. JSON collections are returned as arrays. Errors use `{detail:{code,message,node_id?,field?}}`; draft validation uses `{code,message,node_id?,field?}` objects.

`GET /api/workflows` → workflow array. `POST /api/workflows` and `PUT /api/workflows/{id}` accept `{name,description,nodes,edges,params,schedule,timezone,enabled,timeout}` and return the saved workflow with `id`, `published_version_id`, `validation_errors`. `GET /api/workflows/{id}` returns one. Saving never publishes; incomplete drafts are accepted, while enabling requires a publication.

Node shape: `{id,name,kind,source,config,inputs,outputs,position}`. Kinds: `sql,python,javascript,shell,java,c,cpp`. `config` includes `dialect`, `connection_id`, `runtime_id`, `join` (`all` default or `any`), `merge` (`named` default or `append`), `timeout`. `outputs` is a JSON Schema for the produced `data` object (optional). Input fields map to `{source:"constant",value:any}`, `{source:"parameter",path:"field.subfield"}`, or `{source:"node",node_id:"stable-id",path:"rows",optional:false,default:null}`. Input mapping may declare `type` (JSON primitive/object/array). References require an upstream edge path. Multiple incoming edges are explicit merges and require `config.join`.

Edges: `{source,target,condition?}`. Conditions compare source output with `{path:"summary.count",operator:"eq|ne|gt|gte|lt|lte|truthy",value:any}`. False conditions emit skipped completion markers. A node runs after all predecessor markers arrive, with `join:any` accepting any successful activated predecessor. Missing required mappings fail; optional mappings use explicit defaults.

`POST /api/workflows/{id}/publish` → `{version_id,version,workflow}`; validates and freezes graph plus compiler outputs. `POST .../run` accepts `{params:{},test:false,idempotency_key?:string}` → HTTP 202 run. `test:true` uses immutable draft snapshot and needs administrator. `POST .../preview` accepts optional `{schedule,timezone,after}` → `{next_runs:[ISO],timezone}`. Schedule kinds: manual; interval `{every:positiveInt,unit:"minutes|hours",anchor:ISO}`; daily/weekdays `{time:"HH:MM"}`; weekly adds `weekdays:[0..6]`; monthly adds `day:1..31|"last"`; once `{date:"YYYY-MM-DD",time:"HH:MM"}`. All support `start`, `end`, and daily/weekly/monthly may use `times:["HH:MM"]`. Cron is rejected.

`GET /api/workflow-runs?workflow_id=` → run array. `GET /api/workflow-runs/{id}` → `{id,workflow_id,version_id,status,params,nodes:{[node_id]:{status,inputs,output,stdout,stderr,error,attempts,started_at,finished_at}},artifacts,created_at,started_at,finished_at,error,engine:"n8n"}`. Status queued/running/succeeded/failed/cancelling/cancelled/timed_out. `POST .../cancel` requests cancellation. `GET .../artifacts/{artifact_id}` downloads authorized files. One script invocation consumes one input object; stdout is only a log. Node outputs are `{schemaVersion:1,data:{},artifacts:[{name,path,mediaType}]}`; files must reside inside `SLEEP_IN_ARTIFACT_DIR`.

`GET /api/workflow-templates` → `[{id,name,description,nodes,edges,params,...}]`; instantiate by posting a template object to `/api/workflows`.

`GET /api/runtimes` → `[{id,language,name,status,executable,version,reason,capabilities}]`. Ready reflects detected toolchain; publication performs compiled-language builds. Native processes do not enforce container CPU/memory isolation.

`GET /api/connections` → metadata array (never secrets). `POST /api/connections` accepts `{name,dialect,config,write_enabled:false}`; SQLite is managed synthetic storage (`config:{synthetic:true}`). External config is encrypted. `POST /api/connections/{id}/test` performs a read-only connectivity query and returns `{ok,message}`.

Integration: `register_workflow_routes(app,store,require)` before the catch-all route. `WorkflowService(store).tick()` admits schedules and `dispatch_pending()` starts actual n8n processes. Set `SLEEP_IN_N8N_COMMAND` to a JSON argv array (e.g. `["/path/node","/path/n8n/bin/n8n"]`) and `SLEEP_IN_BASE_URL` to the loopback app URL. n8n CLI executes exported graphs whose HTTP nodes call authenticated per-node endpoints. No Python topological executor is substituted for n8n. Missing n8n blocks execution with a recorded actionable failure. Internal calls use the store's dispatch token; each node admission is idempotent.

## Test-first contract

[The test plan](TEST-PLAN.md) and [contract decisions](testing/contract-decisions.md) fix dependency-vs-input, missing-vs-null, optional dependency, join, schema and duplication behavior. This draft API does not yet cover the full PRD: multiple trigger CRUD, immutable runtime preparation, node testing, notifications and import/export require explicit interfaces and failing tests before implementation. Those requirements are not dropped.

## Implemented core contract updates (fixed test decisions)

HTTP failures use `{detail:{code,message,node_id?,field?}}`. Draft errors carry the same structured fields. Node `config.entry_mode` is `function` (default for Python/JavaScript) or `file`. Function mode always wraps the plain object returned by `main(inputs)` as envelope data; a business property named `schemaVersion` never changes interpretation. File mode executes the source directly and expects the author to write the complete version-1 envelope to `SLEEP_IN_OUTPUT_FILE`. The template's JavaScript field is `summary`, mapped from Python's `summary`.

Canonical binding paths are token arrays relative to `output.data`, e.g. `["rows",1,"order_id"]` or `["literal.dot.key"]`; unambiguous dot shorthand remains accepted. `optional:true` requires explicit typed `default`; present null is preserved. `source:none` omits a binding; connected nodes with `inputs:{}` receive exactly `{}`. `context` supports `run_id`, `workflow_id`, `version_id`. Credential-reference and artifact-materialization mappings are not yet implemented by this core API.

Edges separately accept `required` (default true). A failed required predecessor blocks both all/any joins. An optional failed predecessor can be tolerated with independently optional/defaulted data bindings; workflow outcome becomes `partial`. Normal unselected/skipped branches do not introduce failure. All/any both wait for every predecessor's terminal marker. Each logical worker executes once regardless of how many n8n merge items arrive. A known declared output/input type mismatch blocks publication. Schema support is type (including nullable unions), properties/items/required plus title/description/metadata; unknown validation keywords are rejected.

Native run details include each node's historical `name` and `kind` but no source or callback capability. `partial` is an additional workflow status. Run admission first resolves an idempotency key; a different key while the workflow is active returns HTTP 409 `workflow_active`. The initial policy has no bounded queue. Test admission validates/builds outside the store transaction and rechecks the draft revision before freezing a snapshot.

### Named scheduled triggers

Workflow create/update accepts `triggers:[{id,name,kind:"scheduled",params:{},schedule:{...},timezone:"America/Chicago",enabled:false,version_id:null}]`. IDs are unique stable strings. Null/omitted `version_id` follows the current publication; a supplied ID must belong to this workflow. Scheduled enabled triggers require publication and a valid form schedule/timezone. Save an initially disabled trigger, publish, then enable it with `PUT /api/workflows/{id}`. The author route requires administrator access; a dedicated operator trigger-only update API is not yet implemented. Manual/API trigger types may be stored but named-trigger invocation is not yet exposed; `/run` remains authenticated manual/API admission to the publication selected in the request. Do not show unavailable named-trigger actions as working.

The top-level schedule/timezone/enabled fields remain a compatibility trigger with ID `default`; named triggers are independent. An occurrence key includes workflow ID, trigger ID and scheduled timestamp. Active-workflow overlaps are recorded as skipped events. Worker recovery does not replay uncertain active executions. On worker startup, interrupted in-flight records fail with an explicit uncertain-effects message requiring an intentional rerun.

### Actual n8n process adapter

The supported CLI path imports a generated per-run graph with `import:workflow --input=...`, then executes its ID with `execute --id=...`. `execute --file` is not used. Each run has isolated n8n storage and a separate loopback task-broker port. The graph contains HTTP callback nodes plus native n8n merge barriers; there is no Python DAG execution fallback. Authenticated final persisted state, not CLI exit status, determines success. The run records the actual n8n execution ID when available.

Worker: `python -m taskconsole.workflows worker`; state is `APP_STATE_DIR` (`STATE_DIR` fallback), with `DATABASE_URL`, `SLEEP_IN_BASE_URL` and `SLEEP_IN_N8N_COMMAND` JSON argv. `SLEEP_IN_NODE` selects Node for JS workers. A filesystem lock prevents duplicate workflow workers. Heartbeat is meta record `workflow_worker`, also readable through authenticated `GET /api/workflow-health`. Internal scheduler endpoint is `POST /internal/workflow-tick` with the existing dispatch token in `x-dispatch-token`. Run-specific node/finish callbacks use a separate execution capability and are not user APIs. `local-stop-request.json` blocks new admission/ticks but permits already queued runs to drain.

SQLite uses managed storage and the synthetic connection. Optional external drivers are reported separately from tested connection readiness; SQL read-only and write behavior require actual dialect evidence. Native scripts have time/output limits and process-group termination, but no container CPU/memory isolation. Java requires a working JDK; an OS launcher stub is unavailable. Complete dependency-locked runtime packs, automatic runtime installation, data-set spill/materialization, retries, retention, notifications, import/export and source-project packaging are separate work and are not implied by these core endpoints.

Array append merge is now explicit: `config.merge:"append"` concatenates the mapped array values in the input-definition order into `{items:[...]}` (override the key with `config.merge_target`). Every mapped value must be an array; a non-array fails before the process starts. Named mode preserves the named input object. Completion order never chooses append order.

### Portable workflow template transfer

`GET /api/workflows/{wid}/export` is administrator-only and returns a JSON template, not an execution backup:

```json
{
  "schemaVersion": 1,
  "workflow": {"name": "Example", "nodes": [], "edges": [], "params": {}, "timezone": "UTC"},
  "requirements": {
    "connections": [{"key": "connection-1", "name": "SQL connection 1", "dialect": "postgresql", "node_ids": ["query"], "write_required": false}],
    "runtimes": [{"language": "python", "node_ids": ["transform"]}]
  },
  "review_notice": {"user_source_and_literals_included": true, "message": "Review user-authored source and literals before sharing."}
}
```

The example illustrates the envelope; valid templates contain 1–50 nodes. Export retains names/descriptions, source, schemas, positions, mappings, edges, parameters, timezone and timeout. Node config is an explicit allowlist: dialect, query/write mode, entry mode, Java main class, join/merge settings, timeout and SQL statements/bindings. A SQL `connection_id` becomes a generic `connection_requirement`; existing connection names, IDs, endpoints and credential configuration are never copied. Runtime IDs, executables, build paths, callbacks, publication IDs, triggers, active state, samples and run history are excluded. Connection requirements use generated names, not private instance connection labels.

Source text, arbitrary schemas, parameters, constants/defaults, condition values and literal SQL bindings are **user-authored content and are included verbatim**. The `review_notice` states this explicitly. The exporter does not claim to detect arbitrary embedded passwords, hostnames or other private literals in code or business data. Review and remove such literals before sharing.

`POST /api/workflow-import` accepts exactly `{template: <the above object>}` and requires administrator permission plus the existing CSRF header. It checks schema version, finite JSON, a 1 MiB encoded size limit, node/edge/mapping shapes, unique node IDs and valid references. Unsupported structural fields such as `_runtime`, `_build`, callback tokens, connection/runtime IDs, published-version IDs and active triggers are rejected with HTTP 422 `invalid_template`; no draft is created on rejection.

Successful import returns a **new disabled draft** with a new workflow ID, preserved graph-local node IDs, no publication/history, a manual schedule and no active triggers. Connection/runtime bindings and execution configuration paths are not restored. `import_requirements` records the portable requirements and `requires_rebinding:true` signals that environments/connections need review. SQL nodes remain unpublishable until a connection is explicitly selected. Runtime readiness and a new publication use the normal publication validation path; the flag is advisory metadata, not a claim that a separate runtime-selection policy is enforced. Import neither executes code nor creates connection records.

Integration: `register_transfer_routes(app, store, require)` is mounted before the static catch-all. Tests use only synthetic connection/credential canaries and temporary state; no real user workflow was exported during implementation.

## Completed execution extensions

See [Execution API](EXECUTION-API.md) for isolated node tests, historical samples, complete dataset pagination/artifact bindings, explicit safe retries, asynchronous n8n submit/status/wait groups, credential references, named API triggers, overlap/missed-occurrence policies, run naming and process-identity recovery. These are separate contracts from whole-workflow testing and do not implicitly replay upstream work.
