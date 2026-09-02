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
import collections
import dataclasses
import datetime
import itertools
import json
import pathlib
import re
import shutil
import urllib.parse
from collections.abc import Awaitable, Container, Iterable, Iterator, Sequence
from typing import Any, Final

import aiofiles.os
import aioshutil
import jinja2
import sqlmodel

import atr.classify as classify
import atr.cle as cle
import atr.constants as constants
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
    # The app's own template root comes second, so the site can include the shared
    # footer while its own templates keep resolving by their bare names
    loader=jinja2.ChoiceLoader(
        [
            jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
            jinja2.FileSystemLoader(str(_TEMPLATES_DIR.parent)),
        ]
    ),
    autoescape=jinja2.select_autoescape(["html"]),
    undefined=jinja2.StrictUndefined,
)
# The footer states the running ATR version, the same as the main site's footer.
_ENVIRONMENT.globals["version"] = metadata.version
_ENVIRONMENT.globals["commit"] = metadata.commit
# The footer stamps the render time, so it's evaluated per page rather than at import.
_ENVIRONMENT.globals["last_updated"] = lambda: datetime.datetime.now(datetime.UTC).strftime("%d %b %Y %H:%M UTC")

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
    "svg/apache_incubator.svg",
    "svg/ASF_short-horizontal-color.svg",
    # The front page reuses the app's card filter, so the same script comes along
    "js/src/card-grid.js",
    # Bootstrap drives the navbar's collapse and the ASF dropdown, same as the app
    "js/min/bootstrap.bundle.min.js",
)

# A committee counts as "current" while it still has a project that hasn't
# retired to the Attic.
_LIVE_PROJECT_STATUSES: Final[frozenset[sql.ProjectStatus]] = frozenset(
    {sql.ProjectStatus.ACTIVE, sql.ProjectStatus.DORMANT, sql.ProjectStatus.STANDING}
)

# The Incubator holds no releases of its own. Its page is an index of the podlings
# instead, so the podlings are catalogued there rather than on the front page.
_INCUBATOR_COMMITTEE_KEY: Final = "incubator"
_ENVIRONMENT.globals["incubator_key"] = _INCUBATOR_COMMITTEE_KEY

# The Attic holds the PMCs that have retired. They reach it either as one of the
# Attic's own projects or as a committee that kept its key, so its page merges both.
_ATTIC_COMMITTEE_KEY: Final = "attic"
_ENVIRONMENT.globals["attic_key"] = _ATTIC_COMMITTEE_KEY

# Both index the rest of the catalogue rather than releases of their own, so their
# pages are written once the walk has been through every committee.
_INDEXING_COMMITTEE_KEYS: Final[frozenset[str]] = frozenset({_ATTIC_COMMITTEE_KEY, _INCUBATOR_COMMITTEE_KEY})

# The `class` qualifier abbreviates an artifact's stored classification to the short token
# filenames use. Only the primary-artifact classes appear; sbom and metadata are companions.
_CLASS_QUALIFIER: Final[dict[str, str]] = {
    classify.FileType.SOURCE.value: "src",
    classify.FileType.BINARY.value: "bin",
    classify.FileType.DOCS.value: "docs",
}


@dataclasses.dataclass
class _CommitteeSummary:
    release_count: int
    latest_date: datetime.datetime | None
    project_names: list[str]


@dataclasses.dataclass
class _SubprojectSummary:
    release_count: int
    latest_date: datetime.datetime | None
    has_archived: bool


async def generate_all(data: db.Session) -> None:
    """Rebuild every page, overwriting in place so the site stays servable throughout."""
    site_dir = paths.get_catalog_site_dir()
    await _write_assets(site_dir)
    # The indexes link a single-project committee straight to that project, so they
    # need its projects alongside it.
    committees = await data.committee(_projects=True).all()
    written = await _write_committee_pages(data, committees, site_dir)
    await _write_index_pages(data, site_dir, written)
    # Every committee and every project holds a directory here. Keyed off all of them
    # rather than the ones written, so a page that failed to render keeps what it had
    keep = {committee.key for committee in committees}
    keep |= {project.key for committee in committees for project in committee.projects}
    await _prune_directories(site_dir, keep | {"assets"})
    log.info(f"Rebuilt catalog site for {len(written)} of {len(committees)} committees")


