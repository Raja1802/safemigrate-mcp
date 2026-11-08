"""Main execution entrypoint for SafeMigrate MCP server."""

import sys
import uvicorn

from src.config import config
from src.server.auth import BearerAuthMiddleware
from src.server.mcp_app import mcp


def create_asgi_app():
    """Create and configure the authenticated ASGI application for SSE/HTTP transport."""
    base_app = mcp.http_app()
    authenticated_app = BearerAuthMiddleware(base_app)
    return authenticated_app


def main():
    """Run the SafeMigrate MCP server according to configured transport."""
    transport = config.TRANSPORT.lower().strip()

    if transport == "stdio":
        # Run MCP server using standard I/O for local IDEs (Cursor / Claude Desktop)
        mcp.run(transport="stdio")
    elif transport in ("sse", "http"):
        # Run MCP server using HTTP / Server-Sent Events with Bearer auth middleware
        app = create_asgi_app()
        uvicorn.run(
            app,
            host=config.HOST,
            port=config.PORT,
            log_level="info",
        )
    else:
        print(f"Error: Unknown transport '{transport}'. Valid options are 'stdio' or 'sse'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
