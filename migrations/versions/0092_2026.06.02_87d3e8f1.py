"""Add update_type provenance to projects and committees

Revision ID: 0092_2026.06.02_87d3e8f1
Revises: 0091_2026.06.01_383a2b3d
Create Date: 2026-06-02 15:08:06.302584+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0092_2026.06.02_87d3e8f1"
down_revision: str | None = "0091_2026.06.01_383a2b3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_update_type = sa.Enum("MANUAL", "BOOTSTRAP", "ASFYAML", name="updatetype")


def upgrade() -> None:
    # Existing rows predate update_type. The autoloaded ones still carry the old
    # updated_by = "bootstrap" marker, so map those to BOOTSTRAP and leave the rest
    # on the MANUAL default.
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("update_type", _update_type, nullable=False, server_default="MANUAL"),
        )
    op.execute("UPDATE project SET update_type = 'BOOTSTRAP' WHERE updated_by = 'bootstrap'")

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("update_type", _update_type, nullable=False, server_default="MANUAL"),
        )
    op.execute("UPDATE committee SET update_type = 'BOOTSTRAP' WHERE updated_by = 'bootstrap'")


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("update_type")

    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_column("update_type")
