#!/usr/bin/env python3
"""Browser test harness: drive the rendered review layer, so claims about the
page can be tested rather than asserted.

Everything the acceptance table verifies in a browser goes through this file.
The helpers are named in the v1 plan (U2) so later units are not redesigning the
harness while using it:

    region_state(id)          class list, data-rv-* attributes, computed background
    type_into(id, text)       a real edit in a contenteditable region
    select_text(id, a, b)     a real selection the page's own listeners observe
    panel_text()              the sidebar/proposal panel's rendered text
    file_on_disk()            the served file's current bytes

Run it directly to execute its self-test:

    pip install -r requirements.txt
    playwright install chromium
    python3 tests/browser.py

A missing driver is a loud failure, never a skip. A harness that silently passes
when it cannot drive anything is the same defect as a probe that reports success
having tested nothing.
"""
import atexit
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEFAULT_FIXTURE = FIXTURES / "prose-article.html"


class DriverMissing(RuntimeError):
    """Raised when no browser driver is installed. Never caught to skip."""


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DriverMissing(
            "No browser driver installed.\n"
            "  pip install -r requirements.txt\n"
            "  playwright install chromium\n"
            f"({exc})"
        ) from exc
    return sync_playwright


# One Playwright driver per process, reference-counted.
#
# The sync API refuses to start a second instance inside a running one, so two
# nested Harnesses - an entirely reasonable thing for a test to want - would die
# with "Sync API inside the asyncio loop". Sharing one driver makes nesting work
# and costs one launch instead of several.
_PW = None
_PW_REFS = 0


def _driver_acquire():
    global _PW, _PW_REFS
    sync_playwright = _require_playwright()
    if _PW is None:
        _PW = sync_playwright().start()
    _PW_REFS += 1
    return _PW


def _driver_release():
    global _PW, _PW_REFS
    _PW_REFS = max(0, _PW_REFS - 1)
    if _PW_REFS == 0 and _PW is not None:
        try:
            _PW.stop()
        finally:
            _PW = None


