#!/usr/bin/env python3
"""Run all four stet gates and give one answer.

    python3 tests/run_all.py

1. probe.py      region discovery and byte-exact round trips over the fixtures
2. e2e.py        the server over HTTP
3. gate 3        e2e.py with the approval gate switched off, which MUST fail:
                 it proves the gate's own test is one that can fail
4. browser.py    the page in a real browser (needs Playwright, see requirements.txt)

A gate that cannot run (for example, no browser driver) counts as a failure,
because a release needs all four. Exits 0 only when all four pass.
"""
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def run(label, script, env_extra=None, must_fail=False):
    env = dict(os.environ, **(env_extra or {}))
    proc = subprocess.run([sys.executable, str(TESTS / script)], env=env,
                          capture_output=True, text=True)
    lines = [l for l in (proc.stdout + proc.stderr).strip().splitlines() if l.strip()]
    last = lines[-1].strip() if lines else "(no output)"
    ok = proc.returncode != 0 if must_fail else proc.returncode == 0
    note = f"exit {proc.returncode}, must be non-zero" if must_fail else last
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<28} {note}")
    if not ok and not must_fail:
        for l in [l for l in lines if "FAIL" in l][:10]:
            print(f"          {l.strip()}")
    return ok


def main():
    print("stet: all four gates\n")
    results = [
        run("1. probe", "probe.py"),
        run("2. e2e over HTTP", "e2e.py"),
        run("3. gate off must fail", "e2e.py", {"RV_GATE_DISABLED": "1"}, must_fail=True),
        run("4. browser", "browser.py"),
    ]
    passed = sum(results)
    print(f"\n{passed}/4 gates passed" + ("" if passed == 4 else "  -  NOT releasable"))
    return 0 if passed == 4 else 1


if __name__ == "__main__":
    sys.exit(main())
