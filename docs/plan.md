# Plan

The canonical plan of snowsky-disc-boot. The contract it implements:
[boot layer contract](contract.md). Decisions and evidence from before this
repository stay in snowsky-disc-web's `docs/plan.md` (its "Boot layer" and
"Public installer" sections).

## Stage 0 — the repository (owner, 2026-10-02)

- [x] Created beside snowsky-disc-web; the boot layer's work continues here,
  the server's follows once this layer is done (owner, 2026-10-02).
- [x] Ported from snowsky-disc-web at `faaf502`, unchanged unless noted: the
  NAND and USB Boot tooling (`scripts/deployment/` without the image builder,
  `device/acquisition/`), the USB console (`device/src/usb_console.c`), the
  boot report (`device/deployment/boot-report.sh`), the firmware profiles
  the tooling reads (`firmware/` without the gateway's catalogs and the OS
  profile), their conformance tests and the documents of the installation
  and its evidence. Links to documents that stayed behind name
  snowsky-disc-web. The build keeps the console and the NAND reader's tests
  (`scripts/build.sh`); `test_firmware_profiles` lost the cases of the
  service's emulator wrapper (the image builder's and the guest selection's
  returned with the builder). Evidence: `scripts/test.sh` (216 tests, OK).
- [x] The image builder, reduced to the boot layer (`build_candidate.py`,
  variant `boot`): the stock rootfs pinned by the profile plus the boot
  layer's objects, no package inside; until the boot program exists those are
  the USB console, its hook and the boot report under the installed images'
  names (`/opt/disc-web/`, `S99disc-usb`). Added folders are derived from the
  payload; `review.py` requires the console's opt-in and no package for the
  variant. Evidence (2026-10-02): unit `test_deployment`, `test_boot_report`,
  `test_deployment_review` (the boot variant), `test_firmware_profiles`; the
  console built here is byte-identical to the installed images'
  (`62b28e1e…`); an offline build from the V2.57 OTA in the disposable
  container kept all 3,492 stock objects, added exactly four, passed the
  full squashfs round trip (packed 80,719,872 bytes, image
  `disc-boot-v257-review-only.bin` content `41a9fb8e…`, the restore image
  the pinned stock `111e4dd7…`); the boot report ran on the packed tree's
  stock BusyBox (passed, 796 bytes); `review.py` passed with
  `flashReady: false`.

## Stage 1 — the boot program (host)

- [ ] `disc-boot`: the key read behind one function, modes and the
  boot-loop guard, `package.json` parsing and verification, slots and
  atomic state, the supervisor (readiness, confirmation, restarts,
  rollback), requests, status, the `/sbin/mq_ui` launcher, recovery from
  the card. Static MIPS, soft-float, like the console.
- [ ] Host tests against a fake root, clock and key: every row of the
  contract's lifecycle, requests and recovery, and every refusal (manifest
  bounds, paths, hashes, API, profile, free space).

## Stage 2 — the guest

- [ ] The emulator models the key pins and runs stock's `rcS` and
  `fiio_init.sh` (a separate session of the emulator project; the external
  repository changes only there), with diskOS's release that supports V2.57
  reviewed in the same session.
- [ ] Guest acceptance with fault injection: install, activate, crash before
  and after ready, rollback, power loss between steps, a wrong staged
  package, both modes, a `ui` package under stock's watch loop.

## Stage 3 — packages and the two-package acceptance

- [ ] The server as a `service` package (in the server's repository): its
  updates, signature and authorization.
- [ ] diskOS's UI as a `ui` package beside our server and player, on the
  guest (owner, 2026-10-02).

## Stage 4 — the image and the installation (separately authorized)

- [ ] The installation for users: the image built from the user's own OTA,
  written and verified through USB Boot, packages staged on the card, the
  first platform boot with Play; the user-confirmed first boot before the
  readback (combined-008's order).
- [ ] An installer for any unit, not only the owner's: that player's NAND
  layout and bad blocks, V2.57 checked, a full backup, write, verify every
  byte, the way back to stock.
- [ ] The owner's player: the image, then the two packages (separately
  authorized).
- [ ] Open facts for the device: what "Reset all" removes in `/usr/data`;
  its free space; Play on GPB15; memory and priority limits.

## Later

- Ready-to-run installers for macOS and Windows (Windows needs a WinUSB
  driver for the USB Boot device); a browser installer over WebUSB.
- An optional "official" label for packages signed by us.
- Whether the installer writes the console marker always or as an option,
  and whether it removes it after the installation.
