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

import atr.shared.keys as keys
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome


def test_publication_notices_report_only_disabled_committees() -> None:
    publications = {
        "delta": outcome.Result(datatypes.KeysPublish.AUTOMATION_DISABLED),
        "alpha": outcome.Result(datatypes.KeysPublish.AUTOMATION_DISABLED),
        "beta": outcome.Result(datatypes.KeysPublish.PUBLISHED),
        "gamma": outcome.Result(datatypes.KeysPublish.SVN_NOT_CONFIGURED),
        "epsilon": outcome.Error(RuntimeError("publish failed")),
    }

    notice = keys.publication_added_notice(publications)
    warning = keys.publication_removed_warning(publications)

    assert keys.publication_disabled(publications) == ["alpha", "delta"]
    assert (notice is not None) and ("alpha and delta" in notice)
    assert (warning is not None) and ("alpha and delta" in warning)
    assert keys.publication_added_notice({"beta": outcome.Result(datatypes.KeysPublish.PUBLISHED)}) is None
    assert keys.publication_removed_warning({}) is None
