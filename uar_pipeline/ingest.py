"""Stage fixture CSVs into SQLite. Completeness is extracted = staged + rejected."""

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HR_COLS = [
    "employee_id", "full_name", "email", "department",
    "manager_employee_id", "status", "hire_date", "termination_date",
]
IDP_USER_COLS = [
    "idp_user_id", "upn", "display_name", "enabled",
    "last_login_date", "employee_id",
]
APP_COLS = [
    "app_name", "app_account_id", "entitlement_name", "account_enabled",
    "last_activity_date", "identifier_kind", "identifier_value",
]
SOURCES: dict[str, tuple[str, list[str]]] = {
    "hr_roster.csv": ("hr_roster", HR_COLS),
    "idp_users.csv": ("idp_users", IDP_USER_COLS),
    "idp_groups.csv": (
        "idp_groups", ["group_id", "group_name", "owner_employee_id", "privileged"],
    ),
    "idp_group_members.csv": (
        "idp_group_members", ["group_id", "member_type", "member_id"],
    ),
    "app_entitlements_salesforce.csv": ("app_entitlements", APP_COLS),
    "app_entitlements_github.csv": ("app_entitlements", APP_COLS),
}
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def load_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_row(row: dict[str, str], columns: list[str]) -> str | None:
    """Return a rejection reason, or None if the row is stageable."""
    if len(row) != len(columns):
        return f"wrong field count: expected {len(columns)} columns, got {len(row)}"
    pk = columns[0]
    if not row.get(pk, "").strip():
        return f"blank primary key: {pk} is empty"
    for name in columns:
        value = row[name]
        if name.endswith("_date") and value.strip():
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                return f"unparseable date: {name}={value!r}"
            if parsed.strftime("%Y-%m-%d") != value:
                return f"unparseable date: {name}={value!r}"
    return None


def check_structural(fixtures_dir: Path) -> str | None:
    """Missing files and header mismatches kill the run. Bad rows do not."""
    for filename, (_table, columns) in SOURCES.items():
        path = fixtures_dir / filename
        if not path.is_file():
            return f"missing fixture file: {path}"
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                found = next(csv.reader(handle))
        except OSError as exc:
            return f"unreadable fixture file: {path} ({exc})"
        except StopIteration:
            found = []
        if found != columns:
            return (
                f"header does not match in {filename}: "
                f"expected {columns}, found {found}"
            )
    return None


def stage_source(
    conn: sqlite3.Connection, path: Path, table: str, columns: list[str]
) -> dict[str, int]:
    extracted = staged = rejected = 0
    placeholders = ", ".join("?" * len(columns))
    col_sql = ", ".join(columns)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for line_no, values in enumerate(reader, start=2):
            extracted += 1
            # Field count is on the CSV list; zip() would hide extras.
            if len(values) != len(columns):
                reason = (
                    f"wrong field count: expected {len(columns)} columns, "
                    f"got {len(values)}"
                )
                raw = json.dumps(values)
            else:
                row = dict(zip(columns, values))
                reason = validate_row(row, columns)
                raw = json.dumps(row)
            if reason:
                conn.execute(
                    "INSERT INTO staging_rejects "
                    "(source_file, line_no, reason, raw_row) VALUES (?, ?, ?, ?)",
                    (path.name, line_no, reason, raw),
                )
                rejected += 1
                continue
            try:
                conn.execute(
                    f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
                    values,
                )
            except sqlite3.IntegrityError:
                pk = columns[0]
                pk_value = values[0] if values else ""
                conn.execute(
                    "INSERT INTO staging_rejects "
                    "(source_file, line_no, reason, raw_row) VALUES (?, ?, ?, ?)",
                    (
                        path.name,
                        line_no,
                        f"duplicate primary key: {pk}={pk_value!r}",
                        raw,
                    ),
                )
                rejected += 1
            else:
                staged += 1
    return {"extracted": extracted, "staged": staged, "rejected": rejected}


def ingest_all(conn: sqlite3.Connection, fixtures_dir: Path) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for filename, (table, columns) in SOURCES.items():
        path = fixtures_dir / filename
        counts = stage_source(conn, path, table, columns)
        digest = hash_file(path)
        results[filename] = {
            "extracted": counts["extracted"],
            "staged": counts["staged"],
            "rejected": counts["rejected"],
            "sha256": digest,
        }
        conn.execute(
            "INSERT INTO ingest_counts "
            "(source_file, rows_extracted, rows_staged, rows_rejected, sha256) "
            "VALUES (?, ?, ?, ?, ?)",
            (filename, counts["extracted"], counts["staged"],
             counts["rejected"], digest),
        )
    conn.commit()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage synthetic UAR fixture CSVs into SQLite."
    )
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args(argv)
    err = check_structural(args.fixtures)
    if err:
        print(err, file=sys.stderr)
        return 2
    db_path: Path = args.db
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        load_schema(conn)
        results = ingest_all(conn, args.fixtures)
    finally:
        conn.close()
    for filename, counts in results.items():
        print(
            f"{filename} extracted={counts['extracted']} "
            f"staged={counts['staged']} rejected={counts['rejected']} "
            f"sha256={counts['sha256']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
