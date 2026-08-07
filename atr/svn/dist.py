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

"""
Decompose dist.apache.org paths into a project subproject and version.
"""

import dataclasses
import re
from typing import Final, Literal

# dist-path -> projects.json key remaps the bare-name match can't reach; subproject None is a
# committee-level layout. Per-entry only - a blanket rule mis-hits (camel-karaf is Camel's)
PROJECT_REMAPS: Final[dict[tuple[str, str | None], str]] = {
    ("activemq", "activemq-artemis"): "artemis",  # Artemis graduated from ActiveMQ, dist still splits it
    ("apr", None): "apr-portable-runtime",  # the committee's top level is the Portable Runtime itself
    ("httpd", None): "httpd-http-server",  # the committee's top level is the HTTP Server
    ("sis", None): "sis-spatial-information-system",
    ("trafficcontrol", None): "traffic-control",
    ("trafficserver", None): "trafficserver-traffic-server",
    ("xmlgraphics", "commons"): "xmlgraphics-xml-graphics-commons",
}

# Lead dirs that name a distribution bucket, not a subproject
_GROUPING_BUCKETS: Final[frozenset[str]] = frozenset(
    {"providers", "source", "sources", "binaries", "bin", "src", "releases"}
)

# Buckets scoped to one committee, where the name is a real subproject elsewhere. cordova ships each
# platform repo under platforms/ (cordova-android-13.0.0.tgz), so the name comes from the file
_COMMITTEE_BUCKETS: Final[frozenset[tuple[str, str]]] = frozenset({("maven", "plugins"), ("cordova", "platforms")})

# Dirs of bundled third-party packages, never a release
_EXCLUDED_PARTS: Final[frozenset[str]] = frozenset({"repos"})

# Build/status tokens dropped from a filename-derived name
_NAME_BUILD_SUFFIXES: Final[frozenset[str]] = frozenset(
    {"src", "source", "sources", "bin", "binaries", "incubating", "v"}
)

# A version-ish component
_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"^v?\d+(?:\.\d+)*(?:[.\-_][A-Za-z0-9]+)*$")

# A bare single digit, a major-version grouping dir (xerces/c/2) not a version
_BARE_DIGIT_RE: Final[re.Pattern[str]] = re.compile(r"^\d$")

# Splits a flat name-version; non-greedy so spark-4.1.0-preview1 comes apart at the version
_COMBINED_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<name>.+?)-(?P<version>v?\d+(?:\.\d+)*(?:[.\-][A-Za-z0-9]+)*)$")

# Semver plus an optional ASF qualifier tail in a filename
_SEMVER_FILE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![0-9])(\d+(?:\.\d+)+(?:[.\-](?:alpha|beta|rc|cr|m|milestone|preview|pre|incubating|final|ga)(?:[.\-]?[0-9]+)?)*)",
    re.IGNORECASE,
)

# Calver dates (2026-01-27 or 20050330), limited to 19xx/20xx
_CALVER_FILE_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|(?:19|20)\d{6})(?!\d)")

# Airflow ships its providers as flat calver batches: a dated source alongside every provider
# package, in one dir with no version subdir. These spot the drop area and the batch source so the
# watcher and cataloguer can collapse the per-provider subprojects into one project keyed by calver
AIRFLOW_PROVIDER_AREAS: Final[frozenset[str]] = frozenset({"providers", "backport-providers"})
_AIRFLOW_CALVER_SOURCE_RE: Final[re.Pattern[str]] = re.compile(
    r"^apache[-_]airflow[-_](?:backport[-_])?providers-(\d{4})[-.](\d{1,2})[-.](\d{1,2})-source\.tar\.gz$"
)
_AIRFLOW_PROVIDER_FILE_RE: Final[re.Pattern[str]] = re.compile(r"^apache[-_]airflow[-_](?:backport[-_])?providers[-_]")


def airflow_provider_area(committee: str, parts: tuple[str, ...]) -> str | None:
    # The airflow provider area a file sits under (airflow/<area>/), or None
    if committee != "airflow":
        return None
    if parts and (parts[0] in AIRFLOW_PROVIDER_AREAS):
        return parts[0]
    return None


