"""SQLite persistence: schema, migrations, criteria management and run storage.

Tables (a superset of the minimum in the brief):

evaluation_criteria(criterion_id, name, description, weight, max_score, is_active, updated_at)
rfp_runs(rfp_run_id, created_at, status, completed_at, llm_provider, llm_model,
         criteria_snapshot, run_json, error_message)
supplier_results(id, rfp_run_id, supplier_name, source_file, submission_date, experience_rating,
                 status, absolute_score, ppi, final_rank, result_json)

Databases created by an earlier version of the app are upgraded in place (missing columns are added).
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluation_criteria (
    criterion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT    NOT NULL DEFAULT '',
    weight       REAL    NOT NULL CHECK (weight >= 0),
    max_score    REAL    NOT NULL DEFAULT 10 CHECK (max_score > 0),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS rfp_runs (
    rfp_run_id        TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    status            TEXT NOT NULL,   -- CREATED | RUNNING | COMPLETED | COMPLETED_WITH_WARNINGS | FAILED
    completed_at      TEXT,
    llm_provider      TEXT,
    llm_model         TEXT,
    criteria_snapshot TEXT,            -- criteria + weights actually used in this run
    run_json          TEXT,            -- the complete result, exactly as downloaded
    error_message     TEXT
);

CREATE TABLE IF NOT EXISTS supplier_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id        TEXT NOT NULL REFERENCES rfp_runs (rfp_run_id),
    supplier_name     TEXT NOT NULL,
    source_file       TEXT,
    submission_date   TEXT NOT NULL,
    experience_rating REAL NOT NULL,
    status            TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING | EVALUATING | VALIDATED | EVALUATED
    absolute_score    REAL,
    ppi               REAL,
    final_rank        INTEGER,
    result_json       TEXT,
    UNIQUE (rfp_run_id, supplier_name)
);

CREATE INDEX IF NOT EXISTS idx_supplier_results_run ON supplier_results (rfp_run_id);
"""

# columns added after the first release: (table, column, DDL)
MIGRATIONS = [
    ("evaluation_criteria", "updated_at", "TEXT"),
    ("rfp_runs", "completed_at", "TEXT"),
    ("rfp_runs", "llm_provider", "TEXT"),
    ("rfp_runs", "llm_model", "TEXT"),
    ("rfp_runs", "run_json", "TEXT"),
    ("rfp_runs", "error_message", "TEXT"),
    ("supplier_results", "source_file", "TEXT"),
    ("supplier_results", "status", "TEXT NOT NULL DEFAULT 'EVALUATED'"),
]

