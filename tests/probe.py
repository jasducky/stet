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
print("R1.1 / I1 - a reference to a region survives an edit to another region")
print("-" * 104)

_id_src = (FIXTURES / "prose-article.html").read_text()
_id_units = html_doc.parse(_id_src)

# Edit one region so its byte length changes, and split another into two. No
# OTHER region's id may disappear, and - the part that matters - no surviving id
# may come back pointing at different text. Checking only that an id still
# exists passes while it silently resolves to the wrong paragraph, which is the
# defect R1.1 describes.
_a = next(u for u in _id_units if u.editable and len(u.raw(_id_src).strip()) > 60)
_others = {u.id: u.raw(_id_src) for u in _id_units if u.id != _a.id}

for _label, _payload in [
    ("grown", "A MUCH LONGER REPLACEMENT " * 12),
    ("shrunk", "tiny"),
    ("split", "first half</p><p>second half that did not exist before"),
]:
    _new = html_doc.write(_id_src, _id_units, _a.id, _payload)
    _by = {u.id: u for u in html_doc.parse(_new)}
    _lost = [i for i in _others if i not in _by]
    _moved = [i for i in _others if i in _by and _by[i].raw(_new) != _others[i]]
    if _lost:
        fails.append(f"identity: {len(_lost)} region ids vanished when one region was {_label}")
    if _moved:
        fails.append(f"identity: {len(_moved)} ids resolved to DIFFERENT text "
                     f"when one region was {_label} (e.g. {_moved[0]})")
    print(f"{'one region ' + _label:32} {len(_lost):3} lost, {len(_moved):3} mis-resolved")

# identical text in different tree positions must not share an id
_rp = (FIXTURES / "repeated-prose.html").read_text()
_same = [u for u in html_doc.parse(_rp)
         if u.raw(_rp).strip() == "The average is the one number that cannot show you the problem."]
if len(_same) < 3:
    fails.append(f"identity: repeated-prose.html holds {len(_same)} identical regions, expected 3")
if len({u.id for u in _same}) != len(_same):
    fails.append("identity: regions with identical text share an id")
print(f"{'identical text, distinct ids':32} {len(_same)} regions, "
      f"{len({u.id for u in _same})} distinct ids")

# re-parsing an unchanged document is deterministic
if [u.id for u in html_doc.parse(_id_src)] != [u.id for u in _id_units]:
    fails.append("identity: re-parsing an unchanged document produced different ids")

# an id from the retired positional scheme must fail loudly, never resolve
try:
    html_doc.write(_id_src, _id_units, "u012", "hijacked")
    fails.append("identity: a retired positional id (u012) still resolved to a region")
except KeyError:
    pass
print(f"{'retired u012 reference':32} raises KeyError, does not resolve")

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
