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

import enum
from typing import Annotated, Literal

import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql

type ADD_FILES = Literal["add_files"]
type SVN_IMPORT = Literal["svn_import"]


class SvnArea(enum.Enum):
    DEV = "dev"
    RELEASE = "release"


class AddFilesForm(form.Form):
    variant: ADD_FILES = form.value(ADD_FILES)
    file_data: form.FileList = form.label("Files", "Select the files to upload.")


class SvnImportForm(form.Form):
    variant: SVN_IMPORT = form.value(SVN_IMPORT)
    # svn_area: form.Enum[SvnArea] = form.label(
    #     "svn:dist area",
    #     "Select whether to import from dev or release.",
    #     widget=form.Widget.RADIO,
    # )
    svn_path: safe.RelPath = form.label(
        "SVN path",
        "The part after the base URL shown above, for example '0.7.2-rc.1' or 'java-library/4_0_4'.",
        required=True,
    )
    revision: str = form.label(
        "Revision",
        "Specify an SVN revision number or leave as HEAD for the latest.",
        default="HEAD",
    )
    target_subdirectory: safe.OptionalRelPath = form.label(
        "Target subdirectory",
        "Optional: Subdirectory to place imported files, defaulting to the root.",
    )


type UploadForm = Annotated[
    AddFilesForm | SvnImportForm,
    form.DISCRIMINATOR,
]


def svn_import_base_path(project: sql.Project, area: SvnArea) -> safe.RelPath:
    committee_key = project.committee_key or project.key
    if (project.committee is not None) and project.committee.is_podling:
        return safe.RelPath(f"{area.value}/incubator/{committee_key}")
    return safe.RelPath(f"{area.value}/{committee_key}")
