---
title: Artefact Review v1 (HTML only) - Plan
type: feat
date: 2026-09-19
origin: SPEC.md
revised: 2026-09-19
---

# Artefact Review v1 (HTML only) - Plan

> **Revision note (19 Sep, second pass).** Reviewed by eight reviewers. Every correctness finding is
> applied. They all shared one shape: a correct principle applied to one instance with its sibling
> missed. The changelog is at the foot of this document. No unit was added or cut; the scope review
> found no inflation against `SPEC.md`, so the count stands at 15.

## Goal Capsule

**Objective:** someone who has never seen this repository can clone it, run its tests and get a
truthful answer, serve a document they care about, edit it themselves, and have an agent's rewrites
arrive as proposals they approve - with the tool's own claims about what it guarantees matching what
it does.

**Means:** harden the existing walking skeleton against the reviewed spec rather than rebuild it
(KTD1).

**Authority hierarchy:** `SPEC.md` is authoritative for behaviour. Where this plan and the spec
disagree, the spec wins and this plan is wrong. The six invariants are not negotiable by an
implementer; a unit that cannot be built without breaking one stops and reports.

**Stop conditions:**

- Any change that writes into the artefact outside an approved edit (breaks I1).
- Any change that re-serialises the document rather than writing a byte range (breaks I2).
- Any change that lets a proposal reach the file without a human action.
- Adding a second adapter, agent-specific packaging, auth, hosting, or structural editing. These are
  named non-goals.

**Execution profile:** single maintainer, no CI, no reviewers. Every unit must be verifiable by
running a command locally.

**Tail ownership:** the maintainer commits and publishes. No unit ends in a push.

---

## Product Contract

The full Product Contract is `SPEC.md` in this repository and is not restated here. This plan cites
its identifiers directly (I1-I6, R1.1-R7.2, A1-A17).

**Summary:** a local server that serves any HTML file with a review layer injected at serve time.
The human edits text in place; an agent proposes rewrites that reach the file only when approved.

**Problem frame:** the skeleton works and is honest about very little. Its test suites pass on a
clean clone having tested nothing, three of its stated guarantees are unmet, its write endpoints
accept requests from anywhere on the machine, and the watcher the whole agent story depends on does
not exist.

---

## Planning Contract

### Key technical decisions

**KTD1 - Harden, do not rewrite.** The adapter's inline-collapse region model produced no overlaps
and no shattering across eight artefacts. **Sample caveat:** those eight are HTML from one machine,
mostly produced by similar tooling, so the evidence is weaker than a count of eight suggests. U3's
hand-authored fixtures exist partly to widen it. If a fixture breaks the model rather than its
edges, KTD1 is wrong and the unit stops and reports.

**KTD2 - Anchor resolution returns a region, never a byte offset.** The anchor text is searched
*within each region's contents*; a match spanning a boundary counts as not found. This keeps an
approved proposal on the same write path as a human edit, so `html_doc.write()`'s editable check
still fires. Resolving to an offset would write into a locked region and defeat I5.

**KTD2a - Normalisation strips inline markup.** A region's contents are raw HTML, not text: the
adapter collapses inline elements into their parent, and **36% of regions in a measured real
artefact contain tags mid-sentence**. Normalisation therefore strips inline tags from the anchor and
from each region's contents before comparing, as well as collapsing whitespace and resolving
entities. Without this, every proposal against a sentence containing a link or emphasis orphans.

**KTD3 - Event suppression happens on read, never on write.** Every event is always appended to
`inbox.jsonl`, **carrying an author**. The watcher filters what it returns using `--as`.

**KTD4 - The trust boundary is the page, not the author string, and it does not cover
`/__propose`.** Write endpoints reachable from the browser require a per-session token plus an
origin check. `/__propose` is reachable from a shell with neither, because that is how an agent
takes its turn; it is guarded only by the loopback bind, which the threat model already accepts. A
proposal writes nothing to the document, so this is not a hole.

**KTD5 - Fixtures are committed, and a missing one fails - in both suites.**

**KTD6 - The browser harness is its own unit, built before anything that needs it.**

