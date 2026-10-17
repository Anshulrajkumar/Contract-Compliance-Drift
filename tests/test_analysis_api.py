"""Integration coverage for persistent API workflow (uses a temporary SQLite DB)."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app import registry, store


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "analyses.db"))
    # Routine persistence tests must never spend a configured provider key.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    registry.reset_registry(); store.clear()
    yield
    registry.reset_registry(); store.clear()


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as instance:
        yield instance


def ids(client):
    documents = client.get("/api/documents").json()
    policy = next(item for item in documents if "Acceptable-Use" in item["filename"])
    contract = next(item for item in documents if "Vendor MSA" in item["filename"])
    return policy["document_id"], contract["document_id"]


def test_document_detail_and_invalid_id(client):
    policy_id, _ = ids(client)
    assert client.get(f"/api/documents/{policy_id}").status_code == 200
    assert client.get("/api/documents/not-a-document").status_code == 404


def test_analysis_is_saved_retrievable_and_filterable(client):
    policy_id, contract_id = ids(client)
    created = client.post("/api/analyses", json={"policy_document_id": policy_id, "contract_document_id": contract_id})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "completed"
    assert body["findings"]
    analysis_id = body["analysis_id"]
    assert client.get("/api/analyses").json()[0]["analysis_id"] == analysis_id
    assert client.get(f"/api/analyses/{analysis_id}").json()["summary"]["ambiguous"] >= 1
    assert client.get(f"/api/analyses/{analysis_id}/findings?status=ambiguous").status_code == 200
    assert client.get(f"/api/analyses/{analysis_id}/findings?status=nope").status_code == 422


def test_review_decision_persists(client):
    policy_id, contract_id = ids(client)
    analysis = client.post("/api/analyses", json={"policy_document_id": policy_id, "contract_document_id": contract_id}).json()
    finding_id = analysis["findings"][0]["finding_id"]
    review = client.patch(f"/api/analyses/{analysis['analysis_id']}/findings/{finding_id}/review", json={"decision": "needs_revision", "comment": "Please clarify scope."})
    assert review.status_code == 200
    assert review.json()["decision"] == "needs_revision"
    assert client.get(f"/api/analyses/{analysis['analysis_id']}").json()["reviews"][0]["comment"] == "Please clarify scope."
    bad = client.patch(f"/api/analyses/{analysis['analysis_id']}/findings/{finding_id}/review", json={"decision": "needs_revision", "comment": ""})
    assert bad.status_code == 422
    assert client.patch(f"/api/analyses/{analysis['analysis_id']}/findings/nope/review", json={"decision": "accepted"}).status_code == 404


def test_invalid_analysis_request_rejected(client):
    assert client.post("/api/analyses", json={"policy_document_id": "bad", "contract_document_id": "also-bad"}).status_code == 404
