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
from types import SimpleNamespace

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
async def test_counted_vote_details_deduplicates_and_applies_precedence(sqlite_sessionmaker) -> None:
    t1 = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 1, 1, 11, 0, tzinfo=datetime.UTC)
    t3 = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)

    async with sqlite_sessionmaker() as data:
        data.add_all(
            [
                # Intra-source ATR: latest BallotPaper id wins even if created differs
                _ballot(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    voter_asf_uid="alice",
                    choice=sql.VoteChoice.NO,
                    receipt_message_id="r-alice-1@apache.org",
                    created=t2,
                ),
                _ballot(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    voter_asf_uid="alice",
                    choice=sql.VoteChoice.YES,
                    comment="Verified checksums",
                    receipt_message_id="r-alice-2@apache.org",
                    created=t1,
                ),
                # Cross-source email newer than ATR ballot -> email wins
                _ballot(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    voter_asf_uid="bob",
                    choice=sql.VoteChoice.NO,
                    receipt_message_id="r-bob@apache.org",
                    created=t1,
                ),
                _interpretation(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    asf_eid="eid-bob-old",
                    voter_asf_uid="bob",
                    voter_email="bob@apache.org",
                    choice=sql.VoteChoice.ABSTAIN,
                    is_binding_at_confirm=True,
                    email_timestamp=t2,
                    created=t1,
                ),
                _interpretation(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    asf_eid="eid-bob-new",
                    voter_asf_uid="bob",
                    voter_email="bob@apache.org",
                    choice=sql.VoteChoice.YES,
                    is_binding_at_confirm=True,
                    email_timestamp=t2,
                    created=t3,
                ),
                # Cross-source exact timestamp tie -> ATR ballot wins
                _ballot(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    voter_asf_uid="carol",
                    choice=sql.VoteChoice.ABSTAIN,
                    receipt_message_id="r-carol@apache.org",
                    created=t2,
                ),
                _interpretation(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    asf_eid="eid-carol",
                    voter_asf_uid="carol",
                    voter_email="carol@apache.org",
                    choice=sql.VoteChoice.YES,
                    is_binding_at_confirm=True,
                    email_timestamp=t2,
                    created=t3,
                ),
                # Unmapped personal email deduplicated case-insensitively by email:<lower>
                _interpretation(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    asf_eid="eid-ext-1",
                    voter_asf_uid=None,
                    voter_email="User@Example.org",
                    choice=sql.VoteChoice.YES,
                    is_binding_at_confirm=False,
                    email_timestamp=t1,
                    created=t1,
                ),
                _interpretation(
                    release_key="project-1.0.0",
                    vote_seq=1,
                    vote_round=None,
                    asf_eid="eid-ext-2",
                    voter_asf_uid=None,
                    voter_email="user@example.org",
                    choice=sql.VoteChoice.NO,
                    is_binding_at_confirm=False,
                    email_timestamp=t3,
                    created=t3,
                ),
            ]
        )
        await data.commit()

        release = SimpleNamespace(key="project-1.0.0")
        details, summary = await interaction.counted_vote_details_and_summary(release, 1, data)
        only_summary = await interaction.counted_vote_summary(release, 1, data)
        bob_vote = await interaction.counted_vote_for_voter(release, 1, "bob", data)
        missing_vote = await interaction.counted_vote_for_voter(release, 1, "nobody", data)

    assert summary == only_summary
    assert summary.binding_votes == 3
    assert summary.binding_votes_yes == 2
    assert summary.binding_votes_abstain == 1
    assert summary.binding_votes_no == 0
    assert summary.non_binding_votes == 1
    assert summary.non_binding_votes_no == 1

    assert [(d.voter_asf_uid or d.voter_email, d.source, d.choice, d.status_label) for d in details] == [
        ("alice", "atr", sql.VoteChoice.YES, "PMC Member (binding)"),
        ("bob", "email", sql.VoteChoice.YES, "PMC Member (binding)"),
        ("carol", "atr", sql.VoteChoice.ABSTAIN, "PMC Member (binding)"),
        ("user@example.org", "email", sql.VoteChoice.NO, "Contributor (non-binding)"),
    ]
    assert details[0].comment == "Verified checksums"
    assert details[0].receipt_message_id == "r-alice-2@apache.org"
    assert details[1].asf_eid == "eid-bob-new"
    assert details[1].confirmed_by == "chair"
    assert bob_vote == details[1]
    assert missing_vote is None


