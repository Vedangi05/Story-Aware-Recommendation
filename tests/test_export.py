import io
import re
import zipfile
from datetime import datetime, timezone

import openpyxl

from conftest import MASTER, csrf_of, label, start_session


def test_participant_export(make_client):
    c = make_client()
    aid, _, tok = start_session(c)
    label(c, tok, "1", "Same Story", notes="clear sequel")
    label(c, tok, "3", "Unrelated")
    resp = c.get("/export")
    assert resp.status_code == 200
    cd = resp.headers["Content-Disposition"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert f"POPROX_Annotations_{aid}_{today}.xlsx" in cd
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    ws = wb["Article-pairs"]
    headers = [c.value for c in ws[1]]
    assert headers[:10] == ["Pair #", "Article A - Headline", "Article A - Subhead",
                            "Article A - First paragraph", "Link text", "Article B - Headline",
                            "Article B - Subhead", "Article B - First paragraph", "Label", "Notes"]
    rows = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True) if r[0] is not None}
    assert len(rows) == 100
    assert rows[1][8] == "Same Story" and rows[1][9] == "clear sequel"
    assert rows[3][8] == "Unrelated" and rows[2][8] is None
    # Original article data preserved
    master = {r[0]: r for r in openpyxl.load_workbook(MASTER)["Article-pairs"].iter_rows(min_row=2, values_only=True) if r[0]}
    for k in master:
        assert rows[k][:8] == master[k][:8]
    assert "Label-info" in wb.sheetnames


def test_admin_export_protected(make_client):
    c = make_client()
    assert c.get("/admin/export").status_code == 403
    assert c.get("/admin/export", headers={"Authorization": "Bearer wrong"}).status_code == 403
    c.post("/admin", data={"secret": "wrong", "csrf_token": csrf_of(c)})
    assert c.get("/admin/export").status_code == 403


def test_admin_disabled_without_secret(app, make_client):
    app.config["ADMIN_SECRET"] = ""
    c = make_client()
    assert c.get("/admin").status_code == 404
    assert c.get("/admin/export", headers={"Authorization": "Bearer "}).status_code == 404


def test_admin_combined_and_zip(make_client):
    users = []
    for name, lab in (("A", "Related"), ("B", "Unrelated")):
        c = make_client()
        aid, _, tok = start_session(c, name)
        label(c, tok, "1", lab)
        users.append((aid, lab))
    admin = make_client()
    admin.post("/admin", data={"secret": "admin-secret", "csrf_token": csrf_of(admin)})
    page = admin.get("/admin").get_data(as_text=True)
    assert all(aid in page for aid, _ in users)

    resp = admin.get("/admin/export?format=combined")
    assert resp.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    ws = wb["All-annotations"]
    header = [c.value for c in ws[1]]
    assert header[:7] == ["Annotator ID", "Display Name", "Pair #", "Article A Headline",
                          "Article B Headline", "Link Text", "Selected Label"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 200  # one row per annotator per pair
    for aid, lab in users:
        r = next(r for r in rows if r[0] == aid and r[2] == 1)
        assert r[6] == lab and r[8]  # timestamp present

    resp = admin.get("/admin/export?format=zip", headers={})
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    for aid, _ in users:
        assert f"annotator_{aid}.xlsx" in names
    assert any(re.match(r"POPROX_All_Annotations_.*\.xlsx", n) for n in names)


def test_admin_bearer_token(make_client):
    c = make_client()
    r = c.get("/admin/export?format=combined", headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200


def test_annotation_files_not_public(app, make_client):
    c = make_client()
    aid, _, _ = start_session(c)
    assert c.get(f"/static/../annotations/annotator_{aid}.xlsx").status_code == 404
    assert c.get(f"/annotations/annotator_{aid}.xlsx").status_code == 404
