# Contract Compliance Drift & Redline Drafter

AI-powered contract compliance analysis that compares company policies against legacy vendor contracts, identifies compliance gaps, and generates proposed contract revisions.

## Project Structure

```
├── policy/                          # Policy & contract PDFs (do not modify)
│   ├── Vendor MSA_en.pdf
│   ├── Supplier-Code-of-Conduct-Updated-2026.pdf
│   └── Acceptable-Use-of-Proprietary-and-Confidential-...-Policy-17-09-2025.pdf
├── app/
│   ├── __init__.py
│   ├── config.py                    # Environment / path configuration
│   ├── models.py                    # Pydantic schemas (pages, clauses, docs)
│   ├── registry.py                  # Document discovery & inventory
│   ├── extractor.py                 # PDF text extraction engine
│   ├── store.py                     # In-memory extraction result store
│   └── main.py                      # FastAPI application & endpoints
├── tests/
│   └── test_extraction.py           # 20 tests covering extraction & API
├── requirements.txt
├── .env.example
└── README.md
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. (Optional) Configure environment

```bash
cp .env.example .env
# Edit .env if your PDFs are in a different directory
```

### 3. Start the backend

```bash
uvicorn app.main:app --reload
```

The server starts at **http://localhost:8000**.  
Interactive API docs at **http://localhost:8000/docs**.

### 4. Run the tests

```bash
python -m pytest tests/test_extraction.py -v
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/documents` | List discovered documents & status |
| `POST` | `/api/documents/{document_id}/extract` | Extract text from a document |
| `GET` | `/api/documents/{document_id}/pages` | Get extracted pages with source refs |
| `GET` | `/api/documents/{document_id}/clauses` | Get detected clauses (best-effort) |

### Example workflow

```bash
# 1. List available documents
curl http://localhost:8000/api/documents

# 2. Extract a document (use the document_id from step 1)
curl -X POST http://localhost:8000/api/documents/<document_id>/extract

# 3. Retrieve extracted pages
curl http://localhost:8000/api/documents/<document_id>/pages
```

## PDF Extraction Details

- **Two-phase extraction**: Tries `pypdf` first (fast), falls back to `pdfplumber` per page when text is sparse.
- **Page-level tracking**: Every extracted page carries `document_id`, `filename`, `page_number`, and character count.
- **Clause segmentation**: Best-effort detection layered *on top* of page data — clause detection failures never corrupt the underlying page text.
- **OCR detection**: Pages with fewer than 30 characters are flagged as `ocr_recommended`.
- **Error handling**: Corrupt, encrypted, empty, and missing PDFs produce explicit error statuses rather than silent empty results.

## Extraction Status Values

| Status | Meaning |
|--------|---------|
| `pending` | Document discovered but not yet extracted |
| `extracted` | All pages yielded text |
| `partial` | Some pages have text, others may need OCR |
| `ocr_required` | No pages contained extractable text |
| `failed` | Document could not be opened (corrupt / encrypted / missing) |

## What's Next

Future steps will add:
- Frontend dashboard

## Step 2: comparison engine

Create an evidence-backed comparison using document IDs from `GET /api/documents`:

```bash
curl -X POST http://localhost:8000/api/comparisons \
  -H "Content-Type: application/json" \
  -d '{"policy_document_id":"<policy-id>","contract_document_id":"<contract-id>"}'
