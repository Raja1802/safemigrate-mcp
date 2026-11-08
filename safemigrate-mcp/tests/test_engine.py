"""Unit tests for SafeMigrate migration analysis and zero-downtime rewriting engine."""

import pytest
from src.engine.analyzer import analyze_migration
from src.engine.models import LockLevel, RiskTier
from src.engine.rewriter import rewrite_migration_sql


def test_unsafe_index_vs_concurrently():
    """Test detection of unsafe CREATE INDEX vs zero-downtime CREATE INDEX CONCURRENTLY."""
    # Unsafe index creation
    unsafe_sql = "CREATE INDEX idx_user_email ON users(email);"
    unsafe_report = analyze_migration(unsafe_sql, dialect="postgres")
    assert unsafe_report.overall_risk == RiskTier.HIGH
    assert unsafe_report.has_blocking_locks is True
    assert len(unsafe_report.findings) == 1
    assert unsafe_report.findings[0].lock_level == LockLevel.SHARE_ROW_EXCLUSIVE
    assert "CONCURRENTLY" in unsafe_report.findings[0].remediation

    # Safe concurrent index creation
    safe_sql = "CREATE INDEX CONCURRENTLY idx_user_email ON users(email);"
    safe_report = analyze_migration(safe_sql, dialect="postgres")
    assert safe_report.overall_risk == RiskTier.SAFE
    assert safe_report.has_blocking_locks is False
    assert safe_report.findings[0].lock_level == LockLevel.SHARE_UPDATE_EXCLUSIVE

    # Test rewrite
    rewritten_sql, changes = rewrite_migration_sql(unsafe_sql, dialect="postgres")
    assert "CREATE INDEX CONCURRENTLY" in rewritten_sql
    assert len(changes) == 1


def test_foreign_key_without_not_valid():
    """Test foreign key added without NOT VALID vs with NOT VALID."""
    # Unsafe foreign key
    unsafe_fk = "ALTER TABLE orders ADD CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id);"
    unsafe_report = analyze_migration(unsafe_fk, dialect="postgres")
    assert unsafe_report.overall_risk == RiskTier.HIGH
    assert unsafe_report.has_blocking_locks is True
    assert unsafe_report.findings[0].lock_level == LockLevel.ACCESS_EXCLUSIVE
    assert unsafe_report.findings[0].requires_exclusive_lock is True
    assert "NOT VALID" in unsafe_report.findings[0].remediation

    # Safe foreign key with NOT VALID
    safe_fk = "ALTER TABLE orders ADD CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;"
    safe_report = analyze_migration(safe_fk, dialect="postgres")
    assert safe_report.overall_risk == RiskTier.LOW
    assert safe_report.has_blocking_locks is False

    # Test rewrite to two-step migration
    rewritten_sql, changes = rewrite_migration_sql(unsafe_fk, dialect="postgres")
    assert "NOT VALID" in rewritten_sql
    assert "VALIDATE CONSTRAINT fk_orders_user" in rewritten_sql
    assert len(changes) == 1


def test_add_column_with_default():
    """Test adding column with default vs adding nullable column without default."""
    # Unsafe: column with DEFAULT and NOT NULL
    unsafe_col = "ALTER TABLE users ADD COLUMN age INT NOT NULL DEFAULT 0;"
    unsafe_report = analyze_migration(unsafe_col, dialect="postgres")
    assert unsafe_report.overall_risk == RiskTier.HIGH
    assert unsafe_report.has_blocking_locks is True
    assert unsafe_report.findings[0].lock_level == LockLevel.ACCESS_EXCLUSIVE
    assert unsafe_report.findings[0].causes_table_rewrite is True

    # Safe / low-risk: nullable column without default
    safe_col = "ALTER TABLE users ADD COLUMN bio TEXT;"
    safe_report = analyze_migration(safe_col, dialect="postgres")
    assert safe_report.overall_risk == RiskTier.LOW
    assert safe_report.findings[0].causes_table_rewrite is False

    # Test rewrite to 4-step safe migration pattern
    rewritten_sql, changes = rewrite_migration_sql(unsafe_col, dialect="postgres")
    assert "ADD COLUMN age INT" in rewritten_sql
    assert "ALTER COLUMN age SET DEFAULT 0" in rewritten_sql
    assert "CHECK (age IS NOT NULL) NOT VALID" in rewritten_sql
    assert "VALIDATE CONSTRAINT users_age_not_null" in rewritten_sql
    assert len(changes) == 1


