# 3.21. SBOM architecture

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.20.` [Resource management](resource-management)

**Next**: (none)

**Sections**:

* [Overview](#overview)
* [Current status](#current-status)
* [The SBOM package](#the-sbom-package)
* [The SBOM CLI](#the-sbom-cli)
* [Package modules](#package-modules)
* [External tools](#external-tools)
* [How the server uses the package](#how-the-server-uses-the-package)
* [The SBOM report](#the-sbom-report)
* [Related documentation](#related-documentation)

## Overview

This page describes how ATR's SBOM support is put together, for developers working on ATR itself. For a release manager's view of what the SBOM features do and how to use them, see [SBOM workflows](sbom-workflows).

ATR works with SBOMs in the CycloneDX format, stored as `.cdx.json` files (and other suffixes) that sit beside the artifact they describe. A project-supplied SBOM is always preferred. Most of the SBOM logic lives in its own package, `atr/sbom/`, which the ATR server drives through a set of background tasks. Only the read-only report is currently exposed in the UI; see [Current status](#current-status).

## Current status

Only the read-only SBOM report is currently reachable from the UI. A file that is a CycloneDX JSON SBOM gets a *View SBOM* button in a release's checks (`atr/get/checks.py`), which opens the report described below.

The other workflows, generation, conversion, OSV scanning, and augmentation, are present in the code, as task handlers in `atr/tasks/sbom.py` and as routes in `atr/post/draft.py` and `atr/post/sbom.py`, but nothing in the UI triggers them, so they are dormant. The intention is to build these into external tooling rather than the server, which is what the package's own command line (below) is a step toward. The rest of this page describes the whole toolkit, dormant paths included, since the code is all present.

## The SBOM package

`atr/sbom/` is its own package with its own command line, so the SBOM operations can be driven directly, not only through the server. The standalone entry point is `python -m atr.sbom` (`__main__.py` just calls `cli.main()`), and `cli.py`'s `setup_cli_logging` wires the shared logging up to stderr, so a bare CLI run produces the same diagnostics as the in-server code.

That standalone use is why part of the package reads differently from the rest of the codebase. The modules on the CLI path refer to each other with relative, direct-name imports, for example `from .cyclonedx import validate_cli`, rather than the absolute, module-only style, `import atr.x as x`, that ATR uses everywhere else. The package also holds some server-only modules (`heatmap`, `observations`, `streaming`, and `maintenance`, used by the SBOM tasks and the catalog site, not by the CLI). Those keep ATR's ordinary house style, since the standalone rationale does not apply to them.

The package is not fully decoupled from ATR, though. Its core imports `atr.util` (in `utilities.py` and `osv.py`) alongside `atr.log` and `atr.loggers`, and its `heatmap` and `observations` modules also pull in `atr.metadata` and `atr.models.results`. It is standalone in the sense of having a working command line, not in the sense of having no ATR dependencies.

## The SBOM CLI

`cli.py` is the standalone command line. It takes a command and an SBOM path:

```shell
python -m atr.sbom <command> <sbom-path>
```

The commands map onto the package's operations:

* `license` - list the license warnings (Category B) and errors (Category X).
* `missing` and `where` - list, and locate within the document, the NTIA 2021 fields an SBOM is missing.
* `osv` - scan the components against OSV.
* `outdated` - report an SBOM written by a known-outdated tool.
* `patch-ntia` and `patch-vuln` - emit the JSON patch that would add the missing NTIA metadata, or the OSV findings.
* `merge` - apply the NTIA patch and print the resulting document.
* `scores` - print the sbomqs score, before and after an NTIA merge.
* `validate-cli` and `validate-py` - validate the document, through the CycloneDX CLI and through the CycloneDX Python library respectively.

## Package modules

Each command is a thin wrapper over a module that does the work, and the ATR server calls those same modules directly:

* `cyclonedx.py` validates a document two ways: `validate_cli` shells out to the external `cyclonedx` binary, and `validate_py` uses the CycloneDX Python library.
* `conformance.py` checks a document against the NTIA 2021 minimum data fields.
* `licenses.py` places each declared license into an ASF policy category.
* `sbomqs.py` runs the sbomqs scorer.
* `osv.py` looks components up in OSV.
* `tool.py` detects an SBOM written by an outdated tool version.
* `utilities.py` loads a file into a `Bundle`, builds the NTIA and vulnerability patches, applies them, and writes documents back out.
* `components.py` reads a document into the component breakdown the report renders.
* `models/` holds the typed models the rest of the package passes around, including `bundle`, `conformance`, `licenses`, and `components`.

The package also carries `maven.py`, which maps a date to the Maven plugin version current at that time, `spdx.py`, an SPDX license-expression parser, and a `constants` subpackage.

## External tools

The package, and the tasks around it, drive several external tools:

* **syft** generates a CycloneDX SBOM from an artifact. This runs in the task layer (`atr/tasks/sbom.py`) under the sandbox, not in the package. Generation is currently dormant (see [Current status](#current-status)).
* the **CycloneDX CLI** (`cyclonedx`) validates documents, through `cyclonedx.py`'s `validate_cli`.
* **sbomqs** scores documents, through `sbomqs.py`.
* **OSV** supplies vulnerability data, through `osv.py`.

The CycloneDX Python library is a direct dependency too, used for validation and for writing documents out.

## How the server uses the package

The ATR server wraps the package's operations as background [tasks](tasks):

* `atr/tasks/sbom.py` holds the task handlers. It runs syft for generation, and calls into `atr/sbom/` for conversion, scoring, scanning, and augmentation. The task types include `SBOM_GENERATE_CYCLONEDX`, `SBOM_CONVERT`, `SBOM_TOOL_SCORE`, `SBOM_QS_SCORE`, `SBOM_OSV_SCAN`, and `SBOM_AUGMENT`.
* `atr/storage/writers/sbom.py` queues those tasks as the acting committee member.
* `atr/post/draft.py` holds the generation and conversion routes (each would land its output as a new revision), and `atr/post/sbom.py` holds the report's scan and augment routes. None of these is currently triggered from the UI (see [Current status](#current-status)).
* `atr/analysis.py` classifies files, with `is_cyclonedx_json` and `is_cyclonedx_xml` gating which operations apply to a given file.

## The SBOM report

`atr/get/sbom.py` builds the report programmatically with `atr/htm.py` rather than a template. The `quality` handler serves `/sbom/quality/<project>/<version>/<file>` and is reached from a file's check row (see `atr/get/checks.py` and `check-selected-path-table.html`).

The page reads the most recent `SBOM_TOOL_SCORE` task for the file at the release's latest revision, and reads the component breakdown straight from the SBOM through `atr/sbom/components.py`. It deliberately does not render a single headline score: it surfaces the component list, license categories, declared vulnerabilities, and the conformance, outdated-tool, and CycloneDX CLI findings, so a reader sees the underlying issues rather than a number.

## Related documentation

* [SBOM workflows](sbom-workflows) is the user-facing counterpart to this page.
* [Tasks](tasks) explains the background task system these operations run on.
* [Checks](checks) covers the wider check pipeline the SBOM report is reached from.
