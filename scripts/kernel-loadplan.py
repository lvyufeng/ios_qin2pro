#!/usr/bin/env python3
"""Print a Mach-O kernel's load plan: what a loader must copy where.

XNU is linked at a fixed virtual base; a loader running with the MMU off has to
place each segment at `load_base + (vmaddr - text_vmaddr)` and branch to
`load_base + entry_offset`. This prints those numbers so the loader does not
have to parse Mach-O itself.

Usage:
  scripts/kernel-loadplan.py build/artifacts/kernel.macho            # inspect
  scripts/kernel-loadplan.py build/artifacts/kernel.macho --base 0x80000000
"""
import argparse
import struct
import sys
from pathlib import Path

LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 0x05
LC_MAIN = 0x80000028
CPU_TYPE_ARM64 = 0x0100000C

# arm_thread_state64: x0-x28, fp, lr, sp, pc, cpsr — pc is the 33rd 8-byte slot.
ARM64_PC_INDEX = 32


def parse(path):
    blob = path.read_bytes()
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _ = \
        struct.unpack_from('<IiiIIIII', blob, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('%s is not a 64-bit Mach-O file' % path)
    if cputype != CPU_TYPE_ARM64:
        raise SystemExit('%s is not arm64 (cputype %#x)' % (path, cputype))

    segments, entry_va, entry_off = [], None, None
    offset = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', blob, offset)
        if cmd == LC_SEGMENT_64:
            name = blob[offset + 8:offset + 24].split(b'\0', 1)[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', blob, offset + 24)
            maxprot, initprot, nsects, segflags = struct.unpack_from('<IIII', blob, offset + 56)
            segments.append({'name': name, 'vmaddr': vmaddr, 'vmsize': vmsize,
                             'fileoff': fileoff, 'filesize': filesize,
                             'initprot': initprot, 'sections': nsects})
        elif cmd == LC_UNIXTHREAD:
            state = offset + 16
            entry_va = struct.unpack_from('<Q', blob, state + ARM64_PC_INDEX * 8)[0]
        elif cmd == LC_MAIN:
            entry_off = struct.unpack_from('<Q', blob, offset + 8)[0]
        offset += cmdsize
    return {'cputype': cputype, 'filetype': filetype, 'ncmds': ncmds,
            'segments': segments, 'entry_va': entry_va, 'entry_off': entry_off,
            'bytes': len(blob)}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('--base', type=lambda x: int(x, 0),
                        help='physical address to load the image at')
    args = parser.parse_args()

    info = parse(args.kernel)
    segments = info['segments']
    text = next((s for s in segments if s['name'] == '__TEXT'), None)
    if text is None:
        raise SystemExit('no __TEXT segment')

    entry_va = info['entry_va']
    if entry_va is None:
        raise SystemExit('no LC_UNIXTHREAD entry point')
    entry_offset = entry_va - text['vmaddr']

    print('%s' % args.kernel)
    print('  %d bytes, %d load commands, filetype %d'
          % (info['bytes'], info['ncmds'], info['filetype']))
    print('  __TEXT vmaddr %#x vmsize %#x' % (text['vmaddr'], text['vmsize']))
    print('  entry VA %#x -> offset %#x from the image base' % (entry_va, entry_offset))
    if info['entry_off'] is not None:
        print('  LC_MAIN entryoff %#x' % info['entry_off'])

    print('\n  %-18s %-18s %-10s %-18s %-10s %s'
          % ('segment', 'vmaddr', 'vmsize', 'fileoff', 'filesize', 'prot'))
    total = 0
    for seg in segments:
        print('  %-18s %#018x %#010x %#018x %#010x %d'
              % (seg['name'], seg['vmaddr'], seg['vmsize'], seg['fileoff'],
                 seg['filesize'], seg['initprot']))
        total = max(total, seg['vmaddr'] + seg['vmsize'] - text['vmaddr'])

    print('\n  image footprint: %#x (%d bytes)' % (total, total))

    # A single memcpy of the file is only valid while fileoff tracks
    # (vmaddr - text_vmaddr). A segment whose vmsize exceeds its filesize opens
    # a hole that exists in memory but not in the file, shifting every later
    # fileoff backwards. Copying the whole file would then land the later
    # segments at the wrong addresses.
    shifted = [s for s in segments if s['filesize'] and s['fileoff'] != s['vmaddr'] - text['vmaddr']]
    if shifted:
        gap = text['vmaddr'] + shifted[0]['fileoff'] - shifted[0]['vmaddr']
        print('\n  WARNING: the file is NOT laid out like virtual memory.')
        print('  %d segment(s) start %#x earlier in the file than in memory, because an'
              % (len(shifted), -gap))
        print('  earlier segment has vmsize > filesize (a zero-filled hole).')
        print('  A loader MUST copy segment by segment using fileoff -> vmaddr,')
        print('  and zero the holes, or those segments land at the wrong address.')
        for s in shifted:
            print('    %-18s file %#010x -> va %#010x' % (s['name'], s['fileoff'], s['vmaddr']))

    holes = [s for s in segments if s['filesize'] and s['vmsize'] > s['filesize']]
    if holes:
        print('\n  zero-fill required (BSS, not present in the file):')
        for s in holes:
            print('    %-18s %#x bytes at va %#x'
                  % (s['name'], s['vmsize'] - s['filesize'], s['vmaddr'] + s['filesize']))
    if args.base is not None:
        print('\n  loading at %#x:' % args.base)
        for seg in segments:
            if seg['filesize'] == 0:
                continue
            print('    %-18s copy %#010x bytes from file+%#x to %#x'
                  % (seg['name'], seg['filesize'], seg['fileoff'],
                     args.base + (seg['vmaddr'] - text['vmaddr'])))
        print('    %-18s branch to %#x' % ('entry', args.base + entry_offset))
        if args.base + total > args.base + 0x1000:
            print('    region ends at %#x' % (args.base + total))
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())