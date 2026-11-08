"""Read-only access to the master article-pair workbook.

The master workbook is never written to. Data is cached in memory and only
re-read if the file's modification time changes.
"""
import os
import threading

from openpyxl import load_workbook

PAIRS_SHEET = "Article-pairs"
LABELS_SHEET = "Label-info"

# Canonical field name -> header text in the workbook.
COLUMNS = {
    "pair_no": "Pair #",
    "a_headline": "Article A - Headline",
    "a_subhead": "Article A - Subhead",
    "a_paragraph": "Article A - First paragraph",
    "link_text": "Link text",
    "b_headline": "Article B - Headline",
    "b_subhead": "Article B - Subhead",
    "b_paragraph": "Article B - First paragraph",
    "label": "Label",
    "notes": "Notes",
}

DEFAULT_LABELS = [
    {
        "name": "Same Story",
        "description": "B covers an earlier stage of the same event or ongoing development as A. "
        "It tells you what happened before A.",
    },
    {
        "name": "Related",
        "description": "Same topic, person, or issue, but a different story. If B is an analysis "
        "or explainer about the same event, choose Related.",
    },
    {"name": "Unrelated", "description": "No meaningful connection."},
]


class DatasetError(RuntimeError):
    pass


def _norm_header(value):
    return " ".join(str(value or "").replace("–", "-").split()).strip().lower()


def normalize_pair_id(value):
    """Return a canonical string id for a Pair # cell (1, 1.0 and '1' -> '1')."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        as_float = float(text)
        if as_float.is_integer():
            return str(int(as_float))
    except ValueError:
        pass
    return text


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def header_map(ws):
    """Map canonical field -> 1-based column index using the header row."""
    found = {}
    wanted = {_norm_header(h): key for key, h in COLUMNS.items()}
    for cell in ws[1]:
        key = wanted.get(_norm_header(cell.value))
        if key and key not in found:
            found[key] = cell.column
    missing = [COLUMNS[k] for k in ("pair_no",) if k not in found]
    if missing:
        raise DatasetError(f"Worksheet '{ws.title}' is missing required column(s): {missing}")
    return found


class MasterDataset:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._stamp = None
        self._pairs = []
        self._by_id = {}
        self._labels = DEFAULT_LABELS

    def _stat(self):
        st = os.stat(self.path)
        return (st.st_mtime_ns, st.st_size)

    def _ensure_loaded(self):
        try:
            stamp = self._stat()
        except FileNotFoundError as exc:
            raise DatasetError(f"Master workbook not found at {self.path}") from exc
        if stamp == self._stamp:
            return
        with self._lock:
            if stamp == self._stamp:
                return
            wb = load_workbook(self.path, read_only=True, data_only=True)
            try:
                if PAIRS_SHEET not in wb.sheetnames:
                    raise DatasetError(f"Worksheet '{PAIRS_SHEET}' not found in master workbook")
                ws = wb[PAIRS_SHEET]
                cols = header_map_readonly(ws)
                pairs, by_id = [], {}
                for row in ws.iter_rows(min_row=2, values_only=True):
                    def get(key):
                        idx = cols.get(key)
                        return row[idx - 1] if idx and idx - 1 < len(row) else None

                    pid = normalize_pair_id(get("pair_no"))
                    if pid is None or pid in by_id:
                        continue  # blank row (or duplicate id): skip
                    pair = {"id": pid}
                    for key in COLUMNS:
                        if key not in ("pair_no", "label"):
                            pair[key] = _text(get(key))
                    pair["index"] = len(pairs)
                    pairs.append(pair)
                    by_id[pid] = pair

                labels = []
                if LABELS_SHEET in wb.sheetnames:
                    for row in wb[LABELS_SHEET].iter_rows(min_row=2, values_only=True):
                        if row and row[0] and str(row[0]).strip():
                            labels.append({
                                "name": str(row[0]).strip(),
                                "description": _text(row[1] if len(row) > 1 else ""),
                            })
            finally:
                wb.close()
            if not pairs:
                raise DatasetError("No article pairs found in master workbook")
            self._pairs, self._by_id = pairs, by_id
            self._labels = labels or DEFAULT_LABELS
            self._stamp = stamp

    @property
    def pairs(self):
        self._ensure_loaded()
        return self._pairs

    @property
    def pair_ids(self):
        return [p["id"] for p in self.pairs]

    def get(self, pair_id):
        self._ensure_loaded()
        return self._by_id.get(normalize_pair_id(pair_id))

    @property
    def labels(self):
        self._ensure_loaded()
        return self._labels

    @property
    def label_names(self):
        return [l["name"] for l in self.labels]

    def __len__(self):
        return len(self.pairs)


def header_map_readonly(ws):
    first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    wanted = {_norm_header(h): key for key, h in COLUMNS.items()}
    found = {}
    for i, value in enumerate(first, start=1):
        key = wanted.get(_norm_header(value))
        if key and key not in found:
            found[key] = i
    if "pair_no" not in found:
        raise DatasetError(f"Worksheet '{ws.title}' has no 'Pair #' column")
    return found
