# External SQL integration matrix

Status: **24 real-database test cases written and collected; not executed on this Mac because Docker is unavailable.** CI must execute them before reporting PostgreSQL/MySQL/Oracle validated. SQLite and the seven-language matrix are separate evidence.

Fixtures are `deploy/sql-matrix-compose.yaml` and `tests/test_workflow_external_sql.py`. They bind loopback only, create randomly named synthetic tables, drop only those tables, and use no host data volumes. Fixed fixture passwords are deliberately public test values, never user credentials. Do not point this destructive fixture suite at a user database.

Pinned container manifest indexes were resolved from Docker Registry on 2026-09-17:

| Receiver | Version/image | Manifest index SHA256 |
|---|---|---|
| PostgreSQL | Official `postgres:17.9` | `2a0d0fe14825b0939f78a8cad5cd4e6aa68bf94d0e5dd96e24b6d23af4315545` |
| MySQL | Official `mysql:8.4.8` | `2952e3be7807f06fc18de50b3ea1a632d5c70d63482ff7d7376fe3aa8999babf` |
| Oracle Free | `gvenzl/oracle-free:23.26.3-slim-faststart` | `f5ff19033860d662c821cb04eb10483fa94f14f78eae252d054291ea07028093` |

Oracle's image is maintained by the author of [oci-oracle-free](https://github.com/gvenzl/oci-oracle-free), rather than the Docker Official Images library. Its README documents the supported 23.26.3 tag, APP_USER creation in FREEPDB1, healthcheck and faststart semantics. PostgreSQL/MySQL are [Docker Official Images](https://github.com/docker-library/official-images). Pulling these fixtures requires no Oracle account.

Each receiver tests exact NUMERIC/DECIMAL value `12345678901234567890.1234567890`, unsafe integer string `9007199254740993`, Chinese/emoji, SQL null, true parameter binding/injection resistance, placeholder-looking text in literals/comments, database read-only mode, committed writes, transaction rollback after duplicate-key failure, rollback after output schema failure, connection write permission, null binding, empty string behavior, and a one-second receiver deadline against an eight-second call. Oracle maps the empty SQL string to null; this is asserted as a documented dialect distinction.

Run on an isolated Linux CI runner:

```sh
python -m pip install -r requirements.txt pytest pymysql==1.2.3 oracledb==26.0.0
docker compose -f deploy/sql-matrix-compose.yaml up -d --wait --wait-timeout 600
SLEEP_IN_EXTERNAL_SQL_TESTS=1 python -m pytest tests/test_workflow_external_sql.py -q --junitxml=external-sql-results.xml
docker compose -f deploy/sql-matrix-compose.yaml down -v
```

Always place compose cleanup in CI's `always()` step. CI may select one receiver using `SLEEP_IN_TEST_SQL_DIALECTS=oracle` (or `postgresql,mysql`). Optional JSON config overrides are `SLEEP_IN_TEST_SQL_POSTGRESQL`, `SLEEP_IN_TEST_SQL_MYSQL`, and `SLEEP_IN_TEST_SQL_ORACLE`; avoid printing those variables. Without explicit opt-in the tests skip, and skips must not be counted as database validation. Connection errors with opt-in are failures, not skips.
