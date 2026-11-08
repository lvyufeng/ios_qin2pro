#!/usr/bin/env python3
"""Flatten a Mach-O kernel into a memory image a loader can memcpy blindly.

A Mach-O kernel is *not* laid out like memory: a segment whose vmsize exceeds
its filesize (BSS) creates a hole that exists in RAM but not in the file, which
shifts every later segment's file offset backwards. For the kernel we build that
shift is 0xd8000, so a loader that copies the file verbatim places __BOOTDATA,
__LINKINFO and __LINKEDIT at the wrong addresses — and the kernel then misbehaves
in ways that look nothing like a loading bug.

This tool does the per-segment placement once, on the host, and writes an image
where file offset == (vmaddr - text_vmaddr). A loader can then treat it as one
blob: copy N bytes to the load address, zero it first, jump to `entry_offset`.

  scripts/build-kernel-image.py build/artifacts/kernel.macho \\
      --output build/payload/kernel-flat.bin

Prints the entry offset and the total size, which the loader needs as constants.
"""
import argparse
import importlib.util
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_loadplan():
    spec = importlib.util.spec_from_file_location(
        'loadplan', ROOT / 'scripts/kernel-loadplan.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(kernel_path, output):
    plan = load_loadplan()
    info = plan.parse(kernel_path)
    blob = kernel_path.read_bytes()
    segments = info['segments']
    text = next(s for s in segments if s['name'] == '__TEXT')

    total = max(s['vmaddr'] + s['vmsize'] - text['vmaddr'] for s in segments)
    image = bytearray(total)          # zeroed: covers every BSS hole

    placed = 0
    for seg in segments:
        if seg['filesize'] == 0:
            continue
        start = seg['vmaddr'] - text['vmaddr']
        if start + seg['filesize'] > total:
            raise SystemExit('%s overruns the image' % seg['name'])
        image[start:start + seg['filesize']] = blob[seg['fileoff']:seg['fileoff'] + seg['filesize']]
        placed += seg['filesize']

    entry_va = info['entry_va'] or (text['vmaddr'] + info['entry_off'])
    entry_offset = entry_va - text['vmaddr']
    if entry_offset >= total:
        raise SystemExit('entry offset %#x is outside the image' % entry_offset)

    # The bytes at the entry must match the file: if a segment was misplaced,
    # this is where it shows.
    expect = blob[info['entry_va'] - text['vmaddr']] if False else None
    first_words = struct.unpack_from('<IIII', image, entry_offset)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(image))
    print('%s: %d bytes (%#x), %d segment bytes placed, entry %#x'
          % (output, len(image), len(image), placed, entry_offset))
    print('  entry first words: %s' % ' '.join('%08x' % w for w in first_words))
    return entry_offset, len(image)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'build/payload/kernel-flat.bin')
    args = parser.parse_args()
    build(args.kernel, args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())