**KTD7 - Every write path is a controlled path.** A control added to `do_POST` does not cover
`server.py --approve`, which calls `store.apply_edit` directly from `main()`. Any unit adding
validation, logging or an event must cover both entry points or say why not.

### Assumptions

- Python 3.13 available locally. No CI to satisfy.
- No backwards compatibility burden: no external users, one commit of history.
- `.review/` sidecars are disposable.

### Sequencing

Nothing is verifiable until the harness is honest, so U1-U3 precede everything. Security follows,
because those are live defects. Then correctness, the watcher, lifecycle.

---

## Implementation Units

### Unit index

| U-ID | Title | Files touched | Depends on |
|---|---|---|---|
| U1 | Fixture corpus committed, missing fixture fails **in both suites** | `tests/probe.py`, `tests/e2e.py`, `tests/fixtures/` | - |
| U2 | Browser test harness | `tests/browser.py`, `requirements.txt` | U1 |
| U3 | Hand-authored fixtures | `tests/fixtures/`, `SPEC.md` | U1 |
| U4 | Origin and session-token checks, two endpoint classes | `server.py`, `lib/review.js` | U1 |
| U5 | Document script untrusted (nonce CSP) | `server.py`, `lib/review.js` | U4, U2 |
| U6 | Payload validation on **both** write paths | `server.py`, `adapters/html_doc.py` | U1 |
| U7 | Stable region identity | `adapters/html_doc.py` | U1 |
| U8 | Text anchoring, resolved to a region | `server.py`, `adapters/html_doc.py` | U3 |
| U9 | Orphaned, ambiguous and re-place interaction | `lib/review.js`, `server.py` | U8, U2 |
| U10 | Events always written, with an author, on every path | `server.py` | U1 |
| U11 | `watch.py` | `watch.py` | U10 |
| U12 | External modification detection | `server.py`, `adapters/html_doc.py` | U1 |
| U13 | The cooperative gate, tested with a seam | `server.py`, `tests/e2e.py` | U12 |
| U14 | Lifecycle: detach, idle clock, loopback | `server.py`, `lib/review.js` | U1 |
| U15 | Locked regions marked at rest | `lib/review.js`, `server.py` | U3 |

---

### U1. Fixture corpus committed, missing fixture fails in both suites

**Goal:** both test suites tell the truth on a machine that is not the author's.

**Requirements:** the Test fixtures section of `SPEC.md`; I3, I4.

**Files:** `tests/probe.py`, `tests/e2e.py`, `tests/fixtures/`

**Approach:** copy the eight artefacts `probe.py` reaches for by absolute path into
`tests/fixtures/`, and point `TARGETS` at repo-relative paths. Replace its missing-fixture skip
(`probe.py:32-34`) with a recorded failure, and assert a non-zero parsed count before printing the
verdict. Record each fixture's expected region-count bounds so I3's "does not shatter" has a real
assertion.

**Do the same to `tests/e2e.py`.** Its `SRC` at line 15 is
`Path.home() / "Claude/03-Projects/..."`, and line 40 copies from it. Eleven units verify with
`e2e.py`, so leaving it unfixed means every one of those commands dies at `shutil.copy` on a clean
clone, before a single assertion runs. Repoint `SRC` at a committed fixture and make an unresolvable
one a named failure there too.

**Test scenarios:**
- All fixtures present: both suites pass, exit 0.
- One fixture deleted: the suite that uses it fails, names it, exit 1. **Assert this for `e2e.py`
  as well as `probe.py`.**
- `TARGETS` emptied: fails on the zero-parsed assertion rather than reporting success.
- A fixture whose region count exceeds its recorded bound: fails.

**Verification:** `python3 tests/probe.py` **and** `python3 tests/e2e.py`, run from a fresh clone in
a directory with no sibling vault. Both exit 0.

---

### U2. Browser test harness

**Goal:** claims about the rendered page can be tested rather than asserted.

**Requirements:** the Verification column of the spec's acceptance table; A2, A5, A6, A11, A13, A16,
A17.

**Files:** `tests/browser.py`, `requirements.txt`

