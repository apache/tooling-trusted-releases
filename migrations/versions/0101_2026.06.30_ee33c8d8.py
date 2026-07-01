"""Widen artifact download_path_suffix to the whole dist directory

Revision ID: 0101_2026.06.30_ee33c8d8
Revises: 0100_2026.06.30_b2d4f6a8
Create Date: 2026-06-30 14:12:35.831011+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0101_2026.06.30_ee33c8d8"
down_revision: str | None = "0100_2026.06.30_b2d4f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Prepend the committee dir (with the incubator prefix for a podling), from the artifact's project,
# to the release sub-path already stored, so download_path_suffix holds the whole directory under
# the dist root. Rows whose committee can't be resolved are left untouched
_WIDEN_DOWNLOAD_PATH_SUFFIX = """
    UPDATE artifact
    SET download_path_suffix = (
            SELECT (CASE WHEN c.is_podling THEN 'incubator/' ELSE '' END) || c.key
            FROM project p
            JOIN committee c ON c.key = p.committee_key
            WHERE p.key = artifact.project_key
        )
        || (CASE WHEN COALESCE(download_path_suffix, '') = '' THEN '' ELSE '/' || download_path_suffix END)
    WHERE EXISTS (
        SELECT 1 FROM project p
        JOIN committee c ON c.key = p.committee_key
        WHERE p.key = artifact.project_key
    )
"""


def upgrade() -> None:
    op.execute(_WIDEN_DOWNLOAD_PATH_SUFFIX)


def downgrade() -> None:
    # The widening prepends a committee dir that can't be recovered per-row, so it doesn't reverse
    pass