async def queue_full_regeneration(data: db.Session, asf_uid: str) -> sql.Task:
    """Queue a full rebuild of the whole catalog site, reusing one already queued."""
    existing = await data.task(
        status=sql.TaskStatus.QUEUED,
        task_type=sql.TaskType.CATALOG_SITE_GENERATE,
        project_key=None,
    ).get()
    if existing is not None:
        return existing
    task = sql.Task(
        status=sql.TaskStatus.QUEUED,
        task_type=sql.TaskType.CATALOG_SITE_GENERATE,
        task_args=args.CatalogSiteGenerate(asf_uid=asf_uid, project_key=None).model_dump(),
        asf_uid=asf_uid,
        project_key=None,
    )
    data.add(task)
    return task


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
    if project.committee_key is None:
        log.warning(f"Catalog site: project {project_key} has no committee, skipping regeneration")
        return
    # The committee index is built from its projects, and the project page links back
    # past a committee that holds only the one, so both need them loaded.
    committees = await data.committee(_projects=True).all()
    committee = next((c for c in committees if c.key == project.committee_key), None)
    if committee is None:
        log.warning(f"Catalog site: committee {project.committee_key} not found, skipping regeneration")
        return
    site_dir = paths.get_catalog_site_dir()
    await _write_committee_index(data, committee, site_dir)
    # A project sharing its committee's key is the committee page, so it's written already
    if project.key != committee.key:
        await _write_project(data, committee, project, site_dir)
    # A first release, or a last one archived, moves a committee on or off the front page
    # and between the Incubator's two columns, so the indexes are rebuilt every time too.
    # The assets are not: they only change with a deploy, so the full rebuild owns them.
    await _write_index_pages(data, site_dir, committees)


def _artifact_classifier_combos(
    version: api.CatalogVersion, pmc: str, project: str
) -> Iterator[tuple[dict[str, str], str]]:
    """(minimal unique classifier combo, download URL) per downloadable artifact.

    A file's classifiers are class/os/arch/ext; the combo is the smallest subset no other file in
    the release shares, so `?<combo>` names it and no fewer qualifiers would. A file whose whole
    classifier set another file also has - or which carries no classifiers - yields nothing;
    file_name and subpath still address it. Larger combos come first, so an over-specified query
    matches the most specific rule.
    """
    strip = {"apache", pmc, project}
    release_version = str(version.version)
    classified: list[tuple[dict[str, str], str]] = []
    for artifact in version.artifacts:
        url = artifact.artifact_url
        if url is None:
            continue
        classifiers = _artifact_classifiers(artifact, release_version, strip)
        if classifiers:
            classified.append((classifiers, url))
    combos: list[tuple[dict[str, str], str]] = []
    for index, (classifiers, url) in enumerate(classified):
        others = [other for i, (other, _) in enumerate(classified) if i != index]
        combo = _minimal_combo(classifiers, others)
        if combo is not None:
            combos.append((combo, url))
    combos.sort(key=lambda combo_url: len(combo_url[0]), reverse=True)
    yield from combos


def _artifact_classifiers(artifact: api.CatalogArtifact, version: str, strip: Iterable[str]) -> dict[str, str]:
    """The classifier qualifiers a downloadable artifact carries: class, os, arch, ext.

    `class` is the stored classification mapped to its short token; `os`/`arch` are read from
    the subpath tail and `ext` from the file name, all canonicalised by classify.
    """
    classifiers: dict[str, str] = {}
    if artifact.classification is not None:
        class_token = _CLASS_QUALIFIER.get(artifact.classification)
        if class_token is not None:
            classifiers["class"] = class_token
    tail = _artifact_subpath(artifact.artifact_path, version, strip)
    if (os_value := classify.os_in(tail)) is not None:
        classifiers["os"] = os_value
    if (arch_value := classify.arch_in(tail)) is not None:
        classifiers["arch"] = arch_value
    if (ext_value := classify.ext_of(artifact.artifact_path)) is not None:
        classifiers["ext"] = ext_value
    return classifiers


