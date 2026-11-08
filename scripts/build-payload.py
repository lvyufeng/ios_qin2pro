#!/usr/bin/env python3
"""Build the bare-metal bring-up payload and wrap it in a boot image.

  build-payload.py payload --output build/payload/hello-uart.bin
  build-payload.py bootimg --payload build/payload/hello-uart.bin \\
      --stock /path/to/stock/boot.img --output build/payload/hello-uart-boot.img

The payload is assembled with clang for aarch64-none-elf. It must come out with
no relocations, because there is no aarch64 ELF linker on this host: the object's
.text is copied out verbatim and is expected to be position independent.

The boot image reuses the *stock* header — page size, addresses, cmdline — and
only replaces the kernel payload, so the bootloader sees exactly the layout it
already boots.
"""
import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'payload/hello-uart.S'
BOOT_MAGIC = b'ANDROID!'
ARM64_MAGIC = 0x644D5241


def elf64_text(blob):
    """Return the bytes of the .text section of an ELF64 little-endian object."""
    if blob[:4] != b'\x7fELF':
        raise SystemExit('not an ELF file')
    shoff = struct.unpack_from('<Q', blob, 0x28)[0]
    shentsize = struct.unpack_from('<H', blob, 0x3A)[0]
    shnum = struct.unpack_from('<H', blob, 0x3C)[0]
    shstrndx = struct.unpack_from('<H', blob, 0x3E)[0]

    def section(index):
        base = shoff + index * shentsize
        name, kind, flags, addr, offset, size = struct.unpack_from('<IIQQQQ', blob, base)
        return name, kind, flags, addr, offset, size

    _, _, _, _, str_offset, str_size = section(shstrndx)
    names = blob[str_offset:str_offset + str_size]
    for index in range(shnum):
        name, kind, flags, addr, offset, size = section(index)
        label = names[name:names.index(b'\0', name)].decode()
        if label == '.text':
            return blob[offset:offset + size]
    raise SystemExit('no .text section')


def objdump_tool():
    """llvm-objdump ships with Xcode but is not always on PATH."""
    found = shutil.which('llvm-objdump')
    if found:
        return found
    resolved = subprocess.run(['xcrun', '-f', 'llvm-objdump'],
                              capture_output=True, text=True)
    if resolved.returncode == 0 and resolved.stdout.strip():
        return resolved.stdout.strip()
    raise SystemExit('llvm-objdump not found (install Xcode command line tools)')


def relocation_count(object_path):
    result = subprocess.run([objdump_tool(), '-r', str(object_path)],
                            capture_output=True, text=True)
    output = result.stdout
    # The tool prints a header line per section only when relocations exist.
    return sum(1 for line in output.splitlines()
               if line.strip().startswith(('R_AARCH64_', 'R_ARM_', 'R_.*_')))


def build_payload(output, source=SOURCE):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        obj = Path(directory) / (source.stem + '.o')
        # cwd=ROOT so .incbin "build/adt.dtb" resolves the same everywhere.
        subprocess.run(['clang', '--target=aarch64-none-elf', '-c',
                        '-o', str(obj), str(source)], check=True, cwd=str(ROOT))
        text = elf64_text(obj.read_bytes())
        remaining = relocation_count(obj)
    if remaining:
        raise SystemExit('%d relocations left in the object; the payload would be '
                         'wrong once loaded. Keep every reference section-local.' % remaining)
    if struct.unpack_from('<I', text, 0x38)[0] != ARM64_MAGIC:
        raise SystemExit('arm64 image magic missing at offset 0x38')
    output.write_bytes(text)
    print('%s: %d bytes, no relocations' % (output, len(text)))
    return output


