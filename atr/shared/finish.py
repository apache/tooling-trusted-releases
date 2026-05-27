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

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

import atr.form as form
import atr.models.safe as safe
import atr.web as web

type Respond = Callable[[int, str], Awaitable[tuple[web.QuartResponse, int] | web.WerkzeugResponse]]

type DELETE_DIR = Literal["DELETE_DIR"]
type PUBLISH_TO_SVN = Literal["PUBLISH_TO_SVN"]
type REMOVE_RC_TAGS = Literal["REMOVE_RC_TAGS"]


class DeleteEmptyDirectoryForm(form.Form):
    variant: DELETE_DIR = form.value(DELETE_DIR)
    directory_to_delete: safe.RelPath = form.label("Directory to delete", widget=form.Widget.SELECT)


class PublishToSvnForm(form.Form):
    variant: PUBLISH_TO_SVN = form.value(PUBLISH_TO_SVN)
    download_path_suffix: safe.OptionalRelPath = form.label("Download path suffix", default=None)
    revision_number: safe.RevisionNumber = form.label("Revision number", widget=form.Widget.HIDDEN)


class RemoveRCTagsForm(form.Empty):
    variant: REMOVE_RC_TAGS = form.value(REMOVE_RC_TAGS)


type FinishForm = Annotated[
    DeleteEmptyDirectoryForm | PublishToSvnForm | RemoveRCTagsForm,
    form.DISCRIMINATOR,
]