def _artifact_downloads(artifact: api.CatalogArtifact) -> Iterator[tuple[str, str]]:
    """(file name, download URL) per file an artifact exposes: itself, signature, checksum, SBOM.

    A pair with no URL - not downloadable, or the companion absent - is skipped.
    """
    pairs = (
        (artifact.artifact_path, artifact.artifact_url),
        (artifact.signature_path, artifact.signature_url),
        (artifact.checksum_path, artifact.checksum_url),
        (artifact.sbom_path, artifact.sbom_url),
    )
    for file_name, url in pairs:
        if (file_name is not None) and (url is not None):
            yield file_name, url


def _artifact_subpath(file_name: str, version: str, strip: Iterable[str]) -> str:
    """An artifact's distinguishing tail, for the `subpath` qualifier.

    The part after the release version when the file name carries it
    (apache-ivy-2.0.0-bin-with-deps.tar.gz -> bin-with-deps.tar.gz); otherwise the file name
    with `strip` (apache, the PMC and project names) removed longest-first
    (apache-airflow-providers-amazon-1.0.0-bin.tar.gz -> amazon-1.0.0-bin.tar.gz).
    """
    index = file_name.find(version)
    if index != -1:
        return file_name[index + len(version) :].lstrip("-+._")
    trimmed = file_name
    for token in sorted(strip, key=len, reverse=True):
        if token:
            trimmed = re.sub(re.escape(token), "", trimmed, flags=re.IGNORECASE)
    return trimmed.lstrip("-+._")


def _artifact_subpaths(version: api.CatalogVersion, pmc: str, project: str) -> Iterator[tuple[str, str]]:
    """(subpath, download URL) per downloadable artifact, skipping empty or colliding tails.

    A tail two artifacts share can't name one file, so it's dropped; file_name still covers them.
    """
    release_version = str(version.version)
    strip = {"apache", pmc, project}
    derived: list[tuple[str, str]] = []
    for artifact in version.artifacts:
        url = artifact.artifact_url
        if url is None:
            continue
        derived.append((_artifact_subpath(artifact.artifact_path, release_version, strip), url))
    yield from _unique(derived)


async def _committee_summaries(data: db.Session, committees: Sequence[sql.Committee]) -> dict[str, _CommitteeSummary]:
    """Fold the released artifacts onto the committee card that shows them on the front page."""
    releases = await data.release(phase=sql.ReleasePhase.RELEASE, _committee=True, _project=True).all()
    counts: dict[str, int] = {}
    latest: dict[str, datetime.datetime] = {}
    for release in releases:
        if release.is_archived:
            continue
        committee = release.project.committee
        if committee is None:
            continue
        key = _INCUBATOR_COMMITTEE_KEY if committee.is_podling else committee.key
        counts[key] = counts.get(key, 0) + 1
        released = release.released
        if (released is not None) and ((key not in latest) or (released > latest[key])):
            latest[key] = released
    # The search matches project names as well as committee names, so each card lists its
    # projects. The Incubator lists its podlings, which are committees rather than projects.
    names: dict[str, list[str]] = {
        committee.key: sorted(project.display_name for project in committee.projects) for committee in committees
    }
    names[_INCUBATOR_COMMITTEE_KEY] = sorted(committee.display_name for committee in committees if committee.is_podling)
    return {
        key: _CommitteeSummary(
            release_count=counts.get(key, 0),
            latest_date=latest.get(key),
            project_names=names.get(key, []),
        )
        for key in (set(counts) | set(names))
    }


