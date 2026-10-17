"""SQLite persistence for analyses and reviewer decisions.

Serialized Pydantic payloads keep the MVP schema simple while normalized rows
make listing, state transitions, and review ownership transactional.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.config import get_database_path
from app.models import (AnalysisListItem, AnalysisStatus, ComparisonAnalysis,
    RedlineArtifact, ReviewRequest, ReviewResponse, SavedAnalysis)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    path = get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS analyses (
          analysis_id TEXT PRIMARY KEY, policy_document_id TEXT NOT NULL,
          contract_document_id TEXT NOT NULL, policy_document_name TEXT NOT NULL,
          contract_document_name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, summary_json TEXT,
          findings_json TEXT, warnings_json TEXT, error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS requirements (
          analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
          requirement_id TEXT NOT NULL, evidence_json TEXT NOT NULL,
          PRIMARY KEY (analysis_id, requirement_id)
        );
        CREATE TABLE IF NOT EXISTS findings (
          analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
          finding_id TEXT NOT NULL, status TEXT NOT NULL, risk_level TEXT NOT NULL,
          finding_json TEXT NOT NULL, PRIMARY KEY (analysis_id, finding_id)
        );
        CREATE TABLE IF NOT EXISTS reviews (
          analysis_id TEXT NOT NULL, finding_id TEXT NOT NULL, decision TEXT NOT NULL,
          comment TEXT NOT NULL, reviewed_at TEXT NOT NULL,
          PRIMARY KEY (analysis_id, finding_id),
          FOREIGN KEY (analysis_id, finding_id) REFERENCES findings(analysis_id, finding_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS redline_artifacts (
          artifact_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
          filename TEXT NOT NULL, storage_name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
          finding_ids_json TEXT NOT NULL
        );
        """)


def create_pending(analysis_id: str, policy_id: str, contract_id: str, policy_name: str, contract_name: str) -> AnalysisListItem:
    now = _now()
    try:
        with _connect() as conn:
            conn.execute("INSERT INTO analyses VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, NULL, NULL)",
                         (analysis_id, policy_id, contract_id, policy_name, contract_name, now, now))
    except sqlite3.IntegrityError as exc:
        raise ValueError("An analysis for this document pair already exists") from exc
    return AnalysisListItem(analysis_id=analysis_id, policy_document_id=policy_id, contract_document_id=contract_id,
        policy_document_name=policy_name, contract_document_name=contract_name, status=AnalysisStatus.PENDING, created_at=now, updated_at=now)


def mark_running(analysis_id: str) -> None:
    with _connect() as conn:
        changed = conn.execute("UPDATE analyses SET status='running', updated_at=? WHERE analysis_id=? AND status='pending'", (_now(), analysis_id)).rowcount
        if not changed: raise ValueError("Analysis cannot be started from its current state")


def save_completed(analysis: ComparisonAnalysis) -> None:
    now = _now()
    with _connect() as conn:
        changed = conn.execute("UPDATE analyses SET status='completed', updated_at=?, summary_json=?, findings_json=?, warnings_json=?, error_message=NULL WHERE analysis_id=? AND status='running'",
            (now, analysis.summary.model_dump_json(), json.dumps([f.model_dump(mode='json') for f in analysis.findings]), json.dumps(analysis.warnings), analysis.analysis_id)).rowcount
        if not changed: raise ValueError("Analysis completion state conflict")
        for finding in analysis.findings:
            conn.execute("INSERT INTO requirements VALUES (?, ?, ?)", (analysis.analysis_id, finding.requirement_id, json.dumps([e.model_dump(mode='json') for e in finding.policy_evidence])))
            conn.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?)", (analysis.analysis_id, finding.finding_id, finding.status, finding.risk_level, finding.model_dump_json()))


def mark_failed(analysis_id: str, message: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE analyses SET status='failed', updated_at=?, error_message=? WHERE analysis_id=? AND status IN ('pending','running')", (_now(), message[:500], analysis_id))


def _list_item(row: sqlite3.Row) -> AnalysisListItem:
    return AnalysisListItem(**dict(row), summary=json.loads(row["summary_json"]) if row["summary_json"] else None)


def list_analyses() -> list[AnalysisListItem]:
    with _connect() as conn:
        return [_list_item(row) for row in conn.execute("SELECT * FROM analyses ORDER BY created_at DESC")]


def get_analysis(analysis_id: str) -> SavedAnalysis | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
        review_rows = conn.execute("SELECT analysis_id, finding_id, decision, comment, reviewed_at FROM reviews WHERE analysis_id=? ORDER BY reviewed_at", (analysis_id,)).fetchall() if row else []
    if not row: return None
    base = _list_item(row).model_dump()
    return SavedAnalysis(**base, findings=json.loads(row["findings_json"]) if row["findings_json"] else [], warnings=json.loads(row["warnings_json"]) if row["warnings_json"] else [], reviews=[dict(item) for item in review_rows])


def save_review(analysis_id: str, finding_id: str, request: ReviewRequest) -> ReviewResponse | None:
    now = _now()
    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM findings WHERE analysis_id=? AND finding_id=?", (analysis_id, finding_id)).fetchone()
        if not exists: return None
        conn.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?) ON CONFLICT(analysis_id, finding_id) DO UPDATE SET decision=excluded.decision, comment=excluded.comment, reviewed_at=excluded.reviewed_at",
                     (analysis_id, finding_id, request.decision.value, request.comment.strip(), now))
    return ReviewResponse(analysis_id=analysis_id, finding_id=finding_id, decision=request.decision, comment=request.comment.strip(), reviewed_at=now)


def save_artifact(artifact_id: str, analysis_id: str, filename: str, storage_name: str, finding_ids: list[str]) -> RedlineArtifact:
    now = _now()
    with _connect() as conn:
        conn.execute("INSERT INTO redline_artifacts VALUES (?, ?, ?, ?, ?, ?)", (artifact_id, analysis_id, filename, storage_name, now, json.dumps(finding_ids)))
    return RedlineArtifact(artifact_id=artifact_id, analysis_id=analysis_id, filename=filename, created_at=now, included_finding_ids=finding_ids, download_url=f"/api/redlines/{artifact_id}/download")


def list_artifacts(analysis_id: str) -> list[RedlineArtifact]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM redline_artifacts WHERE analysis_id=? ORDER BY created_at DESC", (analysis_id,)).fetchall()
    return [RedlineArtifact(artifact_id=row["artifact_id"], analysis_id=row["analysis_id"], filename=row["filename"], created_at=row["created_at"], included_finding_ids=json.loads(row["finding_ids_json"]), download_url=f"/api/redlines/{row['artifact_id']}/download") for row in rows]


def get_artifact(artifact_id: str) -> tuple[RedlineArtifact, str] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM redline_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    if not row: return None
    artifact = RedlineArtifact(artifact_id=row["artifact_id"], analysis_id=row["analysis_id"], filename=row["filename"], created_at=row["created_at"], included_finding_ids=json.loads(row["finding_ids_json"]), download_url=f"/api/redlines/{row['artifact_id']}/download")
    return artifact, row["storage_name"]
