import threading

import openpyxl

from conftest import label, start_session


def test_users_see_same_pairs(make_client):
    a, b = make_client(), make_client()
    start_session(a, "A")
    start_session(b, "B")
    for pid in ("1", "17", "100"):
        pa = a.get(f"/api/pairs/{pid}").get_json()["pair"]
        pb = b.get(f"/api/pairs/{pid}").get_json()["pair"]
        assert pa == pb


def test_same_name_gets_separate_files(app, make_client):
    a, b = make_client(), make_client()
    aid1, code1, _ = start_session(a, "Sam")
    aid2, code2, _ = start_session(b, "Sam")
    assert aid1 != aid2 and code1 != code2
    store = app.extensions["store"]
    assert store.path_for(aid1) != store.path_for(aid2)
    assert "Sam" not in store.path_for(aid1)


def test_independent_labels(app, make_client):
    a, b = make_client(), make_client()
    aid_a, _, ta = start_session(a, "A")
    aid_b, _, tb = start_session(b, "B")
    label(a, ta, "1", "Same Story")
    label(b, tb, "1", "Unrelated")
    label(a, ta, "2", "Related")
    assert a.get("/api/pairs/1").get_json()["label"] == "Same Story"
    assert b.get("/api/pairs/1").get_json()["label"] == "Unrelated"
    assert b.get("/api/pairs/2").get_json()["label"] is None
    assert a.get("/api/progress").get_json()["completed"] == 2
    assert b.get("/api/progress").get_json()["completed"] == 1


def test_concurrent_saves_do_not_corrupt(app):
    """Many threads hammer one annotator's file and several others at once."""
    users = []
    for i in range(4):
        c = app.test_client()
        users.append((c, *start_session(c, f"U{i}")))
    labels = ["Same Story", "Related", "Unrelated"]
    errors = []

    def worker(client, token, offset, start, step):
        for n in range(start, 101, step):
            try:
                r = label(client, token, str(n), labels[(n + offset) % 3])
                if r.status_code != 200:
                    errors.append(r.get_json())
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

    threads = []
    for idx, (client, _aid, _code, tok) in enumerate(users):
        # Two threads per user writing interleaved pairs to the same file.
        for start in (1, 2):
            # Each thread needs its own client sharing the same cookie jar is not
            # thread-safe, so clone the session cookie into a new client.
            t_client = app.test_client()
            with client.session_transaction() as s:
                data = dict(s)
            with t_client.session_transaction() as s:
                s.update(data)
            threads.append(threading.Thread(target=worker, args=(t_client, tok, idx, start, 2)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    store = app.extensions["store"]
    for idx, (_c, aid, _code, _t) in enumerate(users):
        wb = openpyxl.load_workbook(store.path_for(aid))  # must open cleanly
        ws = wb["Article-pairs"]
        got = {r[0].value: r[8].value for r in ws.iter_rows(min_row=2) if r[0].value is not None}
        assert len(got) == 100
        assert all(got[n] == labels[(n + idx) % 3] for n in range(1, 101))
        assert store.progress(aid)["completed"] == 100
