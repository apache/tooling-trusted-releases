# 5.6. Making a release with the client

**Up**: `5.` [Making releases](making-releases)

**Prev**: `5.5.` [Podling releases](podling-releases)

**Next**: `5.7.` [Troubleshooting](troubleshooting)

**Sections**:

* [From your terminal](#from-your-terminal)
* [Set up the client](#set-up-the-client)
* [Compose the candidate](#compose-the-candidate)
* [Review the checks](#review-the-checks)
* [Start and resolve the vote](#start-and-resolve-the-vote)
* [Finish the release](#finish-the-release)
* [Further information](#further-information)

## From your terminal

The `atr` command line client lets release managers upload files, review checks, start votes, and announce releases.

This walkthrough follows Apache Example 1.0.0, the main project of the Example committee. It assumes that you are a PMC member and that your project votes by email. Complete [Release manager setup](release-manager-setup) first, including linking your public signing key to your committee. [Podling releases](podling-releases) explains the extra voting round for incubating projects.

Replace `alice`, `example`, `1.0.0`, the file names, and the mailing lists below with your own.

## Set up the client

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, install the client and check that it runs:

```shell
uv tool install "apache-trusted-releases @ git+https://github.com/apache/tooling-releases-client"
atr --version
```

The client connects to `releases.apache.org` by default. Create a personal access token, or PAT, on the [Tokens page](/tokens). Copy it straight away, because ATR shows it only once. Then give the client your ASF username and the token:

```shell
atr set asf.uid alice
atr set tokens.pat
atr jwt refresh
```

The second command prompts for the token without showing it, which keeps it out of your shell history. The last command checks your details with ATR and prints a temporary access token, which the client renews by itself from then on. A PAT lasts 180 days. When ATR rejects yours, create a new one and run the last two commands again.

## Compose the candidate

Start a draft:

```shell
atr release start example 1.0.0
```

Prepare the files you intend to release, including a source archive. Each artifact needs a detached OpenPGP signature and a SHA256 or SHA512 checksum. For a single source archive, using your usual signing key:

```shell
gpg --armor --detach-sign apache-example-1.0.0.tar.gz
shasum -a 512 apache-example-1.0.0.tar.gz > apache-example-1.0.0.tar.gz.sha512
```

Upload the archive and its two companion files:

```shell
atr upload example 1.0.0 apache-example-1.0.0.tar.gz apache-example-1.0.0.tar.gz
atr upload example 1.0.0 apache-example-1.0.0.tar.gz.asc apache-example-1.0.0.tar.gz.asc
atr upload example 1.0.0 apache-example-1.0.0.tar.gz.sha512 apache-example-1.0.0.tar.gz.sha512
```

The last two arguments are the destination path in ATR, then the local file to read. Here they have the same name. Each upload creates a new revision, which is a snapshot of the candidate.

If your project has a GitHub source repository, ATR compares the source archive against it and needs the commit the archive was built from. The client has no option for this yet, so record it with the API's `release/commit` call, giving `project`, `version`, and the full 40-character `commit`. The client's generic `atr api post` command can make the call. See [Source commit](uploading-files#source-commit) for the other ways to give it.

Archive uploads pause while ATR validates them. If an upload times out, run `atr list example 1.0.0` and look at the release's compose page on the website before trying again, because validation may still be running. If validation failed, the compose page says why. Fix the archive and upload it again.

For files over the usual 512 MiB upload limit, or a whole directory of release files, use rsync instead. You need local `rsync` and `ssh` commands and an SSH key registered with ATR:

```shell
atr ssh add "$(cat ~/.ssh/id_ed25519.pub)"
atr rsync example 1.0.0 ./candidate/
```

This uploads the contents of `candidate` into the draft. Keep that directory limited to the files you intend to release.

## Review the checks

Wait for the checks, then read the summary and confirm which files arrived:

```shell
atr check wait example 1.0.0 --timeout 300
atr check status example 1.0.0 --verbose
atr list example 1.0.0
```

"Checks completed" means only that the checks have stopped running, not that they passed. If the wait times out, run it again. Then list the blockers and concerns, including findings inside archives:

```shell
atr check blockers example 1.0.0 --members
atr check concerns example 1.0.0 --members
```

Fix any blockers locally, make fresh signatures and checksums for the changed files, upload them, then wait and review again. Read each concern too, because you must acknowledge any that remain when you start the vote. If a check reports an exception, inspect the affected file and ask the Tooling team for help. For a very large candidate, the client may warn that it shows only some results. Read the rest on the compose page. [Checks](checks) explains what the findings mean.

## Start and resolve the vote

`--auto-publish` asks ATR to publish the files when you resolve the vote as passed. Only PMC members can use it. With the client, ATR currently publishes to the top of your committee's distribution area, even if your project has configured a different path. If you need a project or version directory, or are a designated release manager, omit this option and publish from the release's finish page on the website. The client has no separate publish command.

If concerns remain, add `--concerns-noted` with their group keys from `atr check concerns`, separated by commas without spaces. For example, `--concerns-noted atr.tasks.checks.rat.check` acknowledges the Apache RAT group. Acknowledge only groups you have reviewed. If you miss one, ATR names it when it refuses to start the vote.

Start the vote:

```shell
atr vote start example 1.0.0 -m dev@example.apache.org --auto-publish
```

This uses the latest revision if you created it. If another account created it, ATR asks you to give its number. Run `atr release info example 1.0.0` to find `latest_revision_number`.

To choose a revision explicitly, put its number after the version, for example `atr vote start example 1.0.0 00004 -m dev@example.apache.org --auto-publish`. ATR refuses that request if a newer revision has appeared. Once the vote starts, the revision stays fixed so every voter reviews the same files. ATR also refuses while checks are still running.

ATR takes the subject, body, and voting duration from your project's settings, and sends the email only to lists that your project allows. To write your own body, add `--body-file vote.txt`. ATR sends that text exactly as written, without filling in template variables. Check that the vote email reaches the list before you ask people to vote.

If your project uses Trusted Voting, the same command starts a Trusted Vote and voters cast ballots in ATR. Start the vote on the website if you want automatic resolution, which the client cannot request yet.

Once the voting period has ended, compare ATR's tally with the mailing list thread:

```shell
atr vote tabulate example 1.0.0
```

When the result is clear, record it:

```shell
atr vote resolve example 1.0.0 passed
```

ATR replies to the vote thread, including the tally when available, and moves the release to the finish phase. If the vote failed, use `failed`. The release returns to compose, where you can fix the files and start a fresh vote. To cancel a vote early, use the website.

## Finish the release

Automatic publication starts when you resolve the vote. The finish page shows its progress and the download URL. If publication fails, read the error there before publishing again from that page.

If your project ships packages to Maven Central, PyPI, or a similar platform, publish them there as usual. If your project requires recorded distributions, ATR will not announce the release until you record them. For a Maven Central package:

```shell
atr distribution record example 1.0.0 MAVEN org.apache.example example-core 1.0.0
atr distribution list example 1.0.0
```

This records a final distribution rather than a staged one. ATR checks that the package is available on the platform. [Recording distributions](release-manager-setup#recording-distributions) lists the other platform names.

When publication is complete, announce to a mailing list permitted for your project:

```shell
atr announce example 1.0.0 -m announce@apache.org
```

ATR fills the body from your project's announcement template unless you add `--body-file announce.txt`. To copy the announcement to other permitted lists, add `--cc` or `--bcc` once for each list. If the files have not reached the download servers yet, wait a few minutes and try again. ATR allows five attempts an hour. Once ATR accepts the announcement, it adds the release to the [release catalog](release-catalog) and queues the email for delivery. The released files can no longer change.

## Further information

The website and client work on the same releases, so you can switch between them whenever it helps. For example, the website offers an email preview and publication progress, while the client makes repeated uploads quick.

Use `atr --help` to explore, or add `--help` to a command such as `atr vote start`. `atr docs` prints the full command reference. The [client repository](https://github.com/apache/tooling-releases-client) has more examples. The client and API are still changing, so check commands before relying on them in unattended scripts.
