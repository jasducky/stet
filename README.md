# stet

**Edit any HTML document in place. Make the agent ask before it changes your words.**

An agent writes you an HTML document: a report, a one-pager, a mock, a brief. It is
nearly right and you want to change a few sentences.

Your options are usually to ask the agent to regenerate it, and re-read the whole
thing to find what else moved; to open the markup and edit it by hand; or to copy the
text somewhere else, fix it, and paste it back into a second copy that has now lost
the design.

`stet` is the fourth option. Point it at the file:

```bash
python3 server.py report.html
```

The document opens in your browser looking exactly as it was built. Click any block,
retype it, and that block is written back into the real file. Only that block: the
bytes around it do not move, so the diff stays readable and nothing is reformatted.

And when the agent works on the same document, its rewrites do not land. They arrive
as **proposals** in a sidebar, and you approve them or send them back. Every change,
yours and its, is recorded with who made it.

The file on disk only ever changes when you edit it, or when you approve something.

### Why "stet"

The proofreader's mark meaning *let it stand*: the note an editor makes to say
**ignore that change, my words stay as they are.** That is the whole tool.

---

## Why this exists

It was built to fill a gap the author kept hitting: you can ask an agent to change a
document, but you cannot simply fix a word yourself, and nothing makes the agent wait
for a yes.

Comment layers for agents already exist. In all of them the human comments and the
agent edits. Here it is the other way round.

| | Comment | You edit in place | Agent needs approval | Who-changed-what trail |
|---|---|---|---|---|
| Comment layers | yes | no | no | no |
| **stet** | yes | **yes** | **yes** | **yes** |

### It is not tied to one agent

The integration is a command, not a plugin, an extension or an MCP server:

```bash
python3 watch.py report.html --as claude --since 12
```

It blocks until something happens, prints the new events as JSON, and exits. That
works from Claude Code, from Codex, from a shell script and from cron, without any of
them needing to know about the others. Deliberate: a tool that only works inside one
assistant is a tool you lose when you change assistant.

---

## The agent's turn

1. `watch.py <file> --as <name> --since <cursor>` — blocks until something happens
2. read the returned `comment` or `edit` events
3. read `.review/<name>/comments.json` for the full thread
4. `POST /__propose {id, unit, text, note}`
5. `watch.py` again with the new cursor

Events go to stdout, one JSON object per line, exactly as the server wrote them. The
cursor comes back on stderr and stays valid across a server restart.

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
python3 server.py <file.html> [--port 8790] [--author NAME]
                              [--idle-timeout 900] [--detach] [--max-life 28800]
python3 server.py <file.html> --approve c03      apply a proposal from the CLI

python3 watch.py <file.html> --as <name> [--since N] [--timeout 300]
```

The server never leaks. It exits when the process that launched it dies, or after the
idle timeout, and the idle clock counts **human interaction only** — opening the page,
editing, commenting, approving. The browser's background poll does not keep it alive.
`--idle-timeout 0` disables the idle clock.

`--detach` genuinely detaches, for an agent starting the server unattended; a detached
server also has an absolute lifetime cap, eight hours by default, that no amount of
activity can extend.

It binds `127.0.0.1` only. There is no flag to change that.

---

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

`RV_GATE_DISABLED=1` switches off the pre-write freshness check, which is what
detects an agent writing the file directly. With it on, the suite's I6 case is
expected to fail — so this command failing is the evidence that the case is a
test which *can* fail, rather than one that passes because nothing is being
checked.

It is read from the environment once, at import, and never from a request:
there is no endpoint, header or parameter that can set it, and `e2e.py` asserts
that against the parse tree. It is a test seam, not a mode to serve real work in,
and the server says so loudly at startup when it is on.

---

## Credit

The server lifecycle design (parent-death watchdog, idle timeout, `/info` for port reuse) is taken from [paraschopra/make-pages-interactive](https://github.com/paraschopra/make-pages-interactive), which gets it exactly right. [Ch00k/claude-review](https://github.com/Ch00k/claude-review) is the mature option if you want threaded commenting on Markdown and do not need the editing or the approval gate.

## Licence

MIT.
