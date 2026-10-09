# Physical boot-report check — 2026-09-24

The boot-report candidate passed [exact full readback and owner-confirmed normal
boot](boot-report-installation-observation.md). The owner separately authorized
creation of the SD report marker and checking the resulting report.

## Marker provisioned

The player exposed stock `composite-storage` USB (`0525:a4a5`). The mounted PLAY
volume matched the previously observed volume UUID, size (30,936,170,496 bytes)
and exFAT filesystem. It was an external writable partition. Only the known
report/marker paths were inspected; music and catalogs were not enumerated.

`DISC_WEB_BOOT_REPORT` and `DISC_WEB_BOOT_REPORT.txt` were both absent. The existing
28-byte `DISC_WEB_USB_DEBUG` marker contained its expected console opt-in.
At `2026-09-24T11:00:46.353633+00:00`, the separately authorized operation created
only `DISC_WEB_BOOT_REPORT`, containing exactly `DISC_WEB_LOCAL_BOOT_REPORT` and LF.
Exclusive creation and no-follow checks prevented replacement of an existing
entry. The 27 bytes were flushed and read back exactly; SHA-256:
`204c6f2d8d83c1cedc46326b6a941191ee1b289458643adffec50d7fa7c995e1`.
The console marker was rechecked unchanged. `diskutil eject` then reported a
successful safe ejection of the observed whole disk.

Runtime evidence is ignored under `work/boot-report-export-001/`, including the
USB/volume observations, `marker-provision.json` and `eject.txt`. This is physical
marker provisioning acceptance, not proof that the boot hook or report exporter
ran. Existing synthetic marker/export checks are documented in
[boot reporting](../boot-report.md); no implementation changed in this step.

## First report acquired and reviewed

The owner confirmed normal boot with the cable disconnected and the SD card
installed, at least two minutes after stock UI/card readiness, then stock USB
storage. The same volume UUID was rechecked. Both markers retained their exact
contents. The report was copied read-only, without deleting or replacing it.

| Evidence | Observation |
| --- | --- |
| Report | 2,547 bytes, complete V1 header and footer |
| Report SHA-256 | `ae6daaa4c4fc051ff6609f64eeefcee8ffa99f0699c75c9e55de430d96155566` |
| Boot ID | `3d5ad57e-9dcf-430f-b05c-5f0029945fc6` |
| Snapshot uptime | 49.59 seconds |
| Installed USB profile | `c2aaf70ea148c173cf5b8aa9dadeee7817b74d6c3a14cf5feb0868deff3744d3` |
| Kernel | Linux 4.4.94, stock build 236, September 9, 2026 |
| Boot arguments | `root=/dev/mtdblock_bbt_ro2 rootfstype=squashfs ro` |
| Root mount | `/dev/root / squashfs ro,relatime` |
| Card mount | `/dev/mmcblk0p1 /tmp/sdcard exfat rw,...` |
| Native process | PID 1025, `/opt/disc-web/disc-service`, sleeping, parent PID 1 |
| Initial process resources | RSS 396 KiB, virtual size 1,388 KiB, six threads |
| TCP observation | `0100007F:1EBE 0A`: loopback port 7870 listening |

The profile fingerprint matches the **installed image's archived build report**,
not a later corrected profile. `/proc/mtd` identifies mtd2 as primary `rootfs`;
the observed command line matches the independently reviewed primary boot route.
The command line, read-only squashfs root mount, expected executable and listener
now provide physical boot evidence beyond the earlier owner UI report.

This is a saved boot snapshot, not an authenticated live session. Process and
listener observations do not establish socket ownership, an `/api/health` response
or a successful stock protocol round trip. Initial memory numbers are not a
resource-soak acceptance. Those checks and lifecycle/playback remain outstanding.
The raw report, owner sequence confirmation and `report-review.json` remain under
ignored `work/boot-report-export-001/`. No raw device report is committed.

## Concrete USB blocker and profile correction

The USB hook and native supervisor both ran. The log progressed from
`mounted=0 controller=0` to `mounted=1 controller=0`, then ended at the 30-second
startup deadline. The expected controller state was unavailable and no gadget
was present at the snapshot. Actual `/sys/class/udc` contained the sole name
`13500000.otg_new`; the installed profile had requested `13500000.otg`.

`controller_ready()` intentionally requires one controller with the exact
configured name. That mismatch is a sufficient readiness blocker. The cable-free
report boot cannot establish whether later ACM binding, enumeration, configured
state or shell lifecycle would work once the name is corrected.

The selected firmware USB profile now uses the observed `13500000.otg_new`.
Its new canonical SHA-256 is
`25fe6c616db3c15bdf0fcce9300bcde98922078a0362792ccbddf5f74d448b2d`.
No firmware-version branch, arbitrary controller fallback or guard bypass was
added. `physical_qualified` stays false and installer admission stays closed.
The existing physical image still contains the old name: applying this fix needs
a rebuilt/reviewed image and a separately authorized physical update. Earlier
image/review hashes cannot be reused for the changed profile.

Two synthetic native tests reproduce a sole controller with the wrong name and
verify exact binding/cleanup with the configured suffixed name. Another profile
test checks that selected names propagate into both generated scripts and their
fingerprint without source edits. These tests use generated sysfs/PTY fixtures,
not a physical USB device or proprietary firmware.

Validation passed:

- Full host conformance: C assertions, eight JavaScript tests and 281 Python
  tests (`work/boot-report-observation-conformance.log`).
- Focused host USB supervisor and profile suites: 13 tests each
  (`work/boot-report-udc-tests.log`, `work/boot-report-udc-profiles.log`).
- Linux execution of the MIPS USB supervisor fixtures and production guard,
  followed by profile tests: 13 tests each
  (`work/boot-report-udc-mips-linux.log`).

Synthetic tests remain independent of firmware, physical hardware, sibling
repositories and private credentials. No hosted GitHub Actions run is claimed.

The marker and original report remain on the card. Existing output prevents
replacement on later boots. Report deletion or marker removal is not implicit in
this check; preserve the report before any separately requested new export.