**Approach:** a harness that starts the server on an ephemeral port against a fixture, drives a
headless browser, and exposes the helpers its consumers need. **Name the driver and commit the
dependency file**: the repo currently has no `requirements.txt` and no `pyproject.toml`, so a
stranger cannot install what the harness needs, and gate 1 fails by design on their machine. The
gate that exists to protect strangers must be one a stranger can pass.

Four helpers, all named here so U9 and U15 are not redesigning the harness while using it:

| Helper | Returns / does |
|---|---|
| `region_state(id)` | the element's class list, its `data-rv-*` attributes, and its computed `background-color` |
| `type_into(id, text)` | drives a real edit in a contenteditable region |
| `select_text(id, start, end)` | drives a real **selection** across rendered HTML, not a click. Needed by U9's re-place |
| `panel_text()` | the proposal panel's rendered text, a different surface from a region's |
| `file_on_disk()` | the served file's current bytes |

**Test scenarios:**
- Starts and stops the server cleanly, leaving no orphan process.
- Round trip: type into a region, assert the file changed on disk.
- `select_text` produces a real selection the page's own listeners observe.
- Fails loudly when no driver is installed, rather than silently skipping.

**Verification:** `python3 tests/browser.py` passes its self-test, after `pip install -r
requirements.txt` in a fresh clone.

---

### U3. Hand-authored fixtures

**Goal:** the invariants that have never been exercised become testable.

**Requirements:** the Test fixtures table in `SPEC.md`; I5, R1.2, R1.3, R1.4, R2.4, R3.3.

**Files:** `tests/fixtures/`, `SPEC.md`

**Approach:** author four fixtures by hand. The detection rule for a script-built region **already
exists in the adapter** (`html_doc.py:194-204`) and is now written into `SPEC.md` beside I5: a
region is locked when an ancestor's `id` appears in the locked set, which is built by scanning each
`<script>` for a DOM-write pattern and collecting quoted literals in it that match a document `id`.
Author `js-assembled.html` against that rule rather than guessing. The rule is static, so R1.5's
policy does not affect it.

| Fixture | Must contain | Unblocks |
|---|---|---|
| `js-assembled.html` | a `<script>` with a DOM-write pattern and a quoted literal matching an `id` on an element in the page | I5, R1.2, R2.4, A3, A15 |
| `div-only.html` | a visual artefact with no semantic prose tags | I3, R1.3 |
| `malformed/` | **four named documents**, not a vague class: `truncated.html` (cut mid-tag), `unclosed.html` (unclosed block elements), `deep-nest.html` (nesting past any sane depth), `bad-entities.html` (malformed entity references) | R1.4, A10 |
| `repeated-prose.html` | the same sentence appearing in two separate regions | R3.3, A6 |
| `sibling-script.html` | a page referencing its own external `.js` file | A17, U5 |

**Test scenarios:**
- `js-assembled.html` produces at least one locked region. **The current suite finds zero locked
  regions across every fixture, so this is the first time the locked path executes at all.**
- `div-only.html` produces a non-zero region count.
- `repeated-prose.html` produces two regions containing identical text.
- Each `malformed/` document parses without raising. *(Serving them read-only is asserted in U12,
  which has the server; U3 touches no product code and cannot test it.)*

**Verification:** `python3 tests/probe.py` reports a non-zero lock count for the first time.

---

### U4. Origin and session-token checks, two endpoint classes

**Goal:** a web page in another tab cannot edit the document, and the agent can still take its turn.

**Requirements:** R2.5. Threat model, "not accepted" clause 1. KTD4.

**Files:** `server.py`, `lib/review.js`

**Approach:** mint a random token at startup and make it available to the page. Endpoints split into
two classes, and the rule is written down so a later reader can tell which are guarded on purpose:

| Class | Endpoints | Guarded by |
|---|---|---|
| Browser-only | `/__edit`, `/__comment`, `/__reply`, `/__approve`, `/__reject`, `/__resolve`, `/__delete` | `Origin` / `Sec-Fetch-Site` check **and** the session token, checked before any parsing |
| Agent-reachable | `/__propose` | the loopback bind only |

