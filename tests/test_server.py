"""Integration and unit tests for SafeMigrate MCP server, Polar.sh licensing, and TTL caching."""

import time
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from starlette.testclient import TestClient

from src.server.mcp_app import analyze_migration, rewrite_zero_downtime, check_rollback_safety
from src.server.auth import BearerAuthMiddleware
from src.server.main import create_asgi_app
from src.config import config


def test_mcp_tool_analyze_migration_direct():
    """Test analyze_migration tool directly via Python API."""
    sql = "CREATE INDEX idx_user_org ON users(org_id);"
    result = analyze_migration(sql_content=sql, dialect="postgres")
    assert isinstance(result, dict)
    assert result["overall_risk"] == "HIGH"
    assert result["has_blocking_locks"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["lock_level"] == "SHARE ROW EXCLUSIVE"


def test_mcp_tool_rewrite_zero_downtime_direct():
    """Test rewrite_zero_downtime tool directly via Python API."""
    sql = "CREATE INDEX idx_user_org ON users(org_id);"
    result = rewrite_zero_downtime(sql_content=sql, dialect="postgres")
    assert isinstance(result, dict)
    assert "CREATE INDEX CONCURRENTLY" in result["safe_sql"]
    assert len(result["changes_made"]) > 0


def test_mcp_tool_check_rollback_safety_direct():
    """Test check_rollback_safety tool comparing UP and DOWN migrations."""
    up_sql = "ALTER TABLE users ADD COLUMN age INT;"
    down_sql = "ALTER TABLE users DROP COLUMN age;"

    result = check_rollback_safety(up_sql=up_sql, down_sql=down_sql, dialect="postgres")
    assert isinstance(result, dict)
    assert result["is_rollback_safe"] is False
    assert len(result["data_loss_risks"]) >= 1
    assert any("DROP COLUMN" in r or "Critical" in r for r in result["data_loss_risks"])

    # Safe rollback test
    safe_up = "CREATE INDEX CONCURRENTLY idx_a ON t(a);"
    safe_down = "DROP INDEX CONCURRENTLY idx_a;"
    safe_result = check_rollback_safety(up_sql=safe_up, down_sql=safe_down, dialect="postgres")
    assert safe_result["is_rollback_safe"] is True


def test_unauthenticated_requests_return_401():
    """Verify that unauthenticated requests to protected endpoints return HTTP 401."""
    app = create_asgi_app()
    with TestClient(app) as client:
        # 1. Unauthenticated request to /mcp
        res = client.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert res.status_code == 401
        data = res.json()
        assert data["error"] == "Unauthorized: Invalid or expired SafeMigrate license key"

        # 2. Invalid Bearer token
        res_invalid = client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong-token-xyz"},
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        )
        assert res_invalid.status_code == 401
        assert res_invalid.json()["error"] == "Unauthorized: Invalid or expired SafeMigrate license key"

        # 3. /health endpoint is exempt from authentication
        res_health = client.get("/health")
        assert res_health.status_code == 200
        assert res_health.json()["status"] == "ok"


def test_local_fallback_token_bypass():
    """Verify that valid static local tokens bypass remote validation immediately."""
    app = create_asgi_app()
    with TestClient(app) as client:
        # Valid token from default config: "test-token-123"
        res = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test-token-123"},
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        )
        assert res.status_code != 401


def test_query_parameter_authentication():
    """Verify that credentials passed via query parameters (token, key, license_key, customer_session_token) authenticate."""
    app = create_asgi_app()
    with TestClient(app) as client:
        for param in ["token", "key", "license_key", "customer_session_token"]:
            res = client.post(
                f"/mcp?{param}=test-token-123",
                json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            )
            assert res.status_code != 401, f"Failed authentication for query param {param}"


