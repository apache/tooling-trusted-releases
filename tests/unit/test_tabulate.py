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

import atr.tabulate as tabulate


def test_vote_resolution_body_votes_formats_plural_binding_summary() -> None:
    summary = {
        "binding_votes": 9,
        "binding_votes_yes": 8,
        "binding_votes_no": 0,
        "binding_votes_abstain": 1,
    }

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary))

    assert body_lines[2] == "Of these binding votes, 8 were +1, 0 were -1, and 1 was 0."


def test_vote_resolution_body_votes_formats_singular_binding_summary() -> None:
    summary = {
        "binding_votes": 9,
        "binding_votes_yes": 8,
        "binding_votes_no": 1,
        "binding_votes_abstain": 0,
    }

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary))

    assert body_lines[2] == "Of these binding votes, 8 were +1, 1 was -1, and 0 were 0."
