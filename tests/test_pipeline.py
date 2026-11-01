"""End-to-end tests with the offline scorer: no network, fully deterministic."""
import copy
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from rfp import db, llm, ranking
from rfp.evaluation_agent import EvaluationAgent
from rfp.orchestrator import (InputValidationError, Orchestrator, RunFailedError, SupplierInput,
                              validate_inputs)
from rfp.samples import sample_inputs
from tests.helpers import StubLLM


def strip_volatile(run):
    run = copy.deepcopy(run)
    for key in ("rfp_run_id", "created_at", "trace"):
        run.pop(key)
    for s in run["suppliers"]:
        s["llm_call"].pop("elapsed_s")
    return run


class PipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        db.bootstrap(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, client, inputs=None, retries=0):
        with db.session(self.path) as conn:
            return Orchestrator(conn, EvaluationAgent(client, max_retries=retries, backoff_s=0)).run(
                inputs or sample_inputs())


class SuccessfulRunTests(PipelineBase):
    def test_full_run_ranks_persists_and_reloads(self):
        run = self.run_with(llm.MockClient())
        self.assertEqual(run["status"], "COMPLETED")
        self.assertEqual([s["final_rank"] for s in run["suppliers"]], [1, 2, 3, 4, 5])
        self.assertEqual(run["suppliers"][-1]["supplier_name"], "Helix Networks")      # sparse edge case ranks last
        self.assertEqual(run["llm"]["temperature"], 0)
        with db.session(self.path) as conn:
            rows = db.get_supplier_rows(conn, run["rfp_run_id"])
            stored = db.load_run(conn, run["rfp_run_id"])
            listed = db.list_runs(conn)[0]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["status"] == "EVALUATED" for r in rows))
        self.assertEqual({r["final_rank"] for r in rows}, {1, 2, 3, 4, 5})
        self.assertEqual(stored, run)
        self.assertEqual((listed["status"], listed["llm_provider"]), ("COMPLETED", "offline-demo"))

    def test_result_json_is_complete_and_traceable(self):
        run = self.run_with(llm.MockClient())
        for key in ("formulas", "tie_break_rules", "ties_resolved", "benchmarks", "criteria", "trace", "leaderboard"):
            self.assertIn(key, run)
        top = run["suppliers"][0]
        self.assertEqual(len(top["source_document"]["sha256"]), 64)
        self.assertIn(top["source_document"]["extractor"], {"pymupdf", "pypdf"})
        self.assertTrue(top["tie_break_explanation"])
        # every score is traceable to a criterion, weight, evidence and rule
        for a in top["criteria_analysis"]:
            for key in ("name", "weight", "score", "benchmark", "gap", "relative_pct", "evidence", "justification"):
                self.assertIn(key, a)
        steps = [t["step"] for t in run["trace"]]
        for needed in ("2 Input", "3 Batch", "4 Evaluate", "5 Validate", "6-8 Score/Benchmark/Rank", "9 Persist"):
            self.assertIn(needed, steps)

    def test_same_inputs_give_same_numbers_and_order(self):
        a = self.run_with(llm.MockClient())
        b = self.run_with(llm.MockClient(), list(reversed(sample_inputs())))
        self.assertEqual(strip_volatile(a)["suppliers"], strip_volatile(b)["suppliers"])

    def test_rerank_from_stored_scorecards_reproduces_the_ranking(self):
        run = self.run_with(llm.MockClient())
        entries = [{"supplier_name": s["supplier_name"], "submission_date": s["submission_date"],
                    "experience_rating": s["experience_rating"],
                    "evaluation": {"criteria": s["criteria_analysis"], "risks": s["risks"],
                                   "overall_summary": s["overall_summary"]}} for s in run["suppliers"]]
        again, _, _ = ranking.rank_suppliers(entries, run["criteria"])
        self.assertEqual([(s["supplier_name"], s["ppi"], s["absolute_score"]) for s in again],
                         [(s["supplier_name"], s["ppi"], s["absolute_score"]) for s in run["suppliers"]])

    def test_criteria_changes_reach_the_next_run(self):
        with db.session(self.path) as conn:
            rows = db.get_all_criteria(conn)
            rows[4]["is_active"] = 0
            rows[0]["weight"] = 40
            db.update_criteria(conn, rows)
        run = self.run_with(llm.MockClient())
        self.assertEqual(len(run["criteria"]), 4)
        self.assertTrue(all(len(s["criteria_analysis"]) == 4 for s in run["suppliers"]))

    def test_weights_not_100_are_rescaled_with_a_warning(self):
        with db.session(self.path) as conn:                  # bypass the UI check, e.g. an externally edited DB
            conn.execute("UPDATE evaluation_criteria SET weight = 50 WHERE criterion_id = 1")
        run = self.run_with(llm.MockClient())
        self.assertTrue(run["weights_normalized"])
        self.assertAlmostEqual(sum(c["weight"] for c in run["criteria"]), 100.0, places=4)
        self.assertEqual(run["status"], "COMPLETED_WITH_WARNINGS")


