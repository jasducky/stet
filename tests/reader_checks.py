"""Browser checks for the reading and editing experience.

Run from tests/browser.py (section 8), so they are part of the browser gate.
Each block starts its own server on a fresh copy of the fixture.

Covers: comments newest first and marked on the page, the resizable and
closable sidebar, text size settings, comment filters, double-click editing
with the cursor where you clicked, saving by leaving the block, the
who-changed-what view, and plain words in the comment dialog. Every block
also checks that nothing stet draws on screen is written into the file.
"""

MIDPOINT = r"""id => {
    const n = document.querySelector(`[data-rv-id="${id}"]`);
    const w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT); const t = w.nextNode();
    const i = t.nodeValue.indexOf(' ', Math.floor(t.nodeValue.length / 2)) + 1;
    const r = document.createRange(); r.setStart(t, i); r.setEnd(t, i + 1);
    const b = r.getBoundingClientRect();
    return {x: b.left + 1, y: b.top + b.height / 2, word: t.nodeValue.slice(i).split(/\s/)[0]};
}"""

STET_MARKUP = (b"rv-has-comment", b"rv-mark", b"rv-focus", b"rv-by-", b"contenteditable",
               b"rv-editing", b"rv-unsaved", b"--rv-")


def run(check, Harness):
    def clean(h, label):
        disk = h.file_on_disk()
        check(f"{label}: nothing stet draws is written into the file",
              not any(m in disk for m in STET_MARKUP))

    def dbl(h, rid):
        pt = h.page.evaluate(MIDPOINT, rid)
        h.page.mouse.dblclick(pt["x"], pt["y"])
        h.page.wait_for_timeout(250)
        return pt

    def editable(h, rid):
        return h.page.locator(f'[data-rv-id="{rid}"][contenteditable="true"]').count() == 1

    def away(h):
        h.page.mouse.click(40, 600)          # the empty left margin
        h.page.wait_for_timeout(1500)

    print("\n8a. comments: newest first, marked on the page")
    with Harness() as h:
        h.page.set_viewport_size({"width": 1600, "height": 1000})
        ids = h.region_ids(editable_only=True, min_text=60)
        text0 = h.page.evaluate("id => document.querySelector(`[data-rv-id=\"${id}\"]`).innerText", ids[0])
        # a quote with different spacing from the source still highlights
        h.api("/__comment", {"unit": ids[0], "quote": "  " + text0[5:30].replace(" ", "\n   "),
                             "comment": "FIRST comment"})
        h.api("/__comment", {"unit": ids[1], "quote": "", "comment": "SECOND comment"})
        h.reload(); h.page.wait_for_selector(".rv-thread")
        check("a. newest comment is at the top", "SECOND" in h.page.locator(".rv-thread").first.inner_text())
        check("a. comments are numbered #1, #2", h.page.locator(".rv-th-h b").first.inner_text() == "#2")
        check("a. commented blocks are tinted", h.page.locator(".rv-has-comment").count() == 2)
        check("a. a badge per commented block", h.page.locator(".rv-mark").count() == 2)
        check("a. the quoted words are highlighted",
              h.page.evaluate("() => CSS.highlights.has('rv-quote') && CSS.highlights.get('rv-quote').size") == 1)
        check("a. badges sit clear of the sidebar", h.page.evaluate("""() => {
            const L = document.querySelector('.rv-side').getBoundingClientRect().left;
            return [...document.querySelectorAll('.rv-mark')].every(b => b.getBoundingClientRect().right <= L); }"""))
        check("a. badges use a drawn icon, not an emoji", h.page.locator(".rv-mark svg").count() == 2)
        clean(h, "a")

    print("\n8b. sidebar: wider, resizable, closable, remembered")
    with Harness() as h:
        h.page.set_viewport_size({"width": 1600, "height": 1000})
        rid = h.region_ids(editable_only=True, min_text=60)[0]
        h.api("/__comment", {"unit": rid, "quote": "", "comment": "a comment"})
        h.reload(); h.page.wait_for_selector(".rv-thread")
        width = lambda: h.page.evaluate("() => document.querySelector('.rv-side').getBoundingClientRect().width")
        w0 = width()
        check("b. sidebar starts wider than 380px", w0 > 380, str(w0))
        grip = h.page.locator(".rv-side-grip").bounding_box()
        h.page.mouse.move(grip["x"] + 4, grip["y"] + 200); h.page.mouse.down()
        h.page.mouse.move(900, grip["y"] + 200, steps=5); h.page.mouse.up()
        w1 = width()
        check("b. dragging its edge widens it", w1 > w0 + 100, f"{w0} -> {w1}")
        h.reload(); h.page.wait_for_selector(".rv-thread")
        check("b. the width survives a reload", abs(width() - w1) < 2)
        check("b. no sidebar button in the top bar", h.page.locator(".rv-bar >> text=Hide sidebar").count() == 0)
        h.page.click("#rv-side-close")
        check("b. the close button hides it", h.page.locator(".rv-side").is_hidden())
        check("b. a tab with the open count brings it back",
              h.page.locator("#rv-side-tab").is_visible() and "(1)" in h.page.inner_text("#rv-side-tab"))
        h.reload(); h.page.wait_for_selector("#rv-side-tab", state="visible")
        check("b. closed stays closed after a reload", h.page.locator(".rv-side").is_hidden())
        h.page.click(".rv-mark")
        check("b. a page badge reopens it", h.page.locator(".rv-side").is_visible())

    print("\n8c. settings: text size for comments and document")
    with Harness() as h:
        h.page.set_viewport_size({"width": 1600, "height": 1000})
        rid = h.region_ids(editable_only=True, min_text=60)[0]
        h.api("/__comment", {"unit": rid, "quote": "", "comment": "a comment"})
        h.reload(); h.page.wait_for_selector(".rv-thread")
        height = lambda sel: h.page.evaluate(f"() => document.querySelector('{sel}').getBoundingClientRect().height")
        node = f'[data-rv-id="{rid}"]'
        t0, d0 = height(".rv-thread"), height(node)
        check("c. Settings sits at the right of the top bar",
              h.page.locator("#rv-settings-btn").bounding_box()["x"] > 1400)
        h.page.click("#rv-settings-btn")
        for _ in range(3): h.page.click('[data-z="side:1"]')
        for _ in range(2): h.page.click('[data-z="doc:1"]')
        check("c. the panel reads 130% and 120%",
              (h.page.inner_text("#rv-z-side"), h.page.inner_text("#rv-z-doc")) == ("130%", "120%"))
        t1, d1 = height(".rv-thread"), height(node)
        check("c. comments got bigger", t1 > t0 * 1.15, f"{t0} -> {t1}")
        check("c. the document got bigger", d1 > d0 * 1.1, f"{d0} -> {d1}")
        check("c. the top bar did not", h.page.evaluate("() => getComputedStyle(document.querySelector('.rv-bar')).zoom") == "1")
        h.page.uncheck("#rv-set-side")
        check("c. the settings tick box hides the sidebar", h.page.locator(".rv-side").is_hidden())
        h.page.check("#rv-set-side")
        h.page.mouse.click(700, 600)
        check("c. clicking away closes the panel", h.page.locator("#rv-settings").is_hidden())
        h.reload(); h.page.wait_for_selector(".rv-thread")
        check("c. sizes are remembered", abs(height(".rv-thread") - t1) < 2 and abs(height(node) - d1) < 2)
        clean(h, "c")

    print("\n8d. comment filters")
    with Harness() as h:
        ids = h.region_ids(editable_only=True, min_text=60)
        c1 = h.api("/__comment", {"unit": ids[0], "quote": "", "comment": "needs approval"})["id"]
        h.api("/__propose", {"id": c1, "unit": ids[0], "text": "PROPOSED TEXT", "author": "Claude"})
        h.api("/__comment", {"unit": ids[1], "quote": "", "comment": "waiting"})
        c3 = h.api("/__comment", {"unit": None, "quote": "", "comment": "finished"})["id"]
        h.api("/__resolve", {"id": c3, "note": ""})
        h.reload(); h.page.wait_for_selector(".rv-thread")
        count = lambda: h.page.locator(".rv-thread").count()
        label = lambda f: h.page.inner_text(f'#rv-filter [data-f="{f}"]').split()[-1]
        check("d. counts read Needs you 1, Waiting 1, Done 1, All 3",
              [label(f) for f in ("needs", "waiting", "done", "all")] == ["1", "1", "1", "3"])
        h.page.click('[data-f="needs"]'); h.page.wait_for_timeout(300)
        check("d. Needs you shows only the proposal",
              count() == 1 and "Approve and apply" in h.page.inner_text(".rv-thread"))
        h.reload(); h.page.wait_for_selector("#rv-filter"); h.page.wait_for_timeout(400)
        check("d. the filter is remembered", count() == 1)
        h.page.click('[data-f="done"]'); h.page.wait_for_timeout(300)
        check("d. Done shows the finished one", count() == 1 and "finished" in h.page.inner_text(".rv-thread"))

    print("\n8e. editing: double-click, cursor where you clicked, leaving saves")
    with Harness() as h:
        h.page.set_viewport_size({"width": 1600, "height": 1000})
        ids = h.region_ids(editable_only=True, min_text=80)
        a, b = ids[1], ids[2]
        h.page.hover(f'[data-rv-id="{a}"]')
        check("e. nothing floats by the cursor on an ordinary block", not h.page.locator(".rv-tools").is_visible())
        check("e. the how-to line is in the top bar", "Double-click to edit" in h.page.inner_text(".rv-bar"))
        pt = dbl(h, a)
        check("e. double-click opens editing", editable(h, a))
        check("e. no Save or Cancel buttons", h.page.locator(".rv-editbar").count() == 0)
        check("e. double-click does not also offer a comment", h.page.locator(".rv-selpop").count() == 0)
        h.page.keyboard.type("AAA1 ")
        text = h.page.inner_text(f'[data-rv-id="{a}"]')
        check("e. typing lands where you clicked, not at the start",
              "AAA1 " + pt["word"] in text and not text.startswith("AAA1"), text[:90])
        away(h)
        check("e. clicking away saves", b"AAA1" in h.file_on_disk())

        ids = h.region_ids(editable_only=True, min_text=80); a, b = ids[1], ids[2]
        dbl(h, a); h.page.keyboard.type("AAA2 ")
        dbl(h, b)
        check("e. double-clicking the next block moves straight into it", editable(h, b))
        h.page.keyboard.type("BBB2 "); h.page.wait_for_timeout(800)
        check("e. no reload interrupts you mid-edit", editable(h, b))
        away(h)
        disk = h.file_on_disk()
        check("e. both blocks saved", b"AAA2" in disk and b"BBB2" in disk)

        ids = h.region_ids(editable_only=True, min_text=80); a = ids[1]
        before = h.file_on_disk()
        dbl(h, a); h.page.keyboard.type("ZZZ "); h.page.keyboard.press("Escape")
        h.page.wait_for_timeout(800)
        check("e. Esc cancels and saves nothing", h.file_on_disk() == before)

        dbl(h, a); h.page.keyboard.type("CCC1 ")
        h.page.mouse.click(40, 600); h.page.wait_for_timeout(100)
        dbl(h, a); h.page.wait_for_timeout(2500)
        a = h.region_ids(editable_only=True, min_text=80)[1]
        check("e. going straight back into a just-saved block reopens it", editable(h, a))
        h.page.keyboard.type("CCC2"); away(h)
        disk = h.file_on_disk()
        check("e. both edits to that block saved", b"CCC1" in disk and b"CCC2" in disk)
        clean(h, "e")

    print("\n8f. who changed what")
    with Harness() as h:
        h.page.set_viewport_size({"width": 1600, "height": 1000})
        ids = h.region_ids(editable_only=True, min_text=80)
        cid = h.api("/__comment", {"unit": ids[3], "quote": "", "comment": "plainer?"})["id"]
        h.api("/__propose", {"id": cid, "unit": ids[3], "text": "AGENT WORDING", "author": "Claude"})
        h.api("/__approve", {"id": cid})
        h.reload()
        h.type_into(h.region_ids(editable_only=True, min_text=80)[1], "PERSON WORDING")
        h.page.wait_for_timeout(1200); h.reload(); h.page.wait_for_selector(".rv-bar")
        check("f. the person's block and the agent's block are told apart",
              h.page.locator(".rv-by-human").count() == 1 and h.page.locator(".rv-by-ai").count() == 1)
        check("f. Normal view is clean", h.page.locator(".rv-auth-tag").first.is_hidden())
        h.page.click('[data-v="auth"]'); h.page.wait_for_timeout(200)
        tags = [t.inner_text().replace("\n", " ") for t in h.page.locator(".rv-auth-tag").all()]
        check("f. labels name who changed each block, from the record",
              "Julia" in tags and any("Claude" in t and "approved by Julia" in t for t in tags), str(tags))
        check("f. labels sit in the left margin", h.page.evaluate("""() => {
            const L = Math.min(...[...document.querySelectorAll('.rv-by-human,.rv-by-ai')].map(n => n.getBoundingClientRect().left));
            return [...document.querySelectorAll('.rv-auth-tag')].every(t => t.getBoundingClientRect().right <= L + 2); }"""))
        h.reload(); h.page.wait_for_selector(".rv-bar")
        check("f. the view choice is remembered", "rv-view-auth" in (h.page.get_attribute("body", "class") or ""))
        clean(h, "f")

    print("\n8g. comment dialog in plain words")
    with Harness() as h:
        rid = h.region_ids(editable_only=True, min_text=60)[1]
        h.select_text(rid, 0, 20)
        h.page.mouse.up()
        h.page.evaluate("() => document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: 300, clientY: 300}))")
        h.page.wait_for_selector(".rv-selpop", timeout=3000)
        h.page.click(".rv-selpop")
        check("g. title says what is being commented on",
              h.page.inner_text(".rv-card h4") == "Comment on the highlighted text")
        check("g. Submit is the main button", h.page.inner_text('.rv-card [data-a="save"]') == "Submit")
        s = h.page.locator('.rv-card [data-a="save"]').bounding_box()
        c = h.page.locator('.rv-card [data-a="cancel"]').bounding_box()
        check("g. Cancel sits apart from Submit", s["x"] - (c["x"] + c["width"]) > 100)
        h.page.click('.rv-card [data-a="cancel"]')
        h.page.click("#rv-page-comment")
        check("g. the whole-document comment lives in the sidebar",
              h.page.inner_text(".rv-card h4") == "Comment on the whole document"
              and h.page.locator(".rv-side #rv-page-comment").count() == 1)

    print("\n8h. every proposal is kept, and the human can say if it did what was asked")
    with Harness() as h:
        import json as _json, urllib.request as _ur, urllib.error as _ue
        ids = h.region_ids(editable_only=True, min_text=60)
        cid = h.api("/__comment", {"unit": ids[0], "quote": "", "comment": "two things please"})["id"]
        h.api("/__propose", {"id": cid, "unit": ids[0], "text": "FIRST ANSWER", "note": "part one", "author": "Claude"})
        h.api("/__propose", {"id": cid, "unit": ids[0], "text": "SECOND ANSWER", "note": "part two", "author": "Claude"})
        state = {c["id"]: c for c in h.api("/__comments")}[cid]
        hist = state.get("proposal_history", [])
        check("h. both proposals are kept in the history, in order",
              [x["text"] for x in hist] == ["FIRST ANSWER", "SECOND ANSWER"], str([x.get("text") for x in hist]))
        check("h. the history says the second replaced the first",
              len(hist) == 2 and hist[1]["replaced_previous"] is True and hist[0]["replaced_previous"] is False)
        inbox = [_json.loads(l) for l in (h.target.parent / ".review" / h.target.stem / "inbox.jsonl").read_text().splitlines()]
        check("h. each proposal is an event in the log",
              [e["text"] for e in inbox if e["type"] == "proposed"] == ["FIRST ANSWER", "SECOND ANSWER"])
        h.api("/__approve", {"id": cid})
        h.reload(); h.page.wait_for_selector(".rv-rate")
        check("h. an answered comment asks 'did it do what you asked?'",
              "Did it do what you asked?" in h.page.inner_text(".rv-rate"))
        h.page.click('.rv-rate [data-a="rate-down"]'); h.page.wait_for_timeout(500)
        h.page.wait_for_selector(".rv-rate-note")
        h.page.fill(".rv-rate-note", "only did half"); h.page.keyboard.press("Enter"); h.page.wait_for_timeout(500)
        state = {c["id"]: c for c in h.api("/__comments")}[cid]
        check("h. the verdict and note are saved on the comment",
              state.get("rating", {}).get("value") == "down" and state["rating"].get("note") == "only did half", str(state.get("rating")))
        inbox = [_json.loads(l) for l in (h.target.parent / ".review" / h.target.stem / "inbox.jsonl").read_text().splitlines()]
        check("h. and logged as events", [e.get("value") for e in inbox if e["type"] == "rated"] == ["down", "down"])
        req = _ur.Request(h.base + "/__rate", data=_json.dumps({"id": cid, "value": "up"}).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            _ur.urlopen(req); refused = False
        except _ue.HTTPError as e:
            refused = e.code in (401, 403)
        check("h. an agent cannot rate its own work (no browser, no token)", refused)
        clean(h, "h")
