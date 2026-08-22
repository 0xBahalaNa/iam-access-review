"""Stdlib tests for the evidence-packet CLI. No third-party imports."""

import csv
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from uar_pipeline.__main__ import main
from uar_pipeline.checks import apply_checks
from uar_pipeline.ingest import SOURCES, hash_file, ingest_all, load_schema

FX = Path(__file__).resolve().parents[1] / "fixtures"
FILES = ("exceptions.csv", "population.csv", "summary.json")
CHECKS = [
    "check_completeness", "check_direct_assignment", "check_dormant",
    "check_nested_privileged_reach", "check_orphaned_accounts",
    "check_ownerless_groups", "check_reconciliation", "check_terminated_active",
]


def _run(fixtures, out):
    stdout, stderr = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, stderr
    try:
        code = main(["--fixtures", str(fixtures), "--out", str(out)])
    finally:
        sys.stdout, sys.stderr = old
    return code, stdout.getvalue(), stderr.getvalue()


class PacketTests(unittest.TestCase):
    def test_packet_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "evidence"
            code, stdout, err = _run(FX, out)
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(sorted(p.name for p in out.iterdir()), list(FILES))
            with (out / "population.csv").open(newline="", encoding="utf-8") as fh:
                pop = list(csv.DictReader(fh))
            u008 = next(r for r in pop if r["account_id"] == "U008"
                        and r["source_system"] == "idp_users")
            self.assertEqual(
                u008["review_status"],
                "check_nested_privileged_reach,check_terminated_active",
            )
            self.assertTrue(any(r["review_status"] == "clear" for r in pop))
            self.assertEqual(
                next(r["idp_user_id"] for r in pop if r["account_id"] == "sf-016"), ""
            )
            self.assertEqual(len(pop), 53)
            conn = sqlite3.connect(":memory:")
            load_schema(conn)
            ingest_all(conn, FX)
            apply_checks(conn)
            expected = conn.execute(
                "SELECT * FROM exceptions "
                "ORDER BY check_name, source_system, record_id"
            ).fetchall()
            conn.close()
            with (out / "exceptions.csv").open(newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0], [
                "check_name", "exception_type", "source_system",
                "record_id", "detail",
            ])
            self.assertEqual([tuple(r) for r in rows[1:]], expected)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["as_of_date"], "2026-06-30")
            self.assertEqual(list(summary["sources"]), list(SOURCES))
            self.assertEqual(summary["reconciliation"], "PASS")
            self.assertEqual(summary["population_rows"], 53)
            self.assertEqual(list(summary["exceptions_by_check"]), CHECKS)
            self.assertEqual(summary["total_exceptions"], 15)
            self.assertNotIn("timestamp", summary)
            self.assertTrue((out / "summary.json").read_text(encoding="utf-8").endswith("\n"))
            for name, counts in summary["sources"].items():
                self.assertEqual(counts["sha256"], hash_file(FX / name))
            self.assertIn("total exceptions=15", stdout)

    def test_repeatability_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            self.assertEqual(_run(FX, a)[0], 0)
            self.assertEqual(_run(FX, b)[0], 0)
            for name in FILES:
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())

    def test_missing_file_exits_2_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = Path(tmp) / "fx"
            shutil.copytree(FX, fixtures)
            (fixtures / "idp_users.csv").unlink()
            out = Path(tmp) / "out"
            code, _out, err = _run(fixtures, out)
            self.assertEqual(code, 2)
            self.assertIn("idp_users.csv", err)
            self.assertFalse(out.exists())

    def test_population_excludes_disabled(self):
        conn = sqlite3.connect(":memory:")
        try:
            load_schema(conn)
            ingest_all(conn, FX)
            apply_checks(conn)
            conn.execute(
                "UPDATE idp_users SET enabled = '0' WHERE idp_user_id = 'U001'"
            )
            ids = [r[0] for r in conn.execute(
                "SELECT account_id FROM population WHERE source_system = 'idp_users'"
            )]
            self.assertNotIn("U001", ids)
            self.assertIn("U002", ids)
        finally:
            conn.close()

if __name__ == "__main__":
    unittest.main()
