"""Keep committees with activity on email votes before trusted votes become the default

Revision ID: 0143_2026.11.05_71710c28
Revises: 0142_2026.11.05_6213b479
Create Date: 2026-11-05 18:11:25.277354+00:00
"""

import collections.abc as abc
from typing import Final

import alembic.op as op
import sqlalchemy

revision: str = "0143_2026.11.05_71710c28"
down_revision: str | None = "0142_2026.11.05_6213b479"
branch_labels: str | abc.Sequence[str] | None = None
depends_on: str | abc.Sequence[str] | None = None

_PROJECTS_TO_KEEP_ON_EMAIL: Final = sqlalchemy.text("""
    WITH roster (committee_key, asf_uid) AS (
        SELECT c.key, m.value FROM committee AS c, json_each(c.committee_members) AS m
        UNION SELECT c.key, m.value FROM committee AS c, json_each(c.committers) AS m
        UNION SELECT c.key, m.value FROM committee AS c, json_each(c.release_managers) AS m
    ), counted (project_key) AS (
        SELECT (
            SELECT p.key FROM project AS p
            WHERE substr(c.release_key, 1, length(p.key) + 1) = p.key || '-'
            ORDER BY length(p.key) DESC
            LIMIT 1
        )
        FROM (SELECT release_key FROM revisioncounter UNION SELECT release_key FROM votecounter) AS c
    ), held (committee_key) AS (
        SELECT 'bookkeeper'
        UNION SELECT 'polaris'
        UNION SELECT p.committee_key
        FROM project AS p
        LEFT JOIN releasepolicy AS rp ON rp.id = p.release_policy_id
        WHERE p.status = 'ACTIVE'
        AND (
            rp.vote_mode = 'EMAIL'
            OR (
                rp.id IS NULL
                AND (
                    EXISTS (
                        SELECT 1 FROM release AS r
                        WHERE r.project_key = p.key
                        AND (r.phase != 'RELEASE' OR r.vote_mode IS NOT NULL OR r.vote_started IS NOT NULL)
                    )
                    OR EXISTS (SELECT 1 FROM artifact AS a WHERE a.project_key = p.key AND a.managed = 1)
                    OR p.key IN (SELECT project_key FROM counted)
                    OR EXISTS (
                        SELECT 1 FROM roster
                        WHERE roster.committee_key = p.committee_key
                        AND roster.asf_uid IN (p.created_by, p.updated_by)
                    )
                )
            )
        )
    )
    SELECT key FROM project
    WHERE release_policy_id IS NULL AND committee_key IN (SELECT committee_key FROM held)
""")
_INSERT_EMAIL_POLICY: Final = sqlalchemy.text("""
    INSERT INTO releasepolicy (
        vote_mode, release_checklist, start_vote_template, announce_release_template, binary_artifact_paths,
        source_artifact_paths, github_compose_workflow_path, github_vote_workflow_path, github_finish_workflow_path
    ) VALUES ('EMAIL', '', '', '', '[]', '[]', '[]', '[]', '[]')
""")
_SET_PROJECT_POLICY: Final = sqlalchemy.text("UPDATE project SET release_policy_id = :policy_id WHERE key = :key")


def downgrade() -> None:
    pass


def upgrade() -> None:
    connection = op.get_bind()
    for (key,) in connection.execute(_PROJECTS_TO_KEEP_ON_EMAIL).all():
        policy_id = connection.execute(_INSERT_EMAIL_POLICY).lastrowid
        connection.execute(_SET_PROJECT_POLICY, {"policy_id": policy_id, "key": key})
