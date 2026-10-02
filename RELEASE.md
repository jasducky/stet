# Releasing stet

The steps for every change that ships, in order. Each one exists because skipping it has broken
something before, or would break something a user relies on.

## 1. Keep the design rules

Read the invariants in [SPEC.md](SPEC.md) (I1 to I6) before changing how pages are served or
saved. The short version:

- Nothing is ever written into the document except an edit someone made or approved.
- A save replaces only the bytes of the edited block; the file is never re-serialised.
- Anything stet draws on screen (marks, labels, highlights, settings) lives in the page as
  served, never in the file.
- An agent's change reaches the file only through an approved proposal.
- A refused save never loses what the person typed.

## 2. Test

```bash
python3 tests/run_all.py
```

All four gates must pass. The browser gate needs Playwright
(`pip install -r requirements.txt && playwright install chromium`); if it cannot run, the
release is not ready. New behaviour gets a check in `tests/` in the same change, and a check
for a bug is run against the old code first to prove it can fail.

## 3. Check the words

- Every README change is read by someone (or something) with no context for the project,
  and checked for unclear or AI-sounding writing, before it ships.
- Labels on screen use plain words: no tag names, internal ids or code terms.

## 4. Version

- Bump `__version__` in `server.py` (patch for fixes, minor for new behaviour).
- Add a dated entry at the top of [CHANGELOG.md](CHANGELOG.md), written for a person using
  stet.

## 5. Publish

```bash
git commit -m "<version>: <what changed>"
git tag -a v<version> -m "stet <version>: <one line>"
git push origin main && git push origin v<version>
```

Then confirm the remote: `git ls-remote origin refs/heads/main` and
`git ls-remote --tags origin` must show the new commit and tag.
