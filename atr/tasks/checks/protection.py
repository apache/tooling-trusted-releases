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

from typing import Any, Final

import aiohttp

import atr.config as config
import atr.db as db
import atr.ldap as ldap
import atr.models.results as results
import atr.models.safe as safe
import atr.tasks.checks as checks
import atr.user as user
import atr.util as util

_HTTP_TIMEOUT: Final = aiohttp.ClientTimeout(total=30, connect=10)
_TAG_PREFIX: Final = "refs/tags/"

INPUT_POLICY_KEYS: Final[list[str]] = []
CHECK_VERSION: Final[str] = "1"


async def check(args: checks.FunctionArguments) -> results.Results | None:
    recorder = await args.recorder(CHECK_VERSION)
    repository, ref, sha = args.extra_args["repository"], args.extra_args["ref"], args.extra_args["sha"]
    data = {"repository": repository, "ref": ref, "sha": sha}
    try:
        suite = await _find_rule_suite(repository, ref, sha)
        problem = await _problem(args.project_key, ref, suite)
    except Exception as e:
        await recorder.concern(f"Could not check whether {ref} was protected", {**data, "error": str(e)})
        return None
    data["suite"] = suite
    if problem is not None:
        await recorder.concern(f"Could not confirm that {ref} was protected: {problem}", data)
        return None
    await recorder.note(f"{ref} was protected", data)
    return None


async def _find_rule_suite(repository: str, ref: str, sha: str) -> dict[str, Any] | None:
    params = {"ref": ref, "time_period": "month", "per_page": "100"}
    for suite in await _github(f"/repos/{repository}/rulesets/rule-suites", params):
        if (suite["result"] != "fail") and await _reached(repository, ref, suite["after_sha"], sha):
            return await _github(f"/repos/{repository}/rulesets/rule-suites/{suite['id']}")
    return None


async def _github(path: str, params: dict[str, str] | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {config.get().GITHUB_RULES_TOKEN}",
    }
    async with util.create_secure_session(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(f"https://api.github.com{path}", headers=headers, params=params) as response:
            response.raise_for_status()
            return await response.json()


async def _may_bypass(project_key: safe.ProjectKey, actor_id: int) -> bool:
    try:
        uid = await ldap.github_to_apache(actor_id)
    except ldap.LookupError:
        return False
    async with db.session() as data:
        project = await data.project(key=str(project_key), _committee=True).demand(RuntimeError("Project not found"))
    return user.is_committee_member(project.committee, uid) or user.is_release_manager(project.committee, uid)


def _missing_rules(ref: str, evaluations: list[dict[str, Any]]) -> list[str]:
    active = {e["rule_type"] for e in evaluations if e["enforcement"] == "active"}
    if ref.startswith(_TAG_PREFIX) or ("update" in active):
        return sorted({"creation", "update"} - active)
    return sorted({"creation", "non_fast_forward", "pull_request"} - active)


async def _problem(project_key: safe.ProjectKey, ref: str, suite: dict[str, Any] | None) -> str | None:
    if suite is None:
        return "GitHub has no protection record for this change"
    missing = _missing_rules(ref, suite["rule_evaluations"])
    if missing:
        return f"no active rule for {', '.join(missing)}"
    if (suite["result"] == "bypass") and (not await _may_bypass(project_key, suite["actor_id"])):
        return f"bypassed by {suite['actor_name']}, who is not a committee member or release manager"
    return None


async def _reached(repository: str, ref: str, after_sha: str, sha: str) -> bool:
    if after_sha == sha:
        return True
    if not ref.startswith(_TAG_PREFIX):
        return False
    tag = await _github(f"/repos/{repository}/git/tags/{after_sha}")
    return tag["object"]["sha"] == sha