`/__propose` is exempt because the agent posts to it from a shell with no browser, no `Origin` and
no token - the agent credential is deliberately deferred. A proposal writes nothing to the document,
so the exemption costs nothing. The `author` field stays a log label, never a credential.

**Test scenarios:**
- A POST to a browser-only endpoint with no `Origin` and no token: 403, file unchanged, sidecar
  unchanged.
- A POST with a foreign `Origin` and a valid token: 403.
- A POST from the served page: succeeds.
- **A tokenless, `Origin`-less POST to `/__propose` from a separate process: succeeds and stores a
  proposal.** This asserts the exemption rather than leaving it to be discovered.
- The token is not written anywhere under `.review/`.
- All seven browser-only endpoints are covered, not just `/__edit`.

**Verification:** `python3 tests/e2e.py`, covering A12.

---

### U5. Document script untrusted (nonce CSP)

**Goal:** serving a document does not hand that document the pen.

**Requirements:** R1.5, A17. Threat model, "not accepted" clause 2.

**Files:** `server.py`, `lib/review.js`

**Depends on:** U4, U2.

**Approach:** **the mechanism is a per-response nonce, and `script-src 'self'` is not sufficient.**
Two reviewers converged here from different directions:

- `'self'` admits the artefact's own sibling script files, which are same-origin. They would keep
  running and could read the token out of the page and post a valid same-origin write.
- A policy with no nonce blocks the review layer's own inline bootstrap. `server.py:128` injects
  `<script>window.__RV__={payload};</script>`, and `review.js:16` reads
  `window.__RV__ || { units: [], locked: [] }` - so it **degrades silently to an empty config**
  rather than erroring. The page would render dead with zero regions and no error.

Mint a fresh nonce per response and serve
`Content-Security-Policy: script-src 'nonce-<n>'; object-src 'none'; base-uri 'none'`, applying the
nonce to the injected layer's script tags and to the tag carrying the session token. Document
script, inline or sibling, carries no nonce and does not run.

**Add a page-level notice in the review chrome** stating that the document's own scripts are
disabled under review. Without it, a dashboard silently stops filtering and the reviewer has no way
to know whether that is the tool or the artefact.

**Test scenarios:**
- `js-assembled.html`: its script does not run, and its regions are locked rather than populated.
- **`sibling-script.html`: the external script does not execute (A17).** This is the case `'self'`
  would have allowed.
- A fixture whose script attempts `fetch('/__edit')`: no write occurs.
- The review layer functions fully: regions present, editing works, comments work.
- The served page shows the script-disabled notice.

**Verification:** `python3 tests/browser.py` covering A13 and A17.

---

### U6. Payload validation on both write paths

**Goal:** an edit cannot introduce markup the spec says edits do not carry, whichever door it came
through.

**Requirements:** R2.6, and the "text within an existing region only" non-goal. KTD7.

**Files:** `server.py`, `adapters/html_doc.py`

**Approach:** validate an edit payload against the inline elements already present in the target
region. Reject `script`, `style`, `iframe`, `object` and any event-handler attribute, and refuse the
write with a reason.

**Put the validation in `apply_edit`, not in `do_POST`.** `server.py:337-348`'s `--approve` verb
calls `store.apply_edit` directly from `main()` and never touches a POST handler, so validation
added at the HTTP layer would leave the CLI verb writing unvalidated content into the file.

**Test scenarios:**
- A payload containing `<script>`: refused with a reason, file unchanged.
- A payload containing `onclick=`: refused.
- A payload containing an `<em>` already present in the region: accepted.
- An approved **proposal** carrying a script tag: refused on the same path.
- **The same proposal approved via `server.py --approve <cid>`: refused identically.**

**Verification:** `python3 tests/e2e.py` covering A14.

---

### U7. Stable region identity

**Goal:** a reference to a region survives an edit to a different region.

**Requirements:** R1.1, I1.

**Files:** `adapters/html_doc.py`

**Approach:** replace the positional `u{i:03d}` id (`html_doc.py:223`) with an identity derived from
the region's content and position in the element tree. Ids remain opaque; nothing outside the
adapter should parse them. Check `.review/` sidecar contents and `lib/review.js` for anything that
persists or parses the old form before changing it.

