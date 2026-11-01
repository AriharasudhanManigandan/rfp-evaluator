import unittest

from rfp import llm
from rfp.evaluation_agent import EvaluationAgent, LLMError, build_prompts, looks_like_json
from tests.helpers import CRITERIA, StubLLM, good_payload


class PromptTests(unittest.TestCase):
    def test_prompt_is_built_from_the_criteria_passed_in(self):
        crit = [{"criterion_id": 7, "name": "Sustainability", "description": "Carbon plan", "max_score": 5}]
        system, user = build_prompts(crit, "Acme", "DOC TEXT")
        for needle in ("Sustainability", "Carbon plan", '"criterion_id": 7', "Acme", "DOC TEXT",
                       "ONLY evidence present", "untrusted data"):
            self.assertIn(needle, user)
        self.assertIn("never calculate", system)


class AgentTests(unittest.TestCase):
    def agent(self, stub, retries=2):
        return EvaluationAgent(stub, max_retries=retries, backoff_s=0)

    def test_happy_path_single_call(self):
        stub = StubLLM(good_payload(CRITERIA))
        result = self.agent(stub).evaluate(CRITERIA, "Apex Systems", "text")
        self.assertEqual((result.attempts, result.repaired, len(stub.calls)), (1, False, 1))

    def test_retries_after_transient_errors(self):
        stub = StubLLM(ConnectionError("down"), TimeoutError("slow"), good_payload(CRITERIA))
        result = self.agent(stub).evaluate(CRITERIA, "Apex Systems", "text")
        self.assertEqual((result.attempts, len(stub.calls)), (3, 3))

    def test_gives_up_after_retries(self):
        stub = StubLLM(ConnectionError("down"))
        with self.assertRaises(LLMError) as ctx:
            self.agent(stub, retries=1).evaluate(CRITERIA, "Apex Systems", "text")
        self.assertEqual(len(stub.calls), 2)
        self.assertIn("down", str(ctx.exception))

    def test_unparseable_reply_gets_one_self_repair_round(self):
        stub = StubLLM("Sorry, here is my opinion...", good_payload(CRITERIA))
        result = self.agent(stub).evaluate(CRITERIA, "Apex Systems", "text")
        self.assertTrue(result.repaired)
        self.assertIsNone(stub.calls[0]["repair_of"])
        self.assertEqual(stub.calls[1]["repair_of"], "Sorry, here is my opinion...")
        self.assertTrue(looks_like_json(result.raw_text))

    def test_still_unparseable_after_repair_is_passed_on_for_validation(self):
        stub = StubLLM("nope", "still nope")
        result = self.agent(stub).evaluate(CRITERIA, "Apex Systems", "text")
        self.assertFalse(result.repaired)
        self.assertEqual((len(stub.calls), result.raw_text), (2, "nope"))     # original reply kept; V1 follows


class ProviderSelectionTests(unittest.TestCase):
    def test_offline_provider_needs_no_key(self):
        self.assertEqual(llm.build_client("mock").provider, "offline-demo")

    def test_missing_key_is_a_clear_error(self):
        with self.assertRaises(llm.LLMConfigError):
            llm.build_client("openai", secrets={})

    def test_fault_injector_only_damages_chosen_suppliers(self):
        inner = llm.MockClient()
        _, user = build_prompts([{**c, "description": "technical architecture api"} for c in CRITERIA],
                                "Target", "Our architecture uses an API. " * 20)
        clean = inner.complete_json("", user)
        faulty = llm.FaultInjectingClient(inner, {"Target"})
        damaged = faulty.complete_json("", user)
        self.assertNotEqual(clean, damaged)
        self.assertTrue(damaged.startswith("Sure!"))
        self.assertEqual(faulty.complete_json("", user, repair_of="x"), clean)      # repair rounds untouched
        other = user.replace("<supplier_name>Target", "<supplier_name>Someone")
        self.assertEqual(faulty.complete_json("", other), inner.complete_json("", other))


if __name__ == "__main__":
    unittest.main()
