#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# SafeMigrate MCP Zero-Downtime Railway Deployment Script
# ==============================================================================

echo "==> [SafeMigrate MCP] Deploying to Railway..."

if ! command -v railway &> /dev/null; then
    echo "ERROR: Railway CLI is not installed. Visit https://docs.railway.app/guides/cli"
    exit 1
fi

# Set Polar organization environment variable if provided
if [ -n "${POLAR_ORGANIZATION_ID:-}" ]; then
    echo "==> Setting POLAR_ORGANIZATION_ID in Railway..."
    railway variables --set "POLAR_ORGANIZATION_ID=${POLAR_ORGANIZATION_ID}"
fi

if [ -n "${AUTH_TOKENS:-}" ]; then
    echo "==> Setting AUTH_TOKENS in Railway..."
    railway variables --set "AUTH_TOKENS=${AUTH_TOKENS}"
fi

railway variables --set "TRANSPORT=sse"
railway variables --set "HOST=0.0.0.0"

# Trigger deployment
echo "==> Triggering Railway deployment with Dockerfile..."
railway up --detach

echo "==> [SUCCESS] Railway deployment triggered. Monitored via healthcheck at /health."
