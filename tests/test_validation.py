import json
import unittest

from rfp import validation as v
from tests.helpers import CRITERIA


def reply(**overrides):
    data = {"supplier_name": "Acme", "risks": ["r1"], "overall_summary": "fine",
            "criteria": [{"criterion_id": c["criterion_id"], "score": 7, "max_score": c["max_score"],
                          "justification": "ok", "evidence": "quoted text"} for c in CRITERIA]}
    data.update(overrides)
    return data


def codes(card):
    return [w.split(":")[0] for w in card.warnings]


class ParsingTests(unittest.TestCase):
    def test_plain_fenced_and_chatty_json(self):
        self.assertEqual(v.parse_llm_json('{"a": 1}'), {"a": 1})
        self.assertEqual(v.parse_llm_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(v.parse_llm_json('Sure! {"a": 1} Hope that helps'), {"a": 1})

    def test_bad_output_raises(self):
        for bad in ["", None, "no json here", '{"a": ', "[1, 2]"]:
            with self.assertRaises(v.LLMOutputError):
                v.parse_llm_json(bad)

    def test_parse_score(self):
        self.assertEqual(v.parse_score("7,5"), (7.5, None))
        self.assertEqual(v.parse_score("4/5"), (4.0, 5.0))
        for bad in [None, True, "n/a", "about 8", [], float("nan"), float("inf")]:
            self.assertEqual(v.parse_score(bad), (None, None))


class RuleTests(unittest.TestCase):
    def test_clean_reply_has_no_warnings(self):
        card = v.validate_scorecard(reply(), "Acme", CRITERIA)
        self.assertEqual(card.warnings, [])
        self.assertEqual([c.score for c in card.criteria], [7, 7])
        self.assertTrue(card.llm_output_valid_json)

    def test_v1_invalid_json_imputes_everything(self):
        card = v.validate_scorecard("I think this supplier is great!", "Acme", CRITERIA)
        self.assertFalse(card.llm_output_valid_json)
        self.assertTrue(all(c.score == 0 and c.imputed and not c.valid for c in card.criteria))
        self.assertEqual(codes(card), ["V1"])
        card = v.validate_scorecard({"criteria": "oops"}, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V1"])

    def test_fenced_json_is_accepted(self):
        card = v.validate_scorecard("Here:\n```json\n" + json.dumps(reply()) + "\n```", "Acme", CRITERIA)
        self.assertEqual(card.warnings, [])

    def test_v2_name_mismatch(self):
        card = v.validate_scorecard(reply(supplier_name="ACME INC"), "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V2"])
        self.assertEqual(card.supplier_name, "Acme")

    def test_v3_unknown_and_name_matching(self):
        data = reply()
        data["criteria"][0]["criterion_id"] = 99
        data["criteria"][0]["name"] = "technical"           # matched by name
        data["criteria"].append({"criterion_id": 500, "score": 3})
        data["criteria"].append("junk")
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card).count("V3"), 3)
        self.assertEqual(card.criteria[0].score, 7)

    def test_v4_duplicate_first_wins(self):
        data = reply()
        data["criteria"].append({**data["criteria"][0], "score": 1})
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V4"])
        self.assertEqual(card.criteria[0].score, 7)

    def test_v5_non_numeric_scores(self):
        data = reply()
        data["criteria"][0]["score"] = "eight"
        data["criteria"][1]["score"] = None
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V5", "V5"])
        self.assertTrue(all(c.score == 0 and not c.valid for c in card.criteria))

    def test_v6_rescales_other_scales(self):
        data = reply()
        data["criteria"][0].update(score=4, max_score=5)
        data["criteria"][1].update(score="3/5", max_score=None)
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V6", "V6"])
        self.assertEqual([c.score for c in card.criteria], [8.0, 6.0])

    def test_v7_clipping(self):
        data = reply()
        data["criteria"][0]["score"], data["criteria"][1]["score"] = 15, -3
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V7", "V7"])
        self.assertEqual([c.score for c in card.criteria], [10.0, 0.0])

    def test_v8_missing_criterion_imputed(self):
        data = reply()
        data["criteria"].pop()
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V8"])
        self.assertTrue(card.criteria[1].imputed and not card.criteria[1].valid)

    def test_v9_missing_evidence_flagged(self):
        data = reply()
        data["criteria"][0]["evidence"] = ""
        card = v.validate_scorecard(data, "Acme", CRITERIA)
        self.assertEqual(codes(card), ["V9"])
        self.assertTrue(card.criteria[0].evidence_missing and card.criteria[0].valid)

    def test_v10_risks_and_summary_coerced(self):
        card = v.validate_scorecard(reply(risks="single risk", overall_summary={"a": 1}), "Acme", CRITERIA)
        self.assertEqual(card.risks, ["single risk"])
        self.assertEqual(codes(card), ["V10"])
        self.assertEqual(codes(v.validate_scorecard(reply(risks=5), "Acme", CRITERIA)), ["V10"])

    def test_result_is_always_complete_and_in_range(self):
        for garbage in ["", "[]", '{"criteria": [1, 2, 3]}', json.dumps(reply(criteria=[]))]:
            card = v.validate_scorecard(garbage, "Acme", CRITERIA)
            self.assertEqual([c.criterion_id for c in card.criteria], [1, 2])
            self.assertTrue(all(0 <= c.score <= c.max_score for c in card.criteria))
            json.dumps(card.as_evaluation())

    def test_pydantic_model_refuses_out_of_range_score(self):
        with self.assertRaises(ValueError):
            v.CriterionScore(criterion_id=1, name="x", score=11, max_score=10)
        with self.assertRaises(ValueError):
            v.CriterionScore(criterion_id=1, name="x", score=-1, max_score=10)


if __name__ == "__main__":
    unittest.main()
