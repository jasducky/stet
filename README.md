# artefact-review

**Suggest mode for HTML documents an agent wrote.**

Point it at any HTML file. You get a live page where you can edit any region in place, select text and leave comments, and where the agent's own rewrites arrive as **proposals you approve or send back** rather than changes that already happened.

The file on disk only ever changes when you edit it or approve something.

```bash
python3 server.py report.html
```

---

## Why this exists

Tools that let you comment on a document for an agent already exist. In all of them the human comments and the agent edits. Nobody asks permission, and there is no record of who changed what.

That is the wrong shape for a document you are responsible for: a CV, a proposal, a client report. You need to edit it yourself, and you need the agent's changes to stop at a gate you control.

| | Comment | Human edits in place | Agent needs approval | Who-changed-what trail |
|---|---|---|---|---|
| Comment layers | yes | no | no | no |
| **artefact-review** | yes | **yes** | **yes** | **yes** |

---

## How it works

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

### The review layer is injected at serve time

Nothing is written into your artefact. No tags to add, no removal step, no risk of leaving the tool behind in a file you ship. Close the server and the file is exactly as it was.

### Edits are byte-range replacements

Only the edited region's bytes are replaced in the original text. The document is never re-serialised, so everything around your edit survives byte for byte and git diffs stay readable.

---

## What counts as an editable region

This is the whole design, and both obvious answers are wrong. Measured against 12 real Claude-generated artefacts:

| Rule | A visual artefact (all `div`s) | A prose page |
|---|---|---|
| Semantic tags only (`p`, `h1`, `li`…) | **0 regions found** | 41, fine |
| Any element holding text | 17, fine | **206**, including 74 bare `span`s |

Agents write visual artefacts entirely in `div`s, so a tag whitelist finds nothing. They also wrap inline text in `span`s, so a leaf rule shatters one paragraph into a dozen fragments.

What works: **collapse inline elements into their parent, then take the deepest non-inline element that still holds text.** Where the document has real semantic blocks that lands on `<p>`, `<h3>`, `<li>`; where it is all `div`s it lands on the innermost `div`. No whitelist. Regions never nest, so two edits can never overlap.

## Regions the page builds with its own script

If an artefact assembles content in JavaScript and injects it with `innerHTML`, that text does not exist in the file. An edit to it could not be saved.

Those regions are detected and marked **comment-only**, visibly, in the toolbar. A tool that silently accepts an edit it cannot persist is worse than one that says no.

---

## Files it writes

Everything the agent reads sits in `.review/<name>/` beside the artefact:

| File | Holds |
|---|---|
| `edits.md` | Every change, before and after, with an author |
| `comments.json` | Threads, proposals, statuses |
| `inbox.jsonl` | Append-only event stream. Point a file watcher at it |

---

## Options

```
python3 server.py <file.html> [--port 8790] [--author NAME] [--idle-timeout 900]
python3 server.py <file.html> --approve c03      apply a proposal from the CLI
```

The server never leaks: it exits when the process that launched it dies, or after the idle timeout with no browser attached. `--idle-timeout 0` disables that.

---

## Tests

```bash
python3 tests/probe.py    # region detection + no-overlap + byte-exact round trip
python3 tests/e2e.py      # the full loop over HTTP against a copy of a real artefact
```

---

## Credit

The server lifecycle design (parent-death watchdog, idle timeout, `/info` for port reuse) is taken from [paraschopra/make-pages-interactive](https://github.com/paraschopra/make-pages-interactive), which gets it exactly right. [Ch00k/claude-review](https://github.com/Ch00k/claude-review) is the mature option if you want threaded commenting on Markdown and do not need the editing or the approval gate.

## Licence

MIT.
