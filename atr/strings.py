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

"""User-facing UI strings, kept in one place so the same wording is shared between templates and code."""

from typing import Final

# Button labels
REGENERATE_KEYS_BUTTON: Final = "Regenerate KEYS file"
RESET_INACTIVITY_CLOCK_BUTTON: Final = "Reset inactivity clock"
RESOLVE_VOTE_BUTTON: Final = "Resolve vote"
VERIFY_DISTRIBUTION_BUTTON: Final = "Verify a third-party distribution"

# CAP approval votes
CAP_NOT_CONFIGURED: Final = "CAP approval is not configured. Please contact an administrator."

# Link labels
HOW_TO_FIX_THIS: Final = "How to fix this"

# Release information labels
COMMIT_HASH_LABEL: Final = "Commit hash"

# File classification labels
FILE_CLASS_BINARY: Final = "Binary artifact"
FILE_CLASS_DIRECTORY: Final = "Directory"
FILE_CLASS_DISALLOWED: Final = "Disallowed file"
FILE_CLASS_METADATA: Final = "Metadata file"
FILE_CLASS_SBOM: Final = "SBOM"
FILE_CLASS_SOURCE: Final = "Source artifact"

# Project metadata notices
ASF_YAML_FULL_SYNC_NOTICE: Final = (
    "This project's configuration is synced from .asf.yaml in full mode. The next push to the repository's default"
    " branch will update all settings, changes made here are not permanent."
)
ASF_YAML_SYNC_NOTICE: Final = (
    "This project's metadata is synced from .asf.yaml. The next push to the repository's default branch"
    " overwrites settings with any values the file sets, so make any permanent changes there."
)

# Dist watcher rule kinds, keyed by DistRuleKind value, in plain terms for the admin who edits them
DIST_RULE_KIND_DESCRIPTIONS: Final[dict[str, str]] = {
    "project_remap": (
        "Sends a release to a named project when its dist path would otherwise land on the wrong key, or on none."
    ),
    "grouping_bucket": (
        "Marks a directory that only groups projects together, for any committee. The project name is read from "
        "the file, not from this directory."
    ),
    "committee_bucket": (
        "A grouping directory scoped to one committee - used when the same word is a real project somewhere else."
    ),
    "excluded_part": "Marks a path segment that never holds a release, so anything beneath it is skipped.",
    "name_build_suffix": (
        "A trailing word to drop from a release name when working out the project, like -incubating or -bin."
    ),
    "airflow_provider_area": (
        "An Airflow directory whose per-provider packages are rolled up into one dated bundle release."
    ),
}

KEYS_CHECK_CERTIFICATE_FINDINGS: Final[dict[str, tuple[str, str]]] = {
    "unparseable": (
        "Unreadable",
        "The OpenPGP library can't parse the stored text, or ATR's own reader can't read the user IDs in it. ATR"
        " can't parse the stored text. Either ask for the key to be exported again, or investigate the exported"
        " syntax.",
    ),
    "empty": (
        "No certificate",
        "The text parses, but holds no certificates at all. Try exporting the key again.",
    ),
    "wrong-fingerprint": (
        "Another key's certificate",
        "The text holds exactly one certificate, but its fingerprint is different to the fingerprint of the key. The"
        " stored text holds a certificate for a different key. Replace it by hand with a fresh export of this key.",
    ),
    "no-own-certificate": (
        "Only other keys' certificates",
        "The text holds several certificates, and none of them is this key's. The stored text holds several"
        " certificates, but none for this key. Replace it by hand with a fresh export of this key.",
    ),
    "own-certificate-repeated": (
        "Certificate repeated",
        "This key's certificate appears more than once in the text. Edit the key to remove the duplicates, or ask for"
        " a fresh export. (We could potentially just filter duplicates in ATR, but we want to warn people about bad"
        " keys.)",
    ),
    "multi-own-not-first": (
        "Other certificates before this key's",
        "The text holds several certificates, this key's appears once, and it isn't the first. Run"
        " scripts/split_certificate_blocks.py to list the certificates. The script can be used again with --apply to"
        " actually fix the database, but take a backup first.",
    ),
    "multi-own-first": (
        "Other certificates after this key's",
        "The text holds several certificates, and this key's is the first. Run scripts/split_certificate_blocks.py to"
        " list the certificates. The script can be used again with --apply to actually fix the database, but take a"
        " backup first.",
    ),
    "shared-text": (
        "Same text as other keys",
        "Another key's row stores exactly the same text as this one. This usually comes with other keys' certificates"
        " in the text. Run scripts/split_certificate_blocks.py to list the certificates. The script can be used again"
        " with --apply to actually fix the database, but take a backup first.",
    ),
    "signing-keys": (
        "Signing key rows missing",
        "The primary key or one of the subkeys in this key's certificate has no row in the SigningKey table. The"
        " database has no rows for some of the key's signing keys or subkeys. Run scripts/backfill_signing_keys.py to"
        " add them.",
    ),
    "metadata": (
        "Stored details out of date",
        "The stored primary user ID isn't among the user IDs in the text, or the stored latest self-signature date"
        " differs from the date ATR computes from the key. A stored value that is empty isn't checked. ATR refreshes"
        " them when a changed copy of the key is imported, or you can update them by hand.",
    ),
}

KEYS_CHECK_OWNER_FINDINGS: Final[dict[str, tuple[str, str]]] = {
    "different": (
        "Names another account",
        "The key doesn't name its owner in ATR, and names another account. Check Whimsy for each ASF UID listed in"
        " this row to see whether it lists this key's fingerprint. If the owner in ATR is wrong, fix it by hand.",
    ),
    "unowned": (
        "No owner in ATR",
        "The key names an account, but has no owner in ATR. Check Whimsy for each ASF UID listed in this row to see"
        " whether it lists this key's fingerprint. If one does, set it as the owner by hand. Otherwise, leave the key"
        " without an owner.",
    ),
    "unnamed": (
        "Names no account",
        "No user ID on the key names an ASF account, so the check can't confirm the owner. The owner usually came"
        " from the person who added the key. No action is needed unless the owner looks wrong.",
    ),
    "named": (
        "Owner named, but not first",
        "The key names its owner in ATR, but another @apache.org address comes first. When ATR imports a key again,"
        " it keeps the owner it already has as long as one of the key's user IDs still names that owner. It doesn't"
        " switch to whichever @apache.org address happens to come first on the key.",
    ),
}
