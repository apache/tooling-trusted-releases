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
import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any, Final, NoReturn

import aiohttp

import atr.config as config
import atr.db as db
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.util as util

_BASE_URL: Final[str] = "https://api.github.com/repos"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
_IN_PROGRESS_STATUSES: Final[list[str]] = ["in_progress", "queued", "requested", "waiting", "pending", "expected"]
_COMPLETED_STATUSES: Final[list[str]] = ["completed"]
_FAILED_STATUSES: Final[list[str]] = ["failure", "startup_failure"]
_TIMEOUT_S = 60


@checks.with_model(args.WorkflowStatusCheck)
async def status_check(task_args: args.WorkflowStatusCheck) -> results.DistributionWorkflowStatus:
    """Check remote workflow statuses."""

    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {config.get().GITHUB_TOKEN}"}
    log.info("Updating Github workflow statuses from apache/tooling-actions")
    runs = []
    try:
        async with util.create_secure_session(timeout=_HTTP_TIMEOUT) as session:
            try:
                async with session.get(
                    f"{_BASE_URL}/apache/tooling-actions/actions/runs?event=workflow_dispatch", headers=headers
                ) as response:
                    response.raise_for_status()
                    resp_json = await response.json()
                    runs = resp_json.get("workflow_runs", [])
            except aiohttp.ClientResponseError as e:
                _fail(f"Failed to lookup GitHub workflows: {e.message} ({e.status})")

        updated_count = 0

        if len(runs) > 0:
            async with db.session() as data:
                pending_runs = await data.workflow_status(status_in=[*_IN_PROGRESS_STATUSES, ""]).all()

                for pending in pending_runs:
                    # Find matching workflow run from GitHub API
                    matching_run = next(
                        (
                            r
                            for r in runs
                            if (r.get("id") == pending.run_id) and r.get("path", "").endswith(f"/{pending.workflow_id}")
                        ),
                        None,
                    )

                    if matching_run:
                        new_status = matching_run.get("status", "")
                        new_message = matching_run.get("conclusion")
                        if new_message == "failure":
                            new_status = "failed"
                            new_message = "GitHub workflow failed"

                        # Update status if it has changed
                        if new_status != pending.status:
                            pending.status = new_status
                            if new_message:
                                pending.message = new_message
                            updated_count += 1
                            log.info(
                                f"Updated workflow {pending.workflow_id} run {pending.run_id} to status {new_status}"
                            )
                # TODO: If we can't find this run ID in the bulk response, we could check it directly by ID in the API
                await data.commit()

        log.info(
            f"Workflow status update completed: updated {updated_count} workflow(s)",
        )

        await tasks.schedule_next(task_args.asf_uid, task_args.next_schedule_seconds, tasks.workflow_update)

        return results.DistributionWorkflowStatus(
            kind="distribution_workflow_status",
        )

    except aiohttp.ClientError as e:
        _fail(f"Failed to fetch workflow data from GitHub: {e!s}")
    except Exception as e:
        _fail(f"Unexpected error during workflow status update: {e!s}")


