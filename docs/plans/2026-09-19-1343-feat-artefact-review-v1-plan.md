---
title: Artefact Review v1 (HTML only) - Plan
type: feat
date: 2026-09-19
origin: SPEC.md
---

# Artefact Review v1 (HTML only) - Plan

## Goal Capsule

**Objective:** someone who has never seen this repository can clone it, run its tests and get a
truthful answer, serve a document they care about, edit it themselves, and have an agent's rewrites
arrive as proposals they approve - with the tool's own claims about what it guarantees matching what
it does.

**Means:** harden the existing walking skeleton against the reviewed spec rather than rebuild it.
The region model, the sidecar and the approval flow stay; anchoring, the trust surface, the watcher
and the test harness are the work (KTD1).

**Authority hierarchy:** `SPEC.md` is authoritative for behaviour. Where this plan and the spec
disagree, the spec wins and this plan is wrong. The six invariants are not negotiable by an
implementer; a unit that cannot be built without breaking one stops and reports.

**Stop conditions:**

- Any change that writes into the artefact outside an approved edit (breaks I1).
- Any change that re-serialises the document rather than writing a byte range (breaks I2).
- Any change that lets a proposal reach the file without a human action (breaks the cooperative
  gate's one real guarantee).
- Adding a second adapter, agent-specific packaging, auth, hosting, or structural editing. These are
  named non-goals; a unit that seems to need one has misread its requirement.

**Execution profile:** single maintainer, no CI, no reviewers. Every unit must be verifiable by
running a command locally.

**Tail ownership:** the maintainer commits and publishes. No unit ends in a push.

---

## Product Contract

The full Product Contract is `SPEC.md` in this repository and is not restated here. This plan cites
its identifiers directly (I1-I6, R1.1-R7.2, A1-A15).

**Summary:** a local server that serves any HTML file with a review layer injected at serve time.
The human edits text in place; an agent proposes rewrites that reach the file only when approved.

**Problem frame:** the skeleton works and is honest about very little. Its test suite passes on a
clean clone having tested nothing, three of its stated guarantees are unmet, its write endpoints
accept requests from anywhere on the machine, and the watcher the whole agent story depends on does
not exist.

**Outstanding questions** (carried from the spec, none blocking this plan):

- Does blocking document script (R1.5) cost too much interactivity to be worth it?
- Should an ambiguous anchor orphan, or resolve to nearest-original-position?

---

## Planning Contract

### Key technical decisions

**KTD1 - Harden, do not rewrite.** The adapter's inline-collapse region model is sound: it was
measured across eight real artefacts and produced no overlaps and no shattering. The defects are at
its edges, not in it.

**KTD2 - Anchor resolution returns a region, never a byte offset.** The anchor text is searched
*within each region's contents*; a match spanning a boundary counts as not found. This is what keeps
an approved proposal on the same write path as a human edit, so `html_doc.write()`'s editable check
still fires. Resolving to an offset would write into a locked region and defeat I5.

**KTD3 - Event suppression happens on read, never on write.** Every event is always appended to
`inbox.jsonl`. The watcher filters what it returns using `--as`. The current code suppresses the
append itself, which makes the stream an incomplete record for every reader, not just the actor.

**KTD4 - The trust boundary is the page, not the author string.** A per-session token is minted at
startup and injected into the served page. Write endpoints require it plus an `Origin` /
`Sec-Fetch-Site` check. The `author` field stays a label for the log, never a credential. This closes
cross-site and document-script access without pretending to close the agent's own file tools.

**KTD5 - Fixtures are committed, and a missing one fails.** The corpus moves into `tests/fixtures/`.
`probe.py` treats an unresolvable fixture as a failure and asserts a non-zero parsed count before
printing its verdict.

**KTD6 - The browser harness is its own unit, built before anything that needs it.** Seven
acceptance examples are claims about a rendered page and neither existing suite loads a DOM. Burying
that harness inside a feature unit is how those rows stay permanently unverified.

### Assumptions

- Python 3.13 and a headless-browser driver are available locally. No CI to satisfy.
- No backwards compatibility burden: there are no external users and one commit of history.
- `.review/` sidecars are disposable. A migration path for existing sidecars is not needed.

### Sequencing

Units run in ID order unless a dependency says otherwise. The ordering principle: **nothing is
verifiable until the harness is honest**, so U1-U3 precede everything. Security follows, because
those are live defects rather than future work. Correctness, then the watcher, then lifecycle.

---

## Implementation Units

### Unit index

| U-ID | Title | Files touched | Depends on |
|---|---|---|---|
| U1 | Fixture corpus committed, missing fixture fails | `tests/probe.py`, `tests/fixtures/` | - |
| U2 | Browser test harness | `tests/browser.py`, `tests/fixtures/` | U1 |
| U3 | Hand-authored fixtures | `tests/fixtures/` | U1 |
| U4 | Origin and session-token checks on write endpoints | `server.py`, `lib/review.js` | U1 |
| U5 | Document script untrusted (CSP) | `server.py` | U4, U2 |
| U6 | Server-side payload validation | `server.py`, `adapters/html_doc.py` | U1 |
| U7 | Stable region identity | `adapters/html_doc.py` | U1 |
| U8 | Text anchoring, resolved to a region | `server.py`, `adapters/html_doc.py` | U3, U7 |
| U9 | Orphaned, ambiguous and re-place interaction | `lib/review.js`, `server.py` | U8, U2 |
| U10 | Events always written, filtered on read | `server.py` | U1 |
| U11 | `watch.py` | `watch.py`, `server.py` | U10 |
| U12 | External modification detection | `server.py`, `adapters/html_doc.py` | U1 |
| U13 | The cooperative gate, tested | `tests/e2e.py`, `SPEC.md` | U12, U11 |
| U14 | Lifecycle: detach, idle clock, loopback | `server.py`, `lib/review.js` | U1 |
| U15 | Locked regions marked at rest | `lib/review.js`, `server.py` | U2, U3 |

---

### U1. Fixture corpus committed, missing fixture fails

**Goal:** the test suite tells the truth on a machine that is not the author's.

**Requirements:** the Test fixtures section of `SPEC.md`; I3, I4.

**Files:** `tests/probe.py`, `tests/fixtures/`

**Approach:** copy the eight artefacts `probe.py` currently reaches for by absolute path into
`tests/fixtures/` and point `TARGETS` at repo-relative paths. Replace the `if not path.exists():
print(MISSING); continue` skip with a `fails.append(...)`. Before printing the verdict, assert the
count of fixtures actually parsed is non-zero. Record each fixture's expected region-count bounds so
I3's "does not shatter" has a real assertion rather than a docstring.

**Test scenarios:**
- All fixtures present: suite passes, exit 0.
- One fixture deleted: suite fails, names it, exit 1.
- `TARGETS` emptied: suite fails on the zero-parsed assertion rather than reporting success.
- A fixture whose region count exceeds its recorded bound: fails.

**Verification:** `python3 tests/probe.py` in a fresh clone of the repo, in a directory with no
sibling vault. Exit 0 and a non-zero fixture count.

---

### U2. Browser test harness

**Goal:** claims about the rendered page can be tested rather than asserted.

**Requirements:** the Verification column of the acceptance table; A2, A5, A6, A11, A13, A15.

**Files:** `tests/browser.py`, `tests/fixtures/`

**Approach:** a harness that starts the server on an ephemeral port against a fixture, drives a
headless browser to the page, and exposes helpers to read a region's rendered state, type into one,
and read the file from disk afterwards. No product code changes. It exists so later units have
somewhere to put their tests.

**Test scenarios:**
- Harness starts and stops the server cleanly, leaving no orphan process.
- A trivial round trip: type into a region, assert the file changed on disk.
- Harness fails loudly when no browser driver is installed rather than silently skipping.

**Verification:** `python3 tests/browser.py` passes its own self-test. Every `automated-browser` row
in the spec's acceptance table has somewhere to live.

---

### U3. Hand-authored fixtures

**Goal:** the invariants that have never been exercised become testable.

**Requirements:** the Test fixtures table in `SPEC.md`; I5, R1.2, R1.3, R1.4, R2.4, R3.3.

**Files:** `tests/fixtures/`

**Approach:** author four fixtures by hand. None can be derived from an existing artefact.

| Fixture | Must contain | Unblocks |
|---|---|---|
| `js-assembled.html` | content injected by its own script, so the text is not in the file | I5, R1.2, R2.4, A3, A15 |
| `div-only.html` | a visual artefact with no semantic prose tags | I3, R1.3 |
| `malformed/` | truncated, unclosed, and hostile-shaped documents | R1.4, A10 |
| `repeated-prose.html` | the same sentence appearing in two separate regions | R3.3, A6 |

**Test scenarios:**
- `js-assembled.html` produces at least one locked region. **The current suite finds zero locked
  regions across every fixture, so this is the first time the locked path executes at all.**
- `div-only.html` produces a non-zero region count.
- Each malformed document is served read-only with a reason, and does not raise.
- `repeated-prose.html` produces two regions containing identical text.

**Verification:** `python3 tests/probe.py` reports a non-zero lock count for the first time.

---

### U4. Origin and session-token checks on write endpoints

**Goal:** a web page in another tab cannot edit the document.

**Requirements:** R2.5. Threat model, "not accepted" clause 1.

**Files:** `server.py`, `lib/review.js`

**Approach:** mint a random token at startup. Inject it into the page beside `window.__RV__`. Every
POST handler checks `Origin` / `Sec-Fetch-Site` against the server's own origin **and** the token,
before any parsing. Failure returns 403 with no write and no sidecar mutation. Per KTD4 the `author`
field is untouched: it remains a log label.

**Test scenarios:**
- A POST with no `Origin` and no token: 403, file unchanged, sidecar unchanged.
- A POST with a foreign `Origin` and a valid token: 403.
- A POST from the served page: succeeds.
- The token is not written anywhere under `.review/`.
- All seven write endpoints are covered, not just `/__edit`.

**Verification:** `python3 tests/e2e.py`, with new cases for A12.

---

### U5. Document script untrusted

**Goal:** serving a document does not hand that document the pen.

**Requirements:** R1.5. Threat model, "not accepted" clause 2.

**Files:** `server.py`

**Depends on:** U4 (the token must exist before the layer is exempted from the policy), U2.

**Approach:** serve the page with a Content-Security-Policy permitting only the review layer's own
script assets, so inline and document-origin script does not execute. This is a deliberate product
trade-off already recorded in the spec: the artefact loses its own interactivity under review.

**Test scenarios:**
- `js-assembled.html` served: its script does not run, and its regions are locked rather than
  populated.
- A fixture whose script attempts `fetch('/__edit')`: no write occurs.
- The review layer itself still functions fully.

**Verification:** `python3 tests/browser.py` covering A13.

---

### U6. Server-side payload validation

**Goal:** an edit cannot introduce markup the spec says edits do not carry.

**Requirements:** R2.6, and the "text within an existing region only" non-goal.

**Files:** `server.py`, `adapters/html_doc.py`

**Approach:** validate an edit payload server-side against the inline elements already present in
the target region. Reject `script`, `style`, `iframe`, `object` and any event-handler attribute, and
refuse the write with a reason. Server-side per the spec, because the browser layer is exactly the
part a hostile document could replace. Applies identically to an approved proposal.

**Test scenarios:**
- A payload containing `<script>`: refused with a reason, file unchanged.
- A payload containing `onclick=`: refused.
- A payload containing an `<em>` already present in the region: accepted.
- An approved **proposal** carrying a script tag: refused on the same path.

**Verification:** `python3 tests/e2e.py` covering A14.

---

### U7. Stable region identity

**Goal:** a reference to a region survives an edit to a different region.

**Requirements:** R1.1, I1.

**Files:** `adapters/html_doc.py`

**Approach:** replace the positional `u{i:03d}` id at `html_doc.py:223` with an identity derived
from the region's own content and position in the element tree, so an insert or split elsewhere does
not reindex it. Ids remain opaque strings; nothing outside the adapter should parse them.

**Test scenarios:**
- Edit region A so its byte length changes; a stored reference to region B still resolves to the
  same content.
- Split a region by editing it; ids of later regions are unchanged.
- Two regions with identical text in different tree positions get different ids.
- Re-parsing an unchanged document produces identical ids.

**Verification:** `python3 tests/probe.py`, with a new invariant assertion for I1.

---

### U8. Text anchoring, resolved to a region

**Goal:** an approved proposal lands on the words it was written against, or on nothing.

**Requirements:** R3.2, R3.3, R3.4. This is the correctness fix the spec singles out.

**Files:** `server.py`, `adapters/html_doc.py`

**Depends on:** U3 (needs `repeated-prose.html`), U7.

**Approach:** at propose time, capture the region's text content with whitespace collapsed and HTML
entities resolved, and store it on the proposal. At approve time, search that normalised anchor
within each region's contents, on the same normalisation. Per KTD2 the result is a **region**, and a
match spanning a region boundary counts as not found. Apply through the existing region write path
so `html_doc.write()`'s editable check still fires. Three outcomes: one match applies; more than one
is ambiguous; zero is orphaned. Neither of the latter two writes, and neither is discarded.

**Test scenarios:**
- Anchor found in one region: applies, file changed in that region only.
- Anchor found in two regions (`repeated-prose.html`): ambiguous, nothing written.
- Anchor deleted from the document: orphaned, nothing written, original anchor text retained.
- **Anchor resolving into a locked region: refused with the lock reason, nothing written.** This is
  the case that defeats I5 if anchoring resolves to an offset instead.
- Whitespace and entity differences between propose and approve still match.

**Verification:** `python3 tests/e2e.py` covering A4, A5, A6.

---

### U9. Orphaned, ambiguous and re-place interaction

**Goal:** a detached proposal is visible and recoverable rather than a dead record.

**Requirements:** R3.4, R3.5; A11.

**Files:** `lib/review.js`, `server.py`

**Depends on:** U8, U2.

**Approach:** present an orphaned or ambiguous proposal with its original anchor text shown. Offer
two actions: bin it, or re-place it by selecting the intended text in the document and confirming,
which updates the anchor and re-attempts the apply. Ambiguous additionally lists the candidate
regions.

**Test scenarios:**
- Orphaned proposal renders with its original anchor text visible.
- Re-place: select text, confirm, the proposal applies there.
- Re-place onto a locked region: refused with the lock reason.
- Bin: the proposal is removed and the event recorded.
- Neither state can be approved into the file directly.

**Verification:** `python3 tests/browser.py` covering A5, A6, A11.

---

### U10. Events always written, filtered on read

**Goal:** the stream is a complete record, which is what every later reader depends on.

**Requirements:** R4.4, R4.6; KTD3.

**Files:** `server.py`

**Approach:** remove the `who != "Claude"` condition from **both** `/__edit` (`server.py:216`) and
`/__reply` (`server.py:266`). Every event is appended unconditionally, carrying its author. No
filtering happens at write time. Suppression moves to the watcher in U11.

**Test scenarios:**
- An edit authored `Claude` appends an `edit` event.
- A reply authored `Claude` appends a `reply` event.
- Event ordering is preserved and the file stays append-only.
- No event type is filtered by author at write time anywhere in the file.

**Verification:** `python3 tests/e2e.py`, asserting the stream contains an agent-authored edit.

---

### U11. `watch.py`

**Goal:** an agent can watch for comments, edits and replies by running a command, whatever agent it
is.

**Requirements:** R4.1 to R4.6; the "agent's turn" section of `SPEC.md`.

**Files:** `watch.py`, `server.py`

**Depends on:** U10.

**Approach:** a standalone script, `watch.py <file.html> --as <identity> [--since <cursor>]
[--timeout <seconds>]`. It blocks on `inbox.jsonl` until events after the cursor appear, prints one
JSON object per line, and exits. Every response carries a cursor. Events authored by `--as` are
filtered **on read**. The cursor must remain valid across a server restart, so it is derived from
position in the stream rather than from server-process state. A timeout returns an empty result and
the unchanged cursor.

**Test scenarios:**
- Blocks, then returns when an event is appended.
- Cursor round trip: three events occur while disconnected, reconnecting with the last cursor
  returns exactly those three, once.
- Cursor survives a server restart.
- `--as Claude` does not return Claude's own events, but they are present in the file.
- Timeout returns empty plus the unchanged cursor, so a polling caller needs no special case.
- Needs no capability beyond running a command and reading stdout.

**Verification:** `python3 tests/e2e.py` covering A7 and A8, using two processes.

---

### U12. External modification detection

**Goal:** a byte-range write never lands on content it was not computed against.

**Requirements:** R7.1, R7.2, R2.7. R7 exists to protect I2.

**Files:** `server.py`, `adapters/html_doc.py`

**Approach:** record the file's modification time and size, or a hash, when it is read. Before any
write, re-check. On a mismatch, refuse the write, tell the human the file changed underneath, and
re-read. Per R2.7, content the human has typed and not yet saved survives the re-read.

**Test scenarios:**
- File modified by another process mid-serve: the next approval is refused with a clear message.
- The refusal does not discard in-progress human input.
- After re-read, a subsequent edit succeeds against the new content.
- No mismatch: writes proceed unchanged.

**Verification:** `python3 tests/e2e.py` covering A9.

---

### U13. The cooperative gate, tested

**Goal:** the tool's headline claim has a test that can fail.

**Requirements:** I6.

**Files:** `tests/e2e.py`, `SPEC.md`

**Depends on:** U12, U11.

**Approach:** I6's old test - "there is no code path from `/__propose` to a write" - is true and
passes while three bypasses exist, so it is replaced rather than supplemented. The new test asserts
what the cooperative gate actually guarantees: an agent that writes the file directly is **detected
and surfaced**, not silently absorbed. Confirm the spec's I6 text matches the implemented behaviour
after U12 lands.

**Test scenarios:**
- An agent writes the file directly while served; the next approval is refused and the human is told
  the file changed underneath.
- A proposal that is never approved never appears in the file.
- A rejected proposal never appears in the file, and the reason is recorded.

**Verification:** `python3 tests/e2e.py`. The I6 test must be demonstrated to fail when U12 is
reverted; a test that cannot fail is what this unit exists to replace.

---

### U14. Lifecycle: detach, idle clock, loopback

**Goal:** no forgotten, write-capable server outlives the session that started it.

**Requirements:** R6.3, R6.5, R6.6, R6.7.

**Files:** `server.py`, `lib/review.js`

**Approach:** three related fixes. Make `--detach` genuinely detach so the launching shell returns
immediately; its purpose is an agent starting the server unattended. Stop the client's
`/__version` poll resetting the idle clock: the idle timer counts human interaction - edit, comment,
approve - not polling, which today means one open tab keeps a detached server alive forever. Give a
detached server an absolute lifetime cap independent of request activity. Make loopback-only binding
an asserted requirement rather than an incidental line, with no flag to change it.

**Test scenarios:**
- `--detach` returns the shell immediately; the server survives it.
- A page polling with no human interaction does not prevent idle shutdown.
- A detached server exits at its lifetime cap despite continuous polling.
- The listening socket is `127.0.0.1` and no CLI flag changes it.
- Port in use: reported clearly, naming the file the existing server serves.

**Verification:** `python3 tests/e2e.py` plus a manual detach check, since a true detach is awkward
to assert in-process.

---

### U15. Locked regions marked at rest

**Goal:** a human can see what they cannot edit without probing it.

**Requirements:** R1.2, I5; A15.

**Files:** `lib/review.js`, `server.py`

**Depends on:** U2, U3.

**Approach:** a locked region is visually distinguishable on page load, not only when an edit is
attempted, and its reason is reachable without trying to edit. I5's at-rest half is the part no
automated check covers, so its acceptance row stays `manual` by design.

**Test scenarios:**
- `js-assembled.html` on load: locked regions distinguishable before any interaction.
- The reason is reachable without attempting an edit.
- An edit attempt is still refused with that reason.

**Verification:** `python3 tests/browser.py` for the server-side half (the units endpoint returns
`editable: false` with a `reason`), and a manual check for the at-rest presentation per A15.

---

## Verification Contract

| Command | Covers |
|---|---|
| `python3 tests/probe.py` | I1, I3, I4 over the committed fixture corpus. Must fail on a missing fixture |
| `python3 tests/e2e.py` | server and adapter behaviour: A1, A3, A4, A7, A8, A9, A12, A14, and I6 |
| `python3 tests/browser.py` | rendered-page behaviour: A2, A5, A6, A11, A13 |
| manual | A15 only, and the `--detach` shell check |

**Quality gates before publishing:**

1. **In a fresh clone, outside the author's home directory**, all three suites pass. This is the
   gate the current suite silently fails.
2. `python3 tests/probe.py` reports a **non-zero locked-region count**. Zero means U3's fixture is
   absent or the locked path is still dead.
3. The I6 test fails when U12 is reverted.
4. No acceptance row is marked `automated` while no automated check exists for it.

---

## Definition of Done

**Global:**

- Every requirement in the spec's "Open against this spec" list is either implemented or explicitly
  moved to a deferred section with a reason. None is silently reworded to match the code.
- Every acceptance example A1-A15 has a check at its stated verification level, or is marked
  `manual` deliberately.
- The spec's claims match the implementation. Where a unit changed what is true, `SPEC.md` is updated
  in the same commit.
- Dead ends removed: no abandoned or experimental code left in the diff.
- The README's run instructions work on a clean clone.

**Per unit:** its test scenarios pass, its verification command is green, and no invariant regressed
elsewhere - the full suite runs, not just the touched file.

**Not done until:** the fresh-clone gate passes. Everything else can look finished while that fails,
which is the failure mode this plan exists to close.
