"""Streamlit UI for the Agentic RFP Evaluation & Supplier Ranking mini project."""
from __future__ import annotations

import json
import os
from datetime import date, datetime

import pandas as pd
import streamlit as st

from rfp import db, llm
from rfp.config import EXPERIENCE_MAX, EXPERIENCE_MIN
from rfp.evaluation_agent import EvaluationAgent
from rfp.orchestrator import (InputValidationError, Orchestrator, RunFailedError, SupplierInput,
                              validate_inputs)
from rfp.pdf_tool import DocumentError, extract_text
from rfp.ranking import TIE_BREAK_RULES
from rfp.samples import load_sample_metadata

st.set_page_config(page_title="Agentic RFP Evaluator", page_icon="📑", layout="wide")


# --------------------------------------------------------------------------- helpers
@st.cache_resource
def bootstrap() -> bool:
    db.bootstrap()          # create / upgrade tables and seed the sample criteria on first start
    return True


bootstrap()


def secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:       # no secrets file configured
        pass
    return os.environ.get(name, default)


def friendly_name(file_name: str) -> str:
    """'apex_systems_rfp_response.pdf' -> 'Apex Systems'."""
    stem = file_name.rsplit(".", 1)[0].replace("-", "_").replace(" ", "_")
    words = [w for w in stem.split("_") if w and not w.isdigit()
             and w.casefold() not in {"rfp", "proposal", "response", "submission", "final", "v1", "v2"}]
    return " ".join(w.capitalize() for w in words) or stem


def as_date(value) -> date | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def clean_criteria_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for r in df.to_dict("records"):
        rows.append({
            "criterion_id": None if pd.isna(r.get("criterion_id")) else int(r["criterion_id"]),
            "name": "" if pd.isna(r.get("name")) else str(r["name"]),
            "description": "" if pd.isna(r.get("description")) else str(r["description"]),
            "weight": 0 if pd.isna(r.get("weight")) else r["weight"],
            "max_score": 10 if pd.isna(r.get("max_score")) else r["max_score"],
            "is_active": 0 if pd.isna(r.get("is_active")) else int(bool(r["is_active"])),
        })
    return rows


@st.cache_data(show_spinner=False)
def check_document(file_name: str, data: bytes) -> dict:
    try:
        doc = extract_text(data, file_name)
        return {"file": file_name, "pages": doc.pages, "characters": doc.chars, "extractor": doc.extractor,
                "status": "✅ readable" + (" (truncated)" if doc.truncated else ""), "error": None}
    except DocumentError as exc:
        return {"file": file_name, "pages": None, "characters": 0, "extractor": "-",
                "status": "❌ unreadable", "error": str(exc)}


with db.session() as _conn:
    criteria_all = db.get_all_criteria(_conn)
    criteria_active = db.get_active_criteria(_conn)
    stored_runs = db.list_runs(_conn)

if "result" not in st.session_state:
    st.session_state.result = None

# --------------------------------------------------------------------------- sidebar: AI layer
with st.sidebar:
    st.header("AI layer")
    choice = st.radio(
        "LLM provider", ["Anthropic (Claude)", "OpenAI-compatible", "Offline demo (no API key)"],
        index=0 if secret("ANTHROPIC_API_KEY") else (1 if secret("OPENAI_API_KEY") else 2),
        help="The offline demo is a keyword scorer so the workflow runs without a key. "
             "Use a real LLM for genuine qualitative scoring.")
    client = None
    try:
        if choice.startswith("Anthropic"):
            key = st.text_input("Anthropic API key", value=secret("ANTHROPIC_API_KEY"), type="password")
            model = st.text_input("Model", value=secret("ANTHROPIC_MODEL", llm.DEFAULT_MODELS["anthropic"]))
            client = llm.build_client("anthropic", model, key) if key else None
        elif choice.startswith("OpenAI"):
            key = st.text_input("API key", value=secret("OPENAI_API_KEY"), type="password")
            model = st.text_input("Model", value=secret("OPENAI_MODEL", llm.DEFAULT_MODELS["openai"]))
            base_url = st.text_input("Base URL (optional)", value=secret("OPENAI_BASE_URL"),
                                     help="Blank for OpenAI. Any OpenAI-compatible endpoint works "
                                          "(Groq, Together, Ollama, Gemini's OpenAI endpoint ...).")
            client = llm.build_client("openai", model, key, base_url or None) if key else None
        else:
            client = llm.MockClient()
            st.caption("Scores come from a keyword heuristic, not an LLM. Validation, scoring, benchmarking, "
                       "ranking and persistence are identical.")
    except llm.LLMConfigError as exc:
        st.error(str(exc))
    if client is None:
        st.warning("Enter an API key, or choose the offline demo.")
    else:
        st.success(f"Provider: {client.provider} · {client.model}")
    st.divider()
    st.caption("Temperature 0 · JSON-only prompt · evidence-grounded · one call per supplier, with retry and "
               "one JSON self-repair round.")

