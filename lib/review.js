/* stet client layer.
 *
 * Injected at serve time, so the artefact on disk never carries these tags.
 *
 * Three affordances:
 *   hover a region  -> Edit / Comment
 *   select text     -> Comment on the selection
 *   sidebar         -> threads, and Approve / Needs changes on agent proposals
 *
 * Regions the page builds with its own script cannot be persisted, so they are
 * marked comment-only rather than silently refusing an edit.
 */
(function () {
  "use strict";

  var CFG = window.__RV__ || { units: [], locked: [] };
  var UNITS = {};
  CFG.units.forEach(function (u) { UNITS[u.id] = u; });

  var editing = null;
  // R3.5. Re-placing needs its own mode because the gesture is already taken:
  // the global mouseup handler opens a "Comment on selection" popup for any
  // selection. While this is set, that handler hands the selection to the
  // re-place flow instead. Cleared on confirm, cancel or Escape.
  var replacing = null;
  var lastVersion = null;

  // ---------- helpers ----------
  // Simple line icons, drawn in the text colour so they match wherever they sit.
  // The comment, settings and thumbs icons are from Lucide (lucide.dev):
  // ISC License, Copyright (c) for portions of Lucide are held by Cole Bemis
  // 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are
  // held by Lucide Contributors 2022. Permission to use, copy, modify, and/or
  // distribute this software for any purpose with or without fee is hereby
  // granted, provided that the above copyright notice and this permission
  // notice appear in all copies.
  function icon(paths) {
    return '<svg class="rv-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" ' +
           'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + paths + "</svg>";
  }
  var ICON_UP = icon('<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>');
  var ICON_DOWN = icon('<path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/>');
  var ICON_COMMENT = icon('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>');
  var ICON_SETTINGS = icon('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>');
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function post(path, body) {
    // R2.5: every write from this page carries the session token minted at
    // startup. A page in another tab has no way to read it.
    var headers = { "Content-Type": "application/json" };
    if (CFG.token) headers["X-RV-Token"] = CFG.token;
    return fetch(path, {
      method: "POST",
      headers: headers,
      credentials: "same-origin",
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function status(msg, ms) {
    var s = document.getElementById("rv-status");
    if (!s) return;
    s.textContent = msg;
    if (ms !== 0) setTimeout(function () { if (s.textContent === msg) s.textContent = ""; }, ms || 2600);
  }

  /* contentEditable produces browser-specific soup. Keep a small tag set,
     drop every attribute except href/class, and never let styling in. */
  var KEEP = { A: 1, B: 1, STRONG: 1, I: 1, EM: 1, U: 1, CODE: 1, BR: 1, SPAN: 1, SUP: 1, SUB: 1, SMALL: 1, MARK: 1 };
  function sanitise(node) {
    var out = "";
    node.childNodes.forEach(function (n) {
      if (n.nodeType === 3) { out += esc(n.nodeValue); return; }
      if (n.nodeType !== 1) return;
      if (!KEEP[n.tagName]) { out += sanitise(n); return; }
      if (n.tagName === "BR") { out += "<br>"; return; }
      var attrs = "";
      if (n.getAttribute("href")) attrs += ' href="' + esc(n.getAttribute("href")) + '"';
      if (n.getAttribute("class")) attrs += ' class="' + esc(n.getAttribute("class")) + '"';
      out += "<" + n.tagName.toLowerCase() + attrs + ">" + sanitise(n) + "</" + n.tagName.toLowerCase() + ">";
    });
    return out;
  }

  // ---------- chrome ----------
  function buildChrome() {
    var bar = el("div", "rv-bar");
    // R1.5: say that the document's own scripts are off. Without this a
    // dashboard silently stops filtering and the reviewer cannot tell whether
    // that is the tool or the artefact.
    var scriptNote = CFG.scriptsDisabled
      ? '<span class="rv-noscript" title="This document contains scripts: small programs ' +
        'that build or animate parts of the page. They are paused while you work, so the ' +
        'page cannot change itself underneath your edits. Anything they would normally ' +
        'draw or animate will not appear here.">' +
        "Page animations paused</span>"
      : "";

    bar.innerHTML =
      '<b>stet</b><span class="rv-doc">' + esc(CFG.name || "") + "</span>" +
      '<span class="rv-how">Double-click to edit &middot; Highlight to comment</span>' +
      scriptNote +
      '<span id="rv-status"></span>' +
      '<div class="rv-view" id="rv-view" role="group" aria-label="View">' +
      '<button data-v="normal">Normal</button><button data-v="auth">Who changed what</button></div>' +
      '<button id="rv-settings-btn" class="rv-gear" title="Settings" aria-haspopup="true">' + ICON_SETTINGS + 'Settings</button>' +
      '<div class="rv-settings" id="rv-settings" hidden>' +
      '<div class="rv-set-title">Settings</div>' +
      '<div class="rv-set-h">Text size</div>' +
      sizeRow("side", "Comments") + sizeRow("doc", "Document") +
      '<button class="rv-set-wide" data-z="reset">Reset text size</button>' +
      '<div class="rv-set-h">Comments sidebar</div>' +
      '<label class="rv-set-check"><input type="checkbox" id="rv-set-side"> Show the comments sidebar</label>' +
      '<button class="rv-set-wide" data-z="width">Reset sidebar width</button>' +
      '<p class="rv-set-note">Drag the sidebar\'s left edge to resize it. Settings are kept in this browser only.</p>' +
      '<p class="rv-set-version">stet ' + esc(CFG.version || "") + "</p>" +
      "</div>";
    document.body.appendChild(bar);
    wireSettings(bar);
    wireView(bar);

    var side = el("aside", "rv-side");
    side.innerHTML = '<div class="rv-side-grip" title="Drag to resize"></div>' +
                     '<div class="rv-side-h">Comments <span>newest first</span>' +
                     '<button id="rv-page-comment" class="rv-page-comment" title="A comment about the document as a whole">+ On the whole document</button>' +
                     '<button class="rv-side-close" id="rv-side-close" title="Close comments" aria-label="Close comments">&times;</button>' +
                     '</div>' +
                     '<div class="rv-filter" id="rv-filter">' +
                     '<button data-f="needs">Needs you <i></i></button>' +
                     '<button data-f="waiting">Waiting on agent <i></i></button>' +
                     '<button data-f="done">Done <i></i></button>' +
                     '<button data-f="all">All <i></i></button>' +
                     '</div><div id="rv-threads"></div>';
    document.body.appendChild(side);

    // Closed, the sidebar leaves a tab on the right edge to bring it back.
    var tab = el("button", "rv-side-tab", ICON_COMMENT + 'Comments <span id="rv-tab-n"></span>');
    tab.id = "rv-side-tab";
    tab.title = "Open comments";
    document.body.appendChild(tab);
    wireResize(side.querySelector(".rv-side-grip"));
    try { if (localStorage.getItem("rv-side-off")) document.body.classList.add("rv-side-off"); } catch (e) { }
    document.body.classList.add("rv-on");

    // The filter row sticks directly under the header, whatever height it has.
    document.getElementById("rv-filter").style.top = side.querySelector(".rv-side-h").offsetHeight + "px";
    document.getElementById("rv-filter").onclick = function (e) {
      var b = e.target.closest ? e.target.closest("[data-f]") : null;
      if (!b) return;
      FILTER = b.getAttribute("data-f");
      try { localStorage.setItem("rv-filter", FILTER); } catch (err) { }
      refresh();
    };
    document.getElementById("rv-side-close").onclick = function () { setSide(false); };
    tab.onclick = function () { setSide(true); };
    document.getElementById("rv-page-comment").onclick = function () {
      openComment(null, "", "the whole document");
    };

    var tools = el("div", "rv-tools");
    tools.innerHTML = '<span id="rv-attribution" hidden></span>' +
                      '<span class="rv-lock" title="This region is written by the page\'s own script, so an edit here could not be saved">script-built, comment only</span>';
    document.body.appendChild(tools);

    tools.onmousedown = function (e) { e.preventDefault(); };
    tools.onclick = function (e) {
      var a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (!a || !tools._id) return;
      if (a === "edit") startEdit(tools._id);
      else openComment(tools._id, "", describe(tools._id));
    };
    return tools;
  }

  // The sidebar width is the reader's own choice, so it is remembered in this
  // browser only. Storage can be missing (private window), so every touch is
  // guarded and the default width still applies.
  var SIDE_KEY = "rv-side-w";
  function setSideWidth(px) {
    var w = Math.max(280, Math.min(px, window.innerWidth - 320));
    document.documentElement.style.setProperty("--rv-side-w", w + "px");
    return w;
  }
  function wireResize(grip) {
    try { var saved = parseInt(localStorage.getItem(SIDE_KEY), 10); if (saved) setSideWidth(saved); } catch (e) { }
    grip.addEventListener("mousedown", function (e) {
      e.preventDefault();
      document.body.classList.add("rv-resizing");
      function move(ev) { setSideWidth(window.innerWidth - ev.clientX); placeMarks(); }
      function up(ev) {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        document.body.classList.remove("rv-resizing");
        var w = setSideWidth(window.innerWidth - ev.clientX);
        try { localStorage.setItem(SIDE_KEY, String(w)); } catch (err) { }
        placeMarks();
      }
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
  }

  function setSide(open) {
    document.body.classList.toggle("rv-side-off", !open);
    try { localStorage.setItem("rv-side-off", open ? "" : "1"); } catch (e) { }
    if (typeof placeMarks === "function") placeMarks();
  }

  // ---------- who-changed-what view ----------
  // Each paragraph is one of three kinds, by its LAST change in the edit record:
  // untouched (original), changed by the reader directly, or proposed by an
  // agent and approved by the reader. Paragraph level only; the words inside a
  // paragraph are not tracked separately.
  // Labels in the left margin, one per changed paragraph, named from the
  // record itself (the --author name and the agent's name), never hard-coded.
  var authored = [];
  var authTags = [];
  function buildAuthTags() {
    var layer = document.getElementById("rv-auth-tags");
    if (!layer) { layer = el("div"); layer.id = "rv-auth-tags"; document.body.appendChild(layer); }
    layer.innerHTML = "";
    authTags = authored.map(function (a) {
      var ai = a.record.approved_by != null || a.record.author !== CFG.author;
      var text = a.record.approved_by != null
        ? esc(a.record.author) + "<span>approved by " + esc(a.record.approved_by) + "</span>"
        : esc(a.record.author);
      var t = el("div", "rv-auth-tag " + (ai ? "rv-auth-ai" : "rv-auth-human"), text);
      layer.appendChild(t);
      return { tag: t, node: a.node };
    });
    placeAuthTags();
  }
  function placeAuthTags() {
    authTags.forEach(function (a) {
      var r = a.node.getBoundingClientRect();
      var w = a.tag.offsetWidth || 90;
      a.tag.style.top = (window.scrollY + r.top + 2) + "px";
      a.tag.style.left = (window.scrollX + Math.max(4, r.left - w - 16)) + "px";
    });
  }
  window.addEventListener("resize", placeAuthTags);
  window.addEventListener("load", placeAuthTags);

  function wireView(bar) {
    var view = "normal";
    try { if (localStorage.getItem("rv-view") === "auth") view = "auth"; } catch (e) { }
    function set(v) {
      view = v;
      document.body.classList.toggle("rv-view-auth", v === "auth");
      if (v === "auth") placeAuthTags();
      bar.querySelectorAll("#rv-view [data-v]").forEach(function (b) {
        b.classList.toggle("rv-view-on", b.getAttribute("data-v") === v);
      });
      try { localStorage.setItem("rv-view", v); } catch (e) { }
    }
    bar.querySelector("#rv-view").onclick = function (e) {
      var v = e.target.getAttribute && e.target.getAttribute("data-v");
      if (v) set(v);
    };
    set(view);
  }

  // ---------- text size ----------
  // Two zoom levels, the reader's own preference, kept in this browser only.
  // Zoom is a style on the page as served, never written to the file: the
  // comments scale #rv-threads, the document scales the body's own children.
  var ZOOM = { side: 1, doc: 1 };
  function sizeRow(key, label) {
    return '<div class="rv-set-row"><span>' + label + "</span>" +
      '<button data-z="' + key + ':-1" aria-label="Smaller">A&minus;</button>' +
      '<b id="rv-z-' + key + '">100%</b>' +
      '<button data-z="' + key + ':1" aria-label="Larger">A+</button></div>';
  }
  function applyZoom(key, value) {
    ZOOM[key] = Math.max(0.7, Math.min(2, Math.round(value * 10) / 10));
    document.documentElement.style.setProperty("--rv-" + key + "-zoom", String(ZOOM[key]));
    var label = document.getElementById("rv-z-" + key);
    if (label) label.textContent = Math.round(ZOOM[key] * 100) + "%";
    try { localStorage.setItem("rv-" + key + "-zoom", String(ZOOM[key])); } catch (e) { }
    if (typeof placeMarks === "function") placeMarks();
    if (typeof placeAuthTags === "function") placeAuthTags();
  }
  function wireSettings(bar) {
    ["side", "doc"].forEach(function (k) {
      var v = 1;
      try { v = parseFloat(localStorage.getItem("rv-" + k + "-zoom")) || 1; } catch (e) { }
      applyZoom(k, v);
    });
    var panel = bar.querySelector("#rv-settings");
    var sideBox = panel.querySelector("#rv-set-side");
    bar.querySelector("#rv-settings-btn").onclick = function (e) {
      e.stopPropagation();
      sideBox.checked = !document.body.classList.contains("rv-side-off");
      panel.hidden = !panel.hidden;
    };
    sideBox.onchange = function () { setSide(sideBox.checked); };
    panel.onclick = function (e) {
      e.stopPropagation();
      var z = e.target.getAttribute && e.target.getAttribute("data-z");
      if (!z) return;
      if (z === "reset") { applyZoom("side", 1); applyZoom("doc", 1); return; }
      if (z === "width") {
        document.documentElement.style.removeProperty("--rv-side-w");
        try { localStorage.removeItem("rv-side-w"); } catch (err) { }
        placeMarks();
        return;
      }
      var parts = z.split(":");
      applyZoom(parts[0], ZOOM[parts[0]] + 0.1 * parseInt(parts[1], 10));
    };
    document.addEventListener("click", function () { panel.hidden = true; });
  }

  // A comment is shown as "#3", not its stored id "c03".
  function cnum(cid) { var n = parseInt(String(cid).replace(/\D/g, ""), 10); return isNaN(n) ? esc(cid) : "#" + n; }

  // Plain words for a block, never its tag and internal id.
  var KIND = { P: "paragraph", LI: "list item", TD: "table cell", TH: "table cell",
               BLOCKQUOTE: "quote", FIGCAPTION: "caption", CAPTION: "caption" };
  function describe(id) {
    var u = UNITS[id];
    if (!u) return "the whole document";
    var tag = String(u.tag || "").toUpperCase();
    return "this " + (/^H[1-6]$/.test(tag) ? "heading" : KIND[tag] || "part");
  }


  // ---------- hover toolbar ----------
  var tools;
  function wireHover() {
    document.addEventListener("mouseover", function (e) {
      if (editing) return;
      var t = e.target.closest ? e.target.closest("[data-rv-id]") : null;
      if (!t) return;
      if (t.closest(".rv-bar,.rv-side,.rv-tools")) return;
      showTools(t);
    });
  }

  function showTools(node) {
    var id = node.getAttribute("data-rv-id");
    var locked = node.hasAttribute("data-rv-locked");
    var r = node.getBoundingClientRect();
    tools._id = id;
    var attribution = document.getElementById("rv-attribution");
    attribution.textContent = node.getAttribute("data-rv-attribution") || "";
    attribution.title = attribution.textContent;   // full label when truncated
    attribution.hidden = !attribution.textContent;
    tools.classList.toggle("rv-is-locked", locked);
    tools.style.top = (window.scrollY + r.top - 30) + "px";
    tools.style.left = (window.scrollX + r.left) + "px";
    tools.classList.toggle("rv-show", locked || !attribution.hidden);
    document.querySelectorAll(".rv-hot").forEach(function (n) { n.classList.remove("rv-hot"); });
    node.classList.add("rv-hot");
  }

  // ---------- editing ----------
  // Editing has no Save button. Leaving the block saves it; Esc puts it back.
  // Underneath, stet still writes one block at a time (only the bytes of the
  // block that changed), which is what keeps the rest of the file untouched.
  //
  // A save gives the block a new id, so the page must reload before that block
  // can be edited again. The reload waits until the reader is not mid-edit in
  // another block; going straight back into a just-saved block reloads first and
  // reopens it with the cursor where they clicked.
  var pendingReload = false;

  function textOffset(node, container, offset) {
    var r = document.createRange();
    r.setStart(node, 0);
    r.setEnd(container, offset);
    return r.toString().length;
  }
  function caretAt(node, n) {
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT), t;
    while ((t = walker.nextNode())) {
      if (n <= t.nodeValue.length) {
        var r = document.createRange();
        r.setStart(t, n); r.collapse(true);
        var sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(r);
        return;
      }
      n -= t.nodeValue.length;
    }
  }
  function blockIndex(node) {
    return Array.prototype.indexOf.call(document.querySelectorAll("[data-rv-id]"), node);
  }
  // Never reload over something the reader is typing or has not got saved.
  function busy() {
    return !!(editing || document.querySelector(".rv-unsaved, [contenteditable='true'], .rv-raw"));
  }
  function reloadSoon() {
    pendingReload = true;
    setTimeout(function () { if (pendingReload && !busy()) reloadDoc(); }, 450);
  }

  // raw: Shift + double-click edits the block's HTML in a text box, for links
  // and markup that cannot be typed into the page itself.
  function startEdit(id, caret, raw) {
    var node = document.querySelector('[data-rv-id="' + id + '"]');
    if (!node || node.hasAttribute("data-rv-locked")) return;
    if (node.hasAttribute("data-rv-saved")) {
      // This block was just saved and its id is stale until the page reloads.
      sessionStorage.setItem("rv-reopen", JSON.stringify({ i: blockIndex(node), at: caret || 0, raw: !!raw }));
      reloadDoc();
      return;
    }
    editing = id;
    tools.classList.remove("rv-show");
    node.classList.add("rv-editing");
    node._before = node.innerHTML;

    var ta = null;
    if (raw) {
      ta = el("textarea", "rv-raw");
      ta.value = node._before;
      node.innerHTML = "";
      node.appendChild(ta);
      ta.focus();
    } else {
      node.setAttribute("contenteditable", "true");
      node.focus();
    }
    var target = ta || node;

    var done = false, inflight = false;
    function finish() {
      done = true;
      node.removeAttribute("contenteditable");
      node.classList.remove("rv-editing", "rv-unsaved");
      target.removeEventListener("keydown", onKey);
      target.removeEventListener("blur", onBlur);
      if (editing === id) editing = null;
      if (pendingReload) reloadSoon();    // a change that arrived while typing
    }
    function cancel() {
      if (done) return;
      node.innerHTML = node._before;
      finish();
      status("Edit cancelled");
    }
    function save() {
      if (done || inflight) return;
      var payload = ta ? ta.value : sanitise(node);
      var unchanged = ta ? ta.value === node._before : node.innerHTML === node._before;
      if (unchanged) {
        if (ta) node.innerHTML = node._before;
        finish();
        return;
      }
      inflight = true;
      if (editing === id) editing = null;   // the reader may already be moving on
      post("/__edit", { id: id, text: payload }).then(function (d) {
        inflight = false;
        if (!d.ok) {
          // R2.7: a refused write never loses what was typed. The block stays
          // open with the reader's text in it, marked, until they fix or cancel.
          node.classList.add("rv-unsaved");
          status("Not saved: " + d.error, 8000);
          return;
        }
        finish();
        if (d.changed) {
          node.setAttribute("data-rv-saved", "1");
          status("Saved");
          reloadSoon();
        }
      });
    }
    function onKey(e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
      if (e.key === "Escape") { e.preventDefault(); cancel(); }
    }
    function onBlur() { save(); }
    target.addEventListener("keydown", onKey);
    target.addEventListener("blur", onBlur);
  }

  function reopenAfterReload() {
    var raw = sessionStorage.getItem("rv-reopen");
    if (!raw) return;
    sessionStorage.removeItem("rv-reopen");
    try {
      var r = JSON.parse(raw);
      var node = document.querySelectorAll("[data-rv-id]")[r.i];
      if (!node) return;
      startEdit(node.getAttribute("data-rv-id"), 0, r.raw);
      if (!r.raw) caretAt(node, r.at);
    } catch (e) { }
  }

  // ---------- comments ----------
  function openComment(unitId, quote, where) {
    var back = el("div", "rv-modal");
    back.innerHTML =
      '<div class="rv-card"><h4>' + (quote ? "Comment on the highlighted text" : "Comment on " + esc(where)) + "</h4>" +
      (quote ? '<blockquote>' + esc(quote.slice(0, 300)) + "</blockquote>" : "") +
      '<textarea rows="5" placeholder="What should change, and why?"></textarea>' +
      // Cancel on the left as plain text, Save on the right as the one main
      // button: apart, so neither is pressed for the other.
      '<div class="rv-card-b rv-dialog-b"><button data-a="cancel" class="rv-link">Cancel</button>' +
      '<button data-a="save" class="rv-primary">Submit</button></div></div>';
    document.body.appendChild(back);
    var ta = back.querySelector("textarea");
    ta.focus();
    back.onclick = function (e) {
      var a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (e.target === back || a === "cancel") { back.remove(); return; }
      if (a !== "save") return;
      if (!ta.value.trim()) { back.remove(); return; }
      post("/__comment", { unit: unitId, quote: quote, comment: ta.value.trim() })
        .then(function () { back.remove(); status("Comment saved"); refresh(); });
    };
    ta.onkeydown = function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) back.querySelector('[data-a="save"]').click();
      if (e.key === "Escape") back.remove();
    };
  }

  function wireDoubleClick() {
    document.addEventListener("dblclick", function (e) {
      if (editing || replacing) return;
      if (e.target.closest && e.target.closest(".rv-bar,.rv-side,.rv-tools,.rv-modal,#rv-marks,.rv-side-tab")) return;
      var unit = e.target.closest ? e.target.closest("[data-rv-id]") : null;
      if (!unit) return;
      var pop = document.querySelector(".rv-selpop");
      if (pop) pop.remove();
      if (unit.hasAttribute("data-rv-locked")) {
        status("This part is built by the page, so it can only be commented on", 5000);
        return;
      }
      // Put the cursor where the reader double-clicked, not at the start of
      // the block: they are going in to change one particular spot.
      var at = null;
      if (document.caretRangeFromPoint) at = document.caretRangeFromPoint(e.clientX, e.clientY);
      else if (document.caretPositionFromPoint) {
        var cp = document.caretPositionFromPoint(e.clientX, e.clientY);
        if (cp) { at = document.createRange(); at.setStart(cp.offsetNode, cp.offset); }
      }
      var offset = at && unit.contains(at.startContainer) ? textOffset(unit, at.startContainer, at.startOffset) : 0;
      startEdit(unit.getAttribute("data-rv-id"), offset, e.shiftKey);
      if (!editing || e.shiftKey) return;   // reloading, or the HTML box has focus
      var sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        if (at && unit.contains(at.startContainer)) { at.collapse(true); sel.addRange(at); }
      }
    });
  }

  function wireSelection() {
    document.addEventListener("mouseup", function (e) {
      if (editing) return;
      // A double-click selects a word on its way to opening the editor; that
      // selection is not a request to comment.
      if (e.detail >= 2) return;
      // The popups are excluded too. Without that, a mouseup ON a popup
      // re-enters this handler, which removes the popup being clicked - so the
      // element is detached before its click event fires and the button
      // silently does nothing. That applies to the comment popup as much as to
      // the re-place one.
      if (e.target.closest &&
          e.target.closest(".rv-bar,.rv-side,.rv-tools,.rv-modal,.rv-selpop,.rv-replacepop,.rv-replacebar")) return;
      var sel = window.getSelection();
      var txt = sel ? String(sel).trim() : "";
      if (txt.length < 3) return;

      // Re-place mode owns the selection while it is active, so the comment
      // popup never steals the gesture.
      if (replacing) { offerReplace(sel, txt, e); return; }
      var node = sel.anchorNode;
      node = node && (node.nodeType === 1 ? node : node.parentElement);
      var unit = node && node.closest ? node.closest("[data-rv-id]") : null;
      var pop = el("button", "rv-selpop", ICON_COMMENT + "Comment");
      pop.style.top = (window.scrollY + e.clientY + 10) + "px";
      pop.style.left = (window.scrollX + e.clientX - 20) + "px";
      document.body.appendChild(pop);
      var kill = setTimeout(function () { pop.remove(); }, 4000);
      pop.onclick = function () {
        clearTimeout(kill); pop.remove();
        openComment(unit ? unit.getAttribute("data-rv-id") : null, txt,
                    unit ? describe(unit.getAttribute("data-rv-id")) : "the page");
      };
    });
  }

  // ---------- re-place (R3.5) ----------

  function regionOf(node) {
    var n = node && (node.nodeType === 1 ? node : node.parentElement);
    return n && n.closest ? n.closest("[data-rv-id]") : null;
  }

  function startReplace(cid, anchorText) {
    cancelReplace();                       // never two at once
    replacing = { id: cid, anchor: anchorText || "" };
    document.body.classList.add("rv-replacing");
    var bar = el("div", "rv-replacebar");
    bar.id = "rv-replacebar";
    bar.innerHTML =
      '<b>Re-placing comment ' + cnum(cid) + "</b>" +
      '<span>Select the new text in the document. Esc to cancel.</span>' +
      (anchorText
        ? '<span class="rv-replace-was">was: ' + esc(anchorText.slice(0, 90)) + "</span>"
        : "") +
      '<button data-a="cancel-replace">Cancel</button>';
    document.body.appendChild(bar);
    bar.onclick = function (e) {
      if (e.target.getAttribute && e.target.getAttribute("data-a") === "cancel-replace") {
        cancelReplace();
        status("Re-place cancelled");
      }
    };
    status("Re-placing comment " + cnum(cid) + ": select the new text", 8000);
  }

  function cancelReplace() {
    replacing = null;
    document.body.classList.remove("rv-replacing");
    var bar = document.getElementById("rv-replacebar");
    if (bar) bar.remove();
    var pop = document.querySelector(".rv-replacepop");
    if (pop) pop.remove();
  }

  function offerReplace(sel, txt, e) {
    var old = document.querySelector(".rv-replacepop");
    if (old) old.remove();

    // A selection crossing a region boundary is rejected AT THE CONFIRM STEP,
    // with a reason, and the mode stays active so the human can try again.
    var a = regionOf(sel.anchorNode);
    var b = regionOf(sel.focusNode);
    var crossed = !a || !b || a !== b;

    var pop = el("button", "rv-replacepop",
                 crossed ? "That spans more than one block - select inside one"
                         : "Use this text");
    if (crossed) pop.classList.add("rv-replacepop-bad");
    document.body.appendChild(pop);

    // Positioned in VIEWPORT coordinates and clamped inside it. A selection
    // spanning two blocks produces a bounding box that can put a document-
    // positioned confirm anywhere, including under the fixed chrome or past the
    // bottom of a long page - a confirm you cannot reach is a confirm you
    // cannot press. 58px clears the review bar and the re-placing bar.
    var w = pop.offsetWidth || 160, h = pop.offsetHeight || 26;
    var x = Math.min(Math.max(8, e.clientX - 20), window.innerWidth - w - 8);
    var y = Math.min(Math.max(58, e.clientY + 10), window.innerHeight - h - 8);
    pop.style.left = x + "px";
    pop.style.top = y + "px";

    pop.onclick = function () {
      pop.remove();
      if (crossed) {
        status("A re-place has to sit inside one block. Mode still on - select again.", 6000);
        return;                            // mode deliberately stays active
      }
      var cid = replacing.id;
      post("/__replace", { id: cid, anchor: txt }).then(function (d) {
        if (d.ok) {
          cancelReplace();
          status("Re-placed and applied");
          refresh();
          reloadDoc();
          return;
        }
        // Refused, ambiguous or still orphaned: say why, stay in mode.
        status("Not re-placed: " + (d.error || d.status), 7000);
        refresh();
      });
    };
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && replacing) {
      cancelReplace();
      status("Re-place cancelled");
    }
  });

  // ---------- sidebar ----------
  // ---------- comment marks on the page ----------
  // A comment the reader has just written has to be visible where it was made.
  // Nothing here touches the region's children: the block gets a class (the
  // edit sanitiser reads children only), the badge lives in its own layer on
  // <body>, and the quoted words use the CSS Custom Highlight API, which paints
  // a range without adding a single element. So no mark can leak into a save.
  var DONE = { applied: 1, resolved: 1, binned: 1 };
  // Which comments the sidebar lists. "Needs you" is everything waiting on the
  // human: a proposal to approve, or one that lost its place and must be
  // re-placed. "Waiting on agent" is an open comment with no proposal yet.
  var NEEDS = { proposed: 1, orphaned: 1, ambiguous: 1 };
  var FILTERS = {
    needs: function (c) { return NEEDS[c.status]; },
    waiting: function (c) { return c.status === "open"; },
    done: function (c) { return DONE[c.status]; },
    all: function () { return true; }
  };
  var FILTER = "all";
  try { if (FILTERS[localStorage.getItem("rv-filter")]) FILTER = localStorage.getItem("rv-filter"); } catch (e) { }
  var marks = [];

  function textRange(node, quote) {
    if (!quote) return null;
    var walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    // A selection reports rendered spacing; the source keeps its line breaks
    // and indentation. Match on collapsed whitespace, keeping a map from each
    // collapsed character back to its text node and offset.
    var flat = "", map = [], t, prevSpace = true;
    while ((t = walker.nextNode())) {
      var v = t.nodeValue;
      for (var j = 0; j < v.length; j++) {
        var sp = /\s/.test(v[j]);
        if (sp && prevSpace) continue;
        flat += sp ? " " : v[j];
        map.push({ n: t, o: j });
        prevSpace = sp;
      }
    }
    var q = quote.replace(/\s+/g, " ").trim();
    var i = q ? flat.indexOf(q) : -1;
    if (i < 0) return null;
    var a = map[i], b = map[i + q.length - 1];
    var r = document.createRange();
    r.setStart(a.n, a.o); r.setEnd(b.n, b.o + 1);
    return r;
  }

  function showThread(cid) {
    if (document.body.classList.contains("rv-side-off")) setSide(true);
    var t = document.getElementById("rv-t-" + cid);
    if (!t) return;
    t.scrollIntoView({ behavior: "smooth", block: "nearest" });
    t.classList.add("rv-thread-flash");
    setTimeout(function () { t.classList.remove("rv-thread-flash"); }, 1400);
  }

  function markComments(live) {
    document.querySelectorAll(".rv-has-comment").forEach(function (n) { n.classList.remove("rv-has-comment"); });
    var layer = document.getElementById("rv-marks");
    if (!layer) { layer = el("div"); layer.id = "rv-marks"; document.body.appendChild(layer); }
    layer.innerHTML = "";
    marks = [];
    var ranges = [];
    var byUnit = {};
    live.forEach(function (c) {
      if (!c.unit || DONE[c.status]) return;
      var node = document.querySelector('[data-rv-id="' + c.unit + '"]');
      if (!node) return;
      node.classList.add("rv-has-comment");
      var r = textRange(node, c.quote);
      if (r) ranges.push(r);
      (byUnit[c.unit] = byUnit[c.unit] || { node: node, ids: [] }).ids.push(c.id);
    });
    Object.keys(byUnit).forEach(function (u) {
      var m = byUnit[u];
      var b = el("button", "rv-mark", ICON_COMMENT + (m.ids.length > 1 ? m.ids.length : cnum(m.ids[0])));
      b.title = "Comments " + m.ids.map(cnum).join(", ");
      b.onclick = function () { showThread(m.ids[m.ids.length - 1]); };
      layer.appendChild(b);
      marks.push({ btn: b, node: m.node });
    });
    if (window.CSS && CSS.highlights && window.Highlight) {
      CSS.highlights.delete("rv-quote");
      if (ranges.length) CSS.highlights.set("rv-quote", new Highlight(...ranges));
    }
    placeMarks();
  }

  // Badges sit in the margin just right of their block, clear of the sidebar.
  function placeMarks() {
    var side = document.querySelector(".rv-side");
    var limit = side && !document.body.classList.contains("rv-side-off")
      ? side.getBoundingClientRect().left : window.innerWidth;
    marks.forEach(function (m) {
      var r = m.node.getBoundingClientRect();
      var w = m.btn.offsetWidth || 50;
      var x = Math.min(r.right + 10, limit - w - 8);
      m.btn.style.top = (window.scrollY + r.top) + "px";
      m.btn.style.left = (window.scrollX + x) + "px";
    });
  }
  window.addEventListener("resize", placeMarks);
  window.addEventListener("load", placeMarks);

  function thread(c) {
    var box = el("div", "rv-thread rv-" + c.status);
    box.id = "rv-t-" + c.id;
    var head = '<div class="rv-th-h"><b>' + cnum(c.id) + "</b> " + esc(c.author) +
               ' <span class="rv-pill">' + esc(c.status) + "</span></div>";
    var quote = c.quote ? '<blockquote>' + esc(c.quote.slice(0, 200)) + "</blockquote>" : "";
    var body = '<p>' + esc(c.comment) + "</p>";
    var reps = (c.replies || []).map(function (r) {
      return '<div class="rv-reply"><b>' + esc(r.author) + ":</b> " + esc(r.text) + "</div>";
    }).join("");
    var prop = "";
    if (c.proposal) {
      var detached = c.status === "orphaned" || c.status === "ambiguous";
      var was = c.proposal.anchor || "";

      // R3.4: a detached proposal is shown with the words it was written
      // against, so it can be recognised and re-placed rather than guessed at.
      var detail = "";
      if (detached) {
        detail =
          '<div class="rv-detached">' +
          '<div class="rv-detached-h">' +
          (c.status === "orphaned"
            ? "Orphaned - these words are no longer in the document"
            : "Ambiguous - these words appear in more than one block") +
          "</div>" +
          (was ? '<blockquote class="rv-was">' + esc(was.slice(0, 300)) + "</blockquote>" : "") +
          "</div>";
      }

      // An applied proposal is a record, not a question: no buttons, or it
      // reads as if the approval did nothing.
      var actions = c.status === "applied"
        ? '<div class="rv-done-note">Applied</div>'
        : detached
        ? '<div class="rv-card-b"><button data-a="replace">Re-place it</button>' +
          '<button data-a="bin">Bin it</button></div>'
        : '<div class="rv-card-b"><button data-a="approve">Approve and apply</button>' +
          '<button data-a="reject">Needs changes</button></div>';

      prop = '<div class="rv-prop"><div class="rv-prop-h">Proposed change</div>' +
             (c.proposal.note ? "<p>" + esc(c.proposal.note) + "</p>" : "") +
             '<pre>' + esc(c.proposal.text.slice(0, 900)) + "</pre>" +
             detail + actions + "</div>";
    }
    // Did the agent do what the comment asked? The human's own verdict, optional,
    // offered once there is an answer to judge. Separate from approving, because
    // an approved proposal can still have missed part of the ask.
    var rate = "";
    if (c.proposal || (c.proposal_history || []).length) {
      var rv = (c.rating || {}).value;
      rate = '<div class="rv-rate"><span>Did it do what you asked?</span>' +
        '<button data-a="rate-up" class="' + (rv === "up" ? "rv-rate-on" : "") + '" title="Yes" aria-label="Yes, it did what I asked">' + ICON_UP + "</button>" +
        '<button data-a="rate-down" class="' + (rv === "down" ? "rv-rate-on rv-rate-down" : "") + '" title="Not quite" aria-label="No, not quite">' + ICON_DOWN + "</button>" +
        (rv ? '<input class="rv-rate-note" placeholder="A few words on why (optional), Enter to save" value="' + esc((c.rating || {}).note || "") + '">' : "") +
        "</div>";
    }
    box.innerHTML = head + quote + body + reps + prop + rate +
      '<div class="rv-th-b"><button data-a="reply">Reply</button>' +
      (c.status === "open" ? '<button data-a="resolve">Mark done</button>' : "") + "</div>";

    if (c.unit && !DONE[c.status]) {
      box.onmouseenter = function () {
        var n = document.querySelector('[data-rv-id="' + c.unit + '"]');
        if (n) n.classList.add("rv-focus");
      };
      box.onmouseleave = function () {
        document.querySelectorAll(".rv-focus").forEach(function (n) { n.classList.remove("rv-focus"); });
      };
    }

    var note = box.querySelector(".rv-rate-note");
    if (note) {
      note.onclick = function (e) { e.stopPropagation(); };
      note.onkeydown = function (e) {
        if (e.key !== "Enter") return;
        post("/__rate", { id: c.id, value: c.rating.value, note: note.value.trim() }).then(function (d) {
          status(d.ok ? "Note saved" : "Not saved: " + d.error, 4000); refresh();
        });
      };
    }

    box.onclick = function (e) {
      var btn = e.target.closest ? e.target.closest("[data-a]") : null;
      var a = btn && btn.getAttribute("data-a");
      if (!a) {
        if (c.unit) {
          var n = document.querySelector('[data-rv-id="' + c.unit + '"]');
          if (n) { n.scrollIntoView({ behavior: "smooth", block: "center" }); n.classList.add("rv-flash"); setTimeout(function () { n.classList.remove("rv-flash"); }, 1400); }
        }
        return;
      }
      if (a === "rate-up" || a === "rate-down") {
        post("/__rate", { id: c.id, value: a === "rate-up" ? "up" : "down" }).then(function (d) {
          if (!d.ok) { status("Not saved: " + d.error, 5000); return; }
          status("Noted"); refresh();
        });
        return;
      }
      if (a === "approve") {
        post("/__approve", { id: c.id }).then(function (d) {
          status(d.ok ? "Applied" : "Could not apply: " + d.error, d.ok ? 2600 : 6000);
          refresh(); if (d.ok) reloadDoc();
        });
      } else if (a === "reject") {
        var why = prompt("What is wrong with it?");
        if (why == null) return;
        post("/__reject", { id: c.id, reason: why }).then(function () { status("Sent back"); refresh(); });
      } else if (a === "reply") {
        var t = prompt("Reply");
        if (!t) return;
        post("/__reply", { id: c.id, text: t }).then(refresh);
      } else if (a === "resolve") {
        post("/__resolve", { id: c.id, note: "" }).then(refresh);
      } else if (a === "replace") {
        startReplace(c.id, (c.proposal || {}).anchor || "");
      } else if (a === "bin") {
        post("/__bin", { id: c.id }).then(function () {
          status("Proposal binned");
          refresh();
        });
      }
    };
    return box;
  }

  function refresh() {
    return fetch("/__comments").then(function (r) { return r.json(); }).then(function (cs) {
      var host = document.getElementById("rv-threads");
      host.innerHTML = "";
      var live = cs.filter(function (c) { return !c.deleted; });
      document.querySelectorAll("#rv-filter [data-f]").forEach(function (b) {
        var f = b.getAttribute("data-f");
        b.querySelector("i").textContent = live.filter(FILTERS[f]).length;
        b.classList.toggle("rv-on-f", f === FILTER);
      });
      var shown = live.filter(FILTERS[FILTER]);
      if (!live.length) host.innerHTML = '<p class="rv-empty">No comments yet. Highlight some text to comment on it.</p>';
      else if (!shown.length) host.innerHTML = '<p class="rv-empty">' +
        ({ needs: "Nothing needs you right now.", waiting: "No comments are waiting on the agent.",
           done: "Nothing is done yet." }[FILTER] || "") + "</p>";
      // Newest first: the comment just written is the one being looked for.
      shown.slice().reverse().forEach(function (c) { host.appendChild(thread(c)); });
      markComments(live);
      var waiting = live.filter(function (c) { return !DONE[c.status]; }).length;
      var tabN = document.getElementById("rv-tab-n");
      if (tabN) tabN.textContent = waiting ? "(" + waiting + ")" : "";
      var n = live.filter(function (c) { return c.status === "proposed"; }).length;
      document.title = (n ? "(" + n + ") " : "") + (CFG.name || "review");
    });
  }

  function reloadDoc() {
    var y = window.scrollY;
    sessionStorage.setItem("rv-scroll", String(y));
    location.reload();
  }

  function poll() {
    setInterval(function () {
      fetch("/__version").then(function (r) { return r.json(); }).then(function (v) {
        var sig = v.doc + ":" + v.comments;
        if (lastVersion === null) { lastVersion = sig; return; }
        if (sig === lastVersion) return;
        var docChanged = String(lastVersion).split(":")[0] !== String(v.doc);
        lastVersion = sig;
        if (docChanged && !busy()) reloadDoc();
        else if (docChanged) pendingReload = true;
        else refresh();
      }).catch(function () { });
    }, 3000);
  }

  // ---------- boot ----------
  function boot() {
    tools = buildChrome();
    // The server compares raw source HTML. DOM innerHTML normalises entities
    // and attributes, so comparing it here would lose valid matches.
    document.querySelectorAll("[data-rv-id]").forEach(function (node) {
      var record = (CFG.attribution || {})[node.getAttribute("data-rv-id")];
      node.removeAttribute("data-rv-attribution");
      node.classList.remove("rv-by-ai", "rv-by-human");
      if (!record || node.hasAttribute("data-rv-locked")) return;
      node.classList.add(record.approved_by != null ? "rv-by-ai" :
                         record.author === CFG.author ? "rv-by-human" : "rv-by-ai");
      authored.push({ node: node, record: record });
      var label = "Last changed by " + record.author;
      if (record.approved_by != null) label += ", approved by " + record.approved_by;
      node.setAttribute("data-rv-attribution", label);
    });
    buildAuthTags();
    wireHover();
    wireSelection();
    wireDoubleClick();
    refresh();
    poll();
    var y = sessionStorage.getItem("rv-scroll");
    if (y) { window.scrollTo(0, parseInt(y, 10)); sessionStorage.removeItem("rv-scroll"); }
    reopenAfterReload();
    var locked = (CFG.locked || []).length;
    // CFG.units is EVERY region, so labelling that count "editable" while also
    // reporting the locked ones contradicted itself: "6 editable regions, 2
    // comment-only" out of six regions in total. Report the editable count,
    // which is what the word means.
    status("Ready: " + (CFG.units.length - locked) + " parts you can edit" +
           (locked ? ", " + locked + " you can only comment on" : ""), 5000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
