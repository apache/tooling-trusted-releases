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

import asyncio
import json
import os
import pathlib
from typing import Any, Final

import aiofiles
import aiofiles.os
import aiohttp
import orjson

import atr.analysis as analysis
import atr.archives as archives
import atr.classify as classify
import atr.config as config
import atr.db as db
import atr.hashes as hashes
import atr.log as log
import atr.models.args as args
import atr.models.attestable as attestable
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.sbom as sbom
import atr.storage as storage
import atr.tasks.checks as checks
import atr.util as util

_CONFIG: Final = config.get()
_OSV_UNAVAILABLE_MESSAGE: Final = "The OSV service could not be reached; try the scan again later"


class SBOMConversionError(Exception):
    """Custom exception for SBOM conversion failures."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class SBOMGenerationError(Exception):
    """Custom exception for SBOM generation failures."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class SBOMScanningError(Exception):
    """Custom exception for SBOM scanning failures."""

    pass


class SBOMScoringError(Exception):
    """Raised on a failure to score an SBOM."""

    def __init__(self, msg: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(msg)
        self.context = context if (context is not None) else {}


@checks.with_model(args.FileArgs)
async def augment(args: args.FileArgs) -> results.Results | None:
    revision_str = str(args.revision_number)
    path_str = str(args.file_path)

    base_dir = paths.get_unfinished_dir_for(args.project_key, args.version_key, args.revision_number)
    if not await aiofiles.os.path.isdir(base_dir):
        raise SBOMScoringError("Revision directory does not exist", {"base_dir": str(base_dir)})
    full_path = base_dir / path_str
    full_path_str = str(full_path)
    if not (analysis.is_cyclonedx_json(full_path_str) and await aiofiles.os.path.isfile(full_path)):
        raise SBOMScoringError("SBOM file does not exist", {"file_path": path_str})
    # Read from the old revision
    bundle = sbom.utilities.path_to_bundle(full_path.path)
    if not bundle:
        raise SBOMScoringError("Could not load bundle")
    patch_ops = await sbom.utilities.bundle_to_ntia_patch(bundle)
    new_full_path: pathlib.Path | None = None
    new_full_path_str: str | None = None
    new_version = None
    if patch_ops:
        new_version, merged = sbom.utilities.apply_patch("augment", revision_str, bundle, patch_ops)
        description = "SBOM augmentation through web interface"
        async with storage.write(args.asf_uid) as write:
            wacp = await write.as_project_committee_participant(args.project_key)

            async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
                nonlocal new_full_path, new_full_path_str
                new_full_path = (path / path_str).path
                new_full_path_str = str(new_full_path)
                # Write to the new revision
                log.info(f"Writing augmented SBOM to {new_full_path_str}")
                await aiofiles.os.remove(new_full_path)
                async with aiofiles.open(new_full_path, "w", encoding="utf-8") as f:
                    await f.write(orjson.dumps(merged).decode())

            await wacp.revision.create_revision_with_quarantine(
                args.project_key,
                args.version_key,
                args.asf_uid or "unknown",
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )

    return results.SBOMAugment(
        kind="sbom_augment",
        path=(new_full_path_str if (new_full_path_str is not None) else full_path_str),
        bom_version=new_version,
    )


@checks.with_model(args.ConvertCycloneDX)
async def convert_cyclonedx(args: args.ConvertCycloneDX) -> results.Results | None:
    """Generate a JSON CycloneDX SBOM from a given XML SBOM."""
    try:
        result_data = await _convert_cyclonedx_core(args.artifact_path, args.output_path, args.revision)
        log.info(f"Successfully converted CycloneDX SBOM for {args.artifact_path}")
        msg = result_data["message"]
        if not isinstance(msg, str):
            raise SBOMConversionError(f"Invalid message type: {type(msg)}")
        return results.SBOMConvert(
            kind="sbom_convert", path=str(args.output_path), bom_version=result_data.get("version")
        )
    except (archives.ExtractionError, SBOMGenerationError) as e:
        log.error(f"SBOM conversion failed for {args.artifact_path}: {e}")
        raise


@checks.with_model(args.FileArgs)
async def generate(args: args.FileArgs) -> results.Results | None:
    revision_str = str(args.revision_number)
    path_str = str(args.file_path)

    base_dir = paths.get_unfinished_dir_for(args.project_key, args.version_key, args.revision_number)
    if not await aiofiles.os.path.isdir(base_dir):
        raise SBOMGenerationError("Revision directory does not exist", {"base_dir": str(base_dir)})
    if not await aiofiles.os.path.isfile(base_dir / path_str):
        raise SBOMGenerationError("Artifact file does not exist", {"file_path": path_str})
    sbom_rel_path = path_str + ".cdx.json"
    bom_version: int | None = None
    description = "SBOM generation through the API"
    async with storage.write(args.asf_uid) as write:
        wacp = await write.as_project_committee_participant(args.project_key)

        async def modify(
            path: safe.StatePath, _old_rev: sql.Revision | None
        ) -> dict[safe.RelPath, attestable.ProvenanceV2] | None:
            nonlocal bom_version
            artifact_path = path / path_str
            output_path = path / sbom_rel_path
            if await aiofiles.os.path.exists(output_path):
                raise SBOMGenerationError("SBOM file already exists", {"file_path": sbom_rel_path})
            source_content_hash = await hashes.compute_file_hash(artifact_path)
            await _generate_cyclonedx_core(artifact_path, output_path, path_str, str(args.version_key))
            bundle = sbom.utilities.path_to_bundle(output_path.path)
            if not bundle:
                raise SBOMGenerationError("Could not load bundle")
            patch_ops = await sbom.utilities.bundle_to_ntia_patch(bundle)
            if patch_ops:
                bom_version, merged = sbom.utilities.apply_patch("augment", revision_str, bundle, patch_ops)
                await aiofiles.os.remove(output_path)
                async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                    await f.write(orjson.dumps(merged).decode())
            provenance = attestable.ProvenanceV2(
                generator=attestable.GeneratorV2.SBOM_FROM_ARTIFACT,
                metadata={
                    "initiated_by": args.asf_uid,
                    "source_content_hashes": {path_str: source_content_hash},
                    "source_paths": [path_str],
                },
            )
            return {safe.RelPath(sbom_rel_path): provenance}

        result = await wacp.revision.create_revision_with_quarantine(
            args.project_key,
            args.version_key,
            args.asf_uid or "unknown",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description=description,
            modify=modify,
        )

    return results.SBOMGenerate(
        kind="sbom_generate",
        path=sbom_rel_path,
        bom_version=bom_version,
        revision_number=(result.number if isinstance(result, sql.Revision) else None),
    )


@checks.with_model(args.GenerateCycloneDX)
async def generate_cyclonedx(args: args.GenerateCycloneDX) -> results.Results | None:
    """Generate a CycloneDX SBOM for the given artifact and write it to the output path."""
    # Tasks queued before source_name existed fall back to the bare filename
    source_name = str(args.source_name) if (args.source_name is not None) else args.artifact_path.name
    source_version = str(args.source_version) if (args.source_version is not None) else None
    try:
        result_data = await _generate_cyclonedx_core(args.artifact_path, args.output_path, source_name, source_version)
        log.info(f"Successfully generated CycloneDX SBOM for {args.artifact_path}")
        msg = result_data["message"]
        if not isinstance(msg, str):
            raise SBOMGenerationError(f"Invalid message type: {type(msg)}")
        return results.SBOMGenerateCycloneDX(
            kind="sbom_generate_cyclonedx",
            msg=msg,
        )
    except (archives.ExtractionError, SBOMGenerationError) as e:
        log.error(f"SBOM generation failed for {args.artifact_path}: {e}")
        raise


@checks.with_model(args.FileArgs)
async def osv_scan(args: args.FileArgs) -> results.Results | None:
    revision_str = str(args.revision_number)
    path_str = str(args.file_path)

    base_dir = paths.get_unfinished_dir_for(args.project_key, args.version_key, args.revision_number)
    if not await aiofiles.os.path.isdir(base_dir):
        raise SBOMScanningError("Revision directory does not exist", {"base_dir": str(base_dir)})
    full_path = base_dir / path_str
    full_path_str = str(full_path)
    if not (analysis.is_cyclonedx_json(full_path_str) and await aiofiles.os.path.isfile(full_path)):
        raise SBOMScanningError("SBOM file does not exist", {"file_path": path_str})
    bundle = sbom.utilities.path_to_bundle(full_path.path)
    if not bundle:
        raise SBOMScanningError("Could not load bundle")
    try:
        vulnerabilities, ignored = await sbom.osv.scan_bundle(bundle)
    except (TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientPayloadError) as e:
        raise SBOMScanningError(_OSV_UNAVAILABLE_MESSAGE) from e
    except aiohttp.ClientResponseError as e:
        if (e.status != 429) and (e.status < 500):
            raise
        raise SBOMScanningError(_OSV_UNAVAILABLE_MESSAGE) from e
    patch_ops = await sbom.utilities.bundle_to_vuln_patch(bundle, vulnerabilities)
    components = []
    for v in vulnerabilities:
        components.append(
            results.OSVComponent(
                purl=v.ref,
                vulnerabilities=[
                    results.VulnerabilityDetails.model_validate(vuln.model_dump()) for vuln in v.vulnerabilities
                ],
            )
        )

    new_full_path: pathlib.Path | None = None
    new_full_path_str: str | None = None
    new_version, merged = sbom.utilities.apply_patch("osv-scan", revision_str, bundle, patch_ops)
    description = "SBOM vulnerability scan through web interface"
    async with storage.write(args.asf_uid) as write:
        wacp = await write.as_project_committee_participant(args.project_key)

        async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
            nonlocal new_full_path, new_full_path_str
            new_full_path = (path / str(args.file_path)).path
            new_full_path_str = str(new_full_path)
            # Write to the new revision
            log.info(f"Writing updated SBOM to {new_full_path_str}")
            await aiofiles.os.remove(new_full_path)
            async with aiofiles.open(new_full_path, "w", encoding="utf-8") as f:
                await f.write(orjson.dumps(merged).decode())

        await wacp.revision.create_revision_with_quarantine(
            args.project_key,
            args.version_key,
            args.asf_uid or "unknown",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description=description,
            modify=modify,
        )

    return results.SBOMOSVScan(
        kind="sbom_osv_scan",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        bom_version=new_version,
        file_path=full_path_str,
        new_file_path=new_full_path_str or full_path_str,
        components=components,
        ignored=ignored,
    )


@checks.with_model(args.FileArgs)
async def score_qs(args: args.FileArgs) -> results.Results | None:
    path_str = str(args.file_path)

    base_dir = paths.get_unfinished_dir_for(args.project_key, args.version_key, args.revision_number)
    if not await aiofiles.os.path.isdir(base_dir):
        raise SBOMScoringError("Revision directory does not exist", {"base_dir": str(base_dir)})
    full_path = base_dir / path_str
    full_path_str = str(full_path)
    if not (analysis.is_cyclonedx_json(full_path_str) and await aiofiles.os.path.isfile(full_path)):
        raise SBOMScoringError("SBOM file does not exist", {"file_path": path_str})
    proc = await asyncio.create_subprocess_exec(
        "sbomqs",
        "score",
        "--json",
        "--",
        full_path.name,
        cwd=str(full_path.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # TODO: Timeout should probably be a lot shorter
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise SBOMScoringError(
            "sbomqs command failed",
            {"returncode": proc.returncode, "stderr": stderr.decode("utf-8", "ignore")},
        )
    report_obj = results.SbomQsReport.model_validate(json.loads(stdout.decode("utf-8")))
    return results.SBOMQsScore(
        kind="sbom_qs_score",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        file_path=args.file_path,
        report=report_obj,
    )


@checks.with_model(args.ScoreArgs)
async def score_tool(args: args.ScoreArgs) -> results.Results | None:
    path_str = str(args.file_path)

    base_dir = paths.get_unfinished_dir_for(args.project_key, args.version_key, args.revision_number)
    previous_base_dir = None
    if args.previous_release_version is not None:
        previous_base_dir = paths.get_finished_dir_for(args.project_key, args.previous_release_version)
    if not await aiofiles.os.path.isdir(base_dir):
        raise SBOMScoringError("Revision directory does not exist", {"base_dir": str(base_dir)})
    full_path = base_dir / path_str
    full_path_str = str(full_path)
    if not (analysis.is_cyclonedx_json(full_path_str) and await aiofiles.os.path.isfile(full_path)):
        raise SBOMScoringError("SBOM file does not exist", {"file_path": path_str})
    bundle = sbom.utilities.path_to_bundle(full_path.path)
    if not bundle:
        raise SBOMScoringError("Could not load bundle")
    version, properties = sbom.utilities.get_props_from_bundle(bundle)
    warnings, errors = sbom.conformance.ntia_2021_issues(bundle)
    # TODO: Could update the ATR version with a constant showing last change to the augment/scan
    #  tools so we know if it's outdated
    outdated = sbom.tool.plugin_outdated_version(bundle.bom)
    # Category B licences are only an error where the SBOM describes a source release
    is_source = await _sbom_describes_source(args, path_str)
    _, license_warnings, license_errors = sbom.licenses.check(bundle.bom, is_source_release=is_source)
    vulnerabilities = sbom.osv.vulns_from_bundle(bundle)
    cli_errors = sbom.cyclonedx.validate_cli(bundle)

    prev_version = None
    prev_licenses = None
    prev_vulnerabilities = None
    if previous_base_dir is not None:
        previous_full_path = previous_base_dir / path_str
        try:
            previous_bundle = sbom.utilities.path_to_bundle(previous_full_path.path)
        except FileNotFoundError:
            # Previous release didn't include this file
            previous_bundle = None
        if previous_bundle is not None:
            prev_version, _ = sbom.utilities.get_props_from_bundle(previous_bundle)
            prev_good, prev_license_warnings, prev_license_errors = sbom.licenses.check(
                previous_bundle.bom, include_all=True, is_source_release=is_source
            )
            prev_licenses = [*prev_good, *prev_license_warnings, *prev_license_errors]
            prev_vulnerabilities = sbom.osv.vulns_from_bundle(previous_bundle)

    return results.SBOMToolScore(
        kind="sbom_tool_score",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        bom_version=version,
        prev_bom_version=prev_version,
        file_path=args.file_path,
        warnings=[w.model_dump_json() for w in warnings],
        errors=[e.model_dump_json() for e in errors],
        outdated=[o.model_dump_json() for o in outdated] if outdated else None,
        license_warnings=[w.model_dump_json() for w in license_warnings] if license_warnings else None,
        license_errors=[e.model_dump_json() for e in license_errors] if license_errors else None,
        vulnerabilities=[v.model_dump_json() for v in vulnerabilities],
        prev_licenses=[w.model_dump_json() for w in prev_licenses] if prev_licenses else None,
        prev_vulnerabilities=[v.model_dump_json() for v in prev_vulnerabilities] if prev_vulnerabilities else None,
        atr_props=[{p.name: p.value or ""} for p in properties],
        cli_errors=cli_errors,
    )


async def _convert_cyclonedx_core(
    artifact_path: safe.StatePath, output_path: safe.StatePath, revision_str: safe.RevisionNumber
) -> dict[str, Any]:
    """Core logic to convert XML CycloneDX SBOM to JSON."""
    log.info(f"Generating CycloneDX JSON SBOM for {artifact_path} -> {output_path}")

    # TODO: Should create a new revision here rather than in the caller
    bundle = sbom.utilities.path_to_bundle(pathlib.Path(artifact_path))
    if not bundle:
        raise SBOMConversionError("Could not load bundle")
    sbom.utilities.apply_patch("conversion to JSON", str(revision_str), bundle, [])
    outputter = sbom.utilities.bundle_outputter(bundle)
    text = outputter.output_as_string(indent=2)

    try:
        async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
            await f.write(text)
        log.info(f"Successfully wrote JSON SBOM to {output_path}")
    except Exception as write_err:
        log.exception(f"Failed to write SBOM JSON to {output_path}: {write_err}")
        raise SBOMConversionError(f"Failed to write SBOM to {output_path}: {write_err}") from write_err

    return {
        "message": "Successfully generated and saved CycloneDX SBOM",
        "sbom": text,
        "format": "CycloneDX",
        "version": str(bundle.bom.version),
    }


def _dependency_graph_root(doc: dict[str, Any]) -> dict[str, Any] | None:
    dependencies = doc.get("dependencies", [])
    if not dependencies:
        return None
    depended_on = {ref for entry in dependencies for ref in entry.get("dependsOn", [])}
    roots = [entry["ref"] for entry in dependencies if entry.get("ref") not in depended_on]
    if len(roots) != 1:
        # No single root means no unambiguous primary, so we leave syft's own choice in place
        return None
    return next((c for c in doc.get("components", []) if c.get("bom-ref") == roots[0]), None)


def _extracted_dir(temp_dir: str) -> str | None:
    # Loop through all the dirs in temp_dir
    extract_dir = None
    log.info(f"Checking directories in {temp_dir}: {os.listdir(temp_dir)}")
    for dir_name in os.listdir(temp_dir):
        if dir_name.startswith("."):
            continue
        dir_path = os.path.join(temp_dir, dir_name)
        if os.path.isdir(dir_path):
            if extract_dir is None:
                extract_dir = dir_path
            else:
                return temp_dir
    if extract_dir is None:
        extract_dir = temp_dir
    return extract_dir


async def _generate_cyclonedx_core(
    artifact_path: safe.StatePath, output_path: safe.StatePath, source_name: str, source_version: str | None
) -> dict[str, Any]:
    """Core logic to generate CycloneDX SBOM on failure."""
    log.info(f"Generating CycloneDX SBOM for {artifact_path} -> {output_path}")

    # TODO: Should create a new revision here rather than in the caller
    async with util.async_temporary_directory(prefix="cyclonedx_sbom_") as temp_dir:
        log.info(f"Created temporary directory: {temp_dir}")

        # # Find and validate the root directory
        # try:
        #     root_dir = await asyncio.to_thread(targz.root_directory, artifact_path)
        # except targz.RootDirectoryError as e:
        #     raise SBOMGenerationError(f"Archive root directory issue: {e}", {"artifact_path": artifact_path}) from e
        # except Exception as e:
        #     raise SBOMGenerationError(
        #         f"Failed to determine archive root directory: {e}", {"artifact_path": artifact_path}
        #     ) from e
        #
        # extract_dir = os.path.join(temp_dir, root_dir)

        # Extract the archive to the temporary directory
        # TODO: Ideally we'd have task dependencies or archive caching
        log.info(f"Extracting {artifact_path} to {temp_dir}")
        extracted_size, _extracted_paths = await asyncio.to_thread(
            archives.extract,
            artifact_path,
            str(temp_dir),
            max_size=_CONFIG.MAX_EXTRACT_SIZE,
            chunk_size=_CONFIG.EXTRACT_CHUNK_SIZE,
        )
        log.info(f"Extracted {extracted_size} bytes")

        # Find the root directory
        if (extract_dir := _extracted_dir(str(temp_dir))) is None:
            log.error("No root directory found in archive")
            return {
                "valid": False,
                "message": "No root directory found in archive",
                "errors": [],
            }

        log.info(f"Using root directory: {extract_dir}")

        # Run syft to generate the CycloneDX SBOM
        syft_command = [
            "syft",
            extract_dir,
            "-o",
            "cyclonedx-json",
            "--enrich",
            "all",
            "--base-path",
            f"{temp_dir!s}",
            "--source-name",
            source_name,
        ]
        if source_version is not None:
            syft_command.extend(["--source-version", source_version])
        log.info(f"Running syft: {' '.join(syft_command)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *syft_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)

            stdout_str = stdout.decode("utf-8").strip() if stdout else ""
            stderr_str = stderr.decode("utf-8").strip() if stderr else ""

            if process.returncode != 0:
                log.error(f"syft command failed with code {process.returncode}")
                log.error(f"syft stderr: {stderr_str}")
                log.error(f"syft stdout: {stdout_str[:1000]}...")
                raise SBOMGenerationError(
                    f"syft command failed with code {process.returncode}",
                    {"returncode": process.returncode, "stderr": stderr_str, "stdout": stdout_str[:1000]},
                )

            # Parse the JSON output from syft
            try:
                sbom_data = json.loads(stdout_str)
                _strip_temp_prefix(sbom_data, str(temp_dir))
                _promote_primary_component(sbom_data)
                log.info(f"Successfully parsed syft output for {artifact_path}")

                # Write the SBOM data to the specified output path
                try:
                    # Record ASF as the manufacturer and ATR as a tool before we write
                    doc = sbom_data
                    patch_ops: sbom.models.patch.Patch = []
                    sbom.utilities.record_manufacturer(doc, patch_ops)
                    sbom.utilities.record_atr_tool(doc, patch_ops)
                    merged = sbom.utilities.patch_document(doc, patch_ops)
                    async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                        await f.write(json.dumps(merged, indent=2))
                    log.info(f"Successfully wrote SBOM to {output_path}")
                except Exception as write_err:
                    log.exception(f"Failed to write SBOM JSON to {output_path}: {write_err}")
                    raise SBOMGenerationError(f"Failed to write SBOM to {output_path}: {write_err}") from write_err

                return {
                    "message": "Successfully generated and saved CycloneDX SBOM",
                    "sbom": sbom_data,
                    "format": "CycloneDX",
                    "components": len(sbom_data.get("components", [])),
                }
            except json.JSONDecodeError as e:
                log.error(f"Failed to parse syft output as JSON: {e}")
                raise SBOMGenerationError(
                    f"Failed to parse syft output: {e}",
                    {"error": str(e), "syft_output": stdout_str[:1000]},
                ) from e

        except TimeoutError:
            log.error("syft command timed out after 5 minutes")
            raise SBOMGenerationError("syft command timed out after 5 minutes")
        except FileNotFoundError:
            log.error("syft command not found. Is it installed and in PATH?")
            raise SBOMGenerationError("syft command not found")


def _promote_primary_component(doc: dict[str, Any]) -> None:
    # Scanning an archive, syft describes it with a synthetic file component and catalogues the
    # real package alongside the rest. Where that package is the sole root of the dependency graph
    # it is the thing the SBOM is really about, so it stands in as the primary component, carrying
    # its own identifier and rooting the graph on itself
    metadata = doc.get("metadata")
    if not (isinstance(metadata, dict) and isinstance(metadata.get("component"), dict)):
        return
    root_component = _dependency_graph_root(doc)
    if root_component is None:
        return
    metadata["component"] = root_component
    doc["components"] = [c for c in doc.get("components", []) if c is not root_component]


async def _sbom_describes_source(args: args.ScoreArgs, sbom_rel_path: str) -> bool:
    # Find the classification of the artifact this SBOM pairs with
    artifact_rel = None
    for suffix in analysis.CYCLONEDX_JSON_SUFFIXES:
        if sbom_rel_path.endswith(suffix):
            artifact_rel = sbom_rel_path.removesuffix(suffix)
            break
    if not artifact_rel:
        return False
    release_key = sql.release_key(str(args.project_key), str(args.version_key))
    revision_seq = int(str(args.revision_number))
    async with db.session() as data:
        classification = await data.release_file_classification_at(release_key, artifact_rel, revision_seq)
    if classification is None:
        return False
    return classify.FileType(classification) == classify.FileType.SOURCE


def _strip_temp_prefix(doc: dict[str, Any], temp_dir: str) -> None:
    # syft names file components after the absolute path it scanned, which --base-path leaves alone
    for component in doc.get("components", []):
        name = component.get("name")
        if isinstance(name, str) and name.startswith(temp_dir):
            # Drop the scan directory and the slash that used to join it to the path within
            component["name"] = name.removeprefix(temp_dir).removeprefix("/")
