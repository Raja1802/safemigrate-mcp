#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# SafeMigrate MCP Zero-Downtime VPS Deployment Script
# Uses Docker Compose with rolling container health verification
# ==============================================================================

echo "==> [SafeMigrate MCP] Starting zero-downtime VPS deployment..."

APP_NAME="safemigrate-mcp"
NEW_IMAGE="${APP_NAME}:latest"

# 1. Pull / Build latest image
echo "==> Building fresh production container..."
docker build -t "${NEW_IMAGE}" -f Dockerfile .

# 2. Check if existing container is running
EXISTING_CONTAINER=$(docker ps -q -f name="${APP_NAME}")

if [ -n "${EXISTING_CONTAINER}" ]; then
    echo "==> Existing container detected (${EXISTING_CONTAINER}). Launching canary..."
    CANARY_CONTAINER="${APP_NAME}-canary"
    
    docker run -d \
        --name "${CANARY_CONTAINER}" \
        --env-file .env \
        -p 8001:8000 \
        "${NEW_IMAGE}"

    echo "==> Probing canary healthcheck at http://localhost:8001/health..."
    HEALTHY=false
    for i in {1..12}; do
        if curl -s -f http://localhost:8001/health | grep -q '"status":"ok"'; then
            HEALTHY=true
            echo "==> Canary passed health check on attempt $i!"
            break
        fi
        echo "    Waiting for canary to become ready... (attempt $i/12)"
        sleep 2
    done

    if [ "$HEALTHY" = false ]; then
        echo "ERROR: Canary failed healthcheck! Rolling back and aborting."
        docker stop "${CANARY_CONTAINER}" && docker rm "${CANARY_CONTAINER}"
        exit 1
    fi

    # Clean up canary
    docker stop "${CANARY_CONTAINER}" && docker rm "${CANARY_CONTAINER}"
    
    # Switch main service
    echo "==> Re-launching production container on port 8000..."
    docker stop "${APP_NAME}" || true
    docker rm "${APP_NAME}" || true
fi

# 3. Start production container
docker run -d \
    --name "${APP_NAME}" \
    --restart unless-stopped \
    --env-file .env \
    -p 8000:8000 \
    "${NEW_IMAGE}"

# 4. Final verification
sleep 3
if curl -s -f http://localhost:8000/health | grep -q '"status":"ok"'; then
    echo "==> [SUCCESS] SafeMigrate MCP zero-downtime deployment complete!"
    docker ps -f name="${APP_NAME}"
else
    echo "ERROR: Production container failed final health check!"
    exit 1
fi