async def _guarded_write(description: str, work: Awaitable[None]) -> None:
    """Run one index write on its own, logging and swallowing a failure so the rest proceed."""
    try:
        await work
    except Exception:
        log.exception(f"Failed to render catalog site {description}")


def _has_live_project(committee: sql.Committee) -> bool:
    return any(project.status in _LIVE_PROJECT_STATUSES for project in committee.projects)


def _htaccess_cond(qualifier: str, value: str) -> str:
    """A RewriteCond matching `<qualifier>=<value>` anywhere in the query string."""
    # Match the raw (percent-encoded) query string, regex-escaped so its metacharacters
    # read literally.
    encoded = re.escape(urllib.parse.quote(value, safe=""))
    return f'RewriteCond %{{QUERY_STRING}} "(^|&){qualifier}={encoded}(&|$)"'


def _htaccess_rule(qualifier: str, value: str, url: str) -> tuple[str, str]:
    """One qualifier's RewriteCond/RewriteRule pair, redirecting `?<qualifier>=<value>`."""
    return _htaccess_cond(qualifier, value), _redirect_rule(url)


async def _lifecycle_events(data: db.Session, project: sql.Project) -> Sequence[sql.LifecycleEvent]:
    via = sql.validate_instrumented_attribute
    stmt = sqlmodel.select(sql.LifecycleEvent).where(via(sql.LifecycleEvent.project_key) == project.key)
    return (await data.execute(stmt)).scalars().all()


def _minimal_combo(classifiers: dict[str, str], others: list[dict[str, str]]) -> dict[str, str] | None:
    """The smallest subset of `classifiers` that no dict in `others` contains, or None."""
    items = sorted(classifiers.items())
    for size in range(1, len(items) + 1):
        for subset in itertools.combinations(items, size):
            if not any(all(other.get(key) == value for key, value in subset) for other in others):
                return dict(subset)
    return None


async def _prune_directories(directory: safe.StatePath, keep: Container[str]) -> None:
    """Remove the subdirectories of one that this build didn't write.

    Files are left alone, since every page is rewritten in place. Only whole directories
    go stale, when a committee, project or release stops being catalogued under that name.
    """
    path = directory.path
    try:
        entries = await aiofiles.os.listdir(path)
    except FileNotFoundError:
        return
    pruned: list[str] = []
    for name in sorted(entries):
        if (name in keep) or (not await aiofiles.os.path.isdir(path / name)):
            continue
        await aioshutil.rmtree(path / name)
        pruned.append(name)
    if pruned:
        log.info(f"Catalog site: pruned {len(pruned)} stale directories from {path}: {', '.join(pruned)}")


def _redirect_rule(url: str) -> str:
    """The RewriteRule redirecting the release directory to a download URL."""
    # archive.apache.org is a permanent home (301); the closer.lua mirror is not (302). NE
    # stops Apache re-encoding the already percent-encoded URL.
    status = 301 if url.startswith(constants.ARCHIVE_APACHE_URL) else 302
    return f'RewriteRule "^$" "{url}" [R={status},NE,L]'


def _release_htaccess(version: api.CatalogVersion, pmc: str, project: str) -> str | None:
    """Render a release's files to an Apache `.htaccess` map, or None if none are downloadable.

    The vhost routes a qualified PURL request into the release directory; these rules match its
    query string and redirect to the download URL. Every file is reachable by `?file_name=`, each
    artifact also by `?subpath=` and by the minimal combo of its class/os/arch/ext classifiers.
    """
    rules: list[str] = []
    for artifact in version.artifacts:
        for file_name, url in _artifact_downloads(artifact):
            rules.extend(_htaccess_rule("file_name", file_name, url))
    for subpath, url in _artifact_subpaths(version, pmc, project):
        rules.extend(_htaccess_rule("subpath", subpath, url))
    for combo, url in _artifact_classifier_combos(version, pmc, project):
        rules.extend(_htaccess_cond(key, combo[key]) for key in sorted(combo))
        rules.append(_redirect_rule(url))
    if not rules:
        return None
    return "RewriteEngine On\n" + "\n".join(rules) + "\n"


