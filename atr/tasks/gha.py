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
import datetime
import json
import uuid
from collections.abc import Callable
from typing import Any, Final, NoReturn

import aiohttp
import pydantic

import atr.config as config
import atr.db as db
import atr.log as log
import atr.models.results as results
import atr.models.schema as schema
import atr.models.sql as sql

# import atr.shared as shared
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.checks as checks
from atr.models.results import DistributionWorkflowStatus

_BASE_URL: Final[str] = "https://api.github.com/repos"
_IN_PROGRESS_STATUSES: Final[list[str]] = ["in_progress", "queued", "requested", "waiting", "pending", "expected"]
_COMPLETED_STATUSES: Final[list[str]] = ["completed"]
_FAILED_STATUSES: Final[list[str]] = ["failure", "startup_failure"]
_TIMEOUT_S = 60


class DistributionWorkflow(schema.Strict):
    """Arguments for the task to start a Github Actions workflow."""

    namespace: str = schema.description("Namespace to distribute to")
    package: str = schema.description("Package to distribute")
    version: str = schema.description("Version to distribute")
    staging: bool = schema.description("Whether this is a staging distribution")
    project_name: str = schema.description("Project name in ATR")
    version_name: str = schema.description("Version name in ATR")
    phase: str = schema.description("Release phase in ATR")
    asf_uid: str = schema.description("ASF UID of the user triggering the workflow")
    committee_name: str = schema.description("Committee name in ATR")
    platform: str = schema.description("Distribution platform")
    arguments: dict[str, str] = schema.description("Workflow arguments")
    name: str = schema.description("Name of the run")


class WorkflowStatusCheck(schema.Strict):
    run_id: int | None = schema.description("Run ID")
    next_schedule_seconds: int = pydantic.Field(default=0, description="The next scheduled time")
    asf_uid: str = schema.description("ASF UID of the user triggering the workflow")


@checks.with_model(DistributionWorkflow)
async def trigger_workflow(args: DistributionWorkflow, *, task_id: int | None = None) -> results.Results | None:
    unique_id = f"atr-dist-{args.name}-{uuid.uuid4()}"
    try:
        sql_platform = sql.DistributionPlatform[args.platform]
    except KeyError:
        _fail(f"Invalid platform: {args.platform}")
    workflow = f"distribute-{sql_platform.value.gh_slug}.yml"
    payload = {
        "ref": "main",
        "inputs": {
            "atr-id": unique_id,
            "asf-uid": args.asf_uid,
            "project": args.project_name,
            "phase": args.phase,
            "version": args.version_name,
            "distribution-owner-namespace": args.namespace,
            "distribution-package": args.package,
            "distribution-version": args.version,
            "staging": "true" if args.staging else "false",
            **args.arguments,
        },
    }
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {config.get().GITHUB_TOKEN}"}
    log.info(
        f"Triggering Github workflow apache/tooling-actions/{workflow} with args: {
            json.dumps(args.arguments, indent=2)
        }"
    )
    async with aiohttp.ClientSession() as session:
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
        async with storage.write_as_committee_member(args.committee_name, args.asf_uid) as w:
            try:
                await w.workflowstatus.add_workflow_status(
                    workflow, run_id, args.project_name, task_id, status=run.get("status")
                )
            except storage.AccessError as e:
                _fail(f"Failed to record distribution: {e}")
        return results.DistributionWorkflow(
            kind="distribution_workflow", name=args.name, run_id=run_id, url=run.get("html_url", "")
        )


@checks.with_model(WorkflowStatusCheck)
async def status_check(args: WorkflowStatusCheck) -> DistributionWorkflowStatus:
    """Check remote workflow statuses."""

    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {config.get().GITHUB_TOKEN}"}
    log.info("Updating Github workflow statuses from apache/tooling-actions")
    runs = []
    try:
        async with aiohttp.ClientSession() as session:
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
                            if r.get("id") == pending.run_id and r.get("path", "").endswith(f"/{pending.workflow_id}")
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

        # Schedule next update
        await _schedule_next(args)

        return results.DistributionWorkflowStatus(
            kind="distribution_workflow_status",
        )

    except aiohttp.ClientError as e:
        _fail(f"Failed to fetch workflow data from GitHub: {e!s}")
    except Exception as e:
        _fail(f"Unexpected error during workflow status update: {e!s}")


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


#
# async def _record_distribution(
#     committee_name: str,
#     release: str,
#     platform: sql.DistributionPlatform,
#     namespace: str,
#     package: str,
#     version: str,
#     staging: bool,
# ):
#     log.info("Creating distribution record")
#     dd = distribution.Data(
#         platform=platform,
#         owner_namespace=namespace,
#         package=package,
#         version=version,
#         details=False,
#     )
#     async with storage.write_as_committee_member(committee_name=committee_name) as w:
#         try:
#             _dist, _added, _metadata = await w.distributions.record_from_data(release=release, staging=staging, dd=dd)
#         except storage.AccessError as e:
#             _fail(f"Failed to record distribution: {e}")


async def _request_and_retry(
    session: aiohttp.client.ClientSession,
    url: str,
    headers: dict[str, str],
    response_func: Callable[[Any], dict[str, Any] | None],
) -> dict[str, Any] | None:
    for _attempt in range(_TIMEOUT_S * 10):  # timeout_s * 10):
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


async def _schedule_next(args: WorkflowStatusCheck) -> None:
    if args.next_schedule_seconds:
        next_schedule = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=args.next_schedule_seconds)
        await tasks.workflow_update(args.asf_uid, schedule=next_schedule, schedule_next=True)
        log.info(
            f"Scheduled next workflow status update for: {next_schedule.strftime('%Y-%m-%d %H:%M:%S')}",
        )


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