def test_polar_license_validation_granted():
    """Verify successful token validation via Polar.sh API when status is 'granted'."""
    import asyncio

    async def _test():
        middleware = BearerAuthMiddleware(
            app=AsyncMock(),
            valid_tokens={"local-admin"},
            polar_org_id="org_polar_test_123",
            polar_api_url="https://api.polar.sh/v1",
            cache_ttl_seconds=300,
        )

        mock_response = httpx.Response(
            status_code=200,
            json={"status": "granted", "key": "polar_valid_key_001"},
            request=httpx.Request("POST", "https://api.polar.sh/v1/customer-portal/license-keys/validate"),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            is_valid = await middleware.validate_token("polar_valid_key_001")
            assert is_valid is True
            mock_post.assert_awaited_once_with(
                "https://api.polar.sh/v1/customer-portal/license-keys/validate",
                json={"key": "polar_valid_key_001", "organization_id": "org_polar_test_123"},
            )
            # Entry should be in cache
            assert "polar_valid_key_001" in middleware.token_cache
            cached_valid, _ = middleware.token_cache["polar_valid_key_001"]
            assert cached_valid is True

    asyncio.run(_test())


def test_polar_license_validation_revoked():
    """Verify rejected token validation when Polar.sh returns non-granted status or 400 error."""
    import asyncio

    async def _test():
        middleware = BearerAuthMiddleware(
            app=AsyncMock(),
            valid_tokens={"local-admin"},
            polar_org_id="org_polar_test_123",
            polar_api_url="https://api.polar.sh/v1",
            cache_ttl_seconds=300,
        )

        # 1. Status is 'revoked'
        mock_revoked = httpx.Response(
            status_code=200,
            json={"status": "revoked"},
            request=httpx.Request("POST", "https://api.polar.sh/v1/customer-portal/license-keys/validate"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_revoked
            is_valid = await middleware.validate_token("polar_revoked_key")
            assert is_valid is False

        # 2. HTTP 404 (license key not found)
        mock_not_found = httpx.Response(
            status_code=404,
            json={"detail": "License key not found"},
            request=httpx.Request("POST", "https://api.polar.sh/v1/customer-portal/license-keys/validate"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_not_found
            is_valid = await middleware.validate_token("polar_unknown_key")
            assert is_valid is False

    asyncio.run(_test())


def test_in_memory_cache_hit():
    """Verify that subsequent requests with the same token use the in-memory cache without calling Polar API."""
    import asyncio

    async def _test():
        middleware = BearerAuthMiddleware(
            app=AsyncMock(),
            valid_tokens={"local-admin"},
            polar_org_id="org_polar_test_123",
            polar_api_url="https://api.polar.sh/v1",
            cache_ttl_seconds=300,
        )

        mock_response = httpx.Response(
            status_code=200,
            json={"status": "granted"},
            request=httpx.Request("POST", "https://api.polar.sh/v1/customer-portal/license-keys/validate"),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            # First request: cache miss, calls Polar API
            res1 = await middleware.validate_token("polar_cache_test_key")
            assert res1 is True
            assert mock_post.await_count == 1

            # Second request: cache hit, must NOT call Polar API
            res2 = await middleware.validate_token("polar_cache_test_key")
            assert res2 is True
            assert mock_post.await_count == 1  # Still 1, no second call made!

    asyncio.run(_test())


def test_in_memory_cache_expiration():
    """Verify that expired cache entries trigger a fresh call to the Polar API."""
    import asyncio

    async def _test():
        middleware = BearerAuthMiddleware(
            app=AsyncMock(),
            valid_tokens={"local-admin"},
            polar_org_id="org_polar_test_123",
            polar_api_url="https://api.polar.sh/v1",
            cache_ttl_seconds=1,  # Short TTL for expiration test
        )

        mock_response = httpx.Response(
            status_code=200,
            json={"status": "granted"},
            request=httpx.Request("POST", "https://api.polar.sh/v1/customer-portal/license-keys/validate"),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            # Request 1
            res1 = await middleware.validate_token("expiring_key")
            assert res1 is True
            assert mock_post.await_count == 1

            # Manually backdate cache entry timestamp to simulate expiration
            middleware.token_cache["expiring_key"] = (True, time.time() - 10)

            # Request 2 should trigger a new HTTP call
            res2 = await middleware.validate_token("expiring_key")
            assert res2 is True
            assert mock_post.await_count == 2

    asyncio.run(_test())
