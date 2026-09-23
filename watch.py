#!/usr/bin/env python3
"""Watch an artefact's review inbox and print new events, then exit.

This is the integration surface. The tool ships the watching; the agent runs a
command. That is what "no agent-specific capability" means in practice, and it
is why this is a command rather than a plugin, an MCP server or a library: it
works from Claude Code, from Codex, from a shell script and from cron without
any of them knowing about the others.

    python3 watch.py <file.html> --as <identity> [--since <cursor>]
                                 [--timeout <seconds>]

The agent's turn:

    1. watch.py <file> --as claude --since <cursor>   blocks until something happens
    2. read the returned comment or edit events
    3. read .review/<name>/comments.json for the full thread
    4. POST /__propose {id, unit, text, note, author: "claude"}
       Pass author with the exact same identity as watch.py --as.
    5. watch.py again with the new cursor

Output, deliberately split so each stream has one job (R4.2, R4.3):

    stdout   one event per line, byte-for-byte the line from inbox.jsonl.
             Nothing else is ever written there, so a consumer can parse every
             line as an event without special-casing a trailer.
    stderr   a single line, {"cursor": "<n>"}, on every run including a timeout
             and including a run that returned nothing.

Exit status is 0 whether or not events arrived: a quiet period is not an error.
It is non-zero only when the arguments or the file are wrong.
"""
import argparse
import json
import sys
import time
from pathlib import Path


def inbox_path(target: Path) -> Path:
    """Where the server keeps the stream for this artefact."""
    return target.parent / ".review" / target.stem / "inbox.jsonl"


def read_events(path: Path):
    """Every complete event line currently in the stream.

    A partially written final line is ignored rather than guessed at: the file
    is append-only, so it will be complete on the next pass. The alternative -
    parsing half a line - would hand an agent a malformed event once in a while
    and be almost impossible to reproduce.
    """
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(line)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="watch.py",
        description="Block until new review events arrive, print them, exit.")
    ap.add_argument("file", help="the .html artefact being reviewed")
    ap.add_argument("--as", dest="identity", required=True,
                    help="who is watching. Events with this author are not "
                         "returned, so an actor is never woken by its own action. "
                         "Pass the same identity as author in POST /__propose")
    ap.add_argument("--since", default="0",
                    help="cursor from a previous run. Position in the stream, so "
                         "it stays valid across a server restart")
    ap.add_argument("--timeout", type=float, default=300,
                    help="seconds to wait before returning empty (default 300)")
    ap.add_argument("--poll", type=float, default=0.25,
                    help="seconds between checks (default 0.25)")
    args = ap.parse_args(argv)

    target = Path(args.file).resolve()
    if target.suffix.lower() not in (".html", ".htm"):
        print(f"error: expected an .html file, got {target.name}", file=sys.stderr)
        return 2

    path = inbox_path(target)

    try:
        cursor = int(args.since)
        if cursor < 0:
            raise ValueError
    except (TypeError, ValueError):
        print(f"error: --since must be a whole number, got {args.since!r}",
              file=sys.stderr)
        return 2

    def emit_cursor(n):
        # Always on stderr, always exactly one line, even for a timeout.
        sys.stderr.write(json.dumps({"cursor": str(n)}) + "\n")
        sys.stderr.flush()

    deadline = time.monotonic() + max(0.0, args.timeout)
    while True:
        lines = read_events(path)

        # The cursor counts events CONSUMED, not events returned. Advancing past
        # this watcher's own events is what stops them coming back for ever on
        # the next reconnect.
        if len(lines) > cursor:
            fresh = lines[cursor:]
            new_cursor = len(lines)

            shown = 0
            for raw in fresh:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # R4.6: suppression happens on READ, never on write, and by
                # comparing against --as rather than a hardcoded name.
                if event.get("author") == args.identity:
                    continue
                # Printed verbatim so the line an agent parses is the line the
                # server wrote. Re-serialising would reorder keys and quietly
                # change the shape the integration was written against.
                sys.stdout.write(raw + "\n")
                shown += 1

            sys.stdout.flush()
            if shown:
                emit_cursor(new_cursor)
                return 0
            # Everything in this batch was our own. Move the cursor past it and
            # keep waiting rather than returning an empty result.
            cursor = new_cursor

        if time.monotonic() >= deadline:
            emit_cursor(cursor)          # R4.5: empty, and the cursor unchanged
            return 0

        time.sleep(max(0.01, args.poll))


if __name__ == "__main__":
    sys.exit(main())
