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

"""Renders the release catalogue out to a directory of static files.

The dynamic ``/catalog/<project>`` page is rebuilt here as a standalone,
three-level site (committee, then project, then release version) that can be
served from a separate host with no application behind it. The data is the
same as the page - ``catalog.assemble`` for the versions and their artifacts,
``cle.project_document`` for the per-project lifecycle feed - so this module
only adds the file layout and the rendering.
"""

import asyncio
import datetime
import json
import pathlib
import shutil
from collections.abc import Sequence
from typing import Any, Final

import aiofiles.os
import jinja2
import sqlmodel

import atr.cle as cle
import atr.db as db
import atr.log as log
import atr.metadata as metadata
import atr.models.api as api
import atr.models.args as args
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.shared.catalog as catalog
import atr.util as util

_TEMPLATES_DIR: Final = pathlib.Path(__file__).parent / "templates" / "catalog_site"
_ENVIRONMENT: Final = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html"]),
    undefined=jinja2.StrictUndefined,
)
# The footer states the running ATR version, the same as the main site's footer.
_ENVIRONMENT.globals["version"] = metadata.version
_ENVIRONMENT.globals["commit"] = metadata.commit

# ATR's own stylesheets and the certified badge, copied under the site's assets/
# so the pages carry the app's look with nothing resolving back to the server.
# atr.css references ../webfonts relative to itself, so the fonts sit alongside.
_STATIC_DIR: Final = pathlib.Path(__file__).parent / "static"
_ASSETS: Final[tuple[str, ...]] = (
    "css/normalize.css",
    "css/atr.css",
    "css/bootstrap.custom.css",
    "webfonts/inter-v.woff2",
    "webfonts/inter-vi.woff2",
    "webfonts/jost-v.woff2",
    "svg/atr_certified_badge.svg",
    "svg/atr_logo.svg",
)

# A committee counts as "current" while it still has a project that hasn't
# retired to the Attic.
_LIVE_PROJECT_STATUSES: Final[frozenset[sql.ProjectStatus]] = frozenset(
    {sql.ProjectStatus.ACTIVE, sql.ProjectStatus.DORMANT, sql.ProjectStatus.STANDING}
)


async def generate_all(data: db.Session) -> None:
    """Rebuild every page, overwriting in place so the site stays servable throughout."""
    site_dir = paths.get_catalog_site_dir()
    await _write_assets(site_dir)
    committees = await data.committee().all()
    await _write_root_index(site_dir, committees)
    written = 0
    for committee in committees:
        try:
            projects = await _write_committee_index(data, committee, site_dir)
        except Exception:
            log.exception(f"Failed to render catalog site committee index for {committee.key}")
            continue
        for project in projects:
            try:
                await _write_project(data, committee, project, site_dir)
            except Exception:
                log.exception(f"Failed to render catalog site for project {project.key}")
        written += 1
    log.info(f"Rebuilt catalog site for {written} of {len(committees)} committees")


async def queue_regeneration(data: db.Session, asf_uid: str, project_key: str) -> None:
    """Queue an incremental regeneration of one project's subtree.

    Adds the task to the caller's session without committing, so it sits in the
    same transaction as the catalogue change that triggered it. A QUEUED task
    already waiting for the same project is left to do the work, since it will
    read the committed change anyway; a run that's already in flight is serialized
    separately, by the handler deferring to it.
    """
    existing = await data.task(
        status=sql.TaskStatus.QUEUED,
        task_type=sql.TaskType.CATALOG_SITE_GENERATE,
        project_key=project_key,
    ).get()
    if existing is not None:
        return
    data.add(
        sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.CATALOG_SITE_GENERATE,
            task_args=args.CatalogSiteGenerate(asf_uid=asf_uid, project_key=project_key).model_dump(),
            asf_uid=asf_uid,
            project_key=project_key,
        )
    )


