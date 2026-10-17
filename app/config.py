"""Application-wide configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def get_policy_dir() -> Path:
    """Return the resolved path to the policy documents directory."""
    raw = os.getenv("POLICY_DIR", "./policy")
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
PROJECT_ROOT: Path = _PROJECT_ROOT


def get_database_path() -> Path:
    """SQLite database location, kept outside the source-PDF directory."""
    raw = os.getenv("DATABASE_PATH", "./data/analyses.db")
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def get_artifact_dir() -> Path:
    """Directory for generated drafts, never source PDFs or user-selected paths."""
    raw = os.getenv("ARTIFACT_DIR", "./data/redlines")
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def get_cors_origins() -> list[str]:
    """Configured UI origins. Local development is the intentionally narrow default."""
    return [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if origin.strip()]
