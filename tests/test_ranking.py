import random
import unittest

from rfp import ranking
from tests.helpers import CRITERIA, entry


def by_name(ranked):
    return {s["supplier_name"]: s for s in ranked}


class FormulaTests(unittest.TestCase):
    def test_formulas_hand_calculated(self):
        ranked, bench, _ = ranking.rank_suppliers([entry("A", [8, 5]), entry("B", [6, 10])], CRITERIA)
        s = by_name(ranked)
        self.assertEqual({b["name"]: b["benchmark_score"] for b in bench}, {"Technical": 8, "Commercial": 10})
        self.assertAlmostEqual(s["A"]["absolute_score"], 68.0)      # .8*60 + .5*40
        self.assertAlmostEqual(s["B"]["absolute_score"], 76.0)      # .6*60 + 1*40
        self.assertAlmostEqual(s["A"]["ppi"], 80.0)                 # (100*60 + 50*40)/100
        self.assertAlmostEqual(s["B"]["ppi"], 85.0)                 # (75*60 + 100*40)/100
        self.assertEqual([x["supplier_name"] for x in ranked], ["B", "A"])
        gaps = {a["name"]: a["gap"] for a in s["B"]["criteria_analysis"]}
        self.assertEqual(gaps, {"Technical": -2.0, "Commercial": 0.0})

    def test_gaps_never_positive_and_leaders_flagged(self):
        ranked, bench, _ = ranking.rank_suppliers(
            [entry("A", [8, 6]), entry("B", [4, 8]), entry("C", [1, 2])], CRITERIA)
        self.assertTrue(all(a["gap"] <= 0 for s in ranked for a in s["criteria_analysis"]))
        leaders = {b["name"]: b["leaders"] for b in bench}
        self.assertEqual(leaders, {"Technical": ["A"], "Commercial": ["B"]})
        self.assertTrue(by_name(ranked)["A"]["criteria_analysis"][0]["is_benchmark_leader"])
        self.assertFalse(by_name(ranked)["C"]["criteria_analysis"][0]["is_benchmark_leader"])

    def test_zero_benchmark_is_safe(self):
        ranked, bench, _ = ranking.rank_suppliers([entry("A", [0, 5]), entry("B", [0, 5])], CRITERIA)
        self.assertTrue(bench[0]["benchmark_zero"])
        self.assertEqual(ranked[0]["ppi"], 100.0)
        self.assertFalse(ranked[0]["criteria_analysis"][0]["is_benchmark_leader"])

    def test_invalid_scores_never_set_the_benchmark(self):
        _, bench, _ = ranking.rank_suppliers(
            [entry("A", [0, 5], valid=[False, True]), entry("B", [4, 5])], CRITERIA)
        self.assertEqual(bench[0]["benchmark_score"], 4)

    def test_duplicate_names_rejected(self):
        with self.assertRaises(ValueError):
            ranking.rank_suppliers([entry("A", [1, 1]), entry("a", [2, 2])], CRITERIA)

    def test_weight_normalization(self):
        scaled, changed = ranking.normalize_weights([{**CRITERIA[0], "weight": 3}, {**CRITERIA[1], "weight": 1}])
        self.assertTrue(changed)
        self.assertAlmostEqual(scaled[0]["weight"], 75.0)
        self.assertFalse(ranking.normalize_weights(CRITERIA)[1])
        with self.assertRaises(ValueError):
            ranking.normalize_weights([{**CRITERIA[0], "weight": 0}])


class TieBreakTests(unittest.TestCase):
    def test_each_rule_in_order_with_explanations(self):
        e = [entry("Zed", [5, 5], "2026-10-12", 9), entry("Bob", [5, 5], "2026-10-10", 1),
             entry("Amy", [5, 5], "2026-10-10", 7), entry("Cat", [5, 5], "2026-10-10", 7)]
        ranked, _, ties = ranking.rank_suppliers(e, CRITERIA)
        self.assertEqual([s["supplier_name"] for s in ranked], ["Amy", "Cat", "Bob", "Zed"])
        self.assertEqual([s["final_rank"] for s in ranked], [1, 2, 3, 4])
        self.assertEqual([d["rule_number"] for d in ties["decisions"]], [4, 3, 2])
        self.assertEqual(len(ties["ties_resolved"]), 3)
        self.assertIn("Rule 4", ranked[1]["tie_break_explanation"])
        self.assertIn("Rule 2", ranked[3]["tie_break_explanation"])

    def test_ppi_beats_every_other_rule(self):
        ranked, _, ties = ranking.rank_suppliers(
            [entry("Early", [5, 5], "2020-01-01", 10), entry("Better", [9, 9], "2030-01-01", 0)], CRITERIA)
        self.assertEqual(ranked[0]["supplier_name"], "Better")
        self.assertFalse(ties["ties_found"])
        self.assertIn("Rule 1", ranked[1]["tie_break_explanation"])

    def test_single_supplier_explanation(self):
        ranked, _, _ = ranking.rank_suppliers([entry("Solo", [5, 5])], CRITERIA)
        self.assertIn("only supplier", ranked[0]["tie_break_explanation"])

    def test_order_independent_over_many_shuffles(self):
        base = [entry("A", [8, 6], "2026-10-11", 4), entry("B", [8, 6], "2026-10-11", 4),
                entry("C", [9, 2], "2026-10-09", 8), entry("D", [7, 7], "2026-09-01"), entry("E", [7, 7], "2026-09-01")]
        expected = ranking.rank_suppliers(list(base), CRITERIA)
        for seed in range(20):
            shuffled = base[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(ranking.rank_suppliers(shuffled, CRITERIA), expected)


if __name__ == "__main__":
    unittest.main()
