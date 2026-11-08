---
name: Feature Request / New Dialect
about: Suggest an additional database dialect or new DDL guard rule
title: "[FEAT]: "
labels: ["enhancement"]
assignees: []
---

## Feature Summary
A clear and concise description of the dialect or feature you would like to see supported (e.g., SQLite, CockroachDB, ClickHouse, TiDB).

## Motivation & Problem
Explain why this rule or dialect is needed. What table locks or operational risks does it pose in production?

## Proposed DDL Rule or Edge Cases
Provide examples of unsafe DDL statements for this engine and the zero-downtime rewritten alternatives:

### Unsafe Pattern
```sql
-- Unsafe example
```

### Safe Zero-Downtime Replacement
```sql
-- Safe example
```

## Relevant Documentation & References
Links to official database documentation covering locking mechanisms, online schema changes, or concurrency limits (e.g., pg_locks, MySQL Online DDL, gh-ost, pt-online-schema-change).
