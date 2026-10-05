# 5. Making releases

**Up**: [Documentation](.)

**Prev**: `4.2.` [Trusted Publishing](trusted-publishing)

**Next**: `5.1.` [Compose phase](compose-phase)

**Pages**:

* `5.1.` [Compose phase](compose-phase)
* `5.2.` [Vote phase](vote-phase)
* `5.3.` [Finish phase](finish-phase)
* `5.4.` [Archiving and lifecycle](archiving-and-lifecycle)
* `5.5.` [Podling releases](podling-releases)

**Sections**:

* [Releases](#releases)

## Releases

The ASF releases open source software under its
[release policy](https://www.apache.org/legal/release-policy.html). The ATR platform helps PMCs
release their project's artifacts through several stages:

1. ***Candidate***. Guiding a ***Release Candidate*** through the phases of ASF Governance to make a
   Release is the primary purpose of the ATR platform.
   * ***Compose***. ***Release Artifacts*** are assembled and checked for compliance, and the
     Release Manager iterates on them until the Candidate is ready. See
     [Compose phase](compose-phase).
   * ***Vote***. The PMC votes on the Candidate, which stays in ATR for the duration of the vote.
     See [Vote phase](vote-phase).
   * ***Finish***. The approved Release Artifacts are committed to `svn:dist:release`, and the
     release is announced once they have reached the download servers. See
     [Finish phase](finish-phase).
2. ***Released***. Published releases are active and cataloged, and available both via a standard
   catalog API and at the proper URLs on the CDN and download servers. Releases made through the
   legacy methods that bypass ATR are also cataloged.
3. ***Archived***. All cataloged releases may be archived using the ATR platform or by direct
   removal from `svn:dist:release`. See [Archiving and lifecycle](archiving-and-lifecycle).
