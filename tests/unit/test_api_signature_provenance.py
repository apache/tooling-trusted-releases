# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from collections.abc import AsyncIterator

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.api as api
import atr.db as db
import atr.models as models
import atr.models.safe as safe
import atr.models.sql as sql


@pytest.fixture
async def sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_match_artifact_rows(sessionmaker) -> None:
    digest = "ab" * 32
    args = models.api.SignatureProvenanceArgs(
        signature_file_name="a.tar.gz.asc",
        signature_asc_text="unused",
        signature_sha3_256=digest,
    )
    async with sessionmaker() as data:
        data.add(
            sql.Artifact(
                project_key="foo",
                version="1.0",
                artifact_path="sub/a.tar.gz",
                signature_path="sub/a.tar.gz.asc",
                signature_sha3_256=digest,
            )
        )
        data.add(
            sql.Artifact(
                project_key="bar",
                version="2.0",
                artifact_path="b.tar.gz",
                signature_path="b.tar.gz.asc",
                signature_sha3_256="cd" * 32,
            )
        )
        await data.commit()

        assert await api._match_artifact_rows(data, ["foo"], args) is True
        assert await api._match_artifact_rows(data, ["foo"], args, version=safe.VersionKey("1.0")) is True
        assert await api._match_artifact_rows(data, ["foo"], args, version=safe.VersionKey("9.9")) is False
        assert await api._match_artifact_rows(data, ["bar"], args) is False
        assert await api._match_artifact_rows(data, [], args) is False

        renamed = args.model_copy(update={"signature_file_name": "other.asc"})
        assert await api._match_artifact_rows(data, ["foo"], renamed) is False
