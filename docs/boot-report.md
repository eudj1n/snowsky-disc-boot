# Independent boot report for the engineering image

Since combined-008 the opt-in file is `.disc/dev/boot-report` and the report
`.disc/dev/boot-report.txt` (same exact content and format as the
`DISC_WEB_BOOT_REPORT` files described below for combined-007 and earlier);
the product image carries no boot report.

The first physical engineering candidate passed writing, complete exact readback
and owner-confirmed normal reboot. Its diagnostic serial port did not appear.
A subsequent read-only stock USB-storage observation established a partitioned
exFAT card and an exact regular `DISC_WEB_USB_DEBUG` marker (28 bytes). This
eliminates a mistyped marker and establishes USB data transfer in stock mode;
it does not establish the Linux mount source, active root or native startup.
The subsequent [physical report](boot-report-observation.md) identified an exact
controller-name mismatch; a [corrected image](udc-update.md) is prepared.

## Implementation and boundaries

The revised engineering image adds `/opt/disc-web/boot-report.sh`. The USB hook
starts this script with the **stock `/bin/sh` and BusyBox**, independently of
`disc-usb-console`, then records entry and the helper's exit code in volatile
`/run/disc-usb.log`. A missing, incompatible or failing native helper therefore
does not prevent the shell reporter from running. The companion hook records
entry, opt-out/missing-executable state and the launcher return code in
`/run/disc-web-launch.log`. Background-launch success is explicitly not health.

The native helper now reports bounded readiness changes and setup failures,
including missing controller/card readiness and failed configfs operations.
Its ownership, opt-in, stop and session rules remain unchanged. All stock files
remain intact; the engineering variant now adds six objects including its
private directory. The ordinary companion variant does not include the reporter
or write to SD, although its hook also records the small volatile launch log.

The reporter takes a single snapshot after the profile-selected delay. It records:

- Report schema, USB-profile fingerprint, kernel boot ID and uptime.
- Kernel/boot arguments, root/card mount entries and MTD partition names.
- Bounded companion launch and USB-helper log excerpts.
- Companion pidfile, executable link and bounded process status.
- Listening entries for the hook's loopback service port, plus controller/gadget
  names and expected controller state.

It does not list music, catalogs or other card files, execute card contents,
request playback changes, start another companion, configure USB, or mount/unmount
anything. A listening socket or process is not `/api/health` acceptance.

## Separate SD opt-in and bounds

The USB-console marker does **not** authorize report export. A separate regular,
non-symlink file at the card root must be named `DISC_WEB_BOOT_REPORT`, containing
exactly `DISC_WEB_LOCAL_BOOT_REPORT` followed by LF (27 bytes).

The current reviewed profile selects a 45-second delay, at most 120 one-second
poll iterations, and a 16,384-byte report limit. These are profile settings, not
firmware-version branches. The delay must exceed the console startup budget;
the loader rejects invalid timing/size values. The time limit bounds polling,
not kernel I/O: an unresponsive filesystem can still stall a read/write. The
startup hook itself backgrounds the reporter and does not wait for card I/O.

A volatile directory lock allows only one attempt per boot. Export requires
exactly one supported-filesystem mount with the configured source/target, the
exact report marker, and no foreign USB gadget. These are checked again after
snapshot collection. Card loss, marker removal or a foreign gadget at that check
prevents publication. No forced stock USB takeover is introduced.

The output is `DISC_WEB_BOOT_REPORT.txt` at the card root. Existing output,
including symlinks/special files, is preserved. Noclobber creation refuses
replacement; incomplete/truncated snapshots without the final
`END_DISC_WEB_BOOT_REPORT_V1` line are not published. An I/O failure or power loss
during the final copy can still leave an incomplete output: preserve it as failed
evidence, and require the final line before accepting a report. There is no
rotation, overwrite, automatic retry or persistent logger.

Checks do not make card ownership atomic against the independent stock USB
worker. Boot with the cable disconnected and wait for reporting to finish before
enabling USB storage. The first [physical export](boot-report-observation.md)
passed this sequence; concurrent stock USB ownership remains unqualified. Absence of a report
is not proof that the native service failed: the hook, card gate or export can fail.

## Proposed physical sequence after separately authorized installation

1. Review the new candidate hash, changed source/profile pins and a fresh write /
   exact readback plan. The old write approval belongs to the previous image.
   The installer admission flag is closed again. Prior candidate installation
   must be accounted for; do not assert that the original stock rootfs is current.
2. After authorized installation and exact readback, explicitly provision only
   the report marker. The existing console marker has its own independent effect.
3. Boot normally with the cable disconnected. Wait at least two minutes after
   stock UI/card readiness before connecting and selecting USB storage.
4. Read `DISC_WEB_BOOT_REPORT.txt`, retain its hash and correlate the boot ID and
   profile fingerprint. Inspect launch failures, mount/root and controller state.
   A missing report or incomplete footer keeps the result unqualified.
5. Archive the report before an explicitly requested deletion for another run;
   remove the report marker to disable future export. Never delete an earlier
   report automatically. Native health/USB acceptance still needs live evidence.

The initial stage only prepared/tested an offline image and local marker.
Subsequent physical installation and marker provisioning are recorded below;
those actions required separate concrete authorization.

