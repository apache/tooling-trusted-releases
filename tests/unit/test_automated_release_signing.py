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

import atr.util as util


def test_is_automated_release_signing_uid() -> None:
    domain = "x.apache.org"
    assert util.is_automated_release_signing_uid("X Automated Release Signing <private@x.apache.org>", "x", domain)
    assert util.is_automated_release_signing_uid("ASF X Services RM <private@x.apache.org>", "x", domain)
    assert util.is_automated_release_signing_uid(
        "Apache Asyncband automated release signing key <private@asyncband.apache.org>",
        "asyncband",
        "asyncband.apache.org",
    )
    assert util.is_automated_release_signing_uid("X AUTOMATED RELEASE SIGNING <private@x.apache.org>", "x", domain)
    assert util.is_automated_release_signing_uid("ASF X sErViCeS rM <private@x.apache.org>", "x", domain)
    assert not util.is_automated_release_signing_uid("X Automated Release Signing <private@y.apache.org>", "x", domain)
    assert not util.is_automated_release_signing_uid("X PMC <private@x.apache.org>", "x", domain)
    assert not util.is_automated_release_signing_uid(
        "X Automated Release Signing <private@x.apache.org.y>", "x", domain
    )
    assert not util.is_automated_release_signing_uid(None, "x", domain)


def test_is_automated_release_signing_uid_on_either_domain() -> None:
    on_list_domain = "HttpComponents Automated Release Signing <private@hc.apache.org>"
    on_key_domain = "HttpComponents Automated Release Signing <private@httpcomponents.apache.org>"
    assert util.is_automated_release_signing_uid(on_list_domain, "httpcomponents", "hc.apache.org")
    assert util.is_automated_release_signing_uid(on_key_domain, "httpcomponents", "hc.apache.org")
    assert not util.is_automated_release_signing_uid(
        "HttpComponents Automated Release Signing <private@other.apache.org>", "httpcomponents", "hc.apache.org"
    )