**Not a blocker for U8.** U8 re-searches the region set returned by the current parse, so it needs
region objects, not stable ids. U7 protects comment and lock persistence across sessions, which is
its own value.

**Test scenarios:**
- Edit region A so its byte length changes; a stored reference to region B still resolves to the
  same content.
- Split a region by editing it; ids of later regions are unchanged.
- Two regions with identical text in different tree positions get different ids.
- Re-parsing an unchanged document produces identical ids.
- Existing `.review/` sidecars referencing old ids degrade visibly rather than silently mis-resolving.

**Verification:** `python3 tests/probe.py`, with a new invariant assertion for I1.

---

### U8. Text anchoring, resolved to a region

**Goal:** an approved proposal lands on the words it was written against, or on nothing.

**Requirements:** R3.2, R3.3, R3.4. The correctness fix the spec singles out.

**Files:** `server.py`, `adapters/html_doc.py`

**Depends on:** U3.

**Approach:** at propose time, capture the region's text **with inline markup stripped**, whitespace
collapsed and entities resolved (KTD2a). At approve time, apply the identical normalisation to each
region's contents before searching. A region's raw contents contain tags mid-sentence in roughly a
third of real cases, so comparing a plain-text anchor against raw HTML would orphan every proposal
touching a link or an emphasis.

Per KTD2 the result is a **region**; a match spanning a region boundary counts as not found. Apply
through the existing region write path so the editable check still fires. Three outcomes: one match
applies, more than one is ambiguous, zero is orphaned. Neither of the latter writes, neither is
discarded.

**Test scenarios:**
- Anchor found in one region: applies, file changed in that region only.
- **Anchor in a region containing `<em>`, `<a>` or `<small>` mid-sentence: matches and applies.**
  This is the case that fails without KTD2a.
- Anchor found in two regions (`repeated-prose.html`): ambiguous, nothing written.
- Anchor deleted from the document: orphaned, nothing written, original anchor text retained.
- **Anchor resolving into a locked region: refused with the lock reason, nothing written.**
- Whitespace and entity differences between propose and approve still match.

**Verification:** `python3 tests/e2e.py` covering A4, A5, A6.

---

### U9. Orphaned, ambiguous and re-place interaction

**Goal:** a detached proposal is visible and recoverable rather than a dead record.

**Requirements:** R3.4, R3.5; A11, A16.

**Files:** `lib/review.js`, `server.py`

**Depends on:** U8, U2.

**Approach:** present an orphaned or ambiguous proposal with its original anchor text shown.
Ambiguous additionally lists the candidate regions. Two actions: bin it, or re-place it.

**Re-place needs a mode flag, because the gesture is already taken.** `review.js:219-224` attaches a
global `mouseup` listener that opens a "Comment on selection" popup for any selection of three or
more characters whenever the user is not mid-edit. Without suppression, selecting text to re-place a
proposal opens the comment box instead. Add a re-place flag alongside the existing `editing`
variable; while set, that handler hands the selection to the re-place flow instead of opening the
popup; cleared on confirm, cancel or Escape.

The interaction, specified rather than left to be invented:

1. Clicking "re-place" on the card enters re-place mode and shows a persistent
   *"re-placing - select the new text"* state in the review chrome.
2. Selecting text raises an explicit confirm control (*"Use this text"*), mirroring the existing
   `rv-selpop` pattern. The raw selection alone never commits.
3. A selection crossing a region boundary is rejected at the confirm step with a reason, and mode
   stays active.
4. Escape or cancel returns to the card unchanged, with the proposal intact.

**Test scenarios:**
- Orphaned proposal renders with its original anchor text visible.
- Entering re-place mode suppresses the comment popup on selection.
- Re-place: select, confirm, the proposal applies there.
- Selection crossing a region boundary: rejected with a reason, mode stays active.
- Re-place onto a locked region: refused with the lock reason.
- Escape: returns to the card, proposal intact, mode cleared.
- Bin: proposal removed, event recorded.
- **In-progress typed content survives an external-modification refusal (A16).**

