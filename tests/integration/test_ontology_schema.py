from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import insert, inspect, text
from sqlalchemy.exc import IntegrityError

from ekb.adapters.persistence.postgres.models import Base, VersionRow
from ekb.domain.models import ScanReport

pytestmark = pytest.mark.integration


async def test_recreation_replaces_owned_schema_without_dropping_unrelated_tables(database):
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated_records (id integer PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO unrelated_records VALUES (42)"))
        await connection.execute(
            text(
                "CREATE TABLE concept_aliases ("
                "decision_id uuid PRIMARY KEY REFERENCES reconciliation_decisions(id), "
                "alias_id uuid REFERENCES concepts(id), target_id uuid REFERENCES concepts(id))"
            )
        )
        await connection.execute(text("ALTER TABLE concept_versions ADD COLUMN old_kind text"))
        await connection.execute(
            text(
                "INSERT INTO sources (id, connector, location, payload) "
                "VALUES (:id, 'test', 'urn:test', '{}')"
            ),
            {"id": uuid4()},
        )
    await database.initialize()
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('concept_aliases')")) is not None
    async with database.reset(recreate=True):
        pass
    async with database.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: {item["name"] for item in inspect(sync).get_columns("concept_versions")}
        )
        assert "semantic_type" in columns and "old_kind" not in columns
        assert await connection.scalar(text("SELECT to_regclass('concept_aliases')")) is None
        assert await connection.scalar(text("SELECT count(*) FROM sources")) == 0
        assert await connection.scalar(text("SELECT id FROM unrelated_records")) == 42


async def test_recreation_keeps_obsolete_tables_when_an_active_scan_blocks_reset(database):
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE concept_aliases (id integer PRIMARY KEY)"))
    async with database() as uow:
        await uow.processing.save_scan(ScanReport(id=uuid4()))
        await uow.commit()
    with pytest.raises(ValueError, match="active scan"):
        async with database.reset(recreate=True):
            pass
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass('concept_aliases')")) is not None
        assert await connection.scalar(text("SELECT count(*) FROM processing_runs")) == 1


async def test_recreation_does_not_remove_namesakes_in_other_schemas(database):
    other_schema = f"ekb_unrelated_{uuid4().hex}"
    async with database.engine.begin() as connection:
        current_schema = await connection.scalar(text("SELECT current_schema()"))
        preparer = connection.dialect.identifier_preparer
        current = preparer.quote_schema(current_schema)
        other = preparer.quote_schema(other_schema)
        await connection.execute(text(f"CREATE SCHEMA {other}"))
        await connection.execute(
            text(f"CREATE TABLE {other}.concept_aliases (id integer PRIMARY KEY)")
        )
        await connection.execute(text(f"INSERT INTO {other}.concept_aliases VALUES (42)"))
        await connection.execute(text(f"SET search_path TO {current}, {other}"))
        assert await connection.scalar(
            text("SELECT to_regclass('concept_aliases') = to_regclass(:name)"),
            {"name": f"{other_schema}.concept_aliases"},
        )
    try:
        async with database.reset(recreate=True):
            pass
        async with database.engine.connect() as connection:
            assert await connection.scalar(text(f"SELECT id FROM {other}.concept_aliases")) == 42
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text(f"SET search_path TO {current}"))
            await connection.execute(text(f"DROP TABLE {other}.concept_aliases"))
            await connection.execute(text(f"DROP SCHEMA {other}"))


async def test_database_enforces_ontology_classes_and_version_payload_agreement(database):
    async with database.engine.begin() as connection:
        concept_id = uuid4()
        await connection.execute(
            text("INSERT INTO concepts (id, identity_key) VALUES (:id, 'new')"),
            {"id": concept_id},
        )

        def version(semantic_type, payload_type):
            return insert(VersionRow).values(
                id=uuid4(),
                concept_id=concept_id,
                statement="Source statement",
                semantic_type=semantic_type,
                payload={"candidate": {"semantic_type": payload_type}},
            )

        for semantic_type in ("StrategicGoal", "TechnicalRequirement", None):
            await connection.execute(version(semantic_type, semantic_type))
        with pytest.raises(IntegrityError, match="version_semantic_type"):
            async with connection.begin_nested():
                await connection.execute(version("objective", "objective"))
        with pytest.raises(IntegrityError, match="version_semantic_type_payload"):
            async with connection.begin_nested():
                await connection.execute(version("StrategicGoal", "TechnicalRequirement"))
        constraint = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                "'ontology_relationship_kind' AND conrelid = 'relationships'::regclass"
            )
        )
        assert "refines" in constraint and "constrains" in constraint
        assert "DERIVES_FROM" not in constraint and "SUPERSEDES" not in constraint


async def test_archived_migrations_match_the_current_schema_and_ontology_constraints(database):
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)

        def migrate(sync):
            context = MigrationContext.configure(sync)
            revisions = list(ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions())
            with Operations.context(context):
                for revision in reversed(revisions):
                    revision.module.upgrade()
            assert compare_metadata(context, Base.metadata) == []
            assert "concept_aliases" not in inspect(sync).get_table_names()
            names = set(
                sync.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid IN "
                        "('concept_versions'::regclass, 'relationships'::regclass)"
                    )
                ).scalars()
            )
            assert {
                "version_semantic_type",
                "version_semantic_type_payload",
                "ontology_relationship_kind",
            } <= names

        await connection.run_sync(migrate)
