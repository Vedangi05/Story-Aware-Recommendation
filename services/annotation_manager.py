"""Per-annotator result workbooks.

Each participant gets ``annotations/annotator_<id>.xlsx``: a full copy of the
master workbook (all article data, Notes column, Label-info sheet) plus

* their labels in the ``Label`` column,
* an ``Annotated At`` timestamp column, and
* an ``Annotator-info`` sheet holding their display name and created date.

All writes happen under an exclusive per-file lock and are saved atomically.
"""
import os
import threading
from datetime import datetime, timezone

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from .annotator_manager import is_valid_annotator_id
from .excel_reader import PAIRS_SHEET, header_map, header_map_readonly, normalize_pair_id
from .storage import atomic_write, backup_path, file_lock

INFO_SHEET = "Annotator-info"
TIMESTAMP_HEADER = "Annotated At"


class AnnotationFileError(RuntimeError):
    """The participant's results file is missing or unreadable."""


def _utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def set_text(cell, value):
    """Write a value as plain text (prevents '=...' being stored as a formula)."""
    cell.value = value
    if isinstance(value, str) and value:
        cell.data_type = "s"


class AnnotationStore:
    def __init__(self, directory, dataset):
        self.directory = os.path.abspath(directory)
        self.dataset = dataset
        os.makedirs(self.directory, exist_ok=True)
        self._cache = {}
        self._cache_lock = threading.Lock()

    # -- paths -------------------------------------------------------------
    def path_for(self, annotator_id):
        if not is_valid_annotator_id(annotator_id):
            raise ValueError("Invalid annotator id")
        path = os.path.join(self.directory, f"annotator_{annotator_id}.xlsx")
        # Belt and braces: never leave the annotations directory.
        if os.path.dirname(os.path.abspath(path)) != self.directory:
            raise ValueError("Invalid annotator path")
        return path

    def exists(self, annotator_id):
        try:
            return os.path.isfile(self.path_for(annotator_id))
        except ValueError:
            return False

    def list_annotator_ids(self):
        ids = []
        for name in sorted(os.listdir(self.directory)):
            if name.startswith("annotator_") and name.endswith(".xlsx"):
                aid = name[len("annotator_"):-len(".xlsx")]
                if is_valid_annotator_id(aid):
                    ids.append(aid)
        return ids

    # -- create ------------------------------------------------------------
    def create(self, annotator_id, display_name):
        path = self.path_for(annotator_id)
        os.makedirs(self.directory, exist_ok=True)
        with file_lock(path):
            if os.path.exists(path):
                raise FileExistsError(path)
            wb = load_workbook(self.dataset.path)  # full mode keeps formatting
            ws = wb[PAIRS_SHEET]
            cols = header_map(ws)
            if "label" not in cols:
                cols["label"] = ws.max_column + 1
                set_text(ws.cell(row=1, column=cols["label"]), "Label")
            if "notes" not in cols:
                cols["notes"] = ws.max_column + 1
                set_text(ws.cell(row=1, column=cols["notes"]), "Notes")
            ts_col = ws.max_column + 1
            header = ws.cell(row=1, column=ts_col)
            set_text(header, TIMESTAMP_HEADER)
            src = ws.cell(row=1, column=cols["notes"])
            if src.has_style:
                header.font = src.font.copy()
                header.fill = src.fill.copy()
                header.alignment = src.alignment.copy()
                header.border = src.border.copy()
            ws.column_dimensions[header.column_letter].width = 22

            if INFO_SHEET in wb.sheetnames:
                del wb[INFO_SHEET]
            info = wb.create_sheet(INFO_SHEET)
            rows = [
                ("Field", "Value"),
                ("Annotator ID", annotator_id),
                ("Display name", display_name),
                ("Created at (UTC)", _utcnow()),
                ("Source dataset", os.path.basename(self.dataset.path)),
                ("Total pairs", len(self.dataset)),
            ]
            for r, (k, v) in enumerate(rows, start=1):
                set_text(info.cell(row=r, column=1), k)
                set_text(info.cell(row=r, column=2), v)
            for c in (info["A1"], info["B1"]):
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="202A38")
            info.column_dimensions["A"].width = 20
            info.column_dimensions["B"].width = 48

            atomic_write(path, wb.save, keep_backup=False)
        self._invalidate(annotator_id)
        return path

    # -- read ----------------------------------------------------------------
    def _load_rw(self, path):
        """Load a workbook for editing, falling back to the .bak copy."""
        errors = []
        for candidate in (path, backup_path(path)):
            if not os.path.exists(candidate):
                continue
            try:
                wb = load_workbook(candidate)
                if PAIRS_SHEET not in wb.sheetnames:
                    raise AnnotationFileError("missing Article-pairs sheet")
                return wb, candidate != path
            except Exception as exc:  # corrupted zip / xml
                errors.append(f"{os.path.basename(candidate)}: {exc}")
        if not errors:
            raise AnnotationFileError("Results file not found")
        raise AnnotationFileError("Results file could not be read (" + "; ".join(errors) + ")")

    def _parse(self, path):
        last_exc = None
        for candidate in (path, backup_path(path)):
            if not os.path.exists(candidate):
                continue
            try:
                return self._parse_file(candidate)
            except Exception as exc:
                last_exc = exc
        if last_exc is None:
            raise AnnotationFileError("Results file not found")
        raise AnnotationFileError(f"Results file could not be read: {last_exc}")

    def _parse_file(self, path):
        valid = set(self.dataset.label_names)
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[PAIRS_SHEET]
            cols = header_map_readonly(ws)
            first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
            ts_idx = next((i for i, v in enumerate(first, 1) if str(v or "").strip() == TIMESTAMP_HEADER), None)
            labels, notes, stamps = {}, {}, {}
            for row in ws.iter_rows(min_row=2, values_only=True):
                pid = normalize_pair_id(row[cols["pair_no"] - 1] if row else None)
                if pid is None:
                    continue
                def get(idx):
                    return row[idx - 1] if idx and idx - 1 < len(row) else None
                label = get(cols.get("label"))
                if isinstance(label, str) and label.strip() in valid:
                    labels[pid] = label.strip()
                note = get(cols.get("notes"))
                if note not in (None, ""):
                    notes[pid] = str(note)
                ts = get(ts_idx)
                if ts not in (None, ""):
                    stamps[pid] = str(ts)
            meta = {}
            if INFO_SHEET in wb.sheetnames:
                for k, v, *_ in wb[INFO_SHEET].iter_rows(min_row=2, values_only=True):
                    if k:
                        meta[str(k)] = "" if v is None else str(v)
        finally:
            wb.close()
        return {
            "labels": labels,
            "notes": notes,
            "timestamps": stamps,
            "display_name": meta.get("Display name", ""),
            "created_at": meta.get("Created at (UTC)", ""),
        }

    def state(self, annotator_id):
        """Parsed annotations for one annotator (cached until the file changes)."""
        path = self.path_for(annotator_id)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            raise AnnotationFileError("Results file not found")
        stamp = (st.st_mtime_ns, st.st_size)
        with self._cache_lock:
            hit = self._cache.get(annotator_id)
            if hit and hit[0] == stamp:
                return hit[1]
        parsed = self._parse(path)
        with self._cache_lock:
            self._cache[annotator_id] = (stamp, parsed)
        return parsed

    def _invalidate(self, annotator_id):
        with self._cache_lock:
            self._cache.pop(annotator_id, None)

    # -- write -------------------------------------------------------------
    def _update(self, annotator_id, pair_id, label=None, notes=None, set_label=True):
        path = self.path_for(annotator_id)
        pid = normalize_pair_id(pair_id)
        if self.dataset.get(pid) is None:
            raise KeyError(pair_id)
        with file_lock(path):
            if not os.path.exists(path):
                raise AnnotationFileError("Results file not found")
            wb, from_backup = self._load_rw(path)
            ws = wb[PAIRS_SHEET]
            cols = header_map(ws)
            ts_col = next(
                (c.column for c in ws[1] if str(c.value or "").strip() == TIMESTAMP_HEADER), None
            )
            target = None
            for r in range(2, ws.max_row + 1):
                if normalize_pair_id(ws.cell(row=r, column=cols["pair_no"]).value) == pid:
                    target = r
                    break
            if target is None:
                raise KeyError(pair_id)
            label_cell = ws.cell(row=target, column=cols["label"])
            previous = label_cell.value.strip() if isinstance(label_cell.value, str) else None
            was_new = previous not in set(self.dataset.label_names)
            if set_label:
                set_text(label_cell, label)
                if ts_col:
                    set_text(ws.cell(row=target, column=ts_col), _utcnow())
            if notes is not None and "notes" in cols:
                set_text(ws.cell(row=target, column=cols["notes"]), notes or None)
            # Don't overwrite a good backup with a damaged main file.
            atomic_write(path, wb.save, keep_backup=not from_backup)
        self._invalidate(annotator_id)
        return was_new

    def save_label(self, annotator_id, pair_id, label, notes=None):
        if label not in self.dataset.label_names:
            raise ValueError("Invalid label")
        return self._update(annotator_id, pair_id, label=label, notes=notes, set_label=True)

    def save_notes(self, annotator_id, pair_id, notes):
        self._update(annotator_id, pair_id, notes=notes, set_label=False)

    def read_bytes(self, annotator_id):
        path = self.path_for(annotator_id)
        with file_lock(path):
            with open(path, "rb") as fh:
                return fh.read()

    # -- progress ------------------------------------------------------------
    def progress(self, annotator_id):
        st = self.state(annotator_id)
        ids = self.dataset.pair_ids
        labels = {pid: st["labels"][pid] for pid in ids if pid in st["labels"]}
        counts = {name: 0 for name in self.dataset.label_names}
        for value in labels.values():
            counts[value] = counts.get(value, 0) + 1
        first_open = next((pid for pid in ids if pid not in labels), None)
        return {
            "completed": len(labels),
            "total": len(ids),
            "counts": counts,
            "labels": labels,
            "first_unannotated": first_open,
            "all_complete": first_open is None,
        }
