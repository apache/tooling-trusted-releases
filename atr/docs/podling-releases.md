# 5.5. Podling releases

**Up**: `5.` [Making releases](release-process-description)

**Prev**: `5.4.` [Archiving and lifecycle](archiving-releases)

**Next**: `6.` [Release catalog](release-catalog)

**Sections**:

* [Overview](#overview)
* [Composing a podling release](#composing-a-podling-release)
* [The two rounds of voting](#the-two-rounds-of-voting)
* [Finishing a podling release](#finishing-a-podling-release)

## Overview

A ***Podling*** is a project in the Apache Incubator, and its committee is a ***PPMC***. A
podling release goes through the same compose, vote, and finish phases as any other release, but
incubation policy adds some requirements. The main one is that a podling release must be approved
twice: first by the podling's own PPMC, and then by the Incubator PMC. ATR runs these as two
rounds of voting on the same candidate.

ATR knows whether a committee is a podling from the ASF's own records, so there is nothing for you
to configure. The differences described on this page apply automatically.

## Composing a podling release

Two of ATR's [checks](checks) apply extra rules to a podling, and both report a ***blocker*** if
the rule is not met, so the candidate cannot proceed to a vote until it is fixed:

* Every artifact file name must include the word `incubating`.
* Every source archive must have a `DISCLAIMER` or `DISCLAIMER-WIP` file in its root directory.

Expedited releases are not available to podlings.

## The two rounds of voting

You start a podling vote in the same way as any other, from the compose page. The differences
are these:

* **First round.** The vote is held by the PPMC, on the podling's own mailing list. It cannot be
  held on the podling's `private@` list. When you start the vote, you also choose the list that
  the second round will be sent to, which is `general@incubator.apache.org`.
* **Moving to the second round.** When you resolve the first round as passed, ATR does not move
  the release on to the finish phase. Instead it starts the second round straight away, sending a
  new vote email to the Incubator list. The second round is held on the same revision, with the
  same duration, using your project's vote email template.
* **Second round.** The vote is held by the Incubator PMC. When you resolve it as passed, the
  release moves on to the finish phase, and ATR sends the result to the second round thread and
  also, as a reply, to the first round thread.

If either round fails or is canceled, the candidate returns to the compose phase. A later vote on
the revised candidate starts again from the first round.

Whose vote counts differs between the rounds, and ATR labels votes to match:

| Round  | Held by           | Votes that count                                   | Other votes   |
|--------|-------------------|----------------------------------------------------|---------------|
| First  | The PPMC          | "Formal" votes, from members of the PPMC           | "Informal"    |
| Second | The Incubator PMC | "Binding" votes, from members of the Incubator PMC | "Non-binding" |

A PPMC member who is not also a member of the Incubator PMC therefore has a formal vote in the
first round, but a non-binding vote in the second.

Some options on the vote form behave differently for a podling:

* If you ask ATR to publish the release automatically when the vote resolves, that choice is
  carried over to the second round, and publication happens once the second round passes.
* Automatic vote resolution, which is offered in the Trusted [vote mode](project-configuration#vote),
  is not available for the first round.
* A podling release cannot use the Manual vote mode, in which a vote is held outside ATR and its
  result recorded afterwards. It must go through the two rounds described here.

See [Vote phase](vote-phase) and [Staging and voting](staging-and-voting) for everything that is
common to all votes.

## Finishing a podling release

Podling releases are published under the Incubator's area of the distribution repository, rather
than at the top level:

* Published files go to `https://dist.apache.org/repos/dist/release/incubator/<committee>/`, and
  are served from `https://downloads.apache.org/incubator/<committee>/`.
* The committee's `KEYS` file lives in the same place, at
  `https://downloads.apache.org/incubator/<committee>/KEYS`.
* An SVN import during the compose phase reads from
  `https://dist.apache.org/repos/dist/dev/incubator/<committee>/`.

[Promoting to release](promoting-to-release) describes publication and announcement, which
otherwise work as they do for any other release.

In the [Release catalog](release-catalog), podlings are grouped together on a single Incubator
page, rather than each having a card on the front page.
