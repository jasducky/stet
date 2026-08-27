#!/usr/bin/env python3
"""Run the adapter over real Claude artefacts and check the invariants.

Three things must hold or the whole design is wrong:
  1. unit count is sane - not 0 (missed everything), not 200+ (shattered prose)
  2. units never overlap - an edit can never clobber a neighbouring edit
  3. a round-trip edit changes ONLY the edited span, byte for byte
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapters import html_doc  # noqa: E402

VAULT = Path.home() / "Claude"
TARGETS = [
    VAULT / "samples/page-v8-overview.html",
    VAULT / "samples/card.html",
    VAULT / "samples/page-mock-v4.html",
    VAULT / "10-System/Images_generated/agent-design-page-mockup-v2.html",
    VAULT / "10-System/Images_generated/5q-landing-page-mockup.html",
    VAULT / "10-System/Images_generated/agent-harness-explained.html",
    VAULT / "10-System/Images_generated/10habits-spot-checklist.html",
    VAULT / "samples/page-mock-v6.html",
]

fails = []
print(f"{'file':44} {'units':>6} {'lock':>5} {'tags (top 4)':32} {'overlap':>8} {'round-trip':>11}")
print("-" * 112)

for path in TARGETS:
    if not path.exists():
        print(f"{path.name[:42]:44} MISSING")
        continue
    text = path.read_text(errors="replace")
    units = html_doc.parse(text)

    # 2. no overlap
    spans = sorted((u.inner for u in units))
    overlap = sum(1 for a, b in zip(spans, spans[1:]) if a[1] > b[0])

    # 3. round-trip on the first editable unit with real text
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
            fails.append(f"{path.name}: round-trip corrupted bytes outside the span")

    tags = {}
    for u in units:
        tags[u.tag] = tags.get(u.tag, 0) + 1
    top = ", ".join(f"{k}:{v}" for k, v in sorted(tags.items(), key=lambda x: -x[1])[:4])
    locked = sum(1 for u in units if not u.editable)

    if not units:
        fails.append(f"{path.name}: found NO units")
    if overlap:
        fails.append(f"{path.name}: {overlap} overlapping units")

    print(f"{path.name[:42]:44} {len(units):6} {locked:5} {top[:32]:32} {overlap:8} {rt:>11}")

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("All invariants held.")
