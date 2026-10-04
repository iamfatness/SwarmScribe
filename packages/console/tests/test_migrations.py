import asyncio
import os
import subprocess
import sys

import asyncpg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from console_testkit import console_env, recreate, with_database
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from swarmscribe_console import audit
from swarmscribe_console.db.migrate import alembic_config, current_revision, head_revision
from swarmscribe_console.db.models import AuditEntry, Base, Snapshot
from swarmscribe_console.main import main

SEALED = "'\\x01'::bytea || decode(repeat('00', 40), 'hex')"
LEADER_SQL = (
    "insert into leaders (name, base_url, credential, credential_updated_by, added_by)"
    " values (:name, :url, " + SEALED + ", 'test', 'test') returning id"
)


async def test_migrations_produce_exactly_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diff == []


async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0001"
    assert await current_revision(engine) == "0001"


async def test_leader_names_are_unique_ignoring_case(sessionmaker):
    async with sessionmaker() as session:
        await session.execute(text(LEADER_SQL), {"name": "EU-1", "url": "https://a.example"})
        await session.commit()
        with pytest.raises(IntegrityError):
            await session.execute(text(LEADER_SQL), {"name": "eu-1", "url": "https://b.example"})
        await session.rollback()


@pytest.mark.parametrize(
    ("name", "url"),
    [("eu-1", "http://leader.example"), ("../eu", "https://leader.example"), ("", "https://x")],
    ids=["http-url", "path-name", "empty-name"],
)
async def test_the_database_refuses_bad_leader_rows(sessionmaker, name, url):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(LEADER_SQL), {"name": name, "url": url})
        await session.rollback()


async def test_the_database_refuses_an_unsealed_credential(sessionmaker):
    plain = LEADER_SQL.replace(SEALED, "'\\x41'::bytea")
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(plain), {"name": "eu-1", "url": "https://a.example"})
        await session.rollback()


@pytest.mark.parametrize(
    "sql",
    [
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'root', 'all', 'email', 'a@b.org', 't')",
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'viewer', 'everything', 'email', 'a@b.org', 't')",
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'viewer', 'all', 'user', 'a@b.org', 't')",
        "insert into console_admins (id, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'group', 'x', 't')",
    ],
    ids=["unknown-role", "unknown-scope", "unknown-principal-kind", "unknown-admin-kind"],
)
async def test_the_database_refuses_bad_grants(sessionmaker, sql):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(sql))
        await session.rollback()


async def test_removing_a_leader_removes_its_snapshots(sessionmaker):
    async with sessionmaker() as session:
        leader_id = await session.scalar(
            text(LEADER_SQL), {"name": "eu-1", "url": "https://leader.example"}
        )
        await session.execute(
            text(
                "insert into snapshots (leader_id, taken_at, reachable, outcome)"
                " values (:id, now(), true, 'ok')"
            ),
            {"id": leader_id},
        )
        await session.execute(text("delete from leaders where id = :id"), {"id": leader_id})
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Snapshot)) == 0


async def test_an_audit_entry_commits_with_its_change(sessionmaker):
    async with sessionmaker() as session:
        audit.record(session, actor="a (b c)", action="leader.add", leader="eu-1", target="x")
        await session.commit()
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.actor, entry.action, entry.leader, entry.target, entry.outcome) == (
        "a (b c)",
        "leader.add",
        "eu-1",
        "x",
        "ok",
    )
    assert entry.detail == {}
    assert entry.at is not None


async def test_an_audit_entry_apart_never_raises(sessionmaker):
    def broken():
        raise RuntimeError("database is down")

    await audit.record_apart(broken, actor="a", action="x")
    await audit.record_apart(sessionmaker, actor="a", action="request.refused", outcome="csrf")
    async with sessionmaker() as session:
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert (entry.action, entry.outcome) == ("request.refused", "csrf")


def test_migrate_brings_an_empty_database_to_head_and_back(admin_database_url, monkeypatch):
    name = "swarmscribe_console_migrate"
    url = with_database(admin_database_url, name)
    asyncio.run(recreate(admin_database_url, name))
    console_env(monkeypatch, url)

    async def tables() -> set[str]:
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(
                "select tablename from pg_tables where schemaname = 'public'"
            )
            return {row["tablename"] for row in rows}
        finally:
            await conn.close()

    try:
        assert main(["migrate"]) == 0
        assert main(["migrate"]) == 0  # idempotent
        assert {
            "leaders",
            "role_grants",
            "console_admins",
            "snapshots",
            "audit_log",
            "sessions",
            "login_attempts",
        } <= asyncio.run(tables())
        command.downgrade(alembic_config(url), "base")
        assert asyncio.run(tables()) == {"alembic_version"}
    finally:
        asyncio.run(recreate(admin_database_url, name, drop_only=True))


