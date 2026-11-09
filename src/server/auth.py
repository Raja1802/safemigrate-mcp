"""Authentication middleware for SafeMigrate MCP supporting Polar.sh licensing, TTL caching, and query param tokens."""
import json
import time
from typing import Iterable
from urllib.parse import parse_qs
import httpx
from starlette.types import ASGIApp, Receive, Scope, Send
from src.config import config


class BearerAuthMiddleware:
    """ASGI middleware verifying tokens via local bypass and Polar.sh license keys with TTL caching."""

    def __init__(
        self,
        app: ASGIApp,
        valid_tokens: set[str] | None = None,
        excluded_paths: Iterable[str] = ("/health",),
        polar_org_id: str | None = None,
        polar_api_url: str | None = None,
        cache_ttl_seconds: int | None = None,
    ):
        self.app = app
        self.valid_tokens = valid_tokens if valid_tokens is not None else config.valid_tokens
        self.excluded_paths = set(excluded_paths)
        self.polar_org_id = polar_org_id if polar_org_id is not None else config.POLAR_ORGANIZATION_ID
        self.polar_api_url = polar_api_url if polar_api_url is not None else config.POLAR_API_URL
        self.cache_ttl_seconds = (
            cache_ttl_seconds if cache_ttl_seconds is not None else config.TOKEN_CACHE_TTL_SECONDS
        )
        self.token_cache: dict[str, tuple[bool, float]] = {}

    async def validate_token(self, token: str) -> bool:
        """Validate token against local bypass list, in-memory TTL cache, or Polar.sh API."""
        if not token:
            return False

        # 1. Local static bypass check (internal dev, admin tokens)
        if token in self.valid_tokens:
            return True

        current_time = time.time()

        # 2. In-memory TTL cache check
        if token in self.token_cache:
            is_valid, expire_ts = self.token_cache[token]
            if current_time < expire_ts:
                return is_valid

        # 3. Polar.sh License Key API Call
        if self.polar_org_id:
            url = f"{self.polar_api_url.rstrip('/')}/customer-portal/license-keys/validate"
            payload = {
                "key": token,
                "organization_id": self.polar_org_id,
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload)
                    if response.status_code == 200:
                        data = response.json()
                        is_valid = data.get("status") == "granted"
                    else:
                        is_valid = False
            except Exception:
                is_valid = False

            self.token_cache[token] = (is_valid, current_time + self.cache_ttl_seconds)
            return is_valid

        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Always permit unauthenticated health check endpoint
        if path in self.excluded_paths:
            if path == "/health":
                response_body = json.dumps({"status": "ok", "service": "safemigrate-mcp"}).encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(response_body)).encode("ascii")),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": response_body,
                })
                return
            await self.app(scope, receive, send)
            return

        token: str | None = None

        # Strategy 1: Extract Authorization header from raw ASGI headers
        headers = dict(scope.get("headers", []))
        auth_raw = headers.get(b"authorization", b"").decode("latin-1").strip()
        if auth_raw.startswith("Bearer "):
            token = auth_raw[7:].strip()

        # Strategy 2: Fallback to query parameters
        if not token:
            query_string = scope.get("query_string", b"").decode("latin-1")
            if query_string:
                params = parse_qs(query_string)
                token = (
                    params.get("token", [None])[0]
                    or params.get("key", [None])[0]
                    or params.get("license_key", [None])[0]
                    or params.get("customer_session_token", [None])[0]
                )

        authorized = False
        if token:
            authorized = await self.validate_token(token)

        if not authorized:
            error_body = json.dumps(
                {"error": "Unauthorized: Invalid or expired SafeMigrate license key"}
            ).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(error_body)).encode("ascii")),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": error_body,
            })
            return

        await self.app(scope, receive, send)
