"""Central configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

APP_VERSION = "2.0.0"
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("RFP_DB_PATH", BASE_DIR / "data" / "rfp_evaluator.db"))
SAMPLE_DIR = BASE_DIR / "sample_rfps"

# --- documents
MAX_DOC_CHARS = 60_000          # text sent to the LLM per supplier (truncated beyond this)
MIN_TEXT_CHARS = 200            # below this a PDF is treated as empty / scanned
MAX_PDF_BYTES = 10 * 1024 * 1024

# --- input rules
MIN_SUPPLIERS = 2               # peer benchmarking needs peers
EXPERIENCE_MIN, EXPERIENCE_MAX = 0.0, 10.0   # historical experience rating scale

# --- LLM behaviour
LLM_TEMPERATURE = 0
LLM_MAX_RETRIES = 2             # extra attempts after a failed call (network, auth, rate limit)
LLM_BACKOFF_SECONDS = 2.0       # waits backoff * attempt between retries

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4o-mini",
}
