# 7.20. Resource management

**Up**: `7.` [Developer guide](developer-guide)

**Prev**: `7.19.` [ASF modules](asf-modules)

**Next**: `7.21.` [SBOM architecture](sbom-architecture)

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

The manager ([`manager`](/ref/atr/manager.py)) tracks four to eight workers, each of which works on 8 to 16 tasks before exiting. Workers claim the oldest known task. The queue is global: there is no user quota, fairness, etc. Workers arm a soft CPU limit of 300 processor seconds at the start of every task, so the budget is per task rather than per worker lifetime; children inherit the limit, each with its own fresh accounting. The limit is enforced by the kernel as an uncaught SIGXCPU, which no code may handle or ignore, because on macOS it is delivered only once and the hard limit is not enforced.

Each worker also runs a watchdog thread, polling every 500ms, which kills the worker's own process group when the resident memory of its process tree exceeds 3 GB. As a second layer, the manager samples each tracked worker's tree on its two second cycle and kills the group of any worker exceeding that limit by 20%. It records a task failure only when the task identified before measurement is still active when the verdict is written, and keeps tracking a worker that survives the kill to retry on later cycles.

After the per-type time limit, 300s by default, spent on a task, the manager fails it and then stops the worker in the background so that no other worker waits behind it. Stopping means SIGTERM to the worker's process group, then ten seconds, then SIGKILL to the group if any member other than a zombie is still alive, then polling the group for up to five seconds, and a logged error naming any member that outlives even that.

A worker being stopped stays tracked, and is skipped by later checks, until it is seen to exit; it does not count towards the minimum pool size, so its replacement is spawned immediately. A stop which fails, or which leaves the group alive, makes the worker eligible to be stopped again rather than leaving it skipped. Workers handle SIGTERM on their event loop by cancelling their tasks, force exiting after a 15s grace so that work wedged in a thread cannot hold the process open. Server shutdown stops all workers by the same route, concurrently.

The tracked pool of four to eight is not a cap on live processes, because workers orphaned by a server restart run on beside the pool. Every claim records the process number of the claiming worker and the creation time of that process, which together identify the claimant, because a process which took the number later must have been created later. Creation times are compared with a two second tolerance, because stepping the wall clock shifts the boot time from which the kernel derives them.

A claim is returned to the queue only when its claimant is provably dead, and if the dead claimant left survivors in its process group they are killed first, with the claim returned once a later pass finds the group empty. A live claimant within its time limit is left to finish, which is the restart case. A live claimant over its limit has its task failed and its process group killed, with SIGKILL because the manager holds no handle on it, and its creation time is checked once more immediately before the kill, because failing the task first can block for as long as the database busy timeout, within which the number could pass to an unrelated process.

A claim recorded before creation times were kept identifies its claimant by process number alone, which proves nothing, so it is returned to the queue as all untracked claims were before.

## Heavy operations

Archive extraction ([`archives`](/ref/atr/archives.py)) allows up to 2 GiB of extracted file content and 100,000 files per archive by default. See [File handling](file-handling) for the formats and remaining extraction limits. Extracted content is cached per release and content hash. Concurrent validations can extract the same archive, and SBOM generation extracts it again.

RAT checks use capped JVM resources (64 MB heap, 32 MB metaspace), and are killed at 300s. Lightweight license checks allow 1 MB per license, and 4 KB per source file. SBOM tasks are divided into generation (extract, syft, 300s wait), quality score (sbomqs, 300s), tool score (untimed CycloneDX validator), OSV scan (1000 per batch at 60s, then a detail request per new vulnerability), augmentation (30s requests), and conversion (local). Augment and OSV rebuild whole revisions on change. Comparison checks use a shallow clone (360s wait, unstoppable in its thread, delaying worker exit until the shutdown grace expires), and untimed rsync. SVN import uses a 600s export wait. The whole SVN wrapper is untimed, publishing included, and key administration uses it, unsupervised, in the application process. GitHub dispatch polls up to 600 times at 30s, plus a two minute status task.

