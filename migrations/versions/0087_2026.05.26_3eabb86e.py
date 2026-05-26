"""rename project.category to categories and project.repository to repositories

Revision ID: 0087_2026.05.26_3eabb86e
Revises: 0086_2026.05.26_a69c86b6
Create Date: 2026-05-26 16:10:12.328561+00:00
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0087_2026.05.26_3eabb86e"
down_revision: str | None = "0086_2026.05.26_a69c86b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.alter_column("category", new_column_name="categories")
        batch_op.alter_column("repository", new_column_name="repositories")


def downgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.alter_column("categories", new_column_name="category")
        batch_op.alter_column("repositories", new_column_name="repository")