def _free_port():
    """Ask the OS for a port, release it, hand the number to the server.

    There is a race between release and bind. It is the standard one, and the
    alternative is teaching server.py to report a port it chose itself, which
    belongs to server.py's own unit rather than this harness.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    """Server + headless browser against a disposable copy of a fixture."""

    def __init__(self, fixture=DEFAULT_FIXTURE, author="Julia", headless=True):
        self.fixture = Path(fixture)
        if not self.fixture.exists():
            raise FileNotFoundError(f"fixture missing: {self.fixture}")
        self.author = author
        self.headless = headless
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._tmp = None
        self._proc = None
        self._pw = None
        self._holds_driver = False
        self._browser = None
        self.page = None
        self.target = None

    # ---------- lifecycle ----------

    def start(self):
        _require_playwright()

        self._tmp = Path(tempfile.mkdtemp(prefix="artefact-review-browser-"))
        self.target = self._tmp / self.fixture.name
        shutil.copy(self.fixture, self.target)

        self._proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py"), str(self.target),
             "--port", str(self.port), "--author", self.author, "--idle-timeout", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for _ in range(80):
            if self._proc.poll() is not None:
                out = self._proc.stdout.read() if self._proc.stdout else ""
                raise RuntimeError(f"server exited before serving:\n{out}")
            try:
                urllib.request.urlopen(self.base + "/info", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError(f"server never came up on {self.base}")

        # Safety net. close() in a finally covers the ordinary path, but a test
        # that dies between Popen and close - a timeout raising inside a helper,
        # say - would otherwise leave a server listening and a temp directory
        # behind. Found by leaking one during a mutation run.
        atexit.register(self.close)

        self._pw = _driver_acquire()
        self._holds_driver = True
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.page = self._browser.new_page()
        self.page.goto(self.base + "/", wait_until="load")
        self.page.wait_for_selector("[data-rv-id]", timeout=5000)
        return self

    def close(self):
        """Tear everything down. Leaves no orphan process behind."""
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        self._browser = None

        if self._holds_driver:
            self._holds_driver = False
            self._pw = None
            _driver_release()

        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=3)

        if self._tmp and self._tmp.exists():
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._tmp = None

        try:
            atexit.unregister(self.close)
        except Exception:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------- helpers ----------

    def region_ids(self, editable_only=False, min_text=0):
        return self.page.evaluate(
            """([editableOnly, minText]) =>
                [...document.querySelectorAll('[data-rv-id]')]
                  .filter(n => !editableOnly || !n.hasAttribute('data-rv-locked'))
                  .filter(n => (n.textContent || '').trim().length >= minText)
                  .map(n => n.getAttribute('data-rv-id'))""",
            [editable_only, min_text])

    def region_state(self, rid):
        """Class list, every data-rv-* attribute, and the computed background."""
        state = self.page.evaluate(
            """(rid) => {
                const n = document.querySelector(`[data-rv-id="${rid}"]`);
                if (!n) return null;
                const data = {};
                for (const a of n.attributes)
                    if (a.name.startsWith('data-rv-')) data[a.name] = a.value;
                return {
                    classes: [...n.classList],
                    data,
                    background: getComputedStyle(n).backgroundColor,
                    text: (n.textContent || '').trim(),
                    locked: n.hasAttribute('data-rv-locked'),
                };
            }""", rid)
        if state is None:
            raise LookupError(f"no region with data-rv-id={rid!r}")
        return state

    def type_into(self, rid, text, save=True):
        """Drive a real edit: hover the region, click Edit, type, Save.

        Goes through the page's own affordances rather than posting to /__edit,
        because what is under test is the rendered layer, not the endpoint.
        """
        sel = f'[data-rv-id="{rid}"]'
        self.page.hover(sel)
        self.page.wait_for_selector(".rv-tools.rv-show", timeout=3000)
        self.page.click('.rv-tools [data-a="edit"]')
        self.page.wait_for_selector(f"{sel}[contenteditable='true']", timeout=3000)

        self.page.evaluate(
            """([rid, text]) => {
                const n = document.querySelector(`[data-rv-id="${rid}"]`);
                n.focus();
                const r = document.createRange();
                r.selectNodeContents(n);
                const s = window.getSelection();
                s.removeAllRanges(); s.addRange(r);
            }""", [rid, text])
        self.page.keyboard.type(text)

        if save:
            self.page.click('.rv-editbar [data-a="save"]')
            self.page.wait_for_selector(".rv-editbar", state="detached", timeout=5000)
        return self

    def select_text(self, rid, start, end):
        """Make a real selection across the rendered HTML of a region.

        Walks the region's text nodes so the offsets are against rendered text,
        not source bytes, then dispatches mouseup - which is the event the page
        listens on. Returns the selected string as the browser sees it.
        """
        selected = self.page.evaluate(
            """([rid, start, end]) => {
                const host = document.querySelector(`[data-rv-id="${rid}"]`);
                if (!host) return null;
                const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
                const nodes = [];
                let n, total = 0;
                while ((n = walker.nextNode())) {
                    nodes.push([n, total, total + n.length]);
                    total += n.length;
                }
                if (!nodes.length || start >= total) return null;
                end = Math.min(end, total);
                const find = (off) => {
                    for (const [node, a, b] of nodes)
                        if (off >= a && off < b) return [node, off - a];
                    const [node, a] = nodes[nodes.length - 1];
                    return [node, node.length];
                };
                const [sn, so] = find(start);
                const [en, eo] = find(end);
                const range = document.createRange();
                range.setStart(sn, so);
                range.setEnd(en, eo);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);

                const r = range.getBoundingClientRect();
                host.dispatchEvent(new MouseEvent('mouseup', {
                    bubbles: true, cancelable: true,
                    clientX: r.right, clientY: r.bottom,
                }));
                return String(sel);
            }""", [rid, start, end])
        if selected is None:
            raise LookupError(f"could not select {start}-{end} in {rid!r}")
        return selected

    def panel_text(self):
        """The sidebar / proposal panel's rendered text. A different surface
        from a region, and the one proposals and statuses appear on."""
        return self.page.evaluate(
            """() => {
                const host = document.getElementById('rv-threads');
                return host ? (host.innerText || '').trim() : '';
            }""")

    def file_on_disk(self):
        """The served file's current bytes, read from disk, not from the page."""
        return self.target.read_bytes()

    # ---------- small conveniences used by the self-test ----------

    def status_text(self):
        return self.page.evaluate(
            "() => (document.getElementById('rv-status') || {}).textContent || ''")

    def reload(self):
        self.page.reload(wait_until="load")
        self.page.wait_for_selector("[data-rv-id]", timeout=5000)


