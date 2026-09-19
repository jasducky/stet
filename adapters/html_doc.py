"""HTML adapter: turn a Claude HTML artefact into a list of editable units.

The whole design rests on one question - what is an editable unit? Two obvious
answers were tested against 12 real Claude artefacts and both failed:

  * semantic block tags only  -> 0 units on a visual artefact written in divs
  * any text-bearing leaf     -> 206 units on a prose page, 74 of them bare spans

What works is collapsing inline elements into their parent, then taking the
DEEPEST non-inline element that still holds text. Where the document has real
semantic blocks that lands on <p>/<h3>/<li>; where it is all divs it lands on
the innermost div. No tag whitelist needed.

Units never nest, so one edit can never overlap another.

Write-back replaces the unit's inner byte range in the ORIGINAL text. The
document is never re-serialised: everything Claude wrote outside the edited
span survives byte for byte, and git diffs stay readable.
"""

import hashlib
import re
from html.parser import HTMLParser

# Text inside these belongs to the parent, never to a unit of its own.
INLINE = {
    "span", "a", "em", "strong", "b", "i", "u", "s", "sup", "sub", "code",
    "small", "mark", "abbr", "cite", "q", "kbd", "samp", "var", "time", "br",
    "wbr", "img", "picture", "source",
}

# Never descended into, never editable.
OPAQUE = {"script", "style", "svg", "canvas", "template", "head", "noscript", "iframe"}

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

DOM_WRITE = re.compile(
    r"\.(innerHTML|outerHTML|textContent|innerText)\s*=|insertAdjacentHTML|appendChild|replaceChildren"
)


class Unit:
    """One editable region of the document.

    span      (start, end) byte range of the whole element in the source
    inner     (start, end) byte range of its contents, which is what gets edited
    editable  False when the tool cannot honestly persist an edit
    reason    why not, shown in the UI
    """

    __slots__ = ("id", "tag", "span", "inner", "editable", "reason", "depth")

    def __init__(self, uid, tag, span, inner, depth):
        self.id = uid
        self.tag = tag
        self.span = span
        self.inner = inner
        self.depth = depth
        self.editable = True
        self.reason = ""

    def raw(self, text):
        return text[self.inner[0]:self.inner[1]]

    def to_json(self, text):
        return {
            "id": self.id,
            "tag": self.tag,
            "raw": self.raw(text),
            "editable": self.editable,
            "reason": self.reason,
        }


class _Node:
    __slots__ = ("tag", "start", "inner_start", "inner_end", "end",
                 "children", "has_text", "attrs", "parent")

    def __init__(self, tag, start, inner_start, attrs, parent):
        self.tag = tag
        self.start = start
        self.inner_start = inner_start
        self.inner_end = None
        self.end = None
        self.attrs = attrs
        self.children = []
        self.has_text = False
        self.parent = parent