async def regenerate_project(data: db.Session, project_key: str) -> None:
    """Rewrite one project's pages and refresh its committee index."""
    project = await data.project(key=project_key, _committee=True).get()
    if project is None:
        log.warning(f"Catalog site: project {project_key} not found, skipping regeneration")
        return
    committee = project.committee
    if committee is None:
        log.warning(f"Catalog site: project {project_key} has no committee, skipping regeneration")
        return
    site_dir = paths.get_catalog_site_dir()
    await _write_committee_index(data, committee, site_dir)
    await _write_project(data, committee, project, site_dir)


async def _project_cle_document(data: db.Session, project: sql.Project, now: datetime.datetime) -> dict[str, Any]:
    releases = await data.release(project_key=project.key).all()
    via = sql.validate_instrumented_attribute
    events_stmt = sqlmodel.select(sql.LifecycleEvent).where(via(sql.LifecycleEvent.project_key) == project.key)
    events = (await data.execute(events_stmt)).scalars().all()
    return cle.project_document(project, events, releases, now=now)


async def _write(path: safe.StatePath, content: str) -> None:
    await util.atomic_write_file(path.path, content)


async def _write_assets(site_dir: safe.StatePath) -> None:
    assets_root = site_dir.path / "assets"
    for rel in _ASSETS:
        destination = assets_root / rel
        await aiofiles.os.makedirs(destination.parent, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, _STATIC_DIR / rel, destination)


async def _write_committee_index(
    data: db.Session, committee: sql.Committee, site_dir: safe.StatePath
) -> list[sql.Project]:
    projects = list(await data.project(committee_key=committee.key).all())
    current = sorted((p for p in projects if p.status in _LIVE_PROJECT_STATUSES), key=lambda p: p.display_name.lower())
    archived = sorted(
        (p for p in projects if p.status not in _LIVE_PROJECT_STATUSES), key=lambda p: p.display_name.lower()
    )
    html = _ENVIRONMENT.get_template("committee.html").render(
        committee=committee,
        current=current,
        archived=archived,
        retired=not current,
        root="../",
    )
    await _write(site_dir / committee.key / "index.html", html)
    return projects


async def _write_project(
    data: db.Session, committee: sql.Committee, project: sql.Project, site_dir: safe.StatePath
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    artifacts = await data.artifact(project_key=project.key, _release=True).all()
    project_cycles = await data.project_cycle(project_key=project.key).all()
    assembled = catalog.assemble(project.version_method, artifacts, project_cycles, now)
    current = [v for v in assembled.versions if v.status == "released"]
    archived = [v for v in assembled.versions if v.status == "archived"]

    project_dir = site_dir / committee.key / project.key
    cle_document = await _project_cle_document(data, project, now)
    await _write(project_dir / "cle.json", json.dumps(cle_document, indent=2, default=str))
    await _write(
        project_dir / "index.html",
        _ENVIRONMENT.get_template("project.html").render(
            committee=committee,
            project=project,
            versions=current,
            has_archive=bool(archived),
            retired=project.status not in _LIVE_PROJECT_STATUSES,
            root="../../",
        ),
    )
    await _write(
        project_dir / "archive.html",
        _ENVIRONMENT.get_template("project_archive.html").render(
            committee=committee,
            project=project,
            versions=archived,
            root="../../",
        ),
    )
    for version in assembled.versions:
        await _write_release(project_dir, committee, project, version)


async def _write_release(
    project_dir: safe.StatePath, committee: sql.Committee, project: sql.Project, version: api.CatalogVersion
) -> None:
    release_dir = project_dir / str(version.version)
    await _write(
        release_dir / "index.html",
        _ENVIRONMENT.get_template("release.html").render(
            committee=committee,
            project=project,
            version=version,
            root="../../../",
        ),
    )
    await _write(release_dir / "artifacts.json", version.model_dump_json(indent=2))


async def _write_root_index(site_dir: safe.StatePath, committees: Sequence[sql.Committee]) -> None:
    ordered = sorted(committees, key=lambda committee: committee.display_name.lower())
    await _write(
        site_dir / "index.html",
        _ENVIRONMENT.get_template("index.html").render(committees=ordered, root=""),
    )
