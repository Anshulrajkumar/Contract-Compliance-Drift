"""FastAPI application – Contract Compliance Drift & Redline Drafter."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app import registry, store
from app.extractor import extract_document
from app.models import (
    AnalysisListItem,
    AnalysisStatus,
    ComparisonAnalysis,
    ComplianceFinding,
    ComparisonRequest,
    DocumentInfo,
    ExtractionResult,
    ExtractionStatus,
    ExtractedPage,
    ReviewRequest,
    ReviewResponse,
    RedlineArtifact,
    RedlineRequest,
    SavedAnalysis,
)
from app.comparison import compare, configured_provider
from app import database
from app.config import get_artifact_dir, get_cors_origins
from app.redline import eligible_findings, generate as generate_redline
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan – discover documents at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Discover policy documents when the application starts."""
    docs = registry.discover_documents()
    database.initialize()
    logger.info("Discovered %d document(s) in policy directory", len(docs))
    yield  # Application runs here.


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Contract Compliance Drift & Redline Drafter",
    version="0.1.0",
    description="AI-powered contract compliance analysis and redline generation.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_document(document_id: str) -> DocumentInfo:
    """Validate and return a DocumentInfo, or raise 404."""
    doc = registry.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Unknown document_id: {document_id}")
    return doc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["system"])
async def health() -> dict:
    """Simple health-check."""
    return {"status": "ok", "service": "contract-compliance-drafter"}


@app.get("/api/documents", response_model=list[DocumentInfo], tags=["documents"])
async def list_documents() -> list[DocumentInfo]:
    """List all discovered documents and their processing status."""
    return registry.list_documents()


@app.get("/api/documents/{document_id}", response_model=DocumentInfo, tags=["documents"])
async def get_document(document_id: str) -> DocumentInfo:
    """Return discovered metadata only; callers cannot supply file paths."""
    return _resolve_document(document_id)


@app.post(
    "/api/documents/{document_id}/extract",
    response_model=ExtractionResult,
    tags=["documents"],
)
async def extract(document_id: str) -> ExtractionResult:
    """Extract text from the specified document.

    Re-running extraction on an already-extracted document will re-process
    it (useful during development).
    """
    doc = _resolve_document(document_id)
    pdf_path = registry.get_document_path(document_id)
    if pdf_path is None:
        raise HTTPException(status_code=500, detail="Document path not found in registry")

    result = extract_document(doc, pdf_path)
    registry.update_document(result.document)
    store.save(result)
    return result


@app.get(
    "/api/documents/{document_id}/pages",
    response_model=list[ExtractedPage],
    tags=["documents"],
)
async def get_pages(document_id: str) -> list[ExtractedPage]:
    """Return extracted pages for a document.

    The document must have been extracted first via the extract endpoint.
    """
    _resolve_document(document_id)
    result = store.get(document_id)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Document has not been extracted yet.  POST to /api/documents/{document_id}/extract first.",
        )
    return result.pages


@app.get(
    "/api/documents/{document_id}/clauses",
    response_model=list,
    tags=["documents"],
)
async def get_clauses(document_id: str):
    """Return detected clauses for a document (best-effort)."""
    _resolve_document(document_id)
    result = store.get(document_id)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Document has not been extracted yet.",
        )
    return result.clauses


@app.post("/api/comparisons", response_model=ComparisonAnalysis, tags=["comparisons"])
async def create_comparison(request: ComparisonRequest) -> ComparisonAnalysis:
    """Compare a policy against a contract using cached or fresh extraction.

    Only documents registered from the policy inventory can be supplied.
    """
    policy_doc = _resolve_document(request.policy_document_id)
    contract_doc = _resolve_document(request.contract_document_id)
    if request.policy_document_id == request.contract_document_id:
        raise HTTPException(status_code=422, detail="Policy and contract must be different documents")

    def result_for(doc: DocumentInfo):
        result = store.get(doc.document_id)
        if result is None:
            path = registry.get_document_path(doc.document_id)
            if path is None:
                raise HTTPException(status_code=500, detail="Document path not found in registry")
            result = extract_document(doc, path)
            registry.update_document(result.document)
            store.save(result)
        if result.document.status == ExtractionStatus.FAILED:
            raise HTTPException(status_code=422, detail=f"Cannot compare failed extraction: {result.document.filename}")
        return result

    return compare(result_for(policy_doc), result_for(contract_doc), configured_provider())


def _extraction_for(doc: DocumentInfo):
    """Return a cached extraction or perform it once, with no path exposed."""
    result = store.get(doc.document_id)
    if result is None:
        path = registry.get_document_path(doc.document_id)
        if path is None:
            raise HTTPException(status_code=500, detail="Document path not found in registry")
        result = extract_document(doc, path)
        registry.update_document(result.document)
        store.save(result)
    if result.document.status == ExtractionStatus.FAILED:
        raise HTTPException(status_code=422, detail=f"Cannot compare failed extraction: {result.document.filename}")
    return result


