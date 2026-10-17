"""In-memory store for extraction results, keyed by document_id."""

from __future__ import annotations

from typing import Dict, Optional

from app.models import ExtractionResult

_store: Dict[str, ExtractionResult] = {}


def save(result: ExtractionResult) -> None:
    """Persist an extraction result."""
    _store[result.document.document_id] = result


def get(doc_id: str) -> Optional[ExtractionResult]:
    """Retrieve an extraction result by document id."""
    return _store.get(doc_id)


def clear() -> None:
    """Reset the store.  Useful for testing."""
    _store.clear()
