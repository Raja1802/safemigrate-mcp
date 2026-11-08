"""Migration risk rules and AST inspection evaluators."""

from typing import Optional
import re
import sqlglot
from sqlglot import exp

from src.engine.models import AnalysisFinding, LockLevel, RiskTier


def _is_varchar_or_text_expansion(action: exp.Expression, raw_sql: str) -> bool:
    """Check if column type alteration is an expansion from VARCHAR(N) to VARCHAR(M) (M > N) or TEXT."""
    raw_upper = raw_sql.upper()
    
    # Check if target is VARCHAR or TEXT
    dtype = action.args.get("dtype")
    if not dtype:
        # Fallback to raw text check
        return "TYPE TEXT" in raw_upper or bool(re.search(r"TYPE\s+VARCHAR\s*\(\s*\d+\s*\)", raw_upper))
    
    target_type = dtype.this.name if hasattr(dtype.this, "name") else str(dtype.this).upper()
    if target_type == "TEXT":
        return True
    
    if target_type in ("VARCHAR", "CHAR"):
        # If raw sql shows an increase (e.g. from VARCHAR(50) to VARCHAR(100))
        # Or target is a wider varchar
        params = dtype.args.get("expressions", [])
        if params:
            try:
                target_len = int(params[0].this.name if hasattr(params[0].this, "name") else str(params[0].this))
                # Search if previous size was mentioned in comment or if it's expanding
                match = re.search(r"VARCHAR\s*\(\s*(\d+)\s*\).*?TO.*?VARCHAR\s*\(\s*(\d+)\s*\)", raw_upper)
                if match:
                    old_len = int(match.group(1))
                    new_len = int(match.group(2))
                    return new_len >= old_len
                # If target length is reasonably large or explicitly changing to VARCHAR(100+)
                return True
            except (ValueError, TypeError):
                pass
        return True

    return False


