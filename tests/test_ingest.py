"""Stdlib tests for fixture ingest. No third-party imports."""

import hashlib
import shutil
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from uar_pipeline.ingest import (
    SOURCES, check_structural, hash_file, ingest_all, load_schema, main, validate_row,
)

FX = Path(__file__).resolve().parents[1] / "fixtures"
HR = ["employee_id", "hire_date", "termination_date"]


def _ingest(d: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    load_schema(conn)
    ingest_all(conn, d)
    return conn


def _headers(d: Path) -> None:
    for name, (_t, cols) in SOURCES.items():
        (d / name).write_text(",".join(cols) + "\n", encoding="utf-8")


class IngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = _ingest(FX)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_validate_row_rules(self):
        self.assertIsNone(validate_row(
            {"employee_id": "E001", "hire_date": "2020-01-01", "termination_date": ""}, HR
        ))
        self.assertIn("employee_id", validate_row(
            {"employee_id": "  ", "hire_date": "", "termination_date": ""}, HR
        ))
        reason = validate_row(
            {"employee_id": "E025", "hire_date": "2024-11-01", "termination_date": "2026-13-01"},
            HR,
        )
        self.assertIn("termination_date", reason)
        self.assertIn("2026-13-01", reason)
        short = validate_row(
            {"employee_id": "E012", "hire_date": "2020-05-22", "termination_date": "2025-5-20"},
            HR,
        )
        self.assertIn("termination_date", short)
        self.assertIn("2025-5-20", short)

    def test_extracted_equals_staged_plus_rejected(self):
        rows = self.conn.execute(
            "SELECT source_file, rows_extracted, rows_staged, rows_rejected FROM ingest_counts"
        ).fetchall()
        expected_extracted = {
            "hr_roster.csv": 25,
            "idp_users.csv": 25,
            "idp_groups.csv": 8,
            "idp_group_members.csv": 40,
            "app_entitlements_salesforce.csv": 16,
            "app_entitlements_github.csv": 12,
        }
        self.assertEqual(len(rows), 6)
        for name, extracted, staged, rejected in rows:
            self.assertEqual(extracted, expected_extracted[name], msg=name)
            self.assertEqual(extracted, staged + rejected, msg=name)

    def test_table_counts_match_staged(self):
        expected = {}
        for filename, (table, _cols) in SOURCES.items():
            n = self.conn.execute(
                "SELECT rows_staged FROM ingest_counts WHERE source_file = ?", (filename,)
            ).fetchone()[0]
            expected[table] = expected.get(table, 0) + n
        for table, n in expected.items():
            got = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertGreater(got, 0, msg=table)
            self.assertEqual(got, n, msg=table)

    def test_one_reject_names_date(self):
        rows = self.conn.execute(
            "SELECT source_file, line_no, reason FROM staging_rejects"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:2], ("hr_roster.csv", 26))
        self.assertIn("termination_date", rows[0][2])
        self.assertIn("2026-13-01", rows[0][2])

    def test_sha256_matches_disk(self):
        for source_file, digest in self.conn.execute(
            "SELECT source_file, sha256 FROM ingest_counts"
        ):
            disk = hashlib.sha256((FX / source_file).read_bytes()).hexdigest()
            self.assertEqual(digest, disk)
            self.assertEqual(digest, hash_file(FX / source_file))

    def test_nested_group_chain(self):
        edges = set(self.conn.execute(
            "SELECT group_id, member_type, member_id FROM idp_group_members"
        ))
        self.assertIn(("grp-app-admins", "group", "grp-finance-analysts"), edges)
        self.assertIn(("grp-finance-analysts", "group", "grp-finance-all"), edges)
        self.assertIn(("grp-finance-all", "user", "U007"), edges)

    def _run_main(self, fixtures: Path, db_path: Path) -> tuple[int, str]:
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            code = main(["--fixtures", str(fixtures), "--db", str(db_path)])
        finally:
            sys.stderr = old
        return code, buf.getvalue()

    def test_missing_file_exits_2_without_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = Path(tmp) / "fx"
            fixtures.mkdir()
            _headers(fixtures)
            (fixtures / "idp_users.csv").unlink()
            db_path = Path(tmp) / "uar.db"
            code, err = self._run_main(fixtures, db_path)
            self.assertEqual(code, 2)
            self.assertIn("idp_users.csv", err)
            self.assertFalse(db_path.exists())

    def test_header_mismatch_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _headers(d)
            (d / "hr_roster.csv").write_text("nope,wrong\n", encoding="utf-8")
            err = check_structural(d)
            self.assertIn("hr_roster.csv", err)
            self.assertIn("expected", err)
            self.assertIn("found", err)
            db_path = d / "out.db"
            self.assertEqual(self._run_main(d, db_path)[0], 2)
            self.assertFalse(db_path.exists())

    def test_empty_extract_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _headers(d)
            conn = _ingest(d)
            for row in conn.execute(
                "SELECT rows_extracted, rows_staged, rows_rejected FROM ingest_counts"
            ):
                self.assertEqual(row, (0, 0, 0))
            conn.close()

    def test_duplicate_pk_rejects_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "fx"
            shutil.copytree(FX, d)
            with (d / "hr_roster.csv").open("a", encoding="utf-8") as handle:
                handle.write(
                    "E010,Dup Row,dup@example.com,Engineering,E005,active,2021-07-19,\n"
                )
            db_path = Path(tmp) / "uar.db"
            code, _err = self._run_main(d, db_path)
            self.assertEqual(code, 0)
            conn = sqlite3.connect(db_path)
            reasons = [
                row[0]
                for row in conn.execute("SELECT reason FROM staging_rejects")
            ]
            conn.close()
            hits = [r for r in reasons if "duplicate primary key" in r]
            self.assertEqual(len(hits), 1)
            self.assertIn("employee_id", hits[0])
            self.assertIn("E010", hits[0])


if __name__ == "__main__":
    unittest.main()
