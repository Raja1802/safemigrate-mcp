"""Rule-based AST transformations and zero-downtime SQL rewrites."""

import re
from typing import Optional
import sqlglot
from sqlglot import exp


class RewriteResult:
    """Result of rewriting a SQL migration statement."""

    def __init__(self, original_sql: str, rewritten_sql: str, description: str):
        self.original_sql = original_sql
        self.rewritten_sql = rewritten_sql
        self.description = description


def rewrite_index_creation(node: exp.Expression, sql_text: str, dialect: str) -> Optional[RewriteResult]:
    """Rewrite non-concurrent index creation to use CONCURRENTLY."""
    raw_upper = sql_text.upper()
    is_create_index = (
        (isinstance(node, exp.Create) and str(node.args.get("kind", "")).upper() == "INDEX")
        or isinstance(node, exp.Index)
        or bool(re.match(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX", sql_text, re.IGNORECASE))
    )
    if not is_create_index or dialect != "postgres":
        return None

    if "CONCURRENTLY" in raw_upper or bool(node.args.get("concurrently")):
        return None

    # Replace "CREATE INDEX" with "CREATE INDEX CONCURRENTLY"
    # or "CREATE UNIQUE INDEX" with "CREATE UNIQUE INDEX CONCURRENTLY"
    if re.search(r"\bCREATE\s+UNIQUE\s+INDEX\b", sql_text, re.IGNORECASE):
        rewritten = re.sub(
            r"\bCREATE\s+UNIQUE\s+INDEX\b",
            "CREATE UNIQUE INDEX CONCURRENTLY",
            sql_text,
            flags=re.IGNORECASE,
        )
    else:
        rewritten = re.sub(
            r"\bCREATE\s+INDEX\b",
            "CREATE INDEX CONCURRENTLY",
            sql_text,
            flags=re.IGNORECASE,
        )

    return RewriteResult(
        original_sql=sql_text,
        rewritten_sql=rewritten.strip(),
        description="Added CONCURRENTLY keyword to build index without blocking concurrent writes.",
    )


def rewrite_foreign_key_constraint(node: exp.Expression, sql_text: str, dialect: str) -> Optional[RewriteResult]:
    """Rewrite foreign key addition to a two-step NOT VALID and VALIDATE CONSTRAINT workflow."""
    raw_upper = sql_text.upper()
    if dialect != "postgres":
        return None

    has_fk = (
        ("FOREIGN KEY" in raw_upper and "ADD" in raw_upper)
        or (isinstance(node, exp.Alter) and any(
            a.find(exp.ForeignKey) or a.find(exp.Reference) or "FOREIGN KEY" in a.sql().upper()
            for a in node.args.get("actions", [])
        ))
    )
    if not has_fk or "NOT VALID" in raw_upper:
        return None

    # Extract table name and constraint name
    table_name = None
    constraint_name = None
    if isinstance(node, exp.Alter) and node.this:
        table_name = node.this.name
        for action in node.args.get("actions", []):
            c = action.find(exp.Constraint)
            if c and c.this:
                constraint_name = c.this.name
                break

    if not table_name:
        match_table = re.search(r"\bALTER\s+TABLE\s+([^\s;]+)", sql_text, re.IGNORECASE)
        if match_table:
            table_name = match_table.group(1).strip()

    if not constraint_name:
        match_c = re.search(r"\bCONSTRAINT\s+([^\s;]+)", sql_text, re.IGNORECASE)
        if match_c:
            constraint_name = match_c.group(1).strip()

    # Step 1: Append NOT VALID
    clean_sql = sql_text.strip().rstrip(";")
    step1 = f"-- Step 1: Add without validation (fast lock release)\n{clean_sql} NOT VALID;"

    # Step 2: Validate constraint in separate statement
    c_target = constraint_name if constraint_name else "/* constraint_name */"
    t_target = table_name if table_name else "/* table_name */"
    step2 = f"-- Step 2: Validate concurrently without blocking reads/writes\nALTER TABLE {t_target} VALIDATE CONSTRAINT {c_target};"

    rewritten = f"{step1}\n\n{step2}"
    return RewriteResult(
        original_sql=sql_text,
        rewritten_sql=rewritten,
        description="Decomposed foreign key creation into immediate NOT VALID constraint addition followed by non-blocking VALIDATE CONSTRAINT.",
    )


def rewrite_add_column_with_default(node: exp.Expression, sql_text: str, dialect: str) -> Optional[RewriteResult]:
    """Rewrite ADD COLUMN with DEFAULT and/or NOT NULL into a multi-step safe migration."""
    raw_upper = sql_text.upper()
    if dialect != "postgres":
        return None

    if not ("ADD" in raw_upper and ("DEFAULT" in raw_upper or "NOT NULL" in raw_upper)):
        return None

    table_name = None
    col_name = None
    col_type = None
    default_val = None
    has_not_null = "NOT NULL" in raw_upper

    if isinstance(node, exp.Alter) and node.this:
        table_name = node.this.name
        for action in node.args.get("actions", []):
            if isinstance(action, exp.ColumnDef):
                col_name = action.this.name if action.this else None
                if action.args.get("kind"):
                    col_type = action.args["kind"].sql(dialect="postgres")
                for c in action.args.get("constraints", []):
                    if isinstance(c.kind, exp.DefaultColumnConstraint) and c.kind.this:
                        default_val = c.kind.this.sql(dialect="postgres")

    # Fallback to regex if AST extraction missed tokens
    if not table_name:
        m = re.search(r"\bALTER\s+TABLE\s+([^\s;]+)", sql_text, re.IGNORECASE)
        if m:
            table_name = m.group(1).strip()

    if not col_name or not col_type:
        m = re.search(r"\bADD\s+(?:COLUMN\s+)?([^\s;]+)\s+([A-Za-z0-9_]+(?:\([^\)]+\))?)", sql_text, re.IGNORECASE)
        if m:
            col_name = m.group(1).strip()
            col_type = m.group(2).strip()

    if not default_val:
        m = re.search(r"\bDEFAULT\s+([^\s,;]+)", sql_text, re.IGNORECASE)
        if m:
            default_val = m.group(1).strip()

    t = table_name or "tbl"
    c = col_name or "col"
    ct = col_type or "INT"
    dv = default_val if default_val is not None else "0"

    steps = [
        f"-- Step 1: Add column nullable without default (metadata-only update)",
        f"ALTER TABLE {t} ADD COLUMN {c} {ct};",
        f"\n-- Step 2: Set default for future inserts (metadata-only)",
        f"ALTER TABLE {t} ALTER COLUMN {c} SET DEFAULT {dv};",
        f"\n-- Step 3: Backfill existing rows asynchronously in small batches",
        f"-- (e.g., UPDATE {t} SET {c} = {dv} WHERE {c} IS NULL;)",
    ]

    if has_not_null:
        steps.extend([
            f"\n-- Step 4: Add NOT NULL check constraint as NOT VALID, then validate",
            f"ALTER TABLE {t} ADD CONSTRAINT {t}_{c}_not_null CHECK ({c} IS NOT NULL) NOT VALID;",
            f"ALTER TABLE {t} VALIDATE CONSTRAINT {t}_{c}_not_null;",
        ])

    rewritten = "\n".join(steps)
    return RewriteResult(
        original_sql=sql_text,
        rewritten_sql=rewritten,
        description="Expanded ADD COLUMN with DEFAULT/NOT NULL into nullable column creation, default definition, asynchronous backfill, and deferred constraint validation.",
    )


def rewrite_migration_sql(sql_content: str, dialect: str = "postgres") -> tuple[str, list[str]]:
    """Parse migration SQL and rewrite unsafe operations into zero-downtime equivalents.

    Returns:
        tuple[str, list[str]]: (rewritten_sql, list_of_changes_made)
    """
    try:
        statements = sqlglot.parse(sql_content, read=dialect)
    except Exception:
        statements = []

    # If parsing failed or returned no statements, split on semicolon
    if not statements:
        raw_stmts = [s.strip() for s in sql_content.split(";") if s.strip()]
    else:
        raw_stmts = [s.sql(dialect=dialect) for s in statements]

    rewritten_blocks = []
    changes_made = []

    for stmt_str in raw_stmts:
        if not stmt_str.strip():
            continue

        try:
            node = sqlglot.parse_one(stmt_str, read=dialect)
        except Exception:
            node = exp.Expression()

        result = None
        # Try rewrite strategies in order
        for rewriter_func in [
            rewrite_index_creation,
            rewrite_foreign_key_constraint,
            rewrite_add_column_with_default,
        ]:
            result = rewriter_func(node, stmt_str, dialect)
            if result:
                break

        if result:
            rewritten_blocks.append(result.rewritten_sql)
            changes_made.append(result.description)
        else:
            # Check if statement had a terminating semicolon
            cleaned = stmt_str.strip()
            if not cleaned.endswith(";"):
                cleaned += ";"
            rewritten_blocks.append(cleaned)

    full_rewritten_sql = "\n\n".join(rewritten_blocks)
    return full_rewritten_sql, changes_made
