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

import unittest.mock as mock
from types import SimpleNamespace

import asfquart.base as base
import pytest

import atr.db.interaction as interaction
import atr.ldap as ldap


@pytest.mark.asyncio
async def test_validate_trusted_jwt_reports_unlinked_github_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = SimpleNamespace(actor="abc", actor_id=123)
    ldap_error = ldap.LookupError("GitHub NID 123 not registered with ATR")

    monkeypatch.setattr(interaction.jwtoken, "verify_github_oidc", mock.AsyncMock(return_value=payload))
    monkeypatch.setattr(interaction.ldap, "github_to_apache", mock.AsyncMock(side_effect=ldap_error))

    with pytest.raises(base.ASFQuartException) as exc_info:
        await interaction.validate_trusted_jwt("github", "jwt")

    assert exc_info.value.errorcode == 403
    assert str(exc_info.value) == (
        "GitHub account abc (ID 123) is not yet linked to an ASF user in gitbox.apache.org/boxer"
    )
    assert exc_info.value.__cause__ is ldap_error
