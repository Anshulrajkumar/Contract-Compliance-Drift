import type { Analysis, AnalysisListItem, Document, Finding, RedlineArtifact, Review } from "./types";
const base = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api").replace(/\/$/, "");
export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`${base}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers }, signal: AbortSignal.timeout(30000) }); }
  catch { throw new ApiError(0, "Unable to reach the backend. Check that it is running."); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, body.detail || "The request could not be completed.");
  return body as T;
}
export const api = { health: () => request<{status:string}>("/health"), documents: () => request<Document[]>("/documents"), analyses: () => request<AnalysisListItem[]>("/analyses"), analysis: (id: string) => request<Analysis>(`/analyses/${id}`), createAnalysis: (policy_document_id: string, contract_document_id: string) => request<Analysis>("/analyses", { method: "POST", body: JSON.stringify({ policy_document_id, contract_document_id }) }), findings: (id: string, query = "") => request<Finding[]>(`/analyses/${id}/findings${query}`), review: (analysisId: string, findingId: string, decision: Review["decision"], comment: string) => request<Review>(`/analyses/${analysisId}/findings/${findingId}/review`, { method: "PATCH", body: JSON.stringify({ decision, comment }) }), redlines: (id: string) => request<RedlineArtifact[]>(`/analyses/${id}/redlines`), createRedline: (id: string, finding_ids?: string[]) => request<RedlineArtifact>(`/analyses/${id}/redlines`, { method: "POST", body: JSON.stringify(finding_ids ? {finding_ids} : {}) }), downloadUrl: (url: string) => `${base}${url}` };
