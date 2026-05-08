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

import datetime
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.sql as sql


@pytest.fixture
async def sqlite_sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)

    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


@pytest.mark.asyncio
async def test_previous_round_one_vote_seq_returns_most_recent_after_retry(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        first_round_one = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            vote_round=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="r1a@apache.org",
        )
        retry_round_one = _ballot(
            release_key="project-1.0.0",
            vote_seq=3,
            vote_round=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="r1b@apache.org",
        )
        retry_round_two = _ballot(
            release_key="project-1.0.0",
            vote_seq=4,
            vote_round=2,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="r2b@apache.org",
        )
        data.add_all([first_round_one, retry_round_one, retry_round_two])
        await data.commit()

        found = await interaction.previous_round_one_vote_seq("project-1.0.0", 4, data)

    assert found == 3


@pytest.mark.asyncio
async def test_previous_round_one_vote_seq_returns_none_when_no_round_one_ballots(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        ballot = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            vote_round=None,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="r1@apache.org",
        )
        data.add(ballot)
        await data.commit()

        found = await interaction.previous_round_one_vote_seq("project-1.0.0", 2, data)

    assert found is None


@pytest.mark.asyncio
async def test_previous_round_one_vote_seq_returns_round_one_seq_for_typical_round_two(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        round_one = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            vote_round=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="r1@apache.org",
        )
        round_two = _ballot(
            release_key="project-1.0.0",
            vote_seq=2,
            vote_round=2,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="r2@apache.org",
        )
        data.add_all([round_one, round_two])
        await data.commit()

        found = await interaction.previous_round_one_vote_seq("project-1.0.0", 2, data)

    assert found == 1


def _ballot(
    *,
    release_key: str,
    vote_seq: int,
    vote_round: int | None,
    voter_asf_uid: str,
    choice: sql.VoteChoice,
    receipt_message_id: str,
    created: datetime.datetime | None = None,
) -> sql.BallotPaper:
    return sql.BallotPaper(
        release_key=release_key,
        vote_seq=vote_seq,
        vote_round=vote_round,
        voter_asf_uid=voter_asf_uid,
        voter_fullname="Voter",
        choice=choice,
        comment="",
        is_binding_at_cast=True,
        revision_number_at_cast="00001",
        receipt_message_id=receipt_message_id,
        created=created or datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
