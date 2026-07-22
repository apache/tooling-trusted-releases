# 3.18. Resource management

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.17.` [ASFQuart usage](asfquart-usage)

**Next**: (none)

**Sections**:

* [Introduction](#introduction)
* [Worker pool](#worker-pool)
* [Heavy operations](#heavy-operations)
* [Requests and responses](#requests-and-responses)
* [Limits](#limits)
* [Capacity](#capacity)

## Introduction

ATR answers web requests, which we want to be fast, and performs long jobs such as unpacking archives or scanning source trees for license headers. We don't want the long tasks to starve the resources of one another or of the fast web requests. This document describes how we try to prevent this from happening.

Any service can fail either by running out of resources (memory, disk, etc.), or by taking so long that the client gives up. Everything described on this page tries to prevent one of those two classes of failure. Some limits in ATR terminate the work directly, and some allow work to continue even though we stop waiting.

This page is intended to address requirement 15.1.3 of ASVS version 5.0.0, which asks to identify functionality that is time consuming or resource demanding and to say how we intend its availability to be defended.

The five main places where ATR consumes resources are: 1. the task system, 2. web requests, 3. streamed responses of large bodies, 4. the SSH server (including rsync), and 5. the server's own background jobs.

## Worker pool

The manager ([`manager`](/ref/atr/manager.py)) tracks four to eight workers, each of which works on 8 to 16 tasks before exiting. Workers claim the oldest known task. The queue is global: there is no user quota, fairness, etc. Workers request, but are not guaranteed, lifetime limits of 300 processor seconds and 3 GB of address space (which children inherit, with separate accounting). Note that these limits are per worker, not per task.

After 300s spent on a task, the manager fails it and SIGTERMs the worker. It does not confirm the effect, and does not send the SIGTERM to the worker's children. The manager drops the worker, replacing only when there are fewer than four, so four to eight is the tracked pool, not a cap on live processes: a worker that hasn't acted on its SIGTERM runs on beside the pool, may overwrite its failed verdict if it finishes, and may run another task twice via the untracked reset.

## Heavy operations

Extraction ([`archives`](/ref/atr/archives.py)) allows 2 GB of file content (total and per file), 100,000 regular files, 100 to 1 expansion per member, depth 32, no absolute paths or hard links, and confines symlinks to the root. Quarantine extracts `.tar.gz`, `.tgz`, `.zip`, `.tar.bz2`, `.tar.xz`, `.jar`, `.war`, `.apk`, `.nar`, and `.whl` archives (plain `.tar`, `.deb`, `.rpm`, etc. are excluded). Caching is per release and content hash, but SBOM generation extracts again and concurrent validations race. `EXTRACT_CHUNK_SIZE` is ignored.

RAT checks use capped JVM resources (64 MB heap, 32 MB metaspace), and are killed at 300s. Lightweight license checks allow 1 MB per license, and 4 KB per source file. SBOM tasks are divided into generation (extract, syft, 300s wait), quality score (sbomqs, 300s), tool score (untimed CycloneDX validator, 80 MB managed heap cap), OSV scan (1000 per batch at 60s, then a detail request per new vulnerability), augmentation (30s requests), and conversion (local). Augment and OSV rebuild whole revisions on change. Comparison checks use a shallow clone (360s wait, unstoppable in its thread, blocking worker exit), and untimed rsync. SVN import uses a 600s export wait. The whole SVN wrapper is untimed, publishing included, and key administration uses it, unsupervised, in the application process. GitHub dispatch polls up to 600 times at 30s, plus a two minute status task.

Signature and hash checks read whole artifacts; KEYS import parses keys, consults LDAP, may publish via SVN, and builds a revision; and metadata refresh scans every account and project. Other tasks with caps include mail (a 30s SMTP client timeout, not a task-level bound), distribution status (20 per run, every two minutes), and the CAP poller (1m to 6h backoff).

## Requests and responses

Uploads (512 MB max, 3600s to receive on the two upload routes, 60s elsewhere) build a revision before answering unless newly detected archives require quarantine; then revision creation is deferred to a worker pending validation. They perform a hard link clone, validation, hash every file, and compare inodes. API uploads base64 decode entire payloads in memory first. Moves, deletions, hash generation, and SBOM draft actions also build revisions in requests. ZIP downloads list every file before streaming, and announcing probes publication URLs and moves the tree in the request. Tabulation fetches whole mail threads, does a fetch per message (100 at once), buffers and sorts before the 10,000 message limit, which bounds processing only, not fetching or memory. No supervisor watches a request, so rate limits are the only bound.

Other heavy work is queued and pages poll. Recording a distribution means checking an external registry, which may not list the package yet. When that happens, the standard API endpoint returns an error and gives up, while the workflow endpoint stores the distribution as pending, reports success to the caller, and leaves a scheduled task to retry the check later. Slow bodies get a logged 408. After a handler returns, transmission has 60s before the connection closes. Handlers are unbounded. ZIPs are deliberately unlimited (committers only) but share that window with file downloads.

The SSH server, in the application process, allows, per minute, 100 connections per address and ten authentications per user. Accepted commands run rsync as a child; only the rsync wait is capped at 90 minutes, after which that child is killed. Post-rsync processing is outside that timeout. The child's stderr is piped but not drained, and this may stall sessions. Uploads make rsync skip, not reject, files over 2,000,000,000 bytes. After a successful rsync transfer, uploads either build a revision in process or queue quarantine validation; post-rsync revision failures may still leave rsync's successful exit status.

## Limits

From [`config`](/ref/atr/config.py): `MAX_CONTENT_LENGTH` (512 MB) caps declared or buffered bodies (chunked multipart can evade it, hence a proxy cap too), `UPLOAD_BODY_TIMEOUT` (3600s), `MAX_EXTRACT_SIZE` (2 GB), `MAX_SESSION_AGE` (72 hours), and `ACCOUNT_CHECK_INTERVAL` (300s).

As enforced, the defaults (100 a minute, 1000 an hour) apply per endpoint, keyed by web session user or client address, in process memory, and reset by restarts. Announce and vote start get five an hour, vote cast 60, and key, token, and similar endpoints ten. API wide 500 an hour and website token route limits are declared but appear to be unenforced. Sessions last 72 hours.

Cached checks deduplicate on inputs hashes, and recurring tasks replace queued scheduled instances. Other caps include pagination at 1000 rows, offset at 1,000,000, file viewer at 512 KB, RAT reports at 100 per category, propagation probes at 24 artifacts, notifications at 1024 characters, ignore patterns at 128 characters without backtracking, and npm descriptors at 512 KB.

## Capacity

Disk grows everywhere, for example through release trees, the extraction cache (freed with its release), attestable records, database, logs, audit records, SVN working copy, quarantine and staging areas, and temporary space. There is no disk gauge, and no storage quota. Hard links keep extra revisions nearly free. SQLite runs WAL, writers locking up front, one at a time, with a five second busy timeout and roughly 64 MB of cache per connection across processes.

A first start backfill extracts uncached unfinished release archives, blocking startup, failures only warned, and without retries. Maintenance schedules immediately, others at six to eight minutes (metadata daily, workflow and distribution every two minutes). Only maintenance schedules its successor first, so other failures break the succession until restart.
