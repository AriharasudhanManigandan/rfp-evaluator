"""Orchestrator Agent: runs the ten-step workflow and calls each tool in the required order.

Setup -> Input -> Batch -> Evaluate -> Validate -> Score -> Benchmark -> Rank -> Persist -> Present

The orchestrator decides *which tool runs when*; it never scores anything itself.
LLM   -> evaluation_agent.EvaluationAgent  (judges proposal content only)
Tools -> pdf_tool, validation, ranking, db (deterministic Python)
Every tool call is recorded in ``trace`` so a run can be audited end to end.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from . import __version__, db, pdf_tool, ranking, validation
from .config import EXPERIENCE_MAX, EXPERIENCE_MIN, LLM_TEMPERATURE, MIN_SUPPLIERS
from .evaluation_agent import EvaluationAgent

ProgressFn = Callable[[float, str], None]


class InputValidationError(Exception):
    """The batch is not acceptable (bad metadata, unreadable PDF ...). Nothing was written to the database."""

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


class RunFailedError(Exception):
    """The run started but could not finish. It is stored with status FAILED and the reason."""

    def __init__(self, message: str, run_id: str | None = None):
        super().__init__(message)
        self.run_id = run_id


@dataclass
class SupplierInput:
    supplier_name: str
    filename: str
    pdf_bytes: bytes
    submission_date: date
    experience_rating: float


def validate_inputs(suppliers: list[SupplierInput], today: date | None = None) -> list[str]:
    """Pre-flight business checks, shown in the UI before the Evaluate button is enabled."""
    today = today or date.today()
    if not suppliers:
        return ["Upload at least one supplier RFP PDF."]
    errors, seen = [], {}
    if len(suppliers) < MIN_SUPPLIERS:
        errors.append(f"Upload at least {MIN_SUPPLIERS} supplier PDFs - peer benchmarking needs peers.")
    for s in suppliers:
        label = s.filename or "(unnamed file)"
        name = (s.supplier_name or "").strip()
        if not name:
            errors.append(f"{label}: supplier name is required.")
        elif name.casefold() in seen:
            errors.append(f"Duplicate supplier name '{name}' ({seen[name.casefold()]} and {label}).")
        else:
            seen[name.casefold()] = label
        if not isinstance(s.submission_date, date):
            errors.append(f"{name or label}: a valid submission date is required.")
        elif s.submission_date > today:
            errors.append(f"{name or label}: submission date {s.submission_date} is in the future.")
        rating = validation.coerce_number(s.experience_rating)
        if rating is None or not (EXPERIENCE_MIN <= rating <= EXPERIENCE_MAX):
            errors.append(f"{name or label}: experience rating must be between "
                          f"{EXPERIENCE_MIN:g} and {EXPERIENCE_MAX:g}.")
        if not s.pdf_bytes:
            errors.append(f"{label}: the file is empty.")
        elif not s.pdf_bytes.lstrip()[:5] == b"%PDF-":
            errors.append(f"{label}: not a valid PDF file.")
    return errors


class Orchestrator:
    def __init__(self, conn, agent: EvaluationAgent, progress: ProgressFn | None = None):
        self.conn, self.agent = conn, agent
        self.progress = progress or (lambda fraction, message: None)
        self.trace: list[dict] = []
        # Tool registry: the orchestrator reaches the outside world only through these.
        self.tools = {
            "document.extract_text": pdf_tool.extract_text,
            "criteria.load": db.get_active_criteria,
            "agent.evaluate": agent.evaluate,
            "validation.validate_scorecard": validation.validate_scorecard,
            "ranking.rank_suppliers": ranking.rank_suppliers,
            "db.save_run_results": db.save_run_results,
        }

    def _call(self, step: str, tool: str, supplier: str | None, fn, *args, detail=None):
        started = time.perf_counter()
        try:
            result = fn(*args)
        except Exception as exc:
            self._log(step, tool, supplier, "error", str(exc), started)
            raise
        self._log(step, tool, supplier, "ok", detail(result) if detail else "", started)
        return result

    def _log(self, step: str, tool: str, supplier: str | None, status: str, detail: str, started: float | None = None):
        self.trace.append({
            "step": step, "tool": tool, "supplier": supplier, "status": status, "detail": detail,
            "seconds": round(time.perf_counter() - started, 3) if started is not None else None,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    # ------------------------------------------------------------------ workflow
    def run(self, suppliers: list[SupplierInput]) -> dict:
        errors = validate_inputs(suppliers)
        if errors:
            raise InputValidationError(errors)

        # Step 2 - extract every document up front so an unreadable PDF stops the batch before anything is stored
        docs, doc_errors = {}, []
        for s in suppliers:
            try:
                docs[s.supplier_name] = self._call(
                    "2 Input", "document.extract_text", s.supplier_name, self.tools["document.extract_text"],
                    s.pdf_bytes, s.filename,
                    detail=lambda d: f"{d.pages} page(s), {d.chars:,} chars via {d.extractor}"
                                     f"{' (truncated)' if d.truncated else ''}")
            except pdf_tool.DocumentError as exc:
                doc_errors.append(str(exc))
        if doc_errors:
            raise InputValidationError(doc_errors)
        self.progress(0.05, "Documents read")

        # Step 3 - batch: run id + one row per supplier
        run_id = db.new_run_id()
        db.create_run(self.conn, run_id, self.agent.llm.provider, self.agent.llm.model)
        db.add_supplier_entries(self.conn, run_id, [
            {"supplier_name": s.supplier_name, "source_file": s.filename,
             "submission_date": s.submission_date.isoformat(), "experience_rating": s.experience_rating}
            for s in suppliers])
        self._log("3 Batch", "db.create_run", None, "ok", f"{run_id}: {len(suppliers)} supplier entries")

        try:
            # Step 4 - reload the LATEST active criteria
            raw_criteria = self._call("4 Evaluate", "criteria.load", None, self.tools["criteria.load"], self.conn,
                                      detail=lambda r: f"{len(r)} active criteria: " + ", ".join(
                                          f"{c['name']} {c['weight']:g}%" for c in r))
            problems = db.validate_criteria(raw_criteria, require_total=False)
            if problems:
                raise ValueError("Criteria invalid: " + "; ".join(problems))
            criteria, rescaled = ranking.normalize_weights(raw_criteria)
            warnings: list[str] = []
            if rescaled:
                warnings.append(f"Active criterion weights total "
                                f"{sum(c['configured_weight'] for c in criteria):g}%, not 100%; weights were "
                                "scaled proportionally to 100%.")
            db.set_run_status(self.conn, run_id, "RUNNING", criteria_snapshot=criteria)

            entries = []
            for index, s in enumerate(suppliers):
                name = s.supplier_name
                self.progress(0.08 + 0.8 * index / len(suppliers), f"Evaluating {name} ({index + 1}/{len(suppliers)})")
                db.set_supplier_status(self.conn, run_id, name, "EVALUATING")
                doc = docs[name]
                result = self._call("4 Evaluate", "agent.evaluate", name, self.tools["agent.evaluate"],
                                    criteria, name, doc.text,
                                    detail=lambda r: f"{r.attempts} call(s), {r.elapsed_s}s"
                                                     f"{', JSON self-repaired' if r.repaired else ''}")
                card = self._call("5 Validate", "validation.validate_scorecard", name,
                                  self.tools["validation.validate_scorecard"], result.raw_text, name, criteria,
                                  detail=lambda c: f"{len(c.warnings)} warning(s)"
                                                   f"{'' if c.llm_output_valid_json else ' - reply was not valid JSON'}")
                db.set_supplier_status(self.conn, run_id, name, "VALIDATED")
                warnings += [f"{name}: {w}" for w in doc.warnings + card.warnings]
                entries.append({
                    "supplier_name": name, "submission_date": s.submission_date,
                    "experience_rating": float(s.experience_rating), "evaluation": card.as_evaluation(),
                    "source_file": s.filename, "validation_warnings": doc.warnings + card.warnings,
                    "llm_output_valid_json": card.llm_output_valid_json,
                    "llm_call": {"attempts": result.attempts, "repaired": result.repaired,
                                 "elapsed_s": result.elapsed_s, "raw_reply_chars": len(result.raw_text)},
                    "llm_raw_response": result.raw_text,
                    "source_document": {"file_name": s.filename, "pages": doc.pages, "chars": doc.chars,
                                        "sha256": doc.sha256, "extractor": doc.extractor,
                                        "truncated": doc.truncated}})

            # Steps 6-8 - score, benchmark, rank (deterministic)
            self.progress(0.9, "Scoring, benchmarking and ranking")
            ranked, benchmarks, tie_break = self._call(
                "6-8 Score/Benchmark/Rank", "ranking.rank_suppliers", None, self.tools["ranking.rank_suppliers"],
                entries, criteria,
                detail=lambda r: f"{len(r[0])} supplier(s) ranked; {r[2]['summary']}")

            run = {
                "rfp_run_id": run_id, "created_at": db.utc_now(),
                "status": "COMPLETED_WITH_WARNINGS" if warnings or any(e["validation_warnings"] for e in entries)
                else "COMPLETED",
                "app_version": __version__,
                "llm": {"provider": self.agent.llm.provider, "model": self.agent.llm.model,
                        "temperature": LLM_TEMPERATURE},
                "criteria": criteria, "weights_normalized": rescaled, "formulas": ranking.FORMULAS,
                "tie_break_rules": ranking.TIE_BREAK_RULES, "tie_break": tie_break,
                "ties_resolved": tie_break["ties_resolved"], "benchmarks": benchmarks,
                "leaderboard": ranking.build_leaderboard(ranked), "suppliers": ranked, "warnings": warnings,
            }
            # Step 9 - persist everything under one RFP_RUN_ID (logged first so the stored trace includes it)
            self._log("9 Persist", "db.save_run_results", None, "ok",
                      f"saved {len(ranked)} supplier results and the run JSON under {run_id}")
            run["trace"] = self.trace
            self.tools["db.save_run_results"](self.conn, run)
            self.progress(1.0, "Done")
            return run
        except Exception as exc:
            self._log("FAILED", "orchestrator", None, "error", str(exc))
            db.set_run_status(self.conn, run_id, "FAILED", error_message=str(exc))
            raise RunFailedError(str(exc), run_id) from exc
