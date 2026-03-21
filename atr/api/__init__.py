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
import datetime
import hashlib
import pathlib
from typing import Any, Final, Literal

import aiofiles.os
import asfquart.base as base
import pgpy
import quart
import quart_rate_limiter as rate_limiter
import quart_schema
import sqlalchemy
import sqlmodel
import werkzeug.exceptions as exceptions

import atr.blueprints.api as api
import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.hashes as hashes
import atr.jwtoken as jwtoken
import atr.log as log
import atr.models as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.paths as paths
import atr.principal as principal
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.types as types
import atr.tabulate as tabulate
import atr.user as user
import atr.util as util

# FIXME: we need to return the dumped model instead of the actual pydantic class
#        as otherwise pyright will complain about the return type
#        it would work though, see https://github.com/pgjones/quart-schema/issues/91
#        For now, just explicitly dump the model.

# We implicitly have /api/openapi.json

type DictResponse = tuple[dict[str, Any], int]

ROUTES_MODULE: Final[Literal[True]] = True


@api.typed
@quart_schema.validate_response(models.api.ChecksListResults, 200)
async def checks_list(
    _checks_list: Literal["checks/list"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> DictResponse:
    """
    URL: GET /checks/list/<project_key>/<version_key>

    List checks by project and version.

    Checks are only conducted during the compose a draft phase. This endpoint
    only returns the checks for the most recent draft revision. Once a release
    has been promoted to the vote phase or beyond, the checks returned are
    still those for the compose phase.

    Warning: the check results include results for archive members, so there
    may potentially be thousands or results or more.
    """
    # TODO: We should perhaps paginate this
    async with db.session() as data:
        release_key = sql.release_key(str(project_key), str(version_key))
        release = await data.release(key=release_key).demand(exceptions.NotFound(f"Release {release_key} not found"))
        check_results = await interaction.checks_for(release, caller_data=data)

    return models.api.ChecksListResults(
        endpoint="/checks/list",
        checks=check_results,
        checks_revision=release.unwrap_revision_number,
        current_phase=release.phase,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ChecksListResults, 200)
async def checks_list_revision(
    _checks_list: Literal["checks/list"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
) -> DictResponse:
    """
    URL: GET /checks/list/<project_key>/<version_key>/<revision>

    List checks by project, version, and revision.

    Checks are only conducted during the compose a draft phase. This endpoint
    only returns the checks for the specified draft revision. Once a release
    has been promoted to the vote phase or beyond, the checks returned are
    still those for the specified revision from the compose phase.

    Warning: the check results include results for archive members, so there
    may potentially be thousands or results or more.
    """
    async with db.session() as data:
        release_key = sql.release_key(str(project_key), str(version_key))
        release_result = await data.release(key=release_key).demand(
            exceptions.NotFound(f"Release '{release_key}' does not exist")
        )

        revision_result = await data.revision(release_key=release_key, number=str(revision)).get()
        if revision_result is None:
            raise exceptions.NotFound(f"Revision '{revision}' does not exist for release '{release_key}'")

        check_results = await interaction.checks_for(release_result, revision=revision, caller_data=data)

    return models.api.ChecksListResults(
        endpoint="/checks/list",
        checks=check_results,
        checks_revision=str(revision),
        current_phase=release_result.phase,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ChecksOngoingResults, 200)
async def checks_ongoing(
    _checks_ongoing: Literal["checks/ongoing"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber | None = None,
) -> DictResponse:
    """
    URL: GET /checks/ongoing/<project_key>/<version_key>[/<revision>]

    Count ongoing checks by project, version, and optionally revision.

    Checks are only conducted during the compose a draft phase. This endpoint
    returns the number of ongoing checks for the specified draft revision if
    present, or the most recent draft revision otherwise. A draft release
    cannot be promoted to the vote phase if checks are still ongoing.
    """
    ongoing_tasks_count, _latest_revision = await interaction.tasks_ongoing_revision(project_key, version_key, revision)
    # TODO: Is there a way to return just an int?
    # The ResponseReturnValue type in quart does not allow int
    # And if we use quart.jsonify, we must return web.QuartResponse which quart_schema tries to validate
    # ResponseValue = Union[
    #     "Response",
    #     "WerkzeugResponse",
    #     bytes,
    #     str,
    #     Mapping[str, Any],  # any jsonify-able dict
    #     list[Any],  # any jsonify-able list
    #     Iterator[bytes],
    #     Iterator[str],
    # ]
    return models.api.ChecksOngoingResults(
        endpoint="/checks/ongoing",
        ongoing=ongoing_tasks_count,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.CommitteeGetResults, 200)
async def committee_get(
    _committee_get: Literal["committee/get"],
    name: safe.CommitteeKey,
) -> DictResponse:
    """
    URL: GET /committee/get/<name>

    Get a committee by name.

    The name of the committee is the name without any prefixes or suffixes such
    as "Apache" or "PMC", in lower case, and with hyphens instead of spaces.
    The Apache Simple Example PMC, for example, would have the name
    "simple-example".
    """
    async with db.session() as data:
        committee = await data.committee(key=str(name)).demand(
            exceptions.NotFound(f"Committee '{name!s}' was not found")
        )
    return models.api.CommitteeGetResults(
        endpoint="/committee/get",
        committee=committee,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.CommitteeKeysResults, 200)
async def committee_keys(
    _committee_keys: Literal["committee/keys"],
    name: safe.CommitteeKey,
) -> DictResponse:
    """
    URL: GET /committee/keys/<name>

    List public OpenPGP keys by committee name.

    The name of the committee is the name without any prefixes or suffixes such
    as "Apache" or "PMC", in lower case, and with hyphens instead of spaces.
    The Apache Simple Example PMC, for example, would have the name
    "simple-example".
    """
    async with db.session() as data:
        committee = await data.committee(key=str(name), _public_signing_keys=True).demand(
            exceptions.NotFound(f"Committee '{name!s}' was not found")
        )
    return models.api.CommitteeKeysResults(
        endpoint="/committee/keys",
        keys=committee.public_signing_keys,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.CommitteeProjectsResults, 200)
async def committee_projects(
    _committee_projects: Literal["committee/projects"],
    name: safe.CommitteeKey,
) -> DictResponse:
    """
    URL: GET /committee/projects/<name>

    List projects by committee name.

    The name of the committee is the name without any prefixes or suffixes such
    as "Apache" or "PMC", in lower case, and with hyphens instead of spaces.
    The Apache Simple Example PMC, for example, would have the name
    "simple-example".
    """
    async with db.session() as data:
        committee = await data.committee(key=str(name), _projects=True).demand(
            exceptions.NotFound(f"Committee '{name!s}' was not found")
        )
    return models.api.CommitteeProjectsResults(
        endpoint="/committee/projects",
        projects=committee.projects,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.CommitteesListResults, 200)
async def committees_list(
    _committees_list: Literal["committees/list"],
) -> DictResponse:
    """
    URL: GET /committees/list

    List committees.

    The list of committees is returned in no particular order.
    """
    async with db.session() as data:
        committees = await data.committee().all()
    return models.api.CommitteesListResults(
        endpoint="/committees/list",
        committees=committees,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
async def distribute_ssh_register(
    _distribute_ssh_register: Literal["distribute/ssh/register"],
    data: models.api.DistributeSshRegisterArgs,
) -> DictResponse:
    """
    URL: POST /distribute/ssh/register

    Register an SSH key sent with a corroborating Trusted Publisher JWT,
    validating the requested release is in the correct phase.
    """
    payload, asf_uid, project, release = await interaction.trusted_jwt_for_dist(
        data.publisher,
        data.jwt,
        data.asf_uid,
        interaction.TrustedProjectPhase(data.phase),
        data.project_key,
        data.version,
    )
    async with storage.write_as_committee_member(util.unwrap(project.committee).key, asf_uid) as wacm:
        fingerprint, expires = await wacm.ssh.add_workflow_key(
            payload.actor,
            payload.actor_id,
            release.safe_project_key,
            data.ssh_key,
            payload,
        )

    return models.api.DistributeSshRegisterResults(
        endpoint="/distribute/ssh/register",
        fingerprint=fingerprint,
        project=release.safe_project_key,
        expires=expires,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.DistributionRecordResults, 200)
async def distribution_record(
    _distribution_record: Literal["distribution/record"],
    data: models.api.DistributionRecordArgs,
) -> DictResponse:
    """
    URL: POST /distribution/record

    Record a manual distribution.
    """
    asf_uid = _jwt_asf_uid()
    async with db.session() as db_data:
        release = await db_data.release(
            project_key=str(data.project),
            version=str(data.version),
        ).demand(exceptions.NotFound(f"Release {data.project!s} {data.version!s} not found"))
    if release.committee is None:
        raise exceptions.NotFound(f"Release {release.key} has no committee")
    dd = models.distribution.Data(
        platform=data.platform,
        owner_namespace=data.distribution_owner_namespace,
        package=data.distribution_package,
        version=data.distribution_version,
        details=data.details,
    )
    async with storage.write(asf_uid) as write:
        wacm = write.as_committee_member(release.committee.key)
        _dist, _added, metadata = await wacm.distributions.record_from_data(
            release.safe_key,
            data.staging,
            dd,
        )
        if metadata is None:
            raise exceptions.FailedDependency("Distribution could not be found, ATR will retry this automatically")

    return models.api.DistributionRecordResults(
        endpoint="/distribution/record",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
async def distribution_record_from_workflow(
    _distribute_record_from_workflow: Literal["distribute/record_from_workflow"],
    data: models.api.DistributionRecordFromWorkflowArgs,
) -> DictResponse:
    """
    URL: POST /distribute/record_from_workflow

    Record the result of an automated distribution from the GH tooling-actions workflow.
    """
    _payload, asf_uid, _project, release = await interaction.trusted_jwt_for_dist(
        data.publisher,
        data.jwt,
        data.asf_uid,
        interaction.TrustedProjectPhase(data.phase),
        data.project,
        data.version,
    )
    # TODO: Split the below code into a new function and reuse in /publisher and /distribution / record.
    if release.committee is None:
        raise exceptions.NotFound(f"Release {release.key} has no committee")
    dd = models.distribution.Data(
        platform=data.platform,
        owner_namespace=data.distribution_owner_namespace,
        package=data.distribution_package,
        version=data.distribution_version,
        details=data.details,
    )
    async with storage.write_as_committee_member(release.committee.key, asf_uid) as wacm:
        _dist, _added, metadata = await wacm.distributions.record_from_data(
            release.safe_key, data.staging, dd, allow_retries=True
        )
        if metadata is None:
            log.warning("Distribution could not be found, ATR will retry this automatically")

    return models.api.DistributionRecordFromWorkflowResults(
        endpoint="/distribute/record_from_workflow",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.IgnoreAddResults, 200)
async def ignore_add(
    _ignore_add: Literal["ignore/add"],
    data: models.api.IgnoreAddArgs,
) -> DictResponse:
    """
    URL: POST /ignore/add

    Add a check ignore.
    """
    asf_uid = _jwt_asf_uid()
    if not any(data.model_dump().values()):
        raise exceptions.BadRequest("At least one field must be provided")
    async with storage.write(asf_uid) as write:
        wacm = await write.as_project_committee_member(data.project_key)
        await wacm.checks.ignore_add(
            data.project_key,
            data.release_glob,
            data.revision_number,
            data.checker_glob,
            data.primary_rel_path_glob,
            data.member_rel_path_glob,
            data.status,
            data.message_glob,
        )
    return models.api.IgnoreAddResults(
        endpoint="/ignore/add",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.IgnoreDeleteResults, 200)
async def ignore_delete(
    _ignore_delete: Literal["ignore/delete"],
    data: models.api.IgnoreDeleteArgs,
) -> DictResponse:
    """
    URL: POST /ignore/delete

    Delete a check ignore.
    """
    asf_uid = _jwt_asf_uid()
    if not any(data.model_dump().values()):
        raise exceptions.BadRequest("At least one field must be provided")
    async with storage.write(asf_uid) as write:
        wacm = await write.as_project_committee_member(data.project_key)
        # TODO: This is more like discard
        # Should potentially check for rowcount, and raise an error if it's 0
        await wacm.checks.ignore_delete(data.id)
    return models.api.IgnoreDeleteResults(
        endpoint="/ignore/delete",
        success=True,
    ).model_dump(mode="json"), 200


# TODO: Rename to ignores
@api.typed
@quart_schema.validate_response(models.api.IgnoreListResults, 200)
async def ignore_list(
    _ignore_list: Literal["ignore/list"],
    project_key: safe.ProjectKey,
) -> DictResponse:
    """
    URL: GET /ignore/list/<project_key>

    List ignores by project name.
    """
    async with db.session() as data:
        await data.project(key=str(project_key)).demand(exceptions.NotFound())
        ignores = await data.check_result_ignore(project_key=str(project_key)).all()
    return models.api.IgnoreListResults(
        endpoint="/ignore/list",
        ignores=ignores,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
async def jwt_create(
    _jwt_create: Literal["jwt/create"],
    data: models.api.JwtCreateArgs,
) -> DictResponse:
    """
    URL: POST /jwt/create

    Create a JWT.

    The payload must include a valid PAT.
    """
    # Expects {"asfuid": "uid", "pat": "pat-token"}
    # Returns {"asfuid": "uid", "jwt": "jwt-token"}
    asf_uid = data.asfuid
    log.set_asf_uid(asf_uid)
    async with storage.write(asf_uid) as write:
        wafc = write.as_foundation_committer()
        jwt = await wafc.tokens.issue_jwt(data.pat)

    return models.api.JwtCreateResults(
        endpoint="/jwt/create",
        asfuid=data.asfuid,
        jwt=jwt,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.KeyAddResults, 200)
async def key_add(
    _key_add: Literal["key/add"],
    data: models.api.KeyAddArgs,
) -> DictResponse:
    """
    URL: POST /key/add

    Add a public OpenPGP key.

    Once associated with the specified committees, the key will appear in the
    automatically generated KEYS file for each committee.
    """
    asf_uid = _jwt_asf_uid()
    selected_committee_keys = data.committees

    async with storage.write(asf_uid) as write:
        wafc = write.as_foundation_committer()
        ocr: outcome.Outcome[types.Key] = await wafc.keys.ensure_stored_one(data.key)
        key = ocr.result_or_raise()

        for selected_committee_key in selected_committee_keys:
            wacm = write.as_committee_member(selected_committee_key)
            oc: outcome.Outcome[types.LinkedCommittee] = await wacm.keys.associate_fingerprint(
                key.key_model.fingerprint
            )
            oc.result_or_raise()

    return models.api.KeyAddResults(
        endpoint="/key/add",
        fingerprint=key.key_model.fingerprint.upper(),
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.KeyDeleteResults, 200)
async def key_delete(
    _key_delete: Literal["key/delete"],
    data: models.api.KeyDeleteArgs,
) -> DictResponse:
    """
    URL: POST /key/delete

    Delete a public OpenPGP key.

    Warning: we plan to change how key deletion works.
    """
    asf_uid = _jwt_asf_uid()
    fingerprint = data.fingerprint.lower()

    outcomes = outcome.List[str]()
    async with storage.write(asf_uid) as write:
        wafc = write.as_foundation_committer()
        # audit_guidance fingerprint ownership verified in storage layer via authenticated user's asfuid
        oc: outcome.Outcome[sql.PublicSigningKey] = await wafc.keys.delete_key(fingerprint)
        key = oc.result_or_raise()

        for committee in key.committees:
            wacm = write.as_committee_member_outcome(committee.key).result_or_none()
            if wacm is None:
                continue
            outcomes.append(await wacm.keys.autogenerate_keys_file())
    # TODO: Add error outcomes as warnings to the response

    return models.api.KeyDeleteResults(
        endpoint="/key/delete",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@quart_schema.validate_response(models.api.KeyGetResults, 200)
async def key_get(
    _key_get: Literal["key/get"],
    fingerprint: unsafe.UnsafeStr,
) -> DictResponse:
    """
    URL: GET /key/get/<fingerprint>

    Get a public OpenPGP key by fingerprint.

    All public OpenPGP keys stored within the database are accessible.
    """
    async with db.session() as data:
        key = await data.public_signing_key(fingerprint=str(fingerprint).lower()).demand(
            exceptions.NotFound(f"Key '{fingerprint!s}' not found")
        )
    return models.api.KeyGetResults(
        endpoint="/key/get",
        key=key,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.KeysUploadResults, 200)
async def keys_upload(
    _keys_upload: Literal["keys/upload"],
    data: models.api.KeysUploadArgs,
) -> DictResponse:
    """
    URL: POST /keys/upload

    Upload a public OpenPGP KEYS file.
    """
    asf_uid = _jwt_asf_uid()
    filetext = data.filetext
    selected_committee_key = data.committee
    async with storage.write(asf_uid) as write:
        wacm = write.as_committee_member(selected_committee_key)
        outcomes: outcome.List[types.Key] = await wacm.keys.ensure_associated(filetext)

        # TODO: It would be nice to serialise the actual outcomes
        # Or, perhaps better yet, to have a standard datatype mapping
        # This would be specified in models.api, then imported into storage.types
        # Or perhaps it should go in models.storage or models.outcomes
        api_outcomes = []
        for oc in outcomes.outcomes():
            api_outcome: models.api.KeysUploadOutcome | None = None
            match oc:
                case outcome.Result(result):
                    api_outcome = models.api.KeysUploadResult(
                        status="success",
                        key=result.key_model,
                    )
                case outcome.Error(error):
                    # TODO: This branch means we must improve the return type
                    match error:
                        case types.PublicKeyError() as pke:
                            api_outcome = models.api.KeysUploadException(
                                status="error",
                                key=pke.key.key_model,
                                error=str(pke),
                                error_type=type(pke).__name__,
                            )
                        case _ as e:
                            api_outcome = models.api.KeysUploadException(
                                status="error",
                                key=None,
                                error=str(e),
                                error_type=type(e).__name__,
                            )
            # Type checker is sure that it can no longer be None
            api_outcomes.append(api_outcome)
    return models.api.KeysUploadResults(
        endpoint="/keys/upload",
        results=api_outcomes,
        success_count=outcomes.result_count,
        error_count=outcomes.error_count,
        submitted_committee=selected_committee_key,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@quart_schema.validate_response(models.api.KeysUserResults, 200)
async def keys_user(
    _keys_user: Literal["keys/user"],
    asf_uid: unsafe.UnsafeStr,
) -> DictResponse:
    """
    URL: GET /keys/user/<asf_uid>

    List public OpenPGP keys by the ASF UID of a user.
    """
    async with db.session() as data:
        keys = await data.public_signing_key(apache_uid=str(asf_uid)).all()
    return models.api.KeysUserResults(
        endpoint="/keys/user",
        keys=keys,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.PolicyGetResults, 200)
async def policy_get(
    _policy_get: Literal["policy/get"],
    project_key: safe.ProjectKey,
) -> DictResponse:
    """
    URL: GET /policy/get/<project_key>

    Get project policy by name.

    Returns the release policy settings for a project.
    If no policy has been configured, defaults are returned.
    """
    async with db.session() as data:
        project = await data.project(key=str(project_key), _release_policy=True, _committee=True).demand(
            exceptions.NotFound()
        )
    return models.api.PolicyGetResults(
        endpoint="/policy/get",
        project_key=project.safe_key,
        policy_announce_release_subject=project.policy_announce_release_subject,
        policy_announce_release_template=project.policy_announce_release_template,
        policy_binary_artifact_paths=project.policy_binary_artifact_paths,
        policy_github_compose_workflow_path=project.policy_github_compose_workflow_path,
        policy_github_finish_workflow_path=project.policy_github_finish_workflow_path,
        policy_github_repository_branch=project.policy_github_repository_branch,
        policy_github_repository_name=project.policy_github_repository_name,
        policy_github_vote_workflow_path=project.policy_github_vote_workflow_path,
        policy_license_check_mode=project.policy_license_check_mode,
        policy_mailto_addresses=project.policy_mailto_addresses,
        policy_manual_vote=project.policy_manual_vote,
        policy_min_hours=project.policy_min_hours,
        policy_pause_for_rm=project.policy_pause_for_rm,
        policy_preserve_download_files=project.policy_preserve_download_files,
        policy_release_checklist=project.policy_release_checklist,
        policy_source_artifact_paths=project.policy_source_artifact_paths,
        policy_start_vote_subject=project.policy_start_vote_subject,
        policy_start_vote_template=project.policy_start_vote_template,
        policy_strict_checking=project.policy_strict_checking,
        policy_vote_comment_template=project.policy_vote_comment_template,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.PolicyUpdateResults, 200)
async def policy_update(
    _policy_update: Literal["policy/update"],
    data: models.api.PolicyUpdateArgs,
) -> DictResponse:
    """
    URL: POST /policy/update

    Update release policy fields for a project.

    Only fields present in the request body are modified.
    """
    asf_uid = _jwt_asf_uid()
    try:
        async with storage.write_as_project_committee_member(data.project, asf_uid) as wacm:
            await wacm.policy.edit_policy(data.project, data)
    except (storage.AccessError, ValueError) as e:
        raise exceptions.BadRequest(str(e))
    return models.api.PolicyUpdateResults(
        endpoint="/policy/update",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ProjectGetResults, 200)
async def project_get(
    _project_get: Literal["project/get"],
    project_key: safe.ProjectKey,
) -> DictResponse:
    """
    URL: GET /project/get/<project_key>

    Get a project by name.
    """
    async with db.session() as data:
        project = await data.project(key=str(project_key)).demand(exceptions.NotFound())
    return models.api.ProjectGetResults(
        endpoint="/project/get",
        project=project,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ProjectReleasesResults, 200)
async def project_releases(
    _project_releases: Literal["project/releases"],
    project_key: safe.ProjectKey,
) -> DictResponse:
    """
    URL: GET /project/releases/<project_key>

    List releases by project name.
    """
    async with db.session() as data:
        await data.project(key=str(project_key)).demand(exceptions.NotFound())
        releases = await data.release(project_key=str(project_key)).all()
    return models.api.ProjectReleasesResults(
        endpoint="/project/releases",
        releases=releases,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ProjectsListResults, 200)
async def projects_list(
    _projects_list: Literal["projects/list"],
) -> DictResponse:
    """
    URL: GET /projects/list

    List projects.
    """
    # TODO: Add pagination?
    async with db.session() as data:
        projects = await data.project().all()
    return models.api.ProjectsListResults(
        endpoint="/projects/list",
        projects=projects,
    ).model_dump(mode="json"), 200


@api.typed
async def publisher_distribution_record(
    _publisher_distribution_record: Literal["publisher/distribution/record"],
    data: models.api.PublisherDistributionRecordArgs,
) -> DictResponse:
    """
    URL: POST /publisher/distribution/record

    Record a distribution with a corroborating Trusted Publisher JWT.
    """
    try:
        _payload, asf_uid, project = await interaction.trusted_jwt(
            data.publisher,
            data.jwt,
            interaction.TrustedProjectPhase.FINISH,
        )
    except interaction.ReleasePolicyNotFoundError:
        # TODO: We could perform a more advanced query with multiple in_ statements
        _payload, asf_uid, project = await interaction.trusted_jwt(
            data.publisher,
            data.jwt,
            interaction.TrustedProjectPhase.COMPOSE,
        )
    async with db.session() as db_data:
        release = await db_data.release(
            project_key=project.key,
            version=str(data.version),
        ).demand(exceptions.NotFound(f"Release {project.key} {data.version!s} not found"))
    if release.committee is None:
        raise exceptions.NotFound(f"Release {release.key} has no committee")
    dd = models.distribution.Data(
        platform=data.platform,
        owner_namespace=data.distribution_owner_namespace,
        package=data.distribution_package,
        version=data.distribution_version,
        details=data.details,
    )
    async with storage.write(asf_uid) as write:
        wacm = write.as_committee_member(release.committee.key)
        await wacm.distributions.record_from_data(
            release.safe_key,
            data.staging,
            dd,
        )

    return models.api.PublisherDistributionRecordResults(
        endpoint="/publisher/distribution/record",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
async def publisher_release_announce(
    _publisher_release_announce: Literal["publisher/release/announce"],
    data: models.api.PublisherReleaseAnnounceArgs,
) -> DictResponse:
    """
    URL: POST /publisher/release/announce

    Announce a release with a corroborating Trusted Publisher JWT.
    """
    _payload, asf_uid, project = await interaction.trusted_jwt(
        data.publisher,
        data.jwt,
        interaction.TrustedProjectPhase.FINISH,
    )
    try:
        # TODO: Add defaults
        committee = util.unwrap(project.committee)
        async with storage.write_as_committee_member(committee.key, asf_uid) as wacm:
            await wacm.announce.release(
                project_key=project.safe_key,
                version_key=data.version,
                preview_revision_number=data.revision,
                recipient=data.email_to,
                body=data.body,
                download_path_suffix=data.path_suffix,
                asf_uid=asf_uid,
                fullname=asf_uid,
            )
    except storage.AccessError as e:
        raise exceptions.BadRequest(str(e))

    return models.api.PublisherReleaseAnnounceResults(
        endpoint="/publisher/release/announce",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
async def publisher_ssh_register(
    _publisher_ssh_register: Literal["publisher/ssh/register"],
    data: models.api.PublisherSshRegisterArgs,
) -> DictResponse:
    """
    URL: POST /publisher/ssh/register

    Register an SSH key sent with a corroborating Trusted Publisher JWT.
    """
    payload, asf_uid, project = await interaction.trusted_jwt(
        data.publisher, data.jwt, interaction.TrustedProjectPhase.COMPOSE
    )
    async with storage.write_as_committee_member(util.unwrap(project.committee).key, asf_uid) as wacm:
        fingerprint, expires = await wacm.ssh.add_workflow_key(
            payload.actor,
            payload.actor_id,
            project.safe_key,
            data.ssh_key,
            payload,
        )

    return models.api.PublisherSshRegisterResults(
        endpoint="/publisher/ssh/register",
        fingerprint=fingerprint,
        project=project.safe_key,
        expires=expires,
    ).model_dump(mode="json"), 200


@api.typed
async def publisher_vote_resolve(
    _publisher_vote_resolve: Literal["publisher/vote/resolve"],
    data: models.api.PublisherVoteResolveArgs,
) -> DictResponse:
    """
    URL: POST /publisher/vote/resolve

    Resolve a vote with a corroborating Trusted Publisher JWT.
    """
    # TODO: Need to be able to resolve and make the release immutable
    _payload, asf_uid, project = await interaction.trusted_jwt(
        data.publisher,
        data.jwt,
        interaction.TrustedProjectPhase.VOTE,
    )
    async with storage.write_as_project_committee_member(project.safe_key, asf_uid) as wacm:
        # TODO: Get fullname and use instead of asf_uid
        # TODO: Add resolution templating to atr.construct
        _release, _voting_round, _success_message, _error_message = await wacm.vote.resolve(
            project.safe_key,
            data.version,
            data.resolution,
            asf_uid,
            f"The vote {data.resolution}.",
        )

    return models.api.PublisherVoteResolveResults(
        endpoint="/publisher/vote/resolve",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.ReleaseAnnounceResults, 201)
async def release_announce(
    _release_announce: Literal["release/announce"],
    data: models.api.ReleaseAnnounceArgs,
) -> DictResponse:
    """
    URL: POST /release/announce

    Announce a release.

    After a vote on a release has passed, if everything is in order and all
    paths are correct, the release can be announced. This will send an email to
    the specified announcement address, and promote the release to the finished
    release phase. Once announced, a release is final and cannot be changed.
    """
    asf_uid = _jwt_asf_uid()

    try:
        async with storage.write_as_project_committee_member(data.project, asf_uid) as wacm:
            # TODO: Get fullname and use it instead of asf_uid
            await wacm.announce.release(
                project_key=data.project,
                version_key=data.version,
                preview_revision_number=data.revision,
                recipient=data.email_to,
                body=data.body,
                download_path_suffix=data.path_suffix,
                asf_uid=asf_uid,
                fullname=asf_uid,
            )
    except storage.AccessError as e:
        raise exceptions.BadRequest(str(e))

    return models.api.ReleaseAnnounceResults(
        endpoint="/release/announce",
        success=True,
    ).model_dump(mode="json"), 201


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.ReleaseCreateResults, 201)
async def release_create(
    _release_create: Literal["release/create"],
    data: models.api.ReleaseCreateArgs,
) -> DictResponse:
    """
    URL: POST /release/create

    Create a release.

    Release are created as a draft, which must be composed.
    """
    asf_uid = _jwt_asf_uid()

    try:
        async with storage.write(asf_uid) as write:
            wacp = await write.as_project_committee_participant(data.project)
            release, _project = await wacp.release.start(data.project, data.version)
    except storage.AccessError as e:
        raise exceptions.BadRequest(str(e))

    return models.api.ReleaseCreateResults(
        endpoint="/release/create",
        release=release,
    ).model_dump(mode="json"), 201


# TODO: Duplicates the below
@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.ReleaseDeleteResults, 200)
async def release_delete(
    _release_delete: Literal["release/delete"],
    data: models.api.ReleaseDeleteArgs,
) -> DictResponse:
    """
    URL: POST /release/delete

    Delete a release.
    """
    asf_uid = _jwt_asf_uid()
    if not user.is_admin(asf_uid):
        raise exceptions.Forbidden("You do not have permission to delete a release")

    async with storage.write(asf_uid) as write:
        waca = await write.as_project_committee_admin(data.project)
        error = await waca.release.delete(data.project, data.version)
        # Ensure that deletion errors are reported to the user
        if error is not None:
            raise RuntimeError(error)
    return models.api.ReleaseDeleteResults(
        endpoint="/release/delete",
        deleted=True,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ReleaseGetResults, 200)
async def release_get(
    _release_get: Literal["release/get"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> DictResponse:
    """
    URL: GET /release/get/<project_key>/<version_key>

    Get a release by project and version.
    """
    async with db.session() as data:
        release_key = sql.release_key(str(project_key), str(version_key))
        release = await data.release(key=release_key).demand(exceptions.NotFound())
    return models.api.ReleaseGetResults(
        endpoint="/release/get",
        release=release,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ReleasePathsResults, 200)
async def release_paths(
    _release_paths: Literal["release/paths"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber | None = None,
) -> DictResponse:
    """
    URL: GET /release/paths/<project_key>/<version_key>[/<revision>]

    List paths in a release by project and version.
    """
    async with db.session() as data:
        release_key = sql.release_key(str(project_key), str(version_key))
        release = await data.release(key=release_key).demand(exceptions.NotFound())
        if revision is None:
            dir_path = paths.release_directory(release)
        else:
            await data.revision(release_key=release_key, number=str(revision)).demand(exceptions.NotFound())
            dir_path = paths.release_directory_version(release) / str(revision)
    if not (await aiofiles.os.path.isdir(dir_path)):
        raise exceptions.NotFound("Files not found")
    files: list[str] = [str(path) for path in [p async for p in util.paths_recursive(dir_path)]]
    files.sort()
    return models.api.ReleasePathsResults(
        endpoint="/release/paths",
        rel_paths=files,
    ).model_dump(mode="json"), 200


@api.typed
@quart_schema.validate_response(models.api.ReleaseRevisionsResults, 200)
async def release_revisions(
    _release_revisions: Literal["release/revisions"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> DictResponse:
    """
    URL: GET /release/revisions/<project_key>/<version_key>

    List revisions by project and version.
    """
    async with db.session() as data:
        release_key = sql.release_key(str(project_key), str(version_key))
        revisions = await data.revision(release_key=release_key).all()
    if not isinstance(revisions, list):
        revisions = list(revisions)
    revisions.sort(key=lambda rev: rev.number)
    return models.api.ReleaseRevisionsResults(
        endpoint="/release/revisions",
        revisions=revisions,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.ReleaseUploadResults, 201)
async def release_upload(
    _release_upload: Literal["release/upload"],
    data: models.api.ReleaseUploadArgs,
) -> DictResponse:
    """
    URL: POST /release/upload

    Upload a file to a release.
    """
    asf_uid = _jwt_asf_uid()

    # async with db.session() as db_data:
    #     project = await db_data.project(name=data.project, _committee=True).demand(exceptions.NotFound())
    #     # TODO: user.is_participant(project, asf_uid)
    #     if not (user.is_committee_member(project.committee, asf_uid) or user.is_admin(asf_uid)):
    #         raise exceptions.Forbidden("You do not have permission to upload to this project")

    async with storage.write(asf_uid) as write:
        wacp = await write.as_project_committee_participant(data.project)
        result = await wacp.release.upload_file(data)
    if isinstance(result, sql.Quarantined):
        return {
            "endpoint": "/release/upload",
            "quarantined": True,
            "message": "Upload received. Archive validation in progress.",
        }, 202
    return models.api.ReleaseUploadResults(
        endpoint="/release/upload",
        revision=result,
    ).model_dump(mode="json"), 201


@api.typed
@quart_schema.validate_response(models.api.ReleasesListResults, 200)
async def releases_list(
    _releases_list: Literal["releases/list"],
    query_args: models.api.ReleasesListQuery,
) -> DictResponse:
    """
    URL: GET /releases/list

    List releases.

    The list of releases is paged and can be filtered by phase.
    """
    _pagination_args_validate(query_args)
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        statement = sqlmodel.select(sql.Release)

        if query_args.phase:
            try:
                phase_value = sql.ReleasePhase(query_args.phase)
            except ValueError:
                raise exceptions.BadRequest(f"Invalid phase: {query_args.phase}")
            statement = statement.where(sql.Release.phase == phase_value)

        statement = (
            statement.order_by(via(sql.Release.created).desc()).limit(query_args.limit).offset(query_args.offset)
        )

        paged_releases = (await data.execute(statement)).scalars().all()

        count_stmt = sqlalchemy.select(sqlalchemy.func.count(via(sql.Release.key)))
        if query_args.phase:
            phase_value = sql.ReleasePhase(query_args.phase) if query_args.phase else None
            if phase_value is not None:
                count_stmt = count_stmt.where(via(sql.Release.phase) == phase_value)

        count = (await data.execute(count_stmt)).scalar_one()

    return models.api.ReleasesListResults(
        endpoint="/releases/list",
        data=paged_releases,
        count=count,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.SignatureProvenanceResults, 200)
async def signature_provenance(
    _signature_provenance: Literal["signature/provenance"],
    data: models.api.SignatureProvenanceArgs,
) -> DictResponse:
    """
    URL: POST /signature/provenance

    Get the provenance of a signature.
    """
    # POST because this uses significant computation and I/O
    # We receive a file name and an SHA3-256 hash
    # From these we find which committee(s) published the file with a signature
    # Then we deliver the appropriate signing key from the KEYS file(s)
    # And the URL of the KEYS file(s) for them to check

    signing_keys: list[models.api.SignatureProvenanceKey] = []
    conf = config.get()
    host = conf.APP_HOST

    signature_asc_data = data.signature_asc_text
    sig = pgpy.PGPSignature.from_blob(signature_asc_data)

    if not hasattr(sig, "signer_fingerprint"):
        raise exceptions.NotFound("No signer fingerprint found")

    signer_fingerprint = getattr(sig, "signer_fingerprint").lower()
    async with db.session() as db_data:
        key = await db_data.public_signing_key(
            fingerprint=signer_fingerprint,
            _committees=True,
        ).demand(
            exceptions.NotFound(
                f"Key with fingerprint {signer_fingerprint} not found",
            )
        )

    downloads_dir = paths.get_downloads_dir()
    matched_committee_keys = await _match_committee_keys(key.committees, paths.get_finished_dir(), data)

    for matched_committee_key in matched_committee_keys:
        keys_file_path = downloads_dir / matched_committee_key / "KEYS"
        async with aiofiles.open(keys_file_path, "rb") as f:
            keys_file_data = await f.read()
        keys_file_sha3_256 = hashes.compute_sha3_256(keys_file_data)
        signing_keys.append(
            models.api.SignatureProvenanceKey(
                committee=matched_committee_key,
                keys_file_url=f"https://{host}/downloads/{matched_committee_key}/KEYS",
                keys_file_sha3_256=keys_file_sha3_256,
            )
        )

    if not signing_keys:
        raise exceptions.NotFound("No signing keys found")

    return models.api.SignatureProvenanceResults(
        endpoint="/signature/provenance",
        fingerprint=signer_fingerprint,
        key_asc_text=key.ascii_armored_key,
        committees_with_artifact=signing_keys,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.SshKeyAddResults, 201)
async def ssh_key_add(
    _ssh_key_add: Literal["ssh-key/add"],
    data: models.api.SshKeyAddArgs,
) -> DictResponse:
    """
    URL: POST /ssh-key/add

    Add an SSH key.

    An SSH key is associated with a single user.
    """
    asf_uid = _jwt_asf_uid()
    async with storage.write(asf_uid) as write:
        wafc = write.as_foundation_committer()
        fingerprint = await wafc.ssh.add_key(data.text)
    return models.api.SshKeyAddResults(
        endpoint="/ssh-key/add",
        fingerprint=fingerprint,
    ).model_dump(mode="json"), 201


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.SshKeyDeleteResults, 201)
async def ssh_key_delete(
    _ssh_key_delete: Literal["ssh-key/delete"],
    data: models.api.SshKeyDeleteArgs,
) -> DictResponse:
    """
    URL: POST /ssh-key/delete

    Delete an SSH key.

    An SSH key can only be deleted by the user who owns it.
    """
    asf_uid = _jwt_asf_uid()
    async with storage.write(asf_uid) as write:
        wafc = write.as_foundation_committer()
        await wafc.ssh.delete_key(data.fingerprint)
    return models.api.SshKeyDeleteResults(
        endpoint="/ssh-key/delete",
        success=True,
    ).model_dump(mode="json"), 201


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
async def ssh_keys_list(
    _ssh_keys_list: Literal["ssh-keys/list"],
    asf_uid: unsafe.UnsafeStr,
    query_args: models.api.SshKeysListQuery,
) -> DictResponse:
    """
    URL: GET /ssh-keys/list/<asf_uid>

    List SSH keys by ASF UID.
    """
    _pagination_args_validate(query_args)
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        statement = (
            sqlmodel.select(sql.SSHKey)
            .where(sql.SSHKey.asf_uid == str(asf_uid))
            .limit(query_args.limit)
            .offset(query_args.offset)
            .order_by(via(sql.SSHKey.fingerprint).asc())
        )
        paged_keys = (await data.execute(statement)).scalars().all()

        count_stmt = sqlalchemy.select(sqlalchemy.func.count(via(sql.SSHKey.fingerprint)))
        count = (await data.execute(count_stmt)).scalar_one()

    return models.api.SshKeysListResults(
        endpoint="/ssh-keys/list",
        data=paged_keys,
        count=count,
    ).model_dump(mode="json"), 200


@api.typed
async def tasks_list(
    _tasks_list: Literal["tasks/list"],
    query_args: models.api.TasksListQuery,
) -> DictResponse:
    """
    URL: GET /tasks/list

    List tasks.
    """
    _pagination_args_validate(query_args)
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        statement = sqlmodel.select(sql.Task).limit(query_args.limit).offset(query_args.offset)
        if query_args.status:
            if query_args.status not in sql.TaskStatus:
                raise exceptions.BadRequest(f"Invalid status: {query_args.status}")
            statement = statement.where(sql.Task.status == query_args.status)
        statement = statement.order_by(via(sql.Task.id).desc())
        paged_tasks = (await data.execute(statement)).scalars().all()
        count_statement = sqlalchemy.select(sqlalchemy.func.count(via(sql.Task.id)))
        if query_args.status:
            count_statement = count_statement.where(via(sql.Task.status) == query_args.status)
        count = (await data.execute(count_statement)).scalar_one()
    return models.api.TasksListResults(
        endpoint="/tasks/list",
        data=paged_tasks,
        count=count,
    ).model_dump(mode="json"), 200


@api.typed
async def update_distribution_task_status(
    _distribute_task_status: Literal["distribute/task/status"],
    data: models.api.DistributeStatusUpdateArgs,
) -> DictResponse:
    """
    URL: POST /distribute/task/status

    Update the status of a distribution task
    """
    _payload, _asf_uid = await interaction.validate_trusted_jwt(data.publisher, data.jwt)
    async with db.session() as db_data:
        status = await db_data.workflow_status(
            workflow_id=data.workflow,
            project_key=str(data.project_key),
            run_id=int(data.run_id),
        ).demand(exceptions.NotFound(f"Workflow {data.workflow} not found"))
        status.status = data.status
        status.message = data.message
        await db_data.commit()
    return models.api.DistributeStatusUpdateResults(
        endpoint="/distribute/task/status",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.UserInfoResults, 200)
async def user_info(
    _user_info: Literal["user/info"],
) -> DictResponse:
    """
    URL: GET /user/info

    Get information about a user.
    """
    asf_uid = _jwt_asf_uid()
    authorisation = await principal.Authorisation(asf_uid)
    participant_of = authorisation.participant_of()
    member_of = authorisation.member_of()
    return models.api.UserInfoResults(
        endpoint="/user/info",
        participant_of=list(participant_of),
        member_of=list(member_of),
    ).model_dump(mode="json"), 200


@api.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
@quart_schema.validate_response(models.api.UsersListResults, 200)
async def users_list(
    _users_list: Literal["users/list"],
) -> DictResponse:
    """
    URL: GET /users/list

    List known users.

    This is not a list of all ASF users, but only those known to ATR.
    """
    # It is not even a list of users who have logged in to ATR
    # Only those who has stored certain kinds of data:
    # PersonalAccessToken.asfuid
    # SSHKey.asf_uid
    # PublicSigningKey.apache_uid
    # Revision.asfuid
    async with db.session() as data:
        # TODO: Combine these queries
        via = sql.validate_instrumented_attribute
        result = await data.execute(sqlalchemy.select(via(sql.PersonalAccessToken.asfuid)).distinct())
        pat_uids = set(result.scalars().all())

        result = await data.execute(sqlalchemy.select(via(sql.SSHKey.asf_uid)).distinct())
        ssh_uids = set(result.scalars().all())

        result = await data.execute(sqlalchemy.select(via(sql.PublicSigningKey.apache_uid)).distinct())
        public_signing_uids = set(result.scalars().all())

        result = await data.execute(sqlalchemy.select(via(sql.Revision.asfuid)).distinct())
        revision_uids = set(result.scalars().all())

        users = pat_uids | ssh_uids | public_signing_uids | revision_uids
        users -= {None}
    return models.api.UsersListResults(
        endpoint="/users/list",
        users=sorted(users),
    ).model_dump(mode="json"), 200


# TODO: Add endpoints to allow users to vote
@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.VoteResolveResults, 200)
async def vote_resolve(
    _vote_resolve: Literal["vote/resolve"],
    data: models.api.VoteResolveArgs,
) -> DictResponse:
    """
    URL: POST /vote/resolve

    Resolve a vote.

    A vote can be resolved by passing or failing.
    """
    asf_uid = _jwt_asf_uid()
    # try:
    async with storage.write_as_project_committee_member(data.project, asf_uid) as wacm:
        # TODO: Get fullname and use instead of asf_uid
        # TODO: Add resolution templating to atr.construct
        _release, _voting_round, _success_message, _error_message = await wacm.vote.resolve(
            data.project,
            data.version,
            data.resolution,
            asf_uid,
            f"The vote {data.resolution}.",
        )
    # except Exception as e:
    #     import atr.log as log
    #     import traceback
    #     log.info(traceback.format_exc())
    #     raise exceptions.BadRequest(str(e))

    return models.api.VoteResolveResults(
        endpoint="/vote/resolve",
        success=True,
    ).model_dump(mode="json"), 200


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.VoteStartResults, 201)
async def vote_start(
    _vote_start: Literal["vote/start"],
    data: models.api.VoteStartArgs,
) -> DictResponse:
    """
    URL: POST /vote/start

    Start a vote.
    """
    asf_uid = _jwt_asf_uid()

    try:
        async with storage.write(asf_uid) as write:
            wacp = await write.as_project_committee_participant(data.project)
            permitted_recipients = util.permitted_voting_recipients(asf_uid, wacp.committee_key)
            if data.email_to not in permitted_recipients:
                raise exceptions.Forbidden("Invalid mailing list choice")
            # TODO: Get fullname and use instead of asf_uid
            task = await wacp.vote.start(
                data.email_to,
                data.project,
                data.version,
                data.revision,
                data.vote_duration,
                data.subject,
                data.body,
                asf_uid,
                asf_uid,
            )
    # except Exception as e:
    #     import traceback
    #     import atr.log as log
    #     log.info(traceback.format_exc())
    #     raise exceptions.BadRequest(str(e))
    except storage.AccessError as e:
        raise exceptions.BadRequest(str(e))

    return models.api.VoteStartResults(
        endpoint="/vote/start",
        task=task,
    ).model_dump(mode="json"), 201


@api.typed
@jwtoken.require
@quart_schema.security_scheme([{"BearerAuth": []}])
@quart_schema.validate_response(models.api.VoteTabulateResults, 200)
async def vote_tabulate(
    _vote_tabulate: Literal["vote/tabulate"],
    data: models.api.VoteTabulateArgs,
) -> DictResponse:
    """
    URL: POST /vote/tabulate

    Tabulate a vote.
    """
    # asf_uid = _jwt_asf_uid()
    async with db.session() as db_data:
        release_key = sql.release_key(data.project, data.version)
        release = await db_data.release(key=str(release_key), _project_release_policy=True).demand(
            exceptions.NotFound(f"Release {release_key} not found"),
        )

    latest_vote_task = await interaction.release_latest_vote_task(release)
    if latest_vote_task is None:
        raise exceptions.NotFound("No vote task found")
    task_mid = interaction.task_mid_get(latest_vote_task)
    task_recipient = interaction.task_recipient_get(latest_vote_task)

    async with storage.write() as write:
        wagp = write.as_general_public()
        archive_url = await wagp.cache.get_message_archive_url(task_mid, task_recipient)
    if archive_url is None:
        raise exceptions.NotFound("No archive URL found")

    thread_id = archive_url.split("/")[-1]
    committee = await tabulate.vote_committee(thread_id, release)
    details = await tabulate.vote_details(committee, thread_id, release)
    return models.api.VoteTabulateResults(
        endpoint="/vote/tabulate",
        details=details,
    ).model_dump(mode="json"), 200


def _jwt_asf_uid() -> str:
    claims = getattr(quart.g, "jwt_claims", {})
    asf_uid = claims.get("sub")
    if not isinstance(asf_uid, str):
        raise base.ASFQuartException(f"Invalid token subject: {asf_uid!r}, type: {type(asf_uid)}", errorcode=401)
    return asf_uid


async def _match_committee_keys(
    key_committees: list[sql.Committee], finished_dir: pathlib.Path, data: models.api.SignatureProvenanceArgs
) -> set[str]:
    key_committee_keys = set(committee.key for committee in key_committees)
    finished_dir = paths.get_finished_dir()
    matched_committee_keys = set()

    # Check for finished files
    for key_committee_key in key_committee_keys:
        key_committee_finished_dir = finished_dir / key_committee_key
        async for rel_path in util.paths_recursive(key_committee_finished_dir):
            if rel_path.name == data.signature_file_name:
                abs_path = finished_dir / rel_path
                async with aiofiles.open(abs_path, "rb") as f:
                    rel_path_data = await f.read()
                rel_path_sha3_256 = hashlib.sha3_256(rel_path_data).hexdigest()
                if rel_path_sha3_256 == data.signature_sha3_256:
                    # We got a match
                    matched_committee_keys.add(key_committee_key)
                    break

    # Check for unfinished files
    async with db.session() as db_data:
        for key_committee_key in key_committee_keys:
            release_directories = []
            projects = await db_data.project(committee_key=key_committee_key).all()
            for project in projects:
                releases = await db_data.release(project_key=project.key).all()
                release_directories.extend(paths.release_directory(release) for release in releases)
            for release_directory in release_directories:
                if await _match_unfinished(release_directory, data):
                    matched_committee_keys.add(key_committee_key)
                    break
    return matched_committee_keys


async def _match_unfinished(release_directory: pathlib.Path, data: models.api.SignatureProvenanceArgs) -> bool:
    async for rel_path in util.paths_recursive(release_directory):
        if rel_path.name == data.signature_file_name:
            abs_path = release_directory / rel_path
            async with aiofiles.open(abs_path, "rb") as f:
                rel_path_data = await f.read()
                rel_path_sha3_256 = hashlib.sha3_256(rel_path_data).hexdigest()
                if rel_path_sha3_256 == data.signature_sha3_256:
                    return True
    return False


def _pagination_args_validate(query_args: Any) -> None:
    # Users could request any amount using limit=N with arbitrarily high N
    # We therefore limit the maximum limit to 1000
    if hasattr(query_args, "limit"):
        limit = query_args.limit
        if limit > 1000:
            raise exceptions.BadRequest("Maximum limit of 1000 exceeded")
        elif limit < 1:
            raise exceptions.BadRequest("Minimum limit less than 1 is nonsense")
    # Users could request any amount using offset=N with arbitrarily high N
    # We therefore limit the maximum offset to 1000000
    if hasattr(query_args, "offset"):
        offset = query_args.offset
        if offset > 1000000:
            raise exceptions.BadRequest("Maximum offset of 1000000 exceeded")
        elif offset < 0:
            raise exceptions.BadRequest("Minimum offset less than 0 is nonsense")