SEED_CRITERIA = [
    ("Technical Capability", "Architecture, integrations, scalability, technical fit", 30, 10),
    ("Implementation Plan", "Timeline, milestones, staffing, risk plan", 20, 10),
    ("Commercial Value", "Pricing clarity, total cost, assumptions", 20, 10),
    ("Security & Compliance", "Controls, certifications, privacy, auditability", 20, 10),
    ("Support & Experience", "Support model, similar projects, references", 10, 10),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    return f"RFP-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4].upper()}"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path: str | Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db(conn: sqlite3.Connection) -> None:
    """Create missing tables and upgrade older databases in place."""
    conn.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()


def seed_criteria(conn: sqlite3.Connection, force: bool = False) -> int:
    """Insert the sample criteria if the table is empty (or always when force=True)."""
    count = conn.execute("SELECT COUNT(*) FROM evaluation_criteria").fetchone()[0]
    if count and not force:
        return 0
    if force:
        conn.execute("DELETE FROM evaluation_criteria")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'evaluation_criteria'")
    conn.executemany(
        "INSERT INTO evaluation_criteria (name, description, weight, max_score, is_active, updated_at) "
        "VALUES (?, ?, ?, ?, 1, ?)", [(n, d, w, m, utc_now()) for n, d, w, m in SEED_CRITERIA])
    conn.commit()
    return len(SEED_CRITERIA)


def bootstrap(path: str | Path | None = None) -> None:
    """Create schema + seed criteria. Safe to call on every app start."""
    with session(path) as conn:
        init_db(conn)
        seed_criteria(conn)


# --------------------------------------------------------------------------- criteria
def _criteria(conn: sqlite3.Connection, active_only: bool) -> list[dict]:
    sql = "SELECT criterion_id, name, description, weight, max_score, is_active FROM evaluation_criteria"
    if active_only:
        sql += " WHERE is_active = 1"
    return [dict(r) for r in conn.execute(sql + " ORDER BY criterion_id")]


def get_active_criteria(conn: sqlite3.Connection) -> list[dict]:
    return _criteria(conn, active_only=True)


def get_all_criteria(conn: sqlite3.Connection) -> list[dict]:
    return _criteria(conn, active_only=False)


def _is_nan(value) -> bool:
    return isinstance(value, float) and math.isnan(value)


def validate_criteria(rows: list[dict], require_total: bool = True) -> list[str]:
    """Human-readable problems with a criteria list; an empty list means it is valid."""
    errors: list[str] = []
    active = [r for r in rows if r.get("is_active", 1) and not _is_nan(r.get("is_active", 1))]
    if not active:
        errors.append("At least one criterion must be active.")
    names = [str(r.get("name") or "").strip() for r in rows]
    if any(not n for n in names):
        errors.append("Every criterion needs a name.")
    lowered = [n.casefold() for n in names if n]
    if len(lowered) != len(set(lowered)):
        errors.append("Criterion names must be unique.")
    for r in rows:
        label = r.get("name") or "(unnamed)"
        try:
            weight, max_score = float(r.get("weight")), float(r.get("max_score"))
        except (TypeError, ValueError):
            errors.append(f"'{label}': weight and max score must be numbers.")
            continue
        if not (math.isfinite(weight) and 0 <= weight <= 100):
            errors.append(f"'{label}': weight must be between 0 and 100.")
        if not (math.isfinite(max_score) and max_score > 0):
            errors.append(f"'{label}': max score must be greater than 0.")
    if require_total and active:
        total = 0.0
        for r in active:
            try:
                weight = float(r.get("weight") or 0)
            except (TypeError, ValueError):
                continue                    # already reported above as "must be numbers"
            total += weight if math.isfinite(weight) else 0.0
        if abs(total - 100.0) > 0.01:
            errors.append(f"Active criteria weights must total 100% (currently {total:g}%).")
    return errors


def update_criteria(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Apply edits from the UI. Existing criteria absent from `rows` are deactivated (never deleted, so
    historical runs stay traceable). Rows without a criterion_id are inserted. Invalid lists are refused."""
    rows = [r for r in rows if str(r.get("name") or "").strip()]    # ignore blank rows from the editor
    errors = validate_criteria(rows)
    if errors:
        raise ValueError("; ".join(errors))
    cleaned = []
    for r in rows:
        desc = r.get("description")
        cid = r.get("criterion_id")
        cleaned.append({
            "criterion_id": None if cid is None or _is_nan(cid) else int(cid),
            "name": str(r["name"]).strip(),
            "description": "" if desc is None or _is_nan(desc) else str(desc).strip(),
            "weight": float(r["weight"]), "max_score": float(r["max_score"]),
            "is_active": 1 if r.get("is_active") and not _is_nan(r.get("is_active")) else 0,
        })
    seen_ids = {c["criterion_id"] for c in cleaned if c["criterion_id"] is not None}
    for row in conn.execute("SELECT criterion_id FROM evaluation_criteria").fetchall():
        if row["criterion_id"] not in seen_ids:
            conn.execute("UPDATE evaluation_criteria SET is_active = 0, updated_at = ? WHERE criterion_id = ?",
                         (utc_now(), row["criterion_id"]))
    for c in cleaned:
        values = (c["name"], c["description"], c["weight"], c["max_score"], c["is_active"], utc_now())
        if c["criterion_id"] is None:
            conn.execute("INSERT INTO evaluation_criteria (name, description, weight, max_score, is_active, "
                         "updated_at) VALUES (?, ?, ?, ?, ?, ?)", values)
        else:
            conn.execute("UPDATE evaluation_criteria SET name=?, description=?, weight=?, max_score=?, "
                         "is_active=?, updated_at=? WHERE criterion_id=?", values + (c["criterion_id"],))
    conn.commit()


# --------------------------------------------------------------------------- runs
def create_run(conn: sqlite3.Connection, run_id: str, llm_provider: str = "", llm_model: str = "") -> None:
    conn.execute("INSERT INTO rfp_runs (rfp_run_id, created_at, status, llm_provider, llm_model) "
                 "VALUES (?, ?, 'CREATED', ?, ?)", (run_id, utc_now(), llm_provider, llm_model))
    conn.commit()


def add_supplier_entries(conn: sqlite3.Connection, run_id: str, suppliers: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO supplier_results (rfp_run_id, supplier_name, source_file, submission_date, "
        "experience_rating, status) VALUES (?, ?, ?, ?, ?, 'PENDING')",
        [(run_id, s["supplier_name"], s.get("source_file"), str(s["submission_date"]),
          float(s["experience_rating"])) for s in suppliers])
    conn.commit()


def set_run_status(conn: sqlite3.Connection, run_id: str, status: str,
                   criteria_snapshot: list[dict] | None = None, error_message: str | None = None) -> None:
    if criteria_snapshot is not None:
        conn.execute("UPDATE rfp_runs SET criteria_snapshot=? WHERE rfp_run_id=?",
                     (json.dumps(criteria_snapshot), run_id))
    finished = status in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED")
    conn.execute("UPDATE rfp_runs SET status=?, error_message=COALESCE(?, error_message), "
                 "completed_at=CASE WHEN ? THEN ? ELSE completed_at END WHERE rfp_run_id=?",
                 (status, error_message, int(finished), utc_now(), run_id))
    conn.commit()


def set_supplier_status(conn: sqlite3.Connection, run_id: str, supplier_name: str, status: str) -> None:
    conn.execute("UPDATE supplier_results SET status=? WHERE rfp_run_id=? AND supplier_name=?",
                 (status, run_id, supplier_name))
    conn.commit()


def save_run_results(conn: sqlite3.Connection, run: dict) -> None:
    """Persist every supplier result and the complete run JSON in one transaction (step 9)."""
    run_id = run["rfp_run_id"]
    with conn:
        for s in run.get("suppliers", []):
            conn.execute(
                "UPDATE supplier_results SET status='EVALUATED', absolute_score=?, ppi=?, final_rank=?, "
                "result_json=? WHERE rfp_run_id=? AND supplier_name=?",
                (s["absolute_score"], s["ppi"], s["final_rank"], json.dumps(s, ensure_ascii=False),
                 run_id, s["supplier_name"]))
        conn.execute(
            "UPDATE rfp_runs SET status=?, completed_at=?, criteria_snapshot=?, run_json=? WHERE rfp_run_id=?",
            (run["status"], utc_now(), json.dumps(run.get("criteria", [])),
             json.dumps(run, ensure_ascii=False), run_id))


save_run = save_run_results      # backwards-compatible name


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT r.rfp_run_id, r.created_at, r.status, r.llm_provider, r.llm_model, r.error_message, "
        "COUNT(s.id) AS suppliers FROM rfp_runs r LEFT JOIN supplier_results s ON s.rfp_run_id = r.rfp_run_id "
        "GROUP BY r.rfp_run_id ORDER BY r.created_at DESC, r.rfp_run_id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def get_supplier_rows(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT rfp_run_id, supplier_name, source_file, submission_date, experience_rating, status, "
        "absolute_score, ppi, final_rank FROM supplier_results WHERE rfp_run_id=? "
        "ORDER BY final_rank IS NULL, final_rank", (run_id,))
    return [dict(r) for r in rows]


def load_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """Return the complete stored run, or None. Runs saved by older versions are rebuilt from their rows."""
    from .ranking import build_leaderboard      # local import avoids a cycle

    row = conn.execute("SELECT * FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    if row["run_json"]:
        return json.loads(row["run_json"])
    meta = json.loads(row["run_meta_json"]) if "run_meta_json" in row.keys() and row["run_meta_json"] else {}
    suppliers = [json.loads(r["result_json"]) for r in conn.execute(
        "SELECT result_json FROM supplier_results WHERE rfp_run_id = ? AND result_json IS NOT NULL "
        "ORDER BY final_rank", (run_id,))]
    if not suppliers:
        return None
    return {"rfp_run_id": row["rfp_run_id"], "created_at": row["created_at"], "status": row["status"],
            "criteria": json.loads(row["criteria_snapshot"] or "[]"),
            "leaderboard": build_leaderboard(suppliers), "suppliers": suppliers, **meta}


get_run = load_run
