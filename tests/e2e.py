#!/usr/bin/env python3
"""End-to-end: start the server on a COPY of a committed fixture and drive the
whole loop over HTTP - edit, comment, propose, approve - then verify the file
on disk actually changed and that nothing outside the edited span moved.
"""
import json
import os
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
TMP = Path("/tmp/stet-e2e")
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


def _port_free(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/info", timeout=1).read()
        return False
    except Exception:
        return True


def attribution_checks(target, check):
    """Exercise both approval doors and raw-content attribution on real writes."""
    print("\n16. attribution: author, approver and exact current content")
    side = target.parent / ".review" / target.stem

    def records():
        path = side / "edits.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def fresh():
        return next(u for u in call("/__units") if u["editable"] and len(u["raw"]) > 80)

    def propose(unit, text, author="Codex"):
        cid = call("/__comment", {"unit": unit["id"], "quote": "",
                                  "comment": "attribution test"})["id"]
        data = {"id": cid, "unit": unit["id"], "text": text}
        if author is not None:
            data["author"] = author
        result = call("/__propose", data)
        assert result.get("ok"), result
        return cid

    def current(raw):
        return next(u for u in call("/__units") if u["raw"] == raw)

    def approved_event(cid):
        return next(e for e in reversed([json.loads(line) for line in
                    (side / "inbox.jsonl").read_text().splitlines()])
                    if e["type"] == "approved" and e["id"] == cid)

    # Serving acknowledges any earlier CLI/external changes in the existing suite.
    call("/")
    unit = fresh()
    raw = "Attribution keeps exact &amp; source markup."
    cid = propose(unit, raw)
    thread = next(c for c in call("/__comments") if c["id"] == cid)
    check("proposal persists the caller's author", thread["proposal"].get("author") == "Codex")
    before = target.read_bytes()
    check("proposal alone writes no target bytes", raw.encode() not in before)
    reply = call("/__approve", {"id": cid, "author": "Julia"})
    check("named proposal approved over HTTP", reply.get("ok"), str(reply))
    rows = records()
    row = rows[-1] if rows else {}
    check("machine record has every required field",
          {"time", "unit", "tag", "author", "approved_by", "before", "after"} <= row.keys())
    check("HTTP record preserves exact before/after and both identities",
          row.get("before") == unit["raw"] and row.get("after") == raw
          and row.get("author") == "Codex" and row.get("approved_by") == "Julia")
    check("HTTP record identifies the changed region and tag",
          row.get("unit") == unit["id"] and row.get("tag") == unit["tag"])
    heading = f"## {row.get('time')} - Codex - approved by Julia - {unit['id']} <{unit['tag']}>"
    check("human log heading names proposer and approver",
          heading in (side / "edits.md").read_text().splitlines())
    event = approved_event(cid)
    check("approved inbox event keeps approver as actor and adds proposer",
          event.get("author") == "Julia" and event.get("proposer") == "Codex")
    landed = current(raw)
    check("editing changes this region's own id", landed["id"] != unit["id"])
    expected = {"author": "Codex", "approved_by": "Julia"}
    attribution = call("/__attribution")
    check("attribution follows raw content to the current id",
          isinstance(attribution, dict) and attribution.get(landed["id"]) == expected
          and unit["id"] not in attribution)
    page = call("/")
    config = json.loads(re.search(r"window\.__RV__=(\{.*?\});</script>", page, re.S).group(1))
    check("serve-time state and attribution endpoint agree", config.get("attribution") == attribution)
    check("serving attribution writes no review markup to target",
          target.read_bytes() == before.replace(unit["raw"].encode(), raw.encode(), 1)
          and b"data-rv-attribution" not in target.read_bytes())

    no_op = call("/__edit", {"id": landed["id"], "text": raw, "author": "Julia"})
    check("a no-op edit adds no attribution record and preserves the last changer",
          no_op.get("ok") and no_op.get("changed") is False and records() == rows
          and call("/__attribution").get(landed["id"]) == expected)

    # Record order, not timestamp sort or text-normalised matching, determines ownership.
    prefix = (side / "edits.jsonl").read_bytes() if rows else b""
    call("/__edit", {"id": landed["id"], "text": "Temporary attribution text.", "author": "Julia"})
    interim = current("Temporary attribution text.")
    call("/__edit", {"id": interim["id"], "text": raw, "author": "Julia"})
    latest = records()[-1] if records() else {}
    check("direct edit records human author with null approver",
          latest.get("author") == "Julia" and "approved_by" in latest and latest["approved_by"] is None)
    check("machine records append without rewriting earlier records",
          (side / "edits.jsonl").read_bytes().startswith(prefix) and len(records()) == len(rows) + 2
          if (side / "edits.jsonl").exists() else False)
    check("latest matching record wins when content returns",
          call("/__attribution").get(current(raw)["id"]) == {"author": "Julia", "approved_by": None})
    direct_heading = f"## {latest.get('time')} - Julia - {interim['id']} <{interim['tag']}>"
    check("direct human heading has no approval label", direct_heading in (side / "edits.md").read_text().splitlines())
    changed_raw = raw.replace("&amp;", "&#38;")
    target.write_bytes(target.read_bytes().replace(raw.encode(), changed_raw.encode(), 1))
    check("equivalent rendered text with different raw markup has no attribution",
          current(changed_raw)["id"] not in call("/__attribution"))
    call("/")

    # Re-place must retain the proposer even when the destination changes.
    source = fresh()
    cid = propose(source, "Re-placed attribution text.")
    dest = current(changed_raw)
    result = call("/__replace", {"id": cid, "anchor": changed_raw, "author": "Julia"})
    check("re-place uses the original proposer and current approver",
          result.get("ok") and records()[-1].get("author") == "Codex"
          and records()[-1].get("approved_by") == "Julia"
          and approved_event(cid).get("proposer") == "Codex", str(result))

    # The compatibility fallback for a new authorless request is the server author.
    unit = fresh()
    cid = propose(unit, "Fallback identity proposal.", author=None)
    thread = next(c for c in call("/__comments") if c["id"] == cid)
    check("new authorless proposal preserves resolved server author",
          thread["proposal"].get("author") == "Julia")
    call("/__bin", {"id": cid})

    for legacy in (False, True):
        unit = fresh()
        cid = propose(unit, "Legacy CLI attribution." if legacy else "Named CLI attribution.")
        if legacy:
            state = json.loads((side / "comments.json").read_text())
            next(c for c in state if c["id"] == cid)["proposal"].pop("author", None)
            (side / "comments.json").write_text(json.dumps(state))
        cli = subprocess.run([sys.executable, str(ROOT / "server.py"), str(target),
                              "--approve", cid, "--author", "Julia"], capture_output=True, text=True)
        label = "legacy" if legacy else "named"
        check(f"{label} CLI approval succeeds", cli.returncode == 0, cli.stdout.strip())
        expected_author = "unknown agent" if legacy else "Codex"
        row = records()[-1] if records() else {}
        check(f"{label} CLI log records proposer and approver",
              row.get("author") == expected_author and row.get("approved_by") == "Julia"
              and f" - {expected_author} - approved by Julia - " in (side / "edits.md").read_text())
        event = approved_event(cid)
        check(f"{label} CLI inbox distinguishes actor and proposer",
              event.get("author") == "Julia" and event.get("proposer") == expected_author)
        call("/")

    # Legacy HTTP approval independently exercises the other fallback call site.
    unit = fresh()
    cid = propose(unit, "Legacy HTTP attribution.")
    state = json.loads((side / "comments.json").read_text())
    next(c for c in state if c["id"] == cid)["proposal"].pop("author", None)
    (side / "comments.json").write_text(json.dumps(state))
    result = call("/__approve", {"id": cid})
    check("legacy HTTP approval never guesses an agent name",
          result.get("ok") and records()[-1].get("author") == "unknown agent"
          and approved_event(cid).get("proposer") == "unknown agent")
    hostile = '</script><img src=x onerror="window.attributionAttack=1">'
    unit = current("Legacy HTTP attribution.")
    result = call("/__edit", {"id": unit["id"], "text": "Hostile author label text.",
                               "author": hostile})
    page = call("/")
    payload = re.search(r"window\.__RV__=(\{.*?\});</script>", page, re.S).group(1)
    config = json.loads(payload)
    check("hostile author round-trips as a label in served attribution state",
          result.get("ok") and config.get("attribution", {}).get(current("Hostile author label text.")["id"])
          == {"author": hostile, "approved_by": None})
    check("hostile author cannot close the injected configuration script",
          hostile not in page and "<" not in payload)
    attribution = call("/__attribution")
    with (side / "edits.jsonl").open("a") as f:
        f.write('not valid json\n[]\n{"after": "Legacy HTTP attribution.", "author": 3}\n')
    check("damaged machine records do not hide valid attribution",
          call("/__attribution") == attribution and bool(attribution))


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
        # Derived from the server's own set, never re-typed here. A hardcoded
        # list silently covers fewer endpoints than exist the moment one is
        # added, while still reporting a clean pass.
        sys.path.insert(0, str(ROOT))
        import server as _srv
        BROWSER_ONLY = sorted(_srv.BROWSER_ONLY)
        before_doc = target.read_text()
        sidecar = TMP / ".review" / target.stem
        before_side = sorted((f.name, f.read_text()) for f in sidecar.iterdir())

        bare = []
        for ep in BROWSER_ONLY:
            st_code, _ = raw_call(ep, {"id": "c01", "text": "HIJACKED",
                                       "comment": "x", "reason": "x"},
                                  {"Content-Type": "application/json"})
            bare.append((ep, st_code))
        check(f"all {len(BROWSER_ONLY)} browser-only endpoints refuse a tokenless, "
              f"Origin-less POST",
              all(c == 403 for _, c in bare) and len(BROWSER_ONLY) >= 9,
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

        print("\n8. agent event stream: complete, authored, unfiltered (R4.4, R4.6)")

        stale = call("/__propose", {"id": cid_r if False else "c01",
                                    "unit": "rdeadbeef00",
                                    "text": "cannot land anywhere"})
        check("a proposal against a vanished region is refused at propose time",
              stale.get("ok") is False and stale.get("status") == "unanchored",
              json.dumps(stale)[:100])

        # an agent-authored edit and reply must BOTH appear. They were filtered
        # out at write time, so the record was missing exactly the events a
        # second reader would care about.
        agent_unit = next(u for u in call("/__units")
                          if u["editable"] and len(u["raw"].strip()) > 60)
        call("/__edit", {"id": agent_unit["id"], "text": "AGENT WROTE THIS",
                         "author": "Claude"})
        cid_r = call("/__comment", {"unit": agent_unit["id"], "quote": "",
                                    "comment": "for an agent reply"})["id"]
        call("/__reply", {"id": cid_r, "text": "agent replying", "author": "Claude"})

        inbox_path = TMP / ".review" / target.stem / "inbox.jsonl"
        lines = inbox_path.read_text().strip().splitlines()
        events = [json.loads(l) for l in lines]
        kinds = [e["type"] for e in events]

        check("inbox is append-only JSONL for Monitor", len(lines) >= 4,
              f"{len(lines)} events")
        check("an edit authored Claude is recorded",
              any(e["type"] == "edit" and e.get("author") == "Claude" for e in events))
        check("a reply authored Claude is recorded",
              any(e["type"] == "reply" and e.get("author") == "Claude" for e in events))

        # the one that matters: nothing in the stream may lack an author
        missing = [e for e in events if not e.get("author")]
        check("EVERY event carries a non-empty author",
              not missing,
              f"{len(missing)} without: {sorted({e['type'] for e in missing})}"
              if missing else f"{len(events)} events")

        # and every event type reached by this run is represented
        check("several event types were exercised, not just one",
              len(set(kinds)) >= 5, ",".join(sorted(set(kinds))))

        # ordering preserved and the file only ever grew
        before_len = len(lines)
        call("/__comment", {"unit": agent_unit["id"], "quote": "", "comment": "one more"})
        after = inbox_path.read_text().strip().splitlines()
        check("the file is append-only: earlier lines are untouched",
              after[:before_len] == lines)
        check("and it grew by exactly the new event", len(after) == before_len + 1)

        # no author filtering anywhere in the source
        src = (ROOT / "server.py").read_text()
        check("no event is filtered by author at write time",
              'who != "Claude"' not in src and "who != 'Claude'" not in src)

        # the CLI verb records its own write (KTD7)
        # re-read the units: agent_unit's id went stale the moment its text was
        # edited above, which is exactly what R1.1's identity scheme does.
        cli_unit = next(u for u in call("/__units")
                        if u["editable"] and len(u["raw"].strip()) > 60
                        and "AGENT WROTE THIS" not in u["raw"])
        cid_cli = call("/__comment", {"unit": cli_unit["id"], "quote": "",
                                      "comment": "approved from the shell"})["id"]
        pr = call("/__propose", {"id": cid_cli, "unit": cli_unit["id"],
                                 "text": "APPLIED FROM THE CLI"})
        check("the proposal anchored", pr.get("ok"), json.dumps(pr)[:90])
        before_cli = len(inbox_path.read_text().strip().splitlines())
        cli = subprocess.run(
            [sys.executable, str(ROOT / "server.py"), str(target), "--approve", cid_cli],
            capture_output=True, text=True)
        cli_events = [json.loads(l) for l in
                      inbox_path.read_text().strip().splitlines()[before_cli:]]
        check("server.py --approve applied it", cli.returncode == 0,
              (cli.stdout + cli.stderr).strip()[:80])
        check("and appended an approved event",
              any(e["type"] == "approved" for e in cli_events),
              str([e["type"] for e in cli_events]))
        # all([]) is True, so the count is asserted first: a vacuous pass over an
        # empty list is the same defect as a suite that tested nothing.
        check("with an author on it",
              len(cli_events) > 0 and all(e.get("author") for e in cli_events),
              f"{len(cli_events)} events")

        print("\n9. watch.py: the agent's turn, as a command (R4.1-R4.6)")

        def watch(ident, since="0", timeout=6, wait=True):
            """Run watch.py. Returns (events, cursor, returncode)."""
            r = subprocess.run(
                [sys.executable, str(ROOT / "watch.py"), str(target),
                 "--as", ident, "--since", str(since),
                 "--timeout", str(timeout), "--poll", "0.05"],
                capture_output=True, text=True, timeout=timeout + 20)
            evs = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
            cur = None
            for l in r.stderr.splitlines():
                try:
                    cur = json.loads(l).get("cursor")
                except json.JSONDecodeError:
                    pass
            return evs, cur, r.returncode

        # where the stream currently is
        inbox_now = len((TMP / ".review" / target.stem / "inbox.jsonl")
                        .read_text().strip().splitlines())

        # (a) blocks, then returns when an event is appended
        w = subprocess.Popen(
            [sys.executable, str(ROOT / "watch.py"), str(target),
             "--as", "Claude", "--since", str(inbox_now),
             "--timeout", "15", "--poll", "0.05"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.6)
        check("a. it is still blocking with nothing to report", w.poll() is None)
        live_unit = next(u for u in call("/__units")
                         if u["editable"] and len(u["raw"].strip()) > 60)
        call("/__comment", {"unit": live_unit["id"], "quote": "",
                            "comment": "wake the watcher"})
        out, err = w.communicate(timeout=20)
        evs = [json.loads(l) for l in out.splitlines() if l.strip()]
        check("a. it returned once an event was appended", w.returncode == 0)
        check("a. and returned the event", any(e["type"] == "comment" for e in evs),
              str([e["type"] for e in evs]))

        # (b) R4.2: each stdout line has the SAME KEYS as its inbox.jsonl line
        raw_lines = (TMP / ".review" / target.stem / "inbox.jsonl") \
            .read_text().strip().splitlines()
        by_shape = {}
        for rl in raw_lines:
            o = json.loads(rl)
            by_shape.setdefault((o["type"], o.get("at")), o)
        shape_ok, shape_why = True, ""
        for e in evs:
            src = by_shape.get((e["type"], e.get("at")))
            if src is None or set(src.keys()) != set(e.keys()):
                shape_ok = False
                shape_why = f"{e['type']}: {sorted(e.keys())} vs " \
                            f"{sorted(src.keys()) if src else 'missing'}"
                break
        check("b. every output line carries the same keys as its inbox line",
              shape_ok and len(evs) > 0, shape_why or f"{len(evs)} events")

        # watch.py claims to print the line VERBATIM, so that is what is checked,
        # not merely that the keys survived. Re-serialising would reorder keys
        # and quietly change the shape an integration was written against.
        raw_set = {l.strip() for l in raw_lines}
        printed = [l for l in out.splitlines() if l.strip()]
        check("b. and is byte-for-byte the line the server wrote",
              len(printed) > 0 and all(l in raw_set for l in printed),
              f"{len(printed)} lines")
        check("b. stdout carries events only, no trailer to special-case",
              all("cursor" not in e for e in evs))

        cursor_after_a = None
        for l in err.splitlines():
            try:
                cursor_after_a = json.loads(l).get("cursor")
            except json.JSONDecodeError:
                pass
        check("b. the cursor came back on stderr", cursor_after_a is not None,
              str(cursor_after_a))

        # (c) cursor round trip: three events while disconnected -> exactly three
        for i in range(3):
            call("/__comment", {"unit": live_unit["id"], "quote": "",
                                "comment": f"while disconnected {i}"})
        evs3, cur3, rc3 = watch("Claude", since=cursor_after_a, timeout=6)
        check("c. reconnecting returns exactly the three missed events",
              len(evs3) == 3, f"{len(evs3)}: {[e.get('comment') for e in evs3]}")
        evs_again, cur_again, _ = watch("Claude", since=cur3, timeout=1)
        check("c. and they are not returned a second time", len(evs_again) == 0,
              str(len(evs_again)))

        # (d) the cursor survives a server restart
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        proc2 = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), str(target),
             "--port", str(PORT), "--author", "Julia", "--idle-timeout", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(60):
            try:
                call("/info"); break
            except Exception:
                time.sleep(0.1)
        page3 = call("/")
        TOKEN = _read_token(page3)
        globals()["TOKEN"] = TOKEN
        unit2 = next(u for u in call("/__units")
                     if u["editable"] and len(u["raw"].strip()) > 60)
        call("/__comment", {"unit": unit2["id"], "quote": "",
                            "comment": "after the restart"})
        evsR, curR, _ = watch("Claude", since=cur_again, timeout=6)
        check("d. a cursor from before the restart still resumes",
              any(e.get("comment") == "after the restart" for e in evsR),
              str([e.get("comment") for e in evsR]))
        check("d. and returns only what came after it", len(evsR) == 1, str(len(evsR)))
        proc = proc2                      # so the outer finally tears down the right one

        # (e) --as does not return that identity's own events, and they ARE in the file
        base_cursor = curR
        kinds_written = []
        cid_w = call("/__comment", {"unit": unit2["id"], "quote": "",
                                    "comment": "claude comment", "author": "Claude"})["id"]
        kinds_written.append("comment")
        call("/__edit", {"id": unit2["id"], "text": "CLAUDE EDIT FOR WATCH",
                         "author": "Claude"})
        kinds_written.append("edit")
        call("/__reply", {"id": cid_w, "text": "claude reply", "author": "Claude"})
        kinds_written.append("reply")
        u3 = next(u for u in call("/__units")
                  if u["editable"] and len(u["raw"].strip()) > 60)
        call("/__propose", {"id": cid_w, "unit": u3["id"], "text": "CLAUDE PROPOSAL"})
        call("/__approve", {"id": cid_w, "author": "Claude"})
        kinds_written.append("approved")
        cid_w2 = call("/__comment", {"unit": u3["id"], "quote": "",
                                     "comment": "to reject", "author": "Claude"})["id"]
        call("/__propose", {"id": cid_w2, "unit": u3["id"], "text": "X"})
        call("/__reject", {"id": cid_w2, "reason": "no", "author": "Claude"})
        kinds_written.append("rejected")

        tail = [json.loads(l) for l in
                (TMP / ".review" / target.stem / "inbox.jsonl")
                .read_text().strip().splitlines()[int(base_cursor):]]
        in_file = {e["type"] for e in tail if e.get("author") == "Claude"}
        check("e. all five event types were written by Claude to the file",
              in_file >= {"comment", "edit", "reply", "approved", "rejected"},
              str(sorted(in_file)))
        evsC, curC, _ = watch("Claude", since=base_cursor, timeout=2)
        check("e. and watch --as Claude returns none of them",
              not any(e.get("author") == "Claude" for e in evsC),
              str([(e["type"], e.get("author")) for e in evsC]))

        # The cursor must move PAST its own events, not sit before them. If it
        # does not, the agent re-scans the same batch on every reconnect for
        # ever - which is the loop suppression exists to prevent, arrived at
        # from the other direction. Returning nothing looks identical either
        # way, so only the cursor shows the difference.
        check("e. and the cursor advanced past them, so they are not re-scanned",
              curC is not None and int(curC) > int(base_cursor),
              f"{curC} vs {base_cursor}")
        evsJ, curJ, _ = watch("Julia", since=base_cursor, timeout=2)
        check("e. while --as Julia does see them",
              {e["type"] for e in evsJ if e.get("author") == "Claude"}
              >= {"comment", "edit", "reply", "approved", "rejected"},
              str(sorted({e["type"] for e in evsJ})))

        # (f) timeout returns empty plus the unchanged cursor
        t0 = time.monotonic()
        evsT, curT, rcT = watch("Julia", since=curJ, timeout=1)
        elapsed = time.monotonic() - t0
        check("f. a timeout returns no events", len(evsT) == 0, str(len(evsT)))
        check("f. with the cursor unchanged", curT == curJ, f"{curT} vs {curJ}")
        check("f. exit 0, because a quiet period is not an error", rcT == 0)
        check("f. and it actually waited", elapsed >= 0.9, f"{elapsed:.2f}s")

        print("\n10. external modification, and unreadable documents (R7, A9, A10)")

        # (a) no mismatch: writes proceed unchanged
        ok_unit = next(u for u in call("/__units")
                       if u["editable"] and len(u["raw"].strip()) > 60)
        r = call("/__edit", {"id": ok_unit["id"], "text": "ORDINARY EDIT WORKS",
                             "author": "Julia"})
        check("a. with nothing changed underneath, a write proceeds", r.get("ok"),
              json.dumps(r)[:90])

        # (b) another process writes to the file mid-serve
        call("/")                                   # the human is looking at this
        victim = next(u for u in call("/__units")
                      if u["editable"] and len(u["raw"].strip()) > 60
                      and "ORDINARY EDIT WORKS" not in u["raw"])
        time.sleep(0.01)
        outside = target.read_text().replace(
            "</body>", "<p>added by another process entirely</p></body>", 1)
        target.write_text(outside)                  # nothing to do with the server

        before_ext = target.read_text()
        r = call("/__edit", {"id": victim["id"], "text": "SHOULD NOT LAND",
                             "author": "Julia"})
        check("b. the next write is refused", r.get("ok") is False, json.dumps(r)[:90])
        check("b. and says the file changed underneath",
              r.get("status") == "changed-underneath", str(r.get("status")))
        check("b. the message is for a human, not a stack trace",
              "changed on disk" in str(r.get("error", "")), str(r.get("error"))[:90])
        check("b. nothing was written", target.read_text() == before_ext)
        check("b. the other process's change is still there",
              "added by another process entirely" in target.read_text())

        # (c) after the re-read, a subsequent edit succeeds against the new content
        fresh = next(u for u in call("/__units")
                     if u["editable"] and len(u["raw"].strip()) > 60)
        r = call("/__edit", {"id": fresh["id"], "text": "AFTER THE RE-READ",
                             "author": "Julia"})
        check("c. the next attempt succeeds against the new content", r.get("ok"),
              json.dumps(r)[:90])
        check("c. and it landed", "AFTER THE RE-READ" in target.read_text())

        # (d) an approval is guarded on the same path
        call("/")
        au = next(u for u in call("/__units")
                  if u["editable"] and len(u["raw"].strip()) > 60)
        cid_x = call("/__comment", {"unit": au["id"], "quote": "",
                                    "comment": "guarded approval"})["id"]
        call("/__propose", {"id": cid_x, "unit": au["id"], "text": "APPROVED MID-CHANGE"})
        time.sleep(0.01)
        target.write_text(target.read_text().replace(
            "</body>", "<p>and another outside change</p></body>", 1))
        before_x = target.read_text()
        r = call("/__approve", {"id": cid_x})
        check("d. an approval is refused the same way",
              r.get("status") == "changed-underneath", json.dumps(r)[:90])
        check("d. and the proposal did not reach the file",
              target.read_text() == before_x)

        # (e) A10: each malformed document is served read-only with a reason
        import glob as _glob
        mal = sorted(_glob.glob(str(ROOT / "tests" / "fixtures" / "malformed" / "*.html")))
        check("e. there are malformed fixtures to serve", len(mal) == 4, str(len(mal)))
        served_ro, raised = [], []
        for mpath in mal:
            mp = Path(mpath)
            mtarget = TMP / ("mal-" + mp.name)
            shutil.copy(mp, mtarget)
            mport = PORT + 3
            mproc = subprocess.Popen(
                [sys.executable, str(ROOT / "server.py"), str(mtarget),
                 "--port", str(mport), "--author", "Julia", "--idle-timeout", "0"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            main_base, main_token, main_origin = BASE, TOKEN, ORIGIN
            BASE = f"http://127.0.0.1:{mport}"
            try:
                up = False
                for _ in range(60):
                    try:
                        call("/info"); up = True; break
                    except Exception:
                        time.sleep(0.1)
                if not up:
                    raised.append(mp.name)
                    continue
                page_m = call("/")
                TOKEN = _read_token(page_m)
                ORIGIN = f"http://127.0.0.1:{mport}"
                cfg_ro = '"readOnly": true' in page_m
                mu = call("/__units")
                all_locked = bool(mu) and all(not u["editable"] for u in mu)
                reasoned = all(u.get("reason") for u in mu if not u["editable"])
                if cfg_ro:
                    served_ro.append(mp.name)
                    # and a write is actually refused, not merely discouraged
                    if mu:
                        before_m = mtarget.read_text()
                        rr = call("/__edit", {"id": mu[0]["id"], "text": "NOPE",
                                              "author": "Julia"})
                        if rr.get("ok") or mtarget.read_text() != before_m:
                            raised.append(mp.name + " (write not refused)")
                    if not (all_locked and reasoned):
                        raised.append(mp.name + " (regions not all locked with a reason)")
            finally:
                BASE, TOKEN, ORIGIN = main_base, main_token, main_origin
                mproc.terminate()
                try:
                    mproc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    mproc.kill()

        check("e. every malformed document served without the server dying",
              not [x for x in raised if "(" not in x], str(raised))
        check("e. the unreadable ones are served read-only, locked, with a reason",
              set(served_ro) >= {"truncated.html", "unclosed.html"},
              f"read-only: {sorted(served_ro)}")
        check("e. and no read-only document accepted a write",
              not [x for x in raised if "(" in x], str(raised))

        print("\n11. the cooperative gate, tested with a seam (I6)")

        # I6's old test was "there is no code path from /__propose to a write".
        # That is true, and passes while every one of the three known bypasses
        # exists. A test that cannot fail is not a test, so this asserts what the
        # cooperative gate actually guarantees: an agent that writes the file
        # directly is DETECTED and the human is told.
        #
        # The case is run TWICE against two servers - one ordinary, one with the
        # seam on - so gate 3 is this command and its exit code, not a procedure
        # someone has to remember to perform by hand at every publish.

        def i6_case(env_extra, port_offset, label):
            """Agent writes the file directly while served. Returns (refused, wrote)."""
            src = ROOT / "tests" / "fixtures" / "prose-article.html"
            tgt = TMP / f"i6-{label}.html"
            shutil.copy(src, tgt)
            port = PORT + 10 + port_offset
            env = dict(os.environ)
            env.update(env_extra)
            proc_i6 = subprocess.Popen(
                [sys.executable, str(ROOT / "server.py"), str(tgt),
                 "--port", str(port), "--author", "Julia", "--idle-timeout", "0"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            main_base, main_token, main_origin = BASE, TOKEN, ORIGIN
            globals()["BASE"] = f"http://127.0.0.1:{port}"
            try:
                for _ in range(60):
                    try:
                        call("/info"); break
                    except Exception:
                        time.sleep(0.1)
                page_i6 = call("/")
                globals()["TOKEN"] = _read_token(page_i6)
                globals()["ORIGIN"] = f"http://127.0.0.1:{port}"

                u = next(x for x in call("/__units")
                         if x["editable"] and len(x["raw"].strip()) > 60)
                cid = call("/__comment", {"unit": u["id"], "quote": "",
                                          "comment": "gate case"})["id"]
                call("/__propose", {"id": cid, "unit": u["id"],
                                    "text": "APPROVED AFTER A DIRECT WRITE"})

                # the agent goes round the server entirely, with its own file tools
                time.sleep(0.01)
                tgt.write_text(tgt.read_text().replace(
                    "</body>", "<p>written directly by an agent</p></body>", 1))

                before = tgt.read_text()
                r = call("/__approve", {"id": cid})
                refused = r.get("status") == "changed-underneath"
                wrote = tgt.read_text() != before
                return refused, wrote, r
            finally:
                globals()["BASE"], globals()["TOKEN"], globals()["ORIGIN"] = \
                    main_base, main_token, main_origin
                proc_i6.terminate()
                try:
                    proc_i6.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc_i6.kill()

        # The seam is inherited from the ambient environment here ON PURPOSE.
        # Under an ordinary run it is off and this must refuse; under
        # RV_GATE_DISABLED=1 python3 tests/e2e.py it is on and this must fail,
        # which is what makes that command gate 3.
        refused_on, wrote_on, resp_on = i6_case({}, 0, "gate-live")
        check("a. an agent's direct write is detected and the approval refused",
              refused_on, json.dumps(resp_on)[:100])
        check("a. and the proposal did not reach the file", not wrote_on)
        check("a. the human is told what happened, in words",
              "changed on disk" in str(resp_on.get("error", "")),
              str(resp_on.get("error"))[:90])

        # the same case with the detection switched off: it must NOT be detected
        refused_off, wrote_off, resp_off = i6_case({"RV_GATE_DISABLED": "1"}, 1, "gate-off")
        check("b. with RV_GATE_DISABLED=1 the same case is NOT detected",
              not refused_off, json.dumps(resp_off)[:100])
        check("b. so the assertion above is one that can fail", refused_on and not refused_off)

        # The seam must not be reachable from a request. Checked against the
        # PARSE TREE rather than by splitting the source on text: an earlier
        # version of this check split on "def do_POST" and failed on a startup
        # print that merely sits later in the file, which is a check reporting on
        # where lines happen to be rather than on what the code does.
        import ast as _ast
        tree = _ast.parse((ROOT / "server.py").read_text())

        assigns = [n for n in _ast.walk(tree)
                   if isinstance(n, _ast.Assign)
                   and any(isinstance(tgt, _ast.Name) and tgt.id == "GATE_DISABLED"
                           for tgt in n.targets)]
        check("c. the seam is assigned exactly once", len(assigns) == 1, str(len(assigns)))
        check("c. and only from the environment",
              len(assigns) == 1 and "os.environ" in _ast.unparse(assigns[0].value),
              _ast.unparse(assigns[0].value)[:60] if assigns else "none")

        handler = next((n for n in _ast.walk(tree)
                        if isinstance(n, _ast.ClassDef) and n.name == "Handler"), None)
        check("c. the request handler exists to check", handler is not None)
        names_in_handler = {n.id for n in _ast.walk(handler) if isinstance(n, _ast.Name)}
        strings_in_handler = {n.value for n in _ast.walk(handler)
                              if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
        check("c. no request path reads the seam, by name or by string",
              "GATE_DISABLED" not in names_in_handler
              and not any("RV_GATE_DISABLED" in s for s in strings_in_handler))

        # (d) a proposal never approved never appears in the file
        nu = next(u for u in call("/__units")
                  if u["editable"] and len(u["raw"].strip()) > 60)
        cid_n = call("/__comment", {"unit": nu["id"], "quote": "",
                                    "comment": "never approved"})["id"]
        call("/__propose", {"id": cid_n, "unit": nu["id"],
                            "text": "NEVER APPROVED SO NEVER WRITTEN"})
        check("d. a proposal never approved is not in the file",
              "NEVER APPROVED SO NEVER WRITTEN" not in target.read_text())
        state = {x["id"]: x for x in call("/__comments")}
        check("d. and it is still recorded, waiting",
              state[cid_n]["status"] == "proposed")

        # (e) a rejected proposal never appears, and the reason is recorded
        cid_j = call("/__comment", {"unit": nu["id"], "quote": "",
                                    "comment": "to be rejected"})["id"]
        call("/__propose", {"id": cid_j, "unit": nu["id"],
                            "text": "REJECTED SO NEVER WRITTEN"})
        call("/__reject", {"id": cid_j, "reason": "the tone is wrong"})
        check("e. a rejected proposal is not in the file",
              "REJECTED SO NEVER WRITTEN" not in target.read_text())
        state = {x["id"]: x for x in call("/__comments")}
        check("e. the reason is recorded",
              any("tone is wrong" in r["text"] for r in state[cid_j]["replies"]))
        check("e. and the proposal is cleared", "proposal" not in state[cid_j])

        print("\n12. lifecycle: detach, the idle clock, loopback (R6.3-R6.7)")

        # (a) --detach returns the shell immediately, and the server survives it.
        #     The plan called this a manual check; it is not, so it is not left
        #     to someone's memory.
        det_src = ROOT / "tests" / "fixtures" / "prose-article.html"
        det_target = TMP / "detached.html"
        shutil.copy(det_src, det_target)
        det_port = PORT + 20
        t0 = time.monotonic()
        # A short --max-life on purpose: if this test ever fails to clean up,
        # the leak heals itself in seconds instead of sitting there for the
        # eight-hour default. An earlier run of this very test left a detached
        # server on this port, which is the failure R6.5 and R6.7 are about.
        # A --detach that does not release the launcher's stdio HANGS rather
        # than fails, and a hang tells whoever is running the gates nothing. The
        # timeout is caught and reported as a failed check instead.
        try:
            launch = subprocess.run(
                [sys.executable, str(ROOT / "server.py"), str(det_target),
                 "--port", str(det_port), "--author", "Julia",
                 "--idle-timeout", "0", "--max-life", "60", "--detach"],
                capture_output=True, text=True, timeout=25)
            launched_in = time.monotonic() - t0
            check("a. --detach returns the launching command", launch.returncode == 0,
                  f"{launched_in:.2f}s")
        except subprocess.TimeoutExpired:
            launched_in = time.monotonic() - t0
            launch = subprocess.CompletedProcess([], 1, stdout="", stderr="")
            check("a. --detach returns the launching command", False,
                  "it did not return: the launcher was still held after 25s, so "
                  "--detach is not detaching")
        check("a. quickly, rather than holding the terminal", launched_in < 10,
              f"{launched_in:.2f}s")
        det_pid = None
        for line in launch.stdout.splitlines():
            if "detached" in line and "pid" in line:
                det_pid = int(line.split("pid")[1].split(",")[0].strip())
        check("a. and reports the pid it left running", det_pid is not None,
              launch.stdout.strip().splitlines()[-1] if launch.stdout.strip() else "")

        det_base = f"http://127.0.0.1:{det_port}"
        alive = False
        for _ in range(60):
            try:
                urllib.request.urlopen(det_base + "/info", timeout=2).read()
                alive = True
                break
            except Exception:
                time.sleep(0.1)
        check("a. the detached server is serving after its launcher exited", alive)

        # (e) R6.4: a busy port is reported clearly, naming the file
        clash = subprocess.run(
            [sys.executable, str(ROOT / "server.py"), str(target),
             "--port", str(det_port), "--author", "Julia", "--idle-timeout", "0"],
            capture_output=True, text=True, timeout=30)
        both = clash.stdout + clash.stderr
        check("e. a port already in use fails rather than hanging",
              clash.returncode != 0, str(clash.returncode))
        check("e. and names the file the existing server is serving",
              "detached.html" in both, both.strip().splitlines()[-1][:90] if both.strip() else "")

        # Ask the server for its own pid rather than relying on having parsed
        # the launcher's output: if the parse failed, the process is still there.
        if det_pid is None:
            try:
                det_pid = json.loads(urllib.request.urlopen(
                    det_base + "/info", timeout=2).read().decode()).get("pid")
            except Exception:
                det_pid = None
        if det_pid:
            try:
                os.kill(det_pid, 15)
            except (ProcessLookupError, PermissionError):
                pass
        for _ in range(40):
            try:
                urllib.request.urlopen(det_base + "/info", timeout=1).read()
                time.sleep(0.1)
            except Exception:
                break
        check("a. and it stops when asked, leaving nothing behind",
              _port_free(det_port), f"port {det_port}")

        # (b) polling alone does not keep a server alive: the idle clock counts
        #     human interaction, and /__version is not that.
        poll_target = TMP / "polled.html"
        shutil.copy(det_src, poll_target)
        poll_port = PORT + 21
        poll_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), str(poll_target),
             "--port", str(poll_port), "--author", "Julia", "--idle-timeout", "2"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        poll_base = f"http://127.0.0.1:{poll_port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(poll_base + "/info", timeout=2).read()
                break
            except Exception:
                time.sleep(0.1)
        # poll exactly as the injected client does, faster than the idle timeout
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and poll_proc.poll() is None:
            try:
                urllib.request.urlopen(poll_base + "/__version", timeout=2).read()
            except Exception:
                break
            time.sleep(0.3)
        time.sleep(1.0)
        check("b. a page polling with no human interaction does not hold it open",
              poll_proc.poll() is not None,
              "still running" if poll_proc.poll() is None else "exited")
        if poll_proc.poll() is None:
            poll_proc.kill()

        # (c) a detached server exits at its lifetime cap despite continuous polling
        cap_target = TMP / "capped.html"
        shutil.copy(det_src, cap_target)
        cap_port = PORT + 22
        cap_launch = subprocess.run(
            [sys.executable, str(ROOT / "server.py"), str(cap_target),
             "--port", str(cap_port), "--author", "Julia",
             "--idle-timeout", "0", "--max-life", "3", "--detach"],
            capture_output=True, text=True, timeout=30)
        cap_base = f"http://127.0.0.1:{cap_port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(cap_base + "/info", timeout=2).read()
                break
            except Exception:
                time.sleep(0.1)
        # keep it busy the whole time, including real writes
        gone_at = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 14:
            try:
                urllib.request.urlopen(cap_base + "/__version", timeout=2).read()
            except Exception:
                gone_at = time.monotonic() - t0
                break
            time.sleep(0.25)
        check("c. a detached server exits at its cap despite continuous polling",
              gone_at is not None, f"still up after 14s" if gone_at is None
              else f"exited after {gone_at:.1f}s")
        check("c. and it lived at least as long as its cap",
              gone_at is None or gone_at >= 2.5, f"{gone_at}")

        # (d) R6.6: loopback only, and no flag can change it
        srv_src = (ROOT / "server.py").read_text()
        import ast as _ast2
        tree2 = _ast2.parse(srv_src)
        binds = [n for n in _ast2.walk(tree2)
                 if isinstance(n, _ast2.Call)
                 and getattr(n.func, "id", "") == "HTTPServer"]
        check("d. there is exactly one bind site", len(binds) == 1, str(len(binds)))
        check("d. and it binds the loopback constant, not a variable address",
              len(binds) == 1 and "LOOPBACK" in _ast2.unparse(binds[0].args[0]),
              _ast2.unparse(binds[0].args[0]) if binds else "none")
        check("d. no CLI flag sets an address",
              not any(flag in srv_src for flag in
                      ('"--host"', "'--host'", '"--bind"', "'--bind'",
                       '"--address"', "'--address'", '"0.0.0.0"')))
        info_target = json.loads(
            urllib.request.urlopen(BASE + "/info", timeout=3).read().decode())
        check("d. the running server reports itself on the loopback address",
              "127.0.0.1" in BASE, BASE)

        print("\n13. locked regions are marked at rest (R1.2, I5, A15)")

        lk_src = ROOT / "tests" / "fixtures" / "js-assembled.html"
        lk_target = TMP / "locked.html"
        shutil.copy(lk_src, lk_target)
        lk_port = PORT + 30
        lk_proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), str(lk_target),
             "--port", str(lk_port), "--author", "Julia", "--idle-timeout", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        main_base, main_token, main_origin = BASE, TOKEN, ORIGIN
        BASE = f"http://127.0.0.1:{lk_port}"
        try:
            for _ in range(60):
                try:
                    call("/info"); break
                except Exception:
                    time.sleep(0.1)
            lk_page = call("/")
            TOKEN = _read_token(lk_page)
            ORIGIN = f"http://127.0.0.1:{lk_port}"

            lu = call("/__units")
            locked_units = [u for u in lu if not u["editable"]]
            check("a. the fixture yields locked regions", len(locked_units) >= 2,
                  f"{len(locked_units)} of {len(lu)}")
            check("a. every locked region carries a non-empty reason",
                  all((u.get("reason") or "").strip() for u in locked_units),
                  str([u.get("reason", "")[:40] for u in locked_units][:2]))
            check("a. and every editable one does not claim a reason",
                  all(not (u.get("reason") or "").strip()
                      for u in lu if u["editable"]))

            # b. an edit attempt is refused WITH that reason
            lock1 = locked_units[0]
            before_lk = lk_target.read_text()
            r = call("/__edit", {"id": lock1["id"], "text": "SHOULD BE REFUSED",
                                 "author": "Julia"})
            check("b. an edit to a locked region is refused", r.get("ok") is False,
                  json.dumps(r)[:90])
            check("b. and the refusal carries that region's own reason",
                  lock1["reason"][:24] in str(r.get("error", "")),
                  str(r.get("error"))[:90])
            check("b. nothing was written", lk_target.read_text() == before_lk)

            # c. AT REST in the served page: the marker and the reason are both
            #    present before any interaction, and without needing script.
            check("c. the served page marks locked regions at rest",
                  'data-rv-locked="1"' in lk_page)
            check("c. and carries the reason as an attribute, readable by CSS",
                  "data-rv-reason=" in lk_page)
            for u in locked_units:
                frag = f'data-rv-id="{u["id"]}"'
                idx = lk_page.find(frag)
                seg = lk_page[idx:idx + 400] if idx >= 0 else ""
                if "data-rv-reason=" not in seg:
                    check("c. every locked region carries its reason in the page",
                          False, u["id"])
                    break
            else:
                check("c. every locked region carries its reason in the page", True,
                      f"{len(locked_units)} regions")
            check("c. an editable region is not marked",
                  not any('data-rv-locked' in lk_page[lk_page.find(f'data-rv-id="{u["id"]}"'):
                                                      lk_page.find(f'data-rv-id="{u["id"]}"') + 60]
                          for u in lu if u["editable"]))
        finally:
            BASE, TOKEN, ORIGIN = main_base, main_token, main_origin
            lk_proc.terminate()
            try:
                lk_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                lk_proc.kill()

        attribution_checks(target, check)

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
