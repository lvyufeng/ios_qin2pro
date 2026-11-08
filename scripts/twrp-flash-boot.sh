#!/bin/bash
# Flash the boot partition from TWRP, where we have a root shell.
#
# U-Boot's fastboot 'flash' never answers and never writes on this unit, but
# TWRP gives us /dev/block/by-name/boot and a working dd, so the write path is
# direct and, more importantly, verifiable: this script reads the partition back
# and compares it with the file it wrote.
#
#   scripts/twrp-flash-boot.sh --backup          # save the current boot image
#   scripts/twrp-flash-boot.sh --image IMG       # write IMG (backs up first)
#   scripts/twrp-flash-boot.sh --image IMG --reboot
#
# The backup is pulled to build/twrp/boot-backup-<timestamp>.img before anything
# is written, and the write is refused if the backup cannot be read back.

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADB="${ADB:-$HOME/Android/sdk/platform-tools/adb}"
IMAGE=""
DO_BACKUP=0
DO_REBOOT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --image) IMAGE="${2:-}"; shift 2 ;;
        --backup) DO_BACKUP=1; shift ;;
        --reboot) DO_REBOOT=1; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

say "checking the phone is in TWRP with root"
"$ADB" devices | grep -q "device$" || die "no adb device; boot the phone into TWRP first"
UID_LINE=$("$ADB" shell id 2>/dev/null | tr -d '\r')
echo "    id: $UID_LINE"
case "$UID_LINE" in
    *"uid=0"*) ;;
    *) die "adb shell is not root; TWRP normally is, so this may be Android, not recovery" ;;
esac

BOOT_PART=$("$ADB" shell 'ls -l /dev/block/by-name/boot 2>/dev/null || ls -l /dev/block/bootdevice/by-name/boot 2>/dev/null' | tr -d '\r')
[ -n "$BOOT_PART" ] || die "cannot find the boot partition by name"
echo "    $BOOT_PART"
DEV=$(echo "$BOOT_PART" | sed 's/.*-> //')
[ -b "$DEV" ] 2>/dev/null || true
case "$DEV" in
    /dev/block/*) ;;
    *) die "unexpected block device: $DEV" ;;
esac

mkdir -p "$ROOT/build/twrp"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$ROOT/build/twrp/boot-backup-$STAMP.img"

say "reading the current boot partition (this is the rollback image)"
"$ADB" shell "dd if=$DEV of=/sdcard/boot-backup-$STAMP.img bs=1M" 2>&1 | tail -2
"$ADB" pull "/sdcard/boot-backup-$STAMP.img" "$BACKUP" 2>&1 | tail -1
[ -s "$BACKUP" ] || die "the backup came back empty; refusing to continue"
echo "    saved $BACKUP ($(wc -c < "$BACKUP") bytes)"
echo "    sha256 $(shasum -a 256 "$BACKUP" | cut -d' ' -f1)"

[ "$DO_BACKUP" = 1 ] && [ -z "$IMAGE" ] && exit 0
[ -n "$IMAGE" ] || die "--image is required (or use --backup on its own)"
[ -f "$IMAGE" ] || die "no such image: $IMAGE"

say "pushing $IMAGE"
"$ADB" push "$IMAGE" "/sdcard/boot-new.img" 2>&1 | tail -1
IMAGE_BYTES=$(wc -c < "$IMAGE")

say "writing $IMAGE_BYTES bytes to $DEV"
"$ADB" shell "dd if=/sdcard/boot-new.img of=$DEV bs=1M conv=notrunc" 2>&1 | tail -2
"$ADB" shell "sync"

say "reading it back to prove the write landed"
"$ADB" shell "dd if=$DEV bs=1M count=$(( (IMAGE_BYTES + 1048575) / 1048576 )) of=/sdcard/boot-check.img" 2>&1 | tail -1
"$ADB" pull "/sdcard/boot-check.img" "$ROOT/build/twrp/boot-check.img" 2>&1 | tail -1
LOCAL_SHA=$(shasum -a 256 "$IMAGE" | cut -d' ' -f1)
CHECK_SHA=$(dd if="$ROOT/build/twrp/boot-check.img" bs=1 count="$IMAGE_BYTES" 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
echo "    wrote   $LOCAL_SHA"
echo "    readback $CHECK_SHA"
if [ "$LOCAL_SHA" != "$CHECK_SHA" ]; then
    say "MISMATCH - restoring the backup"
    "$ADB" push "$BACKUP" /sdcard/boot-restore.img >/dev/null 2>&1
    "$ADB" shell "dd if=/sdcard/boot-restore.img of=$DEV bs=1M conv=notrunc && sync" 2>&1 | tail -1
    die "write did not verify; original restored"
fi
echo "    verified"

say "cleaning up temporary files on the phone"
"$ADB" shell "rm -f /sdcard/boot-new.img /sdcard/boot-check.img"

if [ "$DO_REBOOT" = 1 ]; then
    say "rebooting"
    "$ADB" reboot
fi

cat <<MSG

Rollback, if the phone does not come back:
  boot TWRP, then
  adb push $BACKUP /sdcard/boot-restore.img
  adb shell 'dd if=/sdcard/boot-restore.img of=$DEV bs=1M conv=notrunc && sync'
MSG