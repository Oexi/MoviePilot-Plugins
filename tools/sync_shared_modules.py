#!/usr/bin/env python3
"""Synchronize repository-canonical pure/shared plugin modules.

MoviePilot plugins are packaged independently, so runtime cross-plugin imports
are deliberately avoided.  Instead, canonical repository copies are mirrored
into each plugin directory and CI verifies that they have not drifted.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIRRORS = {
    ROOT / "shared" / "_host_compat.py": (
        ROOT / "plugins.v3" / "jackettextend" / "_host_compat.py",
        ROOT / "plugins.v3" / "prowlarrextend" / "_host_compat.py",
    ),
    ROOT / "shared" / "_site_registry.py": (
        ROOT / "plugins.v3" / "jackettextend" / "_site_registry.py",
        ROOT / "plugins.v3" / "prowlarrextend" / "_site_registry.py",
    ),
    ROOT / "shared" / "_torznab_core.py": (
        ROOT / "plugins.v3" / "jackettextend" / "_torznab_core.py",
        ROOT / "plugins.v3" / "prowlarrextend" / "_torznab_core.py",
    ),
}


def _check() -> list[str]:
    mismatches: list[str] = []
    for source, destinations in MIRRORS.items():
        source_bytes = source.read_bytes()
        for destination in destinations:
            if not destination.exists() or destination.read_bytes() != source_bytes:
                mismatches.append(f"{destination.relative_to(ROOT)} != {source.relative_to(ROOT)}")
    return mismatches


def _sync() -> None:
    for source, destinations in MIRRORS.items():
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify mirrors; exit non-zero on drift",
    )
    args = parser.parse_args()

    if args.check:
        mismatches = _check()
        if mismatches:
            for mismatch in mismatches:
                print(mismatch)
            return 1
        return 0

    _sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
