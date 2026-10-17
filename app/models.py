"""Pydantic models for PDF extraction results and API responses."""

from __future__ import annotations

from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ExtractionStatus(str, Enum):
    """Processing status for a document."""
    PENDING = "pending"
    EXTRACTED = "extracted"
    PARTIAL = "partial"          # Some pages extracted, others failed / empty
    FAILED = "failed"
    OCR_REQUIRED = "ocr_required"


# ---------------------------------------------------------------------------
# Page-level models
# ---------------------------------------------------------------------------

class ExtractedPage(BaseModel):
    """Text content extracted from a single PDF page."""
    document_id: str = Field(..., description="Stable identifier derived from the filename")
    filename: str = Field(..., description="Original PDF filename on disk")
    page_number: int = Field(..., ge=1, description="1-based page number")
    text: str = Field("", description="Extracted text content for this page")
    char_count: int = Field(0, description="Number of characters extracted")
    ocr_recommended: bool = Field(
        False,
        description="True when the page appears to be a scan with little/no text",
    )


# ---------------------------------------------------------------------------
# Clause-level models (best-effort)
# ---------------------------------------------------------------------------

class ExtractedClause(BaseModel):
    """A clause / section detected within a document."""
    clause_id: str = Field(..., description="Detected clause number or generated id")
    clause_title: str = Field("", description="Detected heading / title of the clause")
    clause_text: str = Field(..., description="Full clause body text")
    page_start: int = Field(..., ge=1)
    page_end: int = Field(..., ge=1)


# ---------------------------------------------------------------------------
# Document-level models
# ---------------------------------------------------------------------------

class DocumentInfo(BaseModel):
    """Metadata about a discovered document, before or after extraction."""
    document_id: str
    filename: str
    size_bytes: int
    status: ExtractionStatus = ExtractionStatus.PENDING
    total_pages: Optional[int] = None
    extracted_pages: Optional[int] = None
    ocr_recommended: bool = False
    error: Optional[str] = None


class ExtractionResult(BaseModel):
    """Full extraction output for one document."""
    document: DocumentInfo
    pages: list[ExtractedPage] = []
    clauses: list[ExtractedClause] = []


# ---------------------------------------------------------------------------
# Compliance-comparison models
# ---------------------------------------------------------------------------

class PolicyRequirement(BaseModel):
    """A source-preserving obligation found in a policy document."""
    requirement_id: str
    requirement_title: str
    requirement_text: str
    category: str = "general"
    mandatory_or_advisory: Literal["mandatory", "advisory", "ambiguous"]
    source_document: str
    source_page: int = Field(..., ge=1)
    source_section: str = ""
    confidence: float = Field(..., ge=0, le=1)
    review_note: Optional[str] = None


class EvidenceExcerpt(BaseModel):
    text: str
    document: str
    page_number: int = Field(..., ge=1)
    section: str = ""


class ProposedRevision(BaseModel):
    kind: Literal["insertion", "replacement"]
    text: str
    rationale: str
    legal_review_required: bool = True


class ComplianceFinding(BaseModel):
    finding_id: str
    requirement_id: str
    status: Literal["compliant", "potentially_non_compliant", "missing", "ambiguous", "not_applicable"]
    risk_level: Literal["low", "medium", "high"]
    summary: str
    reasoning_summary: str
    policy_evidence: list[EvidenceExcerpt]
    contract_evidence: list[EvidenceExcerpt] = []
    search_scope: str
    proposed_revision: Optional[ProposedRevision] = None
    confidence: float = Field(..., ge=0, le=1)
    review_required: bool = True


class AnalysisSummary(BaseModel):
    compliant: int = 0
    potentially_non_compliant: int = 0
    missing: int = 0
    ambiguous: int = 0
    not_applicable: int = 0


class ComparisonRequest(BaseModel):
    policy_document_id: str = Field(..., min_length=1, max_length=160)
    contract_document_id: str = Field(..., min_length=1, max_length=160)


class ComparisonAnalysis(BaseModel):
    analysis_id: str
    policy_document: DocumentInfo
    contract_document: DocumentInfo
    requirements_analyzed: int
    findings: list[ComplianceFinding]
    summary: AnalysisSummary
    warnings: list[str] = []


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisListItem(BaseModel):
    analysis_id: str
    policy_document_id: str
    contract_document_id: str
    policy_document_name: str
    contract_document_name: str
    status: AnalysisStatus
    created_at: str
    updated_at: str
    summary: Optional[AnalysisSummary] = None
    error_message: Optional[str] = None


class SavedAnalysis(AnalysisListItem):
    findings: list[ComplianceFinding] = []
    warnings: list[str] = []
    reviews: list["ReviewResponse"] = []


class ReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    comment: str = Field("", max_length=4000)

    def model_post_init(self, __context) -> None:
        if self.decision == ReviewDecision.NEEDS_REVISION and not self.comment.strip():
            raise ValueError("A review comment is required for needs_revision")


class ReviewResponse(BaseModel):
    analysis_id: str
    finding_id: str
    decision: ReviewDecision
    comment: str
    reviewed_at: str


class RedlineRequest(BaseModel):
    finding_ids: list[str] | None = Field(default=None, max_length=100)


class RedlineArtifact(BaseModel):
    artifact_id: str
    analysis_id: str
    filename: str
    created_at: str
    included_finding_ids: list[str]
    download_url: str
    visual_redline: bool = True
