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
import re
from typing import Final

APACHE_DOMAIN: Final[str] = "apache.org"
_MESSAGE_ID_DOMAIN_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_MESSAGE_ID_LOCAL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_MESSAGE_ID_MAX_LENGTH: Final[int] = 255


class MailFooterCategory(enum.StrEnum):
    NONE = "none"
    USER = "user"
    AUTO = "auto"


def message_id_validate(message_id: str | None) -> None:
    # We should consider an atr.safe class for this
    # But it has very limited use
    if message_id is None:
        return
    if message_id == "":
        raise ValueError("Message ID cannot be empty")
    if len(message_id) > _MESSAGE_ID_MAX_LENGTH:
        raise ValueError("Message ID is too long")
    if not message_id.isascii():
        raise ValueError("Message ID must be ASCII")
    if any((ch.isspace() or (ch in "<>") or (ord(ch) < 32) or (ord(ch) == 127)) for ch in message_id):
        raise ValueError("Message ID must be a bare ID without whitespace, brackets, or control characters")
    if message_id.count("@") != 1:
        raise ValueError("Message ID must contain exactly one @")
    local, domain = message_id.rsplit("@", 1)
    if (_MESSAGE_ID_LOCAL_RE.fullmatch(local) is None) or (".." in local):
        raise ValueError("Message ID local part is invalid")
    if (domain != APACHE_DOMAIN) and (not domain.endswith(f".{APACHE_DOMAIN}")):
        raise ValueError(f"Message ID domain must be {APACHE_DOMAIN} or a subdomain")
    labels = domain.split(".")
    if any((_MESSAGE_ID_DOMAIN_LABEL_RE.fullmatch(label) is None) for label in labels):
        raise ValueError("Message ID domain is invalid")
