"""Apply SQL control-check views and print exception counts."""

import argparse
import sqlite3
import sys
from pathlib import Path

CHECKS_SQL = Path(__file__).with_name("checks.sql")


def apply_checks(conn: sqlite3.Connection) -> None:
    conn.executescript(CHECKS_SQL.read_text(encoding="utf-8"))


def open_staged_db(db_path: Path) -> sqlite3.Connection:
    """Open a db ingest already wrote. Fail loud if the path is not that."""
    if not db_path.exists():
        raise FileNotFoundError(f"missing database: {db_path}")
    if not db_path.is_file():
        raise sqlite3.DatabaseError(f"not a staged database: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'ingest_counts'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise sqlite3.DatabaseError(
            f"not a staged database: {db_path}"
        ) from exc
    if row is None:
        conn.close()
        raise sqlite3.DatabaseError(f"not a staged database: {db_path}")
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply UAR SQL control checks and print exception counts."
    )
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        conn = open_staged_db(args.db)
    except (FileNotFoundError, sqlite3.DatabaseError) as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        apply_checks(conn)
        rows = conn.execute(
            "SELECT check_name, exception_count FROM check_summary"
        ).fetchall()
    except sqlite3.Error as exc:
        print(f"checks failed on {args.db}: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    total = 0
    for check_name, count in rows:
        print(f"{check_name} exceptions={count}")
        total += count
    print(f"total exceptions={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