@app.post("/api/analyses", response_model=SavedAnalysis, status_code=201, tags=["analyses"])
async def create_analysis(request: ComparisonRequest) -> SavedAnalysis:
    """Persist, run, and persist a synchronous comparison workflow."""
    policy_doc = _resolve_document(request.policy_document_id)
    contract_doc = _resolve_document(request.contract_document_id)
    if policy_doc.document_id == contract_doc.document_id:
        raise HTTPException(status_code=422, detail="Policy and contract must be different documents")
    analysis_id = hashlib.sha256(f"{policy_doc.document_id}:{contract_doc.document_id}".encode()).hexdigest()[:16]
    try:
        database.create_pending(analysis_id, policy_doc.document_id, contract_doc.document_id, policy_doc.filename, contract_doc.filename)
        database.mark_running(analysis_id)
        analysis = compare(_extraction_for(policy_doc), _extraction_for(contract_doc), configured_provider())
        database.save_completed(analysis)
    except HTTPException as exc:
        database.mark_failed(analysis_id, str(exc.detail))
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        logger.exception("Analysis failed: %s", analysis_id)
        database.mark_failed(analysis_id, "Analysis could not be completed. Please retry after reviewing server configuration.")
        raise HTTPException(status_code=500, detail="Analysis could not be completed")
    saved = database.get_analysis(analysis_id)
    if saved is None:
        raise HTTPException(status_code=500, detail="Analysis persistence failed")
    return saved


@app.get("/api/analyses", response_model=list[AnalysisListItem], tags=["analyses"])
async def list_analyses() -> list[AnalysisListItem]:
    return database.list_analyses()


@app.get("/api/analyses/{analysis_id}", response_model=SavedAnalysis, tags=["analyses"])
async def get_analysis(analysis_id: str) -> SavedAnalysis:
    analysis = database.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Unknown analysis_id")
    return analysis


@app.get("/api/analyses/{analysis_id}/findings", response_model=list[ComplianceFinding], tags=["analyses"])
async def get_findings(analysis_id: str, status: str | None = None, risk_level: str | None = None) -> list[ComplianceFinding]:
    analysis = database.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Unknown analysis_id")
    allowed_status = set(ComplianceFinding.model_fields["status"].annotation.__args__)
    allowed_risk = {"low", "medium", "high"}
    if status is not None and status not in allowed_status:
        raise HTTPException(status_code=422, detail="Invalid finding status filter")
    if risk_level is not None and risk_level not in allowed_risk:
        raise HTTPException(status_code=422, detail="Invalid risk_level filter")
    return [f for f in analysis.findings if (status is None or f.status == status) and (risk_level is None or f.risk_level == risk_level)]


@app.patch("/api/analyses/{analysis_id}/findings/{finding_id}/review", response_model=ReviewResponse, tags=["reviews"])
async def review_finding(analysis_id: str, finding_id: str, request: ReviewRequest) -> ReviewResponse:
    review = database.save_review(analysis_id, finding_id, request)
    if review is None:
        # Do not reveal whether a finding exists in a different analysis.
        raise HTTPException(status_code=404, detail="Unknown analysis_id or finding_id")
    return review


@app.post("/api/analyses/{analysis_id}/redlines", response_model=RedlineArtifact, status_code=201, tags=["redlines"])
async def create_redline(analysis_id: str, request: RedlineRequest) -> RedlineArtifact:
    """Generate a visual-redline DOCX from validated, persisted proposals only."""
    analysis = database.get_analysis(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Unknown analysis_id")
    try:
        findings = eligible_findings(analysis, request.finding_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not findings:
        raise HTTPException(status_code=422, detail="No selected findings have a valid proposed revision")
    try:
        artifact_id = uuid.uuid4().hex
        storage_name = f"{artifact_id}.docx"
        output_dir = get_artifact_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        target = (output_dir / storage_name).resolve()
        if target.parent != output_dir.resolve():
            raise RuntimeError("Invalid artifact storage path")
        target.write_bytes(generate_redline(analysis, findings))
        return database.save_artifact(artifact_id, analysis_id, f"redline-draft-{analysis_id}.docx", storage_name, [finding.finding_id for finding in findings])
    except HTTPException:
        raise
    except Exception:
        logger.exception("Redline generation failed for analysis %s", analysis_id)
        raise HTTPException(status_code=500, detail="Redline draft could not be generated")


@app.get("/api/analyses/{analysis_id}/redlines", response_model=list[RedlineArtifact], tags=["redlines"])
async def list_redlines(analysis_id: str) -> list[RedlineArtifact]:
    if database.get_analysis(analysis_id) is None:
        raise HTTPException(status_code=404, detail="Unknown analysis_id")
    return database.list_artifacts(analysis_id)


@app.get("/api/redlines/{artifact_id}/download", tags=["redlines"])
async def download_redline(artifact_id: str):
    stored = database.get_artifact(artifact_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Unknown artifact_id")
    artifact, storage_name = stored
    base = get_artifact_dir().resolve()
    path = (base / storage_name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(status_code=404, detail="Generated artifact is unavailable")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=artifact.filename)
