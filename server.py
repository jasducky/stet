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


def inject(text, units, locked_ids, cfg):
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
    layer = (
        '<link rel="stylesheet" href="/__lib/review.css">'
        f'<script>window.__RV__={payload};</script>'
        '<script src="/__lib/review.js" defer></script>'
    )
    if "</body>" in out:
        return out.replace("</body>", layer + "</body>", 1)
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

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
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
            return self._send(200, inject(text, units, locked, {
                "author": self.author, "name": st.target.name}))

        return self._send(404, "not found")

    # ---------- POST ----------
    def do_POST(self):
        st = self.store
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length)) if length else {}
        p = self.path
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
            c["proposal"] = {"unit": data.get("unit") or c.get("unit"),
                             "text": data.get("text", ""),
                             "note": data.get("note", "")}
            c["status"] = "proposed"

        elif p == "/__approve":
            pr = c.get("proposal")
            if not pr:
                return self._json({"ok": False, "error": "nothing proposed"}, 400)
            try:
                st.apply_edit(pr["unit"], pr["text"], "Claude (approved)")
            except (KeyError, ValueError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            c["status"] = "applied"
            c["resolved"] = pr.get("note", "")
            st.append_inbox({"type": "approved", "at": iso(), "id": c["id"],
                             "unit": pr["unit"]})

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

        elif p == "/__resolve":
            c["status"] = "applied"
            c["resolved"] = data.get("note", "")

        elif p == "/__delete":
            c["deleted"] = True

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
        store.apply_edit(pr["unit"], pr["text"], "Claude (approved)")
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
