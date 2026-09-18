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
