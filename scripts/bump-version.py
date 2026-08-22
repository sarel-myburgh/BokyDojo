#!/usr/bin/env python3
"""Bump the version in apps/core/version.py — plan §3.

⚠ Patch on every push. A version that only moves on a release cannot answer
"did the container actually take the new image", which is the question the badge
exists for.

    python scripts/bump-version.py           # 0.1.0 -> 0.1.1
    python scripts/bump-version.py --minor   # 0.1.4 -> 0.2.0
    python scripts/bump-version.py --major   # 0.2.7 -> 1.0.0

⚠ --minor and --major are for when Sarel asks, never a judgement call made here.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

VERSION_FILE = pathlib.Path(__file__).resolve().parent.parent / "apps" / "core" / "version.py"
PATTERN = re.compile(r'^VERSION = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--minor", action="store_true", help="ask Sarel first")
    group.add_argument("--major", action="store_true", help="ask Sarel first")
    args = parser.parse_args()

    source = VERSION_FILE.read_text(encoding="utf-8")
    match = PATTERN.search(source)
    if match is None:
        print(f"error: no 'VERSION = \"x.y.z\"' line in {VERSION_FILE}", file=sys.stderr)
        return 1

    major, minor, patch = (int(part) for part in match.groups())
    if args.major:
        major, minor, patch = major + 1, 0, 0
    elif args.minor:
        minor, patch = minor + 1, 0
    else:
        patch += 1

    new = f"{major}.{minor}.{patch}"
    VERSION_FILE.write_text(PATTERN.sub(f'VERSION = "{new}"', source, count=1), encoding="utf-8")
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