```

The endpoint extracts documents on demand, preserves policy and contract excerpts with page references, retrieves clause/page candidates using lexical scoring plus a full-contract fallback, and calculates status counts in Python. The default offline evaluator is intentionally conservative: it emits `ambiguous` rather than treating topical similarity or a failed retrieval as proof of compliance or absence.

To enable an OpenAI-compatible reviewer, add these private server-side variables to `.env` (never expose them to a frontend):

```dotenv
LLM_API_KEY=...
LLM_MODEL=gpt-4o-mini
# Optional for compatible providers; defaults to OpenAI's v1 endpoint.
LLM_BASE_URL=https://api.openai.com/v1
```

### Gemini

Gemini works through its OpenAI-compatible chat-completions endpoint. Copy `.env.example` to `.env`, then set only your own secret locally:

```dotenv
GEMINI_API_KEY=your_google_ai_studio_key
LLM_MODEL=gemini-2.5-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
```

Restart Uvicorn after editing `.env`. `GEMINI_API_KEY` is server-only and takes precedence over `LLM_API_KEY`; do not place it in React code, API responses, or version control. Gemini documents this compatible base URL and chat-completions API. 

Provider output is validated. A malformed response, timeout, or provider error leaves a conservative finding and warning; it never becomes a compliant result. Proposed draft language is generated only for provider-classified `missing` or `potentially_non_compliant` findings and is always marked for legal review. Source PDFs are never modified.

`tests/fixtures/comparison_evaluation.json` is a small labelled evaluation set (deadline conflict, equivalent confidentiality wording, and missing wording). Run it with the full suite:

```bash
python -m pytest -q
```

## Step 3: persistent analysis API

`POST /api/analyses` runs the same comparison engine and records the lifecycle in SQLite. Results, source evidence, findings, and reviewer decisions survive process restarts. The default database is `data/analyses.db`; override it for deployment or testing:

```dotenv
DATABASE_PATH=./data/analyses.db
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/documents/{document_id}` | One inventory item |
| `POST` | `/api/analyses` | Persist and run an analysis |
| `GET` | `/api/analyses` | List analysis metadata |
| `GET` | `/api/analyses/{analysis_id}` | Saved findings and summary |
| `GET` | `/api/analyses/{analysis_id}/findings?status=&risk_level=` | Filter findings |
| `PATCH` | `/api/analyses/{analysis_id}/findings/{finding_id}/review` | Save a reviewer decision |

Example review request:

```bash
curl -X PATCH http://localhost:8000/api/analyses/<analysis-id>/findings/<finding-id>/review \
  -H "Content-Type: application/json" \
  -d '{"decision":"needs_revision","comment":"Confirm the defined-term scope."}'
```

This is a trusted-demo backend: there is no authentication or authorization layer yet. Do not expose it publicly or use it with confidential documents until access controls are added.

## Frontend

The React + TypeScript frontend is in `frontend/`. It uses the real FastAPI endpoints for document selection, saved analyses, evidence-backed findings, and reviewer decisions. It never receives an LLM key.

```bash
# Terminal 1 — project root
uvicorn app.main:app --reload

# Terminal 2
cd frontend
copy .env.example .env
npm install
npm run dev
```

Open the URL printed by Vite (normally `http://localhost:5173`). The default `VITE_API_BASE_URL` is `http://localhost:8000/api`; set it in `frontend/.env` only if the backend is hosted elsewhere. Build a production bundle with `npm run build`.

## Redline drafts

## Controlled evaluation and demo

The synthetic corpus at `tests/fixtures/controlled_evaluation.json` covers compliant, deadline-conflict, missing, equivalent-wording, ambiguous, exception, scope-conflict, and irrelevant-keyword cases. Run its conservative offline baseline with:

```bash
python -m tests.run_evaluation
```

The offline evaluator intentionally avoids legal conclusions and therefore returns primarily `ambiguous`; use a configured provider plus human legal review for substantive comparison. The script prints actual denominators rather than an invented accuracy claim.

Demo sequence: start both services; select the third-party confidential-information policy and `Vendor MSA_en.pdf`; run and inspect an evidence-backed finding; save a review decision; reopen it from history; then generate and download a redline when a provider-backed proposal is present. Use the synthetic corpus—not the real PDFs—to demonstrate planted discrepancies.

Create a visual-redline DOCX from saved findings with valid proposed revisions:

```bash
curl -X POST http://localhost:8000/api/analyses/<analysis-id>/redlines \
  -H "Content-Type: application/json" -d '{}'
```

The response supplies an artifact ID and `download_url`. `GET /api/analyses/<analysis-id>/redlines` lists prior drafts, and `GET /api/redlines/<artifact-id>/download` downloads the actual Word document. Files are stored in `ARTIFACT_DIR` (default `data/redlines`). The DOCX uses **visual redlining**—red strikethrough deletions and green additions—not native Microsoft Word tracked changes. Every generated file is labelled as a draft for legal review and does not modify a source PDF.
