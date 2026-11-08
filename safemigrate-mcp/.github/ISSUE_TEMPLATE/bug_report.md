---
name: Bug Report / Undetected Lock
about: Report an undetected table lock, incorrect risk level, or parsing failure
title: "[BUG]: "
labels: ["bug", "ast-engine"]
assignees: []
---

## Description
A clear and concise description of what the unexpected behavior is.

## SQL Statement / Migration Payload
Provide the exact SQL snippet that caused the issue:
```sql
-- Paste SQL here
```

## Expected Behavior
What risk tier, lock level, or rewritten SQL did you expect?
- Expected Dialect: `[postgres | mysql]`
- Expected Risk Tier: `[CRITICAL | HIGH | MEDIUM | LOW | SAFE]`
- Expected Lock Level: `[ACCESS EXCLUSIVE | SHARE ROW EXCLUSIVE | SHARE UPDATE EXCLUSIVE | ...]`
- Expected Rewrite (if applicable):

## Actual Behavior
What did SafeMigrate MCP output instead?
- Actual Risk Tier:
- Actual Findings:
- Error Message / Traceback (if any):

## Environment Information
- SafeMigrate MCP Version: 0.1.0
- Transport: `[stdio | sse]`
- Client: `[Cursor | Claude Desktop | Windsurf | Direct HTTP]`
- Python Version (if local):
