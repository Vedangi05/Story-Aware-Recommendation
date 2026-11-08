import hashlib
import os
import re
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MASTER = os.path.join(ROOT, "data", "article-link-annotation.xlsx")


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@pytest.fixture
def master_copy(tmp_path):
    dst = tmp_path / "master.xlsx"
    shutil.copy(MASTER, dst)
    return str(dst)


@pytest.fixture
def app(tmp_path, master_copy):
    from app import create_app
    flask_app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "ADMIN_SECRET": "admin-secret",
        "MASTER_WORKBOOK": master_copy,
        "ANNOTATIONS_DIR": str(tmp_path / "annotations"),
    })
    return flask_app


@pytest.fixture
def make_client(app):
    def _make():
        return app.test_client()
    return _make


def csrf_of(client):
    html = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def start_session(client, name="Tester"):
    token = csrf_of(client)
    resp = client.post("/start", data={"name": name, "csrf_token": token})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/annotate")
    with client.session_transaction() as s:
        return s["annotator_id"], s["resume_code"], s["csrf"]


def label(client, token, pair_id, value, notes=None):
    body = {"pair_id": pair_id, "label": value}
    if notes is not None:
        body["notes"] = notes
    return client.post("/api/annotate", json=body, headers={"X-CSRF-Token": token})