def check_index_creation(node: exp.Expression, sql_text: str, dialect: str) -> Optional[AnalysisFinding]:
    """Detect unsafe index creation lacking CONCURRENTLY or online algorithms."""
    is_create_index = (
        (isinstance(node, exp.Create) and str(node.args.get("kind", "")).upper() == "INDEX")
        or isinstance(node, exp.Index)
        or bool(re.match(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX", sql_text, re.IGNORECASE))
    )
    if not is_create_index:
        return None

    raw_upper = sql_text.upper()
    is_concurrent = (
        bool(node.args.get("concurrently"))
        or "CONCURRENTLY" in raw_upper
    )

    if dialect == "postgres":
        if is_concurrent:
            return AnalysisFinding(
                statement=sql_text,
                risk_tier=RiskTier.SAFE,
                lock_level=LockLevel.SHARE_UPDATE_EXCLUSIVE,
                causes_table_rewrite=False,
                requires_exclusive_lock=False,
                issue="Index created with CONCURRENTLY, allowing concurrent reads and writes.",
                remediation="Safe: Concurrent index build prevents blocking write traffic.",
            )
        else:
            return AnalysisFinding(
                statement=sql_text,
                risk_tier=RiskTier.HIGH,
                lock_level=LockLevel.SHARE_ROW_EXCLUSIVE,
                causes_table_rewrite=False,
                requires_exclusive_lock=False,
                issue="Creating an index without CONCURRENTLY acquires a SHARE ROW EXCLUSIVE lock, blocking table writes (INSERT, UPDATE, DELETE).",
                remediation="Add CONCURRENTLY keyword to allow non-blocking writes.",
            )
    elif dialect == "mysql":
        has_online_clause = "ALGORITHM=INPLACE" in raw_upper or "LOCK=NONE" in raw_upper
        if has_online_clause:
            return AnalysisFinding(
                statement=sql_text,
                risk_tier=RiskTier.SAFE,
                lock_level=LockLevel.NONE,
                causes_table_rewrite=False,
                requires_exclusive_lock=False,
                issue="Index created using online DDL algorithm in MySQL.",
                remediation="Safe: ALGORITHM=INPLACE and LOCK=NONE allow non-blocking operations.",
            )
        else:
            return AnalysisFinding(
                statement=sql_text,
                risk_tier=RiskTier.MEDIUM,
                lock_level=LockLevel.SHARE_ROW_EXCLUSIVE,
                causes_table_rewrite=False,
                requires_exclusive_lock=False,
                issue="Creating an index without ALGORITHM=INPLACE, LOCK=NONE may stall concurrent writes in MySQL.",
                remediation="Specify ALGORITHM=INPLACE, LOCK=NONE for zero-downtime index creation.",
            )

    return None


def check_foreign_key_constraint(node: exp.Expression, sql_text: str, dialect: str) -> Optional[AnalysisFinding]:
    """Detect foreign key constraints added without NOT VALID."""
    raw_upper = sql_text.upper()
    has_fk = False

    if isinstance(node, exp.Alter):
        for action in node.args.get("actions", []):
            if action.find(exp.ForeignKey) or action.find(exp.Reference) or "FOREIGN KEY" in action.sql().upper():
                has_fk = True
                break
    elif "FOREIGN KEY" in raw_upper and "ADD" in raw_upper:
        has_fk = True

    if not has_fk:
        return None

    if dialect == "postgres":
        has_not_valid = "NOT VALID" in raw_upper or bool(getattr(node, "args", {}).get("not_valid"))
        if not has_not_valid:
            return AnalysisFinding(
                statement=sql_text,
                risk_tier=RiskTier.HIGH,
                lock_level=LockLevel.ACCESS_EXCLUSIVE,
                causes_table_rewrite=False,
                requires_exclusive_lock=True,
                issue="Adding a foreign key constraint without NOT VALID acquires an ACCESS EXCLUSIVE lock and scans the entire table, blocking all concurrent transactions.",
                remediation="Use 'ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID', then run 'VALIDATE CONSTRAINT' in a separate transaction.",
            )
        else:
            return AnalysisFinding(
                statement=sql_text,
                risk_tier=RiskTier.LOW,
                lock_level=LockLevel.SHARE_UPDATE_EXCLUSIVE,
                causes_table_rewrite=False,
                requires_exclusive_lock=False,
                issue="Foreign key constraint added with NOT VALID. Avoids full table scan under heavy lock.",
                remediation="Safe: Remember to run 'VALIDATE CONSTRAINT' in a separate transaction.",
            )
    elif dialect == "mysql":
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.HIGH,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=True,
            requires_exclusive_lock=True,
            issue="Adding a foreign key constraint in MySQL locks the child and parent tables during verification.",
            remediation="Ensure table lock time is minimized or disable foreign key checks temporarily if safe.",
        )

    return None


def check_add_column(node: exp.Expression, sql_text: str, dialect: str) -> Optional[AnalysisFinding]:
    """Detect ADD COLUMN operations, especially with DEFAULT expressions or NOT NULL."""
    raw_upper = sql_text.upper()

    # If statement is adding a constraint or foreign key, exclude from add_column
    if "ADD CONSTRAINT" in raw_upper or "FOREIGN KEY" in raw_upper:
        return None

    has_add_column = False
    has_default = False
    has_not_null = False

    if isinstance(node, exp.Alter):
        for action in node.args.get("actions", []):
            if isinstance(action, exp.ColumnDef) and not isinstance(action, exp.AddConstraint):
                has_add_column = True
                constraints = action.args.get("constraints", [])
                for c in constraints:
                    if isinstance(c.kind, exp.DefaultColumnConstraint):
                        has_default = True
                    elif isinstance(c.kind, exp.NotNullColumnConstraint):
                        has_not_null = True
    elif re.search(r"\bADD\s+COLUMN\b", raw_upper) or re.search(r"\bADD\s+(?!CONSTRAINT\b|KEY\b|INDEX\b)[a-zA-Z0-9_]+\s+[A-Za-z]", raw_upper):
        has_add_column = True

    if not has_add_column:
        return None

    if "DEFAULT" in raw_upper:
        has_default = True
    if "NOT NULL" in raw_upper:
        has_not_null = True

    if has_default or has_not_null:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.HIGH,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=True,
            requires_exclusive_lock=True,
            issue="Adding a column with a DEFAULT expression or NOT NULL constraint requires an ACCESS EXCLUSIVE lock and can trigger a full table rewrite on older engines or volatile expressions.",
            remediation="Add column as NULLable without default, backfill in batches, then set default and NOT NULL with VALIDATE CONSTRAINT.",
        )
    else:
        # Nullable column without default
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.LOW,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=True,
            issue="Adding a nullable column without default is a fast metadata update in modern Postgres, but acquires a brief ACCESS EXCLUSIVE lock.",
            remediation="Set a strict lock_timeout before running the DDL statement.",
        )


def check_column_type_change(node: exp.Expression, sql_text: str, dialect: str) -> Optional[AnalysisFinding]:
    """Detect column type alterations and identify full table rewrites."""
    raw_upper = sql_text.upper()
    is_type_alter = False
    action_node = None

    if isinstance(node, exp.Alter):
        for action in node.args.get("actions", []):
            if isinstance(action, exp.AlterColumn) or "TYPE" in action.sql().upper():
                is_type_alter = True
                action_node = action
                break
    elif re.search(r"\bALTER\s+COLUMN\s+.*?TYPE\b", raw_upper) or re.search(r"\bMODIFY\s+(COLUMN\s+)?", raw_upper):
        is_type_alter = True

    if not is_type_alter:
        return None

    # Exception check: VARCHAR size enlargement or change to TEXT in Postgres
    if dialect == "postgres" and action_node and _is_varchar_or_text_expansion(action_node, sql_text):
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.LOW,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=True,
            issue="Increasing VARCHAR length or converting to TEXT is a metadata-only change in Postgres, requiring only a brief ACCESS EXCLUSIVE lock.",
            remediation="Ensure lock_timeout is configured to prevent blocking in transaction queues.",
        )

    # All other type conversions cause full table rewrite
    return AnalysisFinding(
        statement=sql_text,
        risk_tier=RiskTier.HIGH,
        lock_level=LockLevel.ACCESS_EXCLUSIVE,
        causes_table_rewrite=True,
        requires_exclusive_lock=True,
        issue="Altering column data type triggers a full table rewrite while holding an ACCESS EXCLUSIVE lock, blocking all reads and writes.",
        remediation="Create new column with target type, backfill data in batches, set up trigger/dual-write, and switch over.",
    )


