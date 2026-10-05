# 3.1. Project configuration

**Up**: `3.` [Projects](projects)

**Prev**: `3.` [Projects](projects)

**Next**: `4.` [Release managers](release-manager-setup)

**Sections**:

* [Overview](#overview)
* [Metadata](#metadata)
* [Security](#security)
* [Lifecycle](#lifecycle)
* [Trusted Publishing](#trusted-publishing)
* [Compose](#compose)
* [Vote](#vote)
* [Finish](#finish)
* [Configuring a project with .asf.yaml](#configuring-a-project-with-asfyaml)

## Overview

Each project in ATR has a project page, which you can reach from the "About this project" link on your [home page](/). The page is divided into tabs, and the settings on each tab are described in the sections below. The first tab, Releases, lists the project's releases and is not a settings tab, though it is where you set [cycle dates](#cycle-dates).

Who can change a setting depends on the tab:

* Members of the project's committee, and its release managers, can edit the Metadata and Security tabs, and the release policy tabs: Lifecycle, Trusted Publishing, Compose, Vote, and Finish.
* Only members of the committee can change a project's categories and programming languages, or use **Export .asf.yaml**.
* Everybody else sees the same settings, but cannot change them.

The release policy tabs are shown only for an active project, and a retired project cannot be edited at all.

You can also manage most of these settings from your repository, as described under [Configuring a project with .asf.yaml](#configuring-a-project-with-asfyaml).

## Metadata

The Metadata tab describes the project to its users, and ATR uses several of its values in vote and announcement emails.

* **Project name.** The display name, in title case. ATR adds the "Apache" prefix for you. Required.
* **Project description** and **Short description.** The description is required.
* **Homepage.** The project website. Required.
* **Download page.** The project's official download page. Required.
* **Repositories.** The project's source repositories, one per line. At least one is required.
* **Lifecycle page.** The page describing the project's release support and lifecycle plans.
* **Bug database.** The project's issue tracker.
* **Mailing lists page.** The page on the project website that lists its mailing lists.
* **Standards.** Any standards that the project implements, one per line.

The tab also shows the project key and its committee, which cannot be changed here, and lets committee members add and remove the project's **Categories** and **Programming languages**.

If a required value is missing when you start a release, ATR asks you to complete it first.

## Security

The Security tab records how security matters for the project are handled:

* **Security contact.** Where vulnerability reports for the project should be sent. This is either `security@apache.org` or your committee's own security list.
* **Threat model.** The URL of the project's published, human readable threat model.
* **Threat model source.** The URL of the threat model's plain text source document.

## Lifecycle

The Lifecycle tab sets the project's ***version scheme***, which tells ATR how the project numbers its releases and how those releases group into ***cycles***. A cycle is a line of releases that is maintained together, such as all `2.x` releases. Projects that maintain several release lines at once have several active cycles.

* **Version method.** One of:
  * *Simple*, where ATR does not interpret version numbers, and orders releases by their release date.
  * *Semver*, where versions follow semantic versioning and are ordered accordingly.
  * *Calver*, where versions are based on the calendar.
* **Version pattern.** An optional regular expression that every release version must match. Leave it empty to accept any version.
* **Cycle match.** For a semver project, a regular expression applied to the version, whose first capture group is the name of the cycle. For example, `(\d+)\..*` places version `2.0.1` in cycle `2`. Leave it empty to keep a single default cycle.
* **Cycle format.** For a calver project, a date format describing the shape of the version. It uses the tokens `YYYY` or `YY`, `MM` or `M`, `DD` or `D`, and `N` for a serial number, with literal separators between them. Wrap the part that names the cycle in parentheses, for example `(YY.MM).N`. Leave it empty to keep a single default cycle.
* **Branch template.** An optional naming hint for the source branch of each cycle. ATR does not currently enforce it.

### Cycle dates

Each cycle has its own dates, which you set from the cycle's entry on the Releases tab:

* **End of development.** When active development on the cycle is planned to stop.
* **End of support.** When the cycle is planned to stop receiving support.
* **End of life.** When the cycle is planned to be retired entirely.
* **Long-term support.** Marks the cycle as a long-term support line.

ATR fills in the dates of the cycle's first release candidate, first release, and latest release itself. These dates feed the project's [lifecycle events](archiving-releases#lifecycle-events), which are published in the [Release catalog](release-catalog#machine-readable-data).

## Trusted Publishing

The Trusted Publishing tab names the GitHub repository, branch, and workflows that ATR should trust to act on the project's releases. See [Configuring repository and workflow paths](trusted-publishing#configuring-repository-and-workflow-paths).

## Compose

The Compose tab controls how ATR checks the files in a candidate:

* **Source artifact license checker.** Whether source artifacts are checked with the lightweight license checks, with Apache RAT, or with both. Binary artifacts always get the lightweight checks.
* **Lightweight source excludes** and **RAT source excludes.** Files to leave out of each license check.
* **RAT excludes URL.** The URL of a `.rat-excludes` file that your project already maintains, which ATR fetches afresh for each revision it checks.
* **Tagging spec.** Defines ***tags*** that apply to the files in a release, written in YAML as a tag name followed by a list of glob patterns for the files it covers. A tag names a distribution platform, and ATR will not let a release be announced until a distribution to each tagged platform has been recorded. For example, if you tag files as `mavencentral`, the release must be distributed to Maven Central before it can be announced. The platform tags are `artifacthub`, `dockerhub`, `mavencentral`, `npm`, and `pypi`.

[Project policy inputs](checks#project-policy-inputs) explains how the checks use these settings, and [License checks](license-checks) gives the syntax for the exclusions.

## Vote

The Vote tab sets the defaults for the project's votes. The release manager can still adjust most of them when starting a vote.

* **Default vote recipient**, with optional CC and BCC recipients. The mailing list that vote emails go to.
* **Vote mode.** How votes are cast and counted:
  * *Email*. ATR sends the vote email, and votes are cast by replying to the thread. ATR tallies the replies when the vote is resolved.
  * *Trusted*. ATR sends the vote email, but votes are cast on the vote page in ATR and recorded there, with a receipt for each vote sent to the thread. Only in this mode can the vote be resolved automatically when it ends.
  * *Manual*. The vote is held entirely outside ATR. You give ATR the URL of the vote thread and record the result yourself. This mode is not available to podlings.
* **Minimum voting period.** The shortest time a vote may run, between 72 and 168 hours.
* **Release checklist.** Markdown text telling voters how to test a release candidate.
* **Vote comment template.** Starting text for the comment that accompanies a vote, which each voter can edit.
* **Start vote subject** and **Start vote template.** The subject and body of the email that starts a vote.
* **Finish vote template.** The body of the email that reports the result of a vote.

Each email template also has a **template URL** field, as an alternative to composing the template in ATR. If you set one, ATR fetches the template from that URL whenever it shows the email form,
so you can keep the text in your own repository, or reuse the same template for multiple projects easily. Whoever is sending the email can still edit it first. The URL must be on an `apache.org` host or `raw.githubusercontent.com`.
If the fetch fails, ATR shows the default template with a warning, so check it carefully before sending. Emails that ATR sends without anyone reviewing them, such as an automatic vote resolution,
use the default template in the same situation, and the API refuses to send rather than fall back.

The templates and the checklist can include variables, written in the form `{{VERSION}}`, which ATR replaces when it uses the text. Each template field on the tab lists the variables available to it.

## Finish

The Finish tab sets the defaults for publishing and announcing a release:

* **Default download path suffix.** Where the release is published beneath your committee's directory. It can use the tokens `{{PROJECT_KEY}}`, `{{VERSION}}`, and `{{MAJOR_VERSION}}`. See [Publishing to ASF Distribution Area](promoting-to-release#publishing-to-asf-distribution-area) for the default layout.
* **Allow auto-archive.** Lets a new release archive the previous release in the same cycle when it is announced. See [Archiving the prior release automatically](archiving-releases#archiving-the-prior-release-automatically).
* **Announce release subject** and **Announce release template.** The subject and body of the announcement email, which can use variables in the same way as the vote templates.
* **Default announce recipient**, with optional CC and BCC recipients. The mailing list that announcements go to.

## Configuring a project with .asf.yaml

You can manage project metadata in ATR or in the `project` block of your repository's `.asf.yaml` file. Committee members can use **Export .asf.yaml** on the project page to get the current configuration as a starting point.

For example, the Maven Filtering project has the following metadata:

```yaml
project:
  metadata:
    key: maven-filtering
    committee: maven
    name: Apache Maven Filtering
```

`key` is the project identifier used in ATR URLs and API requests. `committee` identifies the committee responsible for the project. `name` is the project's full display name and must start with `Apache` and a space. The web form adds this prefix automatically, but `.asf.yaml` synchronization does not.

A name must be supplied when creating a project. When updating an existing project, omitting the name preserves its current value. If you use `doap:` to supply metadata, the name comes from the DOAP project's `<name>` element and must include the prefix there. A `name` supplied alongside `doap:` is ignored.

Synchronization is enabled by default and runs from the repository's default branch. When enabled, the next push imports the supplied values, overwriting manual changes to those fields in ATR. Correct the name in `.asf.yaml` or its linked DOAP file so that subsequent imports preserve the correction.

While the most recent update to a project came from `.asf.yaml`, ATR shows a notice on the project page to anyone who can edit it, and repeats it when they save a change there. The notice clears once somebody edits the project in ATR, and returns with the next push.

See the [asfyaml project metadata reference](https://github.com/apache/infrastructure-asfyaml#project) for the complete configuration format, release policy settings, and synchronization options.
