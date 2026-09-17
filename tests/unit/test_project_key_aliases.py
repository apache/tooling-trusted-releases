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

import collections.abc

import pytest
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.datasources.apache as apache
import atr.db as db
import atr.models.sql as sql
import atr.svn.catalog as catalog
import atr.svn.dist as dist

CASES = [
    ("aries-javax.persistence", "aries-javax-persistence", "aries/javax.persistence_2.0-2.7.3-sources.jar"),
    ("felix-javax.servlet", "felix-javax-servlet", "felix/javax.servlet-1.0.0-bin.tar.gz"),
    ("felix-org.osgi.compendium", "felix-org-osgi-compendium", "felix/org.osgi.compendium-1.0.0-bin.tar.gz"),
    ("felix-org.osgi.core", "felix-org-osgi-core", "felix/org.osgi.core-1.0.0.jar"),
    ("felix-org.osgi.foundation", "felix-org-osgi-foundation", "felix/org.osgi.foundation-1.0.0-bin.tar.gz"),
    ("felix-org.osgi.service.obr", "felix-org-osgi-service-obr", "felix/org.osgi.service.obr-1.0.0.jar"),
    ("felix-osgi.core", "felix-osgi-core", "felix/osgi.core-8.0.0-AtomosEquinox.jar"),
    ("felix-shell.tui", "felix-shell-tui", "felix/shell.tui-1.0.0.tar.gz"),
    (
        "lucene.net",
        "lucenenet",
        "incubator/lucene.net/binaries/2.9.2-incubating/Apache-Lucene.Net-2.9.2-incubating.bin.zip",
    ),
    ("lucenenet-Apache-Lucene.Net", "lucenenet", "lucenenet/Apache-Lucene.Net-3.0.3-RC2.bin.zip"),
    ("lucenenet-lucenedotnet", "lucenenet", "lucenenet/3.0.3-RC2/Apache-Lucene.Net-3.0.3-RC2.bin.zip"),
    ("openwhisk-OpenWhisk", "openwhisk", "openwhisk/OpenWhisk-2.0.0-sources.tar.gz"),
    ("sling-maven-plugin.parent", "sling-maven-plugin", "sling/sling-maven-plugin.parent-2.4.0-source-release.zip"),
    ("tapestry-Tapestry", "tapestry", "tapestry/binaries/3.0/Tapestry-3.0-bin.tar.gz"),
    ("tapestry-Tapestry-Web", "tapestry", "tapestry/Tapestry-Web-3.0.4.tar.gz"),
    ("vcl-VCL", "vcl", "vcl/apache-VCL-2.2.2.tar.bz2"),
]


@pytest.fixture
async def data() -> collections.abc.AsyncIterator[db.Session]:
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
        async with db.Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.parametrize(("old_key", "canonical_key", "path"), CASES)
@pytest.mark.parametrize("existing", [False, True])
async def test_data_source_import_reuses_canonical_project(
    data: db.Session, old_key: str, canonical_key: str, path: str, existing: bool
) -> None:
    committee = sql.Committee(key=canonical_key.split("-")[0])
    data.add(committee)
    if existing:
        data.add(sql.Project(key=canonical_key, name="Apache Existing", committee=committee))
    await data.commit()
    projects = apache.ProjectsData.model_validate(
        {old_key: {"name": "Apache Incoming", "homepage": "https://example.apache.org/", "pmc": committee.key}}
    )
    assert await apache._update_projects(data, projects) == (0 if existing else 1, 0)
    await data.commit()
    assert await apache._update_projects(data, projects) == (0, 0)
    await data.commit()
    stored = await data.project().all()
    assert [(project.key, project.name) for project in stored] == [
        (canonical_key, "Apache Existing" if existing else "Apache Incoming")
    ]


@pytest.mark.parametrize(("old_key", "canonical_key", "path"), CASES)
async def test_dist_paths_resolve_to_repaired_project(
    data: db.Session, old_key: str, canonical_key: str, path: str
) -> None:
    committee = sql.Committee(key=canonical_key.split("-")[0])
    project = sql.Project(key=canonical_key, name="Apache Existing", committee=committee)
    data.add(project)
    await data.commit()
    change = catalog._decompose_change(f"release/{path}")
    assert change is not None
    dist_committee, decomposed, _is_dir, _filename = change
    subproject = dist.module_component(dist_committee, decomposed.subproject) or decomposed.subproject
    assert await catalog._resolve_project(data, dist_committee, subproject) is project


async def test_dist_remaps_do_not_fold_unrelated_committees(data: db.Session) -> None:
    project = sql.Project(key="karaf", committee=sql.Committee(key="karaf"))
    data.add(project)
    await data.commit()
    changes = catalog._structural_changes({"release/camel/Karaf-1.2.3-source.tar.gz": {"flags": "A "}})
    ((committee, subproject, _version),) = changes.added
    assert await catalog._resolve_project(data, committee, subproject) is None
    assert apache.canonical_project_key("camel-Karaf") == "camel-Karaf"
