# 2.8. Promoting to release

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.7.` [Staging and voting](staging-and-voting)

**Next**: `3.1.` [Running the server](running-the-server)

**Sections**:

* [Overview](#overview)
* [Publishing to SVN](#publishing-to-svn)
* [Announcing](#announcing)
* [Removing superseded releases](#removing-superseded-releases)
* [The KEYS file](#the-keys-file)

## Overview

SVN is not an intrinsic part of the ATR release process until publication. The candidate is staged in ATR and voted on there, whatever method was used to upload the files, as described in [Staging and voting](staging-and-voting). When the vote passes, the release moves to the finish phase, in which ATR commits the approved artifacts to the canonical release area of the Apache distribution SVN repository:

* TLP: `https://dist.apache.org/repos/dist/release/<committee>/`
* Podling: `https://dist.apache.org/repos/dist/release/incubator/<committee>/`

Files committed there are served from `downloads.apache.org` and the download CDN, and are picked up by `archive.apache.org` automatically. No manual SVN step is needed to publish a release.

**Podling note**: for a podling, prefix the committee path with `incubator/` in every path below, including the `downloads.apache.org` URL.

## Publishing to SVN

Publication happens in the finish phase, once the vote has resolved successfully. There are two ways to trigger it:

* Automatically, by selecting "Automatically publish to SVN when this vote resolves" when starting the vote. This option is offered when a committee member starts a non-expedited vote in email or Trusted Vote mode.
* Manually, by pressing the publish button on the finish page for the release.

The finish page shows the destination, and the resulting SVN revision and URL once publication completes.

By default the files are placed in a per release subdirectory, `release/<committee>/<project>-<version>/`, except for a committee's top level project, whose files go directly into `release/<committee>/`. Projects can configure this layout with the download path suffix in their release policy, using the `{{PROJECT_KEY}}`, `{{VERSION}}`, and `{{MAJOR_VERSION}}` tokens, and the release manager can adjust the suffix when publishing manually.

## Announcing

A release cannot be announced until it has been published to SVN. When you announce, ATR verifies that the published artifacts are reachable on the download servers, and asks you to try again later if they have not finished propagating. Once verified, ATR sends the announcement email and adds the release to the release catalog.

You can also verify the publication yourself:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/
```

The files should soon become visible at `https://downloads.apache.org/<committee>/...`.

## Removing superseded releases

Apache distribution policy is that `/dist/release/` should hold only the current release of each line, with everything older moved to `archive.apache.org`. Releases committed to `/dist/release/` are picked up by the archive automatically, so you do not have to copy them there yourself. You do, however, have to delete the superseded ones. What to delete depends on the layout your project publishes to, described above. With the default layout for a project that is not the committee's top level project, a superseded release is a single subdirectory:

```shell
svn rm -m "Remove superseded <committee> <previous-version>" \
  https://dist.apache.org/repos/dist/release/<committee>/<project>-<previous-version>
```

A top level project's files sit directly in the committee directory, so list each superseded file in the `svn rm` command instead.

Do this once you have verified that the new release is reachable. Archiving a release in ATR records the archival in the release catalog, but does not currently remove the files from `/dist/release/`, so this step remains manual.

## The KEYS file

Signing releases is done with individual keys, as described in [Signing artifacts](signing-artifacts). Separately, each committee publishes a single `KEYS` file listing the public keys that its release managers sign with. The file lives at `https://dist.apache.org/repos/dist/release/<committee>/KEYS`, is managed independently of any individual release, and is what downstream users fetch to verify release signatures. When you upload your key to ATR and associate it with a committee, it becomes part of the set that ATR holds for that committee's `KEYS` file.

Committee members choose how the file is kept in step with ATR, using the KEYS file management setting on the committee's page. There are three modes:

* **Automatically update the committee's KEYS file.** ATR owns the file. Whenever the committee's keys change in ATR, ATR regenerates the `KEYS` file and commits it to SVN. This is the simplest option if the committee manages its keys in ATR.
* **Automatically import changes to the KEYS file made in SVN.** SVN owns the file, and this is the default. ATR watches the committee's `KEYS` file in SVN and imports updates to it, but never writes back. In this mode the committee's keys are read-only in ATR, so uploads, associations, and deletions for the committee are refused; make the change in SVN instead.
* **Manually upload KEYS files in ATR.** ATR holds the keys, but commits to SVN only on an explicit request. The committee page offers two actions: import keys from an uploaded `KEYS` file, or regenerate the published file from the keys ATR already holds. Either publishes the result to SVN. Other key changes are not published until then, so the file in SVN can lag what ATR holds.

| | The keys are managed... | ATR commits the file to SVN... | ATR imports changes from SVN... |
| --- | --- | --- | --- |
| Automatically update | in ATR | on every key change | no |
| Automatically import (default) | in SVN | never | when the file is updated |
| Manually upload | in ATR | when you upload or regenerate | no |

Changing the mode does not, of itself, delete any keys from ATR. Switching to the import mode starts an import of the current file in SVN, however, which can remove keys from the committee that the file does not contain. A key that has signed artifacts catalogued by ATR is never removed this way: it stays associated with the committee, marked on the committee page as missing from SVN, until it reappears in the file or the situation is resolved by hand. Deleting the `KEYS` file in SVN does not remove any keys either. Switching to either of the other two modes does not publish anything; the file in SVN changes only on the triggers described above.

Whichever mode is chosen, the public keys of any keypairs used to sign a release must be present in the committee's `KEYS` file before you publish signed artifacts.
