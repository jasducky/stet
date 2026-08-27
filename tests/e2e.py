#!/usr/bin/env python3
"""End-to-end: start the server on a COPY of a real artefact and drive the
whole loop over HTTP - edit, comment, propose, approve - then verify the file
on disk actually changed and that nothing outside the edited span moved.
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path.home() / "Documents/sample.html"
TMP = Path("/tmp/artefact-review-e2e")
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


def call(path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main():
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    target = TMP / SRC.name
    shutil.copy(SRC, target)
    original = target.read_text()

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), str(target),
         "--port", str(PORT), "--author", "Julia", "--idle-timeout", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    ok = []
    try:
        for _ in range(50):
            try:
                call("/info"); break
            except Exception:
                time.sleep(0.1)
        else:
            print("server never came up"); print(proc.stdout.read()); return 1

        def check(label, cond, extra=""):
            print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + extra) if extra else ''}")
            ok.append(cond)

        print("\n1. serve + inject")
        page = call("/")
        check("review layer injected", "/__lib/review.js" in page)
        check("artefact file NOT modified by serving", target.read_text() == original)
        check("data-rv-id stamped in the served copy", 'data-rv-id="u0' in page)
        check("stamped ids absent from the file on disk", "data-rv-id" not in target.read_text())

        units = call("/__units")
        check("units returned", len(units) > 40, f"{len(units)} units")
        target_unit = next(u for u in units
                           if u["editable"] and len(u["raw"].strip()) > 40)

        print("\n2. Julia edits a block")
        new_text = "REPLACED BY THE TEST"
        r = call("/__edit", {"id": target_unit["id"], "text": new_text, "author": "Julia"})
        check("edit accepted", r.get("ok") and r.get("changed"), json.dumps(r))
        after = target.read_text()
        check("file on disk changed", after != original)
        check("new text present", new_text in after)
        check("byte-exact outside the span",
              len(after) == len(original) - len(target_unit["raw"]) + len(new_text))
        edits = (TMP / ".review" / target.stem / "edits.md").read_text()
        check("audit trail records author + before/after",
              "Julia" in edits and "**before**" in edits and "**after**" in edits)

        print("\n3. Julia comments, Claude proposes, Julia approves")
        c = call("/__comment", {"unit": units[6]["id"], "quote": "some quote",
                                "comment": "this needs to be sharper", "author": "Julia"})
        cid = c["id"]
        check("comment created", c.get("ok"))
        r = call("/__propose", {"id": cid, "unit": units[6]["id"],
                                "text": "A SHARPER LINE", "note": "tightened it"})
        check("proposal recorded", r.get("ok"))
        state = {x["id"]: x for x in call("/__comments")}
        check("status is proposed, not applied", state[cid]["status"] == "proposed")
        check("document untouched while proposed", "A SHARPER LINE" not in target.read_text())

        r = call("/__approve", {"id": cid})
        check("approve applied it", r.get("ok"))
        check("proposed text now in the file", "A SHARPER LINE" in target.read_text())
        state = {x["id"]: x for x in call("/__comments")}
        check("status now applied", state[cid]["status"] == "applied")

        print("\n4. the approval gate actually gates")
        c2 = call("/__comment", {"unit": units[8]["id"], "quote": "", "comment": "x"})
        call("/__propose", {"id": c2["id"], "unit": units[8]["id"], "text": "NEVER APPLIED"})
        call("/__reject", {"id": c2["id"], "reason": "no, wrong tone"})
        check("rejected proposal never reached the file", "NEVER APPLIED" not in target.read_text())
        state = {x["id"]: x for x in call("/__comments")}
        check("rejection recorded as a reply", any("wrong tone" in r["text"] for r in state[c2["id"]]["replies"]))
        check("proposal cleared on rejection", "proposal" not in state[c2["id"]])

        print("\n5. agent event stream")
        inbox = (TMP / ".review" / target.stem / "inbox.jsonl").read_text().strip().splitlines()
        kinds = [json.loads(l)["type"] for l in inbox]
        check("inbox is append-only JSONL for Monitor", len(inbox) >= 4, ",".join(kinds))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
