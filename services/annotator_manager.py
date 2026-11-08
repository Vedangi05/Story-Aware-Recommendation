"""Annotator identity: resume codes and server-generated identifiers.

A participant receives a random *resume code* (80 bits of entropy, shown as
``XXXX-XXXX-XXXX-XXXX``). Their internal annotator ID is derived from that
code with SHA-256, so:

* the server never stores the plaintext code,
* the code alone is enough to find the participant's results file again
  (on any device), and
* filenames are always server-generated hex strings – never user input.
"""
import hashlib
import re
import secrets

# 32 easy-to-read symbols (no I, O, 0 or 1) -> 5 bits each, 80 bits per code.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
assert len(_ALPHABET) == 32
CODE_LENGTH = 16
ANNOTATOR_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def generate_resume_code():
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def normalize_resume_code(code):
    """Uppercase, drop spaces/dashes. Returns None if it can't be a valid code."""
    if not isinstance(code, str):
        return None
    cleaned = re.sub(r"[\s\-_]", "", code).upper()
    if len(cleaned) != CODE_LENGTH or any(c not in _ALPHABET for c in cleaned):
        return None
    return cleaned


def annotator_id_from_code(code):
    normalized = normalize_resume_code(code)
    if normalized is None:
        return None
    digest = hashlib.sha256(("poprox-annotation:" + normalized).encode()).hexdigest()
    return digest[:12]


def is_valid_annotator_id(value):
    return isinstance(value, str) and bool(ANNOTATOR_ID_RE.match(value))


def clean_display_name(name, max_length=80):
    if not isinstance(name, str):
        return ""
    name = " ".join(name.split())  # collapse whitespace / strip control chars like \n
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:max_length].strip()
