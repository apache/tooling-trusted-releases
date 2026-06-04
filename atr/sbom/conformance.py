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

from __future__ import annotations

import datetime
import urllib.parse
from typing import TYPE_CHECKING, Final

import aiohttp
import yyjson

from . import constants, models
from .maven import cache_read, cache_write
from .utilities import ensure_metadata, get_pointer

if TYPE_CHECKING:
    from cyclonedx.model.component import Component

    from .models.bundle import Bundle


_SAFE_URL_SCHEMES: Final = frozenset({"http", "https"})


def _validate_url_protocol(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _SAFE_URL_SCHEMES:
        return None
    return url


def assemble_component_identifier(doc: yyjson.Document, patch_ops: models.patch.Patch, index: int) -> None:
    # May be able to derive this from other fields
    pass


def assemble_component_name(doc: yyjson.Document, patch_ops: models.patch.Patch, index: int) -> None:
    # May be able to derive this from other fields
    pass


async def assemble_component_supplier(
    session: aiohttp.ClientSession,
    doc: yyjson.Document,
    patch_ops: models.patch.Patch,
    index: int,
) -> None:
    # We need to detect whether this is an ASF component
    # If it is, we can trivially fix it
    # If not, this is much more difficult
    # NOTE: The sbomqs tool requires a URL (or email) on a supplier
    def make_supplier_op(name: str, url: str) -> models.patch.AddOp:
        return models.patch.AddOp(
            op="add",
            path=f"/components/{index}/supplier",
            value={
                "name": name,
                "url": [url],
            },
        )

    add_asf_op = make_supplier_op(
        constants.conformance.THE_APACHE_SOFTWARE_FOUNDATION,
        constants.conformance.APACHE_URL,
    )

    if get_pointer(doc, f"/components/{index}/publisher") == constants.conformance.THE_APACHE_SOFTWARE_FOUNDATION:
        patch_ops.append(add_asf_op)
        return

    if purl_value := get_pointer(doc, f"/components/{index}/purl"):
        prefix = tuple(purl_value.split("/", 2)[:2])
        if prefix in constants.conformance.KNOWN_PURL_SUPPLIERS:
            supplier, supplier_url = constants.conformance.KNOWN_PURL_SUPPLIERS[prefix]
            patch_ops.append(make_supplier_op(supplier, supplier_url))
            return
        for key, value in constants.conformance.KNOWN_PURL_PREFIXES.items():
            if purl_value.startswith(key):
                supplier, supplier_url = value
                patch_ops.append(make_supplier_op(supplier, supplier_url))
                return

    if group_id := get_pointer(doc, f"/components/{index}/group"):
        if group_id.startswith("org.apache."):
            patch_ops.append(add_asf_op)
            return
        if group_id.startswith("com.github."):
            github_user = group_id.split(".", 2)[2]
            patch_ops.append(
                make_supplier_op(
                    f"@github/{github_user}",
                    f"https://github.com/{github_user}",
                )
            )
            return

    if bom_ref := get_pointer(doc, f"/components/{index}/bom-ref"):
        if bom_ref.startswith("pkg:maven/org.apache."):
            patch_ops.append(add_asf_op)
            return

    if purl_value and purl_value.startswith("pkg:maven/"):
        package_version = purl_value.removeprefix("pkg:maven/").rsplit("?", 1)[0]
        if "@" not in package_version:
            return
        package, version = package_version.rsplit("@", 1)
        package = package.replace("/", ":")
        key = f"{package} / {version}"

        def supplier_op_from_url(url: str) -> models.patch.AddOp | None:
            if _validate_url_protocol(url) is None:
                return None
            if url.startswith("https://github.com/"):
                github_user = url.removeprefix("https://github.com/").split("/", 1)[0]
                return make_supplier_op(f"@github/{github_user}", f"https://github.com/{github_user}")
            domain = urllib.parse.urlparse(url).netloc
            if domain.endswith(".github.io"):
                github_user = domain.removesuffix(".github.io")
                return make_supplier_op(f"@github/{github_user}", f"https://github.com/{github_user}")
            if ("//" in url) and (url.count("/") == 2):
                url += "/"
            return make_supplier_op(url, url)

        cache = cache_read()

        if key in cache:
            cached = cache[key]
            if cached is None:
                return
            if isinstance(cached, str) and cached:
                op = supplier_op_from_url(cached)
                if op is not None:
                    patch_ops.append(op)
            return

        url = f"https://api.deps.dev/v3/systems/MAVEN/packages/{package}/versions/{version}"
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                data = yyjson.Document(await response.read())
        except aiohttp.ClientResponseError:
            cache[key] = None
            cache_write(cache)
            return
        links = get_pointer(data, "/links") or []
        homepage = None
        for link in links:
            if isinstance(link, dict) and (link.get("label") == "HOMEPAGE"):
                homepage = link.get("url")
                break
        if homepage:
            op = supplier_op_from_url(homepage)
            if op is not None:
                patch_ops.append(op)
            cache[key] = homepage
        else:
            cache[key] = None
        cache_write(cache)
        return


def assemble_component_version(doc: yyjson.Document, patch_ops: models.patch.Patch, index: int) -> None:
    # May be able to derive this from other fields
    pass


def assemble_dependencies(doc: yyjson.Document, patch_ops: models.patch.Patch) -> None:
    # This is just a warning
    # There is nothing we can do, but we should alert the user
    pass


def assemble_metadata(doc: yyjson.Document, patch_ops: models.patch.Patch) -> None:
    ensure_metadata(doc, patch_ops)


def assemble_metadata_author(doc: yyjson.Document, patch_ops: models.patch.Patch) -> None:
    # We can't synthesise an author here
    pass


def assemble_metadata_component(doc: yyjson.Document, patch_ops: models.patch.Patch) -> None:
    # This is a hard failure
    # The SBOM is completely invalid, and there is no recovery
    raise ValueError("metadata.component is required")


def assemble_metadata_supplier(doc: yyjson.Document, patch_ops: models.patch.Patch) -> None:
    assemble_metadata(doc, patch_ops)
    # NOTE: The sbomqs tool requires a URL (or email) on a supplier
    patch_ops.append(models.patch.AddOp(op="add", path="/metadata/supplier", value=constants.conformance.ASF_ENTITY))


def assemble_metadata_timestamp(doc: yyjson.Document, patch_ops: models.patch.Patch) -> None:
    assemble_metadata(doc, patch_ops)
    if get_pointer(doc, "/metadata/timestamp") is None:
        patch_ops.append(
            models.patch.AddOp(
                op="add",
                path="/metadata/timestamp",
                value=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )


def ntia_2021_issues(
    bundle: Bundle,
) -> tuple[list[models.conformance.Missing], list[models.conformance.Missing]]:
    # 1. Supplier
    # ECMA-424 1st edition says that this is the supplier of the primary component
    # Despite it being bom.metadata.supplier and not bom.metadata.component.supplier
    # bom.metadata.supplier,
    # bom.components[].supplier

    # 2. Component Name
    # NOTE: The CycloneDX guide is missing bom.metadata.component.name
    # bom.components[].name

    # 3. Component Version
    # NOTE: The CycloneDX guide is missing bom.metadata.component.version
    # bom.components[].version

    # 4. Other Unique Identifiers
    # NOTE: The CycloneDX guide is missing bom.metadata.component.cpe,purl,swid
    # bom.components[].cpe,purl,swid
    # NOTE: NTIA 2021 does not require unique identifiers
    # This is clear from the CISA 2025 draft adding this requirement

    # 5. Dependency Relationship
    # bom.dependencies[]
    # NTIA 2021 requires this, but it can only be checked out of band

    # 6. Author of SBOM Data
    # bom.metadata.author

    # 7. Timestamp
    # bom.metadata.timestamp
    # NTIA 2021 only requires that this be present
    # It does not mandate a format

    warnings: list[models.conformance.Missing] = []
    errors: list[models.conformance.Missing] = []
    bom_value = bundle.bom
    original_metadata = bundle.doc.get_pointer("/metadata")

    if bom_value.metadata:
        if bom_value.metadata.supplier is None:
            errors.append(models.conformance.MissingProperty(property=models.conformance.Property.METADATA_SUPPLIER))

        if bom_value.metadata.component is not None:
            if not bom_value.metadata.component.name:
                errors.append(
                    models.conformance.MissingComponentProperty(property=models.conformance.ComponentProperty.NAME)
                )

            if bom_value.metadata.component.version is None:
                errors.append(
                    models.conformance.MissingComponentProperty(property=models.conformance.ComponentProperty.VERSION)
                )

            cpe_is_none = bom_value.metadata.component.cpe is None
            purl_is_none = bom_value.metadata.component.purl is None
            swid_is_none = bom_value.metadata.component.swid is None
            type_is_file = bom_value.metadata.component.type == "file"
            if cpe_is_none and purl_is_none and swid_is_none and (not type_is_file):
                warnings.append(
                    models.conformance.MissingComponentProperty(
                        property=models.conformance.ComponentProperty.IDENTIFIER
                    )
                )
        else:
            errors.append(models.conformance.MissingProperty(property=models.conformance.Property.METADATA_COMPONENT))

        if len(bom_value.metadata.authors) < 1 and bom_value.metadata.manufacturer is None:
            errors.append(
                models.conformance.MissingProperty(property=models.conformance.Property.METADATA_AUTHOR_OR_MANUFACTURER)
            )
        if original_metadata.get("timestamp") is None:
            errors.append(models.conformance.MissingProperty(property=models.conformance.Property.METADATA_TIMESTAMP))
    else:
        errors.append(models.conformance.MissingProperty(property=models.conformance.Property.METADATA))

    components: list[Component] = list(bom_value.components)
    for index, component in enumerate(components):
        component_type = component.type
        component_friendly_name = component.name
        if component_type:
            component_friendly_name = f"{component_type.value}: {component_friendly_name}"
        if component.supplier is None:
            errors.append(
                models.conformance.MissingComponentProperty(
                    property=models.conformance.ComponentProperty.SUPPLIER,
                    index=index,
                    component=component_friendly_name,
                )
            )

        if not component.name:
            errors.append(
                models.conformance.MissingComponentProperty(
                    property=models.conformance.ComponentProperty.NAME,
                    index=index,
                    component=component_friendly_name,
                )
            )

        if not component.version:
            errors.append(
                models.conformance.MissingComponentProperty(
                    property=models.conformance.ComponentProperty.VERSION,
                    index=index,
                    component=component_friendly_name,
                )
            )

        component_cpe_is_none = component.cpe is None
        component_purl_is_none = component.purl is None
        component_swid_is_none = component.swid is None
        component_type_is_file = component_type == "file"
        if component_cpe_is_none and component_purl_is_none and component_swid_is_none and (not component_type_is_file):
            warnings.append(
                models.conformance.MissingComponentProperty(
                    property=models.conformance.ComponentProperty.IDENTIFIER,
                    index=index,
                    component=component_friendly_name,
                )
            )

    if not bom_value.dependencies:
        warnings.append(models.conformance.MissingProperty(property=models.conformance.Property.DEPENDENCIES))

    return warnings, errors


async def ntia_2021_patch(
    session: aiohttp.ClientSession,
    doc: yyjson.Document,
    errors: list[models.conformance.Missing],
) -> models.patch.Patch:
    patch_ops: models.patch.Patch = []
    for error in errors:
        match error:
            case models.conformance.MissingProperty(property=property_value):
                match property_value:
                    case models.conformance.Property.METADATA_SUPPLIER:
                        assemble_metadata_supplier(doc, patch_ops)
                    case models.conformance.Property.METADATA:
                        assemble_metadata(doc, patch_ops)
                    case models.conformance.Property.METADATA_COMPONENT:
                        assemble_metadata_component(doc, patch_ops)
                    case models.conformance.Property.METADATA_AUTHOR_OR_MANUFACTURER:
                        assemble_metadata_author(doc, patch_ops)
                    case models.conformance.Property.METADATA_TIMESTAMP:
                        assemble_metadata_timestamp(doc, patch_ops)
                    case models.conformance.Property.DEPENDENCIES:
                        assemble_dependencies(doc, patch_ops)
            case models.conformance.MissingComponentProperty(property=property_value, index=index):
                match property_value:
                    case models.conformance.ComponentProperty.SUPPLIER if index is not None:
                        await assemble_component_supplier(session, doc, patch_ops, index)
                    case models.conformance.ComponentProperty.NAME if index is not None:
                        assemble_component_name(doc, patch_ops, index)
                    case models.conformance.ComponentProperty.VERSION if index is not None:
                        assemble_component_version(doc, patch_ops, index)
                    case models.conformance.ComponentProperty.IDENTIFIER if index is not None:
                        assemble_component_identifier(doc, patch_ops, index)
    return patch_ops
