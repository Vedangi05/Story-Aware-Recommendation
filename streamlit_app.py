"""Run the POPROX annotation site on Streamlit (Community Cloud or `streamlit run`).

The Flask app in app.py is used exactly as it is: same routes, templates, CSS,
JS and Excel storage. Streamlit can only serve its own pages, so this file:

1. starts the Flask app inside this process (no extra server or port);
2. shows its pages in a custom component (streamlit_bridge/frontend/index.html)
   that forwards every link, form and fetch() to Flask and shows the answer;
3. backs up the participant workbooks to Supabase Storage, because Streamlit
   Cloud deletes local files whenever the app restarts (streamlit_bridge/supabase_sync.py).

Run locally:  streamlit run streamlit_app.py
"""
import base64
import hashlib
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit_bridge.supabase_sync import SupabaseSync, workbook_mtimes  # noqa: E402

FRONTEND_SRC = ROOT / "streamlit_bridge" / "frontend"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("poprox.streamlit")

st.set_page_config(page_title="POPROX News Annotation", page_icon="📰",
                   layout="wide", initial_sidebar_state="collapsed")

# Hide Streamlit's own header, padding and status widgets so the site fills the window.
st.html("""
<style>
  header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
  [data-testid="stStatusWidget"], footer { display: none !important; }
  html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stMain"] { overflow: hidden !important; }
  [data-testid="stMainBlockContainer"], .block-container { padding: 0 !important; max-width: 100% !important; }
  [data-testid="stVerticalBlock"] { gap: 0 !important; }
  [data-testid="stElementContainer"]:has(> iframe), [data-testid="stCustomComponentV1"] { height: 100vh !important; }
  iframe[title*="poprox_bridge"] { display: block; width: 100% !important; height: 100vh !important; }
  .poprox-banner { position: fixed; left: 12px; bottom: 12px; z-index: 1000; max-width: 360px;
                   background: #FEF3F2; color: #B42318; border: 1px solid #FECDCA; border-radius: 10px;
                   padding: 8px 12px; font: 500 13px/1.4 system-ui, sans-serif; }
</style>
""")


def _load_secrets_into_env():
    """Streamlit secrets -> environment variables, which config.py reads."""
    try:
        items = dict(st.secrets)
    except Exception:  # no secrets.toml: fine for local testing
        items = {}
    for key, value in items.items():
        if isinstance(value, (str, int, float, bool)):
            os.environ.setdefault(key, str(value))


