# 7.23. Application logs

**Up**: `7.` [Developer guide](developer-guide)

**Prev**: `7.22.` [File handling](file-handling)

**Next**: `8.` [Admin dashboards](admin-dashboards)

**Sections**:

* [Inventory](#inventory)

## Inventory

Relative paths below are inside `STATE_DIR`. [Storage interface](storage-interface#what-about-audit-logging) and [Authentication security](authentication-security#audit-logging) describe the audit log in more detail.

| Log | Destination | Format | Use |
| --- | --- | --- | --- |
| Audit log | `audit/daily/YYYY-MM-DD.jsonl` | JSON Lines | Authentication events, storage writes and submitted keys |
| Release audit | `audit/releases/PROJECT/VERSION.jsonl` | JSON Lines | One release's audit entries, compiled when it is finalised |
| Requests | `logs/requests.log` | JSON Lines | HTTP request summaries without query strings, correlated with audit entries by request ID |
| Server output | Standard error, captured with Hypercorn output in `hypercorn/logs/hypercorn.log` by [`start-atr.sh`](/ref/start-atr.sh). Launch errors from the script go to the container output | Mixed JSON and plain text | Operational messages and errors with tracebacks, at `LOG_LEVEL` |
| Route timings | `logs/route-performance.log` | Text | How long each route takes, summarised in *Admin → System → Performance* |
| Worker log | `logs/atr-worker.log` | JSON Lines | Task progress, failures with tracebacks, and integrity check findings |
| Worker crashes and output | `logs/atr-worker-error.log`. Raw output is discarded unless debug capture in [`manager`](/ref/atr/manager.py) is switched on, which writes `logs/worker_TIMESTAMP_PID.log` | Text | Errors that stop a worker, and debugging |
| Recurring tasks | `logs/tasks.log` | JSON Lines | Successful runs of recurring tasks such as maintenance and audit sealing |
| Task history | `task` table in `database/atr.db` | SQLite rows | Other tasks, with arguments, timings, results and errors, shown in *Admin → System → Recent tasks* |
| Key import | `logs/keys-import.log` | Text | Output of each [`keys_import.py`](/ref/scripts/keys_import.py) run, shown in *Admin → Database → Update keys* |
| Recent logs | Memory, the last 100 server log lines, development only | Text | Shown at `/admin/logs` |
| Sent email | `logs/sent-email-dev.log`, development only | Text | Email that development instances log instead of sending |
| Frontend proxy | Set by the proxy, not by ATR | Set by the proxy | Requests and errors seen by the proxy |
