#!/bin/bash
# Guided bring-up: get the phone into fastboot, check the link, optionally flash.
#
# The point is to stop hand-typing USB experiments. Every step prints what it is
# doing and what to do when it fails, and the script never resets or reconfigures
# the device: the two things that left it unresponsive during development.
#
#   scripts/bringup.sh                 # check only, writes nothing
#   scripts/bringup.sh --flash IMG     # flash boot with IMG after the check
#
# Environment: ADB, FASTBOOT (paths), FASTBOOT_LIBUSB (libusb dylib).

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-$HOME/Android/sdk/platform-tools/adb}"
LIBUSB="${FASTBOOT_LIBUSB:-/tmp/libusb/out/libusb-1.0.0.dylib}"
FLASH_IMAGE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --flash) FLASH_IMAGE="${2:-}"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

wait_for_usb() {
    local what="$1" timeout="$2" waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if [ "$what" = fastboot ]; then
            if ioreg -p IOUSB -w0 2>/dev/null | grep -qi "fastboot Gadget"; then
                return 0
            fi
        else
            if "$ADB" devices 2>/dev/null | grep -q "device$"; then
                return 0
            fi
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

say "1/5 looking for the phone in Android"
if ! "$ADB" devices 2>/dev/null | grep -q "device$"; then
    cat <<'MSG'
    No Android device. Do this, then run the script again:
      - plug the phone in
      - if it is stuck in fastboot, long-press power 10-15 s
      - accept the "Allow USB debugging" prompt on the phone
MSG
    exit 1
fi
"$ADB" devices -l | sed -n '2p'

say "2/5 rebooting into fastboot (reason: fastboot, not bootloader)"
"$ADB" reboot fastboot
if ! wait_for_usb fastboot 90; then
    cat <<'MSG'
    The phone did not enumerate as a fastboot device.

    Fall back to entering fastboot by hand: power off completely, then hold
    volume-down + power. If that also fails the bootloader may not offer
    fastboot at all on this unit.
MSG
    exit 1
fi
echo "    enumerated as fastboot Gadget"

say "3/5 read-only probe (no state changes)"
python3 "$ROOT/scripts/fastboot-lite.py" --libusb "$LIBUSB" getvar version
PROBE_STATUS=$?
if [ $PROBE_STATUS -ne 0 ]; then
    cat <<'MSG'
    The device enumerated but does not answer on its bulk endpoints.

    Observed during development: control requests (GET_DESCRIPTOR) still work
    while every bulk transfer times out, which means the gadget is enumerated
    but not servicing fastboot. Things that did NOT help and made it worse:
    libusb_reset_device, set_configuration(0), clear_halt loops.

    Do this instead:
      - unplug the cable, wait five seconds, plug it back in
      - if it stays silent, long-press power 10-15 s and start over
MSG
    exit 1
fi

say "4/5 reading max-download-size"
python3 "$ROOT/scripts/fastboot-lite.py" --libusb "$LIBUSB" getvar max-download-size

if [ -z "$FLASH_IMAGE" ]; then
    say "5/5 check only; refusing to write flash without an image"
    echo "    rerun with: scripts/bringup.sh --flash <image>"
    exit 0
fi

[ -f "$FLASH_IMAGE" ] || die "no such image: $FLASH_IMAGE"

say "5/5 flashing '$FLASH_IMAGE' to the boot partition"
cat <<'MSG'
    Before this writes anything, make sure the stock image is at hand:
      gzip -dc firmware/stock-boot.img.gz | shasum -a 256
      -> 879580dc0784e33550473a56e7feea372cb6de1e94a0b390562d21485c0c6bb1
    Recovery, if the flash does not boot: same cable, fastboot flash boot <stock>.
MSG
printf '    type "yes" to continue: '
read -r answer
[ "$answer" = yes ] || die "aborted"

python3 "$ROOT/scripts/fastboot-lite.py" --libusb "$LIBUSB" \
    flash boot "$FLASH_IMAGE" --yes