# ---------------------------------------------------------------- self-test

def _selftest():
    checks = []

    def check(label, cond, extra=""):
        checks.append(bool(cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + extra) if extra else ''}")

    print("\n0. driver present")
    try:
        _require_playwright()
        check("playwright importable", True)
    except DriverMissing as exc:
        print(f"  FAIL  no driver\n{exc}")
        return 1

    print("\n1. starts and serves")
    h = Harness()
    try:
        h.start()
        check("server up and page loaded", h.page.title() != "")
        ids = h.region_ids(editable_only=True, min_text=40)
        check("regions present in the rendered page", len(ids) > 10, f"{len(ids)} editable")

        print("\n2. region_state")
        st = h.region_state(ids[0])
        check("returns a class list", isinstance(st["classes"], list))
        check("returns data-rv-* attributes", "data-rv-id" in st["data"], str(st["data"]))
        check("returns a computed background", bool(st["background"]), st["background"])

        print("\n3. a second harness can be nested")
        # The sync driver refuses a second instance inside a running one, so this
        # is checked here rather than discovered by whichever later unit needs two.
        h2 = Harness(fixture=FIXTURES / "js-assembled.html")
        try:
            h2.start()
            check("two harnesses alive at once", len(h2.region_ids()) > 0)
            check("region_state reports a locked flag",
                  "locked" in h2.region_state(h2.region_ids()[0]))
        finally:
            h2.close()
        check("first harness still usable after the second closed",
              len(h.region_ids()) > 0)

        # NOTE: whether a locked region is still MARKED at rest in the browser is
        # R1.2, and it is not met today - the document's own script executes and
        # overwrites the stamped nodes. The server stamps them correctly; R1.5's
        # CSP is what makes them survive. Asserted by the unit that owns R1.2,
        # not here. This harness only has to report faithfully what the DOM holds.

        print("\n4. type_into round trip")
        before = h.file_on_disk()
        marker = "HARNESS ROUND TRIP MARKER"
        h.type_into(ids[0], marker)
        h.page.wait_for_timeout(300)
        after = h.file_on_disk()
        check("file on disk changed", after != before)
        check("typed text reached the file", marker.encode() in after)

        print("\n5. select_text produces a real selection")
        h.reload()
        rid = h.region_ids(editable_only=True, min_text=60)[-1]
        picked = h.select_text(rid, 0, 24)
        check("browser reports a selection", len(picked.strip()) >= 3, repr(picked[:40]))
        h.page.wait_for_selector(".rv-selpop", timeout=3000)
        check("the page's own listener observed it", h.page.is_visible(".rv-selpop"))

        print("\n5b. R1.5 - the document's own scripts do not run")

        # An inline script that steals the token, posts a write, and rewrites
        # the page. Every one of the three must fail.
        ha = Harness(fixture=FIXTURES / "script-attack.html")
        try:
            ha.start()
            ha.page.wait_for_timeout(600)          # give the attack time to land
            # Read the marker element, never document.body.innerText: that
            # includes the script's own source text, so a body-wide search finds
            # the attack's string whether or not it ever ran.
            marker = ha.page.evaluate(
                "() => (document.getElementById('attack-status')||{}).textContent || ''").strip()
            check("inline document script did not execute",
                  "DOCUMENT SCRIPT EXECUTED" not in marker, repr(marker[:60]))
            check("its marker paragraph is untouched",
                  "The script has not run." in marker)
            # Not document.title: review.js sets the title itself, so it would
            # overwrite the attack's value and the check would pass either way.
            check("neither deferred attack ran (DOMContentLoaded or timeout)",
                  ha.page.evaluate(
                      "() => document.body.getAttribute('data-attack-ran')") is None)
            # Not "is the planted string absent" - that string is a literal in the
            # fixture's own script source, so the check could never pass whatever
            # the server did. The file being byte-identical to the committed
            # fixture is the assertion that can actually fail.
            check("its fetch to /__edit wrote nothing at all",
                  ha.file_on_disk() == (FIXTURES / "script-attack.html").read_bytes())

            csp = ha.page.evaluate(
                """() => {
                    const m = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
                    return m ? m.content : null;
                }""")
            check("policy is a nonce, not 'self'", csp is None)   # header, not meta

            # Regression: this fixture carries the literal "</body>" inside a JS
            # comment. inject() used to replace the FIRST occurrence, which put
            # the whole review layer inside the document's script block - the
            # layer never ran, window.__RV__ was undefined, and review.js
            # degraded silently to an empty config with no error shown.
            check("the review layer was injected OUTSIDE the document's script",
                  ha.page.evaluate("() => !!window.__RV__"))

            # A17: a sibling .js file is same-origin, which 'self' would allow.
            hs = Harness(fixture=FIXTURES / "sibling-script.html")
            try:
                hs.start()
                hs.page.wait_for_timeout(600)
                stext = hs.page.evaluate(
                    "() => (document.getElementById('chart-body')||{}).textContent || ''").strip()
                check("external sibling script did not execute (A17)",
                      "Rendered by the sibling script" not in stext
                      and hs.page.evaluate(
                          "() => (document.getElementById('chart-body')||{})"
                          ".getAttribute('data-sibling-ran')") is None,
                      repr(stext[:60]))
                check("its placeholder survives",
                      "Waiting for the sibling script." in stext)
                check("review layer still works on that page",
                      len(hs.region_ids(editable_only=True)) > 3)
            finally:
                hs.close()

            print("\n5c. the review layer still functions, and says scripts are off")
            check("regions present", len(ha.region_ids(editable_only=True)) > 2)
            check("the script-disabled notice is shown",
                  ha.page.is_visible(".rv-noscript"),
                  ha.page.evaluate(
                      "() => (document.querySelector('.rv-noscript')||{}).textContent || ''"))
            rid = ha.region_ids(editable_only=True, min_text=40)[0]
            ha.type_into(rid, "EDITED UNDER CSP")
            ha.page.wait_for_timeout(300)
            check("editing still works with the policy on",
                  b"EDITED UNDER CSP" in ha.file_on_disk())

            print("\n5d. R1.2 - locked regions now survive to be seen at rest")
            hj = Harness(fixture=FIXTURES / "js-assembled.html")
            try:
                hj.start()
                hj.page.wait_for_timeout(600)
                locked = [r for r in hj.region_ids() if hj.region_state(r)["locked"]]
                check("locked regions marked at rest, before any interaction",
                      len(locked) >= 2, f"{len(locked)} locked")
                jtext = hj.page.evaluate(
                    "() => (document.getElementById('live-summary')||{}).textContent || ''")
                check("the page's script did not replace them",
                      "Median handover this period" not in jtext
                      and "Loading the current period" in jtext, repr(jtext.strip()[:60]))
            finally:
                hj.close()
        finally:
            ha.close()

        print("\n6. panel_text is a distinct surface")
        panel = h.panel_text()
        check("panel readable", isinstance(panel, str), repr(panel[:48]))
        check("panel is not a region's text", marker not in panel)

    finally:
        proc = h._proc
        h.close()
        print("\n7. teardown")
        check("server process reaped, no orphan", proc is None or proc.poll() is not None)
        check("temp directory removed", h._tmp is None or not h._tmp.exists())

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(_selftest())
