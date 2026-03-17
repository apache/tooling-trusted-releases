"""mass rename name => key

Revision ID: 0060_2026.03.16_2c8e4716
Revises: 0059_2026.03.16_7dda4775
Create Date: 2026-03-16 18:35:29.940455+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic
revision: str = "0060_2026.03.16_2c8e4716"
down_revision: str | None = "0059_2026.03.16_7dda4775"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- committee ---
    op.add_column("committee", sa.Column("key", sa.String(), nullable=True))
    op.add_column("committee", sa.Column("parent_committee_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE committee SET key = name, parent_committee_key = parent_committee_name"))
    # We split the batch into two because, despite the inefficency,
    # we can"t run the name update and the unique drop in the same batch
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.alter_column("key", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("name", existing_type=sa.VARCHAR(), nullable=True)
        batch_op.drop_constraint(batch_op.f("pk_committee"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_committee"), ["key"])
        batch_op.drop_constraint(batch_op.f("uq_committee_name"), type_="unique")
    op.execute(sa.text("UPDATE committee SET name = full_name"))
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.create_unique_constraint(batch_op.f("uq_committee_key"), ["key"])
        batch_op.drop_constraint(batch_op.f("fk_committee_parent_committee_name_committee"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_committee_parent_committee_key_committee"), "committee", ["parent_committee_key"], ["key"]
        )
        batch_op.drop_column("full_name")
        batch_op.drop_column("parent_committee_name")

    # --- project ---
    op.add_column("project", sa.Column("key", sa.String(), nullable=True))
    op.add_column("project", sa.Column("super_project_key", sa.String(), nullable=True))
    op.add_column("project", sa.Column("committee_key", sa.String(), nullable=True))
    op.execute(
        sa.text("UPDATE project SET key = name, super_project_key = super_project_name, committee_key = committee_name")
    )
    # We split the batch into two because, despite the inefficency,
    # we can"t run the name update and the unique drop in the same batch
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.alter_column("key", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("name", existing_type=sa.VARCHAR(), nullable=True)
        batch_op.drop_constraint(batch_op.f("pk_project"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_project"), ["key"])
        batch_op.drop_constraint(batch_op.f("uq_project_name"), type_="unique")
    op.execute(sa.text("UPDATE project SET name = full_name"))
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.create_unique_constraint(batch_op.f("uq_project_key"), ["key"])
        batch_op.drop_constraint(batch_op.f("fk_project_committee_name_committee"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("fk_project_super_project_name_project"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_project_super_project_key_project"), "project", ["super_project_key"], ["key"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_project_committee_key_committee"), "committee", ["committee_key"], ["key"]
        )
        batch_op.drop_column("full_name")
        batch_op.drop_column("super_project_name")
        batch_op.drop_column("committee_name")

    # --- release ---
    op.add_column("release", sa.Column("key", sa.String(), nullable=True))
    op.add_column("release", sa.Column("project_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE release SET key = name, project_key = project_name"))
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("key", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("project_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_release"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_release"), ["key"])
        batch_op.drop_constraint(batch_op.f("uq_release_name"), type_="unique")
        batch_op.drop_constraint(batch_op.f("unique_project_version"), type_="unique")
        batch_op.create_unique_constraint("unique_project_version", ["project_key", "version"])
        batch_op.create_unique_constraint(batch_op.f("uq_release_key"), ["key"])
        batch_op.drop_constraint(batch_op.f("fk_release_project_name_project"), type_="foreignkey")
        batch_op.create_foreign_key(batch_op.f("fk_release_project_key_project"), "project", ["project_key"], ["key"])
        batch_op.drop_column("name")
        batch_op.drop_column("project_name")

    # --- revision ---
    op.add_column("revision", sa.Column("key", sa.String(), nullable=True))
    op.add_column("revision", sa.Column("release_key", sa.String(), nullable=True))
    op.add_column("revision", sa.Column("parent_key", sa.String(), nullable=True))
    op.add_column("revision", sa.Column("merge_base_revision_key", sa.String(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE revision SET key = name, release_key = release_name, parent_key = parent_name, "
            "merge_base_revision_key = merge_base_revision_name"
        )
    )
    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.alter_column("key", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_revision"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_revision"), ["key"])
        batch_op.drop_constraint(batch_op.f("uq_revision_name"), type_="unique")
        batch_op.drop_constraint(batch_op.f("uq_revision_release_number"), type_="unique")
        batch_op.create_unique_constraint("uq_revision_release_number", ["release_key", "number"])
        batch_op.drop_constraint(batch_op.f("uq_revision_release_seq"), type_="unique")
        batch_op.create_unique_constraint("uq_revision_release_seq", ["release_key", "seq"])
        batch_op.create_unique_constraint(batch_op.f("uq_revision_key"), ["key"])
        batch_op.drop_constraint(batch_op.f("fk_revision_release_name_release"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("fk_revision_parent_name_revision"), type_="foreignkey")
        batch_op.create_foreign_key(batch_op.f("fk_revision_release_key_release"), "release", ["release_key"], ["key"])
        batch_op.create_foreign_key(batch_op.f("fk_revision_parent_key_revision"), "revision", ["parent_key"], ["key"])
        batch_op.drop_column("name")
        batch_op.drop_column("parent_name")
        batch_op.drop_column("merge_base_revision_name")
        batch_op.drop_column("release_name")

    # --- releasefilestate ---
    op.add_column("releasefilestate", sa.Column("release_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE releasefilestate SET release_key = release_name"))
    with op.batch_alter_table("releasefilestate", schema=None) as batch_op:
        batch_op.alter_column("release_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_releasefilestate"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_releasefilestate"), ["release_key", "path", "since_revision_seq"])
        batch_op.drop_constraint(
            batch_op.f("fk_releasefilestate_release_name_since_revision_seq_revision"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_releasefilestate_release_key_since_revision_seq_revision"),
            "revision",
            ["release_key", "since_revision_seq"],
            ["release_key", "seq"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("release_name")

    # --- checkresult ---
    op.add_column("checkresult", sa.Column("release_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE checkresult SET release_key = release_name"))
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.alter_column("release_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_checkresult_release_name"))
        batch_op.create_index(batch_op.f("ix_checkresult_release_key"), ["release_key"], unique=False)
        batch_op.drop_constraint(batch_op.f("fk_checkresult_release_name_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_checkresult_release_key_release"), "release", ["release_key"], ["key"], ondelete="CASCADE"
        )
        batch_op.drop_column("release_name")

    # --- checkresultignore ---
    op.add_column("checkresultignore", sa.Column("project_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE checkresultignore SET project_key = project_name"))
    with op.batch_alter_table("checkresultignore", schema=None) as batch_op:
        batch_op.alter_column("project_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_checkresultignore_project_name_project"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_checkresultignore_project_key_project"), "project", ["project_key"], ["key"]
        )
        batch_op.drop_column("project_name")

    # --- distribution ---
    op.add_column("distribution", sa.Column("release_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE distribution SET release_key = release_name"))
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.alter_column("release_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_distribution_release_name"))
        batch_op.drop_constraint(batch_op.f("pk_distribution"), type_="primary")
        batch_op.create_primary_key(
            batch_op.f("pk_distribution"), ["release_key", "platform", "owner_namespace", "package", "version"]
        )
        batch_op.create_index(batch_op.f("ix_distribution_release_key"), ["release_key"], unique=False)
        batch_op.drop_constraint(batch_op.f("fk_distribution_release_name_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_distribution_release_key_release"), "release", ["release_key"], ["key"], ondelete="CASCADE"
        )
        batch_op.drop_column("release_name")

    # --- keylink ---
    op.add_column("keylink", sa.Column("committee_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE keylink SET committee_key = committee_name"))
    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.alter_column("committee_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_keylink_committee_name_committee"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("pk_keylink"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_keylink"), ["committee_key", "key_fingerprint"])
        batch_op.create_foreign_key(
            batch_op.f("fk_keylink_committee_key_committee"), "committee", ["committee_key"], ["key"]
        )
        batch_op.drop_column("committee_name")

    # --- quarantined ---
    op.add_column("quarantined", sa.Column("release_key", sa.String(), nullable=True))
    op.add_column("quarantined", sa.Column("prior_revision_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE quarantined SET release_key = release_name, prior_revision_key = prior_revision_name"))
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.alter_column("release_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_quarantined_release_name"))
        batch_op.create_index(batch_op.f("ix_quarantined_release_key"), ["release_key"], unique=False)
        batch_op.drop_constraint(batch_op.f("fk_quarantined_release_name_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_quarantined_release_key_release"), "release", ["release_key"], ["key"], ondelete="CASCADE"
        )
        batch_op.drop_column("release_name")
        batch_op.drop_column("prior_revision_name")

    # --- revisioncounter ---
    op.add_column("revisioncounter", sa.Column("release_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE revisioncounter SET release_key = release_name"))
    with op.batch_alter_table("revisioncounter", schema=None) as batch_op:
        batch_op.alter_column("release_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_revisioncounter"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_revisioncounter"), ["release_key"])
        batch_op.drop_column("release_name")

    # --- task ---
    op.add_column("task", sa.Column("project_key", sa.String(), nullable=True))
    op.add_column("task", sa.Column("version_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE task SET project_key = project_name, version_key = version_name"))
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_version_name"))
        batch_op.create_index(batch_op.f("ix_task_version_key"), ["version_key"], unique=False)
        batch_op.create_foreign_key(batch_op.f("fk_task_project_key_project"), "project", ["project_key"], ["key"])
        batch_op.drop_column("project_name")
        batch_op.drop_column("version_name")

    # --- workflowsshkey ---
    op.add_column("workflowsshkey", sa.Column("project_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE workflowsshkey SET project_key = project_name"))
    with op.batch_alter_table("workflowsshkey", schema=None) as batch_op:
        batch_op.alter_column("project_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_workflowsshkey_project_name"))
        batch_op.create_index(batch_op.f("ix_workflowsshkey_project_key"), ["project_key"], unique=False)
        batch_op.drop_column("project_name")

    # --- workflowstatus ---
    op.add_column("workflowstatus", sa.Column("project_key", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE workflowstatus SET project_key = project_name"))
    with op.batch_alter_table("workflowstatus", schema=None) as batch_op:
        batch_op.alter_column("project_key", existing_type=sa.String(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_workflowstatus_project_name"))
        batch_op.create_index(batch_op.f("ix_workflowstatus_project_key"), ["project_key"], unique=False)
        batch_op.drop_column("project_name")


def downgrade() -> None:
    # --- workflowstatus ---
    op.add_column("workflowstatus", sa.Column("project_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE workflowstatus SET project_name = project_key"))
    with op.batch_alter_table("workflowstatus", schema=None) as batch_op:
        batch_op.alter_column("project_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_workflowstatus_project_key"))
        batch_op.create_index(batch_op.f("ix_workflowstatus_project_name"), ["project_name"], unique=False)
        batch_op.drop_column("project_key")

    # --- workflowsshkey ---
    op.add_column("workflowsshkey", sa.Column("project_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE workflowsshkey SET project_name = project_key"))
    with op.batch_alter_table("workflowsshkey", schema=None) as batch_op:
        batch_op.alter_column("project_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_index(batch_op.f("ix_workflowsshkey_project_key"))
        batch_op.create_index(batch_op.f("ix_workflowsshkey_project_name"), ["project_name"], unique=False)
        batch_op.drop_column("project_key")

    # --- task ---
    op.add_column("task", sa.Column("version_name", sa.VARCHAR(), nullable=True))
    op.add_column("task", sa.Column("project_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE task SET project_name = project_key, version_name = version_key"))
    with op.batch_alter_table("task", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_task_project_key_project"), type_="foreignkey")
        batch_op.create_foreign_key(batch_op.f("fk_task_project_name_project"), "project", ["project_name"], ["name"])
        batch_op.drop_index(batch_op.f("ix_task_version_key"))
        batch_op.create_index(batch_op.f("ix_task_version_name"), ["version_name"], unique=False)
        batch_op.drop_column("version_key")
        batch_op.drop_column("project_key")

    # --- revisioncounter ---
    op.add_column("revisioncounter", sa.Column("release_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE revisioncounter SET release_name = release_key"))
    with op.batch_alter_table("revisioncounter", schema=None) as batch_op:
        batch_op.alter_column("release_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_revisioncounter"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_revisioncounter"), ["release_name"])
        batch_op.drop_column("release_key")

    # --- releasefilestate ---
    op.add_column("releasefilestate", sa.Column("release_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE releasefilestate SET release_name = release_key"))
    with op.batch_alter_table("releasefilestate", schema=None) as batch_op:
        batch_op.alter_column("release_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_releasefilestate"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_releasefilestate"), ["release_name", "path", "since_revision_seq"])
        batch_op.drop_constraint(
            batch_op.f("fk_releasefilestate_release_key_since_revision_seq_revision"), type_="foreignkey"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_releasefilestate_release_name_since_revision_seq_revision"),
            "revision",
            ["release_name", "since_revision_seq"],
            ["release_name", "seq"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("release_key")

    # --- revision ---
    op.add_column("revision", sa.Column("release_name", sa.VARCHAR(), nullable=True))
    op.add_column("revision", sa.Column("merge_base_revision_name", sa.VARCHAR(), nullable=True))
    op.add_column("revision", sa.Column("parent_name", sa.VARCHAR(), nullable=True))
    op.add_column("revision", sa.Column("name", sa.VARCHAR(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE revision SET name = key, release_name = release_key, parent_name = parent_key, "
            "merge_base_revision_name = merge_base_revision_key"
        )
    )
    with op.batch_alter_table("revision", schema=None) as batch_op:
        batch_op.alter_column("name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_revision"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_revision"), ["name"])
        batch_op.drop_constraint(batch_op.f("fk_revision_parent_key_revision"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("fk_revision_release_key_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_revision_parent_name_revision"), "revision", ["parent_name"], ["name"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_revision_release_name_release"), "release", ["release_name"], ["name"]
        )
        batch_op.drop_constraint(batch_op.f("uq_revision_key"), type_="unique")
        batch_op.drop_constraint("uq_revision_release_seq", type_="unique")
        batch_op.create_unique_constraint(batch_op.f("uq_revision_release_seq"), ["release_name", "seq"])
        batch_op.drop_constraint("uq_revision_release_number", type_="unique")
        batch_op.create_unique_constraint(batch_op.f("uq_revision_release_number"), ["release_name", "number"])
        batch_op.create_unique_constraint(batch_op.f("uq_revision_name"), ["name"])
        batch_op.drop_column("merge_base_revision_key")
        batch_op.drop_column("parent_key")
        batch_op.drop_column("release_key")
        batch_op.drop_column("key")

    # --- release ---
    op.add_column("release", sa.Column("project_name", sa.VARCHAR(), nullable=True))
    op.add_column("release", sa.Column("name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE release SET name = key, project_name = project_key"))
    with op.batch_alter_table("release", schema=None) as batch_op:
        batch_op.alter_column("name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.alter_column("project_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("pk_release"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_release"), ["name"])
        batch_op.drop_constraint(batch_op.f("fk_release_project_key_project"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_release_project_name_project"), "project", ["project_name"], ["name"]
        )
        batch_op.drop_constraint(batch_op.f("uq_release_key"), type_="unique")
        batch_op.drop_constraint("unique_project_version", type_="unique")
        batch_op.create_unique_constraint(batch_op.f("unique_project_version"), ["project_name", "version"])
        batch_op.create_unique_constraint(batch_op.f("uq_release_name"), ["name"])
        batch_op.drop_column("project_key")
        batch_op.drop_column("key")

    # --- quarantined ---
    op.add_column("quarantined", sa.Column("prior_revision_name", sa.VARCHAR(), nullable=True))
    op.add_column("quarantined", sa.Column("release_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE quarantined SET release_name = release_key, prior_revision_name = prior_revision_key"))
    with op.batch_alter_table("quarantined", schema=None) as batch_op:
        batch_op.alter_column("release_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_quarantined_release_key_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_quarantined_release_name_release"), "release", ["release_name"], ["name"], ondelete="CASCADE"
        )
        batch_op.drop_index(batch_op.f("ix_quarantined_release_key"))
        batch_op.create_index(batch_op.f("ix_quarantined_release_name"), ["release_name"], unique=False)
        batch_op.drop_column("prior_revision_key")
        batch_op.drop_column("release_key")

    # --- project ---
    op.add_column("project", sa.Column("committee_name", sa.VARCHAR(), nullable=True))
    op.add_column("project", sa.Column("super_project_name", sa.VARCHAR(), nullable=True))
    op.add_column("project", sa.Column("full_name", sa.VARCHAR(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE project SET full_name = name, name = key, committee_name = committee_key, "
            "super_project_name = super_project_key"
        )
    )
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("pk_project"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_project"), ["name"])
        batch_op.drop_constraint(batch_op.f("fk_project_committee_key_committee"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("fk_project_super_project_key_project"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_project_super_project_name_project"), "project", ["super_project_name"], ["name"]
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_project_committee_name_committee"), "committee", ["committee_name"], ["name"]
        )
        batch_op.drop_constraint(batch_op.f("uq_project_key"), type_="unique")
        batch_op.create_unique_constraint(batch_op.f("uq_project_name"), ["name"])
        batch_op.alter_column("name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_column("committee_key")
        batch_op.drop_column("super_project_key")
        batch_op.drop_column("key")

    # --- keylink ---
    op.add_column("keylink", sa.Column("committee_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE keylink SET committee_name = committee_key"))
    with op.batch_alter_table("keylink", schema=None) as batch_op:
        batch_op.alter_column("committee_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_keylink_committee_key_committee"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("pk_keylink"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_keylink"), ["committee_name", "key_fingerprint"])
        batch_op.create_foreign_key(
            batch_op.f("fk_keylink_committee_name_committee"), "committee", ["committee_name"], ["name"]
        )
        batch_op.drop_column("committee_key")

    # --- distribution ---
    op.add_column("distribution", sa.Column("release_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE distribution SET release_name = release_key"))
    with op.batch_alter_table("distribution", schema=None) as batch_op:
        batch_op.alter_column("release_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_distribution_release_key_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_distribution_release_name_release"),
            "release",
            ["release_name"],
            ["name"],
            ondelete="CASCADE",
        )
        batch_op.drop_index(batch_op.f("ix_distribution_release_key"))
        batch_op.create_index(batch_op.f("ix_distribution_release_name"), ["release_name"], unique=False)
        batch_op.drop_constraint(batch_op.f("pk_distribution"), type_="primary")
        batch_op.create_primary_key(
            batch_op.f("pk_distribution"), ["release_name", "platform", "owner_namespace", "package", "version"]
        )
        batch_op.drop_column("release_key")

    # --- committee ---
    op.add_column("committee", sa.Column("parent_committee_name", sa.VARCHAR(), nullable=True))
    op.add_column("committee", sa.Column("full_name", sa.VARCHAR(), nullable=True))
    op.execute(
        sa.text("UPDATE committee SET full_name = name, name = key, parent_committee_name = parent_committee_key")
    )
    with op.batch_alter_table("committee", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("pk_committee"), type_="primary")
        batch_op.create_primary_key(batch_op.f("pk_committee"), ["name"])
        batch_op.drop_constraint(batch_op.f("fk_committee_parent_committee_key_committee"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_committee_parent_committee_name_committee"), "committee", ["parent_committee_name"], ["name"]
        )
        batch_op.drop_constraint(batch_op.f("uq_committee_key"), type_="unique")
        batch_op.create_unique_constraint(batch_op.f("uq_committee_name"), ["name"])
        batch_op.alter_column("name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_column("parent_committee_key")
        batch_op.drop_column("key")

    # --- checkresultignore ---
    op.add_column("checkresultignore", sa.Column("project_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE checkresultignore SET project_name = project_key"))
    with op.batch_alter_table("checkresultignore", schema=None) as batch_op:
        batch_op.alter_column("project_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_checkresultignore_project_key_project"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_checkresultignore_project_name_project"), "project", ["project_name"], ["name"]
        )
        batch_op.drop_column("project_key")

    # --- checkresult ---
    op.add_column("checkresult", sa.Column("release_name", sa.VARCHAR(), nullable=True))
    op.execute(sa.text("UPDATE checkresult SET release_name = release_key"))
    with op.batch_alter_table("checkresult", schema=None) as batch_op:
        batch_op.alter_column("release_name", existing_type=sa.VARCHAR(), nullable=False)
        batch_op.drop_constraint(batch_op.f("fk_checkresult_release_key_release"), type_="foreignkey")
        batch_op.create_foreign_key(
            batch_op.f("fk_checkresult_release_name_release"), "release", ["release_name"], ["name"], ondelete="CASCADE"
        )
        batch_op.drop_index(batch_op.f("ix_checkresult_release_key"))
        batch_op.create_index(batch_op.f("ix_checkresult_release_name"), ["release_name"], unique=False)
        batch_op.drop_column("release_key")
