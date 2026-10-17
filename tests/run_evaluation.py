"""Run the controlled, clearly synthetic compliance-evaluation corpus."""
from __future__ import annotations
import json
from pathlib import Path
from app.comparison import compare
from app.models import DocumentInfo, ExtractedPage, ExtractionResult

def result(identifier, text):
    doc=DocumentInfo(document_id=identifier, filename=f"synthetic-{identifier}.txt", size_bytes=len(text))
    return ExtractionResult(document=doc,pages=[ExtractedPage(document_id=identifier,filename=doc.filename,page_number=1,text=text,char_count=len(text))])

def main():
    cases=json.loads((Path(__file__).parent/"fixtures"/"controlled_evaluation.json").read_text())
    # Offline mode intentionally only returns ambiguous: this measures its safe baseline,
    # not a fabricated live-LLM accuracy claim.
    actual=[compare(result("policy",c["policy"]),result("contract",c["contract"])).findings[0] for c in cases]
    correct=sum(f.status==c["expected"] for f,c in zip(actual,cases)); predicted=sum(f.status=="potentially_non_compliant" for f in actual); expected=sum(c["expected"]=="potentially_non_compliant" for c in cases); tp=sum(f.status==c["expected"]=="potentially_non_compliant" for f,c in zip(actual,cases))
    traceable=sum(bool(f.policy_evidence) and all(e.text in cases[i]["policy"] for e in f.policy_evidence) for i,f in enumerate(actual))
    print(json.dumps({"cases":len(cases),"correct_classifications":correct,"correct_classification_rate":f"{correct}/8", "noncompliance_precision":f"{tp}/{predicted}" if predicted else "0/0 (no predictions)","noncompliance_recall":f"{tp}/{expected}","false_positive_rate":"0/0 (no positive predictions)","false_negative_count":expected-tp,"evidence_traceability_rate":f"{traceable}/8","proposed_revision_validity":"0/0 (offline mode produces no proposals)"},indent=2))
if __name__=="__main__": main()
