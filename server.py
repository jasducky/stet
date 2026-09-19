#!/usr/bin/env python3
"""artefact-review - edit and comment on any Claude HTML artefact, locally.

    python3 server.py <file.html> [--port 8790] [--author NAME] [--detach]
    python3 server.py --approve <cid>            apply a proposal from the CLI
    python3 server.py --status  <cid> <status> ["note"]

Two things make this different from a comment layer:

  1. BOTH parties edit. Julia edits blocks in the browser; the agent edits
     through the same API. Every change is logged before/after with an author.
  2. The agent cannot change her words silently. It writes a PROPOSAL onto a
     comment; she approves it in the browser; only then is it applied.

The target file is NEVER modified except by an approved edit. The review layer
is injected at serve time, so nothing is written into the artefact and there is
no removal step - close the server and the file is exactly as Claude wrote it.
"""

import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import html_doc  # noqa: E402

HERE = Path(__file__).resolve().parent
INITIAL_PPID = os.getppid()
_last_hit = time.time()

# R2.5. Minted once per server process, injected into the served page, and never
# written to disk - nothing under .review/ may contain it, because that directory
# is what an agent reads.
SESSION_TOKEN = secrets.token_urlsafe(32)

# Two endpoint classes, written down so a later reader can tell which are guarded
# on purpose and which exemption is deliberate.
#
# Browser-only: everything that writes to the document or the sidecar on a human's
# behalf. Guarded by an Origin / Sec-Fetch-Site check AND the session token, both
# checked before the request body is read at all.
BROWSER_ONLY = frozenset({
    "/__edit", "/__comment", "/__reply", "/__approve",
    "/__reject", "/__resolve", "/__delete", "/__replace", "/__bin",
})

