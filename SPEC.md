# Artefact Review, specification

**Status:** draft, v1 scope (HTML only)
**Date:** 19 September 2026
**Review:** 8 reviewers, ~30 findings folded in. I6 resolved 19 Sep: cooperative protocol.

---

## What this is

A local server that turns any HTML document into something a human and an AI agent both work on,
where **the human edits directly and the agent's changes are gated**.

```bash
python3 server.py report.html --port 8790 --author Julia
```

Open the page in a browser. Text is editable in place. Comments attach to regions. An agent reads
the conversation from a sidecar folder and sends rewrites as proposals, which reach the file only
when you approve them.

The gate is a cooperative protocol rather than an enforcement boundary, and I6 below says exactly
what that does and does not guarantee.

## Why it exists

This is for a document you are personally responsible for and will put your name to: a CV, a
proposal, a client report. An agent helped write it. You need to edit it yourself, and you need the
agent's changes to stop at a gate you control.

Tools in this space hand that round the other way. The agent edits and the human reacts.

### Prior art, and where this sits

Two axes decide it: **can the human edit the text in place, into the source file**, and **is the
agent's individual rewrite gated**.

| Tool | Human edits HTML in place | Agent's individual rewrite gated |
|---|---|---|
| **Plannotator** (8.1k stars, nine agent integrations) | Markdown yes, **HTML annotate-only** | No: one whole-document approve/dismiss to the calling hook |
| **Artifact Server** (Plannotator sibling) | **Yes**, writes to the linked file | No agent loop at all |
| **Claude Artifacts** | No, comment threads only | No: Claude republishes the page |
| **Ch00k/claude-review** | Markdown only | Threaded commenting, no in-place edit |
| **make-pages-interactive** | Not its purpose | Not its purpose |
| **Artefact Review** | **Yes** | **Yes, region by region** |

Nothing else occupies the bottom row. That is the whole scope, and it should not broaden.

---

## Non-goals for v1

- **Markdown or any non-HTML adapter.** The adapter boundary exists in the code; a second adapter
  is not v1.
- **A `SKILL.md` or any agent-specific packaging.** The watcher is a command; that is the
  integration surface.
- **Multi-user, authentication, hosting, or concurrent human editors.** One person, one machine,
  one file. The threat model below states what must be true for that to be safe.
- **Rich text editing, formatting controls, or structural editing.** Text and the inline elements
  already present in a region. No adding, deleting, moving or restyling elements.

---

## Threat model

Written down because this ships publicly under MIT, and a stranger may run it on a shared machine,
in a dev container with a forwarded port, or on a box with other accounts.

**Assumed true:**

- The server binds the loopback interface only (R6.6). It is trusted exactly as far as the local
  user account is.
- The operator owns the file being served.
- There is no sensitive data, no credential and no network exposure in scope.

**Accepted:**

- Any process running as that user can reach the port. That is accepted.

**Not accepted, and therefore a defect if it happens:**

- A web page in another browser tab reaching the write endpoints (R2.5).
- The served document's own script reaching the write endpoints (R1.5).
- Exposure beyond the local machine (R6.6).

---

## Invariants

Testable requirements, not principles. Each was learned by measuring; reversing one quietly breaks
the tool.

| # | Invariant | How it is tested | Verified by |
|---|---|---|---|
| **I1** | **Stable region identity.** A region's identity survives edits to other regions. | Edit region A, then confirm a reference to region B still resolves to the same content. | automated |
| **I2** | **Byte-range writes, never re-serialise.** An edit replaces only the edited region's bytes. | Approve one edit, then diff. Only that region's bytes may differ; whitespace, comments and formatting elsewhere unchanged. | automated |
| **I3** | **Regions found by inline-collapse, not a tag whitelist.** | A div-only visual artefact yields a non-zero region count, and prose does not shatter: region count stays within the stated bounds for each committed fixture. | automated |
| **I4** | **Regions never nest.** | No region's byte span contains another's. Asserted over every committed fixture. | automated |
| **I5** | **Script-built regions are comment-only, and say so.** | The JS-assembled fixture marks those regions locked at rest, and an edit attempt is refused with its reason. | automated (refusal) + manual (at-rest marking) |
| **I6** | **The approval gate is a cooperative protocol.** The tool gates rewrites an agent routes through `/__propose`. It is not an enforcement boundary. | An agent writes the file directly while served; the next approval is refused and the human is told the file changed underneath. | automated |

