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

import atr.log as log
import atr.svn.catalog as catalog
import atr.svn.keys_reflect as keys_reflect

# Dist commits arrive with pubsub_path like /svn/dist/<area>/commit; the changed
# paths inside the commit are relative to the repo root (release/httpd/...), so the
# dev-vs-release split happens on those, not here. Trailing slash so we don't also
# match a /svn/distribution-style repo.
_DIST_PUBSUB_PREFIX: Final[str] = "/svn/dist/"


async def handle(payload: dict) -> str | None:
    pubsub_path = str(payload.get("pubsub_path", ""))
    if not pubsub_path.startswith(_DIST_PUBSUB_PREFIX):
        return None
    commit = payload.get("commit", {})
    if not isinstance(commit, dict):
        return "Commit in SVN pubsub payload is not an object"
    # Log every dist commit we accept, so a deploy can confirm the prefix above
    # actually matches dist commits even when a commit catalogues nothing.
    changed = commit.get("changed", {})
    paths = len(changed) if isinstance(changed, dict) else 0
    log.info(f"dist commit r{commit.get('id')} by {commit.get('committer')}: {paths} changed paths")
    await catalog.catalogue_commit(commit)
    await keys_reflect.reflect_commit(commit)
