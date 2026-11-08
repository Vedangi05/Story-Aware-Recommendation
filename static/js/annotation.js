/* Annotation page: render pairs, save labels asynchronously, keyboard shortcuts. */
(function () {
  "use strict";

  var boot = JSON.parse(document.getElementById("initial-data").textContent);
  var urls = boot.urls;
  var total = boot.progress.total;

  var state = {
    current: null,        // payload for the pair on screen
    saving: false,
    loading: false,
    // NOTES (optional) - commented out:
    // notesSaved: "",       // last persisted note for the current pair
    cache: {}             // pair id -> payload
  };

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    compare: $("compare"),
    counter: $("pair-counter"),
    completed: $("completed-count"),
    bar: $("progress-bar"),
    fill: $("progress-fill"),
    // Link-text box was removed from the page (label buttons sit between the articles now).
    // linkText: $("link-text"),
    prev: $("prev-btn"),
    next: $("next-btn"),
    status: $("save-status"),
    // NOTES (optional) - commented out:
    // notes: $("notes-input"),
    // notesDetails: $("notes-details"),
    // notesStatus: $("notes-status"),
    buttons: Array.prototype.slice.call(document.querySelectorAll(".label-btn"))
  };
  var FIELDS = ["a_headline", "a_subhead", "a_paragraph", "b_headline", "b_subhead", "b_paragraph"];
  var EMPTY = {
    headline: "No headline provided",
    subhead: "No subheading provided",
    paragraph: "No first paragraph provided"
  };

  // ---------- rendering ----------
  function setText(el, text, emptyMsg, highlight) {
    el.textContent = "";
    if (!text) {
      var span = document.createElement("span");
      span.className = "empty-value";
      span.textContent = emptyMsg;
      el.appendChild(span);
      return;
    }
    if (!highlight) { el.textContent = text; return; }
    // Highlight every case-insensitive occurrence of the link text (DOM-safe, no innerHTML).
    var lower = text.toLowerCase(), needle = highlight.toLowerCase(), pos = 0, idx;
    while ((idx = lower.indexOf(needle, pos)) !== -1) {
      if (idx > pos) el.appendChild(document.createTextNode(text.slice(pos, idx)));
      var mark = document.createElement("mark");
      mark.className = "link-hl";
      mark.textContent = text.slice(idx, idx + needle.length);
      el.appendChild(mark);
      pos = idx + needle.length;
    }
    if (pos < text.length) el.appendChild(document.createTextNode(text.slice(pos)));
  }

  function render(payload) {
    var p = payload.pair;
    var link = (p.link_text || "").trim();
    FIELDS.forEach(function (f) {
      var kind = f.split("_")[1];
      setText($(f.replace("_", "-")), p[f], EMPTY[kind], f.charAt(0) === "a" && link.length > 1 ? link : null);
    });
    // Link-text box removed from the page. The link text is still highlighted inside Article A.
    // if (link) {
    //   els.linkText.textContent = link;
    //   els.linkText.classList.remove("empty-value");
    // } else {
    //   els.linkText.textContent = "No link text provided";
    //   els.linkText.classList.add("empty-value");
    // }
    els.counter.textContent = "Pair " + payload.position + " of " + payload.total;
    els.buttons.forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.label === payload.label ? "true" : "false");
    });
    els.prev.disabled = !payload.prev_id;
    els.next.disabled = !payload.next_id;
    // NOTES (optional) - commented out:
    // els.notes.value = payload.notes || "";
    // state.notesSaved = payload.notes || "";
    // if (payload.notes) els.notesDetails.open = true;
    setStatus(payload.label ? "Saved as “" + payload.label + "”" : "", payload.label ? "ok" : "");
    document.title = "Pair " + payload.position + " · POPROX News Annotation";
    state.current = payload;
  }

  function setProgress(completed) {
    els.completed.textContent = completed;
    els.bar.setAttribute("aria-valuenow", completed);
    els.fill.style.width = (total ? (100 * completed / total) : 0).toFixed(1) + "%";
  }

  function setStatus(text, kind) {
    els.status.textContent = text;
    els.status.className = "save-status" + (kind ? " " + kind : "");
  }

  function setBusy(busy) {
    els.compare.setAttribute("aria-busy", busy ? "true" : "false");
  }

  // ---------- networking ----------
  function api(url, options) {
    options = options || {};
    options.credentials = "same-origin";
    options.headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    if (options.body) {
      options.headers["Content-Type"] = "application/json";
      options.headers["X-CSRF-Token"] = POPROX.csrf();
    }
    return fetch(url, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (resp.status === 401) { POPROX.navigate("/"); }
        if (!resp.ok || !data.ok) {
          throw new Error(data.error || ("Request failed (" + resp.status + ")"));
        }
        return data;
      });
    });
  }

  function fetchPair(id) {
    if (state.cache[id]) return Promise.resolve(state.cache[id]);
    return api(urls.pair.replace("__ID__", encodeURIComponent(id))).then(function (data) {
      state.cache[id] = data;
      return data;
    });
  }

  function prefetch(id) { if (id && !state.cache[id]) fetchPair(id).catch(function () {}); }

  function go(id, push) {
    if (!id || state.loading) return Promise.resolve();
    state.loading = true;
    setBusy(true);
    // NOTES (optional) - was: flushNotes().then(function () { return fetchPair(id); })
    return fetchPair(id).then(function (payload) {
      render(payload);
      if (push !== false) {
        history.pushState({ pair: id }, "", urls.annotate_page + "?pair=" + encodeURIComponent(id));
      }
      window.scrollTo({ top: 0, behavior: "smooth" });
      prefetch(payload.next_id);
    }).catch(function (err) {
      POPROX.toast(err.message || "Could not load that pair.", "error");
    }).then(function () {
      state.loading = false;
      setBusy(false);
    });
  }

  function lockButtons(locked, active) {
    els.buttons.forEach(function (b) {
      b.disabled = locked;
      b.classList.toggle("saving", locked && b === active);
    });
    els.prev.disabled = locked || !(state.current && state.current.prev_id);
    els.next.disabled = locked || !(state.current && state.current.next_id);
  }

  function annotate(label) {
    if (state.saving || state.loading || !state.current) return;
    var pairId = state.current.pair.id;
    var btn = els.buttons.filter(function (b) { return b.dataset.label === label; })[0];
    state.saving = true;
    lockButtons(true, btn);
    setStatus("Saving…", "");
    // NOTES (optional) - commented out. Notes are no longer sent, so the server
    // leaves any note already saved for this pair unchanged.
    // var notes = els.notes.value;
    api(urls.annotate, {
      method: "POST",
      body: JSON.stringify({ pair_id: pairId, label: label /* , notes: notes */ })
    }).then(function (res) {
      // Update local view of this pair.
      // state.notesSaved = notes;   // NOTES (optional)
      var cached = state.cache[pairId];
      if (cached) { cached.label = label; /* cached.notes = notes; */ }
      els.buttons.forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.label === label ? "true" : "false"); });
      setProgress(res.completed);
      setStatus("Saved ✓", "ok");
      state.saving = false;
      lockButtons(false);
      if (res.redirect) { POPROX.navigate(res.redirect); return; }
      if (res.next_id && res.next_id !== pairId) {
        setTimeout(function () { go(res.next_id); }, 250);
      }
    }).catch(function (err) {
      state.saving = false;
      lockButtons(false);
      setStatus("Not saved", "err");
      POPROX.toast((err && err.message) || "Could not save. Please try again.", "error");
    });
  }

  // NOTES (optional) - commented out: saves the note when the user leaves the notes box.
  // function flushNotes() {
  //   if (!state.current) return Promise.resolve();
  //   var value = els.notes.value;
  //   if (value === state.notesSaved) return Promise.resolve();
  //   var pairId = state.current.pair.id;
  //   els.notesStatus.textContent = "Saving note…";
  //   return api(urls.notes, { method: "POST", body: JSON.stringify({ pair_id: pairId, notes: value }) })
  //     .then(function () {
  //       state.notesSaved = value;
  //       if (state.cache[pairId]) state.cache[pairId].notes = value;
  //       els.notesStatus.textContent = "Note saved.";
  //     }).catch(function (err) {
  //       els.notesStatus.textContent = "Note not saved: " + err.message;
  //     });
  // }

  // ---------- events ----------
  els.buttons.forEach(function (b) {
    b.addEventListener("click", function () { annotate(b.dataset.label); });
  });
  els.prev.addEventListener("click", function () { if (state.current) go(state.current.prev_id); });
  els.next.addEventListener("click", function () { if (state.current) go(state.current.next_id); });
  // NOTES (optional) - commented out:
  // els.notes.addEventListener("blur", function () { flushNotes(); });

  function modalOpen() {
    var m = document.getElementById("code-modal");
    return m && !m.hidden;
  }

  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target;
    var tag = t && t.tagName ? t.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select" || (t && t.isContentEditable)) return;
    if (modalOpen()) return;
    var labelIndex = { "1": 0, "2": 1, "3": 2 }[e.key];
    if (labelIndex !== undefined && els.buttons[labelIndex]) {
      e.preventDefault();
      annotate(els.buttons[labelIndex].dataset.label);
    } else if (e.key === "ArrowLeft" && state.current && state.current.prev_id && !state.saving) {
      e.preventDefault();
      go(state.current.prev_id);
    } else if (e.key === "ArrowRight" && state.current && state.current.next_id && !state.saving) {
      e.preventDefault();
      go(state.current.next_id);
    }
  });

  window.addEventListener("popstate", function (e) {
    var id = (e.state && e.state.pair) || new URLSearchParams(location.search).get("pair");
    if (id) go(id, false);
  });

  window.addEventListener("beforeunload", function (e) {
    // NOTES (optional) - was: state.saving || (state.current && els.notes.value !== state.notesSaved)
    if (state.saving) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // Resume-code modal
  var modal = document.getElementById("code-modal");
  if (modal) {
    var close = document.getElementById("close-modal");
    var copy = document.getElementById("copy-code");
    var hide = function () { modal.hidden = true; modal.style.display = "none"; };
    close.addEventListener("click", hide);
    modal.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
    copy.addEventListener("click", function () {
      var code = document.getElementById("resume-code-value").textContent.trim();
      if (navigator.clipboard) {
        navigator.clipboard.writeText(code).then(function () { POPROX.toast("Resume code copied"); });
      }
    });
    setTimeout(function () { close.focus(); }, 50);
  }

  // ---------- boot ----------
  state.cache[boot.initial.pair.id] = boot.initial;
  render(boot.initial);
  setProgress(boot.progress.completed);
  history.replaceState({ pair: boot.initial.pair.id }, "",
    urls.annotate_page + "?pair=" + encodeURIComponent(boot.initial.pair.id));
  prefetch(boot.initial.next_id);
})();