@checks.with_model(args.DistributionWorkflow)
async def trigger_workflow(
    task_args: args.DistributionWorkflow, *, task_id: int | None = None
) -> results.Results | None:
    unique_id = f"atr-dist-{task_args.name}-{uuid.uuid4()}"
    project = safe.ProjectKey(task_args.project_key)
    safe.VersionKey(task_args.version_key)
    try:
        sql_platform = sql.DistributionPlatform[task_args.platform]
    except KeyError:
        _fail(f"Invalid platform: {task_args.platform}")
    workflow = f"distribute-{sql_platform.value.gh_slug}{'-stg' if task_args.staging else ''}.yml"
    payload = {
        "ref": "main",
        "inputs": {
            "atr-id": unique_id,
            "asf-uid": task_args.asf_uid,
            "task_id": task_id,
            "project": task_args.project_key,
            "phase": task_args.phase,
            "version": task_args.version_key,
            "distribution-owner-namespace": task_args.namespace,
            "distribution-package": task_args.package,
            "distribution-version": task_args.version,
            # **task_args.arguments,
        },
    }

    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {config.get().GITHUB_TOKEN}"}
    log.info(
        f"Triggering Github workflow apache/tooling-actions/{workflow} with args: {
            json.dumps(task_args.arguments, indent=2)
        }"
    )
    async with util.create_secure_session(timeout=_HTTP_TIMEOUT) as session:
        try:
            async with session.post(
                f"{_BASE_URL}/apache/tooling-actions/actions/workflows/{workflow}/dispatches",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
        except aiohttp.ClientResponseError as e:
            _fail(f"Failed to trigger GitHub workflow: {e.message} ({e.status})")

        run, run_id = await _find_triggered_run(session, headers, unique_id)

        if run.get("status") in _FAILED_STATUSES:
            _fail(f"Github workflow apache/tooling-actions/{workflow} run {run_id} failed with error")
        async with storage.write_as_committee_member(task_args.committee_key, task_args.asf_uid) as w:
            try:
                await w.workflowstatus.add_workflow_status(workflow, run_id, project, task_id, status=run.get("status"))
            except storage.AccessError as e:
                _fail(f"Failed to record distribution: {e}")
        return results.DistributionWorkflow(
            kind="distribution_workflow", name=task_args.name, run_id=run_id, url=run.get("html_url", "")
        )


def _fail(message: str) -> NoReturn:
    log.error(message)
    raise RuntimeError(message)


async def _find_triggered_run(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    unique_id: str,
) -> tuple[dict[str, Any], int]:
    """Find the workflow run that was just triggered."""

    def get_run(resp: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (r for r in resp["workflow_runs"] if (r["head_branch"] == "main") and (r["name"] == unique_id)),
            None,
        )

    run = await _request_and_retry(
        session, f"{_BASE_URL}/apache/tooling-actions/actions/runs?event=workflow_dispatch", headers, get_run
    )
    if run is None:
        _fail(f"Failed to find triggered workflow run for {unique_id}")
    run_id: int | None = run.get("id")
    if run_id is None:
        _fail(f"Found run for {unique_id} but run ID is missing")
    return run, run_id


async def _request_and_retry(
    session: aiohttp.client.ClientSession,
    url: str,
    headers: dict[str, str],
    response_func: Callable[[Any], dict[str, Any] | None],
) -> dict[str, Any] | None:
    for _attempt in range(_TIMEOUT_S * 10):
        async with session.get(
            url,
            headers=headers,
        ) as response:
            try:
                response.raise_for_status()
                runs = await response.json()
                data = response_func(runs)
                if not data:
                    await asyncio.sleep(0.1)
                else:
                    return data
            except aiohttp.ClientResponseError as e:
                # We don't raise here as it could be an ephemeral error - if it continues it will return None
                log.error(f"Failure calling Github: {e.message} ({e.status}, attempt {_attempt + 1})")
                await asyncio.sleep(0.1)
    return None


#
# async def _wait_for_completion(
#     session: aiohttp.ClientSession,
#     args: DistributionWorkflow,
#     headers: dict[str, str],
#     run_id: int,
#     unique_id: str,
# ) -> dict[str, Any]:
#     """Wait for a workflow run to complete."""
#
#     def filter_run(resp: dict[str, Any]) -> dict[str, Any] | None:
#         if resp.get("status") not in _IN_PROGRESS_STATUSES:
#             return resp
#         return None
#
#     run = await _request_and_retry(
#         session, f"{_BASE_URL}/{args.owner}/{args.repo}/actions/runs/{run_id}", headers, filter_run
#     )
#     if run is None:
#         _fail(f"Failed to find triggered workflow run for {unique_id}")
#     return run