class ValidationDemoTests(PipelineBase):
    def test_fault_injection_is_repaired_and_reported(self):
        client = llm.FaultInjectingClient(llm.MockClient(), {"NexaWorks"})
        run = self.run_with(client)
        nexa = next(s for s in run["suppliers"] if s["supplier_name"] == "NexaWorks")
        codes = {w.split(":")[0] for w in nexa["validation_warnings"]}
        self.assertTrue({"V2", "V3", "V5", "V7", "V8", "V9"} <= codes)
        self.assertTrue(all(0 <= a["score"] <= a["max_score"] for a in nexa["criteria_analysis"]))
        self.assertTrue(nexa["criteria_analysis"][-1]["imputed"])
        self.assertEqual(run["status"], "COMPLETED_WITH_WARNINGS")
        self.assertTrue(any(w.startswith("NexaWorks: V") for w in run["warnings"]))

    def test_two_unparseable_replies_become_zero_scores_not_a_crash(self):
        run = self.run_with(StubLLM("not json", "still not json"), sample_inputs()[:2])
        self.assertEqual(run["status"], "COMPLETED_WITH_WARNINGS")
        for s in run["suppliers"]:
            self.assertFalse(s["llm_output_valid_json"])
            self.assertEqual(s["absolute_score"], 0)
            self.assertTrue(s["llm_call"]["attempts"] == 2)


class FailureTests(PipelineBase):
    def test_llm_outage_marks_the_run_failed_with_the_reason(self):
        with self.assertRaises(RunFailedError) as ctx:
            self.run_with(StubLLM(ConnectionError("network down")), sample_inputs()[:2])
        self.assertIn("network down", str(ctx.exception))
        with db.session(self.path) as conn:
            run = db.list_runs(conn)[0]
        self.assertEqual((run["status"], run["rfp_run_id"]), ("FAILED", ctx.exception.run_id))
        self.assertIn("network down", run["error_message"])

    def test_invalid_criteria_fail_the_run(self):
        with db.session(self.path) as conn:
            conn.execute("UPDATE evaluation_criteria SET is_active = 0")
        with self.assertRaises(RunFailedError):
            self.run_with(llm.MockClient())

    def test_unreadable_pdf_stops_the_batch_before_anything_is_stored(self):
        inputs = sample_inputs()
        inputs[1] = SupplierInput("Broken Co", "broken.pdf", b"%PDF-1.4 garbage", date(2026, 10, 1), 5)
        with self.assertRaises(InputValidationError) as ctx:
            self.run_with(llm.MockClient(), inputs)
        self.assertIn("broken.pdf", str(ctx.exception))
        with db.session(self.path) as conn:
            self.assertEqual(db.list_runs(conn), [])


class InputValidationTests(PipelineBase):
    def test_messages(self):
        good = sample_inputs()
        self.assertEqual(validate_inputs(good), [])
        subs = [SupplierInput(s.supplier_name, s.filename, s.pdf_bytes, s.submission_date, s.experience_rating)
                for s in good[:3]]
        subs[1].supplier_name = subs[0].supplier_name.lower()
        subs[0].experience_rating = 99
        subs[2].submission_date = date.today() + timedelta(days=3)
        errors = validate_inputs(subs)
        for needle in ("Duplicate", "experience rating", "future"):
            self.assertTrue(any(needle in e for e in errors), needle)

    def test_needs_at_least_two_suppliers(self):
        self.assertTrue(any("at least 2" in e for e in validate_inputs(sample_inputs()[:1])))
        self.assertEqual(validate_inputs([]), ["Upload at least one supplier RFP PDF."])

    def test_missing_name_date_and_bad_file(self):
        bad = [SupplierInput("", "a.pdf", b"nope", None, 5), SupplierInput("B", "b.pdf", b"", date(2026, 1, 1), 5)]
        errors = validate_inputs(bad)
        self.assertGreaterEqual(len(errors), 4)
        with self.assertRaises(InputValidationError):
            self.run_with(llm.MockClient(), bad)


if __name__ == "__main__":
    unittest.main()
