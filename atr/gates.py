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

import hashlib
import hmac
import json
import os
import pathlib
import re
import time
import urllib.parse
from typing import Any, Final

import aiofiles
import aiohttp
import jwt
import quart
import sqlalchemy.dialects.sqlite as sqlite

import atr.config as config
import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.sql as sql
import atr.util as util

EVENTS: Final[tuple[str, ...]] = ("push", "release", "workflow_dispatch")
REF_TYPES: Final[dict[str, str]] = {"branch": "heads", "tag": "tags"}
TEAMS: Final[dict[str, str]] = {"tooling": ldap.LDAP_TOOLING_SERVICE_BASE}
THRESHOLD: Final[int] = 2

_CALLBACK: Final = re.compile(
    r"https://api\.github\.com/repos/[^/]+/[^/]+/actions/runs/([0-9]+)/deployment_protection_rule"
)
_HTTP_TIMEOUT: Final = aiohttp.ClientTimeout(total=30, connect=10)
_PATTERN: Final = re.compile(r"(?:[^\[\]\\]|\[(?:0-9|a-z|A-Z|[A-Za-z0-9._])+\])+")


async def active(data: db.Session, repository_id: int) -> sql.GateConfig | None:
    via = sql.validate_instrumented_attribute
    query = data.gate_config(repository_id=repository_id, status=sql.GateStatus.APPROVED)
    return await query.order_by(via(sql.GateConfig.decided).desc()).limit(1).get()


async def eligible(committee_key: str) -> set[str]:
    return {uid for uid in await ldap.group_members(TEAMS[committee_key]) if await ldap.is_active(uid)}


async def lookup(repository: str) -> tuple[int, str]:
    token = await _installation_token(repository)
    found = await _github("GET", f"/repos/{repository}", token)
    return found["id"], found["full_name"]


def pattern_supported(pattern: str) -> bool:
    normal = str(pathlib.PurePosixPath(pattern)) == pattern
    return (_PATTERN.fullmatch(pattern) is not None) and (not pattern.endswith("**")) and normal


def run_problem(gate_obj: sql.GateConfig, repository_id: int, run: dict[str, Any]) -> str | None:
    if run["repository"]["id"] != repository_id:
        return "the run is in another repository"
    if run["path"] != gate_obj.workflow:
        return f"the workflow is {run['path']}, not {gate_obj.workflow}"
    if run["event"] != gate_obj.event:
        return f"the event is {run['event']}, not {gate_obj.event}"
    if not pathlib.PurePosixPath(str(run["head_branch"])).full_match(gate_obj.ref_pattern):
        return f"the ref {run['head_branch']} does not match {gate_obj.ref_pattern}"
    return None


async def send_decision(request_id: int) -> None:
    async with db.session() as data:
        request = await data.gate_request(id=request_id).demand(RuntimeError(f"Gate request {request_id} not found"))
        gate_obj = await active(data, request.repository_id)
    if gate_obj is None:
        raise RuntimeError(f"No gate covers repository {request.repository_id}")
    if tally(request.decisions, await eligible(gate_obj.committee_key)) != request.status:
        raise RuntimeError(f"The decision on gate request {request_id} no longer holds")
    token = await _installation_token(gate_obj.repository)
    run = await _github("GET", f"/repos/{gate_obj.repository}/actions/runs/{request.run_id}", token)
    if (run["run_attempt"] != request.run_attempt) or (run["status"] != "waiting"):
        raise RuntimeError(f"Run {request.run_id} is no longer waiting on attempt {request.run_attempt}")
    problem = run_problem(gate_obj, request.repository_id, run)
    if (problem is None) and (request.ref != f"refs/{REF_TYPES[gate_obj.ref_type]}/{run['head_branch']}"):
        problem = f"it was recorded as {request.ref}"
    if problem is not None:
        raise RuntimeError(f"Run {request.run_id} no longer passes the gate: {problem}")
    deciders = ", ".join(f"{uid} (ASF)" for uid in sorted(request.decisions))
    try:
        await _review(
            token, gate_obj, request.run_id, request.status.value, f"{request.status.value} in ATR by {deciders}"
        )
    except (aiohttp.ClientError, TimeoutError) as e:
        raise RuntimeError(f"Delivery of the decision on run {request.run_id} is uncertain: {e!r}") from e


