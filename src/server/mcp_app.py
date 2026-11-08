"""FastMCP application exposing SafeMigrate Guard tools."""

from typing import Any
from fastmcp import FastMCP

from src.engine.analyzer import analyze_migration as run_analysis
from src.engine.rewriter import rewrite_migration_sql
from src.engine.models import RiskTier

# Initialize the FastMCP server
mcp = FastMCP("SafeMigrate Guard")


@mcp.tool()
def analyze_migration(sql_content: str, dialect: str = "postgres") -> dict[str, Any]:
    """Analyze a SQL migration script for heavy locks, table rewrites, and concurrency risks.

    Args:
        sql_content: The raw SQL migration statements to evaluate.
        dialect: SQL dialect ('postgres' or 'mysql'). Defaults to 'postgres'.

    Returns:
        Serialized MigrationReport containing overall risk, blocking lock presence, and findings.
    """
    report = run_analysis(sql_content, dialect=dialect)
    return report.model_dump()


@mcp.tool()
def rewrite_zero_downtime(sql_content: str, dialect: str = "postgres") -> dict[str, Any]:
    """Generate safe, zero-downtime rewritten SQL statements for unsafe migration patterns.

    Args:
        sql_content: The SQL migration statements to rewrite.
        dialect: SQL dialect ('postgres' or 'mysql'). Defaults to 'postgres'.

    Returns:
        Dictionary with original SQL, safe rewritten SQL with rationale comments, and changes made.
    """
    report = run_analysis(sql_content, dialect=dialect)
    safe_sql, changes_made = rewrite_migration_sql(sql_content, dialect=dialect)

    if not changes_made:
        safe_sql = sql_content
        changes_made = ["No unsafe migration patterns detected. Migration is zero-downtime safe."]

    return {
        "original_sql": sql_content,
        "safe_sql": safe_sql,
        "changes_made": changes_made,
        "overall_risk": report.overall_risk.value,
        "has_blocking_locks": report.has_blocking_locks,
    }


@mcp.tool()
def check_rollback_safety(up_sql: str, down_sql: str, dialect: str = "postgres") -> dict[str, Any]:
    """Analyze forward (UP) and rollback (DOWN) migration scripts to detect data loss and rollback hazards.

    Args:
        up_sql: The forward migration SQL.
        down_sql: The rollback/down migration SQL.
        dialect: SQL dialect ('postgres' or 'mysql'). Defaults to 'postgres'.

    Returns:
        Analysis comparing UP and DOWN migrations with data loss risk evaluations and recommendations.
    """
    up_report = run_analysis(up_sql, dialect=dialect)
    down_report = run_analysis(down_sql, dialect=dialect)

    data_loss_risks: list[str] = []
    recommendations: list[str] = []

    # Inspect DOWN migration for destructive operations
    for finding in down_report.findings:
        if finding.risk_tier == RiskTier.CRITICAL:
            data_loss_risks.append(f"Critical risk in rollback: {finding.statement} - {finding.issue}")
            recommendations.append(finding.remediation)
        elif finding.requires_exclusive_lock or finding.causes_table_rewrite:
            data_loss_risks.append(f"Heavy lock in rollback: {finding.statement} ({finding.lock_level.value})")

    is_rollback_safe = len(data_loss_risks) == 0

    if is_rollback_safe:
        recommendations.append("Rollback script does not exhibit irreversible table drops or column destructions.")

    return {
        "is_rollback_safe": is_rollback_safe,
        "data_loss_risks": data_loss_risks,
        "up_migration": up_report.model_dump(),
        "down_migration": down_report.model_dump(),
        "recommendations": recommendations,
    }
