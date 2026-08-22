"""Write the auditor evidence packet from fixtures. SQL decides; this writes files."""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from uar_pipeline.checks import apply_checks
from uar_pipeline.ingest import check_structural, ingest_all, load_schema

AS_OF_DATE = "2026-06-30"


def dump_csv(conn: sqlite3.Connection, path: Path, sql: str) -> int:
    cur = conn.execute(sql)
    rows = cur.fetchall()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([col[0] for col in cur.description])
        writer.writerows(rows)
    return len(rows)


def write_summary(path: Path, sources: dict[str, dict], conn: sqlite3.Connection) -> int:
    recon = conn.execute("SELECT count(*) FROM check_reconciliation").fetchone()[0]
    by_check = {
        name: n for name, n in conn.execute(
            "SELECT check_name, exception_count FROM check_summary "
            "ORDER BY check_name"
        )
    }
    total = sum(by_check.values())
    payload = {
        "as_of_date": AS_OF_DATE,
        "sources": sources,
        "reconciliation": "PASS" if recon == 0 else "FAIL",
        "population_rows": conn.execute("SELECT count(*) FROM population").fetchone()[0],
        "exceptions_by_check": by_check,
        "total_exceptions": total,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run UAR ingest + checks and write the evidence packet."
    )
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    err = check_structural(args.fixtures)
    if err:
        print(err, file=sys.stderr)
        return 2
    conn = sqlite3.connect(":memory:")
    try:
        load_schema(conn)
        sources = ingest_all(conn, args.fixtures)
        apply_checks(conn)
        args.out.mkdir(parents=True, exist_ok=True)
        pop_n = dump_csv(
            conn, args.out / "population.csv",
            "SELECT * FROM population ORDER BY source_system, account_id",
        )
        exc_n = dump_csv(
            conn, args.out / "exceptions.csv",
            "SELECT * FROM exceptions "
            "ORDER BY check_name, source_system, record_id, detail",
        )
        total = write_summary(args.out / "summary.json", sources, conn)
    finally:
        conn.close()
    print(f"wrote {args.out / 'population.csv'} ({pop_n} rows)")
    print(f"wrote {args.out / 'exceptions.csv'} ({exc_n} rows)")
    print(f"wrote {args.out / 'summary.json'}")
    print(f"total exceptions={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
