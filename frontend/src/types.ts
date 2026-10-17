export type AnalysisStatus = "pending" | "running" | "completed" | "failed";
export type FindingStatus = "compliant" | "potentially_non_compliant" | "missing" | "ambiguous" | "not_applicable";
export type RiskLevel = "low" | "medium" | "high";
export interface Document { document_id: string; filename: string; size_bytes: number; status: string; total_pages?: number; extracted_pages?: number; }
export interface Evidence { text: string; document: string; page_number: number; section: string; }
export interface Revision { kind: "insertion" | "replacement"; text: string; rationale: string; legal_review_required: boolean; }
export interface Finding { finding_id: string; requirement_id: string; status: FindingStatus; risk_level: RiskLevel; summary: string; reasoning_summary: string; policy_evidence: Evidence[]; contract_evidence: Evidence[]; search_scope: string; proposed_revision?: Revision; confidence: number; review_required: boolean; }
export interface Summary { compliant: number; potentially_non_compliant: number; missing: number; ambiguous: number; not_applicable: number; }
export interface Review { analysis_id: string; finding_id: string; decision: "accepted" | "rejected" | "needs_revision"; comment: string; reviewed_at: string; }
export interface Analysis { analysis_id: string; policy_document_id: string; contract_document_id: string; policy_document_name: string; contract_document_name: string; status: AnalysisStatus; created_at: string; updated_at: string; summary?: Summary; error_message?: string; findings: Finding[]; warnings: string[]; reviews: Review[]; }
export interface RedlineArtifact { artifact_id: string; analysis_id: string; filename: string; created_at: string; included_finding_ids: string[]; download_url: string; visual_redline: boolean; }
export type AnalysisListItem = Omit<Analysis, "findings" | "warnings" | "reviews">;
