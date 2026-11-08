# SafeMigrate MCP Guard

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Protocol](https://img.shields.io/badge/MCP-1.3+-green.svg)](https://modelcontextprotocol.io/)
[![FastMCP](https://img.shields.io/badge/FastMCP-0.4+-purple.svg)](https://github.com/jlowin/fastmcp)
[![Polar.sh](https://img.shields.io/badge/monetization-Polar.sh-blue.svg)](https://polar.sh)
[![Tests](https://img.shields.io/badge/tests-16%20passed-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)]()

**SafeMigrate MCP** is a production-grade, remote Model Context Protocol (MCP) server that analyzes relational database migrations (PostgreSQL & MySQL) for hazardous locking operations, evaluates table rewrite risks, and automatically produces zero-downtime rewritten SQL statements.

Equipped with **self-service monetization via Polar.sh**, SafeMigrate MCP automatically verifies customer license keys with an in-memory TTL cache to deliver low-latency responses for remote users and IDE clients (Cursor, Claude Desktop).

---

## 1. Architecture & Monetization Overview

SafeMigrate MCP operates on dual transports (`stdio` for local developers and `sse`/HTTP for cloud deployment), protected by a hybrid licensing layer:

```
                      +-----------------------------+
                      |   AI Coding Assistant       |
                      |  (Cursor / Claude Desktop)  |
                      +--------------+--------------+
                                     |
               Standard I/O (Local)  |  Streamable HTTP / SSE (Remote)
                   `stdio`           |   Authorization: Bearer <license_key>
                                     v
                      +-----------------------------+
                      |    SafeMigrate MCP Server   |
                      |   (FastMCP + Starlette)     |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |   BearerAuthMiddleware      |
                      |  1. Local Static Bypass     |
                      |  2. In-Memory TTL Cache     |
                      |  3. Polar.sh Validation API |
                      +--------------+--------------+
                                     |
                             (Granted / Valid)
                                     v
                      +-----------------------------+
                      |       Analysis Engine       |
                      |   - sqlglot AST Parser      |
                      |   - Lock Hierarchy Model    |
                      |   - Zero-Downtime Rewriter  |
                      +-----------------------------+
```

### Self-Service Polar.sh Workflow
1. **Customer Purchase**: The customer purchases a monthly or annual subscription or license key directly from your Polar.sh store.
2. **Key Issuance**: Polar automatically issues a unique customer license key (e.g., `polar_live_...`).
3. **Client Configuration**: The user adds the license key to their Cursor or Claude Desktop config as `Bearer <key>`.
4. **Validation & TTL Caching**:
   - First request hits `https://api.polar.sh/v1/customer-portal/license-keys/validate`.
   - On `status == "granted"`, the key is cached in-memory for `TOKEN_CACHE_TTL_SECONDS` (default 300s / 5 minutes).
   - Subsequent requests within the TTL window resolve instantaneously with zero external network overhead.
   - If a subscription cancels or revokes, validation fails immediately after the TTL expires.

---

## 2. Table Lock Levels Modeled

SafeMigrate classifies operations into PostgreSQL and MySQL locking tiers:
- **`ACCESS EXCLUSIVE`**: Blocks all reads and writes (`SELECT`, `INSERT`, `UPDATE`, `DELETE`). Triggered by `ALTER TABLE ADD COLUMN ... DEFAULT`, `DROP TABLE`, `ALTER COLUMN TYPE`, and unvalidated foreign keys.
- **`SHARE ROW EXCLUSIVE`**: Blocks concurrent writes and schema modifications. Triggered by `CREATE INDEX` without `CONCURRENTLY`.
- **`SHARE UPDATE EXCLUSIVE`**: Concurrent-safe lock; allows reads and writes. Used by `CREATE INDEX CONCURRENTLY` and `VALIDATE CONSTRAINT`.
- **`ROW EXCLUSIVE`**: Standard DML lock for row mutations (`INSERT`, `UPDATE`, `DELETE`).
- **`ACCESS SHARE`**: Standard query lock for `SELECT` queries.
- **`NONE`**: Metadata-only or safe creation of unreferenced new tables.

---

## 3. Exposed MCP Tools

| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| `analyze_migration` | `sql_content: str`, `dialect: str = "postgres"` | Parses SQL AST, identifies lock conflicts, table rewrites, and outputs a structured `MigrationReport`. |
| `rewrite_zero_downtime` | `sql_content: str`, `dialect: str = "postgres"` | Evaluates unsafe patterns and outputs non-blocking, multi-phase SQL with inline engineering rationale. |
| `check_rollback_safety` | `up_sql: str`, `down_sql: str`, `dialect: str = "postgres"` | Cross-analyzes forward and backward migrations to detect irreversible data loss risks (e.g., column drops, truncations). |

---

## 4. Configuration Reference

All settings can be configured via environment variables or a `.env` file:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `HOST` | string | `0.0.0.0` | Bind host for HTTP / SSE server. |
| `PORT` | integer | `8000` | Bind port for HTTP / SSE server. |
| `TRANSPORT` | string | `sse` | Transport mode: `stdio` (local IDE) or `sse` (remote HTTP). |
| `AUTH_TOKENS` | string | `test-token-123` | Comma-separated list of static bypass tokens for testing/admin. |
| `RATE_LIMIT_ENABLED` | boolean | `false` | Enable/disable rate limiting on incoming requests. |
| `POLAR_ORGANIZATION_ID` | string | `""` | Your Polar.sh Organization ID. If empty, only static tokens are valid. |
| `POLAR_API_URL` | string | `https://api.polar.sh/v1` | Polar.sh API base URL. |
| `TOKEN_CACHE_TTL_SECONDS` | integer | `300` | In-memory license validation cache TTL in seconds (5 minutes). |

---

## 5. Client Setup (Cursor & Claude Desktop)

### Cursor Integration (`~/.cursor/settings.json`)

#### Remote Cloud Server with Polar.sh License Key
```json
{
  "mcpServers": {
    "safemigrate-remote": {
      "url": "https://safemigrate-mcp.fly.dev/mcp",
      "headers": {
        "Authorization": "Bearer polar_live_YOUR_LICENSE_KEY_HERE"
      }
    }
  }
}
```

#### Local Developer Mode (`stdio`)
```json
{
  "mcpServers": {
    "safemigrate-local": {
      "command": "python",
      "args": ["-m", "src.server.main"],
      "env": {
        "TRANSPORT": "stdio"
      }
    }
  }
}
```

---

### Claude Desktop Integration (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "safemigrate": {
      "command": "python",
      "args": ["-m", "src.server.main"],
      "env": {
        "TRANSPORT": "stdio"
      }
    }
  }
}
```

---

## 6. Multi-Cloud Zero-Downtime Deployment

SafeMigrate MCP includes production configurations and deployment scripts for zero-downtime updates.

### A. Fly.io Deployment
The provided `fly.toml` uses a rolling deployment strategy with automatic health check monitoring:

```bash
# 1. Login to Fly
fly auth login

# 2. Launch or deploy with automated script
chmod +x deploy/deploy-fly.sh
POLAR_ORGANIZATION_ID="org_your_polar_id" ./deploy/deploy-fly.sh
```

### B. Railway Deployment
The provided `railway.json` includes healthcheck validation before routing traffic:

```bash
# 1. Login to Railway
railway login

# 2. Deploy using deployment script
chmod +x deploy/deploy-railway.sh
POLAR_ORGANIZATION_ID="org_your_polar_id" ./deploy/deploy-railway.sh
```

### C. Docker VPS Deployment (Canary Verification)
The `deploy/deploy-vps.sh` script spins up a canary container on port 8001, verifies the `/health` endpoint responds `200 OK`, and then replaces the production container on port 8000:

```bash
cp .env.example .env
# Edit .env with your POLAR_ORGANIZATION_ID

chmod +x deploy/deploy-vps.sh
./deploy/deploy-vps.sh
```

### Healthcheck Verification
```bash
curl -f http://localhost:8000/health
# Response: {"status":"ok","service":"safemigrate-mcp"}
```

---

## 7. Sample Agent Interaction

Here is a transcript showing how an AI coding assistant (e.g., Cursor) uses SafeMigrate MCP to intercept unsafe DDL and auto-correct it into concurrent, zero-downtime SQL:

### User Prompt
> "Please write a migration to add a unique index on `users(email)` and an `organization_id` foreign key referencing `organizations(id)`."

### Agent Interception & Analysis
The assistant invokes `analyze_migration`:
```json
{
  "sql_content": "CREATE UNIQUE INDEX idx_users_email ON users(email);\nALTER TABLE users ADD CONSTRAINT fk_org FOREIGN KEY (organization_id) REFERENCES organizations(id);"
}
```

### Server Report
```json
{
  "dialect": "postgres",
  "overall_risk": "HIGH",
  "has_blocking_locks": true,
  "findings": [
    {
      "statement": "CREATE UNIQUE INDEX idx_users_email ON users(email)",
      "risk_tier": "HIGH",
      "lock_level": "SHARE ROW EXCLUSIVE",
      "causes_table_rewrite": false,
      "requires_exclusive_lock": false,
      "issue": "Creating an index without CONCURRENTLY acquires a SHARE ROW EXCLUSIVE lock, blocking table writes (INSERT, UPDATE, DELETE).",
      "remediation": "Add CONCURRENTLY keyword to allow non-blocking writes."
    },
    {
      "statement": "ALTER TABLE users ADD CONSTRAINT fk_org FOREIGN KEY (organization_id) REFERENCES organizations(id)",
      "risk_tier": "HIGH",
      "lock_level": "ACCESS EXCLUSIVE",
      "causes_table_rewrite": false,
      "requires_exclusive_lock": true,
      "issue": "Adding a foreign key constraint without NOT VALID acquires an ACCESS EXCLUSIVE lock and scans the entire table, blocking all concurrent transactions.",
      "remediation": "Use 'ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID', then run 'VALIDATE CONSTRAINT' in a separate transaction."
    }
  ]
}
```

### Agent Auto-Correction via `rewrite_zero_downtime`
The assistant generates the zero-downtime replacement:

```sql
-- 1. Create Index Concurrently (non-blocking concurrent writes)
CREATE UNIQUE INDEX CONCURRENTLY idx_users_email ON users(email);

-- 2. Add Foreign Key as NOT VALID (avoids table scan under ACCESS EXCLUSIVE lock)
ALTER TABLE users ADD CONSTRAINT fk_org FOREIGN KEY (organization_id) REFERENCES organizations(id) NOT VALID;

-- 3. Validate Foreign Key in Separate Non-Blocking Step
ALTER TABLE users VALIDATE CONSTRAINT fk_org;
```

---

## 8. Development & Testing

Run all 16 unit and integration tests (including Polar API validation mocks and in-memory TTL cache assertions):

```bash
pytest -v
```

---

## 9. License

MIT License. Designed and maintained for reliable, zero-downtime database operations.