**I1's "inline" is defined** as the HTML inline content model plus `span`, `a`, `em`, `strong`, `b`,
`i`, `code`, `small`, `sub`, `sup`, `br`. Anything else is non-inline. This is stated because two
implementers reading "inline" produced different region sets.

**I5's detection rule**, stated because a fixture cannot be authored against an undocumented one.
A region is locked when an ancestor element's `id` appears in the locked set. That set is built by
scanning each `<script>` in the document for a DOM-write pattern, then collecting the quoted string
literals in that script which match an `id` present in the document.

The rule is **static**: it reads the source text and never executes the script, so R1.5's policy
against document script does not affect it.

It is also **heuristic, and its limits are part of the contract**. A script that builds an id by
concatenation, reads it from data, or writes through a node reference it never names as a string
will not be caught, and the region will be offered as editable. An edit there is persisted to the
file and then overwritten by the script on the next load. This is a known false-negative class, not
a defect to be fixed by guessing harder; widening the rule to catch it would lock regions that are
genuinely editable, which is the worse failure.

### I6 — the approval gate, stated honestly

**Decided 19 September 2026: the gate is a cooperative protocol, not an enforcement boundary.**

The tool gates rewrites an agent routes through `/__propose`. Direct writes are outside its
control, and R7 surfaces them rather than preventing them.

This is stated plainly because the alternative was to claim otherwise. Three reviewers found three
independent bypasses, all live in shipped code:

| # | Bypass | Location | Closeable? |
|---|---|---|---|
| 1 | The agent's own file-write tools. It never touches the server. | outside the tool | **No.** No local server can prevent this |
| 2 | `POST /__edit` writes immediately and accepts any `author` string. | `server.py:208-216` | Yes, with credentials |
| 3 | `server.py --approve <cid>` applies a proposal from the shell. | `server.py:337` | Yes, by removing the verb |

The previous wording of this invariant was tested as *"there is no code path from `/__propose` to a
write"*. That is **true, and passes while all three bypasses exist**. A test that cannot fail is not
a test.

**Bypass 1 is the reason for the decision.** An agent running in Claude Code or Codex holds ordinary
file-editing tools. Enforcement at the server cannot reach it, so a tool that advertised an enforced
gate would be advertising something it could never deliver. What the tool genuinely offers is worth
more than an overstatement: it is the only tool that gates an agent's *individual rewrite* at all.

**Test:** an agent writes the file directly while served. The next approval is refused, and the
human is told the file changed underneath. That test fails if the gate's real guarantee breaks.

### Deferred: the enforced gate

Not scheduled. Recorded so the choice is visible rather than forgotten, and so the spec is not
rewritten from scratch if it is ever taken.

Closing bypasses 2 and 3: the server mints two credentials at startup, a human token injected into
the served page and an agent token printed for the watcher; `/__edit` refuses the agent token; the
`--approve` CLI verb is removed; neither token is written to `.review/`, which the agent reads.

*Test:* a fixture agent holding shell access and the sidecar contents attempts `--approve`, a direct
`/__edit`, and a raw `/__approve`. The file is byte-identical after all three.

**This buys two of the three bypasses.** Bypass 1 remains, and the honest sentence above would still
have to be written. That is why it is deferred rather than done.

---

## Requirements

### R1. Regions

- **R1.1** Every region has an identity stable across edits to other regions.
- **R1.2** A region is editable, or locked with a human-readable reason. A locked region is
  distinguishable **at rest**, on page load, not only when an edit is attempted.
- **R1.3** Region discovery works on hand-written HTML, exported HTML, and div-only visual
  artefacts.
- **R1.4** Malformed or hostile HTML must not crash discovery. A document that cannot be parsed is
  reported as such and served read-only.
- **R1.5** **The served document's own scripts are untrusted.** The page is served with a
  Content-Security-Policy permitting only the review layer's own script assets, so document script
  cannot execute or reach the write endpoints.

> **Known defect (R1.1).** Region ids are positional (`adapters/html_doc.py:223`, `u{i:03d}`), so an
> insert or split reindexes every region after it. Not met today.

