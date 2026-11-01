"""Run one complete evaluation on the bundled sample PDFs from the command line.

Usage:
    python scripts/run_sample.py                                  # offline demo scorer (no API key)
    python scripts/run_sample.py --provider anthropic             # needs ANTHROPIC_API_KEY
    python scripts/run_sample.py --provider openai                # needs OPENAI_API_KEY (+ OPENAI_BASE_URL)
    python scripts/run_sample.py --inject-fault "NexaWorks"       # validation demo (repeat for more suppliers)
    python scripts/run_sample.py --out sample_output/sample_run.json

Uses a throw-away database unless --db is given, and prints the leaderboard and any warnings.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rfp import db, llm  # noqa: E402
from rfp.evaluation_agent import EvaluationAgent  # noqa: E402
from rfp.orchestrator import Orchestrator  # noqa: E402
from rfp.samples import sample_inputs  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["offline", "anthropic", "openai"], default="offline")
    parser.add_argument("--model", default=None)
    parser.add_argument("--db", default=None, help="database to use (default: a temporary one)")
    parser.add_argument("--inject-fault", action="append", default=[], metavar="SUPPLIER")
    parser.add_argument("--no-edge-case", action="store_true", help="skip the sparse fifth sample")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    client = llm.build_client("mock" if args.provider == "offline" else args.provider, args.model)
    if args.inject_fault:
        client = llm.FaultInjectingClient(client, args.inject_fault)
    print(f"Provider: {client.provider} / {client.model}")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(args.db) if args.db else Path(tmp) / "run_sample.db"
        db.bootstrap(path)
        with db.session(path) as conn:
            run = Orchestrator(conn, EvaluationAgent(client),
                               lambda f, m: print(f"[{f:4.0%}] {m}")).run(sample_inputs(not args.no_edge_case))

    out = Path(args.out) if args.out else ROOT / "sample_output" / f"{run['rfp_run_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nRFP_RUN_ID: {run['rfp_run_id']}  status {run['status']}")
    print(f"{'Rank':<6}{'Supplier':<20}{'Absolute':>9}{'PPI':>9}  {'Submitted':<12}{'Exp':>5}")
    for s in run["suppliers"]:
        print(f"{s['final_rank']:<6}{s['supplier_name']:<20}{s['absolute_score']:>9.2f}{s['ppi']:>9.2f}  "
              f"{s['submission_date']:<12}{s['experience_rating']:>5g}")
    print(f"\nWarnings: {len(run['warnings'])}")
    for warning in run["warnings"]:
        print(f"  - {warning}")
    print(f"\nJSON written to {out}")


if __name__ == "__main__":
    main()