st.title("📑 Agentic RFP Evaluation & Supplier Ranking")
st.caption("The LLM judges proposal content. Python validates, scores, benchmarks, breaks ties and ranks.")

tab_criteria, tab_input, tab_board, tab_card, tab_run, tab_hist = st.tabs(
    ["1 · Criteria", "2 · Suppliers & Evaluate", "3 · Leaderboard", "4 · Scorecards", "5 · Run details",
     "History"])

# --------------------------------------------------------------------------- 1. criteria
with tab_criteria:
    st.subheader("Evaluation criteria (stored in SQLite)")
    st.caption("Activate, deactivate or re-weight criteria here. The prompt is built from this table when a run "
               "starts, so no code change is needed. Active weights must total 100%.")
    total = sum(c["weight"] for c in criteria_active)
    ok = abs(total - 100) < 0.01
    m1, m2, m3 = st.columns(3)
    m1.metric("Active criteria", len(criteria_active))
    m2.metric("Active weight total", f"{total:g}%", delta="OK" if ok else "not 100%",
              delta_color="normal" if ok else "inverse")
    m3.metric("Max score scale", ", ".join(sorted({f"0-{c['max_score']:g}" for c in criteria_active})) or "-")

    editor_df = pd.DataFrame(criteria_all)[["criterion_id", "name", "description", "weight", "max_score", "is_active"]]
    editor_df["is_active"] = editor_df["is_active"].astype(bool)
    edited = st.data_editor(
        editor_df, num_rows="dynamic", hide_index=True, width="stretch", key="criteria_editor",
        column_config={
            "criterion_id": st.column_config.NumberColumn("ID", disabled=True),
            "name": st.column_config.TextColumn("Criterion", required=True),
            "description": st.column_config.TextColumn("What the LLM should inspect", width="large"),
            "weight": st.column_config.NumberColumn("Weight %", min_value=0, max_value=100, step=1),
            "max_score": st.column_config.NumberColumn("Max score", min_value=1, step=1),
            "is_active": st.column_config.CheckboxColumn("Active"),
        })
    pending = [r for r in clean_criteria_rows(edited) if r["name"].strip()]
    problems = db.validate_criteria(pending)
    save_col, reset_col, _ = st.columns([1, 1, 4])
    if save_col.button("💾 Save criteria", type="primary", disabled=bool(problems)):
        with db.session() as conn:
            db.update_criteria(conn, pending)
        st.success("Criteria saved. New runs will use them.")
        st.rerun()
    if reset_col.button("↺ Reset to defaults"):
        with db.session() as conn:
            db.seed_criteria(conn, force=True)
        st.rerun()
    for problem in problems:
        st.error(problem)
    st.caption("Criteria are never deleted, only deactivated, so earlier runs stay traceable. "
               "Every run stores a snapshot of the criteria it used.")

