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

import contextlib
import importlib.util
import pathlib
import types
import unittest.mock as mock

import pytest

import atr.admin as admin
import atr.blueprints.admin as admin_blueprint
import atr.form as form
import atr.util as util

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "keys_import.py"


async def _post(target: util.SvnPublishTarget, update_keys: mock.AsyncMock) -> object:
    session = types.SimpleNamespace(
        asf_uid="alice", form_validate=mock.AsyncMock(return_value=form.Empty(csrf_token="csrf"))
    )
    with (
        mock.patch.object(admin_blueprint.common, "authenticate", mock.AsyncMock(return_value=session)),
        mock.patch.object(admin.util, "svn_publish_target", return_value=target),
        mock.patch.object(admin, "_update_keys", update_keys),
    ):
        return await admin.keys_update_post()


def _script():
    spec = importlib.util.spec_from_file_location("keys_import", _SCRIPT)
    assert (spec is not None) and (spec.loader is not None)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_keys_import_amain_refuses_the_release_target_before_importing() -> None:
    script = _script()
    keys_import = mock.AsyncMock()
    with (
        mock.patch.object(script, "log_to_file", contextlib.nullcontext),
        mock.patch.object(script.util, "svn_publish_target", return_value=util.SvnPublishTarget.RELEASE),
        mock.patch.object(script, "keys_import", keys_import),
        pytest.raises(SystemExit) as raised,
    ):
        await script.amain()

    assert raised.value.code == 2
    keys_import.assert_not_awaited()


def test_keys_import_refuses_only_the_release_target() -> None:
    script = _script()

    with mock.patch.object(script.util, "svn_publish_target", return_value=util.SvnPublishTarget.RELEASE):
        assert script.release_target_refused() is True
    with mock.patch.object(script.util, "svn_publish_target", return_value=util.SvnPublishTarget.ATR):
        assert script.release_target_refused() is False


@pytest.mark.asyncio
async def test_keys_update_post_refuses_the_release_target() -> None:
    update_keys = mock.AsyncMock(return_value=123)

    refused, status = await _post(util.SvnPublishTarget.RELEASE, update_keys)

    assert (status == 200) and (refused["category"] == "error") and ("release area" in refused["message"])
    update_keys.assert_not_awaited()


@pytest.mark.asyncio
async def test_keys_update_post_runs_for_the_atr_target() -> None:
    update_keys = mock.AsyncMock(return_value=123)

    started, _ = await _post(util.SvnPublishTarget.ATR, update_keys)

    assert started["category"] == "success"
    update_keys.assert_awaited_once_with("alice")


@pytest.mark.asyncio
async def test_update_keys_runs_the_script_in_its_full_mode() -> None:
    process = types.SimpleNamespace(pid=123, returncode=0, communicate=mock.AsyncMock(return_value=(b"", b"")))
    create = mock.AsyncMock(return_value=process)
    with (
        mock.patch.object(admin.asyncio, "create_subprocess_exec", create),
        mock.patch.object(admin.asfquart, "APP", types.SimpleNamespace()),
    ):
        assert await admin._update_keys("alice") == 123

    arguments = [str(argument) for argument in create.call_args.args]
    assert arguments[-4:] == ["alice", "--apply", "--allow-refresh", "--allow-undelete"]