def is_airflow_provider_filename(filename: str) -> bool:
    # A provider package or its batch source, by name - used to skip the stray copies of these that
    # sit directly under airflow/ (duplicates of the ones under airflow/providers/)
    return _AIRFLOW_PROVIDER_FILE_RE.match(filename) is not None


def airflow_calver_date(filename: str) -> str | None:
    # The calver date of a providers batch source, normalised to YYYY-MM-DD, or None
    match = _AIRFLOW_CALVER_SOURCE_RE.match(filename)
    if match is None:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


@dataclasses.dataclass(frozen=True)
class Decomposed:
    subproject: str | None
    version: str | None
    source: Literal["dir", "combined", "filename", "unknown"]


def module_component(committee: str, subproject: str | None) -> str | None:
    # aries, sling and felix ship each maven module as its own release named committee.component.submodule
    # (aries.blueprint.core, sling.auth.core), so hundreds of modules would key as separate projects.
    # Return the component - the segment after the committee - to collapse them, or None when the
    # subproject isn't that shape (a plain name, or a re-published spec jar like org.osgi.core)
    if subproject is None:
        return None
    bare = subproject.removeprefix("org.apache.")
    if not bare.startswith(f"{committee}."):
        return None
    return bare[len(committee) + 1 :].split(".")[0] or None


def candidate_keys(committee: str, subproject: str | None) -> list[str]:
    # Dist layout and projects.json keys don't always line up (commons/lang -> commons-lang, but
    # accumulo/accumulo-access is already its key), so try the obvious shapes. apache-/apache_ come
    # off and underscores become hyphens, so an underscored dist name lines up with the registry's
    # hyphenated key instead of seeding an underscore duplicate
    if not subproject:
        return [committee]
    bare = subproject.removeprefix("apache-").removeprefix("apache_").replace("_", "-")
    keys: list[str] = []
    for name in dict.fromkeys([bare, subproject]):
        keys += [name, f"{committee}-{name}"]
    return keys


def decompose(committee: str, parts: tuple[str, ...], filename: str | None = None) -> Decomposed | None:
    # The version key is lowercased so a case split (turbine 2.2-RC1 vs 2.2-rc1) is one release
    decomposed = _decompose(committee, parts, filename)
    if (decomposed is None) or (decomposed.version is None):
        return decomposed
    return dataclasses.replace(decomposed, version=decomposed.version.lower())


def version_from_filename(filename: str) -> str | None:
    # Semver wins over calver so a dotted release isn't misread as a date. The semver tail keeps
    # a qualifier's build number, so alpha-1 and alpha-2 stay distinct, and a dotted classifier
    # (Xerces-J-bin.2.10.0) still parses
    semver = _SEMVER_FILE_RE.search(filename)
    if semver is not None:
        return semver.group(1)
    calver = _CALVER_FILE_RE.search(filename)
    if calver is not None:
        return calver.group(1)
    return None


def _choose_version(
    dir_version: str | None, dir_source: Literal["dir", "combined", "unknown"], filename: str | None
) -> tuple[str | None, Literal["dir", "combined", "filename", "unknown"]]:
    # The dir and the filename can each carry a version. An explicit version dir is the release
    # boundary, so a file under it whose own version doesn't refine the dir is a sub-component -
    # an opendal language binding at its own version, or the kafka_2.13-4.3.1 Scala build - and
    # stays on the dir's release. The filename wins only when it refines the dir (a 4.5 series dir
    # the 4.5.13 file pins, a 1.0.0-incubating tail) or there's no dir version to anchor to
    if dir_version is not None:
        dir_version = _normalise_version(dir_version)
    name_version = version_from_filename(filename) if filename else None
    if name_version is None:
        return dir_version, dir_source
    if (dir_version is not None) and not name_version.startswith(dir_version):
        return dir_version, dir_source
    return name_version, "filename"


