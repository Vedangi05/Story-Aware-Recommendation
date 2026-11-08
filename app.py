"""POPROX News Article Annotation Tool – Flask application."""
import hmac
import io
import logging
import secrets
from functools import wraps

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from config import Config
from services.annotation_manager import AnnotationFileError, AnnotationStore
from services.annotator_manager import (annotator_id_from_code, clean_display_name,
                                        generate_resume_code, is_valid_annotator_id)
from services.excel_reader import MasterDataset, normalize_pair_id
from services import export_manager

log = logging.getLogger("poprox")


def _persistent_secret(directory):
    """Read (or create once) a random signing key so sessions survive restarts
    and are shared by all Gunicorn workers when SECRET_KEY isn't configured."""
    import os
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, ".secret_key")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    with open(path) as fh:
        key = fh.read().strip()
    if not key:  # another worker is mid-write; fall back to waiting briefly
        import time
        time.sleep(0.2)
        with open(path) as fh:
            key = fh.read().strip()
    return key


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if app.config.get("SECRET_KEY_IS_EPHEMERAL") and not app.testing:
        app.config["SECRET_KEY"] = _persistent_secret(app.config["ANNOTATIONS_DIR"])
        log.warning("SECRET_KEY env var not set; using a key stored in the annotations directory. "
                    "Set SECRET_KEY explicitly in production.")

    dataset = MasterDataset(app.config["MASTER_WORKBOOK"])
    store = AnnotationStore(app.config["ANNOTATIONS_DIR"], dataset)
    app.extensions["dataset"] = dataset
    app.extensions["store"] = store

    # ------------------------------------------------------------------ helpers
    def wants_json():
        return request.path.startswith("/api/") or request.is_json

    def csrf_token():
        token = session.get("csrf")
        if not token:
            token = session["csrf"] = secrets.token_urlsafe(32)
        return token

    def reset_session():
        """Clear the session but keep the CSRF token so other open tabs keep working."""
        token = session.get("csrf")
        session.clear()
        if token:
            session["csrf"] = token

    def current_annotator():
        aid = session.get("annotator_id")
        if aid and is_valid_annotator_id(aid) and store.exists(aid):
            return aid
        return None

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            aid = current_annotator()
            if aid is None:
                lost = bool(session.get("annotator_id"))
                session.pop("annotator_id", None)
                session.pop("resume_code", None)
                if wants_json():
                    return jsonify(ok=False, error="Your annotation session has ended. "
                                   "Please resume with your resume code."), 401
                if lost:
                    flash("We couldn't find your annotation session. Enter your resume code to continue.", "warning")
                return redirect(url_for("index"))
            return view(aid, *args, **kwargs)
        return wrapper

    def is_admin():
        return bool(app.config["ADMIN_SECRET"]) and session.get("is_admin") is True

    def check_admin_secret(candidate):
        secret = app.config["ADMIN_SECRET"]
        return bool(secret) and isinstance(candidate, str) and hmac.compare_digest(
            candidate.encode(), secret.encode())

    def pair_payload(aid, pair_id):
        pair = dataset.get(pair_id)
        if pair is None:
            return None
        st = store.state(aid)
        ids = dataset.pair_ids
        i = pair["index"]
        return {
            "pair": {k: pair[k] for k in ("id", "a_headline", "a_subhead", "a_paragraph",
                                           "link_text", "b_headline", "b_subhead", "b_paragraph")},
            "position": i + 1,
            "total": len(ids),
            "label": st["labels"].get(pair["id"]),
            "notes": st["notes"].get(pair["id"], ""),
            "prev_id": ids[i - 1] if i > 0 else None,
            "next_id": ids[i + 1] if i + 1 < len(ids) else None,
        }

    @app.context_processor
    def inject_globals():
        aid = current_annotator()
        name = None
        if aid:
            try:
                name = store.state(aid)["display_name"]
            except AnnotationFileError:
                name = None
        return {
            "csrf_token": csrf_token,
            "annotator_id": aid,
            "annotator_name": name,
            "total_pairs": len(dataset),
            "label_defs": dataset.labels,
        }

    @app.before_request
    def protect():
        session.permanent = True
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(str(sent), expected):
                if wants_json():
                    return jsonify(ok=False, error="Security token expired. Please refresh the page."), 400
                flash("Your form expired. Please try again.", "warning")
                return redirect(request.referrer or url_for("index"))

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'",
        )
        if request.path.startswith(("/api/", "/export", "/admin")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return render_template("index.html", active="home")

    @app.post("/start")
    def start():
        name = clean_display_name(request.form.get("name", ""), app.config["MAX_NAME_LENGTH"])
        if not name:
            flash("Please enter your name or annotator ID to begin.", "error")
            return redirect(url_for("index"))
        for _ in range(5):  # astronomically unlikely to loop more than once
            code = generate_resume_code()
            aid = annotator_id_from_code(code)
            try:
                store.create(aid, name)
                break
            except FileExistsError:
                continue
        else:
            abort(500)
        reset_session()
        session["annotator_id"] = aid
        session["resume_code"] = code
        session["show_code"] = True
        log.info("Created annotator %s", aid)
        return redirect(url_for("annotate"))

    @app.post("/resume")
    def resume():
        code = request.form.get("resume_code", "")
        aid = annotator_id_from_code(code)
        if not aid or not store.exists(aid):
            flash("That resume code wasn't recognised. Please check it and try again.", "error")
            return redirect(url_for("index") + "#resume")
        reset_session()
        session["annotator_id"] = aid
        session["resume_code"] = "-".join(
            [code.replace("-", "").replace(" ", "").upper()[i:i + 4] for i in range(0, 16, 4)])
        flash("Welcome back! Your previous annotations have been restored.", "success")
        return redirect(url_for("annotate"))

    @app.post("/signout")
    def signout():
        reset_session()
        flash("You've signed out. Use your resume code to continue later.", "success")
        return redirect(url_for("index"))

    @app.get("/annotate")
    @login_required
    def annotate(aid):
        prog = store.progress(aid)
        requested = normalize_pair_id(request.args.get("pair"))
        if requested and dataset.get(requested):
            start_id = requested
        elif prog["all_complete"]:
            return redirect(url_for("complete"))
        else:
            start_id = prog["first_unannotated"]
        show_code = session.pop("show_code", False)
        return render_template(
            "annotate.html", active="annotate",
            initial=pair_payload(aid, start_id), progress=prog,
            pair_ids=dataset.pair_ids,
            resume_code=session.get("resume_code") if show_code else None,
        )

    @app.get("/guidelines")
    def guidelines():
        return render_template("guidelines.html", active="guidelines")

    @app.get("/progress")
    @login_required
    def progress_page(aid):
        prog = store.progress(aid)
        return render_template("progress.html", active="progress", progress=prog,
                               pairs=dataset.pairs, resume_code=session.get("resume_code"))

    @app.get("/complete")
    @login_required
    def complete(aid):
        prog = store.progress(aid)
        if not prog["all_complete"]:
            return redirect(url_for("annotate"))
        return render_template("complete.html", active="annotate", progress=prog,
                               resume_code=session.get("resume_code"))

    @app.get("/export")
    @login_required
    def export(aid):
        data = store.read_bytes(aid)
        return send_file(io.BytesIO(data), as_attachment=True,
                         download_name=export_manager.participant_filename(aid),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # --------------------------------------------------------------------- API
    @app.get("/api/pairs/<pair_id>")
    @login_required
    def api_pair(aid, pair_id):
        payload = pair_payload(aid, pair_id)
        if payload is None:
            return jsonify(ok=False, error="Unknown pair"), 404
        return jsonify(ok=True, **payload)

    @app.get("/api/progress")
    @login_required
    def api_progress(aid):
        return jsonify(ok=True, **store.progress(aid))

    def _json_body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        return data

    def _clean_notes(value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("notes")
        return value.strip()[: app.config["MAX_NOTES_LENGTH"]]

    @app.post("/api/annotate")
    @login_required
    def api_annotate(aid):
        data = _json_body()
        pair_id = normalize_pair_id(data.get("pair_id"))
        label = data.get("label")
        if not pair_id or dataset.get(pair_id) is None:
            return jsonify(ok=False, error="Unknown article pair."), 400
        if label not in dataset.label_names:
            return jsonify(ok=False, error="Invalid label."), 400
        try:
            notes = _clean_notes(data.get("notes"))
        except ValueError:
            return jsonify(ok=False, error="Invalid notes."), 400
        before = store.progress(aid)
        try:
            was_new = store.save_label(aid, pair_id, label, notes)
        except AnnotationFileError as exc:
            log.exception("Save failed for %s", aid)
            return jsonify(ok=False, error=f"Could not save your annotation: {exc}"), 500
        except OSError:
            log.exception("Save failed for %s", aid)
            return jsonify(ok=False, error="Could not save your annotation. Please try again."), 500
        prog = store.progress(aid)
        if store.state(aid)["labels"].get(pair_id) != label:
            return jsonify(ok=False, error="Save could not be verified. Please try again."), 500

        just_finished = prog["all_complete"] and not before["all_complete"]
        nxt = pair_payload(aid, pair_id)["next_id"]
        redirect_to = None
        if just_finished or (prog["all_complete"] and nxt is None):
            redirect_to = url_for("complete")
        elif nxt is None:
            nxt = prog["first_unannotated"]
        return jsonify(ok=True, was_new=was_new, label=label, completed=prog["completed"],
                       total=prog["total"], counts=prog["counts"], next_id=nxt,
                       all_complete=prog["all_complete"], redirect=redirect_to)

    @app.post("/api/notes")
    @login_required
    def api_notes(aid):
        data = _json_body()
        pair_id = normalize_pair_id(data.get("pair_id"))
        if not pair_id or dataset.get(pair_id) is None:
            return jsonify(ok=False, error="Unknown article pair."), 400
        try:
            notes = _clean_notes(data.get("notes") or "")
            store.save_notes(aid, pair_id, notes)
        except ValueError:
            return jsonify(ok=False, error="Invalid notes."), 400
        except (AnnotationFileError, OSError):
            log.exception("Note save failed for %s", aid)
            return jsonify(ok=False, error="Could not save your note."), 500
        return jsonify(ok=True)

    # ------------------------------------------------------------------- admin
    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        if not app.config["ADMIN_SECRET"]:
            abort(404)
        if request.method == "POST":
            if check_admin_secret(request.form.get("secret", "")):
                session["is_admin"] = True
                return redirect(url_for("admin"))
            flash("Incorrect admin secret.", "error")
            return redirect(url_for("admin"))
        overview = export_manager.annotator_overview(store) if is_admin() else []
        return render_template("admin.html", active="admin", authed=is_admin(), overview=overview)

    @app.post("/admin/logout")
    def admin_logout():
        session.pop("is_admin", None)
        return redirect(url_for("admin"))

    @app.get("/admin/export")
    def admin_export():
        if not app.config["ADMIN_SECRET"]:
            abort(404)
        bearer = request.headers.get("Authorization", "")
        token_ok = bearer.startswith("Bearer ") and check_admin_secret(bearer[7:])
        if not (is_admin() or token_ok):
            abort(403)
        fmt = request.args.get("format", "combined")
        if fmt == "zip":
            return send_file(io.BytesIO(export_manager.zip_bytes(store)), as_attachment=True,
                             download_name=f"POPROX_All_Annotations_{export_manager.today()}.zip",
                             mimetype="application/zip")
        if fmt != "combined":
            abort(400)
        return send_file(io.BytesIO(export_manager.combined_workbook_bytes(store)), as_attachment=True,
                         download_name=f"POPROX_All_Annotations_{export_manager.today()}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ------------------------------------------------------------------ errors
    def _error(code, title, message):
        if wants_json():
            return jsonify(ok=False, error=message), code
        return render_template("error.html", code=code, title=title, message=message), code

    @app.errorhandler(400)
    def bad_request(_e):
        return _error(400, "Bad request", "The request could not be understood.")

    @app.errorhandler(403)
    def forbidden(_e):
        return _error(403, "Access denied", "You don't have permission to view this page.")

    @app.errorhandler(404)
    def not_found(_e):
        return _error(404, "Page not found", "The page you were looking for doesn't exist.")

    @app.errorhandler(413)
    def too_large(_e):
        return _error(413, "Request too large", "That request was too large.")

    @app.errorhandler(AnnotationFileError)
    def annotation_file_error(e):
        log.error("Annotation file error: %s", e)
        return _error(500, "Results file problem",
                      "Your results file could not be read. Your data has not been reset. "
                      "Please contact the research team.")

    @app.errorhandler(500)
    def server_error(_e):
        return _error(500, "Something went wrong", "An unexpected error occurred. Please try again.")

    return app


app = create_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=True, port=5000)
