#!/usr/bin/env python3
"""A minimal fastboot client on top of libusb, for hosts whose `fastboot` cannot
open the device.

Android platform-tools' fastboot does not see the QIN 2 Pro, but the device does
present a standard fastboot interface (class 0xff / subclass 0x42 / protocol 0x03)
with unusual bulk endpoints (0x06 out, 0x85 in). This talks to it directly.

Needs a libusb-1.0 shared library. Build one from source with Xcode when there is
no Homebrew:

  git clone --depth 1 -b v1.0.27 https://github.com/libusb/libusb
  xcodebuild -project libusb/Xcode/libusb.xcodeproj -target libusb \\
      -configuration Release build CODE_SIGNING_ALLOWED=NO \\
      CONFIGURATION_BUILD_DIR=$PWD/libusb-out

Then point FASTBOOT_LITE_LIBUSB at the resulting libusb-1.0.0.dylib, or pass
--libusb.

Commands:
  list                     enumerate USB devices and identify the fastboot one
  getvar VAR               send `getvar:VAR` and print the answer
  flash PARTITION IMAGE    download IMAGE and write it to PARTITION (needs --yes)

There is no `boot` command on purpose: this device's bootloader does not
implement it (see docs/bootloader-analysis.md), so `fastboot boot` always
fails with "unknown command". Writing flash is the only way to run our images.
"""
import argparse
import ctypes as C
import os
import sys
import time

DEFAULT_LIB = '/tmp/libusb/out/libusb-1.0.0.dylib'
FASTBOOT_VID_PID = (0x18D1, 0x0FFF)
FASTBOOT_IFACE = (0xFF, 0x42, 0x03)


class DeviceDescriptor(C.Structure):
    _fields_ = [('bLength', C.c_uint8), ('bDescriptorType', C.c_uint8), ('bcdUSB', C.c_uint16),
                ('bDeviceClass', C.c_uint8), ('bDeviceSubClass', C.c_uint8),
                ('bDeviceProtocol', C.c_uint8), ('bMaxPacketSize0', C.c_uint8),
                ('idVendor', C.c_uint16), ('idProduct', C.c_uint16), ('bcdDevice', C.c_uint16),
                ('iManufacturer', C.c_uint8), ('iProduct', C.c_uint8),
                ('iSerialNumber', C.c_uint8), ('bNumConfigurations', C.c_uint8)]