def read_stock_header(path, page_size=2048, whole=False):
    blob = path.read_bytes()
    if not whole:
        blob = blob[:page_size]
    if blob[:8] != BOOT_MAGIC:
        raise SystemExit('%s is not an Android boot image' % path)
    fields = struct.unpack_from('<8s10I', blob, 0)
    header = {
        'magic': fields[0], 'kernel_size': fields[1], 'kernel_addr': fields[2],
        'ramdisk_size': fields[3], 'ramdisk_addr': fields[4], 'second_size': fields[5],
        'second_addr': fields[6], 'tags_addr': fields[7], 'page_size': fields[8],
        'header_version': fields[9], 'os_version': fields[10],
        'name': blob[48:64], 'cmdline': blob[64:64 + 512], 'id': blob[576:584],
        'extra_cmdline': blob[608:608 + 1024],
    }
    if header['page_size'] != page_size:
        raise SystemExit('unexpected page size %d' % header['page_size'])
    return header, blob


def build_bootimg(payload_path, stock_path, output):
    header, stock = read_stock_header(stock_path)
    payload = payload_path.read_bytes()
    page = header['page_size']

    out = bytearray(page)
    struct.pack_into('<8s10I', out, 0, BOOT_MAGIC, len(payload), header['kernel_addr'],
                     header['ramdisk_size'], header['ramdisk_addr'], header['second_size'],
                     header['second_addr'], header['tags_addr'], page,
                     header['header_version'], header['os_version'])
    out[48:64] = header['name']
    out[64:64 + 512] = header['cmdline']
    out[608:608 + 1024] = header['extra_cmdline']

    # The Android boot image id is SHA1(kernel, kernel_size, ramdisk,
    # ramdisk_size, second, second_size), zero padded to 32 bytes. The stock
    # image's value does not match that formula (nor SHAl(kernel) alone), so it
    # was produced by the vendor's packer; carrying it over would claim our
    # kernel has the stock hash. Compute our own instead.
    digest = hashlib.sha1()
    digest.update(payload)
    digest.update(struct.pack('<I', len(payload)))
    digest.update(struct.pack('<I', header['ramdisk_size']))
    digest.update(struct.pack('<I', header['second_size']))
    out[576:608] = digest.digest() + bytes(12)

    body = bytearray(payload)
    body += bytes(-len(body) % page)
    if header['ramdisk_size']:
        raise SystemExit('stock image has a ramdisk; this packer would drop it')

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(out) + bytes(body))
    print('%s: header page %d, payload %d bytes, cmdline %r'
          % (output, page, len(payload),
             header['cmdline'].split(b'\0')[0].decode('ascii', 'replace')))
    return output


def verify_bootimg(path):
    header, blob = read_stock_header(path, 2048, whole=True)
    page = header['page_size']
    kernel = blob[page:page + header['kernel_size']]
    magic = struct.unpack_from('<I', kernel, 0x38)[0]
    print('%s: kernel_size=%d kernel_addr=%#x magic@0x38=%#010x %s'
          % (path, header['kernel_size'], header['kernel_addr'], magic,
             'OK' if magic == ARM64_MAGIC else 'BAD'))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p1 = sub.add_parser('payload')
    p1.add_argument('--output', type=Path, default=ROOT / 'build/payload/hello-uart.bin')
    p1.add_argument('--source', type=Path, default=SOURCE,
                    help='assembly file to build (default payload/hello-uart.S)')
    p2 = sub.add_parser('bootimg')
    p2.add_argument('--payload', type=Path, default=ROOT / 'build/payload/hello-uart.bin')
    p2.add_argument('--stock', type=Path, required=True)
    p2.add_argument('--output', type=Path, default=ROOT / 'build/payload/hello-uart-boot.img')
    p3 = sub.add_parser('verify')
    p3.add_argument('image', type=Path)
    args = parser.parse_args()

    if args.command == 'payload':
        build_payload(args.output, args.source)
    elif args.command == 'bootimg':
        if not args.payload.is_file():
            raise SystemExit('run the `payload` command first')
        build_bootimg(args.payload, args.stock, args.output)
        verify_bootimg(args.output)
    else:
        verify_bootimg(args.image)


if __name__ == '__main__':
    main()