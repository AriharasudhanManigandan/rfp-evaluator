"""Database creation / seed script (same as scripts/init_db.py).

    python db/seed.py             create tables, seed the 5 criteria if the table is empty
    python db/seed.py --reset     restore the default criteria
    python db/seed.py --db other.db
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "scripts" / "init_db.py"), run_name="__main__")
