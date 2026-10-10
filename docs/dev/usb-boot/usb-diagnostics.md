# Opt-in USB engineering image

The separate `usb-engineering` image adds a bounded local root console while
preserving the stock UI/player and loopback companion. It is implemented and
tested offline. The first candidate passed physical writing/exact readback and
normal owner-confirmed reboot, but diagnostic enumeration/coexistence remain
unqualified. The revised [independent boot reporter](../boot/boot-report.md) records
startup failures to SD only with a separate report marker. The ordinary `companion` image does not include this mode. Neither
image supplies an execution path merely by copying it to a stock player's SD card.

Since combined-008 the opt-in file is `.disc/dev/usb-console` on the card
(the same exact content as `DISC_WEB_USB_DEBUG` below, which images through
combined-007 read at the card root), and the product image
(`build_candidate.py --product`) carries no USB console at all. The steps
below describe the combined-007 layout as it was accepted.

## Firmware evidence

The reviewed USB profile is `firmware/usb/v2.57.json`, tied to the selected main
profile's exact rootfs hash. It supplies the card source/mount, UDC, readiness and
session budgets and stock binary/script fingerprints. Another release needs its
own reviewed USB profile; there is no implicit inheritance or runtime version
check. Current values are `/dev/mmcblk0p1`, `/tmp/sdcard`, `13500000.otg_new`, 90 seconds
and 900 seconds (the startup was 30 s until 2026-10-06: stock's player, which mounts the card,
waits up to 60 s for the boot menu's choice, and the console gave up before it). Stock also supports an unpartitioned card; this initial profile
deliberately admits only the partitioned layout.

The [first physical boot report](../observations/boot-report-observation.md) established the
`_new` controller suffix. Earlier installed images requested `13500000.otg`, so
their exact-name readiness guard timed out. The tested profile correction was
packaged in a [reviewed update](../installer/udc-update.md), which passed
[physical write, exact full readback and owner-confirmed normal UI boot](../observations/udc-installation-observation.md).
ACM enumeration/coexistence still require physical acceptance, and no fallback
to an arbitrary UDC is introduced.
The [new physical report](../observations/udc-boot-report-observation.md) confirms controller
readiness but exposes an ACM symlink-target ENOENT before binding. The helper now
uses an absolute function target, matching configfs lookup semantics regardless
of the caller's cwd. The corrected combined image passed
[physical installation and exact readback](../observations/combined-installation-observation.md);
ACM enumeration remains to be checked.

The installed combined engineering image includes that ACM correction with
SD webroot and marker-gated LAN access (snowsky-disc-web `docs/sd-webroot.md`). The USB root-console
marker and the LAN read-only marker are distinct. The native service's
engineering Wi-Fi listener does not turn the ACM console into a network shell.

Ghidra 12.1.3, with the reference repository's `FindText`, `RefsTo` and `DecAt`
helpers, analyzed pristine `mq_player` SHA-256
`a5a6740435758bb3f3d008a4306c93a463c6634318957bbf32086bd7b00fabbc`.
The private project and outputs remain under ignored `work/usb-re/`.

| Observed path in this exact binary | Consequence |
| --- | --- |
| `0x4c42b8`: card mount handling, partition then whole-device fallback | Wait for a matching `/proc/mounts` entry; a directory or fixed boot delay is insufficient |
| `0x4e5af4`: stock USB mode loop, `uac_demo` / `storage_demo`, card unmount/remount | Refuse existing gadgets; revoke our session if a stock gadget appears or the card is mounted without the marker |
| `0x4e6490`: serial worker checks byte `0x83a77e`, calls stock serial setup and serial-number service | Do not change the flag or assume the vendor service provides a root shell |
| `0x475790` / `0x475a24`: serial-number service start/stop | Use a separate fixed supervisor instead of invoking the factory service |

The serial worker entry was confirmed with assembly and its thread-creation
reference before decompilation. Earlier exploratory mid-function guesses in
`player-usb-decompile.log` were discarded; `serial-assembly.log` and
`serial-verified.log` contain the corrected evidence. These addresses are research
observations, not runtime patches. Stock `serial_config.sh` and boot files remain
unchanged. No assumption of atomic coordination with the stock USB loop is made.

## Activation and lifecycle

The following describes an **already installed engineering image**, not a stock
bootstrap or permission to connect/flash the owner's device.

1. Before boot, deliberately provision a regular file named `DISC_WEB_USB_DEBUG`
   at the root of the partitioned card, containing exactly
   `DISC_WEB_LOCAL_ROOT_CONSOLE` followed by a single LF newline. An empty file,
   arbitrary script, symlink or different content does not enable the console.
2. The optional `S99disc-usb` hook backgrounds the native supervisor. It waits
   within the profile's startup budget for the actual card mount, exact marker,
   sole expected controller and no existing gadget. If the mounted card has no
   valid marker, it exits immediately. It never runs code from the card.
3. It creates only `disc_web_debug` with one ACM function, binds the expected UDC
   and waits for `ttyGS0` and controller state `configured` within that same
   readiness budget. VID/PID `0525:a4a7` follows the inspected stock ACM setup;
   descriptor strings identify the engineering console. Host driver compatibility
   has not been tested. Cable enumeration must complete within the startup budget.
4. One fixed `/bin/sh -i` receives the terminal. A volatile `flock` prevents a
   second supervisor/rebind. There is no restart loop and no network listener.
   The console admits arbitrary local root commands; it is engineering access,
   not the read-only browser API or consumer authentication.
5. The card mounted without the marker, USB disconnect/unbind, another gadget,
   termination signal, shell exit, explicit stop or the session deadline ends the
   session. A card that is only unmounted does not (2026-10-06): stock's player
   unmounts and mounts it again at every start of the pair, which is when a session
   matters most.
   The supervisor polls every 100 ms, allows two seconds for child termination
   and one further second after SIGKILL, then releases only its own gadget.
   It never waits indefinitely for a child stuck in kernel I/O, removes stock
   gadgets or unmounts the shared configfs filesystem.

On an installed image with established diagnostic access:

```sh
/etc/init.d/S99disc-usb stop
# Remove DISC_WEB_USB_DEBUG from the card to disable the next boot too.
# Explicit start after a completed stop is possible through existing root access:
/etc/init.d/S99disc-usb start
```

The hook's stop is an asynchronous request; wait for the supervisor/gadget to
exit before starting again. Read `/run/disc-usb.log` for the outcome. A failed
startup does not fail the stock boot sequence. Removing the marker and rebooting
keeps the mode off. Starting without a card times out instead of waiting forever.

Linux parent-death signaling kills the direct shell if the supervisor dies, but
SIGKILL cannot run gadget cleanup; reboot may be necessary. Root commands can
create independent processes or change the device, and session termination does
not undo them. Stock may need its USB mode reselected after a competing bind;
the helper does not force a stock rebind. These are physical acceptance items.

## Physical check for the combined update

After the candidate image is separately authorized, written once, read back
in full and owner-confirmed to boot normally, check the console as a distinct
goal. The existing `DISC_WEB_USB_DEBUG` marker must have its exact content on
the reviewed partitioned PLAY card. Use a USB data cable connected at normal
boot; do not select USB storage or DAC mode. Within the selected startup
budget, the helper should bind the sole reviewed UDC and expose an ACM device
on the host. On macOS inspect `ioreg -p IOUSB -l -w 0` and the matching
`/dev/cu.usbmodem*` entry; open only the newly enumerated port with a terminal
such as `screen /dev/cu.usbmodemXXXX 115200`. The baud value is a host terminal
setting for USB ACM, not the stock UART rate. Retain the exact host descriptor,
port, startup log and bounded read-only commands (`id`, `/proc/cmdline`,
`/proc/self/mountinfo`, `/proc/mtd`, `/run/disc-usb.log`). Do not use console
access to change boot files or player settings during acceptance.

Then close the terminal, remove the USB marker or stop the helper, and verify
that its own gadget disappears without disturbing stock USB mode. If no ACM
port appears, inspect the fresh SD boot report and host enumeration before any
new firmware attempt. An absent device is not a reason to replay the writer.
The USB mass-storage mode temporarily owns/unmounts the card, so it cannot be
used at the same time as the console or webroot reads. The separate LAN marker
and browser checks are described in SD webroot (snowsky-disc-web `docs/sd-webroot.md`).

## Build and verification

Use the selected firmware and a fresh output directory in the disposable stack:

```sh
bash scripts/build.sh mips
DISC_CONTAINER=$(python3 -c 'import json; print(json.load(open("work/emulator.json"))["id"] + "-emu")')
docker exec -e PYTHONPATH=/repo "$DISC_CONTAINER" python3 -B \
  /platform/scripts/deployment/build_candidate.py --version 2.57 \
  --ota /ota --binary /platform/build/mips/disc-service \
  --hook /platform/device/deployment/S99disc-web \
  --usb-diagnostics-binary /platform/build/mips/disc-usb-console \
  --output /work/disc-deployment-usb
docker exec "$DISC_CONTAINER" timeout 50 unshare --mount --net --pid --fork \
  python3 -B /platform/tests/integration/deployment_boot.py \
  --output /work/disc-deployment-usb
```

The builder checks the USB profile and ELF and rejects the fixture-only binary.
The original image below had two extra objects beyond the ordinary image:
`/opt/disc-web/disc-usb-console` and `/etc/init.d/S99disc-usb`. The revised
reporting image also adds `/opt/disc-web/boot-report.sh`. All 3,492 stock
objects passed content/metadata comparison and a complete pack/extract round trip.
The offline reviewer verifies the USB profile pin and separate artifact names;
its result remains `flashReady: false`.

Recorded 2026-09-24: helper 167,804 bytes, candidate squashfs 81,039,360 bytes;
candidate and official-stock restore each 100,663,296 bytes. Private artifacts
and reports are in `work/deployment-usb/`.

```text
disc-usb-console:
afc6aa2c41aab74a5c18b75fc06a7d7ad700a2d037db41bf2bdc71e69d792a74
disc-web-v257-usb-engineering-review-only.bin:
463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2
stock-v257-restore-review-only.bin:
e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0
```

Eleven firmware-free filesystem/PTY tests exercise opt-in, actual shell I/O,
conflicts, duplicates, revocation, deadlines, signals and partial cleanup. They
run in GitHub Actions without USB, root privileges or proprietary firmware.
`DISC_USB_FIXTURE` is a separate compile-time backend; production has no fixture
path option. The same tests passed against the MIPS fixture under Linux/QEMU.
This mocks configfs and controller state; it is not a USB kernel simulation.

The packed image's stock BusyBox and **production** MIPS helper additionally
passed duplicate-start, explicit-stop and no-card timeout checks in private
namespaces without exposed USB sysfs. The no-card process exited after 30.06
seconds and never created a gadget. Companion health/disable/relaunch passed;
its pre-existing BusyBox executable-match limitation under QEMU remains open.
See validation (snowsky-disc-web `docs/validation.md`) for logs and [remaining installation work](../installer/deployment-writer-review.md).