def _decompose(committee: str, parts: tuple[str, ...], filename: str | None) -> Decomposed | None:
    # parts are the dirs below the committee, filename the release file if any. A grouping bucket
    # aside, the subproject comes from the first dir and the version from the filename when it has
    # one (a .../4.5/ series dir covers 4.5.x; the file pins 4.5.13), with the dir as the fallback
    if any(part.lower() in _EXCLUDED_PARTS for part in parts):
        # A path through a bundled-package dir (bigtop's repos/) identifies no release
        return None
    parts = _strip_self_prefix(committee, parts)
    if not parts:
        return _filename_only(committee, filename)
    if _is_grouping_bucket(committee, parts, filename):
        # The dirs are just a distribution bucket (airflow's providers/<series>), so the
        # subproject and version both live in the filename, as in the flat layout
        return _filename_only(committee, filename)
    subproject, dir_version, dir_source = _subproject_and_dir_version(committee, parts)
    version, source = _choose_version(dir_version, dir_source, filename)
    return Decomposed(subproject=subproject, version=version, source=source)


def _filename_only(committee: str, filename: str | None) -> Decomposed | None:
    # A file directly under the committee has no dir to name the subproject, so it comes from
    # the filename before the version (sling does this); a name == committee is a TLP release
    if not filename:
        return None
    version = version_from_filename(filename)
    if version is None:
        return None
    name = _strip_name_suffixes(filename[: filename.find(version)].rstrip("-._"))
    if (not name) or (name.removeprefix("apache-") == committee):
        return Decomposed(subproject=None, version=version, source="filename")
    return Decomposed(subproject=name, version=version, source="filename")


def _is_grouping_bucket(committee: str, parts: tuple[str, ...], filename: str | None) -> bool:
    # A leading dir that's a bucket holds a project's sub-packages or builds rather than
    # naming a subproject, so the subproject and version come from the filename. The
    # subproject may be null (a flat TLP release like myfaces/source/myfaces-1.0.9-src.tgz)
    # or named (airflow/providers/apache_airflow_providers_<name>); either way the filename
    # must carry a version, or there's nothing for the bucket to key on
    if filename is None:
        return False
    lead = parts[0].lower()
    if (lead not in _GROUPING_BUCKETS) and ((committee, lead) not in _COMMITTEE_BUCKETS):
        return False
    return _filename_only(committee, filename) is not None


def _is_version_dir(component: str) -> bool:
    if _BARE_DIGIT_RE.match(component):
        return False
    return bool(_VERSION_RE.match(component))


def _normalise_version(version: str) -> str:
    # A version dir sometimes uses underscores as the separator (0_92, 1_4_7); the release
    # key uses dots. Only the key changes - the real path keeps its underscores for downloads
    return version.replace("_", ".")


def _split_combined(component: str) -> tuple[str, str] | None:
    match = _COMBINED_RE.match(component)
    if match is None:
        return None
    return match.group("name"), match.group("version")


def _strip_name_suffixes(name: str) -> str:
    # Drop trailing build/status tokens (chukwa-incubating-src -> chukwa); keep at least one
    # token so a name that is only a suffix word survives
    tokens = name.split("-")
    while (len(tokens) > 1) and (tokens[-1].lower() in _NAME_BUILD_SUFFIXES):
        tokens.pop()
    return "-".join(tokens)


def _strip_self_prefix(committee: str, parts: tuple[str, ...]) -> tuple[str, ...]:
    # jackrabbit/jackrabbit/... and ace/apache-ace/... are the TLP's own area, not
    # a subproject. Drop a leading dir that just repeats the committee
    if parts and (parts[0] in (committee, f"apache-{committee}")):
        return parts[1:]
    return parts


def _subproject_and_dir_version(
    committee: str, parts: tuple[str, ...]
) -> tuple[str | None, str | None, Literal["dir", "combined", "unknown"]]:
    # First dir decides the subproject; the version is whichever dir looks like one
    first = parts[0]
    if _is_version_dir(first):
        return None, first, "dir"
    combined = _split_combined(first)
    if combined is not None:
        name, version = combined
        return (None if name == committee else name), version, "combined"
    for part in parts[1:]:
        if _is_version_dir(part):
            return first, part, "dir"
        deeper = _split_combined(part)
        if deeper is not None:
            return first, deeper[1], "combined"
    return first, None, "unknown"
