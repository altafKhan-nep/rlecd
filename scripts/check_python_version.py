"""Fail fast when the running interpreter does not match .python-version.

Vercel resolves and installs dependencies with its own interpreter, then runs
the build command with the one the project pins. When those disagree, pip
installs a wheel for one ABI and Python tries to load it under another. The
symptom is a wall of unrelated-looking errors from whichever compiled
dependency happens to be imported first:

    ImportError: no pq wrapper available.
    - couldn't import psycopg 'c' implementation: No module named 'psycopg_c'
    - couldn't import psycopg 'binary' implementation: cannot import name
      'pq' from 'psycopg_binary'

That names a Postgres driver, not a Python version mismatch, and cost several
build cycles to trace back to its cause. This check runs first in the build
command and names the actual problem.

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
    print("  On Vercel: Project -> Settings -> Python -> Version, set it to",
          file=sys.stderr)
    print(f"  {want} to match .python-version, then redeploy. The install step", file=sys.stderr)
    print("  uses the project's configured version, not this file.", file=sys.stderr)
    print("", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
