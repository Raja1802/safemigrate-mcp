"""Configuration settings for SafeMigrate MCP server."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for SafeMigrate MCP server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    TRANSPORT: str = "sse"  # "sse" or "stdio"
    AUTH_TOKENS: str = "test-token-123"
    RATE_LIMIT_ENABLED: bool = False

    # Polar.sh self-service licensing configuration
    POLAR_ORGANIZATION_ID: str = ""
    POLAR_API_URL: str = "https://api.polar.sh/v1"
    TOKEN_CACHE_TTL_SECONDS: int = 300

    @property
    def valid_tokens(self) -> set[str]:
        """Return the set of valid authorization tokens parsed from AUTH_TOKENS."""
        return {token.strip() for token in self.AUTH_TOKENS.split(",") if token.strip()}


config = Settings()