def test_migrate_refuses_bad_settings_without_echoing_them(monkeypatch, capsys):
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_DATABASE_URL", "postgresql://u:secret-pw@h/db")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_PUBLIC_URL", "https://console.test")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_KEY", "a-wrong-key-value")
    assert main(["migrate"]) == 2
    err = capsys.readouterr().err
    assert "key" in err
    assert "a-wrong-key-value" not in err
    assert "secret-pw" not in err


@pytest.mark.parametrize("field", ["actor", "action", "target", "outcome", "leader"])
async def test_audit_replaces_control_characters_in_every_field(sessionmaker, field):
    fields = {"actor": "a", "action": "x", "target": "t", "outcome": "ok", "leader": "l"}
    fields[field] = "a\r\nb\x00c\u2028d\x85e"
    async with sessionmaker() as session:
        audit.record(session, **fields)
        await session.commit()
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert getattr(entry, field) == "a\ufffd\ufffdb\ufffdc\ufffdd\ufffde"


@pytest.mark.parametrize(
    ("field", "limit"),
    [("actor", 400), ("action", 64), ("target", 400), ("outcome", 64), ("leader", 100)],
)
async def test_audit_truncates_over_long_values_and_still_commits(sessionmaker, field, limit):
    fields = {"actor": "a", "action": "x", "target": "t", "outcome": "ok", "leader": "l"}
    fields[field] = "z" * 10_000
    async with sessionmaker() as session:
        session.add_all([])
        audit.record(session, **fields)
        await session.commit()
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert getattr(entry, field) == "z" * (limit - 1) + "\u2026"


async def test_audit_bounds_detail(sessionmaker):
    async with sessionmaker() as session:
        audit.record(session, actor="a", action="x", detail={"big": "y" * 5000})
        audit.record(session, actor="a", action="x", detail={"k\x00": "v\x00", "n": 1})
        await session.commit()
        entries = (await session.scalars(select(AuditEntry).order_by(AuditEntry.id))).all()
    assert entries[0].detail == {"truncated": True}
    assert entries[1].detail == {"k\ufffd": "v\ufffd", "n": 1}


async def test_audit_apart_logs_failures_without_the_values(caplog):
    def broken():
        raise RuntimeError("secret-value")

    await audit.record_apart(broken, actor="secret-actor", action="x")
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "sql",
    [
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'viewer', 'all', 'email', '', 't')",
        "insert into role_grants (id, role, scope, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'viewer', 'all', 'email', 'a@b.org', '')",
        "insert into console_admins (id, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'email', '', 't')",
        "insert into console_admins (id, principal_kind, principal, created_by)"
        " values (gen_random_uuid(), 'email', 'a@b.org', '')",
    ],
    ids=["grant-principal", "grant-created-by", "admin-principal", "admin-created-by"],
)
async def test_the_database_refuses_empty_principals_and_authors(sessionmaker, sql):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(sql))
        await session.rollback()


async def test_the_audit_log_is_indexed_by_leader_and_time(engine):
    async with engine.connect() as conn:
        definition = await conn.scalar(
            text("select indexdef from pg_indexes where indexname = 'ix_audit_log_leader_at'")
        )
    assert definition is not None
    assert "(leader, at)" in definition


def test_migrate_against_an_unreachable_database_is_one_line_and_leaks_nothing(
    monkeypatch, capsys
):
    console_env(monkeypatch, "postgresql://nobody:hunter2-pw@127.0.0.1:9/none")
    assert main(["migrate"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: cannot migrate the database: ")
    assert "Traceback" not in captured.err
    assert "hunter2-pw" not in captured.err and "nobody" not in captured.err


def test_two_migrate_processes_at_once_both_succeed(admin_database_url, monkeypatch):
    name = "swarmscribe_console_migrate_race"
    url = with_database(admin_database_url, name)
    asyncio.run(recreate(admin_database_url, name))
    console_env(monkeypatch, url)
    code = "from swarmscribe_console.main import run; run()"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, "migrate"],
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        results = []
        for process in processes:
            out, err = process.communicate(timeout=180)
            results.append((process.returncode, err))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
        revision = None

        async def read_revision():
            conn = await asyncpg.connect(url)
            try:
                return await conn.fetchval("select version_num from alembic_version")
            finally:
                await conn.close()

        try:
            revision = asyncio.run(read_revision())
        finally:
            asyncio.run(recreate(admin_database_url, name, drop_only=True))
    assert [code for code, _ in results] == [0, 0], results
    assert revision == head_revision()
