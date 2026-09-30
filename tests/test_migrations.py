"""Migrations apply on a fresh DB, match the models exactly, and fully reverse."""

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Base
from tests.conftest import alembic_config, ensure_database


@pytest.fixture(scope="module")
def migration_url() -> str:
    return ensure_database("personaos_migrations_test")


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_upgrade_downgrade_upgrade(migration_url, fresh_engine_factory):
    cfg = alembic_config(migration_url)
    engine = fresh_engine_factory(migration_url)

    command.downgrade(cfg, "base")  # start from empty whatever the previous run left
    assert _tables(engine) == set()

    command.upgrade(cfg, "head")
    assert _tables(engine) == set(Base.metadata.tables)

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"Models and migrations differ: {diff}"

    command.downgrade(cfg, "base")
    assert _tables(engine) == set()

    command.upgrade(cfg, "head")  # re-runnable
    assert _tables(engine) == set(Base.metadata.tables)


def test_expected_indexes_exist(db_url, fresh_engine_factory):
    insp = inspect(fresh_engine_factory(db_url))
    index_cols = {
        t: {tuple(i["column_names"]) for i in insp.get_indexes(t)} for t in Base.metadata.tables
    }
    assert ("user_id", "status") in index_cols["tasks"]
    assert ("user_id", "status") in index_cols["goals"]
    assert ("user_id", "state") in index_cols["memory_meta"]
    # Every user-owned table can be filtered by an index led by user_id.
    for table in Base.metadata.tables.values():
        if "user_id" in table.c and table.name != "google_tokens":  # PK there
            assert any(cols[0] == "user_id" for cols in index_cols[table.name]), table.name


def _insert(conn, table: str, **values) -> None:
    cols = ", ".join(values)
    params = ", ".join(f":{c}" for c in values)
    conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)


def test_user_delete_cascades(db_url, fresh_engine_factory):
    engine = fresh_engine_factory(db_url)
    uid = uuid.uuid4()
    with engine.begin() as conn:
        _insert(conn, "users", id=uid, auth_uid=f"cascade-{uid}")
        _insert(conn, "goals", id=uuid.uuid4(), user_id=uid, title="g")
        _insert(conn, "tasks", id=uuid.uuid4(), user_id=uid, title="t")
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        for table in ("goals", "tasks"):
            count = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": uid}
            ).scalar()
            assert count == 0, table


def test_status_check_constraint(db_url, fresh_engine_factory):
    engine = fresh_engine_factory(db_url)
    uid = uuid.uuid4()
    with pytest.raises(IntegrityError), engine.begin() as conn:
        _insert(conn, "users", id=uid, auth_uid=f"ck-{uid}")
        _insert(conn, "tasks", id=uuid.uuid4(), user_id=uid, title="t", status="bogus")
