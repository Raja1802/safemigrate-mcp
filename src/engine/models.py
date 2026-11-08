"""Domain models for SafeMigrate MCP migration analysis and reporting."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class LockLevel(str, Enum):
    """Database table lock levels and their concurrency implications."""

    ACCESS_EXCLUSIVE = "ACCESS EXCLUSIVE"
    SHARE_ROW_EXCLUSIVE = "SHARE ROW EXCLUSIVE"
    SHARE_UPDATE_EXCLUSIVE = "SHARE UPDATE EXCLUSIVE"
    ROW_EXCLUSIVE = "ROW EXCLUSIVE"
    ACCESS_SHARE = "ACCESS SHARE"
    NONE = "NONE"


class RiskTier(str, Enum):
    """Risk tiers for migration operations ranked by operational impact."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    SAFE = "SAFE"

    @property
    def severity_rank(self) -> int:
        """Numeric rank for risk severity (higher is more critical)."""
        ranks = {
            RiskTier.SAFE: 0,
            RiskTier.LOW: 1,
            RiskTier.MEDIUM: 2,
            RiskTier.HIGH: 3,
            RiskTier.CRITICAL: 4,
        }
        return ranks.get(self, 0)

    def __ge__(self, other: object) -> bool:
        if isinstance(other, RiskTier):
            return self.severity_rank >= other.severity_rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, RiskTier):
            return self.severity_rank > other.severity_rank
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, RiskTier):
            return self.severity_rank <= other.severity_rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:
        if isinstance(other, RiskTier):
            return self.severity_rank < other.severity_rank
        return NotImplemented


class AnalysisFinding(BaseModel):
    """An individual migration finding detailing lock impact, risk, and remediation."""

    statement: str = Field(..., description="The SQL statement that was analyzed.")
    risk_tier: RiskTier = Field(..., description="Risk severity tier.")
    lock_level: LockLevel = Field(..., description="Postgres/MySQL lock level acquired.")
    causes_table_rewrite: bool = Field(
        ..., description="Whether this operation triggers a full table rewrite."
    )
    requires_exclusive_lock: bool = Field(
        ..., description="Whether this operation acquires an exclusive table lock."
    )
    issue: str = Field(..., description="Explanation of the operational risk.")
    remediation: str = Field(..., description="Recommended safe zero-downtime alternative.")


class MigrationReport(BaseModel):
    """Complete migration analysis report aggregating findings across all statements."""

    dialect: str = Field(default="postgres", description="SQL dialect analyzed.")
    overall_risk: RiskTier = Field(
        default=RiskTier.SAFE, description="Highest risk tier identified across statements."
    )
    has_blocking_locks: bool = Field(
        default=False,
        description="True if any operation acquires ACCESS EXCLUSIVE or SHARE ROW EXCLUSIVE locks.",
    )
    findings: list[AnalysisFinding] = Field(
        default_factory=list, description="List of granular findings per statement."
    )
    suggested_rewrite: Optional[str] = Field(
        default=None, description="Rewritten zero-downtime SQL script, if applicable."
    )
