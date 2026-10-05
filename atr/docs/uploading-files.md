# 5.1.1. Uploading files

**Up**: `5.1.` [Compose phase](compose-phase)

**Prev**: `5.1.` [Compose phase](compose-phase)

**Next**: `5.1.2.` [Checks](checks)

**Sections**:

* [Overview](#overview)
* [Upload limits](#upload-limits)
* [Archive validation](#archive-validation)
* [Browser](#browser)
* [rsync](#rsync)
* [SVN import](#svn-import)
* [Command line client](#command-line-client)
* [Maven plugin](#maven-plugin)
* [GitHub Actions](#github-actions)
* [Revisions](#revisions)

## Overview

Once you have started a release, you add files to it during the [compose phase](compose-phase). ATR
offers several ways to do so, and you can mix them within one release. Each accepted upload creates
a new [revision](#revisions), and ATR runs its [checks](checks) against it. Archives may need
[validation](#archive-validation) before the new revision is created.

Every artifact needs a detached signature and a checksum beside it, as described in
[Signing artifacts](signing-artifacts). Upload those alongside the artifact itself.

You can also include documentation and SBOMs. Uploaded files must not be symbolic links. For
selected formats, ATR also rejects empty files or contents that do not match the file extension.

The upload page for a release, reached with the "Upload files" button on its compose page, covers
the browser, SVN, rsync, and GitHub workflow routes, and shows the exact commands and paths for that
release.

## Upload limits

| Upload method | Default limit |
| --- | --- |
| Browser and HTTP API | 512 MiB per request, including form fields and encoding |
| rsync | Files over 2 GB are skipped |
| SVN import | No fixed size limit on the imported files |

If a browser upload is too large, send fewer files at a time or use rsync within its file size
limit.

## Archive validation

ATR unpacks new archives in supported formats, including `.tar.gz`, `.tgz` and `.zip`, before
accepting them. Each archive can contain up to 2 GiB of unpacked files by default. See the
[developer guide](file-handling#archive-quarantine) for the full format list and extraction limits.

During validation, the upload is not yet part of a revision. The previous revision remains
available. You cannot start a vote until validation finishes. The compose page refreshes
automatically when validation completes.

If archive validation fails, ATR discards the whole upload. Read the error on the compose page, fix
the problem, then upload all the files again, including signatures and checksums. **Dismiss** only
hides the failure notice.

Once the upload is accepted, ATR runs the [release checks](checks). The files remain downloadable
even if these checks report problems. Fix blockers before starting a vote. ATR does not scan uploads
for malware.

## Browser

Use the "File upload" form on the upload page to select one or more files. If your project has a
GitHub source repository, you must also give the full 40-character hash of the source commit that
the files were built from.

This is the route followed in the [Compose section of the tutorial](/tutorial#compose).

## rsync

rsync suits large uploads, whole directory trees, and scripts. It authenticates with an SSH key, so
first add your SSH public key to ATR on the [keys page](/keys). This is separate from your OpenPGP
signing key, as explained under [SSH keys](release-manager-setup#ssh-keys).

The upload page shows the command for your release, which has this form:

```shell
rsync -av -e 'ssh -p 2222' ${YOUR_FILES}/ <asf-uid>@<atr-host>:/<project-key>/<version>/
```

Note that:

* ATR's SSH server listens on port 2222.
* The destination must be the release directory itself, `/<project-key>/<version>/`. To place files
  in a subdirectory, create that subdirectory in the directory you upload from.
* The options must be `-av`. ATR rejects other combinations.
* Each upload starts from the files already in the candidate, so files that you do not send are
  kept. Add `--delete` to remove files from the candidate that are absent from your directory.

If rsync reports an error from ATR, please
[open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=BLANK_ISSUE).

## SVN import

If your files are already in your committee's `dist/dev` area, ATR can import them. Use the "SVN
upload" form on the upload page, and give:

* the SVN path, relative to `https://dist.apache.org/repos/dist/dev/<committee>/`, or to
  `https://dist.apache.org/repos/dist/dev/incubator/<committee>/` for a podling,
* the SVN revision to import, which defaults to `HEAD`,
* optionally, a subdirectory of the candidate to place the files in.

The import runs in the background, so the files appear in the candidate a short while after you
submit the form. We expect most release managers to use one of the other routes, for the reasons
given in [The earlier dist/dev workflow](staging-and-voting#the-earlier-distdev-workflow).

## Command line client

The `atr` command line client, the
[Trusted Releases Client](https://github.com/apache/tooling-releases-client), can take a release
from start to announcement, including starting the release and uploading files to it. It
authenticates with a [personal access token](release-manager-setup#personal-access-tokens), and it
can also drive rsync for you once you have registered an [SSH key](release-manager-setup#ssh-keys).

The client's own
[release process guide](https://github.com/apache/tooling-releases-client/blob/main/RELEASE-PROCESS.md)
covers installation and follows a release from start to finish, and its
[command reference](https://github.com/apache/tooling-releases-client/blob/main/COMMANDS.md) lists
every command. The client and the API that it uses are not yet stable, so take care when relying on
either in unattended scripts.

## Maven plugin

Projects that release with Maven can upload with the
[ATR Maven Plugin](https://github.com/apache/tooling-atr-maven-plugin). It also authenticates with a
[personal access token](release-manager-setup#personal-access-tokens), which you configure as a
server in your Maven `settings.xml`.

You must start the release in ATR before the plugin can upload to it. The plugin can then upload
files as part of your release build, for example during `mvn release:perform`. The
[plugin documentation](https://apache.github.io/tooling-atr-maven-plugin/) shows how to configure
your credentials and a release profile, and how to place files in subdirectories.

## GitHub Actions

Projects that are approved for reproducible builds can upload from a GitHub Actions workflow, with
no long-lived credential stored in the repository. The upload page shows an example workflow for
your release, and [Trusted Publishing](trusted-publishing) explains the approval and setup.

## Revisions

Every change that ATR accepts during the compose phase, whether an upload, an import, or a deletion,
creates a new [***Revision***](projects#revision) rather than altering the existing one. Revisions
are numbered in sequence, starting from `00001`, and ATR runs its checks against each revision.

Use the "Revisions" button on the compose page to see the history of a release. For each revision it
shows:

* who created it, and when,
* the files added, removed, and modified since the previous revision,
* a link to the revision's file manifest.

You can also do two things from that page:

* **Return to an earlier revision.** "Create a new revision from this one" copies an earlier
  revision to a new latest revision. Nothing is lost, because the revisions in between remain in the
  history. This is available only during the compose phase.
* **Tag a revision.** You can give a revision a tag as a memorable name for it. A tag is immutable:
  once set, it cannot be changed or removed.

A vote is held on one specific revision, so the files under vote cannot change while the vote is
open. If a vote fails or is canceled, the candidate returns to the compose phase, where you can
create further revisions.
