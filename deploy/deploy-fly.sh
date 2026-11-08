#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# SafeMigrate MCP Zero-Downtime Fly.io Deployment Script
# ==============================================================================

echo "==> [SafeMigrate MCP] Deploying to Fly.io with rolling update strategy..."

if ! command -v flyctl &> /dev/null && ! command -v fly &> /dev/null; then
    echo "ERROR: Fly CLI is not installed. Visit https://fly.io/docs/hands-on/install-flyctl/"
    exit 1
fi

FLY_CMD=$(command -v flyctl || command -v fly)

# Sync Polar secrets if set in environment
if [ -n "${POLAR_ORGANIZATION_ID:-}" ]; then
    echo "==> Setting POLAR_ORGANIZATION_ID in Fly.io secrets..."
    "${FLY_CMD}" secrets set POLAR_ORGANIZATION_ID="${POLAR_ORGANIZATION_ID}" --stage
fi

if [ -n "${AUTH_TOKENS:-}" ]; then
    echo "==> Setting AUTH_TOKENS in Fly.io secrets..."
    "${FLY_CMD}" secrets set AUTH_TOKENS="${AUTH_TOKENS}" --stage
fi

# Deploy with rolling strategy
echo "==> Triggering rolling zero-downtime deployment..."
"${FLY_CMD}" deploy --strategy rolling

echo "==> Probing deployment health..."
"${FLY_CMD}" status
echo "==> [SUCCESS] Fly.io deployment finished successfully."