@st.cache_resource(show_spinner="Starting the annotation site…")
def boot():
    """Runs once per server process (not once per visitor)."""
    _load_secrets_into_env()
    # Flask only talks to this process, never directly to a browser, so these must be off.
    os.environ["SESSION_COOKIE_SECURE"] = "0"
    os.environ["TRUST_PROXY"] = "0"
    annotations_dir = os.environ.setdefault("ANNOTATIONS_DIR", str(ROOT / "annotations"))
    os.makedirs(annotations_dir, exist_ok=True)

    # 1) Bring participants' workbooks back from Supabase before anyone can use the site.
    sync = SupabaseSync.from_env(annotations_dir)
    if sync is not None:
        sync.download_missing()  # an error here stops startup; Streamlit retries on the next visit
    else:
        log.warning("SUPABASE_URL / SUPABASE_KEY not set: answers are NOT backed up online.")

    # 2) The component folder Streamlit serves: the bridge page plus the site's CSS/JS/images.
    #    Built in a temp folder, so nothing is written into the repository.
    frontend = Path(tempfile.gettempdir()) / "poprox_bridge_frontend"
    shutil.rmtree(frontend, ignore_errors=True)
    shutil.copytree(FRONTEND_SRC, frontend, ignore=shutil.ignore_patterns("static"))
    shutil.copytree(ROOT / "static", frontend / "static")
    digest = hashlib.sha1()
    for path in sorted((ROOT / "static").rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    asset_version = digest.hexdigest()[:10]

    # 3) The unchanged Flask app.
    from app import app as flask_app
    return flask_app, sync, annotations_dir, asset_version, frontend


try:
    flask_app, sync, annotations_dir, asset_version, frontend = boot()
except Exception as exc:  # most likely Supabase is unreachable or paused
    log.exception("Startup failed")
    st.error("The annotation site could not load the saved annotations, so it has not started "
             "(to avoid losing anyone's work). Please try again in a minute.\n\n"
             f"Details for the research team: {type(exc).__name__}: {exc}")
    st.stop()

_bridge = components.declare_component("poprox_bridge", path=str(frontend))
COOKIE_NAME = flask_app.config.get("SESSION_COOKIE_NAME", "session")
STATIC_URL = re.compile(r'(src|href)="/static/([^"?#]+)"')
FORWARDED_HEADERS = {"accept", "content-type", "x-csrf-token"}


def prepare_page(html):
    """Point asset URLs at the component folder and hook the page up to the bridge."""
    html = STATIC_URL.sub(lambda m: f'{m.group(1)}="static/{m.group(2)}?v={asset_version}"', html)
    return html.replace("<head>", "<head><script>parent.POPROX_BRIDGE.install(window);</script>", 1)


def handle(req, client):
    """Run one request from the browser through the Flask app."""
    mode = req.get("mode")
    method = str(req.get("method") or "GET").upper()
    url = str(req.get("url") or "/")
    if not url.startswith("/") or url.startswith("//"):
        url = "/"  # only this site's own pages

    if req.get("cookie"):
        client.set_cookie(COOKIE_NAME, str(req["cookie"]))
    headers = {"Referer": "http://localhost" + str(req.get("referrer") or "/")}
    for key, value in (req.get("headers") or {}).items():
        if key.lower() in FORWARDED_HEADERS:
            headers[key] = str(value)
    if req.get("contentType"):
        headers["Content-Type"] = str(req["contentType"])
    body = req.get("body")
    data = body.encode("utf-8") if isinstance(body, str) else None

    before = workbook_mtimes(annotations_dir) if sync is not None and method != "GET" else None
    resp = client.open(url, method=method, data=data, headers=headers, follow_redirects=(mode == "page"))
    if before is not None:  # queue every workbook this request changed for upload
        for name, mtime in workbook_mtimes(annotations_dir).items():
            if before.get(name) != mtime:
                sync.upload_later(name)

    cookie = client.get_cookie(COOKIE_NAME)
    out = {"id": req.get("id"), "cookie": cookie.value if cookie else None}
    disposition = resp.headers.get("Content-Disposition", "")
    if "attachment" in disposition:
        from werkzeug.http import parse_options_header
        filename = parse_options_header(disposition)[1].get("filename", "download")
        out.update(kind="download", filename=filename, mime=resp.mimetype,
                   b64=base64.b64encode(resp.get_data()).decode("ascii"))
    elif mode == "page":
        path = resp.request.path
        if resp.request.query_string:
            path += "?" + resp.request.query_string.decode("latin-1")
        out.update(kind="page", path=path, html=prepare_page(resp.get_data(as_text=True)))
    else:
        out.update(kind="fetch", status=resp.status_code,
                   contentType=resp.headers.get("Content-Type", ""), body=resp.get_data(as_text=True))
    return out


# One Flask test client (= one browser "cookie jar") per Streamlit visitor session.
ss = st.session_state
if "flask_client" not in ss:
    ss.flask_client = flask_app.test_client()

request_from_page = ss.get("bridge")
if isinstance(request_from_page, dict) and request_from_page.get("id") and \
        request_from_page["id"] != ss.get("handled_id"):
    try:
        ss.last_response = handle(request_from_page, ss.flask_client)
    except Exception:
        log.exception("Request failed: %s %s", request_from_page.get("method"), request_from_page.get("url"))
        ss.last_response = {"id": request_from_page["id"], "kind": "fetch", "status": 500,
                            "contentType": "application/json",
                            "body": '{"ok": false, "error": "Something went wrong. Please try again."}'}
    ss.handled_id = request_from_page["id"]

# Streamlit has no URL paths, so pages like the researcher area are opened with
# ?page=admin (e.g. https://your-app.streamlit.app/?page=admin).
start_page = st.query_params.get("page", "")
start_path = "/" + start_page if re.fullmatch(r"[a-z][a-z_-]{0,30}", start_page or "") else "/"

_bridge(response=ss.get("last_response"), start_path=start_path, key="bridge", default=None)

if sync is None:
    st.html('<div class="poprox-banner">Online backup is OFF (Supabase not configured). '
            'Answers will be lost when the app restarts.</div>')
elif sync.last_error:
    st.html('<div class="poprox-banner">Online backup is retrying. Answers are saved on the server '
            'and will be uploaded when the connection returns.</div>')