async def _subproject_summaries(data: db.Session, subprojects: Sequence[sql.Project]) -> dict[str, _SubprojectSummary]:
    return {subproject.key: await _subproject_summary(data, subproject) for subproject in subprojects}


async def _subproject_summary(data: db.Session, project: sql.Project) -> _SubprojectSummary:
    releases = await data.release(project_key=project.key, phase=sql.ReleasePhase.RELEASE).all()
    release_count = 0
    latest_date: datetime.datetime | None = None
    has_archived = False
    for release in releases:
        if release.is_archived:
            has_archived = True
            continue
        release_count += 1
        released = release.released or release.created
        if (latest_date is None) or (released > latest_date):
            latest_date = released
    return _SubprojectSummary(release_count=release_count, latest_date=latest_date, has_archived=has_archived)


def _unique(pairs: list[tuple[str, str]]) -> Iterator[tuple[str, str]]:
    """Yield the pairs whose (non-empty) key is unique in the list.

    A qualifier value that more than one artifact derives can't address a single file, so it's
    dropped rather than resolving ambiguously; file_name always covers what's dropped.
    """
    counts = collections.Counter(key for key, _ in pairs)
    for key, value in pairs:
        if key and (counts[key] == 1):
            yield key, value


async def _write(path: safe.StatePath, content: str) -> None:
    await util.atomic_write_file(path.path, content)


async def _write_assets(site_dir: safe.StatePath) -> None:
    assets_root = site_dir.path / "assets"
    for rel in _ASSETS:
        destination = assets_root / rel
        await aiofiles.os.makedirs(destination.parent, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, _STATIC_DIR / rel, destination)


async def _write_attic_index(
    site_dir: safe.StatePath,
    attic: sql.Committee,
    projects: Sequence[sql.Project],
    committees: Sequence[sql.Committee],
) -> None:
    # A retired PMC is filed either under the Attic itself or as a committee that kept its own
    # key. Either way it's the projects a reader wants, so both are flattened into one list by
    # their "Apache ..." name, each opening at its archive.
    retired_projects = list(projects)
    for committee in committees:
        retired_projects.extend(committee.projects)
    entries = [(project.display_name, f"../{project.key}/archive.html") for project in retired_projects]
    entries.sort(key=lambda entry: entry[0].lower())
    html = _ENVIRONMENT.get_template("attic.html").render(committee=attic, entries=entries, root="../")
    await _write(site_dir / attic.key / "index.html", html)


async def _write_committee_index(
    data: db.Session, committee: sql.Committee, site_dir: safe.StatePath
) -> list[sql.Project]:
    """Write the committee page, returning the projects that still need one of their own.

    A committee running a project of its own name has no separate page for it. That
    project's releases become the committee page, with the others listed beneath them.
    """
    projects = sorted(committee.projects, key=lambda project: project.display_name.lower())
    tlp = next((project for project in projects if project.key == committee.key), None)
    subprojects = [project for project in projects if project is not tlp]
    current = [project for project in subprojects if project.status in _LIVE_PROJECT_STATUSES]
    archived = [project for project in subprojects if project.status not in _LIVE_PROJECT_STATUSES]
    if tlp is not None:
        await _write_project(data, committee, tlp, site_dir, current, archived)
        return subprojects
    # Nothing of the committee's own to show, so the page is just the way down to
    # the projects, rendered by the same template with no project against it.
    html = _ENVIRONMENT.get_template("project.html").render(
        committee=committee,
        project=None,
        subprojects=current,
        archived_subprojects=archived,
        subproject_summaries=await _subproject_summaries(data, (*current, *archived)),
        retired=not current,
        keys_url=paths.committee_keys_url(committee),
        root="../",
    )
    await _write(site_dir / committee.key / "index.html", html)
    # Its projects sit at the site root, so nothing but the page itself belongs here
    await _prune_directories(site_dir / committee.key, set())
    return subprojects


