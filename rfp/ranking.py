"""Ranking Tool: deterministic arithmetic only. No LLM, no I/O, no randomness.

Given the same validated scorecards, criteria and supplier metadata it always returns the same numbers
and the same order, regardless of the order in which suppliers are passed in.

Formulas (weights are percentages that total 100)
-------------------------------------------------
absolute_score  = SUM( score / max_score * weight )                                  (0-100)
benchmark       = highest *valid* score for the criterion across all suppliers
gap             = score - benchmark                  (0 for the leader, otherwise negative)
relative_pct    = score / benchmark * 100            (100 when the benchmark is 0: nobody is ahead)
PPI             = SUM( relative_pct * weight ) / SUM( weight )                       (0-100)

Order: higher PPI -> earlier submission date -> higher experience rating -> supplier name A-Z.
PPI is compared after rounding to ``PPI_DECIMALS`` so floating-point noise cannot create a false difference.
"""
from __future__ import annotations

from datetime import date

PRECISION = 4          # decimals kept in stored numbers
PPI_DECIMALS = 4       # decimals used when comparing PPI for ties

TIE_BREAK_RULES = [
    "1) Higher Peer Performance Index (PPI) ranks first",
    "2) If PPI is tied, the earlier submission date ranks first",
    "3) If still tied, the higher historical experience rating ranks first",
    "4) If still tied, supplier name in ascending (A-Z) order",
]
TIE_BREAK_ORDER = [
    "Higher Peer Performance Index (PPI)",
    "Earlier submission date",
    "Higher historical experience rating",
    "Supplier name (ascending)",
]
FORMULAS = {
    "absolute_score": "SUM(score / max_score * weight)",
    "benchmark": "highest valid score for the criterion across all suppliers",
    "gap": "score - benchmark",
    "relative_pct": "score / benchmark * 100 (100 if benchmark = 0)",
    "ppi": "SUM(relative_pct * weight) / SUM(weight)",
    "tie_break_order": TIE_BREAK_ORDER,
}


def normalize_weights(criteria: list[dict]) -> tuple[list[dict], bool]:
    """Scale weights to total 100 when they do not. Returns (criteria, was_rescaled)."""
    total = sum(float(c["weight"]) for c in criteria)
    if total <= 0:
        raise ValueError("Active criteria must have a total weight greater than zero.")
    rescaled = abs(total - 100.0) > 1e-9
    out = []
    for c in criteria:
        weight = float(c["weight"]) / total * 100.0 if rescaled else float(c["weight"])
        out.append({**c, "configured_weight": float(c["weight"]), "weight": round(weight, 6)})
    return out, rescaled


def relative_pct(score: float, benchmark: float) -> float:
    if benchmark <= 0:
        return 100.0           # nobody scored above zero, so nobody trails the benchmark
    return min(100.0, score / benchmark * 100.0)


def compute_benchmarks(evaluations: list[dict], criteria: list[dict]) -> dict[int, float]:
    bench = {}
    for crit in criteria:
        valid = [item["score"] for ev in evaluations for item in ev["criteria"]
                 if item["criterion_id"] == crit["criterion_id"] and item.get("valid", True)]
        bench[crit["criterion_id"]] = max(valid) if valid else 0.0
    return bench


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _sort_key(s: dict) -> tuple:
    return (-round(s["ppi"], PPI_DECIMALS), _as_date(s["submission_date"]),
            -float(s["experience_rating"]), s["supplier_name"].casefold(), s["supplier_name"])


def _rule_that_decided(upper: dict, lower: dict) -> tuple[int, str]:
    """Return (rule number, human explanation) for why `upper` sits directly above `lower`."""
    if round(upper["ppi"], PPI_DECIMALS) != round(lower["ppi"], PPI_DECIMALS):
        return 1, f"Rule 1 - higher PPI ({upper['ppi']:.2f} vs {lower['ppi']:.2f})"
    d_up, d_low = _as_date(upper["submission_date"]), _as_date(lower["submission_date"])
    if d_up != d_low:
        return 2, f"PPI tied at {upper['ppi']:.2f}; Rule 2 - earlier submission date ({d_up} vs {d_low})"
    e_up, e_low = float(upper["experience_rating"]), float(lower["experience_rating"])
    if e_up != e_low:
        return 3, f"PPI and date tied; Rule 3 - higher experience rating ({e_up:g} vs {e_low:g})"
    return 4, "PPI, date and experience rating tied; Rule 4 - supplier name A-Z"


