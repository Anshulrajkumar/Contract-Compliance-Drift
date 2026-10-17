"""Document inventory – discovers PDFs in the policy directory and assigns stable IDs."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional

from app.config import get_policy_dir
from app.models import DocumentInfo, ExtractionStatus

logger = logging.getLogger(__name__)

# In-memory registry keyed by document_id.
_registry: Dict[str, DocumentInfo] = {}
_paths: Dict[str, Path] = {}  # document_id -> filesystem path


def _make_id(filename: str) -> str:
    """Derive a short, URL-safe document id from the filename."""
    slug = filename.lower().replace(" ", "-")
    # Keep it short but unique enough for 3-10 documents.
    short_hash = hashlib.sha256(filename.encode()).hexdigest()[:8]
    # Use a simplified slug (first 40 chars) + hash.
    slug = slug[:40].rstrip("-")
    return f"{slug}-{short_hash}"


def discover_documents() -> Dict[str, DocumentInfo]:
    """Scan the policy directory and register every PDF found.

    Returns the current registry.  Safe to call repeatedly – it will
    add newly discovered files and keep existing extraction state.
    """
    policy_dir = get_policy_dir()
    if not policy_dir.is_dir():
        logger.warning("Policy directory does not exist: %s", policy_dir)
        return _registry

    for pdf_path in sorted(policy_dir.glob("*.pdf")):
        doc_id = _make_id(pdf_path.name)
        if doc_id not in _registry:
            _registry[doc_id] = DocumentInfo(
                document_id=doc_id,
                filename=pdf_path.name,
                size_bytes=pdf_path.stat().st_size,
                status=ExtractionStatus.PENDING,
            )
            _paths[doc_id] = pdf_path
            logger.info("Discovered document: %s  →  id=%s", pdf_path.name, doc_id)

    return _registry


def get_document(doc_id: str) -> Optional[DocumentInfo]:
    """Look up a document by its id."""
    return _registry.get(doc_id)


def get_document_path(doc_id: str) -> Optional[Path]:
    """Return the filesystem path for a document id."""
    return _paths.get(doc_id)


def update_document(doc: DocumentInfo) -> None:
    """Update the registry entry for a document."""
    _registry[doc.document_id] = doc


def list_documents() -> list[DocumentInfo]:
    """Return all registered documents."""
    return list(_registry.values())


def reset_registry() -> None:
    """Clear the in-memory registry.  Useful for testing."""
    _registry.clear()
    _paths.clear()