def check_drop_or_rename(node: exp.Expression, sql_text: str, dialect: str) -> Optional[AnalysisFinding]:
    """Detect destructive DROP or RENAME operations on columns or tables."""
    raw_upper = sql_text.upper()

    # Drop column
    is_drop_col = False
    if isinstance(node, exp.Alter):
        for action in node.args.get("actions", []):
            if isinstance(action, exp.Drop) and str(action.args.get("kind", "")).upper() == "COLUMN":
                is_drop_col = True
                break
    elif re.search(r"\bDROP\s+COLUMN\b", raw_upper):
        is_drop_col = True

    if is_drop_col:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.CRITICAL,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=True,
            issue="Directly dropping a column breaks backward compatibility with running code and prevents zero-downtime rollbacks.",
            remediation="Follow expand/contract pattern: stop referencing column in application code, deploy code, then drop/rename in a subsequent release.",
        )

    # Rename column
    is_rename_col = False
    if isinstance(node, exp.Alter):
        for action in node.args.get("actions", []):
            if isinstance(action, exp.RenameColumn) or "RENAME COLUMN" in action.sql().upper():
                is_rename_col = True
                break
    elif re.search(r"\bRENAME\s+COLUMN\b", raw_upper):
        is_rename_col = True

    if is_rename_col:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.CRITICAL,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=True,
            issue="Renaming a column immediately breaks active application queries referencing the old column name.",
            remediation="Follow expand/contract pattern: add new column, dual-write, backfill, migrate reads, and deprecate old column.",
        )

    # Drop Table
    is_drop_table = (
        (isinstance(node, exp.Drop) and str(node.args.get("kind", "")).upper() == "TABLE")
        or bool(re.match(r"^\s*DROP\s+TABLE\b", raw_upper))
    )
    if is_drop_table:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.CRITICAL,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=True,
            issue="Dropping a table destroys all stored data and holds an ACCESS EXCLUSIVE lock on the relation.",
            remediation="Ensure table is deprecated, verified unreferenced in production metrics, and backed up before dropping.",
        )

    # Truncate Table
    is_truncate = isinstance(node, exp.TruncateTable) or bool(re.match(r"^\s*TRUNCATE\b", raw_upper))
    if is_truncate:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.CRITICAL,
            lock_level=LockLevel.ACCESS_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=True,
            issue="TRUNCATE removes all rows and holds an ACCESS EXCLUSIVE lock on the table.",
            remediation="Back up table or delete rows in small batched transactions to avoid exclusive table locks.",
        )

    return None


def check_safe_operations(node: exp.Expression, sql_text: str, dialect: str) -> Optional[AnalysisFinding]:
    """Detect intrinsically safe operations (e.g., CREATE TABLE, VALIDATE CONSTRAINT)."""
    raw_upper = sql_text.upper()

    # Create Table
    is_create_table = (
        (isinstance(node, exp.Create) and str(node.args.get("kind", "")).upper() == "TABLE")
        or bool(re.match(r"^\s*CREATE\s+TABLE\b", raw_upper))
    )
    if is_create_table:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.SAFE,
            lock_level=LockLevel.NONE,
            causes_table_rewrite=False,
            requires_exclusive_lock=False,
            issue="Creating a new table does not acquire locks on existing active tables.",
            remediation="Safe: Table creation is non-blocking.",
        )

    # Validate Constraint
    if "VALIDATE CONSTRAINT" in raw_upper:
        return AnalysisFinding(
            statement=sql_text,
            risk_tier=RiskTier.LOW,
            lock_level=LockLevel.SHARE_UPDATE_EXCLUSIVE,
            causes_table_rewrite=False,
            requires_exclusive_lock=False,
            issue="Validating constraint verifies existing rows with SHARE UPDATE EXCLUSIVE lock, allowing concurrent reads and writes.",
            remediation="Safe: Constraint validation runs non-blockingly.",
        )

    return None


ALL_RULES = [
    check_index_creation,
    check_foreign_key_constraint,
    check_add_column,
    check_column_type_change,
    check_drop_or_rename,
    check_safe_operations,
]