## Reproduction and validation

Firmware-free coverage lives in `tests/conformance/test_boot_report.py` and the
profile tests. It includes independent export after a simulated native exec
failure, exact/separate opt-in, wrong mount, existing outputs, symlinks/FIFO,
foreign gadget, duplicate start, bounded logs/truncation, and revocation or stock
USB arrival between snapshot and publication. GitHub Actions discovers these
without proprietary inputs and syntax-checks the shell source.

The disposable integration `tests/integration/boot_report.py` runs the **packed
script and actual stock BusyBox** in private mount/network/PID namespaces. It
uses synthetic proc/card/controller paths and makes the native helper unavailable
with a private bind mount. It requires an exported report containing exit 126,
the expected listener observation, a complete footer and duplicate preservation.
This tests the fallback independently of the MIPS helper; it does not emulate
physical USB or prove a real SD mount.

The existing packed-image boot integration separately checks companion loopback
health/disable/relaunch and the native USB helper's no-card timeout. QEMU still
changes executable matching for managed stop/idempotency; that limitation is not
silently promoted to hardware acceptance. No browser behavior changed.

```sh
bash scripts/test.sh
# Inside the existing disposable emulator container, after the offline builder:
timeout 145 unshare --mount --net --pid --fork python3 -B \
  /platform/tests/integration/boot_report.py --output /work/new-image-directory
```

All generated images, fixture captures, local markers and test output remain
ignored. Exact final artifact and validation results are recorded below.

## Recorded preparation — 2026-09-24

Final ignored artifacts: `work/deployment-boot-report-002/`. The container's
`/work/disc-deployment-boot-report-002/` holds the extracted/verified trees.
Build 001 is superseded by final boot-ID/profile binding. Both images are
100,663,296 bytes; the candidate squashfs is 81,043,456 bytes. All 3,492 stock
objects passed full content/metadata comparison and pack/extract round trip.
The separate local writer/image review passed with `flashReady: false`.

| Input/artifact | SHA-256 |
| --- | --- |
| Candidate | `18e628030998f4ec018f095d362e8f345a7a10136d32f3fbcce1339bb4d7a506` |
| Unchanged official-stock restore | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |
| Unchanged companion binary | `ed0a4334d4f7b1787c28aaf44714e57df9a9150cefe82a37be1ca54bc9c0268c` |
| Diagnostic native helper | `88e77a3a3b4147788111c5c7fdaa2e1ca473b03f38e3cc64db1c2c86a203779e` |
| Rendered shell reporter | `08b74ba315b23d363685b9e77779ad10f0355f09f4d5d7e0f64abcd0a472ef80` |
| USB profile | `c2aaf70ea148c173cf5b8aa9dadeee7817b74d6c3a14cf5feb0868deff3744d3` |

The pinned toolchain remains
`sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
The diagnostic helper is 169,944 bytes. Static MIPS ELF checks and fixture-only
binary rejection passed; runtime inputs and the separate report script remain
profile-selected without version-specific branches.

Validation:

- Full host suite: **264 Python tests, eight JavaScript tests and native C
  assertions** passed (`work/boot-report-conformance-accepted.log`).
- One additional firmware-free shutdown regression passed after integration
  exposed an interrupted HTTP body. The wait now keeps polling through partial
  JSON/body, timeout and reset errors; only connection refusal confirms the
  stopped listener (`work/boot-report-shutdown-regression.log`).
- All 14 reporter tests passed on macOS and Linux, including late marker
  revocation and foreign gadget arrival (`work/boot-report-linux-accepted.log`).
- All 12 profile tests passed on Linux, including the new bounds case.
- All 11 existing USB lifecycle tests passed against the updated MIPS fixture
  and production binary under Linux/QEMU (`work/boot-report-mips-usb-tests-final.log`).
- The final packed reporter with stock BusyBox exported a complete 778-byte
  synthetic report after native exec failure in 47.51 seconds; repeated hook
  invocation preserved it (`work/boot-report-packed-accepted.log`).
- Final packed-image companion checks passed for loopback health, disable,
  native SIGTERM/relaunch and unrelated-PID preservation. USB duplicate/stop and
  no-card timeout checks passed at 30.03 seconds. Managed stop/idempotency remain
  unqualified under QEMU's changed argv (`work/boot-report-companion-shutdown-accepted.log`).

These are local accepted runs. No hosted GitHub Actions run or new browser
verification is claimed.

A local 27-byte marker is prepared at
`work/usb-diagnostic-activation-001/DISC_WEB_BOOT_REPORT`. It was subsequently
[provisioned on the verified physical card](boot-report-observation.md) after
explicit owner authorization, byte verification and safe ejection. The original
physical installation/captures and approval
are preserved unchanged. A fresh [installation review](boot-report-installation.md)
accounts for that installed candidate. The subsequent separately authorized
[write and exact full readback](boot-report-installation-observation.md) passed.
Physical write admission is closed again. Native health/protocol acceptance
remains outstanding. The first
[physical report](boot-report-observation.md) has since confirmed native process,
loopback listener and primary-root observations and exposed a USB controller-name
mismatch. The corrected profile requires a new image; this page's recorded
artifact hashes continue to describe the image actually installed.
