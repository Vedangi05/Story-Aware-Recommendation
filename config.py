"""Application configuration, read from environment variables."""
import os
import secrets
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Security -----------------------------------------------------------
    # Always set SECRET_KEY in production. A random fallback is generated for
    # local development only; sessions will not survive a restart without it.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_IS_EPHEMERAL = not bool(os.environ.get("SECRET_KEY"))

    # Researcher-only export. If unset, the admin area is disabled entirely.
    ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

    SESSION_COOKIE_NAME = "poprox_annotation"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.environ.get("SESSION_DAYS", "120")))

    # Honour X-Forwarded-* headers when running behind a reverse proxy (Render, Fly, nginx).
    TRUST_PROXY = _bool("TRUST_PROXY", False)

    # --- Storage ------------------------------------------------------------
    # The master dataset ships with the application and is only ever read.
    MASTER_WORKBOOK = os.environ.get(
        "MASTER_WORKBOOK", os.path.join(BASE_DIR, "data", "article-link-annotation.xlsx")
    )
    # Mount this directory on a persistent volume in production.
    ANNOTATIONS_DIR = os.environ.get("ANNOTATIONS_DIR", os.path.join(BASE_DIR, "annotations"))

    MAX_CONTENT_LENGTH = 64 * 1024  # requests are tiny JSON payloads
    MAX_NAME_LENGTH = 80
    MAX_NOTES_LENGTH = 2000
