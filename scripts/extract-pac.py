#!/usr/bin/env python3
"""List or extract partitions from a Spreadtrum .pac firmware file.

Layout (measured on Qin2pro_s9863a1h10_v1.1.0.pac; see
docs/firmware-extraction.md):

  0x000  global header
           0x030  uint32 checksum
           0x034  product name   (UTF-16LE, 256 chars)
           0x234  firmware name  (UTF-16LE, 256 chars)
           0x434  uint32 partition count
           0x438  uint32 offset of the first partition entry
  ...    partition entries, each as long as its own leading uint32:
           0x000  uint32 entry length
           0x004  partition name (UTF-16LE, 256 chars)
           0x204  file name      (UTF-16LE, 512 chars)
           0x604  uint32 payload size
           0x614  uint32 payload offset inside the .pac

Usage:
  extract-pac.py list    FIRMWARE.pac
  extract-pac.py extract FIRMWARE.pac PARTITION OUTPUT
"""
import argparse
import struct
import sys
from pathlib import Path

COUNT_OFFSET = 0x434
LIST_OFFSET = 0x438
NAME_OFFSET = 0x004
FILE_OFFSET = 0x204
SIZE_OFFSET = 0x604
DATA_OFFSET = 0x610
ENTRY_SCAN = 0x620


def utf16(raw):
    return raw.decode('utf-16-le', 'replace').split('\x00', 1)[0]


def entries(path):
    with path.open('rb') as firmware:
        header = firmware.read(LIST_OFFSET + 4)
        count = struct.unpack_from('<I', header, COUNT_OFFSET)[0]
        position = struct.unpack_from('<I', header, LIST_OFFSET)[0]
        if count > 4096:
            raise SystemExit('implausible partition count %d; not a .pac?' % count)
        for _ in range(count):
            firmware.seek(position)
            block = firmware.read(ENTRY_SCAN)
            length = struct.unpack_from('<I', block, 0)[0]
            if length < ENTRY_SCAN:
                raise SystemExit('entry at 0x%x has length %d' % (position, length))
            yield {
                'partition': utf16(block[NAME_OFFSET:NAME_OFFSET + 512]),
                'file': utf16(block[FILE_OFFSET:FILE_OFFSET + 1024]),
                'size': struct.unpack_from('<I', block, SIZE_OFFSET)[0],
                'offset': struct.unpack_from('<I', block, DATA_OFFSET)[0],
            }
            position += length


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('list', 'extract'))
    parser.add_argument('firmware', type=Path)
    parser.add_argument('partition', nargs='?')
    parser.add_argument('output', nargs='?', type=Path)
    args = parser.parse_args()

    if not args.firmware.is_file():
        raise SystemExit('%s is not a file' % args.firmware)

    if args.command == 'list':
        for entry in entries(args.firmware):
            print('%-18s %-28s size=%-10d @0x%08x'
                  % (entry['partition'], entry['file'], entry['size'], entry['offset']))
        return

    if not args.partition or not args.output:
        raise SystemExit('extract needs PARTITION and OUTPUT')
    wanted = {e['partition'].lower(): e for e in entries(args.firmware)}
    entry = wanted.get(args.partition.lower())
    if entry is None:
        print('no such partition; available: %s'
              % ', '.join(sorted(e['partition'] for e in wanted.values())), file=sys.stderr)
        raise SystemExit(1)
    with args.firmware.open('rb') as firmware:
        firmware.seek(entry['offset'])
        payload = firmware.read(entry['size'])
    if len(payload) != entry['size']:
        raise SystemExit('short read: %d of %d bytes' % (len(payload), entry['size']))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print('%s: %d bytes -> %s' % (entry['partition'], len(payload), args.output))


if __name__ == '__main__':
    main()