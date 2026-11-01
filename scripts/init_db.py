"""Create (or upgrade) the SQLite database and seed the sample evaluation criteria.

Usage:
    python scripts/init_db.py              # create tables, seed criteria if the table is empty
    python scripts/init_db.py --reset      # delete the criteria and re-seed the defaults
    python scripts/init_db.py --db other.db
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rfp import db  # noqa: E402
from rfp.config import DB_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite file path")
    parser.add_argument("--reset", action="store_true", help="re-seed the default criteria")
    args = parser.parse_args()
    with db.session(args.db) as conn:
        db.init_db(conn)
        db.seed_criteria(conn, force=args.reset)
        criteria = db.get_all_criteria(conn)
        active = [c for c in criteria if c["is_active"]]
    print(f"Database ready: {args.db}")
    print(f"{'ID':<4}{'Criterion':<26}{'Weight':>8}{'Max':>6}  Active")
    for c in criteria:
        print(f"{c['criterion_id']:<4}{c['name']:<26}{c['weight']:>7g}%{c['max_score']:>6g}  "
              f"{'yes' if c['is_active'] else 'no'}")
    print(f"Active weight total: {sum(c['weight'] for c in active):g}%")


if __name__ == "__main__":
    main()