class _Builder(HTMLParser):
    """Builds a tree with exact byte offsets for every element."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text = text
        # line -> absolute offset, so getpos() converts to a byte index
        self._line_off = [0]
        for line in text.splitlines(keepends=True):
            self._line_off.append(self._line_off[-1] + len(line))
        self.root = _Node("#document", 0, 0, [], None)
        self.stack = [self.root]
        self.opaque_depth = 0
        self._opaque_tag = None
        self._opaque_inner = 0
        self.scripts = []

    def _off(self):
        line, col = self.getpos()
        return self._line_off[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if self.opaque_depth:
            return
        if tag in OPAQUE:
            self.opaque_depth = 1
            self._opaque_tag = tag
            self._opaque_inner = self._off() + len(self.get_starttag_text() or "")
            return
        if tag in VOID:
            return
        start = self._off()
        inner_start = start + len(self.get_starttag_text() or "")
        node = _Node(tag, start, inner_start, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        return  # self-closing: no contents, never a unit

    def handle_endtag(self, tag):
        if self.opaque_depth:
            if tag == self._opaque_tag:
                self.opaque_depth = 0
                if tag == "script":
                    self.scripts.append(self.text[self._opaque_inner:self._off()])
            return
        if tag in VOID:
            return
        # tolerate unclosed tags: unwind to the matching open element if any
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                close = self._off()
                for node in self.stack[i:]:
                    if node.inner_end is None:
                        node.inner_end = close
                        node.end = close + len(tag) + 3
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.opaque_depth or not data.strip():
            return
        self.stack[-1].has_text = True


def _subtree_has_text(node):
    if node.has_text:
        return True
    return any(_subtree_has_text(c) for c in node.children)


def _collect(node, out, depth=0):
    """Deepest non-inline element holding text, inline collapsed into parents.

    Returns True when this subtree yielded at least one unit, so a parent knows
    its children already cover it. Units therefore never nest.
    """
    covered = False
    for child in node.children:
        if child.tag in INLINE:
            # inline text belongs to this node, not to a unit of its own
            if _subtree_has_text(child):
                node.has_text = True
            continue
        if _collect(child, out, depth + 1):
            covered = True

    if covered:
        return True
    if node.tag == "#document":
        return False
    if not _subtree_has_text(node):
        return False
    if node.inner_end is None:
        return False
    out.append((node, depth))
    return True


def _script_locked_ids(scripts, text):
    """Ids a script writes into. Their contents do not live in the file, so an
    edit could not be persisted - they are comment-only by design."""
    doc_ids = set(re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', text))
    locked = set()
    for src in scripts:
        if not DOM_WRITE.search(src):
            continue
        for literal in re.findall(r'["\']([A-Za-z0-9_-]{2,64})["\']', src):
            if literal in doc_ids:
                locked.add(literal)
    return locked


def parse(text):
    """text -> [Unit], in document order."""
    b = _Builder(text)
    b.feed(text)
    b.close()
    for node in b.stack[1:]:  # anything still open at EOF
        if node.inner_end is None:
            node.inner_end = node.end = len(text)

    raw = []
    _collect(b.root, raw)
    raw.sort(key=lambda nd: nd[0].start)

    locked = _script_locked_ids(b.scripts, text)
    units = []
    seen = {}
    for i, (node, depth) in enumerate(raw):
        rid = _region_id(node, text)
        # A collision would make write() edit whichever region came first, with
        # nothing reported. Disambiguate deterministically in document order.
        if rid in seen:
            seen[rid] += 1
            rid = f"{rid}-{seen[rid]}"
        else:
            seen[rid] = 0
        u = Unit(rid, node.tag, (node.start, node.end),
                 (node.inner_start, node.inner_end), depth)
        anc, hit = node, None
        while anc is not None:
            aid = dict(anc.attrs or []).get("id")
            if aid and aid in locked:
                hit = aid
                break
            anc = anc.parent
        if hit:
            u.editable = False
            u.reason = f"built by script (#{hit}) - comment only"
        units.append(u)
    return units


def _path_step(node):
    """What identifies this element among its same-tag siblings.

    Deliberately uses only things that do NOT change when the region's text is
    edited: the tag, its id or classes, and its index among same-tag siblings.
    """
    attrs = dict(node.attrs or [])
    nid = attrs.get("id")
    if nid:
        return f"{node.tag}#{nid}"        # unique in a document; no index needed

    step = node.tag or "?"
    cls = (attrs.get("class") or "").split()
    if cls:
        step += "." + ".".join(cls[:2])
    if node.parent is not None:
        same = [c for c in node.parent.children if getattr(c, "tag", None) == node.tag]
        try:
            step += f"[{same.index(node)}]"
        except ValueError:
            pass
    return step


_WS = re.compile(r"\s+")


def _region_id(node, text):
    """A region's identity: where it sits in the tree, plus what it says.

    R1.1. The old id was the region's ORDINAL (u000, u001, ...), so inserting or
    splitting a region renumbered every region after it and every stored comment
    silently pointed at different text.

    BOTH parts are needed, and position alone is not enough. A structural path
    ending in the node's own sibling index has the same defect in a narrower
    form: splitting a <p> shifts every later <p> from [3] to [4], so an old id
    still resolves - to the wrong paragraph. Measured, not assumed.

    So the node's own step is its tag plus a digest of its normalised text,
    while its ANCESTORS contribute their indexed path. A container being
    inserted is far rarer than a sibling paragraph appearing, and identical text
    in two different containers still gets two ids.

    The consequence, accepted deliberately: editing a region changes that
    region's own id. Re-anchoring a comment onto edited words is text anchoring,
    which is U8, and an anchor that can no longer be placed is U9's orphan case.

    The id is OPAQUE. Nothing outside this module may parse it.
    """
    parts = []
    n = node.parent
    while n is not None and getattr(n, "tag", None):
        parts.append(_path_step(n))
        n = n.parent
    path = "/".join(reversed(parts))

    inner = ""
    if node.inner_start is not None and node.inner_end is not None:
        inner = text[node.inner_start:node.inner_end]
    content = _WS.sub(" ", inner).strip()

    seed = f"{path}/{node.tag}:{content}"
    return "r" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def write(text, units, unit_id, new_inner):
    """Replace one unit's contents by byte range. Returns the new document."""
    for u in units:
        if u.id == unit_id:
            if not u.editable:
                raise ValueError(f"{unit_id} is not editable: {u.reason}")
            s, e = u.inner
            return text[:s] + new_inner + text[e:]
    raise KeyError(unit_id)