def test_column_type_alteration():
    """Test column type alterations: VARCHAR expansion vs full table rewrite."""
    # Safe metadata expansion: VARCHAR(50) to VARCHAR(100) or TEXT
    safe_alter = "ALTER TABLE users ALTER COLUMN name TYPE VARCHAR(100);"
    safe_report = analyze_migration(safe_alter, dialect="postgres")
    assert safe_report.overall_risk == RiskTier.LOW
    assert safe_report.findings[0].causes_table_rewrite is False

    # Heavy rewrite: INT to BIGINT
    unsafe_alter = "ALTER TABLE users ALTER COLUMN counter TYPE BIGINT;"
    unsafe_report = analyze_migration(unsafe_alter, dialect="postgres")
    assert unsafe_report.overall_risk == RiskTier.HIGH
    assert unsafe_report.has_blocking_locks is True
    assert unsafe_report.findings[0].causes_table_rewrite is True


def test_drop_and_rename_critical_risks():
    """Test critical risk operations: DROP COLUMN, RENAME COLUMN, DROP TABLE, TRUNCATE."""
    drop_col = "ALTER TABLE users DROP COLUMN legacy_data;"
    report1 = analyze_migration(drop_col, dialect="postgres")
    assert report1.overall_risk == RiskTier.CRITICAL
    assert report1.has_blocking_locks is True

    rename_col = "ALTER TABLE users RENAME COLUMN legacy_col TO modern_col;"
    report2 = analyze_migration(rename_col, dialect="postgres")
    assert report2.overall_risk == RiskTier.CRITICAL
    assert report2.has_blocking_locks is True

    drop_tbl = "DROP TABLE deprecated_logs;"
    report3 = analyze_migration(drop_tbl, dialect="postgres")
    assert report3.overall_risk == RiskTier.CRITICAL
    assert report3.has_blocking_locks is True

    truncate_tbl = "TRUNCATE TABLE session_cache;"
    report4 = analyze_migration(truncate_tbl, dialect="postgres")
    assert report4.overall_risk == RiskTier.CRITICAL


def test_safe_ddl_create_table():
    """Test that creating a new table is categorized as RiskTier.SAFE."""
    create_tbl = """
    CREATE TABLE audit_logs (
        id SERIAL PRIMARY KEY,
        action VARCHAR(255) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """
    report = analyze_migration(create_tbl, dialect="postgres")
    assert report.overall_risk == RiskTier.SAFE
    assert report.has_blocking_locks is False


def test_multi_statement_migration():
    """Test multiple statements in a single payload aggregating to highest risk."""
    multi_sql = """
    -- Safe new table
    CREATE TABLE accounts (id SERIAL PRIMARY KEY, name VARCHAR(100));

    -- Unsafe index on existing high-traffic table
    CREATE INDEX idx_users_email ON users(email);

    -- Foreign key without NOT VALID
    ALTER TABLE orders ADD CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id);
    """
    report = analyze_migration(multi_sql, dialect="postgres")
    assert report.overall_risk == RiskTier.HIGH
    assert report.has_blocking_locks is True
    # At least 3 findings
    assert len(report.findings) >= 3
    assert report.suggested_rewrite is not None
    assert "CONCURRENTLY" in report.suggested_rewrite
    assert "NOT VALID" in report.suggested_rewrite