def signature_valid(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def tally(decisions: dict[str, bool], eligible_uids: set[str]) -> sql.GateStatus:
    if not all(decisions.values()):
        return sql.GateStatus.REJECTED
    if len(eligible_uids.intersection(decisions)) >= THRESHOLD:
        return sql.GateStatus.APPROVED
    return sql.GateStatus.PENDING


async def webhook() -> quart.Response:
    secret = config.get().GITHUB_APP_WEBHOOK_SECRET
    if (not secret) or (not config.get().GITHUB_APP_ID):
        return quart.Response(status=404)
    body = await quart.request.get_data(cache=True, as_text=False, parse_form_data=False)
    if not signature_valid(secret, body, quart.request.headers.get("X-Hub-Signature-256", "")):
        return quart.Response(status=401)
    if quart.request.headers.get("X-GitHub-Event") != "deployment_protection_rule":
        return quart.Response(status=204)
    await _record(json.loads(body))
    return quart.Response(status=202)


async def _github(method: str, path: str, token: str, body: dict[str, str] | None = None) -> Any:
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    async with util.create_secure_session(timeout=_HTTP_TIMEOUT) as session:
        async with session.request(method, f"https://api.github.com{path}", headers=headers, json=body) as response:
            response.raise_for_status()
            if response.status == 204:
                return None
            return await response.json()


async def _installation_token(repository: str) -> str:
    conf = config.get()
    async with aiofiles.open(os.path.join(conf.STATE_DIR, "secrets", "curated", "github-app.pem")) as f:
        key = await f.read()
    now = int(time.time())
    app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": conf.GITHUB_APP_ID}, key, algorithm="RS256")
    installation = await _github("GET", f"/repos/{repository}/installation", app_jwt)
    token = await _github("POST", f"/app/installations/{installation['id']}/access_tokens", app_jwt)
    return token["token"]


async def _record(payload: dict[str, Any]) -> None:
    repository_id = payload["repository"]["id"]
    async with db.session() as data:
        gate_obj = await active(data, repository_id)
    match = _CALLBACK.fullmatch(payload["deployment_callback_url"])
    if (gate_obj is None) or (match is None) or (payload["environment"].lower() != gate_obj.environment):
        log.warning(f"Ignoring a deployment protection rule request for repository {repository_id}")
        return
    run_id = int(match.group(1))
    token = await _installation_token(gate_obj.repository)
    run = await _github("GET", f"/repos/{gate_obj.repository}/actions/runs/{run_id}", token)
    if run["status"] != "waiting":
        return
    problem = run_problem(gate_obj, repository_id, run) or await _ref_problem(token, gate_obj, run)
    if problem is not None:
        await _review(token, gate_obj, run_id, "rejected", f"ATR refused this run: {problem}")
        return
    try:
        actor_asf_uid = await ldap.github_to_apache(run["triggering_actor"]["id"])
    except ldap.LookupError:
        actor_asf_uid = None
    async with db.session() as data:
        await data.execute(
            sqlite.insert(sql.GateRequest)
            .values(
                repository_id=repository_id,
                run_id=run_id,
                run_attempt=run["run_attempt"],
                head_sha=run["head_sha"],
                ref=f"refs/{REF_TYPES[gate_obj.ref_type]}/{run['head_branch']}",
                actor=run["triggering_actor"]["login"],
                actor_asf_uid=actor_asf_uid,
                decisions={},
            )
            .on_conflict_do_nothing()
        )
        await data.commit()


async def _ref_problem(token: str, gate_obj: sql.GateConfig, run: dict[str, Any]) -> str | None:
    ref = f"refs/{REF_TYPES[gate_obj.ref_type]}/{urllib.parse.quote(run['head_branch'])}"
    try:
        commit = await _github("GET", f"/repos/{gate_obj.repository}/commits/{ref}", token)
    except aiohttp.ClientResponseError as e:
        if e.status != 422:
            raise
        return f"{run['head_branch']} is not a {gate_obj.ref_type}"
    if commit["sha"] != run["head_sha"]:
        return f"the {gate_obj.ref_type} {run['head_branch']} does not point at the run's commit"
    return None


async def _review(token: str, gate_obj: sql.GateConfig, run_id: int, state: str, comment: str) -> None:
    body = {"environment_name": gate_obj.environment, "state": state, "comment": comment}
    await _github("POST", f"/repos/{gate_obj.repository}/actions/runs/{run_id}/deployment_protection_rule", token, body)
