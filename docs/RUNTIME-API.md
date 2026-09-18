# Runtime profiles and source projects API

Implementation contract for the complete-workflows branch. All routes use existing session authentication and CSRF rules. Listing is authenticated; profile/source/toolchain creation/build/install is administrator-only. HTTP errors use `{detail:{code,message}}`. Collections are raw arrays.

## Profiles and immutable versions

`GET /api/runtime-profiles` returns profiles `{id,name,language,versions:[{id,number,status,created_at,platform,architecture,digest,build_log,dependency_summary,verification,referencing_workflows}],latest_version_id}`. Languages: python/javascript/shell/java/c/cpp/custom. Version states are `draft`, `building`, `ready`, `failed`.

`POST /api/runtime-profiles` accepts `{name,language,config}` and creates profile plus first draft version. `POST /api/runtime-profiles/{id}/versions` accepts `{config}` and creates another version without mutating prior versions. `POST /api/runtime-profiles/{id}/build` accepts `{version_id?}` and builds that draft version; returns the version with real build/self-test results. A ready version cannot be rebuilt or edited. `GET /api/runtime-profiles/{id}` returns one profile; `GET /api/runtime-versions/{id}` returns one immutable version and referencing workflow publications.

Common config: `{executable?:string,toolchain_id?:string,shared_files?:{"relative/path":"text"},build_timeout?:seconds}`. Paths are administrator-controlled toolchain choices; never shell-interpolated.

Python config includes `requirements_lock` (pip requirements with exact versions and SHA256 hashes, installed with `--require-hashes`), optional `wheelhouse` for owner-managed offline wheels. JavaScript config includes `package_json` (object), `package_lock` (npm lockfile object, required for external dependencies), `module_format:"cjs|esm"`. Installation occurs during build only. Shared files are copied into each version and included in its digest. A custom profile supplies `run_argv:[...]`, optional `build_argv:[...]`, and `self_test_source` using the JSON file protocol; placeholders `{entrypoint}`, `{project}`, `{output}` are substituted as individual argv values, never shell text.

Node configuration pins `runtime_version_id`. An omitted pin on a built-in language resolves an automatically prepared dependency-free native pack at publication. The publication stores the resolved immutable version and platform identity; later profile versions do not mutate admitted runs. The runtime center distinguishes native discovery from a pack that has completed its actual protocol self-test.

## Source files and ZIP projects

`GET /api/source-projects` lists source project metadata. `POST /api/source-projects` accepts `{name,language,entrypoint,files:{"relative/path":"UTF-8 source"},config?}`. `POST /api/source-projects/upload` accepts multipart `file`, `name`, `language`, `entrypoint`, and optional JSON `config`. ZIP is validated before any extraction: no traversal, absolute paths, symlinks, duplicate paths or oversized archives. A plain source file is also accepted. Java JAR upload uses `.jar` and `config.project_type:"jar"`.

Projects are immutable content-addressed source versions; editing means uploading/creating a new project. `GET /api/source-projects/{id}` includes file names/digest/source metadata, not arbitrary filesystem paths. Node config uses `project_id`; entrypoint defaults to the project's entrypoint. Python/JS support helper `main(inputs)` or explicit file mode. JS CJS and ESM are explicit. Java project types: `source`, `jar`, `maven`, `gradle`; config includes `main_class`, optional `artifact` and owner-selected build tool executable. C/C++ config supports `sources:[relative paths]`, `include_dirs:[relative paths]`, and `compile_args:[argv tokens]`. Build artifacts are cached by source/project/runtime/toolchain/platform identity.

## Optional toolchains

`GET /api/runtime-toolchains` reports installed toolchains and official install candidates (name/version/platform/architecture/download size/license/source/checksum). `POST /api/runtime-toolchains/install` accepts `{manifest_id}` for an advertised candidate. Installation downloads to app-managed storage, verifies SHA256 before extraction, reports progress/status, and runs an actual toolchain self-test. It does not install system packages or modify host PATH/login/power settings. Unavailable platform/network/license prerequisites are recorded as failed/unavailable, never ready.

## Backend hooks

`from taskconsole.workflows_packs import prepare_node, register_runtime_routes`

`prepare_node(store,node)` returns a deep-copied publication-ready node with a pinned runtime/project and build metadata. SQL returns unchanged. Execution calls existing `run_script(node,inputs,directory,store.path,cancelled)`; no dependency installation or project build occurs inside a frozen node's run. `register_runtime_routes(app,store,require)` is mounted before the catch-all route.

Native packs run trusted code under the signed-in account. They provide separate files/dependencies and version identity, not a hostile-code sandbox or container-level CPU/memory enforcement. Build tools and installed dependencies may execute trusted author-supplied build hooks at administrator-authorized build time.

## Verified execution behavior

Management routes above are administrator-only because they reveal trusted source and build configuration. The worker uses the copied project root as its current directory, so bundled relative resource paths work. Python packages support relative imports; CJS accepts a local `main` or `module.exports.main`; ESM supports named `main`. Function return values are always business data, even when they contain a `schemaVersion` field. Non-finite JavaScript numbers are rejected before JSON serialization.

Ready pack/project/compiled manifests are checked again at execution, including symlink targets. Input/output/artifact file paths remain absolute. Worker lifecycle integration may set ephemeral `_on_process(pid)` and `_on_process_exit()` callbacks; they are never part of a persisted publication. A callback/polling exception kills and reaps the process group.

Workflow-level defaults use `settings.runtime_defaults:{python:versionId,javascript:versionId,...}`; explicit node pins take precedence. Publish resolves defaults into node pins. Toolchain responses are `{candidates:[...],installed:[...]}`; installation state is `installing|ready|failed` with phase and byte progress. Advertised installers currently include macOS ARM64 Temurin JDK 21.0.12.1+1 and Node 24.19.0/npm, plus Maven 3.9.16 and Gradle 9.7.1 (the latter require an installed JDK). Maven verifies SHA512; other manifests verify SHA256. Other native platform toolchains can be selected by an administrator via an explicit executable and pass the same runtime protocol build test.