async def _write_committee_pages(
    data: db.Session, committees: Sequence[sql.Committee], site_dir: safe.StatePath
) -> list[sql.Committee]:
    """Write each committee's own page and those of its projects, returning the ones written."""
    written: list[sql.Committee] = []
    for committee in committees:
        if committee.key in _INDEXING_COMMITTEE_KEYS:
            projects = list(committee.projects)
        else:
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
        if committee.key in _INDEXING_COMMITTEE_KEYS:
            # These skip the committee index, which is where the tidying otherwise happens
            await _prune_directories(site_dir / committee.key, set())
        written.append(committee)
    return written


async def _write_front_page(
    data: db.Session,
    site_dir: safe.StatePath,
    all_committees: Sequence[sql.Committee],
    listed: Sequence[sql.Committee],
) -> None:
    # Summaries cover every committee; only the still-releasing ones are listed.
    summaries = await _committee_summaries(data, all_committees)
    await _write_root_index(site_dir, listed, summaries)


async def _write_htaccess(release_dir: safe.StatePath, version: api.CatalogVersion, pmc: str, project: str) -> None:
    htaccess = _release_htaccess(version, pmc, project)
    if htaccess is None:
        return
    # .htaccess is a dotfile, which StatePath's join rejects, so write via the raw OS path
    await util.atomic_write_file(release_dir.path / ".htaccess", htaccess)


async def _write_incubator_index(
    site_dir: safe.StatePath,
    incubator: sql.Committee,
    current: Sequence[sql.Committee],
    retired: Sequence[sql.Committee],
) -> None:
    def by_name(committee: sql.Committee) -> str:
        return committee.display_name.lower()

    html = _ENVIRONMENT.get_template("incubator.html").render(
        committee=incubator,
        current=sorted(current, key=by_name),
        retired=sorted(retired, key=by_name),
        root="../",
    )
    await _write(site_dir / incubator.key / "index.html", html)


async def _write_index_pages(data: db.Session, site_dir: safe.StatePath, committees: Sequence[sql.Committee]) -> None:
    """Write the three pages that index the others: the front page, Incubator and Attic."""
    current: list[sql.Committee] = []
    current_podlings: list[sql.Committee] = []
    retired_podlings: list[sql.Committee] = []
    retired_committees: list[sql.Committee] = []
    incubator: sql.Committee | None = None
    attic: sql.Committee | None = None
    for committee in committees:
        if committee.key == _INCUBATOR_COMMITTEE_KEY:
            incubator = committee
        elif committee.key == _ATTIC_COMMITTEE_KEY:
            attic = committee
        elif committee.is_podling:
            (current_podlings if _has_live_project(committee) else retired_podlings).append(committee)
        elif _has_live_project(committee):
            current.append(committee)
        else:
            # Nothing left to release, so it belongs with the rest of the Attic
            retired_committees.append(committee)
    # Each index is written on its own, so a failure rendering one doesn't starve the
    # others - above all the front page, which comes last and is the page most missed.
    # The committee is still listed even if its own index failed to render this pass;
    # overwrite-in-place means an earlier copy is likely still served.
    if incubator is not None:
        await _guarded_write(
            "Incubator index", _write_incubator_index(site_dir, incubator, current_podlings, retired_podlings)
        )
        current.append(incubator)
    if attic is not None:
        attic_index = _write_attic_index(site_dir, attic, list(attic.projects), retired_committees)
        await _guarded_write("Attic index", attic_index)
        current.append(attic)
    # A committee whose projects have all retired keeps its pages, but drops off the
    # front page, so the index stays a list of where releases are still coming from.
    await _guarded_write("front page", _write_front_page(data, site_dir, committees, current))