def rank_suppliers(entries: list[dict], criteria: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """entries: dicts with supplier_name, submission_date, experience_rating, evaluation (+ any extras).

    Returns (ranked suppliers, per-criterion benchmarks, tie-break explanation)."""
    if not criteria:
        raise ValueError("No active criteria supplied to the ranking tool.")
    if not entries:
        raise ValueError("No suppliers supplied to the ranking tool.")
    if len({e["supplier_name"].casefold() for e in entries}) != len(entries):
        raise ValueError("Supplier names must be unique within a run.")

    total_weight = sum(c["weight"] for c in criteria)
    benchmarks = compute_benchmarks([e["evaluation"] for e in entries], criteria)

    scored = []
    for entry in entries:
        by_id = {item["criterion_id"]: item for item in entry["evaluation"]["criteria"]}
        analysis, absolute, weighted_rel = [], 0.0, 0.0
        for crit in criteria:
            item = by_id[crit["criterion_id"]]
            bench = benchmarks[crit["criterion_id"]]
            contribution = item["score"] / crit["max_score"] * crit["weight"]
            rel = relative_pct(item["score"], bench)
            absolute += contribution
            weighted_rel += rel * crit["weight"]
            analysis.append({
                "criterion_id": crit["criterion_id"], "name": crit["name"],
                "weight": round(crit["weight"], PRECISION), "max_score": crit["max_score"],
                "score": item["score"], "valid": item.get("valid", True),
                "imputed": item.get("imputed", False), "evidence_missing": item.get("evidence_missing", False),
                "benchmark": bench, "gap": round(item["score"] - bench, PRECISION),
                "relative_pct": round(rel, PRECISION),
                "is_benchmark_leader": bench > 0 and item.get("valid", True) and item["score"] == bench,
                "weighted_contribution": round(contribution, PRECISION),
                "justification": item["justification"], "evidence": item["evidence"],
            })
        extras = {k: v for k, v in entry.items() if k != "evaluation"}
        extras["submission_date"] = _as_date(entry["submission_date"]).isoformat()
        raw_ppi = weighted_rel / total_weight
        scored.append({
            **extras,
            "absolute_score": round(absolute, PRECISION),
            "ppi": round(raw_ppi, PRECISION),
            "ppi_sort_value": round(raw_ppi, PPI_DECIMALS),
            "criteria_analysis": analysis,
            "risks": entry["evaluation"]["risks"],
            "overall_summary": entry["evaluation"]["overall_summary"],
        })

    scored.sort(key=_sort_key)
    for position, supplier in enumerate(scored, start=1):
        supplier["final_rank"] = position

    for index, supplier in enumerate(scored):
        if len(scored) == 1:
            supplier["tie_break_explanation"] = "Ranked #1 - the only supplier in this run."
        elif index == 0:
            supplier["tie_break_explanation"] = (
                f"Ranked #1 - ahead of {scored[1]['supplier_name']}: {_rule_that_decided(supplier, scored[1])[1]}.")
        else:
            above = scored[index - 1]
            supplier["tie_break_explanation"] = (
                f"Ranked #{index + 1} - behind {above['supplier_name']}: {_rule_that_decided(above, supplier)[1]}.")

    names = {c["criterion_id"]: c["name"] for c in criteria}
    max_scores = {c["criterion_id"]: c["max_score"] for c in criteria}
    bench_list = []
    for cid, best in benchmarks.items():
        leaders = sorted(s["supplier_name"] for s in scored
                         if any(a["criterion_id"] == cid and a["is_benchmark_leader"] for a in s["criteria_analysis"]))
        bench_list.append({"criterion_id": cid, "name": names[cid], "benchmark_score": best,
                           "max_score": max_scores[cid], "leaders": leaders, "benchmark_zero": best == 0})
    return scored, bench_list, explain_ties(scored)


def explain_ties(ranked: list[dict]) -> dict:
    decisions, resolved = [], []
    for upper, lower in zip(ranked, ranked[1:]):
        if round(upper["ppi"], PPI_DECIMALS) != round(lower["ppi"], PPI_DECIMALS):
            continue
        rule_no, why = _rule_that_decided(upper, lower)
        decisions.append({
            "rank_above": upper["final_rank"], "supplier_above": upper["supplier_name"],
            "rank_below": lower["final_rank"], "supplier_below": lower["supplier_name"],
            "tied_ppi": upper["ppi"], "rule_number": rule_no, "decided_by": TIE_BREAK_ORDER[rule_no - 1],
            "explanation": f"{upper['supplier_name']} (#{upper['final_rank']}) ranks above "
                           f"{lower['supplier_name']} (#{lower['final_rank']}): {why}.",
        })
        resolved.append(f"{upper['supplier_name']} and {lower['supplier_name']} tied on PPI "
                        f"({upper['ppi']:.2f}); resolved by: {why}")
    summary = ("No PPI ties - Rule 1 decided every position." if not decisions
               else f"{len(decisions)} PPI tie(s) resolved with the mandatory tie-break order.")
    return {"rules": TIE_BREAK_RULES, "order": TIE_BREAK_ORDER, "ties_found": bool(decisions),
            "decisions": decisions, "ties_resolved": resolved, "summary": summary}


def build_leaderboard(suppliers: list[dict]) -> list[dict]:
    return [{"rank": s["final_rank"], "supplier_name": s["supplier_name"],
             "absolute_score": s["absolute_score"], "ppi": s["ppi"],
             "submission_date": s["submission_date"], "experience_rating": s["experience_rating"]}
            for s in sorted(suppliers, key=lambda s: s["final_rank"])]
