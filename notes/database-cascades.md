# Database Cascades
Certain deletions in the database automatically cascade to related entities. This document lists them for clarity.

| Parent Entity | Cascades To   | Migration File | Notes |
|---------------|--------------|----------------|-------|
| Release       | CheckResult   | 0008_2025.06.12_26c0022b.py | Deleting a Release automatically deletes all associated CheckResult entries. This is implemented using a CASCADE foreign key. |

> **Note:** Deleting parent entities can automatically delete associated child records. Always be careful with delete operations.
