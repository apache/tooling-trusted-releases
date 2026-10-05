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

import pathlib

import alembic.command as command
import alembic.config
import alembic.operations as operations
import alembic.runtime.migration as migration
import pytest
import sqlalchemy
import sqlmodel

import atr.config as config
import tests.unit.pgp_fixtures as pgp_fixtures

_PARAMETERS = {
    "armor": pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC,
    "fingerprint": pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT,
}

_ROWS = {
    "0001": (
        "INSERT INTO committee (name, is_podling) VALUES ('p', 0)",
        "INSERT INTO publicsigningkey (fingerprint, algorithm, length, apache_uid, ascii_armored_key)"
        " VALUES (:fingerprint, 1, 2048, 'u', :armor)",
        "INSERT INTO releasepolicy"
        " (id, manual_vote, min_hours, release_checklist, pause_for_rm, start_vote_template, announce_release_template)"
        " VALUES (1, 1, 72, '', 0, '[PROJECT] [VERSION]', '')",
        "INSERT INTO sshkey (fingerprint, key, asf_uid) VALUES ('ssh', 'key', 'u')",
        "INSERT INTO textvalue (ns, key, value) VALUES ('test', 'key', 'value')",
        "INSERT INTO keylink (committee_name, key_fingerprint) VALUES ('p', :fingerprint)",
        "INSERT INTO project (name, is_podling, is_retired, committee_name, release_policy_id)"
        " VALUES ('p', 0, 0, 'p', 1)",
        "INSERT INTO release (name, stage, phase, project_name, version)"
        " VALUES ('p-1', 'RELEASE_CANDIDATE', 'RELEASE_CANDIDATE', 'p', '1')",
        "INSERT INTO checkresult (release_name, checker, status, message) VALUES ('p-1', 'test', 'FAILURE', 'test')",
        "INSERT INTO revision (name, release_name, seq, number, asfuid, phase)"
        " VALUES ('p-1 00001', 'p-1', 1, '00001', 'u', 'RELEASE_CANDIDATE')",
        "INSERT INTO task (id, status, task_type) VALUES (1, 'COMPLETED', 'HASHING_CHECK')",
    ),
    "0015": ("INSERT INTO personalaccesstoken (asfuid, token_hash) VALUES ('u', 'hash')",),
    "0017": ("INSERT INTO checkresultignore (asf_uid, committee_name, status) VALUES ('u', 'p', 'WARNING')",),
    "0018": (
        "INSERT INTO distribution (release_name, platform, package, version, api_url)"
        " VALUES ('p-1', 'DOCKER', 'p', '1', 'https://example.invalid')",
    ),
    "0024": (
        "INSERT INTO workflowsshkey (fingerprint, key, project_name, expires) VALUES ('workflow', 'key', 'p', 0)",
    ),
    "0037": (
        "INSERT INTO workflowstatus (workflow_id, run_id, project_name, task_id, status)"
        " VALUES ('workflow', 1, 'p', 1, 'completed')",
    ),
    "0053": (
        "INSERT INTO quarantined (release_name, asf_uid, status, token, created)"
        " VALUES ('p-1', 'u', 'FAILED', 'token', '2026-01-01 00:00:00')",
    ),
    "0059": (
        "INSERT INTO releasefilestate (release_name, path, since_revision_seq, present) VALUES ('p-1', 'file', 1, 0)",
    ),
    "0064": ("INSERT INTO user (asfuid, preferences) VALUES ('u', '{}')",),
    "0065": (
        "INSERT INTO usersession (sid_hash, uid, is_member, is_chair, is_root, committees, projects,"
        " mfa, is_role, downgrade_admin_to_user, cts, uts) VALUES ('session', 'u', 0, 0, 0, '[]', '[]', 0, 0, 0, 0, 0)",
    ),
    "0066": ("INSERT INTO sessionformerror (sid_hash, path, payload, cts) VALUES ('session', '/', '{}', 0)",),
    "0072": (
        "INSERT INTO votecounter (release_key) VALUES ('p-1')",
        "INSERT INTO ballotpaper (release_key, vote_seq, voter_asf_uid, voter_fullname, choice,"
        " is_binding_at_cast, revision_number_at_cast, receipt_message_id, created)"
        " VALUES ('p-1', 1, 'u', 'Test User', 'YES', 0, '00001', 'receipt', '2026-01-01 00:00:00')",
    ),
    "0076": (
        "INSERT INTO lifecycleevent (project_key, cycle_key, version_key, event, effective, published, reference_urls)"
        " VALUES ('p', 'p-default', 'p-1', 'RELEASE', '2026-01-01 00:00:00', '2026-01-01 00:00:00', '[]')",
    ),
    "0083": (
        "INSERT INTO artifact (project_key, version, artifact_path, release_key, key_fingerprint)"
        " VALUES ('p', '1', 'file', 'p-1', :fingerprint)",
    ),
    "0085": (
        "INSERT INTO notification (asf_uid, created, level, message)"
        " VALUES ('u', '2026-01-01 00:00:00', 'INFO', 'test')",
    ),
    "0102": (
        "INSERT INTO approvalrequest (project_key, committee_key, action, cap_question_id, status,"
        " requested_by, requested_at, closes_at)"
        " VALUES ('p', 'p', 'ARCHIVE', 1, 'COMPLETED', 'u', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
    ),
    "0104": ("INSERT INTO signaturehint (hint) VALUES ('hint')",),
    "0105": ("INSERT INTO workflowjti (jti, expires, consumed) VALUES ('jti', 0, 0)",),
    "0106": ("INSERT INTO banner (markdown, asf_uid, set_at) VALUES ('test', 'u', '2026-01-01 00:00:00')",),
    "0115": ("INSERT INTO pubsubfailure (created, detail, payload) VALUES ('2026-01-01 00:00:00', 'test', '{}')",),
    "0121": (
        "INSERT INTO keyattestable (fingerprint, seq, operation, source, updated, actor, role)"
        " VALUES (:fingerprint, 1, 'revise', 'test', '2026-01-01 00:00:00', 'u', 'user')",
    ),
}


