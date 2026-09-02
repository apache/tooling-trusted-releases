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

import atr.models.args as args
import atr.models.results as results
import atr.svn.keys_reflect as keys_reflect
import atr.tasks.checks as checks


@checks.with_model(args.SyncKeysFromSvn)
async def sync_from_svn(task_args: args.SyncKeysFromSvn) -> results.Results | None:
    """Reflect a committee's SVN KEYS file into ATR, for a committee in reflect mode."""
    await keys_reflect.reflect_committee(task_args.committee_key)
    return None
