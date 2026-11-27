"""Fixtures: the REAL ``rag.engine`` (no fake is installed here) on throwaway Postgres, S3 and Valkey.

This directory sits outside ``generation/tests`` on purpose: ``tests/conftest.py`` replaces
``rag.engine`` with a fake for the in-process suites, and this suite exists to run without it.
Run it with ``make real-engine-test`` (needs Docker and a checkout of the engine tag for its
migrations: ``ENGINE_TAG_DIR``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent))

import infra  # noqa: E402

PASSWORD = "pw"
LOGIN = "rag_activity_login"
BUCKET = "rag-artefacts"
REGION = "eu-west-1"
CAPABILITY = "technical_proposal"


@dataclass
class Stack:
    api: Any  # the workers' RagEngineApi over the real engine
    built: Any
    host: str
    pg_port: int
    s3_endpoint: str
    tender: infra.TenderStub

    def dsn(self, db: str, user: str = "test", password: str = "test") -> str:
        return f"postgresql://{user}:{password}@{self.host}:{self.pg_port}/{db}"

    async def admin(self) -> asyncpg.Connection:
        """A superuser connection to ``llm_generation``: seeding and catalog checks only."""
        return await asyncpg.connect(self.dsn("llm_generation"))


async def _migrate(host: str, port: int, tag_dir: str) -> None:
    from pgcommon import Config, Pool
    from pgcommon.migrate import Runner
    from rag.infrastructure.persistence.migrate import apply_library_schemas

    base = f"postgresql://test:test@{host}:{port}"
    admin = await asyncpg.connect(f"{base}/postgres")
    for db in ("llm_corpus", "llm_generation"):
        await admin.execute(f"CREATE DATABASE {db}")
    await admin.close()
    # the outbox (and the inbox) first: migration 0003 grants on them, as production orders it
    for instance, db in (("corpus", "llm_corpus"), ("generation", "llm_generation")):
        boot = await Pool.create(
            Config(dsn=f"{base}/{db}", max_conns=1, min_conns=1, pool_name="boot")
        )
        await apply_library_schemas(boot, instance)
        await boot.drain_and_close()
    for instance, db in (("corpus", "llm_corpus"), ("generation", "llm_generation")):
        await Runner(f"{base}/{db}", infra.migrations_dir(tag_dir) / instance).up()
    for db in ("llm_corpus", "llm_generation"):  # a non-superuser: a superuser would bypass RLS
        conn = await asyncpg.connect(f"{base}/{db}")
        await conn.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{LOGIN}') THEN "
            f"CREATE ROLE {LOGIN} LOGIN PASSWORD '{PASSWORD}' IN ROLE rag_activity; END IF; END $$"
        )
        await conn.close()


async def _create_bucket(endpoint: str) -> None:
    import aioboto3

    async with aioboto3.Session().client("s3", endpoint_url=endpoint, region_name=REGION) as s3:
        await s3.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def stack() -> AsyncIterator[Stack]:
    tag_dir = os.environ.get("ENGINE_TAG_DIR", "")
    if not tag_dir or not (Path(tag_dir) / "migrations" / "generation").is_dir():
        pytest.skip("ENGINE_TAG_DIR must be a checkout of the engine tag (its migrations are used)")
    try:
        infra.docker("info")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker unavailable: {exc}")
    from llm_workers_generation.activities.engine_api import build_rag_engine

    tender = infra.TenderStub(infra.pack_document(CAPABILITY))
    patch = pytest.MonkeyPatch()
    try:
        with (
            infra.container(
                "postgres:18", 5432, "POSTGRES_USER=test", "POSTGRES_PASSWORD=test"
            ) as (host, pg_port),
            infra.container(
                "floci/floci:2.1.0-compat",
                4566,
                f"FLOCI_DEFAULT_REGION={REGION}",
                "FLOCI_DEFAULT_ACCOUNT_ID=000000000000",
            ) as (_, s3_port),
            infra.container("valkey/valkey:8-alpine", 6379) as (_, valkey_port),
        ):

            async def pg_ready() -> None:
                await (
                    await asyncpg.connect(f"postgresql://test:test@{host}:{pg_port}/postgres")
                ).close()

            await infra.wait_for(pg_ready, "postgres")
            await _migrate(host, pg_port, tag_dir)
            endpoint = f"http://{host}:{s3_port}"
            patch.setenv("AWS_ACCESS_KEY_ID", "test")
            patch.setenv("AWS_SECRET_ACCESS_KEY", "test")
            await infra.wait_for(lambda: _create_bucket(endpoint), "floci")
            login = f"postgresql://{LOGIN}:{PASSWORD}@{host}:{pg_port}"
            for name, value in {
                "GENERATION_DATABASE_URL": f"{login}/llm_generation",
                "CORPUS_DATABASE_URL": f"{login}/llm_corpus",
                "PG_BOUNCER_MODE": "true",
                "S3_BUCKET": BUCKET,
                "S3_REGION": REGION,
                "S3_ENDPOINT_URL": endpoint,
                "TENDER_BASE_URL": tender.url,
                "VALKEY_URL": f"redis://{host}:{valkey_port}/0",
            }.items():
                patch.setenv(name, value)
            api, built = await build_rag_engine()  # the workers' own builder: enable_metrics=False
            try:
                yield Stack(api, built, host, pg_port, endpoint, tender)
            finally:
                await built.aclose()
    finally:
        patch.undo()
        tender.close()