Signature and hash checks read whole artifacts; KEYS import parses keys, consults LDAP, may publish via SVN, and builds a revision; and metadata refresh scans every account and project. Other tasks with caps include mail (a 30s SMTP client timeout, not a task-level bound), distribution status (20 per run, every two minutes), and the CAP poller (1m to 6h backoff).

## Requests and responses

HTTP uploads allow 512 MiB per request by default. ATR allows one hour to receive bodies on the browser and two API upload routes, and 60 seconds elsewhere. Uploads build a revision before answering unless archives need quarantine, which defers revision creation to a worker. Creating a revision involves cloning the previous files with hard links, validating the new tree, hashing files and comparing inodes. JSON API uploads decode the entire base64 payload in memory, and the raw store endpoint streams the body to a temporary file.

Moves, deletions, hash generation, and SBOM draft actions also build revisions during requests. ZIP downloads list every file before streaming. Announcing checks publication during the request and queues local file cleanup as a background task.

Vote tabulation fetches whole mail threads, fetching up to 100 messages at once, then buffers and sorts them. A 10,000 message limit applies after fetching. Request handlers do not have the CPU or memory limits applied to workers.

Other heavy work is queued and pages poll. Recording a distribution means checking an external registry, which may not list the package yet. When that happens, the standard API endpoint returns an error and gives up, while the workflow endpoint stores the distribution as pending, reports success to the caller, and leaves a scheduled task to retry the check later. Slow bodies get a logged 408. After a handler returns, transmission has 60s before the connection closes. Handlers are unbounded. ZIPs are deliberately unlimited (committers only) but share that window with file downloads.

The SSH server, in the application process, allows, per minute, 100 connections per address and ten authentications per user. Accepted commands run rsync as a child; only the rsync wait is capped at 90 minutes, after which that child is killed. Post-rsync processing is outside that timeout. The child's stderr is piped but not drained, and this may stall sessions. Uploads make rsync skip, not reject, files over 2,000,000,000 bytes. After a successful rsync transfer, uploads either build a revision in process or queue quarantine validation; post-rsync revision failures may still leave rsync's successful exit status.

## Limits

From [`config`](/ref/atr/config.py): `MAX_CONTENT_LENGTH` (512 MiB) caps declared or received bodies, `UPLOAD_BODY_TIMEOUT` (3600s), `MAX_EXTRACT_SIZE` (2 GiB), `MAX_SESSION_AGE` (72 hours), and `ACCOUNT_CHECK_INTERVAL` (300s). Request bodies over 1 MiB are written to the state temporary directory, and such writes are refused with a 503 if they would leave no more than 512 MiB of disk space plus the maximum body size free. Multipart part submissions and concurrent writers can temporarily push free space below that limit.

As enforced, the defaults (100 a minute, 1000 an hour) apply per endpoint, keyed by web session user or client address, in process memory, and reset by restarts. Announce and vote start get five an hour, vote cast 60, and key, token, and similar endpoints ten. API wide 500 an hour and website token route limits are declared but appear to be unenforced. Sessions last 72 hours.

Cached checks deduplicate on inputs hashes, and recurring tasks replace queued scheduled instances. Other caps include pagination at 1000 rows, offset at 1,000,000, file viewer at 512 KB, RAT reports at 100 per category, propagation probes at 50 artifacts, notifications at 1024 characters, ignore patterns at 128 characters without backtracking, and npm descriptors at 512 KB.

## Capacity

Disk grows everywhere, for example through release trees, the extraction cache (freed with its release), attestable records, database, logs, audit records, SVN working copy, quarantine and staging areas, and temporary space. There is no disk gauge, and no storage quota. Hard links keep extra revisions nearly free. SQLite runs WAL, writers locking up front, one at a time, with a five second busy timeout and roughly 64 MB of cache per connection across processes.

A first start backfill extracts uncached unfinished release archives, blocking startup, failures only warned, and without retries. Maintenance schedules immediately, others at six to eight minutes (metadata daily, workflow and distribution every two minutes). Only maintenance schedules its successor first, so other failures break the succession until restart.