class Usb:
    """Thin libusb binding: only what the fastboot protocol needs."""

    def __init__(self, path):
        self.lib = C.CDLL(path)
        lib = self.lib
        signatures = {
            'libusb_init': ([C.POINTER(C.c_void_p)], C.c_int),
            'libusb_get_device_list': ([C.c_void_p, C.POINTER(C.POINTER(C.c_void_p))], C.c_ssize_t),
            'libusb_get_device_descriptor': ([C.c_void_p, C.POINTER(DeviceDescriptor)], C.c_int),
            'libusb_open': ([C.c_void_p, C.POINTER(C.c_void_p)], C.c_int),
            'libusb_claim_interface': ([C.c_void_p, C.c_int], C.c_int),
            'libusb_release_interface': ([C.c_void_p, C.c_int], C.c_int),
            'libusb_clear_halt': ([C.c_void_p, C.c_ubyte], C.c_int),
            'libusb_control_transfer': ([C.c_void_p, C.c_uint8, C.c_uint8, C.c_uint16,
                                         C.c_uint16, C.POINTER(C.c_ubyte), C.c_uint16, C.c_uint],
                                        C.c_int),
            'libusb_bulk_transfer': ([C.c_void_p, C.c_ubyte, C.POINTER(C.c_ubyte),
                                      C.c_int, C.POINTER(C.c_int), C.c_uint], C.c_int),
            'libusb_strerror': ([C.c_int], C.c_char_p),
            'libusb_close': ([C.c_void_p], None),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(lib, name)
            function.argtypes = argtypes
            function.restype = restype
        self.ctx = C.c_void_p()
        self._check(lib.libusb_init(C.byref(self.ctx)), 'libusb_init')
        self.devices = None

    def _check(self, rc, what):
        if rc < 0:
            raise RuntimeError('%s: %s' % (what, self.lib.libusb_strerror(rc).decode()))

    def enumerate(self):
        devs = C.POINTER(C.c_void_p)()
        count = self.lib.libusb_get_device_list(self.ctx, C.byref(devs))
        self._check(count, 'libusb_get_device_list')
        self.devices = devs
        found = []
        for i in range(count):
            desc = DeviceDescriptor()
            if self.lib.libusb_get_device_descriptor(devs[i], C.byref(desc)) != 0:
                continue
            found.append((devs[i], desc))
        return found

    def open(self, device):
        handle = C.c_void_p()
        self._check(self.lib.libusb_open(device, C.byref(handle)), 'libusb_open')
        return handle

    def config_descriptor(self, handle, length=1024):
        """Fetch the raw configuration descriptor; parse it yourself."""
        buf = (C.c_ubyte * length)()
        got = self.lib.libusb_control_transfer(handle, 0x80, 0x06, 0x0200, 0, buf, length, 3000)
        self._check(got, 'control transfer (GET_DESCRIPTOR)')
        return bytes(buf[:got])

    def bulk(self, handle, endpoint, data, timeout=3000):
        """Write `data`. For reads use read(): a zero-length IN transfer is not
        a valid request and macOS answers it with a stall."""
        buf = (C.c_ubyte * max(len(data), 1))()
        for index, byte in enumerate(data):
            buf[index] = byte
        got = C.c_int(0)
        rc = self.lib.libusb_bulk_transfer(handle, endpoint, buf, len(data), C.byref(got), timeout)
        return rc, bytes(buf[:got.value])

    def read(self, handle, endpoint, size=1024, timeout=3000):
        """Read up to `size` bytes from an IN endpoint."""
        buf = (C.c_ubyte * size)()
        got = C.c_int(0)
        rc = self.lib.libusb_bulk_transfer(handle, endpoint, buf, size, C.byref(got), timeout)
        return rc, bytes(buf[:got.value])


def parse_descriptors(raw):
    interfaces, endpoints = [], []
    index = 0
    while index + 2 <= len(raw):
        length, kind = raw[index], raw[index + 1]
        if length < 2:
            break
        blob = raw[index:index + length]
        if kind == 0x04 and len(blob) >= 9:
            interfaces.append({'number': blob[2], 'alternate': blob[3], 'endpoints': blob[4],
                               'class': blob[5], 'subclass': blob[6], 'protocol': blob[7]})
        elif kind == 0x05 and len(blob) >= 7:
            endpoints.append({'address': blob[2], 'attributes': blob[3] & 0x03,
                              'max_packet': blob[4] | (blob[5] << 8)})
        index += length
    return interfaces, endpoints


class Fastboot:
    def __init__(self, usb, handle, in_endpoint, out_endpoint):
        self.usb = usb
        self.handle = handle
        self.in_endpoint = in_endpoint
        self.out_endpoint = out_endpoint

    def _write(self, payload, timeout=3000):
        rc, _ = self.usb.bulk(self.handle, self.out_endpoint, payload, timeout)
        return rc

    def _read(self, timeout=4000):
        return self.usb.read(self.handle, self.in_endpoint, 1024, timeout)

    def response(self):
        """Read one fastboot reply.

        Android hosts expect STATUS + four hex digits of length + payload, but
        U-Boot 2015.07 sends STATUS + payload with no length prefix (verified on
        this device: `getvar:version` answers seven bytes, "OKAY1.0"). Only
        strip a length field when it is self-consistent.
        """
        buffer = b''
        for _ in range(8):
            rc, data = self._read(timeout=4000 if not buffer else 800)
            if rc < 0:
                if not buffer:
                    return None
                break
            if not data:
                break
            buffer += data
            if len(buffer) >= 4 and buffer[:4] in (b'OKAY', b'FAIL', b'DATA', b'INFO'):
                continue
        if len(buffer) < 4:
            return None
        status = buffer[:4].decode('ascii', 'replace')
        body = buffer[4:]
        if len(body) >= 4:
            try:
                length = int(body[:4], 16)
            except ValueError:
                length = None
            if length is not None and len(body) - 4 == length:
                body = body[4:]
        if status == 'INFO':
            return self.response()
        return (status, body)

    def command(self, text):
        # The OUT transfer usually reports a timeout even though the gadget
        # received the command (observed on macOS with this U-Boot). Treat the
        # write as best-effort and let the response decide.
        self._write(text.encode())
        return self.response()

    def download(self, payload, chunk=512 * 1024):
        result = self.command('download:%08x' % len(payload))
        if not result or result[0] != 'DATA':
            return result
        offset = 0
        while offset < len(payload):
            block = payload[offset:offset + chunk]
            # macOS reports a timeout on these OUT transfers even when the
            # gadget takes the data, so do not treat rc < 0 as fatal: if the
            # data really did not arrive the gadget keeps waiting for it and
            # the final read below fails, which aborts before any flash command.
            rc = self._write(block, timeout=15000)
            if rc < 0:
                print('note: bulk write returned %d after %d bytes (continuing)'
                      % (rc, len(block)), file=sys.stderr)
            offset += len(block)
        return self.response()


def find_fastboot(usb):
    for device, desc in usb.enumerate():
        handle = usb.open(device)
        try:
            interfaces, endpoints = parse_descriptors(usb.config_descriptor(handle))
        except RuntimeError:
            usb.lib.libusb_close(handle)
            continue
        match = [i for i in interfaces
                 if (i['class'], i['subclass'], i['protocol']) == FASTBOOT_IFACE]
        if match:
            return device, desc, handle, match[0], endpoints
        usb.lib.libusb_close(handle)
    return None, None, None, None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--libusb', default=os.environ.get('FASTBOOT_LITE_LIBUSB', DEFAULT_LIB))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    getvar = sub.add_parser('getvar')
    getvar.add_argument('name')
    reboot = sub.add_parser('reboot', help='reboot the device out of fastboot')
    reboot.add_argument('target', nargs='?', default='')
    flash = sub.add_parser('flash')
    flash.add_argument('partition')
    flash.add_argument('image')
    flash.add_argument('--yes', action='store_true',
                       help='confirm writing to flash; without it nothing is sent')
    args = parser.parse_args()

    usb = Usb(args.libusb)

    if args.command == 'list':
        for device, desc in usb.enumerate():
            print('vid=%#06x pid=%#06x class=%#04x' %
                  (desc.idVendor, desc.idProduct, desc.bDeviceClass))
        return

    device, desc, handle, interface, endpoints = find_fastboot(usb)
    if handle is None:
        raise SystemExit('no fastboot interface (ff/42/03) found on the bus')

    out_endpoint = next((e['address'] for e in endpoints if not e['address'] & 0x80), None)
    in_endpoint = next((e['address'] for e in endpoints if e['address'] & 0x80), None)
    if out_endpoint is None or in_endpoint is None:
        raise SystemExit('fastboot interface has no bulk pair')

    # A gadget left mid-transfer keeps stale toggles; clear them before talking.
    usb.lib.libusb_clear_halt(handle, out_endpoint)
    usb.lib.libusb_clear_halt(handle, in_endpoint)
    rc = usb.lib.libusb_claim_interface(handle, interface['number'])
    if rc < 0:
        raise SystemExit('claim interface %d: %s'
                         % (interface['number'], usb.lib.libusb_strerror(rc).decode()))
    time.sleep(0.2)

    fb = Fastboot(usb, handle, in_endpoint, out_endpoint)

    if args.command == 'reboot':
        text = 'reboot' if not args.target else 'reboot-%s' % args.target
        print('reboot: %r' % (fb.command(text),))
        return
    print('device %#06x:%#06x interface %d endpoints out=%#04x in=%#04x'
          % (desc.idVendor, desc.idProduct, interface['number'], out_endpoint, in_endpoint),
          file=sys.stderr)

    if args.command == 'getvar':
        result = fb.command('getvar:%s' % args.name)
        if result is None:
            raise SystemExit('no response (the gadget may be wedged; power-cycle the phone)')
        status, payload = result
        print('%s: %s' % (status, payload.decode('utf-8', 'replace')))
        return

    if args.command == 'flash':
        if not args.yes:
            raise SystemExit('refusing to write flash: pass --yes to confirm')
        payload = open(args.image, 'rb').read()
        print('downloading %d bytes' % len(payload), file=sys.stderr)
        result = fb.download(payload)
        if not result or result[0] != 'OKAY':
            raise SystemExit('download failed: %r' % (result,))
        view = fb.command('getvar:max-download-size')
        if view:
            print('max-download-size: %s' % view[1].decode('ascii', 'replace'),
                  file=sys.stderr)
        result = fb.command('flash:%s' % args.partition)
        if result is None:
            raise SystemExit('no response to flash; the gadget may be wedged')
        status, body = result
        print('flash %s: %s %s'
              % (args.partition, status, body.decode('utf-8', 'replace')))
        return


if __name__ == '__main__':
    main()