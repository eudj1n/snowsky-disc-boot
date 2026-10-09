#!/bin/sh
# Rendered at packaging time. No code or settings are loaded from the card.
BB=/bin/busybox
SD='@SD@'
SOURCE='@SOURCE@'
UDC='@UDC@'
DELAY=@DELAY@
LIMIT=@LIMIT@
MAX_BYTES=@BYTES@
PROFILE='@PROFILE@'
RUN=/run
PROC=/proc
SYS=/sys
umask 077
# A retained volatile lock permits one attempt per boot, including duplicates.
"$BB" mkdir "$RUN/disc-boot-report.lock" 2>/dev/null || exit 0
REPORT="$RUN/disc-boot-report.lock/report.txt"
# combined-008: the engineering switch and the report live in the service's folder.
MARKER="$SD/.disc/dev/boot-report"
OUTPUT="$SD/.disc/dev/boot-report.txt"
mounted() {
    "$BB" awk -v dev="$SOURCE" -v target="$SD" '
      $1 == dev && $2 == target && $3 ~ /^(vfat|exfat|ntfs|fuseblk)$/ { n++ }
      END { exit n != 1 }' "$PROC/mounts"
}
opted_in() {
    [ -f "$MARKER" ] && [ ! -L "$MARKER" ] || return 1
    [ "$("$BB" wc -c < "$MARKER")" -eq 27 ] || return 1
    printf 'DISC_WEB_LOCAL_BOOT_REPORT\n' | "$BB" cmp -s - "$MARKER"
}
usb_safe() {
    # Even an unbound foreign gadget can become active: refuse all of them.
    for gadget in "$SYS/kernel/config/usb_gadget/"*; do
        [ -e "$gadget" ] || [ -L "$gadget" ] || continue
        [ "$gadget" = "$SYS/kernel/config/usb_gadget/disc_web_debug" ] || return 1
    done
}
section() {
    printf '\n[%s]\n' "$1"
    if [ -r "$2" ]; then "$BB" head -c "$3" "$2"; else printf 'unavailable\n'; fi
    printf '\n'
}
snapshot() {
    printf 'DISC_WEB_BOOT_REPORT_V1\n'
    printf 'usb_profile_sha256=%s\n' "$PROFILE"
    printf 'Read-only observations; process/listener presence is not health acceptance.\n'
    section boot_id "$PROC/sys/kernel/random/boot_id" 64
    section uptime "$PROC/uptime" 128
    section kernel "$PROC/version" 512
    section boot_arguments "$PROC/cmdline" 1024
    printf '\n[root_and_card_mounts]\n'
    "$BB" awk -v target="$SD" '$2 == "/" || $2 == target' "$PROC/mounts" | "$BB" head -c 1024
    section partitions "$PROC/mtd" 2048
    # The boot layer's decision and its roles (docs/contract.md, "Status").
    section boot_decision "$RUN/disc-boot/boot.json" 1024
    section boot_service "$RUN/disc-boot/service.json" 1024
    section boot_ui "$RUN/disc-boot/ui.json" 1024
    section boot_log "$RUN/disc-boot/boot.log" 2048
    section usb_launch "$RUN/disc-usb.log" 4096
    section supervisor_pid "$RUN/disc-boot/supervisor.pid" 32
    pid=$("$BB" head -c 16 "$RUN/disc-boot/supervisor.pid" 2>/dev/null)
    case "$pid" in ''|*[!0-9]*) printf 'invalid_or_missing_pid\n' ;;
      *) printf '\n[supervisor_executable]\n'; "$BB" readlink "$PROC/$pid/exe"
         section supervisor_status "$PROC/$pid/status" 2048 ;;
    esac
    printf '\n[port_7870_hex_1EBE]\n'
    "$BB" awk '$2 ~ /:1EBE$/ && $4 == "0A" { print $2, $4 }' "$PROC/net/tcp" | "$BB" head -c 256
    printf '\n[usb_controllers]\n'
    for entry in "$SYS/class/udc/"*; do
        [ -e "$entry" ] || continue
        printf '%s\n' "${entry##*/}"
    done
    section expected_controller_state "$SYS/class/udc/$UDC/state" 128
    printf '\n[gadgets]\n'
    for entry in "$SYS/kernel/config/usb_gadget/"*; do
        [ -e "$entry" ] || continue
        printf '%s\n' "${entry##*/}"
    done
    printf '\nEND_DISC_WEB_BOOT_REPORT_V1\n'
}
# Delay capture until the original USB startup deadline has expired. The wait
# for a usable card is finite; no mount, unmount, gadget mutation or replay.
i=0
while [ "$i" -lt "$LIMIT" ]; do
    if [ "$i" -ge "$DELAY" ] && mounted && opted_in && usb_safe; then
        # Bound the entire report, including unexpectedly large virtual files.
        snapshot 2>&1 | "$BB" head -c "$MAX_BYTES" > "$REPORT"
        "$BB" grep -q '^END_DISC_WEB_BOOT_REPORT_V1$' "$REPORT" || exit 1
        # Recheck immediately before opening. Never replace an earlier report,
        # symlink or special file. Creation uses the shell's noclobber mode.
        mounted && opted_in && usb_safe || exit 1
        [ ! -e "$OUTPUT" ] && [ ! -L "$OUTPUT" ] || exit 0
        (set -C; "$BB" cat "$REPORT" > "$OUTPUT") || exit 1
        printf 'report saved\n'
        exit 0
    fi
    "$BB" sleep 1
    i=$((i + 1))
done
printf 'report deadline: card, opt-in or USB ownership unavailable\n'
exit 0
