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

import atr.htm as htm
import atr.models.sql as sql
import atr.util as util


def inactivity_form_intro(release: sql.Release, action: str = "deleted") -> htm.Element:
    days = max(0, (datetime.datetime.now(datetime.UTC) - release.activity_at).days)
    return htm.div[
        htm.div(".mb-2")[
            f"This release has been inactive for {util.plural(days, 'day')}. "
            f"After 90 days of inactivity this project will be {action}."
        ],
    ]
