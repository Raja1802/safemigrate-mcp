"""Migration analyzer engine parsing SQL AST and evaluating risk rules."""

from typing import Optional
import re
import sqlglot
from sqlglot import exp

from src.engine.models import AnalysisFinding, LockLevel, MigrationReport, RiskTier
from src.engine.rules import ALL_RULES
from src.engine.rewriter import rewrite_migration_sql


def analyze_statement(node: exp.Expression, stmt_sql: str, dialect: str) -> list[AnalysisFinding]:
    """Evaluate a single AST node against all migration rules."""
    findings: list[AnalysisFinding] = []

    for rule in ALL_RULES:
        finding = rule(node, stmt_sql, dialect)
        if finding:
            findings.append(finding)

    # If no specific rule triggered, determine default baseline risk
    if not findings:
        raw_upper = stmt_sql.upper().strip()
        if raw_upper.startswith("SELECT") or raw_upper.startswith("EXPLAIN"):
            findings.append(
                AnalysisFinding(
                    statement=stmt_sql,
                    risk_tier=RiskTier.SAFE,
                    lock_level=LockLevel.ACCESS_SHARE,
                    causes_table_rewrite=False,
                    requires_exclusive_lock=False,
                    issue="Read-only query.",
                    remediation="Safe: Read queries acquire ACCESS SHARE locks.",
                )
            )
        elif raw_upper.startswith("INSERT") or raw_upper.startswith("UPDATE") or raw_upper.startswith("DELETE"):
            findings.append(
                AnalysisFinding(
                    statement=stmt_sql,
                    risk_tier=RiskTier.LOW,
                    lock_level=LockLevel.ROW_EXCLUSIVE,
                    causes_table_rewrite=False,
                    requires_exclusive_lock=False,
                    issue="DML query modifying rows.",
                    remediation="Ensure WHERE clauses are indexed to avoid large row lock escalations.",
                )
            )
        elif raw_upper.startswith("CREATE") or raw_upper.startswith("ALTER"):
            findings.append(
                AnalysisFinding(
                    statement=stmt_sql,
                    risk_tier=RiskTier.LOW,
                    lock_level=LockLevel.SHARE_UPDATE_EXCLUSIVE,
                    causes_table_rewrite=False,
                    requires_exclusive_lock=False,
                    issue="DDL operation with standard schema modification locking.",
                    remediation="Review lock levels and set lock_timeout.",
                )
            )

    return findings


def analyze_migration(sql_content: str, dialect: str = "postgres") -> MigrationReport:
    """Analyze a multi-statement SQL migration string and generate a comprehensive MigrationReport."""
    normalized_dialect = dialect.lower().strip()
    if normalized_dialect not in ("postgres", "mysql"):
        normalized_dialect = "postgres"

    # Clean whitespace and comments if empty
    if not sql_content.strip():
        return MigrationReport(
            dialect=normalized_dialect,
            overall_risk=RiskTier.SAFE,
            has_blocking_locks=False,
            findings=[],
            suggested_rewrite=None,
        )

    # Parse multi-statement SQL with sqlglot
    try:
        parsed_nodes = sqlglot.parse(sql_content, read=normalized_dialect)
    except Exception:
        parsed_nodes = []

    findings: list[AnalysisFinding] = []

    if parsed_nodes:
        for node in parsed_nodes:
            if not node:
                continue
            stmt_sql = node.sql(dialect=normalized_dialect)
            if not stmt_sql.strip():
                continue
            stmt_findings = analyze_statement(node, stmt_sql, normalized_dialect)
            findings.extend(stmt_findings)
    else:
        # Fallback if sqlglot failed to parse entire script
        raw_statements = [s.strip() for s in sql_content.split(";") if s.strip()]
        for stmt in raw_statements:
            try:
                node = sqlglot.parse_one(stmt, read=normalized_dialect)
            except Exception:
                node = exp.Expression()
            stmt_findings = analyze_statement(node, stmt, normalized_dialect)
            findings.extend(stmt_findings)

    # Determine overall risk
    if findings:
        highest_risk = max(findings, key=lambda f: f.risk_tier.severity_rank).risk_tier
    else:
        highest_risk = RiskTier.SAFE

    # Determine if blocking locks exist
    has_blocking = any(
        f.lock_level in (LockLevel.ACCESS_EXCLUSIVE, LockLevel.SHARE_ROW_EXCLUSIVE)
        or f.requires_exclusive_lock
        for f in findings
    )

    # Generate rewrite if high or critical risk is detected
    suggested_rewrite: Optional[str] = None
    if highest_risk in (RiskTier.HIGH, RiskTier.CRITICAL) or has_blocking:
        rewritten_sql, _ = rewrite_migration_sql(sql_content, normalized_dialect)
        if rewritten_sql.strip() and rewritten_sql.strip() != sql_content.strip():
            suggested_rewrite = rewritten_sql

    return MigrationReport(
        dialect=normalized_dialect,
        overall_risk=highest_risk,
        has_blocking_locks=has_blocking,
        findings=findings,
        suggested_rewrite=suggested_rewrite,
    )
