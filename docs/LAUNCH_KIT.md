# SafeMigrate MCP Launch Kit

Production-ready copy for launch day across Hacker News, Cursor Community, and X (Twitter).

---

## 1. Hacker News ("Show HN")

**Title:**
> Show HN: SafeMigrate MCP – Prevent AI coding agents from locking production DBs

**Post Content:**
> Hey HN,
>
> While using Cursor and Claude to build features, I noticed a huge failure mode: agents constantly generate naive migrations like `ALTER TABLE orders ADD COLUMN status VARCHAR NOT NULL DEFAULT 'pending'` or unindexed foreign keys on large tables.
>
> On multi-million row production databases, this grabs an `ACCESS EXCLUSIVE` lock, stalls all traffic, and knocks Postgres offline.
>
> I built SafeMigrate MCP to fix this. It’s an AST-based guardrail running over the Model Context Protocol:
> - Parses incoming SQL DDL using `sqlglot` AST.
> - Flags dangerous lock hierarchy levels (`ACCESS EXCLUSIVE`, `SHARE ROW EXCLUSIVE`) and full table rewrites.
> - Automatically rewrites them into zero-downtime multi-phase DDL (`CONCURRENTLY`, `NOT VALID` foreign keys, batch backfilling patterns).
>
> It hooks directly into Cursor, Claude Desktop, or Windsurf with 5 lines of JSON.
>
> - GitHub: https://github.com/raja1802/safemigrate-mcp
> - Live SSE Endpoint: https://safemigrate-mcp.fly.dev/mcp
>
> Would love your feedback on additional edge cases or dialects you'd like added!

---

## 2. Cursor Community Forum Post

**Category:** Showcase / Extensions  
**Title:** SafeMigrate MCP: Guardrail against table-locking migrations written by Cursor  

**Post Content:**
> If you let Cursor write migrations on Postgres/MySQL, you've probably worried about the agent dropping an unindexed constraint or creating an index without `CONCURRENTLY`.
>
> I built **SafeMigrate MCP** so Cursor can self-correct dangerous DDL before it reaches production.
>
> ### Quick Setup in Cursor
> Add this to your `~/.cursor/settings.json` or `.cursor/settings.json`:
>
> ```json
> {
>   "mcpServers": {
>     "safemigrate": {
>       "url": "https://safemigrate-mcp.fly.dev/mcp",
>       "headers": {
>         "Authorization": "Bearer polar_live_YOUR_KEY"
>       }
>     }
>   }
> }
> ```
>
> *(Or run locally in `stdio` mode via `python -m src.server.main` with zero network dependency).*
>
> ### How it Works
> When you ask Cursor: *"Add an `org_id` foreign key column to `users` with default 1"*, Cursor queries SafeMigrate MCP:
> 1. Intercepts the naive `ALTER TABLE users ADD COLUMN org_id INT NOT NULL DEFAULT 1`.
> 2. Flags `ACCESS EXCLUSIVE` lock and full table rewrite hazard.
> 3. Auto-rewrites it into a non-blocking 4-phase migration:
>    - Step 1: Add nullable column (metadata only).
>    - Step 2: Set default for future writes.
>    - Step 3: Batched backfill comment.
>    - Step 4: Add `CHECK (org_id IS NOT NULL) NOT VALID` and validate concurrently.
>
> GitHub Repo: https://github.com/raja1802/safemigrate-mcp  
> Feedback and contributions welcome!

---

## 3. X / Twitter Launch Thread

> 🚨 AI coding agents write great features, but terrible database migrations.
>
> One `ALTER TABLE ... ADD COLUMN ... DEFAULT` on a 10M row table will take down your entire PostgreSQL cluster.
>
> Today I’m open-sourcing the core of SafeMigrate MCP: a real-time migration guardrail for Cursor and Claude Desktop. 🧵👇
>
> ---
>
> 1/ The Problem: LLMs lack operational context of database lock escalation. They don't know your table size, so they default to blocking statements like `CREATE INDEX` (without CONCURRENTLY) or unvalidated foreign keys.
>
> ---
>
> 2/ The Solution: SafeMigrate inspects SQL via AST analysis (`sqlglot`), models database locking levels (`ACCESS EXCLUSIVE`, `SHARE ROW EXCLUSIVE`), and intercepts hazardous DDL before it executes.
>
> ---
>
> 3/ Zero-Downtime Auto-Rewriter: It doesn't just block; it automatically transforms unsafe queries into multi-step concurrent patterns:
> • `CREATE INDEX` ➔ `CREATE INDEX CONCURRENTLY`
> • Foreign Key ➔ `NOT VALID` + deferred `VALIDATE CONSTRAINT`
> • Defaults ➔ Nullable + Set Default + Validated Check Constraint
>
> ---
>
> 4/ Plug & Play: Connects to Cursor, Claude Desktop, or Windsurf in 10 seconds via MCP over HTTP/SSE or local `stdio`.
>
> ⭐️ Star on GitHub: https://github.com/raja1802/safemigrate-mcp
> 🚀 Live Endpoint: https://safemigrate-mcp.fly.dev/mcp
