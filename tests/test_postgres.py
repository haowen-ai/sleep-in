from __future__ import annotations

import os
import uuid

import pytest

from taskconsole.store import Store


def test_postgres_transactions_commit_and_roll_back(tmp_path) -> None:
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_URL is only set by the PostgreSQL CI job")

    store = Store(tmp_path / "state", url)
    record_id = "postgres-smoke-" + uuid.uuid4().hex
    with store.transaction() as transaction:
        transaction.put("test", {"id": record_id, "value": "committed"})
    with store.transaction() as transaction:
        assert transaction.get("test", record_id)["value"] == "committed"

    with pytest.raises(RuntimeError, match="rollback sentinel"):
        with store.transaction() as transaction:
            transaction.put("test", {"id": record_id, "value": "rolled back"})
            raise RuntimeError("rollback sentinel")
    with store.transaction() as transaction:
        assert transaction.get("test", record_id)["value"] == "committed"
        transaction.remove("test", record_id)
