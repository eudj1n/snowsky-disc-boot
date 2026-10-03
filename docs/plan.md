# Plan

The canonical plan of snowsky-disc-boot. The contract it implements:
[boot layer contract](contract.md). Decisions and evidence from before this
repository stay in snowsky-disc-web's `docs/plan.md` (its "Boot layer" and
"Public installer" sections).

## Stage 0 — the repository (owner, 2026-10-02)

- [x] Created beside snowsky-disc-web; the boot layer's work continues here
  (owner, 2026-10-02). The gateway continues in snowsky-disc-server, started
  the same day while the emulator work is pending; snowsky-disc-web is frozen.
  The default branch is `2.x`, after the firmware version (owner,
  2026-10-02); GitHub and its Actions come once all repositories are
  published.
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

- [x] `disc-boot` (`device/src/boot.c`, `manifest.c`, `boot_util.c`,
  `sha256.c`, jsmn): `early` reads the keys from the GPIO pin level behind one
  function and decides the mode (the default, Volume Up for the other one,
  Play for recovery, the boot-loop guard after 3 unconfirmed boots); `start`
  recovers from the card and supervises the service package (ready,
  confirmed after 180 s, restarts at most 3 in 10 minutes, a tentative slot
  rolled back at its first failure); requests (activate, rollback, default,
  remove); status files; the `mq_ui` launcher with its watcher; `verify`
  for servers. Static MIPS, soft-float, about 360 KB with debug data, built
  with the console. The contract follows the program: role state in
  `<role>/state.json`, status split into `boot.json`, `service.json` and
  `ui.json`, the wrapper and the launch permission below.
- [x] Stock's UI never depends on the boot program (found while packaging,
  2026-10-02): `/sbin/mq_ui` is a shell wrapper that starts the launcher
  (`/opt/disc-boot/mq_ui`, a link to `disc-boot`) only when `early` left
  `/run/disc-boot/ui-launch` (platform mode with a ui package installed).
  In stock mode, after Volume Up, without a package or when the boot program
  fails before deciding, stock's UI starts from the wrapper alone.
- [x] The image builder adds the boot layer: `/opt/disc-boot/` (the program,
  the launcher's link, the console and the boot report, both moved from
  `/opt/disc-web/` in this stage), `/sbin/mq_ui`, `S22disc-boot`,
  `S99disc-boot` and `S99disc-usb`; it refuses the fixture build. The boot
  report shows the boot layer's decision, roles, log and supervisor instead
  of the old companion's.
- [x] Host tests: `test_boot` (25: modes, the guard, every manifest refusal,
  the service's lifecycle and environment, requests, recovery and its
  refusals, the launcher behind the real wrapper, the fallback and rollback
  of a crashing UI, the production build free of fixture switches), the
  SHA-256 vectors of FIPS 180-4, `test_deployment` and `test_boot_report`
  for the new payload. The same `test_boot` passed on Linux against the
  MIPS musl build under `qemu-user` in the emulator's container.
