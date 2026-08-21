"""Stdlib tests for M1 SQL control checks. No third-party imports."""

import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from uar_pipeline.checks import apply_checks, main as checks_main
from uar_pipeline.ingest import ingest_all, load_schema, main as ingest_main

FX = Path(__file__).resolve().parents[1] / "fixtures"
VIEWS = (
    "resolved_accounts", "check_reconciliation", "check_completeness",
    "exceptions", "check_summary",
)


def _run_checks(db_path):
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = checks_main(["--db", str(db_path)])
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code, out.getvalue(), err.getvalue()


class CheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(":memory:")
        load_schema(cls.conn)
        ingest_all(cls.conn, FX)
        apply_checks(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_views_and_acceptance_rows(self):
        names = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            )
        }
        for view in VIEWS:
            self.assertIn(view, names, msg=view)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(exceptions)")]
        self.assertEqual(
            cols,
            ["check_name", "exception_type", "source_system", "record_id", "detail"],
        )
        self.assertEqual(
            self.conn.execute("SELECT record_id FROM check_reconciliation").fetchall(),
            [],
        )
        ids = [r[0] for r in self.conn.execute(
            "SELECT record_id FROM check_completeness"
        )]
        self.assertEqual(ids, ["U025"])

    def test_reconciliation_fires_on_row_count_mismatch(self):
        # Fresh connection: this test corrupts the bookkeeping on purpose,
        # so it must not touch the shared class-level database.
        conn = sqlite3.connect(":memory:")
        try:
            load_schema(conn)
            ingest_all(conn, FX)
            conn.execute(
                "UPDATE ingest_counts SET rows_staged = rows_staged - 1 "
                "WHERE source_file = 'hr_roster.csv'"
            )
            apply_checks(conn)
            ids = [r[0] for r in conn.execute(
                "SELECT record_id FROM check_reconciliation"
            )]
            self.assertEqual(ids, ["hr_roster.csv"])
        finally:
            conn.close()

    def test_cli_prints_counts_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "uar.db"
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                ingest_code = ingest_main(["--fixtures", str(FX), "--db", str(db_path)])
            finally:
                sys.stdout = old
            self.assertEqual(ingest_code, 0)
            code, stdout, err = _run_checks(db_path)
            self.assertEqual((code, err), (0, ""))
            counts, total = {}, None
            for line in stdout.splitlines():
                name, rest = line.split(" ", 1)
                n = int(rest.split("=", 1)[1])
                if name == "total":
                    total = n
                else:
                    counts[name] = n
            self.assertEqual(counts["check_reconciliation"], 0)
            self.assertEqual(counts["check_completeness"], 1)
            self.assertEqual(total, 1)
            self.assertEqual(set(counts), {"check_reconciliation", "check_completeness"})
            # Re-running against the same db must work (views are dropped
            # and recreated, not created blind).
            code2, _stdout2, err2 = _run_checks(db_path)
            self.assertEqual((code2, err2), (0, ""))

    def test_bad_db_path_exits_2_names_path(self):
        missing = Path(tempfile.gettempdir()) / "uar-missing-no-such.db"
        self.assertFalse(missing.exists())
        code, _out, err = _run_checks(missing)
        self.assertEqual(code, 2)
        self.assertIn(str(missing), err)
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "not-a-db.txt"
            junk.write_text("hello", encoding="utf-8")
            code, _out, err = _run_checks(junk)
            self.assertEqual(code, 2)
            self.assertIn(str(junk), err)


if __name__ == "__main__":
    unittest.main()
