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

import dataclasses

import atr.analysis as analysis
import atr.attestable as attestable
import atr.db as db
import atr.models.attestable
import atr.models.sql as sql
import atr.util as util


@dataclasses.dataclass(frozen=True)
class PublishedFile:
    path: str
    size: int | None
    url: str | None


def files(
    artifacts: list[sql.Artifact],
    attested: atr.models.attestable.Attestable | None,
    archived: bool,
) -> list[PublishedFile]:
    entries: dict[str, tuple[int | None, str | None]] = {}
    if attested is not None:
        dist_dir = next((a.download_path_suffix for a in artifacts if a.download_path_suffix), None)
        for path_key, content_hash in attestable.path_hashes(attested).items():
            entry = attested.hashes.get(content_hash)
            entries[path_key] = (entry.size if (entry is not None) else None, dist_dir)
    else:
        for artifact in artifacts:
            for sibling in (
                artifact.artifact_path,
                artifact.signature_path,
                artifact.checksum_path,
                artifact.sbom_path,
            ):
                if sibling:
                    entries.setdefault(sibling, (None, artifact.download_path_suffix))
    published = []
    for rel_path in sorted(entries):
        size, dist_dir = entries[rel_path]
        url = None
        if dist_dir:
            is_artifact = analysis.is_artifact(rel_path)
            kind = util.DownloadFile.ARTIFACT if is_artifact else util.DownloadFile.METADATA
            url = util.download_url_for_published_path(f"{dist_dir}/{rel_path}", kind, archived=archived)
        published.append(PublishedFile(path=rel_path, size=size, url=url))
    return published


async def release_files(release: sql.Release) -> list[PublishedFile]:
    project_key = release.safe_project_key
    version_key = release.safe_version_key
    async with db.session() as data:
        artifacts = list(await data.artifact(project_key=str(project_key), version=str(version_key)).all())
    attested = None
    if (revision_number := await attestable.latest_revision_number(project_key, version_key)) is not None:
        attested = await attestable.load(project_key, version_key, revision_number)
    return files(artifacts, attested, release.is_archived)