**Verification:** `python3 tests/browser.py` covering A5, A6, A11, A16.

---

### U10. Events always written, with an author, on every path

**Goal:** the stream is a complete record, which is what every later reader depends on.

**Requirements:** R4.4, R4.6; KTD3, KTD7.

**Files:** `server.py`

**Approach:** three changes, not one.

1. Remove the `who != "Claude"` condition from **both** `/__edit` (`server.py:216`) and `/__reply`
   (`server.py:266`). No filtering at write time.
2. **Add an `author` field to every `append_inbox` call.** Only `edit` carries one today;
   `comment` (`:227`), `approved` (`:252`), `rejected` (`:260`) and `reply` (`:266`) do not. U11's
   `--as` filter has nothing to match on without it, so an agent would wake on its own approvals
   forever - the loop the suppression existed to prevent.
3. **Make `server.py --approve` append an `approved` event.** It writes to the file today and
   records nothing in the stream, so "the stream is the record" is false for it (KTD7).

**Test scenarios:**
- An edit authored `Claude` appends an `edit` event.
- A reply authored `Claude` appends a `reply` event.
- **Every line in `inbox.jsonl` after a full loop carries a non-empty `author`.**
- `server.py --approve <cid>` appends an `approved` event.
- Event ordering preserved; the file stays append-only.
- No event type is filtered by author at write time anywhere in the file.

**Verification:** `python3 tests/e2e.py`, asserting the stream contains an agent-authored edit and
that no event lacks an author.

---

### U11. `watch.py`

**Goal:** an agent can watch for comments, edits and replies by running a command, whatever agent it
is.

**Requirements:** R4.1 to R4.6; the "agent's turn" section of `SPEC.md`.

**Files:** `watch.py`

**Depends on:** U10.

**Approach:** `watch.py <file.html> --as <identity> [--since <cursor>] [--timeout <seconds>]`. It
blocks on `inbox.jsonl` until events after the cursor appear, prints one JSON object per line, and
exits. Every response carries a cursor, derived from position in the stream rather than from
server-process state so it survives a restart. Events authored by `--as` are filtered on read.

**Test scenarios:**
- Blocks, then returns when an event is appended.
- **Each output line parses as JSON and carries the same keys as its `inbox.jsonl` line (R4.2).**
  This is the requirement an agent integration breaks on and it had no check.
- Cursor round trip: three events occur while disconnected; reconnecting returns exactly those
  three, once.
- Cursor survives a server restart.
- `--as Claude` does not return Claude's own events, across **all five event types**, and they are
  present in the file.
- Timeout returns empty plus the unchanged cursor.

**Verification:** `python3 tests/e2e.py` covering A7 and A8, using two processes.

---

### U12. External modification detection

**Goal:** a byte-range write never lands on content it was not computed against.

**Requirements:** R7.1, R7.2, R1.4; A9, A10.

**Files:** `server.py`, `adapters/html_doc.py`

**Approach:** record the file's modification time and size, or a hash, when read. Re-check before
any write. On mismatch, refuse, tell the human the file changed underneath, and re-read. Content the
human has typed and not yet saved survives the re-read; **its browser-side assertion lives in U9
(A16)**, because `e2e.py` has no DOM.

Also serve the `malformed/` corpus read-only with a reason, which U3 could not test.

**Test scenarios:**
- File modified by another process mid-serve: the next approval is refused with a clear message.
- After re-read, a subsequent edit succeeds against the new content.
- No mismatch: writes proceed unchanged.
- Each `malformed/` document is served read-only with a reason and does not raise (A10).

**Verification:** `python3 tests/e2e.py` covering A9 and A10.

---

### U13. The cooperative gate, tested with a seam

**Goal:** the tool's headline claim has a test that can fail, proved by a command rather than by
someone's memory.

**Requirements:** I6.

**Files:** `server.py`, `tests/e2e.py`

**Depends on:** U12.

**Approach:** I6's old test - "there is no code path from `/__propose` to a write" - is true and
passes while three bypasses exist, so it is replaced. The new test asserts what the cooperative gate
actually guarantees: an agent that writes the file directly is **detected and surfaced**.