# --------------------------------------------------------------------------- 2. suppliers + evaluate
with tab_input:
    st.subheader("Supplier proposals")
    samples = load_sample_metadata()
    use_samples = st.toggle(f"Use the {len(samples)} bundled synthetic RFP PDFs", value=False,
                            help="Loads the sample proposals from sample_rfps/ so you can demo without uploading.")
    files: list[tuple[str, bytes]] = []
    sample_meta = {m["file"]: m for m in samples}
    if use_samples:
        from rfp.config import SAMPLE_DIR
        files = [(m["file"], (SAMPLE_DIR / m["file"]).read_bytes()) for m in samples]
        st.info(f"{len(files)} sample PDFs loaded (the last one is an intentionally sparse edge case).")
    else:
        uploads = st.file_uploader("Upload supplier RFP responses (PDF)", type=["pdf"], accept_multiple_files=True)
        files = [(u.name, u.getvalue()) for u in uploads or []]

    submissions: list[SupplierInput] = []
    if files:
        st.markdown("**Supplier metadata** - edit the cells below")
        meta_rows = []
        for fname, _ in files:
            m = sample_meta.get(fname)
            meta_rows.append({"file": fname, "supplier_name": m["supplier_name"] if m else friendly_name(fname),
                              "submission_date": m["submission_date"] if m else date.today(),
                              "experience_rating": m["experience_rating"] if m else 5.0})
        meta_df = st.data_editor(
            pd.DataFrame(meta_rows), hide_index=True, width="stretch", key=f"meta_{'|'.join(f for f, _ in files)}",
            column_config={
                "file": st.column_config.TextColumn("PDF file", disabled=True),
                "supplier_name": st.column_config.TextColumn("Supplier name", required=True),
                "submission_date": st.column_config.DateColumn("Submission date", format="YYYY-MM-DD", required=True),
                "experience_rating": st.column_config.NumberColumn(
                    f"Historical experience ({EXPERIENCE_MIN:g}-{EXPERIENCE_MAX:g})", min_value=EXPERIENCE_MIN,
                    max_value=EXPERIENCE_MAX, step=0.5, required=True)})
        by_file = dict(files)
        for r in meta_df.to_dict("records"):
            submissions.append(SupplierInput(
                supplier_name="" if pd.isna(r["supplier_name"]) else str(r["supplier_name"]).strip(),
                filename=r["file"], pdf_bytes=by_file[r["file"]], submission_date=as_date(r["submission_date"]),
                experience_rating=r["experience_rating"]))

        problems = validate_inputs(submissions)
        doc_rows = [check_document(s.filename, s.pdf_bytes) for s in submissions]
        problems += [d["error"] for d in doc_rows if d["error"]]
        with st.expander("Document check"):
            st.dataframe(pd.DataFrame(doc_rows).drop(columns=["error"]), hide_index=True, width="stretch")
        for problem in problems:
            st.error(problem)
        if not problems:
            st.success(f"{len(submissions)} suppliers ready for evaluation.")

        with st.expander("🧪 Validation demo - simulate a malformed LLM reply"):
            st.caption("Damages the model's reply for the chosen suppliers: score above the maximum, a non-numeric "
                       "score, a missing evidence quote, a dropped criterion, an invented criterion id, a renamed "
                       "supplier and markdown fences. The Validation Tool must repair it and record warnings.")
            faulty = st.multiselect("Corrupt the reply for", [s.supplier_name for s in submissions if s.supplier_name])

        criteria_problems = db.validate_criteria([{**c, "is_active": 1} for c in criteria_active])
        for problem in criteria_problems:
            st.error(f"Criteria: {problem} Fix them on tab 1 before evaluating.")
        if st.button("🚀 Evaluate suppliers", type="primary",
                     disabled=bool(problems) or client is None or bool(criteria_problems)):
            active_client = llm.FaultInjectingClient(client, faulty) if faulty else client
            bar = st.progress(0.0, text="Starting")
            try:
                with st.status("Running agentic workflow…", expanded=True) as status:
                    def on_progress(fraction: float, message: str) -> None:
                        bar.progress(min(fraction, 1.0), text=message)
                        status.write(message)
                    with db.session() as conn:
                        st.session_state.result = Orchestrator(
                            conn, EvaluationAgent(active_client), on_progress).run(submissions)
                    status.update(label="Evaluation complete", state="complete")
                run = st.session_state.result
                st.success(f"Run {run['rfp_run_id']} finished with {len(run['warnings'])} validation warning(s). "
                           "Open the Leaderboard tab.")
            except InputValidationError as exc:
                for message in exc.errors:
                    st.error(message)
            except RunFailedError as exc:
                st.error(f"Run {exc.run_id} failed and was stored as FAILED: {exc}")
            except Exception as exc:     # unexpected - keep the app alive and show the reason
                st.exception(exc)
    else:
        st.info("Upload two or more supplier PDFs, or switch on the bundled samples.")

