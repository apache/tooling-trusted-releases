# 3.4. Code policies

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.3.` [Code conventions](code-conventions)

**Next**: `3.5.` [Build processes](build-processes)

**Sections**:

* [Introduction](#introduction)

## Introduction

These policies cover security and other miscellaneous policies that describe how our code works.

### Data

* All data stored in ATR must be public readable with the exception of PAT hashes and PII.

### Tasks

* Secret values must never be passed to tasks. This ensures that `Task` objects and results can be considered public.

### Dependencies

* Every `.pth` file installed into the project's virtual environment must appear in the allowlist of [`scripts/check_pth_files.py`](/ref/scripts/check_pth_files.py), enforced by `make sync` and pre-commit, because Python executes their `import` lines on every interpreter startup and they are a known supply-chain attack vector.