**Add a test-only seam.** The previous version of this unit said the test "must be demonstrated to
fail when U12 is reverted", which named no command, left no artefact, and would have to be
re-performed by hand at every publish - the same shape as a suite that reports success having tested
nothing. Instead: an environment variable `RV_GATE_DISABLED=1` skips the pre-write freshness
re-check, and a committed test runs the I6 case twice, asserting failure with the seam on and
success with it off. Gate 3 becomes one command with an exit code. The seam must not be reachable
from any HTTP request.

**Note on what this proves.** With the seam off, this test and U12's first scenario assert the same
behaviour. That is intended: under the cooperative framing, detection *is* the guarantee. The test
proves the detection is live, not that the gate is unbypassable - which it is not, by design.

**Dropped dependency:** U11. No scenario here exercises the watcher, so waiting on it delayed the
spec's headline test for nothing.

**Test scenarios:**
- An agent writes the file directly while served; the next approval is refused and the human is told.
- With `RV_GATE_DISABLED=1`, that test fails.
- A proposal never approved never appears in the file.
- A rejected proposal never appears in the file, and the reason is recorded.

**Verification:** `python3 tests/e2e.py`, plus `RV_GATE_DISABLED=1 python3 tests/e2e.py` exiting
non-zero.

---

### U14. Lifecycle: detach, idle clock, loopback

**Goal:** no forgotten, write-capable server outlives the session that started it.

**Requirements:** R6.3, R6.4, R6.5, R6.6, R6.7.

**Files:** `server.py`, `lib/review.js`

**Approach:** make `--detach` genuinely detach, so the launching shell returns immediately; its
purpose is an agent starting the server unattended. Stop the client's `/__version` poll
(`review.js:312-322`) resetting the idle clock: every response currently sets `_last_hit`
(`server.py:144`), and detaching skips parent-death watching (`server.py:361`), so one open tab
keeps a detached server alive forever. The idle timer counts human interaction only. Give a detached
server an absolute lifetime cap. Make loopback-only binding an asserted requirement with no flag to
change it.

**Test scenarios:**
- `--detach` returns the shell immediately; the server survives it.
- A page polling with no human interaction does not prevent idle shutdown.
- A detached server exits at its lifetime cap despite continuous polling.
- The listening socket is `127.0.0.1` and no CLI flag changes it.
- Port in use: reported clearly, naming the file the existing server serves (R6.4).

**Verification:** `python3 tests/e2e.py`, plus a manual detach check.

---

### U15. Locked regions marked at rest

**Goal:** a human can see what they cannot edit without probing it.

**Requirements:** R1.2, I5; A15.

**Files:** `lib/review.js`, `server.py`

**Depends on:** U3.

**Approach:** a locked region is visually distinguishable on page load, not only when an edit is
attempted, and its reason is reachable without trying to edit. The visual treatment is implementer
latitude; that it exists at rest is not.

**Dropped dependency:** U2. The automated half is a plain HTTP assertion - the units endpoint
returns `editable: false` with a `reason` - which belongs in `e2e.py`. The at-rest presentation is
manual by design per A15. Neither needs the browser harness.

**Test scenarios:**
- The units endpoint returns `editable: false` with a non-empty `reason` for every locked region.
- An edit attempt is refused with that reason.
- *(manual)* `js-assembled.html` on load: locked regions distinguishable before any interaction, and
  the reason reachable without attempting an edit.

**Verification:** `python3 tests/e2e.py` for the server half; manual check for A15.

---

## Verification Contract

| Command | Covers |
|---|---|
| `python3 tests/probe.py` | I1, I3, I4 over the committed corpus. Fails on a missing fixture |
| `python3 tests/e2e.py` | server and adapter behaviour: A1, A3, A4, A7, A8, A9, A10, A12, A14, I6 |
| `RV_GATE_DISABLED=1 python3 tests/e2e.py` | must exit non-zero. This is gate 3 |
| `python3 tests/browser.py` | rendered-page behaviour: A2, A5, A6, A11, A13, A16, A17 |
| manual | A15 only, and the `--detach` shell check |

**Quality gates before publishing:**

