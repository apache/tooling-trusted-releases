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

import pydantic
import pytest

import atr.form as form
import atr.models.api as api
import atr.shared.vote as shared_vote


def _api_args(choice: str, comment: str) -> dict[str, str]:
    return {"project": "example", "version": "1.0.0", "choice": choice, "comment": comment}


def _form_data(decision: str, comment: str) -> dict[str, str]:
    return {
        "csrf_token": "token",
        "decision": decision,
        "comment": comment,
        "vote_seq": "1",
    }


@pytest.mark.parametrize("choice", ["0", "-1"])
@pytest.mark.parametrize("comment", ["", "  \n "])
def test_api_rejects_non_yes_vote_without_comment(choice: str, comment: str) -> None:
    with pytest.raises(pydantic.ValidationError, match="comment is required"):
        api.VoteCastArgs.model_validate(_api_args(choice, comment))


@pytest.mark.parametrize(("choice", "comment"), [("+1", ""), ("0", "+0.5, see thread"), ("-1", "Missing LICENSE")])
def test_api_accepts_yes_vote_or_commented_vote(choice: str, comment: str) -> None:
    args = api.VoteCastArgs.model_validate(_api_args(choice, comment))
    assert args.comment == comment


@pytest.mark.parametrize("decision", ["0", "-1"])
@pytest.mark.parametrize("comment", ["", "  \n "])
def test_form_rejects_non_yes_vote_without_comment(decision: str, comment: str) -> None:
    form_data = _form_data(decision, comment)
    with pytest.raises(pydantic.ValidationError) as excinfo:
        form.validate(shared_vote.CastVoteForm, form_data)

    # The error belongs to the comment field, so the form shows it there on re-render
    flash_data = form.flash_error_data(shared_vote.CastVoteForm, excinfo.value.errors(), form_data)
    assert flash_data["comment"]["msg"] == "Please give a reason when voting 0 or -1."


@pytest.mark.parametrize(("decision", "comment"), [("+1", ""), ("0", "+0.5, see thread"), ("-1", "Missing LICENSE")])
def test_form_accepts_yes_vote_or_commented_vote(decision: str, comment: str) -> None:
    cast_vote_form = form.validate(shared_vote.CastVoteForm, _form_data(decision, comment))
    assert isinstance(cast_vote_form, shared_vote.CastVoteForm)
    assert cast_vote_form.comment == comment
