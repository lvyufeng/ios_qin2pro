#!/usr/bin/env python3
"""Dump an Apple Device Tree (ADT) blob, as produced by make-*-adt.py.

The ADT is not a flattened device tree: each node is a little-endian header
(property count, child count) followed by properties padded to 4 bytes, then
the children inline. Handy when a boot fails with no console to ask.

Usage: scripts/dump-adt.py build/adt.dtb [--max-data 32]
"""
import argparse
import struct
import sys
from pathlib import Path


def read_node(buf, offset, depth, out, max_data):
    if offset + 8 > len(buf):
        raise ValueError('truncated node header at 0x%x' % offset)
    nprops, nchildren = struct.unpack_from('<II', buf, offset)
    # A node with absurd counts means the blob is not an ADT or is corrupt.
    if nprops > 4096 or nchildren > 4096:
        raise ValueError('implausible counts at 0x%x: %d props, %d children'
                         % (offset, nprops, nchildren))
    offset += 8
    props = []
    for _ in range(nprops):
        name, length = struct.unpack_from('<32sI', buf, offset)
        offset += 36
        data = buf[offset:offset + length]
        offset += length + (-length % 4)
        props.append((name.split(b'\0', 1)[0].decode('ascii', 'replace'), data))
    children = []
    for _ in range(nchildren):
        child_offset = offset
        offset = read_node(buf, offset, depth + 1, children, max_data)
        if offset == child_offset:
            raise ValueError('child made no progress at 0x%x' % child_offset)
    out.append((depth, props, children))
    return offset


def render(props, max_data):
    for name, data in props:
        if name == 'name':
            continue
        text = data.split(b'\0', 1)[0]
        if data and len(data) <= 64 and all(32 <= c < 127 or c == 0 for c in data) \
                and any(32 <= c < 127 for c in data):
            yield '%s = %r' % (name, text.decode('ascii', 'replace'))
        elif len(data) == 8:
            yield '%s = 0x%016x' % (name, struct.unpack('<Q', data)[0])
        elif len(data) == 4:
            yield '%s = 0x%08x' % (name, struct.unpack('<I', data)[0])
        else:
            yield '%s = <%d bytes> %s' % (name, len(data), data[:max_data].hex())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('--max-data', type=int, default=24)
    args = parser.parse_args()

    buf = args.path.read_bytes()
    tree = []
    end = read_node(buf, 0, 0, tree, args.max_data)
    if end != len(buf):
        print('%d trailing bytes after the tree' % (len(buf) - end), file=sys.stderr)

    def walk(nodes):
        for depth, props, children in nodes:
            # The first property is always `name`; its value is the node's name.
            raw = next((data for key, data in props if key == 'name'), b'?')
            name = raw.split(b'\0', 1)[0].decode('ascii', 'replace')
            indent = '  ' * depth
            print('%s%s' % (indent, name))
            for line in render(props, args.max_data):
                print('%s  %s' % (indent, line))
            walk(children)

    walk(tree)
    print('\n%d bytes, %.1f KiB' % (len(buf), len(buf) / 1024))


if __name__ == '__main__':
    main()