#!/usr/bin/env python3
"""Check an Apple Device Tree against what XNU's ArmDTPlatform requires.

The requirements come from UniXNU's platform expert,
`drivers/ArmDTPlatform/ArmDTPlatform.cpp`:

  configure()          -> /cpus, /arm-io, /defaults and /options must exist,
                          otherwise "required ... node is absent" (lines 126-141)
  hasNVRAMProxy()      -> /chosen must carry nvram-bank-size (exactly 4 bytes)
                          and nvram-proxy-data (non-empty) (lines 60-71)
  CPU nubs             -> a child with device_type == "cpu" (lines 172-174)
  createDeviceNubs()   -> /arm-io children are published as nubs, so the
                          interrupt controller has to be one of them (75-103)

Usage: scripts/check-adt.py build/adt.dtb
Exits non-zero when a requirement is missing, so it can gate a build.
"""
import argparse
import struct
import sys
from pathlib import Path

CPU_TYPE = b'cpu\0'


def read_node(buf, offset):
    if offset + 8 > len(buf):
        raise ValueError('truncated node header at 0x%x' % offset)
    count, children = struct.unpack_from('<II', buf, offset)
    if count > 4096 or children > 4096:
        raise ValueError('implausible counts at 0x%x' % offset)
    offset += 8
    properties = {}
    for _ in range(count):
        name, length = struct.unpack_from('<32sI', buf, offset)
        offset += 36
        key = name.split(b'\0', 1)[0].decode('ascii', 'replace')
        properties[key] = buf[offset:offset + length]
        offset += length + (-length % 4)
    kids = []
    for _ in range(children):
        child, offset = read_node(buf, offset)
        kids.append(child)
    return {'properties': properties, 'children': kids}, offset


def node_name(node):
    return node['properties'].get('name', b'').split(b'\0', 1)[0].decode('ascii', 'replace')