> **Product trade-off (R1.5).** Blocking document script also costs the artefact its own
> interactivity. A dashboard that filters or animates stops doing so under review. This is a
> deliberate choice: a document that can rewrite itself is worse than a document that cannot move.

### R2. Human editing

- **R2.1** Text is editable in place, in the rendered page.
- **R2.2** An edit persists to the source file immediately, as a byte-range write (I2).
- **R2.3** Every edit is recorded with before, after and author, in `edits.md`.
- **R2.4** An edit to a locked region is refused with its reason, never silently dropped.
- **R2.5** **Every write endpoint rejects a request that did not come from the served page.** The
  server checks `Origin` / `Sec-Fetch-Site` and additionally requires a per-session token minted at
  startup and injected into the page. A request failing either check is refused with 403 and no
  write occurs.
- **R2.6** **An edit's payload is validated server-side** against the non-goals: only text and the
  inline elements already present in the region are accepted. Script, style, iframe, object and
  event-handler attributes are rejected and the write refused with a reason — whether the edit came
  from the human or from an approved proposal. Server-side, because the browser layer is the part a
  hostile document can replace.
- **R2.7** When a write is refused after the human has typed into a region, the in-progress content
  is preserved for retry, never discarded by a re-read.

> **Known defect (R2.5, R2.6).** There is no origin, referer or token check anywhere in
> `server.py`. The browser layer posts the region's `innerHTML` and the server writes it unfiltered,
> so an edit can introduce a `script` tag that persists into the artefact and runs on every
> subsequent serve.

### R3. Proposals and the approval gate

- **R3.1** An agent proposes a rewrite for a region. It is stored, not applied.
- **R3.2** A proposal is anchored to **the text it was written against**, not a region index. The
  anchor is captured at propose time as the region's text content with whitespace collapsed and
  HTML entities resolved, and is compared on the same normalisation.
- **R3.3** On approval, the anchor is resolved **to a region, not to a byte offset**. The anchor
  text is searched within each region's contents; a match spanning a region boundary counts as not
  found.
  - found in exactly one region: apply there, **through the same write path as a human edit**, so a
    locked region refuses it with its reason (I5, R2.4)
  - found in more than one: do not apply. Present as **ambiguous**
  - not found: do not apply. Present as **orphaned**, showing the text it was written against
- **R3.4** An orphaned or ambiguous proposal is never discarded automatically and never applied
  automatically.
- **R3.5** **Re-placing is an interaction, not a concept.** The proposal's original anchor text is
  shown; the human selects the intended text in the document and confirms; that updates the
  proposal's anchor and re-attempts the apply. Binning is a single action.
- **R3.6** Rejection records a reason and returns the region to open.
- **R3.7** Approval is available from the browser and from the CLI (`server.py <file> --approve
  <cid>`). Both are human-initiated. The CLI verb is retained: under a cooperative gate it is a
  convenience, not a hole, and an agent that wanted to bypass the gate has simpler routes.

> R3.2 and R3.3 replace `block: N` anchoring, which can land an approved proposal on the wrong
> content after a split. Anchoring to a byte offset rather than a region would also skip the
> editable check in `html_doc.py:243` and write into a locked region, defeating I5.

### R4. The watcher

**The agent must be able to watch for comments, edits and replies without any agent-specific
capability.** The tool ships the watching; the agent runs a command. This is the integration
surface; see *The agent's turn* below for the loop that defines it.

- **R4.1** A command blocks until new events arrive, prints them, and exits:
  ```bash
  python3 watch.py <file.html> --as <identity> [--since <cursor>] [--timeout <seconds>]
  ```
- **R4.2** Output is one JSON object per line, matching the inbox event shape.
- **R4.3** Every response carries a cursor. Passing it back returns only events after it. **A cursor
  remains valid across a server restart**, so an agent reconnecting after a restart resumes rather
  than re-reading or missing events.
- **R4.4** Events are never lost while no watcher is attached. The stream is the record.
- **R4.5** A timeout returns an empty result and the unchanged cursor.
- **R4.6** **No event is suppressed by hardcoded author name.** An actor is not woken by its own
  action, determined by comparing against `--as`. Suppression happens **on read, never on write**:
  every event is always written to the stream.

