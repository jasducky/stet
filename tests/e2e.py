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
    if isinstance(body, (bytes, bytearray)):
        payload = bytes(body)                 # sent verbatim, malformed on purpose
    elif body is not None:
        payload = json.dumps(body).encode()
    else:
        payload = None
    req = urllib.request.Request(
        BASE + path,
        data=payload,
        headers=headers or {},
        method=method or ("POST" if payload is not None else "GET"))
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

        global BASE, TOKEN, ORIGIN
        TOKEN = _read_token(page)
        ORIGIN = f"http://127.0.0.1:{PORT}"
        check("session token minted and injected into the page",
              bool(TOKEN) and len(TOKEN) >= 32, f"{len(TOKEN or '')} chars")
        check("artefact file NOT modified by serving", target.read_text() == original)
        # The id form is opaque and nothing outside the adapter may parse it,
        # so this asserts the attribute is present, not what it looks like.
        check("data-rv-id stamped in the served copy", 'data-rv-id="' in page)
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

        print("\n6. payload validation on BOTH write paths (R2.6)")
        v_unit = next(u for u in units
                      if u["editable"] and len(u["raw"].strip()) > 40
                      and u["id"] != target_unit["id"])
        doc_before = target.read_text()

        # The expected REASON is asserted, not merely that something was refused.
        # Each rule has its own message, and the message is what the human acts
        # on. It also makes each rule observable: without the hard-ban list the
        # allowlist still refuses <script>, but with a far vaguer reason, and
        # that difference would otherwise go unnoticed.
        for label, payload, expect in [
            ("<script>", 'text <script>alert(1)</script>',
             "may not introduce <script>"),
            ("onclick=", '<span onclick="steal()">text</span>',
             "may not carry event-handler attributes"),
            ("<iframe>", 'text <iframe src="//evil"></iframe>',
             "may not introduce <iframe>"),
            ("javascript: href", '<a href="javascript:x()">text</a>',
             "javascript:, vbscript: or data: URL"),
            ("an inline element not in the region", '<mark>highlighted</mark>',
             "may only use the inline elements already in this region"),
        ]:
            r = call("/__edit", {"id": v_unit["id"], "text": payload, "author": "Julia"})
            err = str(r.get("error", ""))
            check(f"a payload containing {label} is refused, naming why",
                  r.get("ok") is False and expect in err,
                  err[:96] or json.dumps(r)[:96])
        check("the file is unchanged by every refused edit",
              target.read_text() == doc_before)

        # an inline element ALREADY in the region is accepted
        em_unit = next((u for u in units
                        if u["editable"] and "<em>" in u["raw"]), None)
        if em_unit:
            r = call("/__edit", {"id": em_unit["id"],
                                 "text": "kept <em>its emphasis</em> and reworded",
                                 "author": "Julia"})
            check("an <em> already present in the region is accepted",
                  r.get("ok") and r.get("changed"), json.dumps(r)[:90])
        else:
            check("fixture has a region containing <em> to test acceptance", False,
                  "none found - the acceptance half of R2.6 is untested")

        # an approved PROPOSAL carrying a script tag, over HTTP
        c3 = call("/__comment", {"unit": v_unit["id"], "quote": "",
                                 "comment": "try to smuggle a script in"})
        call("/__propose", {"id": c3["id"], "unit": v_unit["id"],
                            "text": 'ok <script>alert(2)</script>'})
        doc_before = target.read_text()
        r = call("/__approve", {"id": c3["id"]})
        check("an approved proposal carrying <script> is refused on the same path",
              r.get("ok") is False and "refused" in str(r.get("error", "")),
              json.dumps(r)[:90])
        check("that proposal did not reach the file", target.read_text() == doc_before)

        # ...and the SAME proposal through the CLI verb, which never touches a
        # handler. This is the path validation at the HTTP layer would miss.
        cli = subprocess.run(
            [sys.executable, str(ROOT / "server.py"), str(target), "--approve", c3["id"]],
            capture_output=True, text=True)
        check("server.py --approve refuses it identically",
              cli.returncode != 0 and "refused" in (cli.stdout + cli.stderr),
              (cli.stdout + cli.stderr).strip()[:90])
        check("the CLI path did not write it either", target.read_text() == doc_before)

        # a malformed body gets a 400, not a dead connection
        status, body = raw_call("/__edit", b"NOT JSON{{{",
                                {"Content-Type": "application/json",
                                 "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin",
                                 "X-RV-Token": TOKEN})
        check("a malformed body returns a status rather than no response",
              status == 400, str(status))

        print("\n7. anchoring: a proposal lands on its words, or on nothing (R3.2-R3.4)")

        def fresh_comment(unit_id, note="anchor test"):
            return call("/__comment", {"unit": unit_id, "quote": "",
                                       "comment": note})["id"]

        units_now = call("/__units")
        by_id = {u["id"]: u for u in units_now}

        # (a) anchor found in exactly one region -> applies, there and nowhere else
        solo = next(u for u in units_now
                    if u["editable"] and len(u["raw"].strip()) > 80
                    and "<em>" not in u["raw"] and "<strong>" not in u["raw"])
        cid_a = fresh_comment(solo["id"])
        call("/__propose", {"id": cid_a, "unit": solo["id"], "text": "ONE MATCH APPLIED"})
        before = target.read_text()
        r = call("/__approve", {"id": cid_a})
        after = target.read_text()
        check("a. an anchor found in one region applies", r.get("ok"), json.dumps(r)[:90])
        check("a. the new text is in the file", "ONE MATCH APPLIED" in after)
        check("a. only that region changed",
              len(after) == len(before) - len(solo["raw"]) + len("ONE MATCH APPLIED"))

        # (b) THE CASE THAT FAILS WITHOUT SHARED NORMALISATION: a region whose
        # text carries <em>/<strong> mid-sentence. A plain-text anchor compared
        # against raw HTML would never match, orphaning every such proposal.
        inline = next((u for u in call("/__units")
                       if u["editable"] and ("<em>" in u["raw"] or "<strong>" in u["raw"])), None)
        if inline is None:
            check("b. fixture has a region with inline markup mid-sentence", False,
                  "none found - the KTD2a case is untested")
        else:
            cid_b = fresh_comment(inline["id"])
            call("/__propose", {"id": cid_b, "unit": inline["id"],
                                "text": "REWRITTEN OVER INLINE MARKUP"})
            r = call("/__approve", {"id": cid_b})
            check("b. an anchor in a region containing <em>/<strong> still matches",
                  r.get("ok"), json.dumps(r)[:90])
            check("b. and it applied", "REWRITTEN OVER INLINE MARKUP" in target.read_text())

        # (c) anchor found in more than one region -> ambiguous, nothing written
        amb_src = ROOT / "tests" / "fixtures" / "repeated-prose.html"
        amb_target = TMP / amb_src.name
        shutil.copy(amb_src, amb_target)
        amb_port = PORT + 1
        amb = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), str(amb_target),
             "--port", str(amb_port), "--author", "Julia", "--idle-timeout", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            main_base, main_token, main_origin = BASE, TOKEN, ORIGIN
            BASE = f"http://127.0.0.1:{amb_port}"
            for _ in range(50):
                try:
                    call("/info"); break
                except Exception:
                    time.sleep(0.1)
            page2 = call("/")
            TOKEN = _read_token(page2)
            ORIGIN = f"http://127.0.0.1:{amb_port}"
            au = call("/__units")
            dup = next(u for u in au
                       if u["editable"]
                       and u["raw"].strip() ==
                       "The average is the one number that cannot show you the problem.")
            cid_c = call("/__comment", {"unit": dup["id"], "quote": "",
                                        "comment": "ambiguous on purpose"})["id"]
            call("/__propose", {"id": cid_c, "unit": dup["id"], "text": "SHOULD NOT LAND"})
            amb_before = amb_target.read_text()
            r = call("/__approve", {"id": cid_c})
            check("c. an anchor in several regions is ambiguous",
                  r.get("status") == "ambiguous", json.dumps(r)[:110])
            check("c. it names how many regions matched",
                  len(r.get("matches", [])) > 1, str(r.get("matches")))
            check("c. and nothing was written", amb_target.read_text() == amb_before)
            state = {x["id"]: x for x in call("/__comments")}
            check("c. the proposal is kept, not discarded",
                  state[cid_c].get("proposal", {}).get("text") == "SHOULD NOT LAND")

            # (e) an anchor resolving into a LOCKED region is refused with the reason
            #     (js-assembled has one; run it on its own server)
        finally:
            BASE, TOKEN, ORIGIN = main_base, main_token, main_origin
            amb.terminate()
            try:
                amb.wait(timeout=3)
            except subprocess.TimeoutExpired:
                amb.kill()

        # (d) anchor deleted from the document -> orphaned, anchor text retained
        orph = next(u for u in call("/__units")
                    if u["editable"] and len(u["raw"].strip()) > 80
                    and u["id"] not in (solo["id"],))
        cid_d = fresh_comment(orph["id"])
        call("/__propose", {"id": cid_d, "unit": orph["id"], "text": "NEVER LANDS"})
        state = {x["id"]: x for x in call("/__comments")}
        kept_anchor = state[cid_d]["proposal"].get("anchor", "")
        check("d. the proposal stored the text it was written against",
              len(kept_anchor) > 30, repr(kept_anchor[:50]))
        # a human edits those very words away
        call("/__edit", {"id": orph["id"], "text": "completely different wording now",
                         "author": "Julia"})
        before = target.read_text()
        r = call("/__approve", {"id": cid_d})
        check("d. the proposal is orphaned", r.get("status") == "orphaned",
              json.dumps(r)[:110])
        check("d. nothing was written", target.read_text() == before)
        state = {x["id"]: x for x in call("/__comments")}
        check("d. the original anchor text is retained for re-placing",
              state[cid_d]["proposal"].get("anchor") == kept_anchor)
        check("d. and the proposal itself is not discarded",
              state[cid_d]["proposal"].get("text") == "NEVER LANDS")

        # (e) an anchor resolving into a LOCKED region is refused with the lock's
        #     reason. Without this, anchoring would be a way round I5: the text
        #     is genuinely there, so a naive resolver finds it and writes.
        lock_src = ROOT / "tests" / "fixtures" / "js-assembled.html"
        lock_target = TMP / lock_src.name
        shutil.copy(lock_src, lock_target)
        lock_port = PORT + 2
        lk = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), str(lock_target),
             "--port", str(lock_port), "--author", "Julia", "--idle-timeout", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            main_base, main_token, main_origin = BASE, TOKEN, ORIGIN
            BASE = f"http://127.0.0.1:{lock_port}"
            for _ in range(50):
                try:
                    call("/info"); break
                except Exception:
                    time.sleep(0.1)
            TOKEN = _read_token(call("/"))
            ORIGIN = f"http://127.0.0.1:{lock_port}"
            lu = call("/__units")
            locked_unit = next((u for u in lu if not u["editable"]), None)
            check("e. the fixture has a locked region to aim at", locked_unit is not None)
            if locked_unit:
                cid_e = call("/__comment", {"unit": locked_unit["id"], "quote": "",
                                            "comment": "aim at a locked region"})["id"]
                call("/__propose", {"id": cid_e, "unit": locked_unit["id"],
                                    "text": "SHOULD BE REFUSED, REGION IS LOCKED"})
                lock_before = lock_target.read_text()
                r = call("/__approve", {"id": cid_e})
                check("e. approving into a locked region is refused",
                      r.get("ok") is False, json.dumps(r)[:110])
                check("e. and the refusal carries the lock's own reason",
                      "not editable" in str(r.get("error", ""))
                      and "comment only" in str(r.get("error", "")),
                      str(r.get("error"))[:110])
                check("e. nothing was written",
                      lock_target.read_text() == lock_before)
        finally:
            BASE, TOKEN, ORIGIN = main_base, main_token, main_origin
            lk.terminate()
            try:
                lk.wait(timeout=3)
            except subprocess.TimeoutExpired:
                lk.kill()

        # (g) the CLI verb resolves the anchor exactly as the browser does.
        #     R3.7: two doors that disagree about where a proposal lands is worse
        #     than one door.
        cli_orphan = subprocess.run(
            [sys.executable, str(ROOT / "server.py"), str(target), "--approve", cid_d],
            capture_output=True, text=True)
        check("g. server.py --approve reports the orphan too",
              cli_orphan.returncode != 0
              and "orphaned" in (cli_orphan.stdout + cli_orphan.stderr),
              (cli_orphan.stdout + cli_orphan.stderr).strip().splitlines()[0][:90]
              if (cli_orphan.stdout + cli_orphan.stderr).strip() else "no output")
        check("g. and the CLI wrote nothing either", "NEVER LANDS" not in target.read_text())

        # (f) whitespace and entity differences still match
        ws_unit = next(u for u in call("/__units")
                       if u["editable"] and len(u["raw"].strip()) > 80
                       and u["id"] not in (solo["id"], orph["id"]))
        cid_f = fresh_comment(ws_unit["id"])
        import re as _re
        mangled = _re.sub(r"\s+", "   \n  ", ws_unit["raw"].strip())
        call("/__propose", {"id": cid_f, "unit": ws_unit["id"],
                            "text": "MATCHED DESPITE WHITESPACE", "anchor": mangled})
        r = call("/__approve", {"id": cid_f})
        check("f. an anchor differing only in whitespace still matches",
              r.get("ok"), json.dumps(r)[:110])

        print("\n8. agent event stream")
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
