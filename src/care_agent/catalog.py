"""Read-only lookups against the mock biomarker catalog (SQLite).

The catalog gives us marker metadata (domain, safety notes, interpretation
notes, reference ranges) that is *not* present in the bloodwork JSON. It is
used strictly as reference/lookup data -- we never write to it, and we never
use it to recompute a classification the dataset already supplies
(``kb_grounding_003``: don't relabel biomarkers unless the implementation
explicitly includes deterministic range logic and tests for it).
"""

from __future__ import annotations

import contextlib
import json as _json
import sqlite3
from pathlib import Path

from care_agent.models import CatalogEntry

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock_biomarker_catalog.sqlite"


class BiomarkerCatalog:
    def __init__(self, db_path: Path | str = DEFAULT_CATALOG_PATH):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Biomarker catalog not found at {self.db_path}")

    @contextlib.contextmanager
    def _connect(self):
        # NOTE: sqlite3.Connection's own context manager only wraps the
        # transaction (commit/rollback on exit) -- it does NOT close the
        # connection, which leaks a file handle per call. This wrapper
        # guarantees the connection is actually closed.
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def lookup(self, concept_id: str) -> CatalogEntry | None:
        """Look up a catalog entry by its canonical biomarker_name (concept_id)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT biomarker_name, display_name, unit, direction, domain_label,
                       importance, interpretation_notes, safety_notes, action_fields_json,
                       optimal_range_min, optimal_range_max, adequate_range_min, adequate_range_max
                FROM biomarker_catalog
                WHERE biomarker_name = ? AND is_active = 1
                """,
                (concept_id,),
            ).fetchone()
        if row is None:
            return None

        return CatalogEntry(
            biomarker_name=row["biomarker_name"],
            display_name=row["display_name"],
            unit=row["unit"],
            direction=row["direction"],
            domain_label=row["domain_label"],
            importance=row["importance"],
            interpretation_notes=row["interpretation_notes"],
            safety_notes=row["safety_notes"],
            action_fields=tuple(_json.loads(row["action_fields_json"] or "[]")),
            optimal_range_min=row["optimal_range_min"],
            optimal_range_max=row["optimal_range_max"],
            adequate_range_min=row["adequate_range_min"],
            adequate_range_max=row["adequate_range_max"],
        )

    def search_by_alias(self, text: str) -> CatalogEntry | None:
        """Resolve a free-text marker name (e.g. 'A1C', 'LDL') to a catalog entry."""
        normalized = text.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT biomarker_name FROM marker_alias WHERE normalized_alias = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return self.lookup(row["biomarker_name"])
