import openpyxl

from conftest import csrf_of, label, sha256, start_session


def test_landing_page(make_client):
    c = make_client()
    html = c.get("/").get_data(as_text=True)
    assert "POPROX News Article Annotation" in html
    assert "Help us understand how news articles relate to one another." in html
    assert "Start Annotating" in html and "100" in html


def test_start_creates_workbook_and_annotate_page(app, make_client):
    c = make_client()
    aid, code, _ = start_session(c, "Ada")
    path = app.extensions["store"].path_for(aid)
    wb = openpyxl.load_workbook(path)
    assert "Article-pairs" in wb.sheetnames and "Annotator-info" in wb.sheetnames
    assert wb["Annotator-info"]["B3"].value == "Ada"
    html = c.get("/annotate").get_data(as_text=True)
    assert "Pair 1 of 100" in html and "0 of 100 completed" in html.replace("<strong id=\"completed-count\">0</strong>", "0")
    assert code in html  # resume code shown once
    assert code not in c.get("/annotate").get_data(as_text=True)


def test_save_advances_and_counts(make_client):
    c = make_client()
    _, _, tok = start_session(c)
    r = label(c, tok, "1", "Same Story").get_json()
    assert r["ok"] and r["was_new"] and r["completed"] == 1 and r["next_id"] == "2"
    r = label(c, tok, "2", "Related").get_json()
    assert r["completed"] == 2 and r["next_id"] == "3"
    prog = c.get("/api/progress").get_json()
    assert prog["first_unannotated"] == "3"
    assert prog["counts"] == {"Same Story": 1, "Related": 1, "Unrelated": 0}


def test_change_label_not_double_counted(app, make_client):
    c = make_client()
    aid, _, tok = start_session(c)
    label(c, tok, "5", "Same Story")
    r = label(c, tok, "5", "Unrelated").get_json()
    assert r["was_new"] is False and r["completed"] == 1
    assert c.get("/api/pairs/5").get_json()["label"] == "Unrelated"
    ws = openpyxl.load_workbook(app.extensions["store"].path_for(aid))["Article-pairs"]
    row = next(r for r in ws.iter_rows(min_row=2) if r[0].value == 5)
    assert row[8].value == "Unrelated"
    assert row[10].value  # timestamp column


def test_validation(make_client):
    c = make_client()
    _, _, tok = start_session(c)
    assert label(c, tok, "1", "Maybe").status_code == 400
    assert label(c, tok, "999", "Related").status_code == 400
    assert label(c, tok, "../../etc", "Related").status_code == 400
    assert c.get("/api/pairs/999").status_code == 404


def test_csrf_required(make_client):
    c = make_client()
    start_session(c)
    r = c.post("/api/annotate", json={"pair_id": "1", "label": "Related"})
    assert r.status_code == 400
    r = c.post("/start", data={"name": "x"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")


def test_requires_session(make_client):
    c = make_client()
    assert c.get("/annotate").status_code == 302
    assert c.get("/api/progress").status_code == 401
    assert c.get("/export").status_code == 302


def test_resume_on_new_device(make_client):
    a = make_client()
    aid, code, tok = start_session(a)
    label(a, tok, "1", "Related")
    label(a, tok, "2", "Related")
    b = make_client()  # e.g. another device: no cookies
    resp = b.post("/resume", data={"resume_code": code.lower().replace("-", " "), "csrf_token": csrf_of(b)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/annotate")
    with b.session_transaction() as s:
        assert s["annotator_id"] == aid
    assert "Pair 3 of 100" in b.get("/annotate").get_data(as_text=True)


def test_bad_resume_code_does_not_create_session(app, make_client):
    c = make_client()
    before = app.extensions["store"].list_annotator_ids()
    resp = c.post("/resume", data={"resume_code": "AAAA-BBBB-CCCC-DDDD", "csrf_token": csrf_of(c)})
    assert resp.status_code == 302
    with c.session_transaction() as s:
        assert "annotator_id" not in s
    assert app.extensions["store"].list_annotator_ids() == before


def test_lost_file_does_not_silently_restore(app, make_client):
    import os
    c = make_client()
    aid, _, _ = start_session(c)
    os.remove(app.extensions["store"].path_for(aid))
    resp = c.get("/annotate")
    assert resp.status_code == 302
    assert "couldn" in c.get("/").get_data(as_text=True)


def test_completion_flow(make_client):
    c = make_client()
    _, _, tok = start_session(c)
    labels = ["Same Story", "Related", "Unrelated"]
    for i in range(1, 100):
        assert label(c, tok, str(i), labels[i % 3]).get_json()["redirect"] is None
    last = label(c, tok, "100", "Related").get_json()
    assert last["all_complete"] and last["redirect"].endswith("/complete")
    assert c.get("/annotate").status_code == 302  # complete -> completion page
    html = c.get("/complete").get_data(as_text=True)
    assert "Annotation Complete!" in html and "Review My Annotations" in html
    # Reviewing after completion is still possible and keeps everything.
    assert "Pair 10 of 100" in c.get("/annotate?pair=10").get_data(as_text=True)
    r = label(c, tok, "10", "Unrelated").get_json()
    assert r["completed"] == 100 and r["redirect"] is None and r["next_id"] == "11"


def test_master_unchanged_after_annotating(app, make_client):
    master = app.config["MASTER_WORKBOOK"]
    before = sha256(master)
    c = make_client()
    _, _, tok = start_session(c)
    label(c, tok, "1", "Related", notes="=HYPERLINK(\"x\")")
    assert sha256(master) == before


def test_formula_injection_stored_as_text(app, make_client):
    c = make_client()
    aid, _, tok = start_session(c, "=cmd|' /C calc'!A0")
    label(c, tok, "1", "Related", notes="=1+1")
    wb = openpyxl.load_workbook(app.extensions["store"].path_for(aid))
    ws = wb["Article-pairs"]
    row = next(r for r in ws.iter_rows(min_row=2) if r[0].value == 1)
    assert row[9].data_type == "s"
    assert wb["Annotator-info"]["B3"].data_type == "s"


def test_notes_endpoint(make_client):
    c = make_client()
    _, _, tok = start_session(c)
    r = c.post("/api/notes", json={"pair_id": "4", "notes": "tricky one"}, headers={"X-CSRF-Token": tok})
    assert r.get_json()["ok"]
    data = c.get("/api/pairs/4").get_json()
    assert data["notes"] == "tricky one" and data["label"] is None
    assert c.get("/api/progress").get_json()["completed"] == 0


def test_corrupted_file_recovers_from_backup(app, make_client):
    c = make_client()
    aid, _, tok = start_session(c)
    label(c, tok, "1", "Related")
    label(c, tok, "2", "Unrelated")  # .bak now holds the state after pair 1
    path = app.extensions["store"].path_for(aid)
    with open(path, "wb") as fh:
        fh.write(b"not a zip file")
    prog = c.get("/api/progress").get_json()
    assert prog["ok"] and prog["labels"].get("1") == "Related"
    # Next save rebuilds the main file from the backup.
    assert label(c, tok, "3", "Related").get_json()["ok"]
    openpyxl.load_workbook(path)
