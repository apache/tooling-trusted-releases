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

from typing import Final

import atr.attestable as attestable
import atr.models.results as results
import atr.tasks.checks as checks

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = ["cross_format_sibling_swhids"]
CHECK_VERSION: Final[str] = "1"


async def across_formats(args: checks.FunctionArguments) -> results.Results | None:
    recorder = await args.recorder(CHECK_VERSION)
    if args.primary_rel_path is None:
        return None
    primary = str(args.primary_rel_path)
    attestable_data = await attestable.load(args.project_key, args.version_key, args.revision_number)
    if attestable_data is None:
        await recorder.exception("Attestable data is not available", {"rel_path": primary})
        return None
    own_swhid = attestable.path_swhid_dir(attestable_data, primary)
    # Do not compare archives if the primary has no SWHID
    # This is a deliberate design choice
    if own_swhid is None:
        return None
    siblings = attestable.cross_format_siblings(attestable_data, primary)
    matched = sorted(path for path, swhid in siblings.items() if swhid == own_swhid)
    mismatched = sorted(path for path, swhid in siblings.items() if (swhid is not None) and (swhid != own_swhid))
    if mismatched:
        await recorder.concern(
            "Cross format archive contents differ from a sibling archive",
            {
                "rel_path": primary,
                "swhid": own_swhid,
                "matched": matched,
                "mismatched": [{"rel_path": path, "swhid": siblings[path]} for path in mismatched],
            },
        )
        return None
    if matched:
        await recorder.note(
            "Cross format archive contents match sibling archives",
            {"rel_path": primary, "swhid": own_swhid, "matched": matched},
        )
    return None
