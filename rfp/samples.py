"""Metadata for the bundled synthetic supplier PDFs (used by the demo toggle and the command-line scripts)."""
from __future__ import annotations

import json
from datetime import date

from .config import SAMPLE_DIR
from .orchestrator import SupplierInput


def load_sample_metadata() -> list[dict]:
    raw = json.loads((SAMPLE_DIR / "metadata.json").read_text(encoding="utf-8"))
    return [{**m, "submission_date": date.fromisoformat(m["submission_date"]),
             "experience_rating": float(m["experience_rating"]), "edge_case": bool(m.get("edge_case", False))}
            for m in raw]


def sample_inputs(include_edge_case: bool = True) -> list[SupplierInput]:
    return [SupplierInput(m["supplier_name"], m["file"], (SAMPLE_DIR / m["file"]).read_bytes(),
                          m["submission_date"], m["experience_rating"])
            for m in load_sample_metadata() if include_edge_case or not m["edge_case"]]