async def _write_project(
    data: db.Session,
    committee: sql.Committee,
    project: sql.Project,
    site_dir: safe.StatePath,
    subprojects: Sequence[sql.Project] = (),
    archived_subprojects: Sequence[sql.Project] = (),
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    artifacts = await data.artifact(project_key=project.key, _release=True).all()
    project_cycles = await data.project_cycle(project_key=project.key).all()
    assembled = catalog.assemble(project.version_method, artifacts, project_cycles, now)
    current = [v for v in assembled.versions if v.status == "released"]
    archived = [v for v in assembled.versions if v.status == "archived"]
    current_groups = catalog.cycle_groups(current, project_cycles, now) if assembled.grouped else []
    archived_groups = catalog.cycle_groups(archived, project_cycles, now) if assembled.grouped else []

    # Every project is addressable as <key>/<version>/artifacts.json from the site root,
    # so a purl resolves without knowing the committee. A project sharing its committee's
    # key lands on the committee directory, which is how the two share a page.
    project_dir = site_dir / project.key
    root = "../"
    retired = project.status not in _LIVE_PROJECT_STATUSES
    # The signing keys are published per committee, beside the release files themselves
    keys_url = paths.committee_keys_url(committee)
    subproject_summaries = await _subproject_summaries(data, (*subprojects, *archived_subprojects))
    # Both the project document and the per-release ones are cut from the same rows
    releases = await data.release(project_key=project.key).all()
    events = await _lifecycle_events(data, project)
    project_document = cle.project_document(project, events, releases, now=now)
    release_documents = cle.release_documents(project, releases, events, now=now)
    await _write(project_dir / "cle.json", json.dumps(project_document, indent=2, default=str))
    await _write(
        project_dir / "index.html",
        _ENVIRONMENT.get_template("project.html").render(
            committee=committee,
            project=project,
            subprojects=subprojects,
            archived_subprojects=archived_subprojects,
            subproject_summaries=subproject_summaries,
            versions=current,
            groups=current_groups,
            has_archive=bool(archived),
            retired=retired,
            keys_url=keys_url,
            root=root,
        ),
    )
    await _write(
        project_dir / "archive.html",
        _ENVIRONMENT.get_template("project_archive.html").render(
            committee=committee,
            project=project,
            versions=archived,
            groups=archived_groups,
            retired=retired,
            keys_url=keys_url,
            root=root,
        ),
    )
    for version in assembled.versions:
        document = release_documents.get(str(version.version))
        await _write_release(project_dir, committee, project, version, f"{root}../", document)
    await _prune_directories(project_dir, {str(version.version) for version in assembled.versions})


async def _write_release(
    project_dir: safe.StatePath,
    committee: sql.Committee,
    project: sql.Project,
    version: api.CatalogVersion,
    root: str,
    cle_document: dict[str, Any] | None,
) -> None:
    release_dir = project_dir / str(version.version)
    if cle_document is not None:
        await _write(release_dir / "cle.json", json.dumps(cle_document, indent=2, default=str))
        # The document is a sibling of artifacts.json, so the link is relative like the page's
        version = version.model_copy(update={"cle_url": "cle.json"})
    await _write(
        release_dir / "index.html",
        _ENVIRONMENT.get_template("release.html").render(
            committee=committee,
            project=project,
            release_version=version,
            root=root,
        ),
    )
    await _write(release_dir / "artifacts.json", version.model_dump_json(indent=2))
    await _write_htaccess(release_dir, version, committee.key, project.key)


async def _write_root_index(
    site_dir: safe.StatePath, committees: Sequence[sql.Committee], summaries: dict[str, _CommitteeSummary]
) -> None:
    ordered = sorted(committees, key=lambda committee: committee.display_name.lower())
    await _write(
        site_dir / "index.html",
        _ENVIRONMENT.get_template("index.html").render(committees=ordered, summaries=summaries, root=""),
    )