# ---------------------------------------------------------------- validation

# Refused whatever the region contains, because a region's byte range can hold
# an opaque element even though discovery never descends into one. Belt and
# braces over the "already present" rule below.
HARD_BANNED = {
    "script", "style", "iframe", "object", "embed", "applet",
    "frame", "frameset", "form", "input", "button", "textarea",
    "link", "meta", "base",
}

# Values that execute when a link or image is activated.
_ACTIVE_URL = re.compile(r"^\s*(javascript|vbscript|data)\s*:", re.I)


class _TagScan(HTMLParser):
    """Collect the tags and attributes a fragment uses. Tolerant of malformed
    input: this runs on whatever a client posted, which may be anything."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = set()
        self.attrs = []          # (tag, name, value)

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, value in attrs:
            self.attrs.append((tag, (name or "").lower(), value or ""))

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self.tags.add(tag)


def _scan(fragment):
    p = _TagScan()
    try:
        p.feed(fragment)
        p.close()
    except Exception:                      # noqa: BLE001 - malformed is expected
        pass
    return p


def validate_edit(new_inner, region_inner):
    """Raise ValueError with a human-readable reason if this payload may not be
    written into a region whose current contents are `region_inner`.

    R2.6: only text and the inline elements ALREADY PRESENT in the region are
    accepted. Server-side, because the browser layer is the part a hostile
    document can replace - and in apply_edit rather than the HTTP handler,
    because `server.py --approve` writes without going through one.

    The "already present" rule is deliberately strict. Adding emphasis to a
    region that had none is refused, and that is the documented trade-off: the
    tool's job is reviewing wording, not restyling markup.
    """
    used = _scan(new_inner)

    banned = sorted(used.tags & HARD_BANNED)
    if banned:
        raise ValueError(
            "refused: an edit may not introduce "
            + ", ".join(f"<{t}>" for t in banned))

    handlers = sorted({n for _, n, _ in used.attrs if n.startswith("on")})
    if handlers:
        raise ValueError(
            "refused: an edit may not carry event-handler attributes ("
            + ", ".join(handlers) + ")")

    active = sorted({f"{t}[{n}]" for t, n, v in used.attrs
                     if n in ("href", "src", "xlink:href") and _ACTIVE_URL.match(v)})
    if active:
        raise ValueError(
            "refused: an edit may not point " + ", ".join(active)
            + " at a javascript:, vbscript: or data: URL")

    allowed = _scan(region_inner).tags & INLINE
    introduced = sorted(used.tags - allowed)
    if introduced:
        raise ValueError(
            "refused: an edit may only use the inline elements already in this "
            "region (" + (", ".join(f"<{t}>" for t in sorted(allowed)) or "none")
            + "); it introduced " + ", ".join(f"<{t}>" for t in introduced))


# ------------------------------------------------------------------ anchoring

class _TextOnly(HTMLParser):
    """Text content only: inline markup dropped, entities resolved."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf = []

    def handle_data(self, data):
        self.buf.append(data)


def anchor_text(fragment):
    """A region's contents as comparable plain text.

    R3.2. Inline markup stripped, entities resolved, whitespace collapsed. A
    region's raw contents carry tags mid-sentence in roughly a third of real
    documents, so comparing a plain-text anchor against raw HTML would orphan
    every proposal that happened to touch a link or an emphasis.

    Capture and comparison MUST use this same function, or the normalisation is
    not actually shared and the bug comes back quietly.
    """
    p = _TextOnly()
    try:
        p.feed(fragment)
        p.close()
    except Exception:                      # noqa: BLE001 - malformed is expected
        pass
    return _WS.sub(" ", "".join(p.buf)).strip()


def find_anchor(text, units, anchor):
    """Regions whose contents contain this anchor. Returns a list of Units.

    R3.3. The result is a REGION, never a byte offset: resolving to an offset
    would skip the editable check in write() and let an approved proposal land
    inside a locked region. Searching per region also means a match spanning a
    region boundary is simply not found, which is the required behaviour rather
    than an accident.

    Locked regions are included deliberately. An anchor that resolves into one
    must be refused with the lock's reason, not silently skipped as though the
    text were not there.
    """
    needle = _WS.sub(" ", anchor or "").strip()
    if not needle:
        return []
    return [u for u in units if needle in anchor_text(u.raw(text))]