@pytest.mark.asyncio
async def test_counted_vote_details_isolates_vote_seq_and_excludes_podling_carryover(sqlite_sessionmaker) -> None:
    t1 = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 1, 2, 10, 0, tzinfo=datetime.UTC)

    async with sqlite_sessionmaker() as data:
        data.add_all(
            [
                # Round 1 (vote_seq=1) votes must NOT leak into Round 2 (vote_seq=2)
                _ballot(
                    release_key="podling-1.0.0",
                    vote_seq=1,
                    vote_round=1,
                    voter_asf_uid="mentor",
                    choice=sql.VoteChoice.YES,
                    receipt_message_id="r-round1@apache.org",
                    created=t1,
                ),
                _interpretation(
                    release_key="podling-1.0.0",
                    vote_seq=1,
                    vote_round=1,
                    asf_eid="eid-round1",
                    voter_asf_uid="ppmc-email",
                    voter_email="ppmc@apache.org",
                    choice=sql.VoteChoice.YES,
                    is_binding_at_confirm=True,
                    email_timestamp=t1,
                    created=t1,
                ),
                # Other release at same vote_seq=2 must NOT leak either
                _ballot(
                    release_key="other-1.0.0",
                    vote_seq=2,
                    vote_round=2,
                    voter_asf_uid="other",
                    choice=sql.VoteChoice.NO,
                    receipt_message_id="r-other@apache.org",
                    created=t1,
                ),
                # Round 2 (vote_seq=2) votes
                _ballot(
                    release_key="podling-1.0.0",
                    vote_seq=2,
                    vote_round=2,
                    voter_asf_uid="ipmc",
                    choice=sql.VoteChoice.YES,
                    receipt_message_id="r-round2-ipmc@apache.org",
                    created=t2,
                ),
                _ballot(
                    release_key="podling-1.0.0",
                    vote_seq=2,
                    vote_round=2,
                    voter_asf_uid="committer",
                    choice=sql.VoteChoice.YES,
                    is_binding_at_cast=False,
                    receipt_message_id="r-round2-committer@apache.org",
                    created=t2,
                ),
            ]
        )
        await data.commit()

        release = SimpleNamespace(key="podling-1.0.0")
        round_one_details, round_one_summary = await interaction.counted_vote_details_and_summary(release, 1, data)
        round_two_details, round_two_summary = await interaction.counted_vote_details_and_summary(release, 2, data)

    assert [(d.voter_asf_uid, d.status_label) for d in round_one_details] == [
        ("mentor", "PPMC Member (binding)"),
        ("ppmc-email", "PPMC Member (binding)"),
    ]
    assert round_one_summary.binding_votes == 2

    assert [(d.voter_asf_uid, d.status_label, d.vote_round) for d in round_two_details] == [
        ("committer", "ASF Committer (non-binding)", 2),
        ("ipmc", "IPMC Member (binding)", 2),
    ]
    assert round_two_summary.binding_votes == 1
    assert round_two_summary.non_binding_votes == 1


def _ballot(
    *,
    release_key: str,
    vote_seq: int,
    vote_round: int | None,
    voter_asf_uid: str,
    choice: sql.VoteChoice,
    receipt_message_id: str,
    comment: str = "",
    is_binding_at_cast: bool = True,
    created: datetime.datetime | None = None,
) -> sql.BallotPaper:
    return sql.BallotPaper(
        release_key=release_key,
        vote_seq=vote_seq,
        vote_round=vote_round,
        voter_asf_uid=voter_asf_uid,
        voter_fullname=voter_asf_uid.title(),
        choice=choice,
        comment=comment,
        is_binding_at_cast=is_binding_at_cast,
        revision_number_at_cast="00001",
        receipt_message_id=receipt_message_id,
        created=created or datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


def _interpretation(
    *,
    release_key: str,
    vote_seq: int,
    vote_round: int | None,
    asf_eid: str,
    voter_asf_uid: str | None,
    voter_email: str,
    choice: sql.VoteChoice,
    is_binding_at_confirm: bool,
    email_timestamp: datetime.datetime,
    created: datetime.datetime,
    confirmed_by: str = "chair",
) -> sql.VoteInterpretation:
    return sql.VoteInterpretation(
        release_key=release_key,
        vote_seq=vote_seq,
        asf_eid=asf_eid,
        vote_round=vote_round,
        voter_asf_uid=voter_asf_uid,
        voter_email=voter_email,
        voter_fullname=voter_asf_uid.title() if voter_asf_uid else "Contributor",
        choice=choice,
        is_binding_at_confirm=is_binding_at_confirm,
        email_timestamp=email_timestamp,
        confirmed_by=confirmed_by,
        created=created,
    )
