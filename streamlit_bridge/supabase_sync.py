"""Keep participants' Excel workbooks safe in Supabase Storage.

Streamlit Community Cloud deletes files written by the app whenever the app
restarts, sleeps or is redeployed. The Flask app still saves every answer to
``annotations/annotator_<id>.xlsx`` exactly as before; this module adds two
things around it:

* on startup, ``download_missing()`` copies every workbook in the bucket back
  into the annotations folder, so participants can resume;
* after a request changes a workbook, ``upload_later()`` queues the file and a
  background thread uploads it to the bucket (retrying until it succeeds).

Configuration (Streamlit secrets or environment variables):
    SUPABASE_URL      e.g. https://abcdefgh.supabase.co
    SUPABASE_KEY      the project's *service_role* key (server-side only, never shown to users)
    SUPABASE_BUCKET   bucket name, default "annotations" (create it as a *private* bucket)
"""
import logging
import os
import queue
import re
import threading
import time

import requests

from services.storage import file_lock

log = logging.getLogger("poprox.sync")

# Only the main workbooks are synced: not the local .bak copies, lock files or temp files.
WORKBOOK_RE = re.compile(r"^annotator_[A-Za-z0-9]+\.xlsx$")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook_mtimes(directory):
    """{filename: modification time} for every main workbook in `directory`."""
    out = {}
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if WORKBOOK_RE.match(entry.name):
                    out[entry.name] = entry.stat().st_mtime_ns
    except FileNotFoundError:
        pass
    return out


class SupabaseSync:
    def __init__(self, directory, url, key, bucket="annotations"):
        self.directory = directory
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.http = requests.Session()
        self.http.headers.update({"Authorization": "Bearer " + key, "apikey": key})
        self._queue = queue.Queue()
        self._pending = set()
        self._pending_lock = threading.Lock()
        self.last_error = None
        threading.Thread(target=self._worker, name="supabase-sync", daemon=True).start()

    @classmethod
    def from_env(cls, directory):
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_KEY", "").strip()
        if not url or not key:
            return None
        return cls(directory, url, key, os.environ.get("SUPABASE_BUCKET", "annotations").strip() or "annotations")

    # ------------------------------------------------------------- startup
    def list_remote(self):
        names, offset = [], 0
        while True:
            resp = self.http.post(f"{self.base}/object/list/{self.bucket}", timeout=30, json={
                "prefix": "", "limit": 1000, "offset": offset,
                "sortBy": {"column": "name", "order": "asc"}})
            resp.raise_for_status()
            batch = resp.json()
            names += [o["name"] for o in batch if WORKBOOK_RE.match(o.get("name", ""))]
            if len(batch) < 1000:
                return names
            offset += 1000

    def download_missing(self):
        """Copy every workbook from the bucket that isn't already on this server."""
        os.makedirs(self.directory, exist_ok=True)
        local = workbook_mtimes(self.directory)
        restored = 0
        remote = set(self.list_remote())
        for name in sorted(remote):
            if name in local:
                continue
            resp = self.http.get(f"{self.base}/object/{self.bucket}/{name}", timeout=60)
            resp.raise_for_status()
            path = os.path.join(self.directory, name)
            tmp = path + ".download"
            with open(tmp, "wb") as fh:
                fh.write(resp.content)
            os.replace(tmp, path)
            restored += 1
        # Workbooks that exist here but not in the bucket (e.g. created while
        # offline) are uploaded so the bucket is complete.
        for name in local:
            if name not in remote:
                self.upload_later(name)
        log.info("Restored %d workbook(s) from Supabase", restored)
        return restored

    # ------------------------------------------------------------- uploads
    def upload_later(self, name):
        with self._pending_lock:
            if name in self._pending:
                return
            self._pending.add(name)
        self._queue.put(name)

    def pending_count(self):
        with self._pending_lock:
            return len(self._pending)

    def _upload(self, name):
        path = os.path.join(self.directory, name)
        with file_lock(path):  # read a consistent copy, never a half-saved one
            with open(path, "rb") as fh:
                data = fh.read()
        resp = self.http.post(f"{self.base}/object/{self.bucket}/{name}", data=data, timeout=60,
                              headers={"Content-Type": XLSX_MIME, "x-upsert": "true",
                                       "Cache-Control": "no-cache"})
        resp.raise_for_status()

    def _worker(self):
        while True:
            name = self._queue.get()
            # Remove from "pending" first, so a save that happens during the upload queues another one.
            with self._pending_lock:
                self._pending.discard(name)
            delay = 2
            while True:
                try:
                    self._upload(name)
                    self.last_error = None
                    break
                except FileNotFoundError:
                    break
                except Exception as exc:  # network trouble, Supabase paused, ...
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    log.warning("Upload of %s failed (%s); retrying in %ss", name, exc, delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
