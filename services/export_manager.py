"""Participant and researcher exports."""
import io
import zipfile
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .annotation_manager import AnnotationFileError, set_text

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="202A38")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def participant_filename(annotator_id):
    return f"POPROX_Annotations_{annotator_id}_{today()}.xlsx"


def _style_header(ws, widths):
    for idx, width in enumerate(widths, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[cell.column_letter].width = width
    ws.freeze_panes = "A2"


def annotator_overview(store):
    """List of dicts summarising every annotator (used by the admin page)."""
    out = []
    for aid in store.list_annotator_ids():
        try:
            st = store.state(aid)
            prog = store.progress(aid)
            out.append({
                "id": aid,
                "name": st["display_name"],
                "created_at": st["created_at"],
                "completed": prog["completed"],
                "total": prog["total"],
                "counts": prog["counts"],
                "last_activity": max(st["timestamps"].values(), default=""),
                "error": None,
            })
        except AnnotationFileError as exc:
            out.append({"id": aid, "name": "", "created_at": "", "completed": 0,
                        "total": len(store.dataset), "counts": {}, "last_activity": "",
                        "error": str(exc)})
    return out


def combined_workbook_bytes(store):
    dataset = store.dataset
    wb = Workbook()
    ws = wb.active
    ws.title = "All-annotations"
    headers = ["Annotator ID", "Display Name", "Pair #", "Article A Headline",
               "Article B Headline", "Link Text", "Selected Label", "Notes",
               "Annotation Timestamp (UTC)"]
    ws.append(headers)
    _style_header(ws, [16, 22, 8, 50, 50, 30, 16, 30, 24])

    summary = wb.create_sheet("Annotator-summary")
    label_names = dataset.label_names
    summary.append(["Annotator ID", "Display Name", "Created At (UTC)", "Completed",
                    "Total", *label_names, "Status"])
    _style_header(summary, [16, 22, 22, 11, 8, *([12] * len(label_names)), 30])

    for aid in store.list_annotator_ids():
        try:
            st = store.state(aid)
        except AnnotationFileError as exc:
            summary.append([aid, "", "", 0, len(dataset), *([0] * len(label_names)), f"Unreadable: {exc}"])
            continue
        counts = {n: 0 for n in label_names}
        for pair in dataset.pairs:
            pid = pair["id"]
            label = st["labels"].get(pid, "")
            if label:
                counts[label] = counts.get(label, 0) + 1
            row = [aid, st["display_name"], int(pid) if pid.isdigit() else pid,
                   pair["a_headline"], pair["b_headline"], pair["link_text"], label,
                   st["notes"].get(pid, ""), st["timestamps"].get(pid, "")]
            ws.append([None] * len(row))
            r = ws.max_row
            for c, value in enumerate(row, start=1):
                set_text(ws.cell(row=r, column=c), value if value != "" else None)
        done = sum(counts.values())
        summary.append([aid, None, st["created_at"], done, len(dataset),
                        *[counts[n] for n in label_names],
                        "Complete" if done == len(dataset) else "In progress"])
        set_text(summary.cell(row=summary.max_row, column=2), st["display_name"] or None)

    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def zip_bytes(store):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for aid in store.list_annotator_ids():
            try:
                zf.writestr(f"annotator_{aid}.xlsx", store.read_bytes(aid))
            except OSError:
                continue
        zf.writestr(f"POPROX_All_Annotations_{today()}.xlsx", combined_workbook_bytes(store))
    return buf.getvalue()