result = st.session_state.result


def need_result() -> bool:
    if not result:
        st.info("No run loaded yet. Evaluate suppliers on tab 2, or load a past run from History.")
        return False
    return True


def warnings_of(s: dict) -> list[str]:
    return s.get("validation_warnings", s.get("warnings", []))


# --------------------------------------------------------------------------- 3. leaderboard
with tab_board:
    if need_result():
        suppliers = result["suppliers"]
        st.subheader(f"Leaderboard · {result['rfp_run_id']}")
        for col, s, medal in zip(st.columns(min(3, len(suppliers))), suppliers, ["🥇", "🥈", "🥉"]):
            col.metric(f"{medal} {s['supplier_name']}", f"PPI {s['ppi']:.2f}")
            col.caption(f"Absolute score {s['absolute_score']:.2f} · submitted {s['submission_date']}")
        board = pd.DataFrame([{
            "Rank": s["final_rank"], "Supplier": s["supplier_name"], "Absolute score": s["absolute_score"],
            "PPI": s["ppi"], "Submission date": s["submission_date"], "Experience rating": s["experience_rating"],
            "Warnings": len(warnings_of(s)), "Why this position": s.get("tie_break_explanation", "")}
            for s in suppliers])
        st.dataframe(board, hide_index=True, width="stretch", column_config={
            "Absolute score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.2f"),
            "PPI": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.2f"),
            "Why this position": st.column_config.TextColumn(width="large")})
        st.markdown("**Absolute score vs Peer Performance Index**")
        st.bar_chart(board.set_index("Supplier")[["Absolute score", "PPI"]], stack=False, sort=False,
                     height=300, horizontal=True)
        st.markdown("**Peer comparison - relative performance % by criterion**")
        matrix = pd.DataFrame({s["supplier_name"]: {a["name"]: a["relative_pct"] for a in s["criteria_analysis"]}
                               for s in suppliers}).T
        st.dataframe(matrix, width="stretch", column_config={
            name: st.column_config.ProgressColumn(name, min_value=0, max_value=100, format="%.0f%%")
            for name in matrix.columns})

# --------------------------------------------------------------------------- 4. scorecards
with tab_card:
    if need_result():
        labels = [f"#{s['final_rank']} {s['supplier_name']}" for s in result["suppliers"]]
        s = result["suppliers"][labels.index(st.selectbox("Supplier", labels))]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Final rank", s["final_rank"])
        k2.metric("Absolute score", f"{s['absolute_score']:.2f}")
        k3.metric("PPI", f"{s['ppi']:.2f}")
        k4.metric("Validation warnings", len(warnings_of(s)))
        if s.get("tie_break_explanation"):
            st.caption(s["tie_break_explanation"])
        card = pd.DataFrame([{
            "Criterion": a["name"], "Weight %": a["weight"], "Score": a["score"], "Max": a["max_score"],
            "Benchmark": a["benchmark"], "Gap": a["gap"], "Relative %": a["relative_pct"],
            "Weighted pts": a["weighted_contribution"],
            "Flags": " ".join(label for label, on in [("🏆 leader", a.get("is_benchmark_leader")),
                                                       ("⚠️ imputed", a.get("imputed")),
                                                       ("❔ no evidence", a.get("evidence_missing"))] if on)}
            for a in s["criteria_analysis"]])
        st.dataframe(card, hide_index=True, width="stretch", column_config={
            "Relative %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%")})
        parts = " + ".join(format(a["weighted_contribution"], "g") for a in s["criteria_analysis"])
        st.caption(f"Absolute = Σ(score/max × weight) = {parts} = {s['absolute_score']:.2f}   ·   "
                   f"PPI = Σ(relative% × weight)/Σweight = {s['ppi']:.2f}")

        st.markdown("#### Evidence and justification")
        for a in s["criteria_analysis"]:
            with st.expander(f"{a['name']} - {a['score']:g}/{a['max_score']:g}  (weight {a['weight']:g}%)"):
                st.markdown(f"**Justification:** {a['justification']}")
                if a["evidence"]:
                    st.markdown(f"**Evidence from the proposal:**\n\n> {a['evidence']}")
                else:
                    st.warning("No supporting evidence quote was returned for this criterion.")
        left, right = st.columns(2)
        with left:
            st.markdown("#### Risks identified")
            for risk in s["risks"] or ["None reported."]:
                st.markdown(f"- {risk}")
        with right:
            st.markdown("#### Overall summary")
            st.write(s["overall_summary"] or "-")
        if warnings_of(s):
            st.markdown("#### Validation warnings")
            for warning in warnings_of(s):
                st.warning(warning)
        doc = s.get("source_document")
        if doc:
            st.caption(f"Source: {doc['file_name']} · {doc['pages']} page(s) · {doc['chars']:,} characters via "
                       f"{doc.get('extractor', '?')} · sha256 {doc['sha256'][:16]}…"
                       + (" · truncated for the prompt" if doc.get("truncated") else ""))

