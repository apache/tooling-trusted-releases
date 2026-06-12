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

from typing import Annotated, Literal

import atr.form as form

type ADD_RELEASE_MANAGER = Literal["add_release_manager"]
type REMOVE_RELEASE_MANAGER = Literal["remove_release_manager"]


class AddReleaseManagerForm(form.Form):
    variant: ADD_RELEASE_MANAGER = form.value(ADD_RELEASE_MANAGER)
    committee_key: str = form.label("Committee name", widget=form.Widget.HIDDEN)
    asf_uid: str = form.label("ASF UID", widget=form.Widget.HIDDEN)


class RemoveReleaseManagerForm(form.Form):
    variant: REMOVE_RELEASE_MANAGER = form.value(REMOVE_RELEASE_MANAGER)
    committee_key: str = form.label("Committee name", widget=form.Widget.HIDDEN)
    asf_uid: str = form.label("ASF UID", widget=form.Widget.HIDDEN)


type CommitteesForm = Annotated[
    AddReleaseManagerForm | RemoveReleaseManagerForm,
    form.DISCRIMINATOR,
]
