# 5.2. Vote phase

**Up**: `5.` [Making releases](making-releases)

**Prev**: `5.1.4.` [SBOM workflows](sbom-workflows)

**Next**: `5.2.1.` [Staging and voting](staging-and-voting)

**Pages**:

* `5.2.1.` [Staging and voting](staging-and-voting)
* `5.2.2.` [File manifest](file-manifest)

**Sections**:

* [Overview](#overview)
* [Responsibilities](#responsibilities)
* [Casting and resolving the vote](#casting-and-resolving-the-vote)

## Overview

The ***vote*** phase is where the PMC decides, as an act of the Foundation, whether the ***Release
Candidate*** should become a release. Once the Release Manager is satisfied with the composed
candidate they start a vote, and the PMC votes to approve it or not. The artifacts being voted on
stay in ATR for the duration of the vote, so the vote email can link to them directly and every
voter reviews the same thing. A vote may be canceled and restarted if a problem comes to light. Once
a vote passes, the candidate is ready to publish in the [finish phase](finish-phase).

For a step by step walkthrough of this phase with screenshots, see the
[Vote section of the tutorial](/tutorial#vote). The tutorial follows a release by a top level
project, with the vote held on a mailing list. A podling release is voted on twice, as described in
[Podling releases](podling-releases#the-two-rounds-of-voting).

## Responsibilities

Both the Release Manager and the wider PMC have a part to play in this phase:

* The ***Release Manager*** starts the vote, links to the artifacts under vote, and, when the vote
  is over, tallies the result and resolves it.
* PMC members and other ASF committers review the candidate and cast their votes, either directly on the vote page in ATR (which records the ballot and sends a receipt to the mailing list) or by replying to the vote thread on the mailing list (for non-expedited releases). Whose votes are binding is a matter of [ASF policy](https://www.apache.org/legal/release-policy.html#release-approval).

## Casting and resolving the vote

ATR keeps the candidate and its artifacts staged for the whole of the vote. Ballots cast on ATR are recorded immediately in the official tally. For non-expedited releases, replies sent directly to the mailing-list thread are parsed heuristically for context on the resolution page and remain uncounted until a PMC member (or Incubator PMC member in podling round 2) confirms them into the official tally; if the same voter both casts an ATR ballot and has a confirmed email reply, the newer vote wins.

When the vote is complete, a PMC member, designated release manager, or (for podling round 2) Incubator PMC member resolves it: a passing vote moves the candidate on to the finish phase (or starts podling round 2), and a failed or canceled vote returns it to compose so that it can be revised. When starting a non-expedited vote, the initiator can request an end-of-vote email reminder, and a committee member can ask ATR to publish the release to SVN automatically once the final approving vote is resolved as passed, as described in [Promoting to release](promoting-to-release). See [Staging and voting](staging-and-voting) for how staging, vote emails, and resolution work in ATR.

If a vote is already passing before its voting period ends, a binding voter can complete it early as passed by giving a reason, which ATR adds to the result email. Release votes should normally remain open for at least 72 hours, so do this only in [exceptional circumstances](https://www.apache.org/legal/release-policy.html#expedited-releases).
