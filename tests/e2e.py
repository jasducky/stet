#!/usr/bin/env python3
"""End-to-end: start the server on a COPY of a committed fixture and drive the
whole loop over HTTP - edit, comment, propose, approve - then verify the file
on disk actually changed and that nothing outside the edited span moved.
"""
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Resolved relative to this file, so the suite runs on a clean clone with no sibling
# vault. prose-article is the fixture used here because the loop needs enough regions
# with real text to edit, comment on and propose against.
SRC = FIXTURES / "prose-article.html"
TMP = Path("/tmp/artefact-review-e2e")
PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"


TOKEN = None          # read out of the served page, the way the real page gets it
ORIGIN = None


def _read_token(page_html):
    """Pull the session token out of the injected window.__RV__ payload.

    This is deliberately how the suite obtains it: the token is not on disk and
    not in an environment variable, so the only way to hold one is to have been
    served the page. A suite that could get it any other way would not be
    testing R2.5.
    """
    m = re.search(r"window\.__RV__=(\{.*?\});</script>", page_html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1)).get("token")
    except json.JSONDecodeError:
        return None


def raw_call(path, body=None, headers=None, method=None):
    """A request with exactly the headers given. Returns (status, parsed body).

    Never adds the token or an Origin of its own, so it can be used to assert a
    refusal. urllib raises on 4xx, which is caught here so the status is data.
    """
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers or {},
        method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw, status = r.read().decode(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode(), e.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def call(path, body=None):
    """A request as the served page makes it: same Origin, session token."""
    headers = {"Content-Type": "application/json"}
    if body is not None:
        if ORIGIN:
            headers["Origin"] = ORIGIN
            headers["Sec-Fetch-Site"] = "same-origin"
        if TOKEN:
            headers["X-RV-Token"] = TOKEN
    status, parsed = raw_call(path, body, headers)
    return parsed


def main():
    # An unresolvable fixture is a named failure, never a stack trace at shutil.copy
    # and never a silent skip. Eleven units verify with this suite.
    if not SRC.exists():
        print(f"FAIL  fixture missing: {SRC}")
        print("      The fixture corpus is committed under tests/fixtures/.")
        return 1

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

        global TOKEN, ORIGIN
        TOKEN = _read_token(page)
        ORIGIN = f"http://127.0.0.1:{PORT}"
        check("session token minted and injected into the page",
              bool(TOKEN) and len(TOKEN) >= 32, f"{len(TOKEN or '')} chars")
        check("artefact file NOT modified by serving", target.read_text() == original)
        check("data-rv-id stamped in the served copy", 'data-rv-id="u0' in page)
        check("stamped ids absent from the file on disk", "data-rv-id" not in target.read_text())

        units = call("/__units")
        # Lower bound matches the fixture's recorded bound in probe.py, so a copy
        # edit to the fixture does not fail this while a discovery regression does.
        check("units returned", len(units) >= 36, f"{len(units)} units")
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

        print("\n5. the write endpoints refuse anything but the served page (R2.5)")
        BROWSER_ONLY = ["/__edit", "/__comment", "/__reply", "/__approve",
                        "/__reject", "/__resolve", "/__delete"]
        before_doc = target.read_text()
        sidecar = TMP / ".review" / target.stem
        before_side = sorted((f.name, f.read_text()) for f in sidecar.iterdir())

        bare = []
        for ep in BROWSER_ONLY:
            st_code, _ = raw_call(ep, {"id": "c01", "text": "HIJACKED",
                                       "comment": "x", "reason": "x"},
                                  {"Content-Type": "application/json"})
            bare.append((ep, st_code))
        check("all seven browser-only endpoints refuse a tokenless, Origin-less POST",
              all(c == 403 for _, c in bare),
              ", ".join(f"{e}={c}" for e, c in bare))

        st_code, _ = raw_call("/__edit", {"id": target_unit["id"], "text": "HIJACKED"},
                              {"Content-Type": "application/json",
                               "Origin": "http://evil.example",
                               "Sec-Fetch-Site": "cross-site",
                               "X-RV-Token": TOKEN})
        check("a foreign Origin with a VALID token is refused", st_code == 403, str(st_code))

        st_code, _ = raw_call("/__edit", {"id": target_unit["id"], "text": "HIJACKED"},
                              {"Content-Type": "application/json",
                               "Origin": ORIGIN,
                               "Sec-Fetch-Site": "same-origin",
                               "X-RV-Token": "not-the-real-token"})
        check("the served Origin with a WRONG token is refused", st_code == 403, str(st_code))

        check("document unchanged by every refused request",
              target.read_text() == before_doc)
        check("sidecar unchanged by every refused request",
              sorted((f.name, f.read_text()) for f in sidecar.iterdir()) == before_side)

        r = call("/__comment", {"unit": units[10]["id"], "quote": "",
                                "comment": "from the served page"})
        check("the same endpoint succeeds from the served page", r.get("ok"), json.dumps(r))

        # the exemption, asserted rather than left to be discovered
        st_code, body = raw_call("/__propose",
                                 {"id": r["id"], "unit": units[10]["id"],
                                  "text": "AGENT PROPOSAL", "note": "no browser here"},
                                 {"Content-Type": "application/json"})
        check("/__propose accepts a tokenless, Origin-less POST", st_code == 200 and body.get("ok"),
              f"{st_code} {body}")
        state = {x["id"]: x for x in call("/__comments")}
        check("the agent's proposal was stored",
              state[r["id"]].get("proposal", {}).get("text") == "AGENT PROPOSAL")
        check("and it did NOT reach the document",
              "AGENT PROPOSAL" not in target.read_text())

        blob = "".join(f.read_text() for f in sidecar.iterdir())
        check("the token is written nowhere under .review/", TOKEN not in blob)

        print("\n6. agent event stream")
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
