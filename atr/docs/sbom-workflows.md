# 2.6. SBOM workflows

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.5.` [Trusted Publishing](trusted-publishing)

**Next**: `2.7.` [Staging and voting](staging-and-voting)

**Sections**:

* [Overview](#overview)
* [Reading the SBOM report](#reading-the-sbom-report)
* [Related documentation](#related-documentation)

## Overview

A Software Bill of Materials (SBOM) lists the components that make up a release artifact, so that consumers can see what they are pulling in and check it against known vulnerabilities and license policy. ATR works with SBOMs in the [CycloneDX](https://cyclonedx.org/) format, stored as JSON files with a `.cdx.json` extension.

The best SBOM is one your own build produces, because it knows what actually went into the artifact. When your build produces a CycloneDX JSON SBOM, include it in your release alongside the artifact it describes, and ATR will check it and give you an SBOM report for that file, described below.

ATR reads the SBOMs you supply. Producing them, and converting them from other formats, is a job for your own build.

## Reading the SBOM report

While a release is being prepared, its checks list each file with its results. Any file that is a CycloneDX JSON SBOM carries a **View SBOM** button that opens its SBOM report. The report has two halves: what the SBOM says about the release (Content), and how well-formed the SBOM itself is (Quality). Some of it, the Licenses and Vulnerabilities below and the whole Quality section, appears only once ATR's SBOM checks have run for the file, so parts may be blank on a first look.

### Content

**Components** lists what the SBOM declares is in the artifact, grouped by type, with each component's name, version, licenses, and package URL (PURL) where the SBOM records one. If the SBOM names an overall subject, the report says which component that is. An SBOM that declares no components is worth a second look, as it usually means the tool that wrote it could not see inside the artifact.

**Licenses** places each declared license in a category of the [ASF third party license policy](https://www.apache.org/legal/resolved.html): Category A (permitted), Category B (permitted with conditions), or Category X (not permitted). The categories are read from the license strings the SBOM tool wrote into the file, and those strings can be wrong. A project can declare its license inaccurately in its build file, and an SBOM tool can map a license name to the wrong identifier. A license that ATR does not recognise is treated as Category X.

So treat anything unexpected as something to check rather than a verdict. Look it up against the upstream project, then report a wrong declaration to that project, a wrong mapping to the SBOM tool, and a correctly spelled license that ATR fails to place, or places wrongly, to [ATR](https://github.com/apache/tooling-trusted-release/issues).

Where a component offers a choice of licenses (an `OR` expression), ATR picks the friendliest category and shows the half it chose in colour, with the alternatives it set aside greyed out, so you can see what was on offer without the alternatives reading as a problem.

**Vulnerabilities** shows the known vulnerabilities recorded in the SBOM. An SBOM does not carry these unless whatever produced it added them, so this section is empty when the SBOM records none.

### Quality

The Quality section reports on the SBOM document rather than the release:

* **Conformance** checks the SBOM against the NTIA 2021 minimum data fields and lists anything missing as warnings or errors.
* **Outdated tool** flags an SBOM written by a tool version known to have problems.
* **CycloneDX CLI validation** lists any structural or schema errors found in the file.

The Quality section only has something to show once the checks behind it have run, so if it is empty, give it a moment and reload.

## Related documentation

* [Checks](checks) covers the wider set of automated checks a release goes through, including how the SBOM report is reached.
* [License checks](license-checks) explains the license checking that ATR applies to the release as a whole.
* [SBOM architecture](sbom-architecture) describes how these workflows are built, for developers working on ATR itself.
