# 2.6. SBOM workflows

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.5.` [Trusted Publishing](trusted-publishing)

**Next**: `3.1.` [Running the server](running-the-server)

**Sections**:

* [Overview](#overview)
* [Supported SBOM operations](#supported-sbom-operations)
* [Convert XML SBOM to JSON](#convert-xml-sbom-to-json)
* [CycloneDX validation](#cyclonedx-validation)
* [SBOM scoring and validation](#sbom-scoring-and-validation)
* [Vulnerability scanning](#vulnerability-scanning)
* [SBOM augmentation](#sbom-augmentation)
* [Related interfaces](#related-interfaces)
* [Related documentation](#related-documentation)

## Overview

ATR provides multiple Software Bill of Materials (SBOM) workflows based on the CycloneDX specification. These workflows help projects generate, validate, augment, convert, and analyze SBOM files for release artifacts.

ATR integrates several external tools including:

* syft
* CycloneDX CLI
* sbomqs
* OSV

These workflows are primarily implemented in the `atr/tasks/sbom.py` module and exposed through draft and report interfaces.

## Supported SBOM operations

ATR supports several SBOM workflows for generation, conversion, analysis, and augmentation.

### Generate CycloneDX SBOM

ATR can generate a CycloneDX JSON SBOM from supported release artifacts including:

* `.tar.gz`
* `.tgz`
* `.zip`
* `.jar`

SBOM generation uses the `syft` tool and produces `.cdx.json` files.

Generation tasks are queued through:

* `atr/post/draft.py`
* `atr/storage/writers/sbom.py`

Core implementation:

* `generate_cyclonedx`
* `_generate_cyclonedx_core`

## Convert XML SBOM to JSON

ATR can convert CycloneDX XML SBOM files into JSON format.

Supported input:

* `.cdx.xml`

Generated output:

* `.cdx.json`

Core implementation:

* `convert_cyclonedx`
* `_convert_cyclonedx_core`

## CycloneDX validation

ATR validates CycloneDX SBOM files using the CycloneDX CLI.

Validation workflows detect:

* schema violations
* malformed SBOM structures
* invalid metadata
* specification compatibility issues

Related modules include:

* `atr/sbom/cyclonedx.py`

## SBOM scoring and validation

ATR performs several validation and scoring operations for CycloneDX SBOM files.

These include:

* CycloneDX CLI validation
* NTIA 2021 conformance checks
* license analysis
* vulnerability analysis
* tool version analysis

The scoring workflow is implemented through:

* `score_tool`

Related modules include:

* `atr/sbom/conformance.py`
* `atr/sbom/licenses.py`
* `atr/sbom/cyclonedx.py`
* `atr/sbom/sbomqs.py`

## Vulnerability scanning

ATR supports vulnerability analysis through OSV integration.

The OSV workflow:

* scans CycloneDX SBOM files
* identifies known vulnerabilities
* augments SBOM files with vulnerability information

Core implementation:

* `osv_scan`
* `bundle_to_vuln_patch`

Related modules:

* `atr/sbom/osv.py`

## SBOM augmentation

ATR can augment existing SBOM files with additional metadata and NTIA-related properties.

Augmentation workflows may generate updated revisions containing modified SBOM files.

Core implementation:

* `augment`
* `bundle_to_ntia_patch`

## Related interfaces

SBOM functionality is exposed through several application layers.

Task handlers:

* `atr/tasks/sbom.py`

POST endpoints:

* `atr/post/sbom.py`
* `atr/post/draft.py`

GET interfaces:

* `atr/get/sbom.py`

Storage writers:

* `atr/storage/writers/sbom.py`

Templates:

* `atr/templates/draft-tools.html`
* `atr/templates/check-selected-path-table.html`

## Related documentation

Additional SBOM-related behavior is described in:

* [Checks](checks)
* [Running the server](running-the-server)
* [Overview of the code](overview-of-the-code)
