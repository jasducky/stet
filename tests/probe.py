#!/usr/bin/env python3
"""Run the adapter over the committed fixture corpus and check the invariants.

Four things must hold or the whole design is wrong:
  1. region count stays within the bounds recorded for each fixture - not 0 (missed
     everything), not far above (shattered prose). This is I3's real assertion.
  2. regions never overlap - an edit can never clobber a neighbouring edit (I4)
  3. a round-trip edit changes ONLY the edited span, byte for byte (I2)
  4. script-built regions are locked (I5)

Fixtures are committed under tests/fixtures/ and resolved relative to this file, so
this suite tells the truth on a clean clone with no sibling vault. A fixture that
cannot be resolved is a FAILURE, never a skip: a suite that reports success having
tested nothing is worse than no suite.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapters import html_doc  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# name -> (min regions, max regions, minimum locked regions)
#
# Bounds bracket the count measured when the fixture was authored, with a small
# margin so an innocuous copy edit does not fail the suite while a change in
# region discovery still does. Tighten a bound rather than widen it.
TARGETS = {
    "div-only-card.html":   (5, 8, 0),
    "checklist-card.html":  (5, 8, 0),
    "js-assembled.html":    (5, 8, 2),
    "landing-page.html":    (22, 31, 0),
    "prose-article.html":   (36, 48, 0),
    "table-report.html":    (38, 50, 0),
    "div-grid-mock.html":   (55, 72, 0),
    "deep-sections.html":   (90, 115, 0),
    "repeated-prose.html":  (13, 18, 0),
    "sibling-script.html":  (6, 9, 0),
    "script-attack.html":   (5, 8, 1),
}

# R1.4: malformed or hostile HTML must not crash discovery.
#
# These are asserted separately because a near-zero region count is a legitimate
# outcome for a broken document, so the bounds above would be meaningless. What
# must hold is that parse() returns rather than raising, and that the round trip
# stays byte-exact on whatever it does find.
#
# name -> minimum regions expected (0 where recovery legitimately finds almost none)
MALFORMED = {
    "malformed/truncated.html":    1,
    "malformed/unclosed.html":     1,
    "malformed/deep-nest.html":    1,
    "malformed/bad-entities.html": 1,
}

fails = []
parsed = 0

print(f"{'fixture':24} {'regions':>8} {'bounds':>11} {'lock':>5} "
      f"{'tags (top 4)':30} {'overlap':>8} {'round-trip':>11}")
print("-" * 104)

for name, (lo, hi, min_locked) in TARGETS.items():
    path = FIXTURES / name

    # 0. a fixture that is not there is a failure, not a skip
    if not path.exists():
        print(f"{name[:22]:24} MISSING")
        fails.append(f"{name}: fixture missing from {FIXTURES}")
        continue

    text = path.read_text(errors="replace")
    units = html_doc.parse(text)
    parsed += 1

    # 1. region count within the recorded bounds
    if not units:
        fails.append(f"{name}: found NO regions")
    elif not (lo <= len(units) <= hi):
        fails.append(f"{name}: {len(units)} regions, outside the recorded bounds {lo}-{hi}")

    # 2. no overlap
    spans = sorted((u.inner for u in units))
    overlap = sum(1 for a, b in zip(spans, spans[1:]) if a[1] > b[0])
    if overlap:
        fails.append(f"{name}: {overlap} overlapping regions")

    # 3. round-trip on the first editable region with real text
    rt = "n/a"
    target = next((u for u in units if u.editable and len(u.raw(text).strip()) > 12), None)
    if target:
        marker = "ZZMARKERZZ"
        new = html_doc.write(text, units, target.id, marker)
        s, e = target.inner
        ok = (new[:s] == text[:s]
              and new[s:s + len(marker)] == marker
              and new[s + len(marker):] == text[e:])
        rt = "OK" if ok else "BROKEN"
        if not ok:
            fails.append(f"{name}: round-trip corrupted bytes outside the span")
    else:
        fails.append(f"{name}: no editable region with enough text to round-trip")

    # 4. script-built regions are locked
    locked = sum(1 for u in units if not u.editable)
    if locked < min_locked:
        fails.append(f"{name}: {locked} locked regions, expected at least {min_locked}")

    tags = {}
    for u in units:
        tags[u.tag] = tags.get(u.tag, 0) + 1
    top = ", ".join(f"{k}:{v}" for k, v in sorted(tags.items(), key=lambda x: -x[1])[:4])

    print(f"{name[:22]:24} {len(units):8} {f'{lo}-{hi}':>11} {locked:5} "
          f"{top[:30]:30} {overlap:8} {rt:>11}")

print()
print("R1.4 - malformed documents must not crash discovery")
print("-" * 104)

for name, min_regions in MALFORMED.items():
    path = FIXTURES / name
    if not path.exists():
        print(f"{name[:30]:32} MISSING")
        fails.append(f"{name}: fixture missing from {FIXTURES}")
        continue

    text = path.read_text(errors="replace")
    try:
        units = html_doc.parse(text)
    except Exception as exc:                      # noqa: BLE001 - that is the assertion
        print(f"{name[:30]:32} RAISED {type(exc).__name__}")
        fails.append(f"{name}: discovery raised {type(exc).__name__}: {exc}")
        continue

    parsed += 1
    spans = sorted((u.inner for u in units))
    overlap = sum(1 for a, b in zip(spans, spans[1:]) if a[1] > b[0])
    if overlap:
        fails.append(f"{name}: {overlap} overlapping regions")
    if len(units) < min_regions:
        fails.append(f"{name}: {len(units)} regions, expected at least {min_regions}")

    # the round trip must stay byte-exact even on a document this broken
    rt = "n/a"
    target = next((u for u in units if u.editable and len(u.raw(text).strip()) > 12), None)
    if target:
        marker = "ZZMARKERZZ"
        new = html_doc.write(text, units, target.id, marker)
        s, e = target.inner
        ok = (new[:s] == text[:s]
              and new[s:s + len(marker)] == marker
              and new[s + len(marker):] == text[e:])
        rt = "OK" if ok else "BROKEN"
        if not ok:
            fails.append(f"{name}: round-trip corrupted bytes outside the span")

    print(f"{name[:30]:32} parsed, {len(units):3} regions, overlap {overlap}, round-trip {rt}")

print()

# An empty or unresolvable corpus must fail here rather than print a verdict.
expected = len(TARGETS) + len(MALFORMED)
if parsed != expected:
    fails.append(f"only {parsed} of {expected} fixtures parsed")
if not TARGETS:
    fails.append("TARGETS is empty - the suite would report success having tested nothing")

if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)

print(f"All invariants held across {parsed} fixtures.")
