"""Fail fast when the running interpreter does not match .python-version.

Render installs dependencies with the interpreter named by the service's
PYTHON_VERSION environment variable, then runs the build command with the
project's pinned version. When those disagree, pip installs a wheel for one
ABI and Python loads it under another. The symptom is a wall of
unrelated-looking errors from whichever compiled dependency is imported first:

    ImportError: no pq wrapper available.
    - couldn't import psycopg 'c' implementation: No module named 'psycopg_c'
    - couldn't import psycopg 'binary' implementation: cannot import name
      'pq' from 'psycopg_binary'

That names a Postgres driver, not a Python version mismatch, which is exactly
why the mismatch is so expensive to diagnose. This check runs first in the
build command and names the actual problem instead.

Only the major.minor pair is compared. A differing patch release is fine --
the ABI tag that matters is the minor version -- and pinning a patch here
would fail builds on hosts that ship a different one.
"""
import sys
from pathlib import Path

EXPECTED_FILE = Path(__file__).resolve().parent.parent / ".python-version"


def expected_version():
    try:
        return EXPECTED_FILE.read_text().strip().split(".")[0:2]
    except OSError:
        return None


def main():
    expected = expected_version()
    if not expected:
        print("No .python-version found; skipping interpreter check.")
        return 0

    want = ".".join(expected)
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running == want:
        print(f"Python {running} matches .python-version ({want}).")
        return 0

    print(f"\n  Python version mismatch\n", file=sys.stderr)
    print(f"    .python-version pins : {want}", file=sys.stderr)
    print(f"    this interpreter is : {running}"
          f"  ({sys.executable})", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Dependencies were installed for a different Python than the one")
    print("  running this build, so compiled wheels do not match. Compiled")
    print("  dependencies here: psycopg-binary (Postgres driver) and Brotli.")
    print("", file=sys.stderr)
    print("  On Render: open the service, go to Environment, and set", file=sys.stderr)
    print(f"  PYTHON_VERSION={want} to match .python-version, then redeploy.",
          file=sys.stderr)
    print("  render.yaml already sets this, so a service created from the", file=sys.stderr)
    print("  blueprint needs no change -- a hand-created service does.", file=sys.stderr)
    print("", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