- [x] The packed image (2026-10-02): an offline build from the V2.57 OTA kept
  all 3,492 stock objects, added exactly nine (`/opt/disc-boot` and its five
  entries, `/sbin/mq_ui` and the two new hooks besides the console's), passed
  the full round trip (packed 80,900,096 bytes, content `0ad94540…`, the
  production `disc-boot` 359,792 bytes `d47165aa…`) and `review.py`
  (`flashReady: false`); the boot report on the packed tree's BusyBox passed
  with its new sections; `tests/integration/boot_layer.py` ran the image's
  own hooks, wrapper and production program on that BusyBox with real
  timings: the default mode without readable keys, nothing counted with
  nothing installed, stock's UI through stock's `PATH` lookup without the
  boot program, then a service and a ui package both confirmed after 181 s,
  the boot-loop count cleared, `status` gathered, and `stop` ending the
  supervisor and its package. The first run caught a stand-in `/proc` that
  made the UI's watcher lose the UI; the watcher now trusts `kill(pid, 0)`
  when `/proc/<pid>/comm` is unreadable, and the run mounts its own proc.
- [x] Rollback only to the version confirmed there (2026-10-02, found while
  designing the server's updates): the previous slot is the inactive one, so
  an update staged there replaced the rollback target and a rollback, asked
  for or after a tentative failure, made the staged, never-run package the
  confirmed one. `state.json` now keeps `previousManifest`, the SHA-256 of
  the previous slot's `package.json` when it became the target; a rollback
  needs it to match, else it is refused (`the previous version was
  replaced`) or the tentative version stops with its reason. The status
  names that version (`previous`) while its slot holds it. Evidence:
  `test_boot` (26; the new case stages over the previous version from a
  confirmed package, then from a tentative one that fails). The packed
  image gets this with stage 2's rebuild.
- [x] `DISC_BOOT_PROGRAM` in a package's environment (2026-10-02): the boot
  program's path (`/opt/disc-boot/disc-boot`; the fixture's own file in
  tests), so a server runs `verify` on a staged update without a path of
  its own. Evidence: `test_boot` (the service's environment).

## Stage 2 — the guest

- [x] The emulator models the key pins and runs stock's `rcS` and
  `fiio_init.sh` (snowsky-disc-qemu #37–#47, `2.x` at `d7f1b9b`, 2026-10-02),
  diskOS 1.2.0 reviewed there. Taken into the contract: the `PATH` stock's
  scripts see (`rcS` sources `/etc/profile`; `/sbin` still first), the card
  mounted after `S99` (recovery now waits up to 90 s, was 30), stock's
  shutdowns by `poweroff -f` without `rcK` (boot never relied on `stop`),
  "Reset all" leaving `/usr/data/disc-boot` alone, the key bits' evidence.
- [x] Guest acceptance with fault injection (2026-10-03):
  `tests/integration/boot_guest.py` through `scripts/guest.py` on the
  emulator at `d7f1b9b` (reviewed in `firmware/emulator-revisions.json`),
  image `boot-image-ff4c801` (disc-boot 370,620 bytes `aac50db6…`, all 3,492
  stock objects kept, review image `b822c7e7…`), real timings, probe
  packages as shell scripts: nothing installed (platform, keys read, stock's
  UI through the wrapper), Volume Up (stock), a damaged staged package
  refused by its hash and left on the card, Play installing and confirming
  version 1 after 183 s, version 2 staged by the running version and
  activated (slot b, 1 kept for a rollback), versions that exit before and
  after ready giving way to 2, version 5 confirmed then a rollback asked for,
  power cut (unsynced) three times while version 6 was tentative (counts 1,
  2, then the boot-loop guard chose stock), Play bringing version 6 back and
  its confirmation, and a `ui` package installed with Play started through
  the launcher at once, confirmed, and started again when killed. Found on
  the way and fixed: the recovery signalled only a UI the launcher had
  started, so stock's own UI kept running and a new `ui` package waited for
  the next boot (`ff4c801`, now by the process name); the MIPS compiler's
  truncation warning in the status (`beb59b4`); stale build IDs in images
  (the Makefile now relinks on a new ID). Recorded in the contract: a
  version restored by a rollback clears the boot-loop count only after its
  180 s again. Again on the emulator at `bfa1988` (#48 and #49 fixed) with
  `boot-image-12d7c02`: all 11 steps passed (2026-10-03).

- [x] Only the standard descriptors for a package (2026-10-03, found by the
  server's soak on the guest): the supervisor kept the boot log and
  `/dev/null` open beside its own standard descriptors, and the service's
  spawn kept the copies `dup2` leaves, so every package started with four
  descriptors of the boot program's (3–6, among them the boot log open for
  writing). The supervisor, the spawn and the UI watcher now close every
  descriptor above 2; the contract says so. Evidence: `test_boot` (the
  package lists none open; without the fix 3, 4, 5 and 6), and the guest
  acceptance repeated on `boot-image-368b0bf` (disc-boot 373,312 bytes
  `28538fd4…`, review image `d098d530…`): all ten steps passed; the
  server's gateway there has 5 descriptors idle (9 before).

## Stage 3 — packages and the two-package acceptance

- [x] The package tool (`scripts/package.py`, owner 2026-10-02): `describe`
  writes a folder's `package.json` (sizes, digests, modes from the executable
  bit), `check` applies `disc-boot`'s rules with its messages to a folder or
  a zip, `zip` packs a deterministic archive with the files' modes, `stage`
  lays a package out on a card for the recovery with Play (written beside,
  checked, swapped in; only with `--confirm-card-write`) and `result` reads
  the recovery's answer. Evidence: `test_package` (7: the tool and
  `disc-boot verify` give the same decision and message on 33 good and
  damaged packages; refused zips with links, unsafe paths or too much data;
  staging replaces a role whole and a broken package stages nothing; a
  staged zip installed and confirmed by `disc-boot` after Play).

- [x] The server as a `service` package (in the server's repository,
  2026-10-02/03): signed `.update` streams (Ed25519, the owner's release
  key in every package) uploaded through its manager under the serial
  number, a request ID and the control owner, checked and staged into the
  inactive slot, then `activate`/`rollback` as boot's request; installed
  with Play and run on a stock-init guest with an update confirmed and
  rolled back (snowsky-disc-server `0734ee0`, `b070557`, `bc629db`; its
  plan, stage 1, steps 3 and 4).
- [x] The card guard and a `ui` package's player launcher (owner,
  2026-10-03, found while preparing diskOS's package): stock's player runs
  `umount` and then `rm -rf` on the card's mount point without checking the
  unmount, which empties a card held busy (read in the V2.57 `mq_player`;
  one open file and a card event emptied the guest's card). The image adds
  `/sbin/mq_player` and `/opt/disc-boot/guard/rm`: both wrappers put the
  guard first in the `PATH` of stock's player and UI in every mode, and it
  refuses an `rm` of the mount point or a folder above it (the mount point
  itself only goes when empty). A `ui` package may name `player`, its own
  launcher of stock's player, which boot runs as `mq_player` while that
  package's UI runs; stock's player starts at once otherwise. Host evidence:
  `test_boot` (the launcher, its status, the fallback, stock mode, a slot
  that fails its check, the manifest's refusals), `test_package` (the tool
  and `disc-boot` agree on seven more packages), `test_deployment` (the
  image's additions, both wrappers, the guard refusing nine spellings of the
  mount point and its parents and passing what lies on the card, under
  `sh` on macOS and `dash`), and the guard under the guest's BusyBox. On
  `boot-image-12d7c02` (disc-boot 378,828 bytes `8cb1bf50…`, packed
  80,908,288 bytes, review image `e6a7f6c7…`): the packed tree's test found
  `mq_player` through stock's `PATH` at `/sbin/mq_player` with the guard
  first, kept a busy mount through stock's "umount, then rm -rf", and ran a
  ui package's player launcher; the guest acceptance passed all eleven steps,
  among them the busy card (stock's own player, its `PATH` starting with the
  guard: one open file and a card event, `refused rm -rf /tmp/sdcard`, the
  card whole) and the probe ui's player launcher starting stock's player
  twice under stock's watch loop. The risk was then shown on the image that
  lacks the guard: on combined-010 (snowsky-disc-web, on the owner's player)
  with its gateway streaming a card track, stock's card event emptied the
  card (four files of four gone, read from the backing image with the guest
  off); the same event with nothing holding a card file open left the card
  whole (`work/combined-010-check/`, ignored). The guarded boot image is the
  fix; a guarded combined image for the owner's player is a separately
  authorized stage.
- [x] diskOS's UI as a `ui` package beside our server and player, on the
  guest (owner, 2026-10-02; done 2026-10-03). `tests/integration/
  diskos_package.py` builds diskOS's UI from a local checkout with its own
  toolchain image (1.2.0, `0edcfba`, GCC 11.2 musl, FP64/NaN2008, static,
  3,945,212 bytes) into a package of ours kept in `work/` and never passed
  on: an entry `mq_ui` that writes diskOS's boot record and runs the binary
  as `mq_ui`, and the binary as the package's `player`. `two_packages.py`
  on `boot-image-12d7c02` with the server's debug package
  (`2026.10.03-181c5a8-debug`) and the page's release (`2026.10.02-05a1422`):
  six steps passed (contract, "Acceptance: two independent packages", which
  also lists what diskOS would change to fit). Found on the way: on the
  guest a restart of stock's player in stock mode ran `rm -rf` on the
  mounted card after an unmount that failed (the mount kept its id); the
  guard refused it, twice. The emulator's gaps were snowsky-disc-qemu #48 and
  #49. Corrected the same day: diskOS's UI itself never got past its start
  on the guest (the emulator drops `argv[0]`, and diskOS runs itself again
  as `mq_ui` endlessly; contract, "Acceptance: two independent packages");
  the run checked the process and its launcher's verdict, and its
  screenshots were empty.
- [ ] diskOS's UI running on the guest: `two_packages.py` without the
  emulator workarounds and requiring the UI to hold the touch panel, on the
  emulator at `bfa1988`, stops at its first step until the emulator keeps
  `argv[0]` (`work/emulator-bfa1988/`, ignored).

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
  its free space; memory and priority limits. The keys' pins and level are
  settled offline (2026-10-03, [kernel review](nand-kernel-review.md#keys))
  and **confirmed on the owner's V2.57 player** (2026-10-03, read-only over
  the engineering USB console, busybox `devmem` of port B `PxPIN` at
  `0x10010100`; no NAND access; `work/device-read/key-read.json`): resting
  word `0xF6EFF327` (a V2.57 read; `0xF6EFE127` was the V2.40 one), and with
  each key held in turn 20 of 20 samples read Volume Up `0xF6EFD327`
  (bit 13), Volume Down `0xF6EFB327` (bit 14) and Play `0xF6EF7327`
  (bit 15) — active low, each key clearing only its own bit, matching the
  kernel review and `read_keys`. The same session confirmed `/dev/mem` is
  present, `/usr/data` has 58 MiB free of 67.6 (ubifs) and the NAND layout
  matches the reviewed one. Recovery with Play (bit 15) is therefore
  qualified on this unit. Still open (not blocking the write): what "Reset
  all" removes in `/usr/data`; memory and priority limits.

## Later

- The emulator repository keeps only its qemu part (owner, 2026-10-02): a
  separate session after the boot layer's emulator work, one PR into its
  `2.x` (where `codex/disc-web` was merged as #36, `f70a066`, keeping our
  reference revisions `a0cf54d` and `992d156` reachable). What our
  repositories import from it and must keep or move along:
  `emulator.runtime` (keys, peripherals, boot_ready), `emulator/scripts`
  (`lib.sh` and the boot scripts), `firmware.tools.firmware_inventory` (this
  builder's OTA input), `controller.fiio_link`, `research.diagnostics`
  (`probe_keys`, `player_memory`) and `ci/cleanup.sh`. The DISC Web
  prototype, `experiments/`, `library/` and the speech work are not used.

- Ready-to-run installers for macOS and Windows (Windows needs a WinUSB
  driver for the USB Boot device); a browser installer over WebUSB.
- An optional "official" label for packages signed by us.
- Whether the installer writes the console marker always or as an option,
  and whether it removes it after the installation.
