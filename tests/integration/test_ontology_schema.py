from uuid import uuid4

import pytest
from sqlalchemy import insert, inspect, text
from sqlalchemy.exc import IntegrityError

from ekb.adapters.persistence.postgres.models import VersionRow

pytestmark = pytest.mark.integration


async def test_recreation_replaces_owned_schema_without_dropping_unrelated_tables(database):
    async with database.engine.begin() as connection:
        await connection.execute(text("CREATE TABLE unrelated_records (id integer PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO unrelated_records VALUES (42)"))
        await connection.execute(text("ALTER TABLE concept_versions ADD COLUMN old_kind text"))
        await connection.execute(
            text(
                "INSERT INTO sources (id, connector, location, payload) "
                "VALUES (:id, 'test', 'urn:test', '{}')"
            ),
            {"id": uuid4()},
        )
    async with database.reset(recreate=True):
        pass
    async with database.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: {item["name"] for item in inspect(sync).get_columns("concept_versions")}
        )
        assert "semantic_type" in columns and "old_kind" not in columns
        assert await connection.scalar(text("SELECT count(*) FROM sources")) == 0
        assert await connection.scalar(text("SELECT id FROM unrelated_records")) == 42


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
