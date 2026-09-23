# How stet works

For contributors, and for anyone who wants to know what happens under the hood. If you only
want to use stet, the [README](../README.md) is enough.

`SPEC.md` is the authoritative description of behaviour: invariants I1 to I6, requirements
R1 to R7, the acceptance examples and the threat model. This page is the readable tour.

---

## The loop

```
  you edit a block ─────────────┐
  you leave a comment ──────────┤
                                ▼
                        ┌───────────────┐
                        │   server.py   │──► the .html file (byte-range edit)
                        │  stdlib only  │──► .review/edits.md      who changed what
                        └───────┬───────┘──► .review/comments.json threads
                                │          ─► .review/inbox.jsonl  agent event stream
                                ▼
                        ┌───────────────┐
                        │  the agent    │  reads inbox, writes a PROPOSAL
                        └───────┬───────┘
                                ▼
                   you press Approve, or Needs changes
                                ▼
                   only then does the file change
```

## The review layer is added as the page is served

Nothing is written into your document. No tags to add, no removal step, no risk of leaving
the tool behind in a file you ship. Stop the server and the file is exactly as it was.

## Edits are byte-range replacements

Only the edited region's bytes are replaced in the original text. The document is never
re-serialised, so everything around your edit survives byte for byte and git diffs stay
readable.

## What counts as an editable region

This is the whole design, and both obvious answers are wrong. Measured against 12 real
Claude-generated HTML documents:

| Rule | A visual document (all `div`s) | A prose page |
|---|---|---|
| Semantic tags only (`p`, `h1`, `li`…) | **0 regions found** | 41, fine |
| Any element holding text | 17, fine | **206**, including 74 bare `span`s |

Agents write visual documents entirely in `div`s, so a tag whitelist finds nothing. They also
wrap inline text in `span`s, so a leaf rule shatters one paragraph into a dozen fragments.

What works: **collapse inline elements into their parent, then take the deepest non-inline
element that still holds text.** Where the document has real semantic blocks that lands on
`<p>`, `<h3>`, `<li>`. Where it is all `div`s it lands on the innermost `div`. No whitelist.
Regions never nest, so two edits can never overlap.

## Regions the page builds with its own script

If a document assembles content in JavaScript and injects it with `innerHTML`, that text does
not exist in the file, so an edit to it could not be saved.

Those regions are detected and marked **comment-only**, visibly, in the toolbar. A tool that
silently accepts an edit it cannot save is worse than one that says no.

## The agent protocol

1. `watch.py <file> --as <name> --since <cursor>` blocks until something happens
2. read the returned `comment` or `edit` events
3. read `.review/<name>/comments.json` for the full thread
4. `POST /__propose {id, unit, text, note}`
5. run `watch.py` again with the new cursor

Events go to stdout, one JSON object per line, exactly as the server wrote them. The cursor
comes back on stderr and stays valid across a server restart.

`/__propose` is the only write endpoint an agent can reach. Editing, approving and rejecting
need a token that only the served page holds.

## Files it writes

Everything sits in `.review/<name>/` beside the document. Add `.review/` to your `.gitignore`.

| File | Holds |
|---|---|
| `edits.md` | Every change, before and after, with an author |
| `comments.json` | Threads, proposals, statuses |
| `inbox.jsonl` | Append-only event stream. Point a file watcher at it |

## Server lifecycle

The server exits when the process that launched it dies, or after the idle timeout. The idle
clock counts **human interaction only**: opening the page, editing, commenting, approving. The
browser's background poll does not keep it alive. `--idle-timeout 0` disables the idle clock.

`--detach` genuinely detaches (fork and setsid), for an agent starting the server unattended.
A detached server also has an absolute lifetime cap, eight hours by default, that no amount of
activity can extend.

It binds `127.0.0.1` only. There is no flag to change that.

## Tests

```bash
pip install -r requirements.txt && playwright install chromium   # browser tests only

python3 tests/probe.py     # region discovery, no overlap, byte-exact round trip,
                           # stable ids, and malformed documents over the fixtures
python3 tests/e2e.py       # the whole loop over HTTP: editing, comments, proposals,
                           # the approval gate, anchoring, the event stream, watch.py,
                           # external-modification detection
python3 tests/browser.py   # the rendered review layer, driven in a real browser
```

`probe.py` and `e2e.py` need nothing installed. Only `browser.py` needs a driver.

### Gate 3: proving the gate's test can fail

```bash
RV_GATE_DISABLED=1 python3 tests/e2e.py    # MUST exit non-zero
```

`RV_GATE_DISABLED=1` switches off the pre-write freshness check, which is what detects an
agent writing the file directly. With it switched off, the suite's I6 case is expected to
fail. So this command failing is the evidence that the case is a test which *can* fail,
rather than one that passes because nothing is being checked.

It is read from the environment once, at import, and never from a request. No endpoint,
header or parameter can set it, and `e2e.py` asserts that against the parse tree. It is a
test seam, not a mode to serve real work in, and the server says so loudly at startup when it
is on.