1. **In a fresh clone, outside the author's home directory**, after `pip install -r
   requirements.txt`, all three suites pass. Both `probe.py` and `e2e.py` must be repointed for this
   to be possible.
2. `python3 tests/probe.py` reports a **non-zero locked-region count**. Zero means U3's fixture is
   absent or the locked path is still dead.
3. `RV_GATE_DISABLED=1 python3 tests/e2e.py` exits non-zero.
4. No acceptance row is marked `automated` while no automated check exists for it.

**Deliberately untested in v1:** R5.2, R5.3 and R5.4 (the sidecar's `comments.json`, `edits.md` and
the never-poll rule). Only R5.1's append-only property is asserted, inside U10. These are internal
file-shape requirements with no failure mode a user would notice before a test would; stated here so
their absence reads as a decision rather than an oversight.

---

## Definition of Done

**Global:**

- Every requirement in the spec's "Open against this spec" list is either implemented or explicitly
  moved to a deferred section with a reason. None is silently reworded to match the code.
- Every acceptance example A1-A17 has a check at its stated verification level, or is marked
  `manual` deliberately.
- The spec's claims match the implementation. Where a unit changed what is true, `SPEC.md` is updated
  in the same commit.
- Dead ends removed: no abandoned or experimental code left in the diff.
- **The README's run *and test* instructions work on a clean clone**, including installing
  dependencies.

**Per unit:** its test scenarios pass, its verification command is green, and no invariant regressed
elsewhere - the full suite runs, not just the touched file.

**Not done until:** the fresh-clone gate passes. Everything else can look finished while that fails,
which is the failure mode this plan exists to close.

---

## Changelog, second pass

Every entry is a correctness fix found by review and verified against the code.

| # | Change | Why |
|---|---|---|
| 1 | U1 now fixes `tests/e2e.py` as well as `probe.py` | `e2e.py:15` also reads from the author's home. Eleven units verify with it, so the headline gate was unpassable |
| 2 | U4 splits endpoints into two classes | Guarding all seven would have 403'd `/__propose`, breaking the agent's turn - the integration surface |
| 3 | U5's mechanism is a per-response nonce | `'self'` admits sibling scripts; no-nonce blocks the layer's own bootstrap, which degrades silently to an empty config |
| 4 | KTD2a added: normalisation strips inline markup | 36% of regions in a measured artefact carry tags mid-sentence; a plain-text anchor would orphan all of them |
| 5 | U6 validates in `apply_edit`, not `do_POST` | `--approve` calls `apply_edit` directly from `main()` and would have bypassed validation |
| 6 | U10 adds an `author` to every event, and an event to `--approve` | Four of five event types carry no author, so U11's `--as` filter had nothing to match |
| 7 | U13 gains the `RV_GATE_DISABLED` seam | The old "demonstrate it fails" gate named no command and left no artefact |
| 8 | U2 names a driver, commits `requirements.txt`, and lists four helpers | No dependency file existed, so the stranger-protecting gate was one a stranger could not pass |
| 9 | U9 specifies re-place mode, confirm, cancel and mis-select | `review.js:219` already claims the selection gesture for the comment popup |
| 10 | U3 documents the lock rule and enumerates `malformed/` | The rule existed in code but nowhere in prose; "hostile-shaped" was unenumerable |
| 11 | U11 gains an R4.2 scenario | The output-shape requirement an integration breaks on had no check |
| 12 | U8 drops U7; U13 drops U11; U15 drops U2 | Three dependency edges that were not load-bearing, each delaying a higher-priority unit |
| 13 | R2.7 moved to U9 as A16; `malformed/` serving moved to U12 | Both had scenarios their own unit's verification command could not run |
| 14 | KTD1 carries a sample caveat | Eight artefacts from one machine made by similar tooling is weaker evidence than "eight" suggests |
| 15 | R5.2-R5.4 named as deliberately untested | They had no scenario and the plan did not say that was a decision |

**Rejected:** one reviewer reported that acceptance identifiers A1-A15 do not exist in `SPEC.md`,
at confidence 100. They do - `grep -c "^| A[0-9]"` returns 17. Not applied.
