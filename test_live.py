"""Live verification script for SafeMigrate MCP on Fly.io."""
import sys
import httpx

# Update this with your live Fly.io app URL
BASE_URL = "https://safemigrate-mcp.fly.dev"

# Set your active token (either polar license key or AUTH_TOKENS bypass)
TEST_TOKEN = "polar_oat_TFCnU9FX4Sa0LSxzYxL84Jm12AvvPWtJMhBkx1HnWcR"


def run_checks():
    print(f"[*] Testing target: {BASE_URL}")

    # 1. Healthcheck probe (No auth required)
    res_health = httpx.get(f"{BASE_URL}/health")
    print(f"[1] GET /health -> Status {res_health.status_code}: {res_health.text}")
    assert res_health.status_code == 200, "Health check failed!"

    # 2. Unauthenticated check (Must return 401)
    res_unauth = httpx.post(
        f"{BASE_URL}/mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    print(f"[2] Unauthenticated POST /mcp -> Status {res_unauth.status_code}")
    assert res_unauth.status_code == 401, "Expected 401 on unauthenticated call!"

    # 3. Authenticated check via Bearer header
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    res_header = httpx.post(
        f"{BASE_URL}/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    print(f"[3] Bearer Auth POST /mcp -> Status {res_header.status_code}")
    if res_header.status_code == 401:
        print("    -> 401 Rejected: Check if token is present in AUTH_TOKENS or granted on Polar.")
    else:
        print("    -> Success! Server authorized the token.")

    # 4. Authenticated check via Query String fallback
    res_query = httpx.get(
        f"{BASE_URL}/mcp?customer_session_token={TEST_TOKEN}",
    )
    print(f"[4] Query String GET /mcp?customer_session_token=... -> Status {res_query.status_code}")


if __name__ == "__main__":
    try:
        run_checks()
    except Exception as e:
        print(f"\n[!] Test run error: {e}")
        sys.exit(1)
