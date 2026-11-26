# 3.1. Project configuration

**Up**: `3.` [Projects](projects)

**Prev**: `3.` [Projects](projects)

**Next**: `4.` [Release manager setup](release-manager-setup)

**Sections**:

* [Overview](#overview)
* [Metadata](#metadata)
* [Security](#security)
* [Version scheme](#version-scheme)
* [Compose](#compose)
* [Vote](#vote)
* [Finish](#finish)
* [Third-party distribution](#third-party-distribution)
* [Settings for one cycle](#settings-for-one-cycle)
* [Configuring a project with .asf.yaml](#configuring-a-project-with-asfyaml)

## Overview

Each project in ATR has a project page, which you can reach from the "About this project" link on
your [home page](/). The page has three tabs, and the settings on them are described in the sections
below:

* **Releases** lists the project's releases, grouped by cycle. It is not a settings tab.
* **Project** has the settings that describe the project as a whole: Metadata, Security, and Version
  scheme.
* **Release settings** has the settings a release uses as it is composed, voted on and finished:
  Compose, Vote, Finish, and Third-party distribution. A project with more than one cycle can give a
  cycle its own Compose, Vote and Finish settings, as described under
  [Settings for one cycle](#settings-for-one-cycle).

Who can change a setting depends on where it is:

* Members of the project's committee, and its release managers, can edit everything under Project
  and Release settings.
* Only members of the committee can change a project's categories and programming languages, or use
  **Export .asf.yaml**.
* Everybody else sees the same settings, but cannot change them.

Version scheme and the Release settings tab are shown only for an active project, and a retired
project cannot be edited at all.

You can also manage most of these settings from your repository, as described under
[Configuring a project with .asf.yaml](#configuring-a-project-with-asfyaml).

## Metadata

The Metadata tab describes the project to its users, and ATR uses several of its values in vote and
announcement emails.

* **Project name.** The display name, in title case. ATR adds the "Apache" prefix for you. Required.
* **Project description** and **Short description.** The description is required.
* **Homepage.** The project website. Required.
* **Download page.** The project's official download page. Required. Where the page differs by
  release, the URL can include `{{VERSION}}`, `{{MAJOR_VERSION}}` or `{{CYCLE}}`, and ATR fills them
  in for each release. An example is `https://example.apache.org/download-{{MAJOR_VERSION}}.html`.
  `{{CYCLE}}` can be used once the project has a cycle match.
* **Repositories.** The project's source repositories, one per line. At least one is required.
  A GitHub Actions workflow can upload to the project only from a repository on this list, when the
  PMC allows [uploads from GitHub Actions](github-actions-uploads). The tab shows which entries ATR
  recognises as GitHub repositories under `apache/`.
* **Lifecycle page.** The page describing the project's release support and lifecycle plans.
* **Bug database.** The project's issue tracker.
* **Mailing lists page.** The page on the project website that lists its mailing lists.
* **Relevant standards.** URLs of standards relevant to the project, one per line.

The tab also shows the project key and its committee, which cannot be changed here, and lets
committee members add and remove the project's **Categories** and **Programming languages**.

If a required value is missing when you start a release, ATR asks you to complete it first.

## Security

The Security tab records how security matters for the project are handled:

* **Security contact.** Where vulnerability reports for the project should be sent. This is either
  `security@apache.org` or your committee's own security list.
* **Threat model.** The URL of the project's published, human readable threat model.
* **Threat model source.** The URL of the threat model's plain text source document.

## Version scheme

The Version scheme settings, under Project, tell ATR how the project numbers its releases and how
those releases group into ***cycles***. A cycle is a line of releases that is maintained together,
such as all `2.x` releases. Projects that maintain several release lines at once have several active
cycles.

* **Version method.** One of:
  * *Simple*, where ATR does not interpret version numbers, and orders releases by their release
    date.
  * *Semver*, where versions follow semantic versioning and are ordered accordingly.
  * *Calver*, where versions are based on the calendar.
* **Version pattern.** An optional regular expression that every release version must match. Leave
  it empty to accept any version.
* **Cycle match.** For a semver project, a regular expression applied to the version, whose first
  capture group is the name of the cycle. For example, `(\d+)\..*` places version `2.0.1` in cycle
  `2`. Leave it empty to keep a single default cycle.
* **Cycle format.** For a calver project, a date format describing the shape of the version. It uses
  the tokens `YYYY` or `YY`, `MM` or `M`, `DD` or `D`, and `N` for a serial number, with literal
  separators between them. Wrap the part that names the cycle in parentheses, for example
  `(YY.MM).N`. Leave it empty to keep a single default cycle.
* **Branch template.** An optional naming hint for the source branch of each cycle. ATR does not
  currently enforce it.
* **Require version in filenames.** When enabled, every artifact filename must contain the release
  version, or the release is blocked. Case is ignored. For example, for
  version `1.2` the file `apache-example-1.2-src.tar.gz` passes while
  `apache-example-1.2.3-src.tar.gz` does not.

### Cycle dates

Each cycle has its own dates, which you set from the cycle's entry on the Releases tab:

* **End of development.** When active development on the cycle is planned to stop.
* **End of support.** When the cycle is planned to stop receiving support.
* **End of life.** When the cycle is planned to be retired entirely.
* **Long-term support.** Marks the cycle as a long-term support line.

ATR fills in the dates of the cycle's first release candidate, first release, and latest release
itself. These dates feed the project's [lifecycle events](archiving-and-lifecycle#lifecycle-events),
which are published in the [Release catalog](release-catalog#machine-readable-data).

## Compose

The Compose tab controls how ATR checks the files in a candidate:

* **Source artifact license checker.** Whether source artifacts are checked with the lightweight
  license checks, with Apache RAT, or with both. Binary artifacts always get the lightweight checks.
* **Lightweight source excludes** and **RAT source excludes.** Files to leave out of each license
  check.
* **RAT excludes URL.** The URL of a `.rat-excludes` file that your project already maintains, which
  ATR fetches afresh for each revision it checks.

[Project policy inputs](checks#project-policy-inputs) explains how the checks use these settings,
and [License checks](license-checks) gives the syntax for the exclusions.

## Vote

The Vote tab sets the defaults for the project's votes. The release manager can still adjust most of
them when starting a vote.

* **Default vote recipient**, with optional CC and BCC recipients. The mailing list that vote emails
  go to.
* **Streamlined voting.** How votes are cast and counted. The Vote tab says when streamlined voting
  is on, but it is set for all of a committee's projects at once, in the Streamlined voting section
  of the committee page:
  * *Off*. ATR sends the vote email, and votes are cast by replying to the thread. ATR tallies the
    replies when the vote is resolved.
  * *On*. ATR sends the vote email, but votes are cast on the vote page in ATR and recorded there,
    with a receipt for each vote sent to the thread. When the vote ends, ATR resolves it and
    publishes the release to SVN automatically if the vote passed and every vote is +1. If anyone
    votes 0 or -1, the release manager resolves the vote and publishes the release by hand.

  A PMC member turns streamlined voting on or off from the committee page, which starts a seven day
  approval vote by the PMC on CAP. ATR makes the change if that vote passes. The API reports
  streamlined voting as the `trusted` vote mode. It cannot be turned on for one project, so
  `.asf.yaml` does not accept `vote_mode: trusted`. Podlings cannot use streamlined voting.
* **Minimum voting period.** The shortest time a vote may run, between 72 and 168 hours.
* **Release checklist.** Markdown text telling voters how to test a release candidate.
* **Vote comment template.** Starting text for the comment that accompanies a vote, which each voter
  can edit.
* **Start vote subject** and **Start vote template.** The subject and body of the email that starts
  a vote.
* **Finish vote template.** The body of the email that reports the result of a vote.

Each email template also has a **template URL** field, as an alternative to composing the template
in ATR. If you set one, ATR fetches the template from that URL whenever it shows the email form, so
you can keep the text in your own repository, or reuse the same template for multiple projects
easily. Whoever is sending the email can still edit it first. The URL must be on an `apache.org`
host or `raw.githubusercontent.com`. If the fetch fails, ATR shows the default template with a
warning, so check it carefully before sending. Emails that ATR sends without anyone reviewing them,
such as an automatic vote resolution, use the default template in the same situation, and the API
refuses to send rather than fall back.

The templates and the checklist can include variables, written in the form `{{VERSION}}`, which ATR
replaces when it uses the text. Each template field on the tab lists the variables available to it.
`{{CYCLE}}` is the cycle that the version is in, such as `10.1`, and is empty for a project with a
single cycle.

## Finish

The Finish tab sets the defaults for publishing and announcing a release:

* **Default publish location.** Where each release is published beneath your committee's
  directory. It can use the tokens `{{PROJECT_KEY}}`, `{{VERSION}}`, and `{{MAJOR_VERSION}}`, so
  that `{{VERSION}}` publishes to `<committee>/<version>/` and `maven-{{MAJOR_VERSION}}/{{VERSION}}`
  to `<committee>/maven-3/<version>/`. Left empty, a committee's top level project publishes
  directly into `<committee>/`, and any other project into `<committee>/<project>-<version>/`.
  Files cannot be moved once ATR has published them, so set this to the layout you already use
  before your first release. See
  [Choosing the published layout](promoting-to-release#choosing-the-published-layout).
* **Allow auto-archive.** Lets a new release archive the previous release in the same cycle when it
  is announced. A project has a single cycle unless it has a **Cycle match**, or a **Cycle format**
  with a part in parentheses, under [Version scheme](#version-scheme). If you support more than one line
  at a time, set up cycles before enabling this, or a release in one line might archive a release in
  another. See
  [Archiving the prior release automatically](archiving-and-lifecycle#archiving-the-prior-release-automatically).
* **Announce release subject** and **Announce release template.** The subject and body of the
  announcement email, which can use variables in the same way as the vote templates.
* **Default announce recipient**, with optional CC and BCC recipients. The mailing list that
  announcements go to.

## Third-party distribution

The Third-party distribution tab lists the packages your project publishes on other platforms for
each release, such as Maven Central, PyPI, or Docker Hub. When a release's vote passes, ATR looks
for each of them on its platform, and the release cannot be announced until each one has been found
or ignored. See [the finish phase](finish-phase) for the release's distribution list.

To add packages, choose the platform and fill in:

* **Owner or namespace.** The groupId for Maven Central, the scope for a scoped npm package, the
  namespace on Docker Hub, or the repository on Artifact Hub. The field is hidden for a platform
  without one.
* **Packages.** The package names, one per line.
* **Version.** Leave this empty when the package has the same version as the release. Otherwise
  write the package's version using the tokens `{{PROJECT_KEY}}`, `{{VERSION}}`, and
  `{{MAJOR_VERSION}}`, so that `{{VERSION}}-jdk17` expects `10.1.62-jdk17` for release `10.1.62`.
  A Docker Hub version must include `{{VERSION}}`, as a tag shared between releases, such as
  `latest`, can be moved to another image.

Packages are listed by [Package URL](https://github.com/package-url/purl-spec) where the platform
has one. If the latest release with distributions found on a platform has any that are not in the
list, ATR offers to add them. It leaves out a package whose version differs from the release's.

Changing the list affects releases whose votes pass afterwards. A release that is already in the
finish phase keeps the list it started with.

## Settings for one cycle

A project that maintains several release lines can need different settings for each, such as a vote
email that says what a line supports. When the project has a **Cycle match**, or a **Cycle format**
with a part in parentheses, the Release settings tab has a **Per-cycle settings** link. It opens an
**Applies to** list, which is already open for a project with settings for a cycle:

* **Default settings** shows the settings that every cycle uses.
* Choosing a cycle shows the Compose, Vote and Finish settings as releases in that cycle use them.
  A setting with a value for the cycle is marked, such as **Set for 10.1**.

To give a cycle its own value for a setting, choose the cycle, change the setting, and save. ATR
keeps only the settings that differ from the defaults, so a later change to a default still reaches
every cycle without its own value for it. Saving a setting with the same value as the default goes
back to the default, and so does **Clear** beside the badge. Third-party distribution applies to
every cycle.

The list offers each cycle that a release is in. Versions that the pattern does not place have no
cycle, and use the default settings. To give a cycle settings before its first release, enter the
cycle under **Add settings for cycle**. ATR checks that the project's version scheme allows a cycle
of that text, and opens the cycle if it already has releases or settings.

A cycle's settings belong to the text of the cycle, such as `10.1`, and not to the pattern. If you
change the pattern so that no release is in a cycle of that text, the settings stay and apply to
nothing. ATR warns you when you save the version scheme, and marks the cycle on the Releases tab and
in the **Applies to** list. From there you can move the settings to another cycle or remove them.

Settings for one cycle can also be set in `.asf.yaml`, as described under
[Configuring a project with .asf.yaml](#configuring-a-project-with-asfyaml).

## Configuring a project with .asf.yaml

You can manage project metadata in ATR or in the `project` block of your repository's `.asf.yaml`
file. Committee members can use **Export .asf.yaml** on the project page to get the current
configuration as a starting point. Allowing [uploads from GitHub Actions](github-actions-uploads)
is **not** part of the file; a PMC member does that on the committee page. Nor are a project's
[GitHub OIDC restrictions](github-actions-uploads#restricting-which-runs-can-upload), which a PMC
member sets under Advanced settings on the project page. The `repositories` list is part of the
file, and it decides which repositories' workflows can upload, so a full synchronization that leaves
it out stops those uploads.

For example, the Maven Filtering project has the following metadata:

```yaml
project:
  metadata:
    key: maven-filtering
    committee: maven
    name: Apache Maven Filtering
```

`key` is the project identifier used in ATR URLs and API requests. `committee` identifies the
committee responsible for the project. `name` is the project's full display name and must start with
`Apache` and a space. The web form adds this prefix automatically, but `.asf.yaml` synchronization
does not.

A name must be supplied when creating a project. When updating an existing project, omitting the
name preserves its current value. If you use `doap:` to supply metadata, the name comes from the
DOAP project's `<name>` element and must include the prefix there. A `name` supplied alongside
`doap:` is ignored.

Synchronization is enabled by default and runs from the repository's default branch. When enabled,
the next push imports the supplied values, overwriting manual changes to those fields in ATR.
Correct the name in `.asf.yaml` or its linked DOAP file so that subsequent imports preserve the
correction.

The packages on the [Third-party distribution](#third-party-distribution) tab go in a `distributions`
list, with the packages that share a platform, namespace and version under one entry:

```yaml
project:
  distributions:
    - platform: maven
      owner_namespace: org.apache.example
      packages:
        - example-core
        - example-api
    - platform: docker_hub
      owner_namespace: apache
      packages:
        - example
      version: "{{VERSION}}-jdk17"
```

The platforms are `maven`, `pypi`, `npm`, `npm_scoped`, `docker_hub` and `artifact_hub`. Leave
`owner_namespace` out for `pypi` and `npm`, and `version` out when the package has the release's
version. A list in the file replaces the whole list in ATR.

[Settings for one cycle](#settings-for-one-cycle) go in a `cycles` block, under the cycle they are
for. A cycle takes the same keys as `policy`, apart from those that apply to every cycle:

```yaml
project:
  cycles:
    "10.1":
      min_hours: 120
      start_vote_template: |
        The proposed {{PROJECT_NAME}} {{VERSION}} release is ready for a vote.
    "9.0":
      vote_recipients:
        to: dev@example.apache.org
```

A `cycles` block is always complete, whichever sync mode the file uses. A cycle it leaves out loses
its settings, and a key left out of a cycle uses the project's value. A cycle has to be one that a
release is in, or one that the version scheme allows. Without a `cycles` block, the settings that
cycles have in ATR are left as they are, unless the sync mode is `full`, which clears them.

A cycle can have a blank value where the project has one. Give a list as `[]`, recipients as `{}`,
and text as `""`:

```yaml
project:
  cycles:
    "10.1":
      source_excludes_rat: []
      vote_recipients: {}
      start_vote_template: ""
```

By default, synchronization only updates the fields present in the file. A field you remove from
`.asf.yaml` keeps its value in ATR, including one set earlier by hand. To make the file the whole
configuration, set `atr_sync_mode: full` under `features`:

```yaml
project:
  features:
    atr_sync: true
    atr_sync_mode: full
```

With `full`, every field that `.asf.yaml` can set but the file leaves out is reset to the ATR
default on each push. In either mode, giving a list as empty, such as `repositories: []`, clears
it. The project name is never reset. The default mode is `merge`. A project created by a full push
starts from the defaults too, rather than from a parent project's settings, since anything the file
leaves out is a default. The export page can produce the file in either mode.

While the most recent update to a project came from `.asf.yaml`, ATR shows a notice on the project
page to anyone who can edit it, and repeats it when they save a change there. The notice clears once
somebody edits the project in ATR, and returns with the next push.

See the
[asfyaml project metadata reference](https://github.com/apache/infrastructure-asfyaml#project) for
the complete configuration format, release policy settings, and synchronization options.
