"""Agentic RFP Evaluation & Supplier Ranking.

One module per component of the brief:

* ``db``                - SQLite persistence (criteria, runs, supplier results, migrations)
* ``pdf_tool``          - Document Tool: PDF -> clean text (PyMuPDF, pypdf fallback)
* ``evaluation_agent``  - Evaluation Agent: dynamic prompt, retry, JSON self-repair
* ``llm``               - provider adapters (Anthropic, OpenAI-compatible, offline demo, fault injector)
* ``validation``        - Validation Tool: Pydantic models + normalisation rules V1-V10
* ``ranking``           - Ranking Tool: deterministic scores, benchmarks, PPI, tie-breaks
* ``orchestrator``      - Orchestrator Agent: the ten-step workflow and its trace
"""
from .config import APP_VERSION as __version__  # noqa: F401
