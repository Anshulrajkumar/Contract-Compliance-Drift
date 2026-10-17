"""PDF text extraction engine using pypdf and pdfplumber.

Design principles
-----------------
* Page-level extraction is always performed and stored independently.
* Clause segmentation is best-effort and layered *on top* of page text –
  failures in clause detection never destroy the underlying page data.
* Confidential document contents are **not** logged.
* Corrupt / encrypted / empty PDFs raise clear diagnostics rather than
  returning silent empty results.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.models import (
    DocumentInfo,
    ExtractionResult,
    ExtractionStatus,
    ExtractedClause,
    ExtractedPage,
)

logger = logging.getLogger(__name__)

# A page with fewer than this many non-whitespace characters is considered
# "effectively empty" and likely requires OCR.
_MIN_TEXT_CHARS = 30

# ---------------------------------------------------------------------------
# Clause segmentation helpers
# ---------------------------------------------------------------------------

# Common clause-heading patterns (best-effort, intentionally broad).
_CLAUSE_RE = re.compile(
    r"^"
    r"(?:"
    r"(?P<num>\d+(?:\.\d+)*)"          # e.g. "1", "3.2", "12.1.4"
    r"[\.\)\s]+"                        # separator
    r"(?P<title>[A-Z][^\n]{0,120})"     # capitalised title on the same line
    r"|"
    r"(?:Section|Article|Clause)\s+"
    r"(?P<named_num>\d+(?:\.\d+)*)"
    r"[\.\:\s]*"
    r"(?P<named_title>[^\n]{0,120})?"
    r")",
    re.MULTILINE,
)


def _detect_clauses(
    pages: List[ExtractedPage],
) -> List[ExtractedClause]:
    """Attempt to split extracted page text into clauses.

    Returns an empty list when no clause headings are found – this is
    perfectly acceptable and preserves the page-level data intact.
    """
    # Build a combined text with page boundary markers so we can map back.
    page_offsets: List[Tuple[int, int, int]] = []  # (start_char, end_char, page_num)
    combined_parts: list[str] = []
    offset = 0
    for page in pages:
        text = page.text
        combined_parts.append(text)
        page_offsets.append((offset, offset + len(text), page.page_number))
        offset += len(text) + 1  # +1 for the join newline
    combined = "\n".join(combined_parts)

    matches = list(_CLAUSE_RE.finditer(combined))
    if not matches:
        return []

    clauses: List[ExtractedClause] = []
    for i, m in enumerate(matches):
        clause_num = m.group("num") or m.group("named_num") or ""
        clause_title = (m.group("title") or m.group("named_title") or "").strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(combined)
        clause_text = combined[start:end].strip()

        # Determine page span.
        page_start = page_end = pages[0].page_number
        for p_start, p_end, p_num in page_offsets:
            if p_start <= start < p_end:
                page_start = p_num
            if p_start < end <= p_end:
                page_end = p_num

        clauses.append(
            ExtractedClause(
                clause_id=clause_num or f"auto-{i + 1}",
                clause_title=clause_title,
                clause_text=clause_text,
                page_start=page_start,
                page_end=page_end,
            )
        )

    return clauses


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


def extract_document(doc: DocumentInfo, pdf_path: Path) -> ExtractionResult:
    """Extract text from *pdf_path* and return a structured result.

    The function **never** silently returns an empty successful result.
    It distinguishes between:
    * A document that was read successfully but contains no extractable text
      → ``ExtractionStatus.OCR_REQUIRED``
    * A document that could not be opened at all (corrupt / encrypted)
      → ``ExtractionStatus.FAILED``
    * A document with some pages having text and others not
      → ``ExtractionStatus.PARTIAL`` (or EXTRACTED if all pages have text)
    """
    if not pdf_path.exists():
        doc.status = ExtractionStatus.FAILED
        doc.error = f"File not found: {pdf_path.name}"
        return ExtractionResult(document=doc)

    pages: List[ExtractedPage] = []
    total_pages = 0
    ocr_needed_pages = 0

    # ------------------------------------------------------------------
    # Phase 1 – try pypdf for fast text extraction
    # ------------------------------------------------------------------
    try:
        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            # Attempt empty-password decrypt (some PDFs use this).
            try:
                reader.decrypt("")
            except Exception:
                doc.status = ExtractionStatus.FAILED
                doc.error = "PDF is encrypted and could not be decrypted"
                return ExtractionResult(document=doc)

        total_pages = len(reader.pages)
    except PdfReadError as exc:
        doc.status = ExtractionStatus.FAILED
        doc.error = f"Corrupt or unreadable PDF: {exc}"
        return ExtractionResult(document=doc)
    except Exception as exc:
        doc.status = ExtractionStatus.FAILED
        doc.error = f"Unexpected error opening PDF: {exc}"
        return ExtractionResult(document=doc)

    if total_pages == 0:
        doc.status = ExtractionStatus.FAILED
        doc.error = "PDF contains zero pages"
        doc.total_pages = 0
        return ExtractionResult(document=doc)

    # ------------------------------------------------------------------
    # Phase 2 – extract text page by page, falling back to pdfplumber
    # ------------------------------------------------------------------
    for page_idx in range(total_pages):
        page_num = page_idx + 1
        text = ""

        # Try pypdf first (faster).
        try:
            text = reader.pages[page_idx].extract_text() or ""
        except Exception:
            text = ""

        # Fallback to pdfplumber when pypdf yields little text.
        if len(text.strip()) < _MIN_TEXT_CHARS:
            try:
                with pdfplumber.open(str(pdf_path)) as plumber:
                    plumber_page = plumber.pages[page_idx]
                    plumber_text = plumber_page.extract_text() or ""
                    if len(plumber_text.strip()) > len(text.strip()):
                        text = plumber_text
            except Exception:
                pass  # Keep whatever pypdf gave us.

        stripped = text.strip()
        is_ocr = len(stripped) < _MIN_TEXT_CHARS
        if is_ocr:
            ocr_needed_pages += 1

        pages.append(
            ExtractedPage(
                document_id=doc.document_id,
                filename=doc.filename,
                page_number=page_num,
                text=stripped,
                char_count=len(stripped),
                ocr_recommended=is_ocr,
            )
        )

    # ------------------------------------------------------------------
    # Phase 3 – determine overall status
    # ------------------------------------------------------------------
    extracted_count = sum(1 for p in pages if not p.ocr_recommended)
    doc.total_pages = total_pages
    doc.extracted_pages = extracted_count

    if extracted_count == 0:
        doc.status = ExtractionStatus.OCR_REQUIRED
        doc.ocr_recommended = True
        doc.error = "No pages contained extractable text – OCR is likely required"
    elif ocr_needed_pages > 0:
        doc.status = ExtractionStatus.PARTIAL
        doc.ocr_recommended = True
    else:
        doc.status = ExtractionStatus.EXTRACTED

    # ------------------------------------------------------------------
    # Phase 4 – best-effort clause segmentation (never mutates pages)
    # ------------------------------------------------------------------
    clauses: List[ExtractedClause] = []
    try:
        clauses = _detect_clauses(pages)
    except Exception:
        logger.debug(
            "Clause detection failed for %s – page data is unaffected",
            doc.filename,
        )

    logger.info(
        "Extraction complete: %s  pages=%d  text_pages=%d  clauses=%d  status=%s",
        doc.filename,
        total_pages,
        extracted_count,
        len(clauses),
        doc.status.value,
    )

    return ExtractionResult(document=doc, pages=pages, clauses=clauses)