# --------------------------------------------------------------------------- 5. run details
with tab_run:
    if need_result():
        st.subheader("Run details")
        llm_info = result.get("llm", {})
        st.markdown(f"**RFP_RUN_ID:** `{result['rfp_run_id']}` &nbsp; **Status:** {result['status']} &nbsp; "
                    f"**LLM:** {llm_info.get('provider', '?')}")
        st.caption(f"Model: {llm_info.get('model', '?')} · created {result['created_at']} · "
                   f"{len(result['suppliers'])} suppliers · {len(result['criteria'])} active criteria")
        st.download_button("⬇️ Download complete result (JSON)",
                           data=json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"),
                           file_name=f"{result['rfp_run_id']}.json", mime="application/json", type="primary")

        st.markdown("#### Tie-break rules (applied in order, then ranks 1…n)")
        st.markdown("  \n".join(TIE_BREAK_RULES))
        tie_break = result.get("tie_break", {})
        for note in tie_break.get("ties_resolved", []):
            st.info(note)
        if not tie_break.get("ties_resolved"):
            st.caption("No PPI ties in this run - Rule 1 decided every position.")
        st.dataframe(pd.DataFrame([{"Rank": s["final_rank"], "Supplier": s["supplier_name"],
                                    "Explanation": s.get("tie_break_explanation", "")}
                                   for s in result["suppliers"]]), hide_index=True, width="stretch")

        st.markdown("#### Benchmarks per criterion")
        benchmarks = result.get("benchmarks", [])
        if isinstance(benchmarks, list):
            st.dataframe(pd.DataFrame([{
                "Criterion": b["name"], "Benchmark score": b["benchmark_score"], "Max": b["max_score"],
                "Leader(s)": ", ".join(b["leaders"]), "Zero benchmark": b["benchmark_zero"]} for b in benchmarks]),
                hide_index=True, width="stretch")
        else:                                           # run saved by an earlier version
            st.json(benchmarks)

        st.markdown(f"#### Validation warnings ({len(result['warnings'])})")
        if result["warnings"]:
            for warning in result["warnings"]:
                st.warning(warning)
        else:
            st.success("The LLM output passed validation without any normalisation.")

        st.markdown("#### Agent trace")
        if result.get("trace"):
            st.dataframe(pd.DataFrame(result["trace"]), hide_index=True, width="stretch")
        with st.expander("Formulas used"):
            st.json(result.get("formulas", {}))
        with st.expander("Criteria snapshot used by this run"):
            st.dataframe(pd.DataFrame(result["criteria"]), hide_index=True, width="stretch")

# --------------------------------------------------------------------------- history
with tab_hist:
    st.subheader("Stored runs")
    if not stored_runs:
        st.info("No runs stored yet.")
    else:
        st.dataframe(pd.DataFrame(stored_runs), hide_index=True, width="stretch")
        loadable = [r["rfp_run_id"] for r in stored_runs if r["status"].startswith("COMPLETED")]
        if loadable:
            picked = st.selectbox("Load a completed run", loadable)
            if st.button("Load run"):
                with db.session() as conn:
                    st.session_state.result = db.load_run(conn, picked)
                st.rerun()
            with db.session() as conn:
                rows = db.get_supplier_rows(conn, picked)
            st.caption("supplier_results rows for this run")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
