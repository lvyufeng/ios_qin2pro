#!/usr/bin/env python3
"""Place the device's XNU patches where the format-1 kernel build applies them.

The kernel build (scripts/build-xnu-4k-baseline.sh) applies `patches/xnu/*.patch`.
A device repository contributes its patches as its own module directory
(`patches/<name>/`), which that glob never reads, and renaming the module to
`xnu` would replace UniXNU's base patch series entirely (the device repository
is scanned first and module names are first-wins).

So this runs as the first archive-plan step and copies this device's patches
into the tree's `patches/xnu/`. That directory is a symlink to the UniXNU
checkout, so the copy lands there — one file, untracked, and removed with the
checkout. Move to `schema_version = 2` (`[kernel] patch_series` +
`device:patches/xnu/series`) to drop this step entirely.
"""
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / 'patches' / 'sc9863a').glob('*.patch'))
TARGET = ROOT / 'patches' / 'xnu'


def main():
    if not SOURCES:
        raise SystemExit('no device patches under patches/sc9863a')
    if not TARGET.is_dir():
        raise SystemExit(f'{TARGET} is not a directory')
    for patch in SOURCES:
        destination = TARGET / patch.name
        if destination.exists() and filecmp.cmp(patch, destination, shallow=False):
            print(f'up to date: {destination.name}')
            continue
        shutil.copy2(patch, destination)
        print(f'installed: {destination.name}')


if __name__ == '__main__':
    main()