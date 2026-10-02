# 5.2. Vote phase

**Up**: `5.` [Making releases](release-process-description)

**Prev**: `5.1.4.` [SBOM workflows](sbom-workflows)

**Next**: `5.2.1.` [Staging and voting](staging-and-voting)

**Pages**:

* `5.2.1.` [Staging and voting](staging-and-voting)

**Sections**:

* [Overview](#overview)
* [Responsibilities](#responsibilities)
* [Casting and resolving the vote](#casting-and-resolving-the-vote)

## Overview

The ***vote*** phase is where the PMC decides, as an act of the Foundation, whether the
***Release Candidate*** should become a release. Once the Release Manager is satisfied with the
composed candidate they start a vote, and the PMC votes to approve it or not. The artifacts being
voted on stay in ATR for the duration of the vote, so the vote email can link to them directly and
every voter reviews the same thing. A vote may be canceled and restarted if a problem comes to
light. Once a vote passes, the candidate is ready to publish in the [finish phase](finish-phase).

For a step by step walkthrough of this phase with screenshots, see the
[Vote section of the tutorial](/tutorial#vote). The tutorial follows a release by a top level
project, with the vote held on a mailing list. A podling release is voted on twice, as described in
[Podling releases](podling-releases#the-two-rounds-of-voting).

## Responsibilities

Both the Release Manager and the wider PMC have a part to play in this phase:

* The ***Release Manager*** starts the vote, links to the artifacts under vote, and, when the
  vote is over, tallies the result and resolves it.
* PMC members review the candidate and cast their votes on the vote thread. Whose votes are
  binding is a matter of [ASF policy](https://www.apache.org/legal/release-policy.html#release-approval).

## Casting and resolving the vote

ATR keeps the candidate and its artifacts staged for the whole of the vote, and tallies the votes
from the vote thread on the mailing list. The tally is there to help, but the Release Manager
must check it against the thread before relying on it. When the vote is complete the Release
Manager resolves it: a passing vote moves the candidate on to the finish phase, and a failed or
canceled vote returns it to compose so that it can be revised. When starting the vote, the Release
Manager can also ask ATR to publish the release automatically once the vote resolves
successfully, as described in [Promoting to release](promoting-to-release). See [Staging and voting](staging-and-voting) for how staging, vote emails, and
resolution work in ATR.