def child(node, name):
    for kid in node['children']:
        if node_name(kid) == name:
            return kid
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('adt', type=Path)
    args = parser.parse_args()

    buf = args.adt.read_bytes()
    tree, end = read_node(buf, 0)
    if end != len(buf):
        print('warning: %d trailing bytes' % (len(buf) - end), file=sys.stderr)

    problems = []

    def need(condition, message):
        print(('  ok   ' if condition else '  FAIL ') + message)
        if not condition:
            problems.append(message)

    print('%s (%d bytes)' % (args.adt, len(buf)))
    need(node_name(tree) == 'device-tree',
         'root node is named "device-tree" (got %r)' % node_name(tree))

    for name in ('cpus', 'arm-io', 'defaults', 'options'):
        node = child(tree, name)
        need(node is not None, '/%s exists' % name)
        if name == 'cpus' and node is not None:
            cpus = [k for k in node['children']
                    if k['properties'].get('device_type') == CPU_TYPE]
            need(len(cpus) > 0, '/cpus has %d cpu entries (device_type "cpu")' % len(cpus))
        if name == 'arm-io' and node is not None:
            names = [node_name(k) for k in node['children']]
            need('intc' in names, '/arm-io publishes an intc nub (%s)' % ', '.join(names))

    # ArmCpu::probe() wants device_type exactly "cpu" and a reg whose first
    # 32 bits are the MPIDR (ArmCpu.cpp lines 238-247).
    cpus = child(tree, 'cpus')
    if cpus is not None:
        for kid in cpus['children']:
            kind = kid['properties'].get('device_type')
            reg = kid['properties'].get('reg')
            need(kind == CPU_TYPE and reg is not None and len(reg) >= 4,
                 '%s has device_type "cpu" and a 32-bit id in reg' % node_name(kid))
        # pe_identify_machine() only reads timebase-frequency from a CPU whose
        # state is "running" (pe_identify_machine.c lines 63-70); with none the
        # clock info keeps its defaults and timing is wrong from the start.
        running = [k for k in cpus['children']
                   if k['properties'].get('state', b'').split(b'\0', 1)[0] == b'running']
        need(len(running) > 0, '/cpus has a cpu with state "running"')
        timed = [k for k in running
                 if len(k['properties'].get('timebase-frequency', b'')) in (4, 8)]
        need(len(timed) > 0, 'a running cpu carries timebase-frequency')

    # ArmCpu::mapGic() is byte-exact about these (ArmCpu.cpp lines 264-282).
    arm_io = child(tree, 'arm-io')
    if arm_io is not None:
        # pe_arm_get_soc_base_phys() runs strlcpy on this property without
        # checking the lookup succeeded (pe_identify_machine.c lines 163-181),
        # so a missing device_type reads through an uninitialised pointer long
        # before any console exists.
        need(arm_io['properties'].get('device_type') is not None,
             '/arm-io has device_type (else pe_arm_get_soc_base_phys() crashes)')
        ranges = arm_io['properties'].get('ranges')
        need(ranges is not None and len(ranges) == 24,
             '/arm-io/ranges is exactly 24 bytes (bus base, parent base, size)')
        intc = child(arm_io, 'intc')
        need(intc is not None, '/arm-io/intc exists')
        if intc is not None:
            reg = intc['properties'].get('reg')
            compatible = intc['properties'].get('compatible')
            stride = intc['properties'].get('redistributor-stride')
            need(reg is not None and len(reg) == 32,
                 'intc/reg is exactly 32 bytes (GICD addr, size, GICR addr, size)')
            need(compatible == b'arm,gic-v3\0',
                 'intc/compatible is exactly "arm,gic-v3\\0" (11 bytes)')
            stride_value = struct.unpack('<Q', stride)[0] if stride and len(stride) == 8 else None
            need(stride_value in (0x20000, 0x30000, 0x40000),
                 'intc/redistributor-stride is 0x20000, 0x30000 or 0x40000')
            if ranges is not None and len(ranges) == 24 and reg is not None and len(reg) == 32:
                bus = struct.unpack('<QQQ', ranges)
                regions = struct.unpack('<QQQQ', reg)
                for base, size in ((regions[0], regions[1]), (regions[2], regions[3])):
                    need(bus[0] <= base and base - bus[0] <= bus[2] and
                         size <= bus[2] - (base - bus[0]),
                         'region 0x%x+0x%x fits the bus window 0x%x+0x%x'
                         % (base, size, bus[0], bus[2]))
                print('       GICD physical 0x%08x, GICR physical 0x%08x'
                      % (bus[1] + regions[0] - bus[0], bus[1] + regions[2] - bus[0]))

    # ArmCpu reads the PSCI conduit from /psci/method (ArmCpu.cpp line 532).
    psci = child(tree, 'psci')
    need(psci is not None and psci['properties'].get('method') is not None,
         '/psci declares a method ("smc")')

    chosen = child(tree, 'chosen')
    need(chosen is not None, '/chosen exists')
    if chosen is not None:
        bank = chosen['properties'].get('nvram-bank-size')
        proxy = chosen['properties'].get('nvram-proxy-data')
        need(bank is not None and len(bank) == 4,
             'chosen/nvram-bank-size is a 4-byte value')
        need(bool(proxy), 'chosen/nvram-proxy-data is non-empty')

    # The owned-memory window is measured from dram-base and must clear the
    # firmware carve-outs in this board's device tree, or XNU will hand modem or
    # pstore memory to the VM as free pages.
    # The window starts at chosen/dram-base, which DarwinBoot rewrites to its
    # actual allocation, so read it rather than assuming DRAM starts at 2 GiB.
    dram_base_prop = chosen['properties'].get('dram-base') if chosen is not None else None
    need(dram_base_prop is not None and len(dram_base_prop) == 8,
         'chosen/dram-base is a 64-bit value')
    dram_base = struct.unpack('<Q', dram_base_prop)[0] if dram_base_prop and len(dram_base_prop) == 8 else 0
    reserved = [
        (0x877C0000, 0x87800000, 'pstore'), (0x87800000, 0x88000000, 'sipc-mem'),
        (0x88000000, 0x88300000, 'wcn-mem'), (0x88300000, 0x8845A800, 'gnss-mem'),
        (0x89600000, 0x8EE50000, 'cp-modem'), (0x94000000, 0x94020000, 'sml-mem'),
        (0x94020000, 0x96020000, 'tos-mem'), (0x96000000, 0x96100000, 'sh-mem'),
        (0x98100000, 0x9C100000, 'iq-mem'), (0x9C600000, 0x9D000000, 'camera'),
        (0x9D000000, 0x9F000000, 'framebuffer'),
    ]
    owned = chosen['properties'].get('darwinboot-memory-size') if chosen is not None else None
    need(owned is not None and len(owned) == 8,
         'chosen/darwinboot-memory-size is a 64-bit value')
    if owned is not None and len(owned) == 8:
        size = struct.unpack('<Q', owned)[0]
        clashes = [name for start, end, name in reserved
                   if dram_base < end and start < dram_base + size]
        need(not clashes,
             'owned window %#x+%#x clears firmware carve-outs%s'
             % (dram_base, size, (' (hits %s)' % ', '.join(clashes)) if clashes else ''))

    if problems:
        print('\n%d requirement(s) missing: this tree is not ready for XNU '
              '(see the FAIL lines above).' % len(problems))
        return 1
    print('\nall requirements satisfied')
    return 0


if __name__ == '__main__':
    sys.exit(main())