> **Known defect (R4.6).** `/__reply` **and `/__edit`** both test `who != "Claude"` and suppress the
> **inbox append itself** (`server.py:216`, `server.py:266`). A direct agent edit therefore produces
> no inbox event at all, so R4.4 is false today for the highest-stakes event type. The edit is still
> recorded in `edits.md`, so it is not untracked, but no watcher, audit pass or reconnecting agent
> can learn of it from the stream. Fixing only `/__reply` leaves this in place.

**Event types**, all already emitted: `comment`, `edit`, `reply`, `approved`, `rejected`.

### The agent's turn

The loop, which is what "no agent-specific capability" means in practice:

1. `python3 watch.py <file> --as <identity> --since <cursor>` — blocks until something happens
2. Read the returned `comment` or `edit` events
3. Read `comments.json` for the full thread
4. `POST /__propose {id, unit, text, note}` with the anchor text
5. `watch.py` again with the new cursor
6. Act on the `approved` or `rejected` event
7. Store the cursor and return to 1

Nothing in that loop requires a capability specific to any agent. It is a command and an HTTP POST.

### R5. The sidecar

`.review/<name>/` beside the artefact, gitignored.

- **R5.1** `inbox.jsonl` is append-only. One line per event. Never rewritten.
- **R5.2** `comments.json` holds threads, proposals and statuses.
- **R5.3** `edits.md` holds every change with before, after and author.
- **R5.4** An agent watches `inbox.jsonl` and reads the others on demand. It never polls them.

### R6. Server lifecycle

- **R6.1** The server exits when its parent process dies.
- **R6.2** The server exits after a configurable idle timeout.
- **R6.3** `--detach` genuinely detaches. **Who needs it:** an agent starting the server unattended,
  as step 0 of *The agent's turn*. A human at a terminal has `nohup` and `&`; this requirement
  exists for the agent case.
- **R6.4** A port already in use is reported clearly, naming the file the existing server is serving.
- **R6.5** No orphaned server outlives a session unnoticed.
- **R6.6** **The server binds the loopback interface only.** There is no option to bind another
  address. Exposure beyond the local machine is permanently out of scope.
- **R6.7** **A detached server has an absolute lifetime cap independent of request activity.** The
  idle clock counts human interaction — edit, comment, approve — not the client's version poll.

> **Known defect (R6.3, R6.5, R6.7).** `--detach` holds the terminal. Worse, it is
> self-contradictory with R6.5 as built: detaching skips parent-death watching
> (`server.py:361`), and the injected client polls `/__version` every 3 seconds
> (`lib/review.js:312-322`) while every response resets `_last_hit` (`server.py:144`). One forgotten
> browser tab keeps a detached, write-capable server alive indefinitely.

### R7. Concurrent and external writes

**R7 protects I2.** A byte-range write assumes the source is unchanged since it was read. R7 is what
keeps that assumption honest; it is not new scope.

- **R7.1** A file modified on disk while served must not be silently overwritten by a byte-range
  write computed against the old content.
- **R7.2** On detecting external modification, refuse the write, tell the human what happened, and
  re-read. In-progress human input survives the re-read (R2.7).

---

## Acceptance examples

| # | Given | When | Then | Verification |
|---|---|---|---|---|
| A1 | A served document, no edits made | The server process exits | The source file is byte-for-byte unchanged | automated |
| A2 | A region with prose | The human edits it in the rendered page and it saves | Only that region's bytes differ | automated-browser |
| A3 | A JS-assembled region | The human tries to edit it | Refused, with a visible reason. File unchanged | automated (refusal) |
| A4 | An open proposal | The human rejects it | The text never appears in the file; the reason is recorded | automated |
| A5 | A proposal anchored to text since deleted | The human approves it | Presented as orphaned with its original anchor text. Nothing written | automated-browser |
| A6 | A proposal whose anchor text appears in two regions | The human approves it | Presented as ambiguous. Nothing written | automated-browser |
| A7 | An agent watching with a cursor, disconnected while 3 events occur | It reconnects with its last cursor | It receives exactly those 3 events, once | automated (two processes) |
| A8 | An agent posts a reply | Its own watcher is running | It is not woken, **and the event is in the stream** | automated (two processes) |
| A9 | The file is modified by another program while served | The human approves an edit | The write is refused and the change is reported | automated |
| A10 | A malformed HTML document | It is served | It does not crash. Read-only, with the reason shown | automated |
| A11 | An orphaned proposal | The human selects the intended text and confirms | The anchor updates and the apply re-attempts | automated-browser |
| A12 | A cross-site page POSTs to `/__edit` | The request arrives | Refused with 403. File unchanged | automated |
| A13 | The served document's own script calls `/__edit` | The page loads | The script does not execute. File unchanged | automated-browser |
| A14 | An edit payload containing a `script` tag | The human saves | Refused with a reason. File unchanged | automated |
| A15 | A locked region | The page loads | It is visibly distinguishable before any interaction | manual |
| A16 | The human is typing into a region, unsaved | An external modification is detected and the write refused | The typed content survives the re-read and can be retried | automated-browser |
| A17 | A served document referencing its own sibling script file | The page loads | That script does not execute. File unchanged | automated-browser |

