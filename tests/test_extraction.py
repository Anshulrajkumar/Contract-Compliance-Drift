"""Tests for the PDF extraction pipeline and API endpoints."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import registry, store
from app.config import get_policy_dir
from app.extractor import extract_document
from app.models import DocumentInfo, ExtractionStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global in-memory state between tests."""
    registry.reset_registry()
    store.clear()
    yield
    registry.reset_registry()
    store.clear()


@pytest.fixture()
def client():
    """FastAPI test client – triggers lifespan to discover documents."""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def policy_dir() -> Path:
    return get_policy_dir()


@pytest.fixture()
def discovered_docs(client) -> list[DocumentInfo]:
    """Return discovered documents via the API."""
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    return [DocumentInfo(**d) for d in resp.json()]


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "service" in body


# ---------------------------------------------------------------------------
# Document discovery
# ---------------------------------------------------------------------------

class TestDocumentDiscovery:
    def test_discovers_three_pdfs(self, discovered_docs):
        assert len(discovered_docs) == 3

    def test_all_documents_start_as_pending(self, discovered_docs):
        for doc in discovered_docs:
            assert doc.status == ExtractionStatus.PENDING

    def test_filenames_are_pdfs(self, discovered_docs):
        for doc in discovered_docs:
            assert doc.filename.endswith(".pdf")

    def test_sizes_are_positive(self, discovered_docs):
        for doc in discovered_docs:
            assert doc.size_bytes > 0

    def test_document_ids_are_unique(self, discovered_docs):
        ids = [d.document_id for d in discovered_docs]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Extraction – real PDFs
# ---------------------------------------------------------------------------

class TestExtractionRealPDFs:
    """Test extraction against the actual policy PDFs."""

    def test_extract_each_document(self, client, discovered_docs):
        for doc in discovered_docs:
            resp = client.post(f"/api/documents/{doc.document_id}/extract")
            assert resp.status_code == 200, f"Extraction failed for {doc.filename}"
            body = resp.json()
            result_doc = body["document"]
            # Must not silently succeed with zero pages.
            assert result_doc["total_pages"] is not None
            assert result_doc["total_pages"] > 0
            assert result_doc["status"] in (
                ExtractionStatus.EXTRACTED.value,
                ExtractionStatus.PARTIAL.value,
                ExtractionStatus.OCR_REQUIRED.value,
            )

    def test_extracted_pages_have_correct_metadata(self, client, discovered_docs):
        for doc in discovered_docs:
            client.post(f"/api/documents/{doc.document_id}/extract")
            resp = client.get(f"/api/documents/{doc.document_id}/pages")
            assert resp.status_code == 200
            pages = resp.json()
            assert len(pages) > 0
            for i, page in enumerate(pages):
                assert page["page_number"] == i + 1
                assert page["document_id"] == doc.document_id
                assert page["filename"] == doc.filename

    def test_page_numbers_are_sequential(self, client, discovered_docs):
        doc = discovered_docs[0]
        client.post(f"/api/documents/{doc.document_id}/extract")
        resp = client.get(f"/api/documents/{doc.document_id}/pages")
        pages = resp.json()
        numbers = [p["page_number"] for p in pages]
        assert numbers == list(range(1, len(numbers) + 1))

    def test_at_least_some_text_extracted(self, client, discovered_docs):
        """At least one of the three real PDFs should contain extractable text."""
        any_text = False
        for doc in discovered_docs:
            client.post(f"/api/documents/{doc.document_id}/extract")
            resp = client.get(f"/api/documents/{doc.document_id}/pages")
            for page in resp.json():
                if page["char_count"] > 0:
                    any_text = True
                    break
        assert any_text, "None of the 3 PDFs yielded any extractable text"

    def test_reextraction_works(self, client, discovered_docs):
        """Extracting the same document twice should succeed."""
        doc = discovered_docs[0]
        r1 = client.post(f"/api/documents/{doc.document_id}/extract")
        r2 = client.post(f"/api/documents/{doc.document_id}/extract")
        assert r1.status_code == 200
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Pages endpoint – edge cases
# ---------------------------------------------------------------------------

class TestPagesEndpoint:
    def test_pages_before_extraction_returns_400(self, client, discovered_docs):
        doc = discovered_docs[0]
        resp = client.get(f"/api/documents/{doc.document_id}/pages")
        assert resp.status_code == 400

    def test_pages_invalid_id_returns_404(self, client):
        resp = client.get("/api/documents/nonexistent-id/pages")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Extraction endpoint – error handling
# ---------------------------------------------------------------------------

class TestExtractionErrors:
    def test_invalid_document_id_returns_404(self, client):
        resp = client.post("/api/documents/does-not-exist/extract")
        assert resp.status_code == 404

    def test_missing_file_reports_failure(self, client):
        """Simulate a document whose file has been deleted."""
        # Manually register a document that points nowhere.
        from app import registry as reg
        fake_doc = DocumentInfo(
            document_id="fake-missing-abc",
            filename="ghost.pdf",
            size_bytes=0,
            status=ExtractionStatus.PENDING,
        )
        reg._registry["fake-missing-abc"] = fake_doc
        reg._paths["fake-missing-abc"] = Path("/nonexistent/ghost.pdf")

        resp = client.post("/api/documents/fake-missing-abc/extract")
        assert resp.status_code == 200  # Extraction runs but reports failure in body.
        body = resp.json()
        assert body["document"]["status"] == ExtractionStatus.FAILED.value
        assert body["document"]["error"] is not None


# ---------------------------------------------------------------------------
# Extractor unit tests – synthetic edge cases
# ---------------------------------------------------------------------------

class TestExtractorUnit:
    def test_corrupt_pdf(self, tmp_path):
        """A file that is not a valid PDF should result in FAILED status."""
        bad_pdf = tmp_path / "corrupt.pdf"
        bad_pdf.write_bytes(b"THIS IS NOT A PDF")
        doc = DocumentInfo(
            document_id="corrupt-test",
            filename="corrupt.pdf",
            size_bytes=len(bad_pdf.read_bytes()),
        )
        result = extract_document(doc, bad_pdf)
        assert result.document.status == ExtractionStatus.FAILED
        assert result.document.error is not None
        assert len(result.pages) == 0

    def test_empty_file(self, tmp_path):
        """A zero-byte file should result in FAILED status."""
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(b"")
        doc = DocumentInfo(
            document_id="empty-test",
            filename="empty.pdf",
            size_bytes=0,
        )
        result = extract_document(doc, empty)
        assert result.document.status == ExtractionStatus.FAILED
        assert result.document.error is not None

    def test_nonexistent_path(self, tmp_path):
        doc = DocumentInfo(
            document_id="missing-test",
            filename="nope.pdf",
            size_bytes=0,
        )
        result = extract_document(doc, tmp_path / "nope.pdf")
        assert result.document.status == ExtractionStatus.FAILED
        assert "not found" in (result.document.error or "").lower()


# ---------------------------------------------------------------------------
# Clauses endpoint
# ---------------------------------------------------------------------------

class TestClausesEndpoint:
    def test_clauses_before_extraction_returns_400(self, client, discovered_docs):
        doc = discovered_docs[0]
        resp = client.get(f"/api/documents/{doc.document_id}/clauses")
        assert resp.status_code == 400

    def test_clauses_after_extraction_returns_list(self, client, discovered_docs):
        doc = discovered_docs[0]
        client.post(f"/api/documents/{doc.document_id}/extract")
        resp = client.get(f"/api/documents/{doc.document_id}/clauses")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
