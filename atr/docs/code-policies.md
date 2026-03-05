# 3.10 Code policies

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.9.` [Code conventions](code-conventions)

**Next**: `3.11.` [How to contribute](how-to-contribute)

**Sections**:

* [Introduction](#introduction)

## Introduction

These policies cover security and other miscellaneous policies that describe how our code works.

### Data

* All data stored in ATR must be public readable with the exception of PAT hashes and PII.

### Tasks

* Secret values must never be passed to tasks. This ensures that `Task` objects and results can be considered public.