**Verification values:** `automated` (server or adapter, runnable in `probe.py` or `e2e.py`),
`automated-browser` (needs a headless browser driving `lib/review.js`), `manual`.

**The browser harness does not exist.** Neither `probe.py` nor `e2e.py` loads a DOM. Every
`automated-browser` row is unverified until that harness is built, and it is its own unit of work,
not something folded into a later one.

---

## Test fixtures

Each is hand-authored, committed under `tests/fixtures/`, and blocks the requirements named. None
can be derived from an existing artefact.

| Fixture | Blocks | Owner |
|---|---|---|
| A JS-assembled page | I5, R1.2, R2.4, A3, A15 | hand-authored |
| A div-only visual artefact | I3, R1.3 | hand-authored |
| A malformed / truncated corpus | R1.4, A10 | hand-authored |
| A page with repeated identical prose | R3.3, A6 | hand-authored |

**The JS-assembled fixture does not exist, and the locked path has never been executed.** The
19 September run found 241 regions and **0 locked**.

**`tests/probe.py` must fail on a missing fixture.** It currently prints `MISSING` and continues
(`probe.py:32-34`), and all eight targets are absolute paths under `Path.home()/"Claude"`. A
stranger cloning the repo gets eight `MISSING` lines, an empty failure list, "All invariants held."
and exit code 0 — a suite that reports success having tested nothing. The corpus moves into the
repository and an unresolvable fixture becomes a failure.

---

## Build order

The document previously gave every requirement group equal weight. It does not.

1. **`probe.py` fixture handling + the committed corpus.** Everything else is verified by a suite
   that currently cannot fail on a clean clone.
2. **R2.5, R1.5, R2.6** — the security gaps. Live defects, not future work.
3. **R3.2, R3.3** — anchor correctness. The most important correctness fix.
4. **R4 + the watcher.** It does not exist, and without it no agent can take a turn, so nobody
   cloning the repo can see the behaviour the positioning rests on.
5. **Enforced gate (the deferred hardening).** Optional, and only worth doing if the cooperative
   framing proves insufficient in use.
6. **R6.3, R6.7, R7** — robustness.

---

## Verified state, 19 September 2026

Run against a 48,733-byte artefact:

- 21/21 `e2e.py` checks pass; `probe.py` resolves all 8 fixtures **on this machine only**
- 241 editable regions found, **0 locked** — the locked path has never been executed
- Source file byte-for-byte unchanged after serving (I1 holds in practice)
- All five event types emitted

**Open against this spec:** R1.1, R1.5, R2.5, R2.6, R2.7, R3.2, R3.3, R3.5, R4 entirely, R6.3,
R6.6, R6.7, R7.

---

## Open questions

1. **Does the cooperative framing hold up in use?** If an agent routinely writes around the gate
   in practice rather than in theory, the deferred hardening below becomes worth its cost.
2. **Does blocking document script (R1.5) cost too much?** A dashboard that filters or animates
   stops doing so under review. Is that still the artefact worth reviewing?
3. **Ambiguous anchors.** R3.3 orphans a proposal whose anchor text appears in more than one region.
   A narrower rule (nearest to original position) would apply more automatically, at some risk.
4. **Where does `--approve` sit** if two credentials are issued? It writes with no server and no
   browser, outside whatever boundary the HTTP layer establishes.
