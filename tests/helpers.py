import json
from datetime import date

CRITERIA = [
    {"criterion_id": 1, "name": "Technical", "description": "", "weight": 60.0, "max_score": 10.0},
    {"criterion_id": 2, "name": "Commercial", "description": "", "weight": 40.0, "max_score": 10.0},
]


def evaluation(scores, valid=None):
    """A validated-style evaluation for CRITERIA."""
    valid = valid or [True] * len(scores)
    return {"risks": [], "overall_summary": "",
            "criteria": [{"criterion_id": c["criterion_id"], "name": c["name"], "max_score": c["max_score"],
                          "score": s, "valid": v, "imputed": False, "evidence_missing": False,
                          "justification": "j", "evidence": "e"} for c, s, v in zip(CRITERIA, scores, valid)]}


def entry(name, scores, day="2026-10-10", exp=5.0, valid=None):
    return {"supplier_name": name, "submission_date": date.fromisoformat(day), "experience_rating": exp,
            "evaluation": evaluation(scores, valid)}


class StubLLM:
    """Replays fixed replies (a reply may be an Exception to raise). Records every call."""
    provider, model = "stub", "stub-1"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete_json(self, system, user, repair_of=None):
        self.calls.append({"system": system, "user": user, "repair_of": repair_of})
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply(user) if callable(reply) else reply


def good_payload(criteria, name="Apex Systems", score=8):
    return json.dumps({"supplier_name": name, "criteria": [
        {"criterion_id": c["criterion_id"], "score": score, "max_score": c["max_score"],
         "justification": "ok", "evidence": "quote"} for c in criteria], "risks": ["r"], "overall_summary": "sum"})