# Agent-reachable: /__propose only. The agent posts from a shell with no browser,
# no Origin and no token, and the agent credential is deliberately deferred. A
# proposal writes nothing to the document - it needs a human approval through a
# browser-only endpoint to reach the file - so the exemption costs nothing.
# The `author` field stays a log label, never a credential.
AGENT_REACHABLE = frozenset({"/__propose"})


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Everything the agent reads lives in .review/ beside the artefact."""

    def __init__(self, target: Path):
        self.target = target
        self.dir = target.parent / ".review" / target.stem
        self.dir.mkdir(parents=True, exist_ok=True)
        self.comments = self.dir / "comments.json"
        self.edits = self.dir / "edits.md"
        self.inbox = self.dir / "inbox.jsonl"

    # --- document ---
    def text(self):
        return self.target.read_text()

    def units(self):
        return html_doc.parse(self.text())

    def apply_edit(self, unit_id, new_inner, author):
        text = self.text()
        units = self.units()
        unit = next((u for u in units if u.id == unit_id), None)
        if unit is None:
            raise KeyError(unit_id)
        before = unit.raw(text)
        if before == new_inner:
            return False

        # R2.6. Here rather than in do_POST because `server.py --approve` calls
        # this straight from main() and never touches an HTTP handler, so
        # validating at the HTTP layer would leave the CLI verb writing
        # unvalidated content into the file. Every write path goes through here.
        html_doc.validate_edit(new_inner, before)

        self.target.write_text(html_doc.write(text, units, unit_id, new_inner))
        self.log_edit(unit_id, unit.tag, before, new_inner, author)
        return True

    def log_edit(self, unit_id, tag, before, after, author):
        new = not self.edits.exists()
        with self.edits.open("a") as f:
            if new:
                f.write(f"# Edits - {self.target.name}\n\n"
                        "Newest last. The artefact itself is the source of truth; "
                        "this is the audit trail of who changed what.\n\n")
            f.write(f"## {now()} - {author} - {unit_id} <{tag}>\n\n"
                    f"**before**\n```\n{before}\n```\n\n"
                    f"**after**\n```\n{after}\n```\n\n")

    # --- comments ---
    def load(self):
        return json.loads(self.comments.read_text()) if self.comments.exists() else []

    def save(self, comments):
        self.comments.write_text(json.dumps(comments, indent=1))

    def append_inbox(self, event):
        """Append-only event stream. The agent watches this file with Monitor,
        so one line per event and never a rewrite."""
        with self.inbox.open("a") as f:
            f.write(json.dumps(event) + "\n")

    def find(self, cid):
        comments = self.load()
        for c in comments:
            if c["id"] == cid:
                return comments, c
        return comments, None


def inject(text, units, locked_ids, cfg, nonce):
    """Add the review layer at serve time. The file on disk is untouched."""
    meta = [u.to_json(text) for u in units]
    # stamp data-rv-id on each unit, working backwards so offsets stay valid
    out = text
    for u in sorted(units, key=lambda x: x.span[0], reverse=True):
        s = u.span[0]
        tag_end = out.find(">", s)
        if tag_end == -1:
            continue
        attrs = f' data-rv-id="{u.id}"'
        if not u.editable:
            attrs += ' data-rv-locked="1"'
        insert_at = tag_end - 1 if out[tag_end - 1] == "/" else tag_end
        out = out[:insert_at] + attrs + out[insert_at:]

    payload = json.dumps({"units": meta, "locked": sorted(locked_ids), **cfg})
    # R1.5: only the review layer's own tags carry the nonce. Document script,
    # inline or sibling, carries none and does not run. The tag holding the
    # session token is nonced for exactly this reason - a same-origin sibling
    # script would otherwise read the token out of it and post a valid write.
    layer = (
        '<link rel="stylesheet" href="/__lib/review.css">'
        f'<script nonce="{nonce}">window.__RV__={payload};</script>'
        f'<script nonce="{nonce}" src="/__lib/review.js" defer></script>'
    )
    # Inject before the LAST closing body tag, not the first.
    #
    # A first-occurrence string replace puts the layer inside the document
    # whenever "</body>" appears earlier as text - in a comment, a string
    # literal, or a code sample. The layer's script tags then become script
    # text, window.__RV__ is never defined, and review.js degrades silently to
    # an empty config: a page that renders dead with zero regions and no error.
    # A tool for reviewing HTML documents is exactly the tool most likely to be
    # pointed at a document that contains HTML as text, so this is a realistic
    # case rather than an exotic one.
    # tests/fixtures/script-attack.html carries such a comment and is the
    # regression test for it.
    last = None
    for m in re.finditer(r"</\s*body\s*>", out, re.I):
        last = m
    if last:
        return out[:last.start()] + layer + out[last.start():]
    return out + layer


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    author = "user"

    def log_message(self, *a):
        pass

    def end_headers(self):
        global _last_hit
        _last_hit = time.time()
        super().end_headers()

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        raw = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload), "application/json")

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        st = self.store

        if path == "/info":
            return self._json({"tool": "artefact-review",
                               "target": str(st.target), "pid": os.getpid()})

        if path.startswith("/__lib/"):
            f = HERE / "lib" / Path(path).name
            if not f.exists():
                return self._send(404, "not found")
            ctype = "text/css" if f.suffix == ".css" else "application/javascript"
            return self._send(200, f.read_bytes(), f"{ctype}; charset=utf-8")

        if path == "/__version":
            def mt(p):
                try:
                    return p.stat().st_mtime
                except OSError:
                    return 0
            return self._json({"doc": mt(st.target), "comments": mt(st.comments)})

        if path == "/__comments":
            return self._json(st.load())

        if path == "/__units":
            text = st.text()
            return self._json([u.to_json(text) for u in st.units()])

        if path in ("/", "/index.html"):
            text = st.text()
            units = st.units()
            locked = {u.id for u in units if not u.editable}

            # A fresh nonce per response. Not 'self': the artefact's own sibling
            # script files are same-origin, so 'self' would keep running them.
            nonce = secrets.token_urlsafe(16)
            csp = (f"script-src 'nonce-{nonce}'; "
                   "object-src 'none'; "
                   "base-uri 'none'")
            page = inject(text, units, locked, {
                "author": self.author, "name": st.target.name,
                "token": SESSION_TOKEN, "scriptsDisabled": True}, nonce)
            return self._send(200, page, extra={"Content-Security-Policy": csp})

        return self._send(404, "not found")

    # ---------- request guard (R2.5) ----------
    def _same_origin(self):
        """True when the request demonstrably came from the served page.

        Sec-Fetch-Site is the modern signal and browsers send it unforgeably.
        Origin is checked as well for anything that does not, and a cross-site
        value is rejected outright rather than falling through to the token.
        """
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            return False

        origin = self.headers.get("Origin")
        if origin is None:
            # No Origin at all is only acceptable when the browser told us the
            # request is same-origin. A bare POST from another process has
            # neither, and is refused.
            return site == "same-origin"

        host = self.headers.get("Host", "")
        allowed = {f"http://{host}"}
        if ":" in host:
            _, _, port = host.rpartition(":")
            allowed |= {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        return origin in allowed

    def _guard(self, path):
        """Refuse a browser-only endpoint unless the request came from the served
        page AND carries this process's token. Returns True when refused.

        Runs before the body is read, so a rejected request is never parsed.
        """
        if path not in BROWSER_ONLY:
            return False

        token = self.headers.get("X-RV-Token", "")
        if not self._same_origin() or not secrets.compare_digest(token, SESSION_TOKEN):
            self._json({"ok": False, "error": "refused: not from the served page"}, 403)
            return True
        return False

    # ---------- POST ----------
    def do_POST(self):
        st = self.store
        p = self.path

        # Guarded before any parsing. Nothing below this line runs for a refused
        # request - not the body read, not the JSON decode, not a store load.
        if self._guard(p):
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Previously this raised, the handler died, and the client got no
            # HTTP response at all rather than a 400. Recorded during U4 and
            # fixed here, because a malformed payload is a payload question.
            return self._json({"ok": False, "error": f"malformed JSON: {e}"}, 400)
        if not isinstance(data, dict):
            return self._json({"ok": False, "error": "body must be a JSON object"}, 400)

        who = data.get("author") or self.author

        if p == "/__edit":
            try:
                changed = st.apply_edit(data["id"], data.get("text", ""), who)
            except (KeyError, ValueError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            if changed and who != "Claude":
                st.append_inbox({"type": "edit", "at": iso(),
                                 "unit": data["id"], "author": who})
            return self._json({"ok": True, "changed": changed})

        comments = st.load()

        if p == "/__comment":
            cid = f"c{len(comments) + 1:02d}"
            c = {"id": cid, "time": now(), "author": who, "status": "open",
                 "unit": data.get("unit"), "quote": data.get("quote", ""),
                 "comment": data["comment"], "replies": []}
            comments.append(c)
            st.save(comments)
            st.append_inbox({"type": "comment", "at": iso(), "id": cid,
                             "unit": c["unit"], "quote": c["quote"][:200],
                             "comment": c["comment"]})
            return self._json({"ok": True, "id": cid})

        comments, c = st.find(data.get("id", ""))
        if c is None:
            return self._json({"ok": False, "error": "no such comment"}, 404)

        if p == "/__propose":
            unit_id = data.get("unit") or c.get("unit")

            # R3.2. Anchor to the WORDS this was written against, not to a
            # region index, and capture them through the same normalisation
            # approval will use. An explicit anchor from the caller wins, so an
            # agent can propose against a phrase rather than a whole region.
            anchor = data.get("anchor")
            if not anchor:
                doc = st.text()
                u = next((x for x in st.units() if x.id == unit_id), None)
                anchor = html_doc.anchor_text(u.raw(doc)) if u is not None else ""

            c["proposal"] = {"unit": unit_id,
                             "text": data.get("text", ""),
                             "note": data.get("note", ""),
                             "anchor": anchor}
            c["status"] = "proposed"

        elif p in ("/__approve", "/__replace"):
            pr = c.get("proposal")
            if not pr:
                return self._json({"ok": False, "error": "nothing proposed"}, 400)

            # R3.5. Re-placing updates the anchor and then re-attempts the apply
            # down the SAME path as an ordinary approval, so the two can never
            # disagree about where a proposal lands.
            if p == "/__replace":
                new_anchor = html_doc.anchor_text(data.get("anchor", ""))
                if not new_anchor:
                    return self._json({"ok": False, "status": "refused",
                                       "error": "select some text to re-place this onto"}, 400)
                pr["anchor"] = new_anchor

            # R3.3. Resolve the anchor to a REGION. Three outcomes, and two of
            # them write nothing and discard nothing (R3.4).
            doc = st.text()
            units_now = st.units()
            anchor = pr.get("anchor")

            if not anchor:
                # A proposal from before anchors existed. Say so rather than
                # quietly trusting a region id that may have moved.
                c["status"] = "orphaned"
                st.save(comments)
                return self._json({"ok": False, "status": "orphaned",
                                   "error": "this proposal carries no anchor, so the "
                                            "words it was written against cannot be found"},
                                  409)

            hits = html_doc.find_anchor(doc, units_now, anchor)

            if len(hits) == 0:
                c["status"] = "orphaned"
                st.save(comments)
                st.append_inbox({"type": "orphaned", "at": iso(), "id": c["id"],
                                 "author": who, "anchor": anchor[:200]})
                return self._json({"ok": False, "status": "orphaned",
                                   "anchor": anchor,
                                   "error": "the text this was written against is no "
                                            "longer in the document"}, 409)

            if len(hits) > 1:
                c["status"] = "ambiguous"
                st.save(comments)
                st.append_inbox({"type": "ambiguous", "at": iso(), "id": c["id"],
                                 "author": who, "anchor": anchor[:200],
                                 "matches": [u.id for u in hits]})
                return self._json({"ok": False, "status": "ambiguous",
                                   "anchor": anchor,
                                   "matches": [u.id for u in hits],
                                   "error": f"this text appears in {len(hits)} regions, "
                                            "so it is not clear which one to change"}, 409)

            # Exactly one. Apply through the same write path as a human edit, so
            # the locked check and payload validation both still fire.
            landed = hits[0]
            try:
                st.apply_edit(landed.id, pr["text"], "Claude (approved)")
            except (KeyError, ValueError) as e:
                return self._json({"ok": False, "status": "refused",
                                   "error": str(e)}, 400)
            c["status"] = "applied"
            c["resolved"] = pr.get("note", "")
            st.append_inbox({"type": "approved", "at": iso(), "id": c["id"],
                             "author": who, "unit": landed.id})

        elif p == "/__reject":
            c["status"] = "open"
            c["replies"].append({"time": now(), "author": who,
                                 "text": data.get("reason", "(no reason given)")})
            c.pop("proposal", None)
            st.append_inbox({"type": "rejected", "at": iso(), "id": c["id"],
                             "reason": data.get("reason", "")})

        elif p == "/__reply":
            c["replies"].append({"time": now(), "author": who, "text": data["text"]})
            if who != "Claude":
                st.append_inbox({"type": "reply", "at": iso(), "id": c["id"],
                                 "text": data["text"]})

        elif p == "/__bin":
            # R3.5: binning is a single action. The thread stays and returns to
            # open; only the proposal goes, and the event records that it did.
            had = c.pop("proposal", None)
            c["status"] = "open"
            st.append_inbox({"type": "binned", "at": iso(), "id": c["id"],
                             "author": who,
                             "anchor": (had or {}).get("anchor", "")[:200]})

        elif p == "/__resolve":
            c["status"] = "applied"
            c["resolved"] = data.get("note", "")

        elif p == "/__delete":
            c["deleted"] = True

        elif p in AGENT_REACHABLE:
            pass  # /__propose is handled above; listed for the reader

        else:
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)

        st.save(comments)
        return self._json({"ok": True})


def watchdog(idle_timeout, watch_parent=True):
    """Never leak a server: die when the launching process dies, or when no
    client has called for idle_timeout seconds. Borrowed from
    paraschopra/make-pages-interactive, which gets this exactly right.

    Parent watching is skipped when the server was started detached (nohup,
    disown, a launch agent), because there the parent is meant to go away.
    """
    watch_parent = watch_parent and INITIAL_PPID != 1
    while True:
        time.sleep(5)
        if watch_parent and os.getppid() == 1:
            print("[review] parent exited, shutting down", flush=True)
            os._exit(0)
        if idle_timeout > 0 and time.time() - _last_hit > idle_timeout:
            print(f"[review] idle >{idle_timeout}s, shutting down", flush=True)
            os._exit(0)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    # CLI verbs operate on a target given by --file, or the only .review/ found
    port, author, idle = 8790, os.environ.get("USER", "user"), 900
    detach = False
    target = None
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--port":
            port = int(args[i + 1]); i += 2
        elif a == "--author":
            author = args[i + 1]; i += 2
        elif a == "--idle-timeout":
            idle = int(args[i + 1]); i += 2
        elif a == "--detach":
            detach = True; i += 1
        elif a.startswith("--"):
            rest.append(a); i += 1
        elif target is None and a.endswith((".html", ".htm")):
            target = Path(a).resolve(); i += 1
        else:
            rest.append(a); i += 1

    if target is None or not target.exists():
        print(f"error: give a path to an .html file (got {target})")
        sys.exit(1)

    store = Store(target)

    if "--approve" in rest:
        cid = rest[rest.index("--approve") + 1]
        comments, c = store.find(cid)
        pr = (c or {}).get("proposal")
        if not pr:
            print(f"{cid}: nothing proposed")
            sys.exit(1)
        # R3.7: the CLI verb resolves the anchor exactly as the browser does,
        # or the two doors disagree about where a proposal lands.
        doc_now = store.text()
        hits = html_doc.find_anchor(doc_now, store.units(), pr.get("anchor") or "")
        if not pr.get("anchor"):
            print(f"{cid}: this proposal carries no anchor, so the words it was "
                  f"written against cannot be found")
            sys.exit(1)
        if len(hits) == 0:
            print(f"{cid}: orphaned - the text this was written against is no longer "
                  f"in the document")
            print(f"  anchor: {pr['anchor'][:120]}")
            sys.exit(1)
        if len(hits) > 1:
            print(f"{cid}: ambiguous - this text appears in {len(hits)} regions "
                  f"({', '.join(u.id for u in hits)})")
            sys.exit(1)
        try:
            store.apply_edit(hits[0].id, pr["text"], "Claude (approved)")
        except (KeyError, ValueError) as e:
            print(f"{cid}: {e}")
            sys.exit(1)
        c["status"] = "applied"
        store.save(comments)
        print(f"{cid} applied to {pr['unit']}")
        return

    units = store.units()
    locked = [u for u in units if not u.editable]
    Handler.store = store
    Handler.author = author

    print(f"artefact-review -> http://localhost:{port}")
    print(f"  target   : {target}")
    print(f"  units    : {len(units)} editable regions"
          + (f", {len(locked)} comment-only (script-generated)" if locked else ""))
    print(f"  review   : {store.dir}")
    shutdown = []
    if not detach:
        shutdown.append("parent-death")
    if idle > 0:
        shutdown.append(f"{idle}s idle")
    print(f"  shutdown : {' or '.join(shutdown) if shutdown else 'manual only'}")

    threading.Thread(target=watchdog, args=(idle, not detach), daemon=True).start()
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
