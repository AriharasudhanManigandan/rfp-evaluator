"""Validation Tool: turn a raw LLM reply into a trusted, complete scorecard.

The LLM output is never trusted. Whatever comes back, ``validate_scorecard`` returns a Pydantic
``ValidatedScorecard`` with exactly one numeric score inside [0, max_score] for every active criterion,
and a list of coded warnings explaining every repair.

Normalisation rules
-------------------
V1  reply is not parseable JSON / ``criteria`` is not a list -> every criterion imputed as 0
V2  supplier_name differs from the input                      -> input name kept
V3  criterion_id missing or unknown                           -> matched by criterion name, else ignored
V4  duplicate result for a criterion                          -> first occurrence kept
V5  score missing / non-numeric / NaN                         -> 0 (excluded from the benchmark)
V6  LLM scored on a different scale (e.g. 4/5)                -> rescaled to the database max_score
V7  score outside [0, max_score]                              -> clipped
V8  active criterion missing from the reply                   -> imputed 0 and flagged ``imputed``
V9  no evidence quote                                         -> kept, flagged ``evidence_missing``
V10 risks / overall_summary have the wrong type               -> coerced
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_TEXT_LEN = 1500
NO_JUSTIFICATION = "No justification provided by the model."


class LLMOutputError(ValueError):
    """The model reply could not be parsed as a JSON object."""


# --------------------------------------------------------------------------- schema (Pydantic)
class CriterionScore(BaseModel):
    """One validated criterion result. ``valid`` is False when the score had to be imputed."""

    model_config = ConfigDict(extra="ignore")

    criterion_id: int
    name: str
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    valid: bool = True
    imputed: bool = False
    evidence_missing: bool = False
    justification: str = ""
    evidence: str = ""

    @field_validator("score")
    @classmethod
    def _round_score(cls, value: float) -> float:
        return round(value, 4)

    @model_validator(mode="after")
    def _within_scale(self):
        if self.score > self.max_score + 1e-9:
            raise ValueError(f"score {self.score} exceeds max_score {self.max_score}")
        return self


class ValidatedScorecard(BaseModel):
    supplier_name: str
    criteria: list[CriterionScore]
    risks: list[str] = Field(default_factory=list)
    overall_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    llm_output_valid_json: bool = True

    def as_evaluation(self) -> dict:
        """Plain-dict form used by the Ranking Tool."""
        return self.model_dump()


# --------------------------------------------------------------------------- parsing helpers
def parse_llm_json(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None or not str(raw).strip():
        raise LLMOutputError("The model returned an empty response.")
    text = str(raw).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMOutputError("No JSON object found in the model response.")
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LLMOutputError(f"Malformed JSON from the model: {exc}") from exc
    if not isinstance(obj, dict):
        raise LLMOutputError("Model response is JSON but not an object.")
    return obj


_NUMBER = r"-?\d+(?:[.,]\d+)?"


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def parse_score(value: Any) -> tuple[float | None, float | None]:
    """Return (score, scale_hint). Accepts numbers, "7", "7,5" and "4/5" (scale hint 5)."""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        number = float(value)
        return (number, None) if math.isfinite(number) else (None, None)
    if isinstance(value, str):
        ratio = re.fullmatch(rf"\s*({_NUMBER})\s*/\s*({_NUMBER})\s*", value)
        if ratio:
            return _num(ratio.group(1)), _num(ratio.group(2))
        single = re.fullmatch(rf"\s*({_NUMBER})\s*", value)
        if single:
            return _num(single.group(1)), None
    return None, None


def coerce_number(value: Any) -> float | None:
    return parse_score(value)[0]


def _text(value: Any, default: str = "") -> tuple[str, bool]:
    """Return (clean text, was_missing)."""
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    if value is None or not str(value).strip():
        return default, True
    return str(value).strip()[:MAX_TEXT_LEN], False


# --------------------------------------------------------------------------- main entry point
def validate_scorecard(raw: str | dict | None, supplier_name: str, criteria: list[dict]) -> ValidatedScorecard:
    """Validate and normalise one supplier's raw LLM reply against the active ``criteria``."""
    warnings: list[str] = []
    by_id = {int(c["criterion_id"]): c for c in criteria}
    by_name = {str(c["name"]).strip().casefold(): int(c["criterion_id"]) for c in criteria}

    try:
        data: dict | None = parse_llm_json(raw)
    except LLMOutputError as exc:
        warnings.append(f"V1: reply is not valid JSON ({exc}); every criterion imputed as 0.")
        data = None
    valid_json = data is not None
    data = data or {}

    claimed = str(data.get("supplier_name") or "").strip()
    if valid_json and claimed and claimed.casefold() != supplier_name.casefold():
        warnings.append(f"V2: model reported supplier name {claimed!r}; using {supplier_name!r}.")

    items = data.get("criteria")
    if valid_json and not isinstance(items, list):
        warnings.append("V1: 'criteria' is missing or not a list; every criterion imputed as 0.")
    if not isinstance(items, list):
        items = []

    accepted: dict[int, CriterionScore] = {}
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            warnings.append(f"V3: criteria entry #{position} is not an object; ignored.")
            continue
        raw_id = coerce_number(item.get("criterion_id"))
        cid = int(raw_id) if raw_id is not None and float(raw_id).is_integer() else None
        if cid not in by_id:
            label = str(item.get("name") or item.get("criterion_name") or item.get("criterion") or "")
            matched = by_name.get(label.strip().casefold())
            if matched is None:
                warnings.append(f"V3: criteria entry #{position} has unknown criterion_id "
                                f"{item.get('criterion_id')!r}; ignored.")
                continue
            cid = matched
            warnings.append(f"V3: criteria entry #{position} matched by name to criterion {cid}.")
        crit = by_id[cid]
        tag = f"criterion {cid} ({crit['name']})"
        if cid in accepted:
            warnings.append(f"V4: duplicate result for {tag}; first occurrence kept.")
            continue

        max_score = float(crit["max_score"])
        score, hint = parse_score(item.get("score"))
        valid = True
        if score is None:
            warnings.append(f"V5: {tag} score {item.get('score')!r} is not numeric; set to 0 and excluded "
                            "from the benchmark.")
            score, valid = 0.0, False
        else:
            reported_max = coerce_number(item.get("max_score")) or hint
            if reported_max and reported_max > 0 and abs(reported_max - max_score) > 1e-9 \
                    and 0 <= score <= reported_max:
                rescaled = score / reported_max * max_score
                warnings.append(f"V6: {tag} was scored on a 0-{reported_max:g} scale; rescaled "
                                f"{score:g} -> {rescaled:.2f} (database max {max_score:g}).")
                score = rescaled
            if score < 0 or score > max_score:
                clipped = min(max(score, 0.0), max_score)
                warnings.append(f"V7: {tag} score {score:g} is outside 0-{max_score:g}; clipped to {clipped:g}.")
                score = clipped

        evidence, no_evidence = _text(item.get("evidence"))
        if no_evidence:
            warnings.append(f"V9: {tag} has no supporting evidence quote.")
        justification, _ = _text(item.get("justification"), NO_JUSTIFICATION)
        accepted[cid] = CriterionScore(
            criterion_id=cid, name=crit["name"], score=score, max_score=max_score, valid=valid,
            evidence_missing=no_evidence, justification=justification, evidence=evidence)

    ordered: list[CriterionScore] = []
    for cid, crit in by_id.items():
        if cid in accepted:
            ordered.append(accepted[cid])
            continue
        if valid_json and isinstance(data.get("criteria"), list):
            warnings.append(f"V8: criterion {cid} ({crit['name']}) is missing from the reply; imputed 0 and "
                            "excluded from the benchmark.")
        ordered.append(CriterionScore(
            criterion_id=cid, name=crit["name"], score=0.0, max_score=float(crit["max_score"]), valid=False,
            imputed=True, evidence_missing=True,
            justification="Not evaluated - the model returned no result for this criterion (imputed 0)."))

    risks = data.get("risks", [])
    if isinstance(risks, str):
        risks = [risks]
    if not isinstance(risks, list):
        warnings.append("V10: 'risks' was not a list; ignored.")
        risks = []
    summary = data.get("overall_summary", "")
    if not isinstance(summary, str):
        warnings.append("V10: 'overall_summary' was not text; converted.")
        summary = json.dumps(summary)

    return ValidatedScorecard(
        supplier_name=supplier_name, criteria=ordered,
        risks=[str(r).strip()[:500] for r in risks if str(r).strip()][:10],
        overall_summary=summary.strip()[:MAX_TEXT_LEN] or "No summary provided by the model.",
        warnings=warnings, llm_output_valid_json=valid_json)
