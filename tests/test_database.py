import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rfp import db

# the schema shipped by version 1 of the app - used to prove existing databases are upgraded in place
LEGACY_SCHEMA = """
CREATE TABLE evaluation_criteria (criterion_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '', weight REAL NOT NULL, max_score REAL NOT NULL DEFAULT 10,
    is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE rfp_runs (rfp_run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
    criteria_snapshot TEXT, run_meta_json TEXT);
CREATE TABLE supplier_results (id INTEGER PRIMARY KEY AUTOINCREMENT, rfp_run_id TEXT NOT NULL,
    supplier_name TEXT NOT NULL, submission_date TEXT NOT NULL, experience_rating REAL NOT NULL,
    absolute_score REAL, ppi REAL, final_rank INTEGER, result_json TEXT NOT NULL, UNIQUE (rfp_run_id, supplier_name));
"""


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.bootstrap(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_creates_five_criteria_totalling_100(self):
        with db.session(self.path) as conn:
            active = db.get_active_criteria(conn)
        self.assertEqual(len(active), 5)
        self.assertEqual(sum(c["weight"] for c in active), 100)
        db.bootstrap(self.path)                                     # idempotent
        with db.session(self.path) as conn:
            self.assertEqual(len(db.get_all_criteria(conn)), 5)

    def test_validate_criteria_messages(self):
        base = [{"criterion_id": 1, "name": "A", "weight": 60, "max_score": 10, "is_active": 1},
                {"criterion_id": 2, "name": "B", "weight": 40, "max_score": 10, "is_active": 1}]
        self.assertEqual(db.validate_criteria(base), [])
        self.assertTrue(any("total 100%" in e for e in db.validate_criteria([{**base[0], "weight": 50}, base[1]])))
        self.assertEqual(db.validate_criteria([{**base[0], "weight": 50}, base[1]], require_total=False), [])
        self.assertTrue(db.validate_criteria([{**b, "is_active": 0} for b in base]))
        self.assertTrue(any("unique" in e for e in db.validate_criteria([base[0], {**base[1], "name": "a"}])))
        self.assertTrue(any("max score" in e for e in db.validate_criteria([{**base[0], "max_score": 0}, base[1]])))
        self.assertTrue(any("number" in e for e in db.validate_criteria([{**base[0], "weight": "x"}, base[1]])))

    def test_update_criteria_refuses_invalid_and_applies_valid_edits(self):
        with db.session(self.path) as conn:
            rows = db.get_all_criteria(conn)
            bad = [{**r} for r in rows]
            bad[0]["weight"] = 50
            with self.assertRaises(ValueError):
                db.update_criteria(conn, bad)
            rows[4]["is_active"] = 0
            rows[0]["weight"] = 40                                   # 40+20+20+20 = 100
            rows.append({"criterion_id": float("nan"), "name": "Sustainability", "description": float("nan"),
                         "weight": 0, "max_score": 5, "is_active": False})
            db.update_criteria(conn, rows)
            names = {c["name"]: c for c in db.get_all_criteria(conn)}
        self.assertEqual(names["Support & Experience"]["is_active"], 0)
        self.assertEqual(names["Technical Capability"]["weight"], 40)
        self.assertEqual((names["Sustainability"]["description"], names["Sustainability"]["max_score"]), ("", 5))

    def test_rows_removed_from_the_editor_are_deactivated_not_deleted(self):
        with db.session(self.path) as conn:
            rows = db.get_all_criteria(conn)[:4]
            rows[3]["weight"] = 30                                   # keep the total at 100
            db.update_criteria(conn, rows)
            self.assertEqual(len(db.get_all_criteria(conn)), 5)
            self.assertEqual(len(db.get_active_criteria(conn)), 4)

    def test_reset_restores_defaults(self):
        with db.session(self.path) as conn:
            conn.execute("UPDATE evaluation_criteria SET weight = 5, is_active = 0")
            db.seed_criteria(conn, force=True)
            active = db.get_active_criteria(conn)
        self.assertEqual((len(active), sum(c["weight"] for c in active), active[0]["criterion_id"]), (5, 100, 1))

    def test_run_lifecycle_and_history(self):
        with db.session(self.path) as conn:
            db.create_run(conn, "RUN-1", "stub", "m1")
            db.add_supplier_entries(conn, "RUN-1", [{"supplier_name": "A", "source_file": "a.pdf",
                                                      "submission_date": "2026-10-01", "experience_rating": 5}])
            self.assertEqual(db.get_supplier_rows(conn, "RUN-1")[0]["status"], "PENDING")
            db.set_supplier_status(conn, "RUN-1", "A", "EVALUATING")
            db.set_run_status(conn, "RUN-1", "RUNNING", criteria_snapshot=[{"criterion_id": 1}])
            run = {"rfp_run_id": "RUN-1", "status": "COMPLETED", "criteria": [], "suppliers": [
                {"supplier_name": "A", "absolute_score": 50, "ppi": 60, "final_rank": 1}], "warnings": []}
            db.save_run_results(conn, run)
            rows = db.get_supplier_rows(conn, "RUN-1")
            listed = db.list_runs(conn)[0]
            self.assertEqual((rows[0]["status"], rows[0]["final_rank"]), ("EVALUATED", 1))
            self.assertEqual((listed["status"], listed["llm_provider"], listed["suppliers"]), ("COMPLETED", "stub", 1))
            self.assertEqual(db.load_run(conn, "RUN-1"), run)
            self.assertIsNone(db.load_run(conn, "NOPE"))
            db.create_run(conn, "RUN-2", "stub", "m1")
            db.set_run_status(conn, "RUN-2", "FAILED", error_message="boom")
            failed = [r for r in db.list_runs(conn) if r["rfp_run_id"] == "RUN-2"][0]
            self.assertEqual((failed["status"], failed["error_message"]), ("FAILED", "boom"))
            self.assertIsNone(db.load_run(conn, "RUN-2"))

    def test_databases_from_the_first_version_are_upgraded_in_place(self):
        old = Path(self.tmp.name) / "old.db"
        raw = sqlite3.connect(old)
        raw.executescript(LEGACY_SCHEMA)
        raw.execute("INSERT INTO evaluation_criteria (name, weight) VALUES ('Only', 100)")
        raw.execute("INSERT INTO rfp_runs VALUES ('OLD-1', '2026-10-01T00:00:00+00:00', 'COMPLETED', '[]', ?)",
                    (json.dumps({"warnings": ["w"]}),))
        raw.execute("INSERT INTO supplier_results (rfp_run_id, supplier_name, submission_date, experience_rating, "
                    "absolute_score, ppi, final_rank, result_json) VALUES ('OLD-1', 'S', '2026-10-01', 5, 50, 60, 1, ?)",
                    (json.dumps({"supplier_name": "S", "final_rank": 1, "absolute_score": 50, "ppi": 60,
                                 "submission_date": "2026-10-01", "experience_rating": 5}),))
        raw.commit()
        raw.close()
        with db.session(old) as conn:
            db.init_db(conn)
            db.init_db(conn)                                         # running twice must be harmless
            self.assertEqual(db.get_supplier_rows(conn, "OLD-1")[0]["status"], "EVALUATED")
            legacy = db.load_run(conn, "OLD-1")
            self.assertEqual((legacy["warnings"], legacy["leaderboard"][0]["supplier_name"]), (["w"], "S"))
            db.create_run(conn, "NEW-1", "stub", "m")                # new columns are usable
            self.assertEqual(db.list_runs(conn)[0]["llm_provider"], "stub")


if __name__ == "__main__":
    unittest.main()
