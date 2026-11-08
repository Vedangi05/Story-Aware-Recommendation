# POPROX News Article Annotation Tool

A lightweight Flask web app where multiple participants independently label the
relationship between 100 pairs of news articles as **Same Story**, **Related** or
**Unrelated**. There is no database: every participant's answers are stored in
their own Excel workbook on the server.

```
data/article-link-annotation.xlsx   master dataset (read-only, ships with the app)
annotations/annotator_<id>.xlsx     one results workbook per participant
annotations/annotator_<id>.bak.xlsx previous version, used for automatic recovery
```

## How it works

| Step | What happens |
|---|---|
| Participant enters a name and clicks **Start Annotating** | The server generates a random resume code (e.g. `K7QM-3XRP-…`), derives an internal 12-character annotator ID from it, and creates `annotations/annotator_<id>.xlsx` – a full copy of the master workbook. The ID is stored in a signed session cookie. |
| Participant clicks a label (or presses 1/2/3) | `POST /api/annotate` writes the label + a UTC timestamp into *their* workbook under an exclusive file lock, saves atomically, re-reads to confirm, and the next pair loads without a page refresh. |
| Participant returns later | Same browser → continues automatically at the first unannotated pair. New device or cleared cookies → enter the resume code on the home page. |
| Researcher | Signs in at `/admin` with `ADMIN_SECRET` to see everyone's progress and download a combined workbook or a ZIP of all files. |

Names are never used as filenames, so two people called "Sam" get separate files.
The plaintext resume code is never stored on the server (only its SHA-256-derived ID).

### Results workbook layout
* **Article-pairs** – the original 10 columns (article data untouched, labels in
  `Label`, optional comments in `Notes`) plus an `Annotated At` (UTC) column.
* **Label-info** – the original label definitions.
* **Annotator-info** – annotator ID, display name, created date.

### Researcher combined export (`/admin/export?format=combined`)
Sheet **All-annotations**: one row per annotator per pair – Annotator ID, Display Name,
Pair #, Article A Headline, Article B Headline, Link Text, Selected Label, Notes,
Annotation Timestamp. Sheet **Annotator-summary**: counts per annotator.
`?format=zip` returns every individual workbook plus the combined file.

For scripts: `curl -H "Authorization: Bearer $ADMIN_SECRET" -o all.xlsx https://<your-site>/admin/export`

## Run locally

```bash
cd poprox-annotation
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export ADMIN_SECRET=choose-a-password        # enables /admin
python app.py                                # http://localhost:5000
```

Run the tests (they use temporary copies, never your real data):

```bash
python -m pytest -q
```

## Deploy

**Requirement:** the `annotations/` directory must live on a **persistent disk**.
Most free/"serverless" hosts wipe the filesystem on every restart or deploy – that
would delete participants' answers. Run **one instance** only; file locks protect
concurrent writes on one machine but not across several servers.

### Option S – Streamlit Community Cloud (free)
`streamlit_app.py` runs this same Flask app inside Streamlit and shows its pages in a
custom component (`streamlit_bridge/frontend/index.html`) that forwards every link,
form and API call to Flask. Streamlit Cloud deletes local files on restart, so each
participant workbook is also copied to **Supabase Storage** and restored on startup
(`streamlit_bridge/supabase_sync.py`).

1. Supabase (free): create a project, then **Storage → New bucket** named `annotations`
   (keep it **private**). Copy the Project URL and the `service_role` key from
   **Project Settings → API**.
2. Push this folder to a GitHub repository (secrets and `annotations/` are git-ignored).
3. share.streamlit.io → **Create app** → pick the repo, main file `streamlit_app.py`,
   Python 3.12 under **Advanced settings**, and paste the values from
   `.streamlit/secrets.toml.example` into **Secrets**.
4. Share `https://<your-app>.streamlit.app`. Researcher page: `https://<your-app>.streamlit.app/?page=admin`.

Run it locally with `streamlit run streamlit_app.py`. Without Supabase settings, a red
banner warns that answers are not backed up.

### Option A – Render (simplest)
1. Push this folder to a GitHub repository.
2. In Render: **New → Blueprint**, pick the repo. `render.yaml` creates a web service
   with a 1 GB persistent disk mounted at `/var/data` (a paid *Starter* instance is
   required for disks).
3. When prompted, enter an `ADMIN_SECRET`. `SECRET_KEY` is generated automatically.
4. Share the `https://….onrender.com` URL with participants.

### Option B – Fly.io
```bash
fly launch --no-deploy            # uses the included Dockerfile and fly.toml
fly volumes create annotations --size 1 --count 1
fly secrets set SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") ADMIN_SECRET=...
fly deploy
```

### Option C – Any VPS / university server (Docker)
```bash
docker build -t poprox-annotation .
docker run -d -p 80:8000 \
  -v /srv/poprox-data:/var/data \
  -e SECRET_KEY=... -e ADMIN_SECRET=... -e SESSION_COOKIE_SECURE=1 -e TRUST_PROXY=1 \
  --restart unless-stopped poprox-annotation
```
Put it behind HTTPS (e.g. Caddy or nginx + Let's Encrypt).

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Signs session cookies. **Set in production.** | If unset, a key is generated once and stored in `annotations/.secret_key` |
| `ADMIN_SECRET` | Password for `/admin` and `/admin/export` | unset → admin area disabled (404) |
| `ANNOTATIONS_DIR` | Where result workbooks are written – point at the persistent disk | `./annotations` |
| `MASTER_WORKBOOK` | Path to the master dataset | `./data/article-link-annotation.xlsx` |
| `SESSION_COOKIE_SECURE` | `1` when served over HTTPS | `0` |
| `TRUST_PROXY` | `1` behind a reverse proxy (Render, Fly, nginx) | `0` |
| `SESSION_DAYS` | How long a browser session stays valid | `120` |

### Backups
Download the ZIP from `/admin` regularly, or copy the volume directory. Each
participant file is self-contained and opens directly in Excel.

### Changing the dataset
Replace `data/article-link-annotation.xlsx` (same sheet/column names) **before**
participants start. Existing participant files are copies of the old dataset, so
don't swap the dataset mid-study.

## Project structure

```
app.py                     Flask routes, CSRF, sessions, error handling
config.py                  Environment-based configuration
services/excel_reader.py   Read-only master dataset loader (skips blank rows, cached)
services/annotator_manager.py  Resume codes and safe annotator IDs
services/annotation_manager.py Per-annotator workbooks: create, read, locked atomic saves
services/storage.py        File locking (fcntl) + atomic replace + backup
services/export_manager.py Participant / researcher exports
templates/                 Jinja2 pages (landing, annotate, guidelines, progress, complete, admin)
static/css/style.css       POPROX theme
static/js/annotation.js    Async labelling, keyboard shortcuts, navigation
static/js/vendor/          Lucide icons (bundled locally, ISC licence)
tests/                     pytest suite (30 tests incl. concurrency)
```

## Keyboard shortcuts
`1` Same Story · `2` Related · `3` Unrelated · `←` previous pair · `→` next pair
(disabled while typing in a text box or when a dialog is open).
