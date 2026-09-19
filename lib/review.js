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
      ? '<span class="rv-noscript" title="This document is served with a policy that ' +
        'blocks its own scripts, inline and external, so it cannot rewrite itself while ' +
        'you review it. Anything the page would normally build or animate will not run.">' +
        "page scripts off</span>"
      : "";

    bar.innerHTML =
      '<b>REVIEW</b><span class="rv-doc">' + esc(CFG.name || "") + "</span>" +
      scriptNote +
      '<button id="rv-page-comment">Comment on the page</button>' +
      '<button id="rv-toggle">Hide sidebar</button>' +
      '<span id="rv-status"></span>';
    document.body.appendChild(bar);

    var side = el("aside", "rv-side");
    side.innerHTML = '<div class="rv-side-h">Comments</div><div id="rv-threads"></div>';
    document.body.appendChild(side);
    document.body.classList.add("rv-on");

    document.getElementById("rv-toggle").onclick = function () {
      document.body.classList.toggle("rv-side-off");
      this.textContent = document.body.classList.contains("rv-side-off") ? "Show sidebar" : "Hide sidebar";
    };
    document.getElementById("rv-page-comment").onclick = function () {
      openComment(null, "", "the whole page");
    };

    var tools = el("div", "rv-tools");
    tools.innerHTML = '<button data-a="edit">Edit</button><button data-a="comment">Comment</button>' +
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

  function describe(id) {
    var u = UNITS[id];
    return u ? "<" + u.tag + "> " + id : "the page";
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
    tools.classList.toggle("rv-is-locked", locked);
    tools.style.top = (window.scrollY + r.top - 30) + "px";
    tools.style.left = (window.scrollX + r.left) + "px";
    tools.classList.add("rv-show");
    document.querySelectorAll(".rv-hot").forEach(function (n) { n.classList.remove("rv-hot"); });
    node.classList.add("rv-hot");
  }

  // ---------- editing ----------
  function startEdit(id) {
    var node = document.querySelector('[data-rv-id="' + id + '"]');
    if (!node || node.hasAttribute("data-rv-locked")) return;
    editing = id;
    tools.classList.remove("rv-show");
    node.classList.add("rv-editing");
    node._before = node.innerHTML;
    node.setAttribute("contenteditable", "true");
    node.focus();

    var box = el("div", "rv-editbar");
    box.innerHTML = '<button data-a="save">Save</button><button data-a="cancel">Cancel</button>' +
                    '<button data-a="raw">Edit raw HTML</button>' +
                    '<span>Cmd/Ctrl+Enter saves, Esc cancels</span>';
    node.parentNode.insertBefore(box, node.nextSibling);

    function finish() {
      node.removeAttribute("contenteditable");
      node.classList.remove("rv-editing");
      box.remove();
      editing = null;
    }
    function save(html) {
      var payload = html != null ? html : sanitise(node);
      post("/__edit", { id: id, text: payload }).then(function (d) {
        if (!d.ok) { status("Not saved: " + d.error, 6000); return; }
        finish();
        status(d.changed ? "Saved" : "No change");
        if (d.changed) reloadDoc();
      });
    }
    box.onclick = function (e) {
      var a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (a === "save") save();
      else if (a === "cancel") { node.innerHTML = node._before; finish(); }
      else if (a === "raw") {
        var cur = node.innerHTML;
        node.removeAttribute("contenteditable");
        var ta = el("textarea", "rv-raw");
        ta.value = cur;
        node.innerHTML = "";
        node.appendChild(ta);
        ta.focus();
        box.querySelector('[data-a="raw"]').remove();
        save = function () {
          post("/__edit", { id: id, text: ta.value }).then(function (d) {
            if (!d.ok) { status("Not saved: " + d.error, 6000); return; }
            finish(); status("Saved"); reloadDoc();
          });
        };
      }
    };
    node.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
      if (e.key === "Escape") { e.preventDefault(); node.innerHTML = node._before; finish(); }
    });
  }

  // ---------- comments ----------
  function openComment(unitId, quote, where) {
    var back = el("div", "rv-modal");
    back.innerHTML =
      '<div class="rv-card"><h4>Comment on ' + esc(where) + "</h4>" +
      (quote ? '<blockquote>' + esc(quote.slice(0, 300)) + "</blockquote>" : "") +
      '<textarea rows="5" placeholder="What needs to change, and why"></textarea>' +
      '<div class="rv-card-b"><button data-a="save">Save comment</button>' +
      '<button data-a="cancel">Cancel</button></div></div>';
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

  function wireSelection() {
    document.addEventListener("mouseup", function (e) {
      if (editing) return;
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
      var pop = el("button", "rv-selpop", "Comment on selection");
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
      '<b>Re-placing ' + esc(cid) + "</b>" +
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
    status("Re-placing " + cid + " - select the new text", 8000);
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
    // cannot press. 52px clears the review bar and the re-placing bar.
    var w = pop.offsetWidth || 160, h = pop.offsetHeight || 26;
    var x = Math.min(Math.max(8, e.clientX - 20), window.innerWidth - w - 8);
    var y = Math.min(Math.max(52, e.clientY + 10), window.innerHeight - h - 8);
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
  function thread(c) {
    var box = el("div", "rv-thread rv-" + c.status);
    var head = '<div class="rv-th-h"><b>' + esc(c.id) + "</b> " + esc(c.author) +
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

      var actions = detached
        ? '<div class="rv-card-b"><button data-a="replace">Re-place it</button>' +
          '<button data-a="bin">Bin it</button></div>'
        : '<div class="rv-card-b"><button data-a="approve">Approve and apply</button>' +
          '<button data-a="reject">Needs changes</button></div>';

      prop = '<div class="rv-prop"><div class="rv-prop-h">Proposed change</div>' +
             (c.proposal.note ? "<p>" + esc(c.proposal.note) + "</p>" : "") +
             '<pre>' + esc(c.proposal.text.slice(0, 900)) + "</pre>" +
             detail + actions + "</div>";
    }
    box.innerHTML = head + quote + body + reps + prop +
      '<div class="rv-th-b"><button data-a="reply">Reply</button>' +
      (c.status === "open" ? '<button data-a="resolve">Mark done</button>' : "") + "</div>";

    box.onclick = function (e) {
      var a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (!a) {
        if (c.unit) {
          var n = document.querySelector('[data-rv-id="' + c.unit + '"]');
          if (n) { n.scrollIntoView({ behavior: "smooth", block: "center" }); n.classList.add("rv-flash"); setTimeout(function () { n.classList.remove("rv-flash"); }, 1400); }
        }
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
      if (!live.length) host.innerHTML = '<p class="rv-empty">No comments yet. Select some text, or hover a block.</p>';
      live.forEach(function (c) { host.appendChild(thread(c)); });
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
        if (docChanged && !editing) reloadDoc();
        else refresh();
      }).catch(function () { });
    }, 3000);
  }

  // ---------- boot ----------
  function boot() {
    tools = buildChrome();
    wireHover();
    wireSelection();
    refresh();
    poll();
    var y = sessionStorage.getItem("rv-scroll");
    if (y) { window.scrollTo(0, parseInt(y, 10)); sessionStorage.removeItem("rv-scroll"); }
    var locked = (CFG.locked || []).length;
    // CFG.units is EVERY region, so labelling that count "editable" while also
    // reporting the locked ones contradicted itself: "6 editable regions, 2
    // comment-only" out of six regions in total. Report the editable count,
    // which is what the word means.
    status((CFG.units.length - locked) + " editable regions" +
           (locked ? ", " + locked + " comment-only" : ""), 5000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
