import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ekb.adapters.persistence.postgres import Postgres
from ekb.adapters.persistence.postgres.models import Base


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("EKB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set EKB_TEST_DATABASE_URL to run disposable-schema PostgreSQL tests")
    schema = f"ekb_test_{uuid4().hex}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    store = Postgres(url, schema=schema)
    try:
        async with store.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield store
    finally:
        await store.close()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