def test_upgrade_populated_database(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    database = tmp_path / "database" / "atr.db"
    database.parent.mkdir()
    monkeypatch.setattr(config.get(), "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config.get(), "SQLITE_DB_PATH", "database/atr.db")
    alembic_config = alembic.config.Config(str(root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(alembic_config, "0001")
    engine = sqlalchemy.create_engine(f"sqlite:///{database}", poolclass=sqlalchemy.pool.NullPool)
    with engine.begin() as connection:
        context = migration.MigrationContext.configure(connection)
        with operations.Operations(context).batch_alter_table("task") as batch_op:
            batch_op.drop_constraint("ck_task_valid_task_status_transitions", type_="check")
    defaults: dict[tuple[str, str], bool] = {}
    for revision, statements in [*_ROWS.items(), ("head", ())]:
        command.upgrade(alembic_config, revision)
        with engine.begin() as connection:
            current = {
                (table, column): default is not None
                for table, column, default in connection.exec_driver_sql(
                    "SELECT m.name, p.name, p.dflt_value FROM sqlite_schema AS m, pragma_table_info(m.name) AS p"
                    " WHERE m.type = 'table'"
                )
            }
            lost = [key for key in defaults.keys() & current.keys() if defaults[key] and (not current[key])]
            assert not lost, f"{revision} removed defaults from {lost}"
            defaults = current
            for statement in statements:
                connection.exec_driver_sql(statement, _PARAMETERS)
    command.check(alembic_config)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        definitions = dict(connection.exec_driver_sql("SELECT name, sql FROM sqlite_schema WHERE type = 'table'").all())
        for table in sqlmodel.SQLModel.metadata.sorted_tables:
            assert connection.execute(sqlalchemy.select(table)).all(), f"Seed {table.name} at its creation revision"
            for constraint in table.constraints:
                if isinstance(constraint, sqlalchemy.CheckConstraint):
                    assert f"CONSTRAINT {constraint.name} CHECK" in definitions[table.name], constraint.name
