"""A small, executable labelled evaluation set for comparison orchestration."""
from __future__ import annotations

import json
from pathlib import Path

from app.comparison import compare
from app.models import DocumentInfo, ExtractedPage, ExtractionResult


class LabelProvider:
    def __init__(self, status: str): self.status = status
    def classify(self, requirement, evidence):
        return {"status": self.status, "summary": "Labelled evaluation result.", "reasoning_summary": "Synthetic fixture.", "confidence": .9}


def _result(identifier: str, text: str) -> ExtractionResult:
    doc = DocumentInfo(document_id=identifier, filename=f"{identifier}.pdf", size_bytes=1)
    return ExtractionResult(document=doc, pages=[ExtractedPage(document_id=identifier, filename=doc.filename, page_number=1, text=text, char_count=len(text))])


def test_labelled_evaluation_cases_run_with_expected_outcomes():
    cases = json.loads((Path(__file__).parent / "fixtures" / "comparison_evaluation.json").read_text())
    actual = []
    for case in cases:
        analysis = compare(_result("policy", case["policy"]), _result("contract", case["contract"]), LabelProvider(case["provider_status"]))
        actual.append(analysis.findings[0].status)
    assert actual == [case["provider_status"] for case in cases]
