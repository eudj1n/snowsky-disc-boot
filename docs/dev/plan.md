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
  `device/usbboot/`), the USB console (`device/console/usb_console.c`), the
  boot report (`device/scripts/boot-report.sh`), the firmware profiles
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

- [x] `disc-boot` (`device/boot/boot.c`, `device/common/` `manifest.c`, `boot_util.c`,
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
  180 s again. Again with `boot-image-12d7c02` on the emulator at
  `bfa1988` (#48 and #49 fixed) and at `d7c0d5b` (#52 fixed): all 11 steps
  passed both times (2026-10-03).

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
- [x] diskOS's UI running on the guest (2026-10-03, emulator `690a55c`,
  snowsky-disc-qemu #52–#55): `two_packages.py` without the emulator
  workarounds and requiring the UI to hold the touch panel passed all six
  steps (on #55's head `6d85cc2`, the same tree, starting from a stale
  shared interpreter that setup replaced itself); `boot_guest.py` passed its
  11. Earlier runs: at `bfa1988` it stopped at its first step (the lost `argv[0]`,
  snowsky-disc-qemu #52). At `d7c0d5b` the UI starts but cannot set up the
  screen: the emulator serves the framebuffer and input `ioctl`s through a
  preload shim, which a static program never loads (`FBIOGET_VSCREENINFO`
  answers `ENOTTY`), so it gives up and exits, and boot falls back to
  stock's UI after three starts (snowsky-disc-qemu #54;
  `work/emulator-d7c0d5b/`, ignored).

## Stage 3b — several UIs (multi-boot), before the image

Owner, 2026-10-03: built into the image before its first write to a device,
so that it never needs a second USB Boot (contract, "Several UIs and the
boot menu").

- [x] Designed in the contract (2026-10-03): several `ui` packages, one per
  boot; a `menu` package that asks at power-on, after which stock's loop
  restarts the pair; the choice by the menu, `next` or the default, with
  stock's UI as the fallback; `ui-default`, `ui-next` and `ui-remove`
  requests; Volume Up and Play unchanged.
- [x] Device facts on the owner's player (2026-10-03, over the console,
  read-only but for a test program run from `/tmp` and removed;
  `work/device-read/menu-facts.json`; contract, "Facts this rests on"):
  stock's `mq_player` owns the watchdog (`cmd_watchdog start 10000`, fed by
  its thread), so the pair's restart after a choice is stock's own;
  `fiio_init.sh` checks every 5 s and restarts both with Wi-Fi and
  Bluetooth, the backlight off until the new player lights it; the
  backlight is lit from power-on. A test program took `event0`
  (`EVIOCGRAB`) for 30 s: every key reached it and the player reacted to
  none (two rounds of Volume +, Volume −, Play, seen by the owner); after
  the release the player changed the volume as usual (seen by the owner)
  and its key thread kept waiting in `evdev_read`. Key codes on `event0`:
  Volume + `0xfb`, Volume − `0xfc`, Play `0xfa`.
- [x] The boot program (2026-10-03): `ui/<name>/` per ui package, the
  choice (`next`, the default or the first installed, stock's UI as the
  fallback) in `choice.json`, the `menu` role and its turn (its answer
  after the pair's restart, two failures or 60 s give the default, a valid
  answer confirms it), `ui-default`, `ui-next` and `ui-remove` (applied at
  the next boot or the launcher's next start, a running service's too), Play
  installing several staged ui packages and a menu, the status;
  `scripts/package.py` stages ui packages under their names and knows the
  `menu` role, with the same messages as `disc-boot verify`. Conformance:
  296 tests (10 new for multi-boot), the existing ui tests on the new layout.
- [x] The hand-over without a restart (owner, 2026-10-03): the menu execs
  `$DISC_BOOT_LAUNCHER`, which records its answer and starts the chosen UI
  in the same process; at the boot's first start of the pair the player
  waits for the choice (no watchdog runs before stock's player starts it)
  and starts the chosen UI's player, so the screen stays lit and Wi-Fi and
  Bluetooth stay up. Once a player ran (`player-ran`, marked by every start
  of one, the wrapper's included), stock's player starts beside the menu and
  a chosen UI with its own player gets the pair restarted. Conformance: 298.
- [x] Guest acceptance with probe packages (2026-10-03, image `be681e3`,
  emulator `690a55c`): `boot_guest.py` passed all 14 steps, the four new
  ones with two ui probes and a menu probe (shell scripts). Play installed
  a second UI and the menu after stock's player had run, so stock's player
  started beside the menu and the pair restarted into its choice (the
  second UI with its own player launcher); a plain power-on ran the menu at
  the pair's first start, the player waited (`waiting`, then the chosen
  package's launcher) and stock's loop restarted nothing (its
  `process_failed.txt` unchanged); `ui-next` from the service gave one boot
  without the menu; Volume Up gave stock mode without it. `two_packages.py`
  passed its six steps on the new layout (diskOS under `ui/diskos/`).
  Evidence in `work/boot-image-be681e3/` (ignored).
- [x] `disc-menu`, the boot menu built in this repository (owner,
  2026-10-03): a static program as the `menu` package, released with the
  boot layer, staged by the installer by default (stage 4) and never part
  of the image. Accepted on the guest (2026-10-03, image `b43034b`,
  emulator `690a55c`, package `2026.10.03-b43034b`,
  `tests/integration/menu_guest.py`): after Play its screen showed the
  package with a `title` by it, the other by its name and "FiiO 2.57",
  the ring counting down; Volume − moved the pill and stopped the count,
  Play started the second UI (the pair restarted for its player launcher,
  stock's player having run); at a plain power-on the countdown started
  the default while the player waited, without a restart; a touch on the
  second row chose it. Frames in `work/menu-b43034b/` (ignored). On the guest (the emulator serves static programs' screen since
  `690a55c`), proving the API before the image fixes it. Built on the host
  2026-10-03 (`device/menu/`): the list as chosen, drawn in integer
  arithmetic on a canvas (the ring's geometry once), into the hidden page and
  panned, 180° turned; Volume ± and Play on `event0` (taken only when a
  player already ran), a touch on `event1`, a key down at the start ignored
  until released; the answer, then the hand-over with every descriptor closed
  on exec. `tests/conformance/test_menu.py` (7); the MIPS build is soft-float.
  - Input by the keys (owner, 2026-10-03): Volume + and − move, Play
    chooses, as events on `event0` (firmware codes, read on the player:
    `0xfb`/`0xfc` a volume click, `0xfa` Play). At the boot's first start no
    player runs beside the menu; when one does (after a recovery), the menu
    takes `event0` for itself (`EVIOCGRAB`) so that neither volume nor
    playback changes under it. A key
    already down when it starts is ignored until released (Play at
    power-on was the recovery). The touch panel (`event1`, the UI's own) may
    choose as well.
  - The owner's choices (2026-10-03, from three mockups): the list layout
    (every entry at once, the selected one in a pill with an accent dot,
    its version at the right), always in English, each package by its
    `title` (else its name), versions shown, stock's UI as "FiiO" with the
    firmware's version, the 5 s countdown as a ring along the panel's edge.
    Colours from the player page's dark theme (`#181614` ground, `#ece8e3`
    text, `#ff795a` accent, `#3a3530` selection), Inter (OFL) as the face.
  - Screen: the round 360×360 panel, mounted 180° (`/dev/fb0` XRGB8888,
    three buffers); a centred list inside the circle; each frame drawn into
    the hidden buffer and shown with `FBIOPAN_DISPLAY` (no tearing, and the
    emulator's evidence of a frame). A small renderer of its own and a font
    rasterised at build time from Inter (OFL; Latin only, since the menu
    is always in English and titles are ASCII), not LVGL: a list of a few
    entries needs neither its size nor its widgets.
  - Behaviour: the default highlighted with a 5 s countdown (owner,
    2026-10-03), stopped by any key or touch; it answers
    `$DISC_BOOT_RUN/choice` and execs `$DISC_BOOT_LAUNCHER`, every
    descriptor closed on exec. Its settings (remember the last choice, ask
    or not) in `$DISC_BOOT_DATA`.
  - Shares the boot program's JSON reading and atomic writes. The manifest's
    optional `title` (printable ASCII, 1–32) and stock's firmware version
    reach it through `choices.json` (built 2026-10-03).
  - Tests: a host build drawing into a file with scripted keys (the logic,
    and frames compared as PNG), the MIPS build under `qemu-user`, and on
    the guest the menu captured from the screen and driven by the
    emulator's key injection to start diskOS.

## Stage 4 — the image and the installation (separately authorized)

- [ ] The installation for users: the image built from the user's own OTA,
  written and verified through USB Boot, packages staged on the card, the
  first platform boot with Play; the user-confirmed first boot before the
  readback (combined-008's order).
- [ ] An installer for any unit, not only the owner's: that player's NAND
  layout and bad blocks, V2.57 checked, a full backup before every write
  (owner, 2026-10-03), write, verify every byte, the way back to stock. First
  as a guided command on macOS over the existing reviewed tools (owner,
  2026-10-03): the OTA and the image, the packages and the console marker on
  the card, USB Boot with backup, write and readback, the first boot with
  Play, and stock restored by the same path; desktop or WebUSB later.
- [x] The catalogs (owner, 2026-10-03): a cascade in one form, this
  repository's `catalog/packages.json` (disc-server, disc-menu by default;
  diskOS as a recipe from its own release, whose `payload/mq_ui` is byte for
  byte our build from `0edcfba`) and the server package's
  `catalog/apps.json` (the player page). `scripts/catalog.py` checks both
  and fetches an entry as a checked package folder, from a local file with
  its digest or its published url. `tests/conformance/test_catalog.py` (5).
- [ ] `install.py`, the guided installer (owner, 2026-10-03), drawn in the
  boot menu's style and colours, in three parts:
  - [x] On the guest (2026-10-03, image `b43034b`, emulator `690a55c`,
    `tests/integration/install_guest.py`): the installer's card code put
    `disc-server` `2026.10.03-c502ce6-debug` (the first with
    `catalog/apps.json`, now the catalog's), `disc-menu`, the player page
    taken by its digest from the server's catalog, and the marker on the
    guest's card; Play installed both, the menu counted down to stock's UI
    (the only entry), the server was confirmed and served the page at `/`,
    and the marker stayed.
  - [x] Without a player (2026-10-03): the computer's check, the image from
    the update (or one built before), the packages of the catalog and the
    apps of the chosen server's catalog, the card after a typed
    confirmation (packages for Play, `Apps/`, the console's marker);
    `--dry-run` into its own folder, `--yes` without questions;
    `tests/conformance/test_installer.py` (4). FiiO's update may be given as
    its own folder, `main_os` or `main_os/ota_v<version>` (its chunks
    counted against the profile); the ground fills the window; Ctrl-C stops
    like any refusal. Published archives are downloaded by default and kept
    in `work/downloads` (diskOS 1.2.0's release fetched from GitHub and its
    package assembled, 2026-10-03); an archive found nowhere is asked for.
  - [ ] The player through USB Boot: a full backup before every write, the
    write, the independent readback and every byte compared, over the
    reviewed tools; the way back to stock; `--simulate` (a NAND file with
    injected faults) and `--guest` (the emulator's guest as the player).
    - [x] `--simulate` (2026-10-03): the steps, the typed `WRITE`/`RESTORE`,
      the progress, the faults and the report against a NAND file in the
      reviewed chip's and writer's geometry, a whole-NAND backup first;
      `--restore` back to stock (8 installer tests in all).
    - [x] The player's own backend over the reviewed tools (2026-10-03,
      `scripts/installer/usbboot.py`, `tests/conformance/test_usbboot.py`
      with the tools replaced by a stand-in; not yet run on a player): the
      package offline, the backup session compared with the history's
      image, the admission opened for one writer call and closed in every
      case, the readback by the approved exact plan, the audits, the next
      `history.json`, the owner's first-boot answer recorded. To review
      before its first physical use: the backup's metadata page (the boot
      capture's), the diskOS checkout at `646212d`. The reviewed readers
      collect the primary rootfs only: a whole-NAND backup needs the reader
      payload (`device/usbboot/`) to read every block.
    - [x] `--guest`: the emulator's guest as the player (2026-10-03,
      `scripts/installer/guest.py` over `scripts/guest.py`'s new `put` and
      `read`): the card staged in the run folder and copied over the
      guest's card, the power-on with Play, the status followed until the
      menu answered and the service was confirmed, `result.json` read back,
      the guest removed in every case. Accepted on the guest with image
      b43034b, emulator 690a55c and the catalog's defaults (disc-server
      c502ce6-debug, disc-menu b43034b, the player page): both installed by
      Play, the menu chose stock's UI, the service confirmed (run
      `work/install-run-guest-2`, not committed).
  - [ ] The first boot with Play and `.disc/boot/result.json` read back on
    the player (on the guest: `--guest` above).
- [x] `console.py`, the player's USB console from this computer (owner,
  2026-10-03; built the same day, `tests/conformance/test_console.py` on a
  pseudo-terminal, not yet run on the player): find the ACM port, run commands and read their answers (the
  shell wants `\n` line endings), send a file to `/tmp` and check its
  SHA-256 (the way `keygrab` went over), and the read-only facts this
  session took by hand (keys, watchdog, input devices, memory), never the
  serial number, the MAC or tokens.
- [ ] The owner's player: the image, then the two packages (separately
  authorized), after stage 3b. The server as its debug package (owner,
  2026-10-03). Prepared offline (2026-10-03): the image b43034b
  (`6410b080…`) accepted on the guest (stage 5); the player's history
  (`work/player-history.json`: combined-010's review, image, write
  `7bdf470c` and readback `06cd95a6`, the boot and stock captures); diskOS's
  writer files at `646212d` (`work/diskos-646212d`, every pin matching); the
  backup's metadata page is the write capture's (the boot capture's page is
  byte for byte combined-010's write page); combined-010's first-boot
  confirmation time set by the owner to their message's time in the chat
  log (14:43:06Z, between the write and the readback; first recorded as the
  write's end); the installation package (write plan `fcfb053b…`, read plan
  `45d3a174…`, the proposal changing only the admission and the review pin
  `0fbc7b1f…`). The card: the debug server c502ce6, the menu 2.57.1, the
  page, the console's marker (`work/first-write/packages.json`).
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
  all" removes in `/usr/data`; memory and priority limits (idle baseline
  read 2026-10-03: 71 of 117 MiB available, no swap, `mq_ui` 16 MiB and
  `mq_player` 9 MiB resident; not yet during playback).

## Stage 4b — after the first write (2026-10-04/05)

The first two writes (images b43034b and fc47ae4) ended in stock's UI
restarting without end on the owner's player. The cause (found 2026-10-05):
the image's wrappers started stock's UI and player by their paths, and
BusyBox's `pgrep -x`, with which stock's watch loop looks for them, matches
`argv[0]` first; the loop never found them and restarted the pair every few
seconds. The guest hid it: under qemu-user `pgrep` falls back to the process
name ([observation](observations/first-write-observation.md)).

- [x] Reconstructed and reproduced on the guest (2026-10-04): no stock
  player, stock's watch loop restarting the UI every 7–8 s, the power key
  and USB Boot out of reach without a reset. (A real defect, not the cause;
  see the cause below.)
- [x] Both wrappers fail open (2026-10-05):
  `tests/conformance/test_wrappers.py` under dash, BusyBox ash and bash in
  POSIX mode; the old line fails it.
- [x] The guest acceptance watches stock's pair after each boot that ends in
  stock's UI or a running service and boots once without the boot program
  (`boot_guest.py`; to run on the next image).
- [x] Back to stock with the approved restore plan (2026-10-05): backup
  (b43034b, exact), write `e4a19c47`, the owner's look at stock's start,
  readback `2105747b` matching stock in all 50,816 records, both USB
  journals audited (the audits learned the restore target).
- [x] The installation review binds a previous restore as the source state
  (2026-10-05): the last write's target (candidate or restore) selects its
  image, exact plan, audit status and comparison file; the installer names
  a readback's comparison `exact-<target>-…`, as the review reads it (the
  installer's own `exact-…` names would have failed the next review of any
  installation it made). Checked on the owner's data: the next package
  (image fc47ae4, write plan `fecadd57…`) starts from the restore
  (`installed-candidate`, target `restore`, stock `e75d85bd…`).
- [x] The fixed image accepted on the guest (2026-10-05, image fc47ae4,
  `e88ff73b…`, the boot binaries of b43034b; emulator 690a55c):
  `boot_guest.py` 16 of 16 (stock's pair steady 45 s after four boots; with
  the boot program unable to run, stock runs and the early hook's log says
  `Permission denied`, `early exit 126`), `two_packages.py` 6 of 6,
  `menu_guest.py` 3 of 3 with the menu 2.57.1, `install.py --guest` with
  the first write's catalog (installed by Play, the menu answered, the
  service confirmed). The first try stopped in the emulator's setup: its
  priming of `sysconfig.db` waits for the file, not its table, and was
  killed early under load; the second ran clean.
- [x] The fixed image written in steps, its first step (2026-10-05, the third
  write, release 2.57.2: image `0da9a217…`, write `6df65b78` exact, readback
  matching in all 50,816 records, both journals audited): the first start
  without Play kept stock's pair steady and the power key working; the boot
  log recorded it ([observation](observations/first-write-observation.md#the-third-write-release-2572)).
- [ ] The packages with Play, the second step: two of the first three starts
  of the menu after the installation broke after a key chose stock (stock's
  UI hung on its logo; stock's UI died at every start of the pair until a
  reboot); the countdown's choice worked. Not reproduced since (an immediate
  key press, a press after ten seconds, 13.5 min of samples). Next, before
  the tags: the boot log records the launcher's decisions, the menu's answer
  and hand-over and the menu watcher's kills; the USB console starts without
  waiting for the card's mount (it gave up while the menu waited); then the
  Play installation's path again, with the console. 2026-10-06: the owner
  saw it once more after the countdown's choice (a dark, flickering screen:
  stock's loop turning the backlight off at each restart of the UI), so the
  key is not the cause. Ruled out offline: the menu's framebuffer page
  (stock's UI pans to page 0 itself at start), the environment the menu
  hands on (stock's, plus `DISC_BOOT_*`), the order of the pair's start alone
  (on the guest every order recovered). Stock's pair talks over POSIX queues
  (`/dev/mqueue/ui`, `/dev/mqueue/player`; each recreates its own and opens
  the other's), which outlive the processes until a reboot. The diagnostics
  for the next failure: the wrappers keep the stock pair's own output (in
  `/run`) and copy its tail into the boot log at the next start, with the
  kernel's fatal-signal lines (`print-fatal-signals`) and the queues' state;
  the boot program logs its decisions there too; the console does not wait
  for the card's mount.
  - [x] Found offline (2026-10-06): stock's pair serialises on a `flock` of
    `/usr/data/fiio/process_lock.txt` (util.c's `process_lock_segment`, a
    blocking `LOCK_EX`; `process_try_lock_segment` with `LOCK_NB`). A holder
    that does not let go explains a UI hanging on its logo; a lock kept by a
    process that inherited its descriptor would outlive the pair's restarts.
    After the menu's choice the UI and the player started at once, where
    stock starts the player 2 s after the UI.
  - [x] The diagnostics and the candidate fix (2026-10-06, branch
    `diagnostics`): the boot log keeps the boot program's decisions (`plog`),
    and in platform mode, at each start of the pair, the previous UI's last
    lines (its output in `/run/disc-boot/out`, a 1 MiB tmpfs; the player's
    output stays on the console, where the emulator waits for its network
    thread's line, which a first try took away), the
    kernel's fatal-signal lines, the lock's holders and waiters from
    `/proc/locks`, processes in uninterruptible sleep and stock's queues; the
    console waits 90 s for the card and survives its remounts; after the
    menu's choice the player starts 2 s behind the UI, as stock's loop
    starts them. Host tests cover each; the guest and the player next.
- [x] diskOS's screen after its own "Screen off" (owner, 2026-10-06/07: it
  did not wake by the power key, twice, and the player was switched off and
  on): diskOS 1.2.0 cuts the panel's rail itself (`bl_power=4`) after its
  screensaver and screen-off times, and wakes it only on a touch, read on a
  raw `event1` descriptor of its own; it ignores the player's signals for
  waking by design ("random wake"), and the power key brings the panel back
  only when the player itself blanked it (`ui/main.c`, PW-10). A touch woke
  it on the owner's player. Not the boot layer's: the menu takes only
  `event0`, never `event1`.
- [ ] The power key in the menu (owner, 2026-10-06: the player could not be
  switched off while the menu asked). The key is on `event0` with the
  others (`x2000_key`, GPE31), whose driver sends gestures: `0x103` a press,
  `0x108` a hold, the standby and power-off that stock's UI answers with
  `poweroff -f` (snowsky-disc-qemu's reading of the stock program; the hold
  not yet seen on the device). The menu takes `event0` and reads only the
  volume keys and Play, and neither stock's UI nor its player runs while it
  asks, so nothing answers the hold. The menu answers it as stock does:
  "Switching off", then the power-off (by the boot layer, as the menu's
  answer, so the menu stays a package that touches no power itself). First
  the codes the player sends, read on the device through the console while
  the menu asks; the emulator does not model the hold (it stops the guest).
- [ ] The menu during an installation with Play (owner, 2026-10-06): the
  installation finishes before the menu offers anything, and the menu shows
  its progress (a status file the boot program writes during the
  installation, read by the menu like `choices.json`, so the menu stays a
  package); no stock UI in between and no restart of the pair after it
  (2026-10-06 on the player with diskOS: the menu, stock playing the last
  track, a dark screen, the menu again with diskOS, then diskOS).
- [ ] One SPL a USB Boot entry (2026-10-05): a second SPL in the same entry
  re-runs the whole DDR bring-up, and its PHY training (`CALIB_DONE`, failure
  30 in diskOS's breadcrumbs) left one byte lane untrained in 3 of 6 recorded
  re-runs (both after the writer, one of four after a read); the first SPL
  of an entry passed in all 11. A session in an entry whose SPL left a clean
  diagnostic in TCSM skips the SPL and keeps its RAM pattern passes; a
  session stopped at the DDR diagnostic (before any NAND access) asks for a
  fresh entry and runs again. Not filed with diskOS (owner, 2026-10-05).
  The fourth write (2026-10-06, image `c460b12e`) stopped the same way:
  the write's session, the third SPL of the backup's entry (backup 38 min,
  the read by digest 27 min, whose page ticks came back zero), failed the
  DDR check (failure 30) before the writer, the player untouched. The
  installer now asks for a fresh entry before each write (the candidate's
  and stock's), and `install.py --resume RUN` goes on with such a run from
  its backup (`test_usbboot`). Still open: the readback and the digest's
  own entries, and the page ticks.
- [x] The second write (image fc47ae4, 2026-10-05: write `46444d85` exact,
  the read by digest agreeing with the full backup and with stock) restarted
  without end too, card or no card: the fail-open wrappers were not the cause.
- [x] The cause found and reproduced (2026-10-05): BusyBox 1.31.1's `pgrep`
  tries `argv[0]` first and the process name only when the pattern is
  nowhere in it; `exec /usr/bin/mq_ui` made `argv[0]` the path, so
  `pgrep -x mq_ui` found nothing. On a native kernel with `busybox:1.31.1`, a
  loop like stock's over compiled stand-ins restarted the pair at every check
  through fc47ae4's wrappers and left it alone through the new ones. Whether
  `disc-boot early` made its run folder on the player is no longer needed to
  explain the loop; the boot log will say.
- [x] The wrappers start the launcher or stock's program from its own path
  with `exec -a` and the bare name (BusyBox ash has it; a shell without it
  starts by path); the contract's "Process names" asks the same of packages
  (a `ui` package's `player` ends in `exec -a mq_player /usr/bin/mq_player`).
  `test_wrappers.py` checks `argv[0]` with compiled stand-ins under dash,
  BusyBox 1.31.1 ash and bash in POSIX mode (fc47ae4's wrappers fail it);
  `boot_guest.py` checks the pair as the player's `pgrep` sees it (`argv[0]`
  from `cmdline`) and refuses fc47ae4 on the guest. Folders of links named as
  stock's programs, tried first, changed the path the emulator finds stock's
  player by (the guest's card events failed); `exec -a` keeps it.
- [x] The image builder keeps stock exactly (2026-10-05): both written
  images had lost 453 of stock's modes and owners (setuid of `/bin/busybox`
  and D-Bus's launch helper, 449 group-writable bits, two owners), built on a
  macOS share mounted into Docker and checked against the same damaged tree.
  It now works in a scratch folder of the container's own file system,
  refuses a file system that changes stock's tree on extraction and compares
  the packed image's listing with stock's (`stock-listing.txt`,
  `candidate-listing.txt`): built on the same share, 0 stock entries differ.
- [x] The fixed image accepted on the guest (2026-10-05, image `f683f082…`,
  the boot binaries of build `a16c806ab4ac`, emulator 690a55c): `boot_guest.py`
  16 of 16, the player's `pgrep` finding the pair after every steady boot, with
  a ui package and after the menu's hand-over; `two_packages.py` 6 of 6;
  `menu_guest.py` 3 of 3 with the menu 2.57.1; `boot_report.py` and
  `boot_layer.py` on the packed tree; `install.py --guest` with the first
  write's catalog (installed by Play, the menu answered, the service
  confirmed). Two more boots showed the boot log's sections (the decision,
  `early exit 0`, `start exit 0`, the wrappers' starts with their uptime);
  `boot_guest.py` checks it at its first boot since. On the guest the boot id
  is the Docker VM's and repeats; the uptime starts again at each boot.
- [x] Back to stock after the second write (2026-10-05, the package's
  restore, `install.py --restore --run`): write `37846365` straight away,
  the owner's look (stock steady), readback `8e4fd642` matching stock in all
  50,816 records, both journals audited; the installer profile pins that
  package's review (`0e1f262a…`).
- [x] Logs that survive a reset (owner, 2026-10-05), before the next write
  of the boot layer: everything the layer wrote went to `/run` (a tmpfs, gone
  at each reset) and the card's boot report waits 45 s. The boot log is
  `/usr/data/disc-boot/boot.log` (UBIFS, mounted by S21 before S22): the
  early hook opens each boot's section (boot id, uptime) before the boot
  program runs, then adds its output, exit status and `boot.json`; each start
  of the pair by the wrappers adds the uptime, the name and whether the boot
  layer chose a package; the start hook its status. Plain commands whose
  failure is ignored; 256 KiB, then `boot.log.1` (the early hook rotates
  it). Not the card: it is mounted later by stock's player, which remounts
  it at start, and a FAT written through resets is at risk.
- [ ] The userdata partition read through USB Boot: the reader's raw pages
  of `userdata`, its UBIFS taken apart offline (the boot log and stock's own
  `/usr/data/fiio/log/process_failed.txt` from a system that never stays
  up); also the whole-NAND backup's missing part.
- [x] `install.py` shows each USB session's progress to the user (owner,
  2026-10-04/05), with the next write of the boot layer: a progress bar in
  the menu's look, the percentage, the time gone and the time left, for the
  backup, the write and the readback. The reviewed tools run in the
  background while the installer counts the session's batches against the
  approved plan (the reads) and the logical blocks written against the write
  plan's 768; the tools and their checks stay as they are. The write counts in
  two phases (2026-10-05, after the second write's bar said 13 minutes left
  with 15 still to come): its calls up to the writer's start, found in the
  journal as the program start at the approved plan's `writer_entry`, then the
  plan's `writer_wait_ms` by the clock; while staging, the calls so far tell
  the staging's length and the wait is added (about 11 + 15 minutes).
- [x] The read by digest, device and host (2026-10-05): the payload
  `rootfs-digest` (`device/usbboot/digest.c`, its own SHA-256 without
  libc, 8,024 bytes then, stack about 13 of 32 KiB) answers the full read's
  batch with 128 bytes a page (the full result's words, the first OOB bytes,
  the SHA-256 of the main bytes); the full read's payload stays byte for
  byte `b1743a1d…`. The collector reads by digest (`--mode rootfs-digest`,
  17 calls a batch instead of 53, 8 KiB a result instead of 277) and
  `readback.py --digest` compares every page with the image.
  `sfc-identity-test` checks the batch on the SFC stand-in against the boot
  program's own SHA-256; `test_collect_rootfs.py` reads a stand-in NAND both
  ways (the same mapping, every page's digest the image's and the full
  read's, a changed page found by its number). The installer runs it right
  after the backup and after the readback, in the same entry, and keeps its
  agreement with the full read and the image and its time; it never stops
  an installation (`test_usbboot.py`). With the real payload and metadata
  page its plan is 794 batches, 13,588 calls and 8.9 MB against the full
  read's 42,220 calls and 227.5 MB. Still to do: an offline audit of its
  journal before it may replace the full read.
- [ ] The read by digest in portions of 16 blocks (owner, 2026-10-05): its
  first run on the player took about 2 s a batch, as the full read does,
  because the host sleeps the transport's fixed settle (2 s) after each of
  the 794 batch executions, while the payload needs tens of milliseconds;
  the digest saved only the transfers. A marker pass as now, then 49
  executions of 1,024 pages each with a wait computed from the portion and
  bounded, the progress in 2 % steps, about a minute a read; the completion
  marker written last keeps a too-short wait a refused result, never a
  wrong one. First beside the full read again.
  - [x] The measure first (2026-10-05): what a page takes on the player is not
    known (only that 64 pages finish within the 2 s settle), and a wait sized
    without it gains nothing (31 ms a page would make each portion wait 32 s).
    The digest payload now keeps each page's CP0 Count ticks, from its read to
    its digest, in the record's first reserved word (`cycles`; payload
    `cb2bb043…`, 8,032 bytes, the full read's still `b1743a1d…`), and the
    collector's result sums them up (`page_ticks`: pages, min, median, p99,
    max, total). The next read beside a backup measures it; the portions'
    wait comes from that measurement. Until the portions, the installer reads
    by digest only beside the backup (owner, 2026-10-05): a second run after
    the readback cost about 27 minutes and told nothing new.
  - [x] The measure came back zero (2026-10-06): three reads by digest beside
    a backup, 27 minutes each, gave every page 0 ticks, though the payload
    reads CP0 Count before and after each page (two `mfc0 $9` in it): Count
    stands still in that context (likely Cause.DC set by the ROM or the SPL;
    not settled). What the sessions do tell: 794 batches in 27.4 min, about
    2.07 s a batch, the host's fixed 2 s settle and the transfers; the
    device's own time a batch is under it. The installer no longer reads by
    digest beside the backup (owner, 2026-10-06).
  - [x] A timer that runs there: not needed for the reads (2026-10-07). The
    host measures each batch by asking the ROM until it answers (the item
    below), so no session of its own. Kept for the measured writer below:
    the stock kernel's tree names the SoC's own timer, `core-ost` at
    `0x12000000` (and `0x12100000` per core), beside clearing Cause.DC for
    Count.
- [x] The settle after each batch (owner's question, 2026-10-06): the backup
  and the readback take 35–40 minutes for 96 MiB that cross USB in about two:
  the host waits the transport's fixed 2 s after each of their batch
  executions. Done without a timer on the player (2026-10-07): the ROM does
  not answer USB while a payload runs, so after each batch the host asks it
  for the CPU reply every 50 ms (`completion_poll_ms`), a timeout being the
  batch still running, until it answers, at most the 2 s settle; the plan
  admits those requests, the journal marks them, the readback's audit
  accepts timeouts then one exact answer, and the result keeps each batch's
  time (`batch_ready_ms`), the measure the timer was for. Expected: each
  full read from about 38 minutes to about 6 (794 batches of 277 KiB at
  0.9 MB/s), the read by digest to about a minute. `test_collect_rootfs`
  (asked until it answers; a timeout at the ask continues and nowhere else;
  an error at an ask stops; past the settle stops), `test_usb_trace_audit`,
  `ram-transport.md`. The ROM's behaviour under such asks is the player's to
  confirm: the next backup does it, and if it stops, nothing was written
  and `completion_poll_ms: 0` brings back the fixed settle. On the player
  the repeated asks failed and became one held request (2026-10-07, stage
  4c below).
- [ ] The write session's waits (owner, 2026-10-05): the host sleeps a fixed
  15 min (`writer_wait_ms`) after starting the writer, since the ROM does not
  answer USB while it runs (diskOS waits the same for `my_write5`), while
  programming 768 blocks takes about a minute by the chip's timings; and
  the staging takes about 11 min of transfers (two RAM pattern passes over
  ~100 MB and the image's upload and comparison). A too-early look at a
  writer is an unknown outcome for these tools, so the wait shortens only
  on measurements. After the third write, as a stage of its own (owner,
  2026-10-05): the third write keeps today's pipeline, proven exact four
  times, so that it tests the boot layer's fix and nothing else.
  - [x] `my_write6` reviewed (2026-10-05, diskOS 1.2.0 `0edcfba`). It is
    `my_write5` (our pinned writer `4f23a3c9…`, unchanged in 1.2.0) plus a
    gate before the NAND is unlocked: the staged image's SHA-256 computed in
    DRAM against a host plan blob, and the kernel, the recovery kernel and the
    recovery rootfs read and hashed against a catalogue. Its CP0 Count timing
    (64-bit accumulation, CPCCR/CPAPCR kept for the conversion) is compiled
    only into the builds that write nothing (`PROBE_ONLY`, `GATE_ONLY`), so it
    measures no writer; diskOS waits 18 min for it instead of 15 (its hashes,
    not measured either). `my_write5` already verifies every block by reading
    it back against the source in DRAM, with retries. Adopting `my_write6`
    would shorten nothing.
  - [x] Where the write session's 26 min go (the second write's journal):
    576 MiB over USB in 64 KiB calls, about 0.9 MB/s. The two RAM pattern
    passes over the image's 96 MiB region (written and read back twice,
    about 7 min), the image's upload (about 2 min), its read back for the
    comparison (about 2 min), then the writer's fixed 15 min, while
    programming 768 blocks takes about 1–1.5 min by the chip's timings.
  - [x] The RAM check on the player (2026-10-07, owner: "возражений нет"):
    the staging check (`device/usbboot/staging.c`, the `staging-check`
    payload, 2,032 bytes, no NAND opcodes) runs from the code region before
    the writer goes there, writes xorshift32 words from a nonce seed over the
    whole image region before reading any back, then their complement, and
    only its 80-byte verdict crosses USB; the small regions keep the host's
    passes. About 7 minutes of transfers become the payload's own time, asked
    every 200 ms, at most 10 minutes (`staging_check_ms`).
    `test_staging_payload` (the C code on the host), `test_writer_transport`,
    [writer transport](writer-transport.md#the-hash-on-the-player).
  - [ ] The staged image checked on the player: a SHA-256 of the image
    region, compared with the image's, instead of reading 96 MiB back over
    USB. The payload's code runs uncached (kseg1, every instruction fetched
    from DRAM), and SHA-256 costs about 40 instructions a byte, so a whole
    image's hash there may take longer than the read back's 2 minutes. For
    now (2026-10-07) the image is read back as before and the player hashes
    its first 1 MiB (`staging_sample_bytes`), compared with the host's and
    timed (`image_hash_sample_ms`); the next write's measures decide. The
    cached alias (kseg0) would make it seconds, after a review of the cache
    state the ROM and the SPL leave.
  - [x] The writer asked until it answers (2026-10-07, owner: "включи
    сразу"): instead of the fixed 15 minutes, the host asks the ROM every
    second while the writer runs; the ROM answers once the writer has
    returned, so the wait ends then, and its length is the measured writer
    (`writer_ms`). A failed ask is no outcome; no answer within the 15
    minutes leaves it unknown as before. `test_writer_transport` (the wait
    ended by the answer, failed asks, an interrupted wait, the audit with
    every ask). With these, a write session from about 26 minutes to about
    10: the region's check, the image's upload and read back (about 4), the
    writer (about 1–1.5).
  - [ ] A measured writer: our reproducible build of `my_write5` with
    `my_write6`'s timing method in the write path (erase, program, verify
    per block), for the time of each step. The total is now measured by the
    asks above; this only tells where it goes.
- [ ] With the next write of the boot layer (owner, 2026-10-05): the exact
  check by digest (stage "Later", the faster exact check), a SHA-256 of
  every logical block computed on the player by the reviewed reader
  (`device/usbboot/`, `device/common/sha256.c`), diskOS's `my_write6`
  read beside it. Its first run goes beside the full read, before and
  after the write, and both must agree; only then it replaces the full read.
  It keeps the reads' batches (owner, 2026-10-05): each batch returns its
  blocks' digests instead of their bytes, so the progress keeps counting
  real work against the plan and a stalled call stays a bounded timeout;
  never one long digest of the whole image.
  The write session too (owner, 2026-10-05): its 25 min are about six
  passes of 100 MB over the ROM's USB protocol (the RAM test with a pattern
  and its complement, the staged image compared again), while NAND takes
  about half a minute; the staging regions and the staged image checked by
  digest on the player, a lighter RAM test once this player's memory is
  qualified, larger transfers. The aim is minutes for a write; the image
  stays written only through USB Boot (FiiO's card update goes through its
  signed recovery), and what changes often is a package on the card.
- [x] The way back is stock, always (owner, 2026-10-05): the installer
  assumes stock before its first write and offers only stock when a write's
  readback or the owner's look at the new system's start fails; stock is
  FiiO's own rootfs from the user's update, depends on nothing of ours, and
  every package approves its restore plan. The backup before every write
  stays mandatory as the proof of what the player held and an exact copy
  for analysis; writing a backup back is a manual operation for us, not a
  user's path. A working boot layer comes back by installing a release
  again over stock (packages and settings in `/usr/data` stay).
- [x] Fewer entries into USB Boot (owner, 2026-10-05; the installer the
  same day): the backup and the
  write follow one another without leaving USB Boot (each session runs the
  SPL and stages afresh; the first write of 2026-10-04 and the restore of
  2026-10-05 went from backup to write that way). A session after the
  writer does not start in the same entry: the restore's readback stopped at
  `SPL DDR diagnostic failed` (2026-10-05, nothing read), so the readback
  needs a fresh entry, which boots what was written once (with a broken
  image the player may not come back to USB Boot until its battery runs
  down). The way out is to check inside the writer's own session, by digest
  (below), before any boot.
- [x] The installer's way back (2026-10-05, `tests/conformance/test_usbboot.py`
  with the tools replaced): after a failed readback or a "no" to the
  start, the guided run offers stock (the approved restore plan, the same
  backup, write, start and readback steps), and `install.py --restore`
  works from a run's package when the player no longer starts.

## Stage 5 — GitHub, CI and releases (owner, 2026-10-03)

- [x] Versions after FiiO's firmware: `<firmware>.<number>` (`2.57.1`, tag
  `v2.57.1`), numbered in each repository on its own; debug builds stay
  local; a release is a draft the owner publishes; the CI rebuilds and must
  find the digests of the guest-accepted files; the server's signed
  `.update` is made on the owner's computer (owner, 2026-10-03).
- [x] Reproducible builds (2026-10-03): the build id is the last commit of
  the binaries' sources, the toolchain's Debian base is pinned by digest.
  Shown locally: a toolchain image built without cache from the pinned
  recipe and a clean worktree gave `disc-boot` `32564a80…`, `disc-menu`
  `01cba485…` and `disc-usb-console` `62b28e1e…`, the accepted image's.
- [x] `scripts/release.py` (build, record, check; deterministic zip and
  tar.gz), `tests/conformance/test_release.py` (4); `ci.yml`'s `mips` job
  and `release.yml` (tag `v*`, draft release; no secrets, actions pinned).
- [x] The first runs on GitHub (2026-10-04, pushed by the owner's go-ahead
  over SSH; run `37148254264` at `1662230`): both jobs pass, and the `mips`
  job's artifact holds `disc-boot` `32564a80…` (build id `b43034ba1e27`),
  `disc-usb-console` `62b28e1e…` and `disc-menu` `01cba485…`, the accepted
  bytes. The first run found what macOS hides: dash ends a script on a
  special built-in's failed redirection (the tests' descriptor probe), and
  musl.cc refuses GitHub's runners (the toolchain falls back to the
  Internet Archive's capture of the same file, same SHA-256; a toolchain
  built that way gave the same bytes).
- [x] `2.57.1` recorded (2026-10-03, `releases/2.57.1.json`): the image
  b43034b holding its `disc-boot` and `disc-usb-console` passed
  `boot_guest.py` (15 of 15) and `two_packages.py` (6 of 6, the debug server
  c502ce6, diskOS 1.2.0 from its release, the page), its `disc-menu`
  package `menu_guest.py` and `install.py --guest` (installed by Play beside
  the server, the menu answered, the service confirmed); emulator 690a55c.
  The catalog names the menu by the release's address; the debug server
  left it for the first write's local catalog (`work/first-write/`).
- [ ] ~~The tag `v2.57.1`~~: not released (2026-10-05): its boot binaries
  have not run on a device (stage 4b). The next release takes the next
  number once the fixed image passed on the player.
- [x] `2.57.2` recorded (2026-10-05, `releases/2.57.2.json`, build
  `a16c806ab4ac`: `disc-menu-2.57.2.zip` `31dfe5cd…`,
  `disc-boot-2.57.2-mips.tar.gz` `034aecee…`), released together with
  snowsky-disc-server 2.57.1 (`disc-server-2.57.1.zip` `caca82e8…`, the
  release variant) and the player page 2026.10.02-05a1422 (owner, 2026-10-05:
  boot and server, the page with them). Accepted on the guest at emulator
  `f1d5e33` (reviewed and listed in `firmware/emulator-revisions.json`):
  `boot_guest.py` 16 of 16 (the pair as the player's `pgrep -x` finds it,
  agreeing with the emulator's `boot_ready.watched()`), `two_packages.py` 6
  of 6 with the release server and page, `menu_guest.py` 3 of 3 with this
  menu, `boot_report.py`, `boot_layer.py`, and `install.py --guest` with the
  release candidate's catalog, building its own image (every stock entry
  exact). The catalog names the menu and the server by their releases'
  addresses; the server is a default beside the menu. The page's zip is the
  one its release workflow packs (Node 24): the 2026-10-02 local build held
  the same files, its gzip twins compressed by Node 26's zlib.
- [ ] The third write with these files, in steps (owner's go-ahead,
  2026-10-05): the image with no package first passed on the player; the
  packages with Play installed, the server confirmed (its page on port 7870
  and its manager on 7871 answered over Wi-Fi), but the menu broke twice in
  its first three starts (stage 4b): the tags wait for that.
- [ ] The tags once the system stayed up on the player (owner, 2026-10-05):
  `v2026.10.02-05a1422` on snowsky-disc-player `c87ddc2` (its workflow
  publishes at once; the published zip's SHA-256 checked against the
  server's catalog), `v2.57.1` on snowsky-disc-server, `v2.57.2` here; the
  drafts rebuilt by CI and compared with the records, published by the
  owner.
- [ ] `install.py` takes `disc-boot` and `disc-usb-console` from the boot
  release by their digests; `build/mips` only for development.
- [x] Project pages for the public releases (owner, 2026-10-06): an optional
  `homepage` in `package.json` and in catalog entries (contract,
  "Packages"), checked by `package.py` and `catalog.py`, ignored by boot
  (`test_package.py`: a good or bad one never stops `disc-boot verify`).
  The catalog names one for each package; diskOS's recipe passes its own
  on. The menu's carries it from the next menu release on: a rebuild of
  2.57.2 from this tree no longer gives its recorded zip, so `v2.57.2`, if
  it is ever tagged, goes on the commit that built it. Checked with
  `test_package`, `test_catalog`, `test_release` (22) and `test_installer`,
  `test_metadata_policy` (17).
- [x] The catalog names disc-server 2.57.5 as the default (2026-10-08): 2.57.4's
  build, whose apps catalog offers Disc Player 1.0.0, the page's first public
  release; 2.57.4 to 2.57.5 through the manager on the guest (boot 2.57.3's
  image), confirmed in 187 s, Disc Player 1.0.0 installed through it
  (snowsky-disc-server `releases/2.57.5.json`, `26d19b4c…`). Its address
  answers once its tag's draft is published. An installer run with
  `--offline` that keeps the server ticked needs that zip and Disc Player
  1.0.0's among its `--from` folders.
- [x] The catalog named disc-server 2.57.3 as the default (2026-10-07): the
  manager reloads itself after a confirmed switch and says the boot layer's
  decision in words; updated from 2.57.2 through the manager on the owner's
  player (snowsky-disc-server `releases/2.57.3.json`, `5a3e63a8…`).
- [x] The catalog named disc-server 2.57.2 as the default (2026-10-06): the
  manager's language and "Player software" with these links; on the owner's
  player 2.57.1 updated to it through the manager and confirmed, the
  server's first update on the device (snowsky-disc-server
  `releases/2.57.2.json`, `6066637f…`). Its address answers once its tag is
  published, with the other tags.
- [x] The status names them (owner, 2026-10-06, for the server's "Player
  software"): the role's status and `ui.json`'s `installed` carry
  `homepage` from the package's manifest, null when it names none or one
  that breaks the rule (dropped, never a reason to refuse the package); a
  status file may hold 8 KiB. `test_boot` (a good and a bad page side by
  side). Reaches the player with the next image, the diagnostics image
  rebuilt with it (build `ef056a613a3f`, image `c460b12e…`), accepted on the
  guest at emulator `f1d5e33` on 2026-10-06 (`work/status-home`):
  `boot_guest`, the diagnostics' own look, `two_packages` (server 2.57.2,
  diskOS with its page, the player page; 6 of 6), `menu_guest` (a menu built
  with its page, 3 of 3), `boot_report`, `boot_layer`, `install.py --guest`.
  `service.json` and `ui.json` named the server's and diskOS's pages (role
  status and `installed`), `menu.json` the menu's. `boot_guest` failed once
  at "installed with play": one reading saw `mq_player` 12230 beside 8589,
  the other not, after 45 steady seconds of one player, a child caught on
  its way to exec; its rerun passed. `found_by_watch_loop` now reads again
  until the two agree, for at most two seconds (not yet run on the guest).

## Stage 4c — the installation with Play, the menu and the faster write (owner, 2026-10-07)

The order (owner, 2026-10-07): first what needs no device (the guest), then
the USB sessions' time offline, one short device session for the timer, and
a write with all of it. Each item with its host tests and the guest.

- [x] The boot log reaches the disk line by line (`fsync` after each line,
  about twenty a start, which also takes the wrappers' lines before it): two
  starts with Play lost their last seconds at a power-off (2026-10-06).
- [x] The keys at an installation: the menu already took a key held since
  power-on only after its release (2026-10-03), and both starts were
  answered by its countdown (5.4 s after it asked), not by Play; they ended
  at about ten seconds of uptime, as a power key held that long switches the
  player off. The installer now says to hold Play until the logo and to
  press the power key briefly (`flow.py`, the contract's user path).
- [x] The installation finishes before the menu offers anything, and the
  menu shows its progress (the item of stage 4b, "The menu during an
  installation with Play"): boot reads the card where stock mounted it or
  mounts it itself (stock mounts it only once its player runs, which now
  waits), writes `install.json`, and both launchers wait for it; the menu
  shows "Installing", asks nothing until it is done (its 60 s from then) and
  then offers what is installed. With Play the launcher always runs, so
  stock's UI no longer starts before the installation, and nothing is
  stopped after it. `test_boot` (the launchers waiting on a card of boot's
  own mount, then the installed UI started with nothing stopped),
  `test_menu` (the installation shown, no answer, then the new list).
  On the guest boot mounted the card itself (`vfat`) at every start with
  Play: stock mounts it only once its player runs.
- [x] The power key held in the menu switches the player off (stage 4b,
  "The power key in the menu"): the menu answers `poweroff` for `0x108`,
  never while boot installs, and writes each key's code to its output;
  boot syncs and powers off as stock's UI does. `test_menu`, `test_boot`.
  The code is still to be seen on the player (the menu's output).
- [x] The menu's status keeps the package's name and version once it has
  answered (`menu.json` named neither on 2026-10-06, so the manager left
  the menu out of "Player software"): the answer's status reads the menu's
  manifest from its slot. `test_boot`.
- [x] The menu starts on the last choice made in it, and its countdown takes
  that one (owner, 2026-10-07): no new setting, a valid answer becomes the
  default UI (`state.json`'s `ui`, logged as "default ui <name>"), which the
  menu is offered as `default`. `test_boot` (the next start's choice and
  the menu's `default`); an explicit default from the manager only if it is
  missed (a package's `ui-default` request exists already).
- [x] Accepted on the guest (2026-10-07, emulator `f1d5e33`, build
  `2506f166236f`, image `e07dc099…`): `boot_guest` (16 steps),
  `two_packages`, `menu_guest` (Play installs the menu and two UIs before
  anything is offered and the keys choose; the countdown starts the last
  answer; a touch chooses), the menu during the installation (it asked
  before `done`, answered after it, nothing stopped), `boot_report`,
  `boot_layer` and `install.py --guest`. Three findings on the way:
  - The installation stopped the launcher it was waiting for: with Play
    every `mq_ui` is the launcher, which `recovery()` took for stock's UI
    before it marked its wait. It stops nothing now (`test_boot`).
  - A start logged every fatal-signal line in the kernel's ring, each
    synced: on the guest, which shares its host's ring, that held up the
    launcher by a second. A start logs the last 8 new ones and a count.
  - Stock's player died at every start after the menu took Volume - and
    Play: the guest's key device is a file a new reader reads from its
    start, so the player, which now starts after the answer, took them
    again. On the player a later reader sees only later presses;
    `menu_guest` empties the file once the menu has answered.
- [ ] The USB sessions' time (stage 4b), in this order: ~~a timer that runs
  in USB Boot~~ the ROM asked until it answers after each batch (done, the
  item of stage 4b: the backup and the readback from 35-40 minutes to a
  few, no device timer needed); the RAM check on the player (done) and the
  staged image's hash there (a 1 MiB sample, measured, the image still read
  back: uncached code); the writer asked until it answers (done); one SPL
  per entry with the check inside the write's own session.
- [x] The asks' settings in a profile of their own (2026-10-07): the first
  installation with them stopped at its review, before any USB access
  ("Evidence firmware/reader/metadata mismatch"). `completion_poll_ms` had
  gone into the transport profile, which the owner's recorded boot and stock
  captures pin byte for byte, and the writer's settings into the installer
  profile, whose RAM contract installations keep identical. Both profiles are
  back as recorded; `firmware/completion/v2.57.json` holds the asks'
  intervals and limits. The review now passes offline on the owner's own
  history, and `test_writer_transport` pins the two fingerprints that
  history holds. Before a device session, the review runs offline on the
  player's history first.
- [x] One held request instead of repeated asks (2026-10-07). The first
  backup with the asks (run `install-20261007-211055`) asked every 50 ms
  after the first batch: 18 asks timed out, the 19th was answered (the batch
  took about 967 ms) and the very next request (the result's address) timed
  out. The ROM keeps every request the host gives up and takes them after
  the payload. Nothing was written (a backup only reads); 2.x went back to
  the proven sessions for a moment (#14). Now the host sends the one CPU
  request right after an execution, held for the whole wait (the settle,
  `staging_check_ms`, `staging_sample_ms`, `writer_wait_ms`) and answered
  once the payload returns: no request is ever given up, the calls are those
  of the fixed wait, and the answer measures the payload
  (`completion_ask` in the completion profile; `false` is the fixed wait).
  On the player, with the owner: a read-only probe (2 blocks, 3 batches, 297
  calls, no timeout; batches of 185, 957 and 964 ms) and a stage-only write
  session (no writer, 9,447 calls, no timeout, 5 min 41 s in all: the image
  region's check 111 s instead of about 7 minutes over USB, the 1 MiB
  sample's hash 4.2 s, so a whole image's hash there would take about 6.7
  minutes and the read back stays). Expected now: the backup and the
  readback about 25 minutes each, the write about 9. `test_collect_rootfs`,
  `test_boot_evidence`, `test_writer_transport` (fake ROMs that keep an
  abandoned request and break the next one), `test_usb_trace_audit`.
- [x] The next write confirms the asks on the player (its backup is the
  first session that runs them), the region's check and the sample's hash
  with their times, and the writer's time, with the rest of the above. The
  fifth write ran the asks (2026-10-07); the sixth ran them in one entry
  (2026-10-08, below).
- [ ] Which pins guard the write and which only add friction (owner,
  2026-10-07: "ой как сложно у нас всё"): after the write, a review of what
  each piece of evidence pins, keeping what proves the right image goes to
  the right player.
- [x] A short identity check instead of the full backup (owner, 2026-10-07:
  the way back is only ever stock, so the backup's bytes restore nothing).
  The backup's one remaining job is to show that the player holds the
  history's image before the write: a read of a few blocks (the probe's 2
  blocks, about a minute, as on 2026-10-07) compared with that image, not
  96 MiB (about 25 minutes with the held ask). What it no longer sees: a
  rootfs changed elsewhere (a FiiO update) beyond those blocks; the write
  replaces the whole rootfs anyway and the check after it proves the result.
  The installer and its review change (the backup is part of the reviewed
  flow): offline on the owner's history first, then on the guest.
  Without a history (a first installation, owner's question, 2026-10-07)
  the blocks are compared with stock of the reviewed version, which the
  installer already builds from FiiO's update (the profile pins its rootfs,
  `111e4dd7…`): the squashfs superblock and first blocks are unique to each
  FiiO build, so another version or another system (diskOS) is refused
  before any write, and the reviewed profiles can name which version it is.
  Blocks inside the squashfs only: FiiO may leave the tail after it other
  than `FF` (the pre-install review's rule). A first installation adds the
  short read of the boot blocks (which rootfs the bootloader selects) to the
  same session: one short read, then the write in a fresh entry.
  Done with a history (2026-10-08, branch `next-image`): `usbboot.identity`,
  the reviewed probe read of 2 blocks compared with the history's image
  (`CHECK`), the write in the same entry; `test_usbboot`. Without a history:
  stage 6.
- [x] The check after the write at the first start instead of the USB
  readback (owner, 2026-10-07). The boot layer, at the first start after an
  installation, reads the rootfs partition as the kernel presents it (the
  view the system boots from, its bad blocks skipped by the kernel's
  driver), computes its SHA-256 (seconds with the CPU's caches, against
  about 25 minutes through the ROM) and compares it with the image's, which
  the installer puts on the card; the outcome goes to the card and the boot
  log, and the installer takes it there. Any corruption, the checking code
  included, changes the hash; it guards against faulty writes, not against
  a deliberate substitution, which the reviewed write path already excludes.
  The writer already reads back and compares every block it programs
  (`my_write5`, its record without a nonce). First: the kernel review, that
  the root device presents the whole padded logical image (its `FF` tail
  included); then the guest. The USB readback stays the way when the result
  does not come back or differs. With both items an installation takes the
  write (about 9 minutes) and a first start, against about an hour now.
  Done (2026-10-08, `next-image`): boot's `rootfs_check` (`test_boot`), the
  installer's `expected-rootfs.json` and its fetch over the USB console into
  `usb/first-start/` (`test_usbboot`), the review taking it as the previous
  write's proof (`test_installed_candidate`; the owner's history passes).
- [ ] One USB Boot entry for an installation (owner, 2026-10-07: easier for
  the user, as diskOS's single `usbboot` run of about 20 minutes, 18 of them a
  blind wait; and users install without a history). What kept sessions out
  of one entry was never the write: each of our sessions is its own tool run
  and loads the SPL again, and a second SPL re-runs the DDR bring-up, whose
  PHY training failed in 3 of 6 recorded re-runs (both after the writer, one
  of four after a read; failure 30, `CALIB_DONE` `0x12`), the first SPL of an
  entry clean in all 11 (`first-write-observation.md`, "The second SPL of a
  USB Boot entry"). The design (2026-10-07), tools kept apart rather than
  merged into one session:
  - [x] The SPL once an entry (`ram_transport.bring_up`): a session first
    reads the DDR diagnostic the SPL leaves in TCSM; the clean one of this
    entry (`d1a6c0de 9 0 0 0`) means DDR is up and the SPL is not run again;
    anything else (a fresh entry) runs it. Each tool (the reads, the boot
    evidence, the write) plans and audits both starts (`test_collect_rootfs`,
    `test_writer_transport`). On the player (2026-10-08, read-only, one
    entry): the first probe found what the power-on left in TCSM (random
    bytes), ran the SPL and read 132 records; the second found the clean
    diagnostic, ran no SPL, passed its RAM checks and read the same blocks.
  - [x] The installation in one entry: without a history, the boot blocks'
    short read (which rootfs the bootloader selects, as the owner's boot
    capture of 2026-09); the identity check of a few rootfs blocks; the
    review offline in seconds while the player waits; the write; then the
    first start's check (above), fetched over the USB console, and a
    readback in a fresh entry only when it is missing or differs.
    With a history done (2026-10-08): `CHECK` then `WRITE` in one entry, then
    the first start's check; on the owner's player the same day (the sixth
    write, below). Without one: stage 6.
  - [ ] The review's two ways: with a history, the identity check against
    the history's image and, for the previous write, its readback or its
    first start's check (the expected image, a match, the primary root
    device, the written build); without one, the fresh boot evidence and the
    identity check against stock of the reviewed version (blocks inside the
    squashfs). Checked offline on the owner's history before any device
    session, and on a synthetic fresh player.
- [x] The fifth write (2026-10-07, run `install-20261007-215049`, image
  `69d82c9d…`, boot release 2.57.3 with the held ask, #15): the backup in
  24 min 11 s (794 batches, median 959 ms, no timeout; 38 min before), the
  write in 9 min 47 s (the image region's check 110.7 s, the sample's hash
  4.24 s, the writer 245.7 s, measured for the first time instead of a blind
  15 minutes; 768 blocks, bad blocks 383 and 716 skipped, no retry), the
  readback in 24 min 3 s, every byte the image's, both audits passed, the
  owner's "it is ok": 58 minutes on the player against about 1 h 45 min
  (`first-write-observation.md`, "The fifth write").
- [x] Found at its first starts (the player's boot log over the USB console,
  2026-10-07), each to fix with a test:
  - The installer left a package staged by an earlier run on the card: the
    first try of the day (stopped at its review) staged the menu, the server
    and the page, the later ones only the menu, and Play installed the
    server too. Staging clears `.disc/boot/install/` of what this run did
    not choose (or names what is there and asks).
  - Installing the version that runs already made the server tentative
    again, and the boot-loop guard counts every start until it is confirmed
    (3 minutes of running): six quick restarts while looking for the menu
    turned the player to stock (`boot-loop`, five unconfirmed). The same
    package (by its digest) already confirmed is skipped ("already
    installed"); and the guard counts only starts that did not reach a
    ready service and UI, not a person restarting.
  - The menu took the release of the recovery gesture as an answer: the
    stock key driver reports Play's gestures when the key is let go (single
    `0xfa`, double `0x10d`, hold `0x10c`), after the menu has started, so
    letting go of Play answered the default. After a start with Play the
    menu takes no Play gesture until the key has been up for a moment after
    it asked.
  - (Not needed, owner: the first start after the image ran the menu
    installed before, 2.57.2, which knows nothing of the installation; only
    the owner's player ever had it, 2.57.2 was never tagged.)
  - The default UI became stock: the old menu's answer at that first start
    is now the last answer. Nothing wrong with the rule; the owner's way back
    to the menu: a start with Play, then 3 minutes of running.
  - The write's progress stood still while a held ask waited (the region's
    check about 2 minutes) and counted the writer as 15 minutes: a held ask
    counts by its expected time (the measured 111 s, 4 s and 246 s).

- [x] The sixth write (2026-10-08, run `install-20261008-195751`, image
  `615d16c7…`, boot release 2.57.4), the first in one entry: the identity
  check in 8.5 s (2 blocks, the history's image), the write in the same
  entry in 9 min 43 s without a second SPL (the region's check 110.7 s, the
  sample's hash 4.21 s, the writer 245.7 s; 768 blocks, no retry), the
  owner's "it is ok", the first start's check over the USB console (the
  written image on `/dev/mtdblock_bbt_ro2`, 18.2 s), no readback: about 15
  minutes on the player against 58 (`first-write-observation.md`, "The
  sixth write"). The menu 2.57.4 and the server 2.57.4 confirmed. The
  installer pinned its review (`972ab515…`); `work/player-history.json`
  now names this run.
- [x] Found after it: the next review on the new history failed in the write
  plan's binding (`validate_binding`), which took only a readback as the
  previous write's proof; it takes the first start's check of the written
  image now (`test_installation_review`), and the review passes offline on
  the owner's new history.
- [x] The installer's last screen says the installation is done and where its
  report is, instead of the first boot's instructions (hold Play), once the
  first start's check was fetched. Its words after the write follow how the
  player leaves USB Boot (owner, 2026-10-08): disconnected, it restarts into
  the written system by itself (the menu shows), so a start with Play comes
  only after that start, switched off and on again (`test_installer`).
- Play's timing at power-on stays as it is (owner, 2026-10-08: still awkward
  to press together with the power key): the menu's installation (stage 7)
  makes it unnecessary for packages.

## Stage 6 — the user's path (owner, 2026-10-06)

The order (owner, 2026-10-06): the menu's failure and the diagnostics (a new
image and write), then the faster write session (stage 4b), then this stage,
then the tags. Run from a clean clone of `2.x` on 2026-10-06, `install.py
--dry-run` stopped at its first check: it needs a local build of the boot
layer and the emulator's checkout to build the image.

  All fixed on `next-image` (2026-10-08) with their tests: the card cleared,
  the running package left as it is, the count cleared when proven packages
  are ready (`boot_guest`'s probe now writes its slot to the disk before its
  request, so its boot-loop step runs the tentative version), the recovery's
  Play release, the progress by the held asks' measured times.
- [x] The boot layer from the release file (`disc-boot-<v>-mips.tar.gz`) by
  the digests of its record, not a local build (2026-10-08): the newest
  release recorded for the firmware, found by its digest (`--from`, earlier
  downloads) or downloaded, its build id the record's; `--boot-build` takes
  `build/mips` for development (`test_release`). Its programs are the local
  build's, byte for byte.
- [x] The same update and boot release give the same image (2026-10-08): the
  image built from 2.57.4's release file differed from the recorded one
  (`5c89a583…` against `615d16c7…`) only in the build's time on the added
  files and in the superblock. The additions now take the stock file
  system's time, the stock folders keep theirs and the superblock is packed
  with it: two builds gave one image (`4f8d68b6…`, `test_deployment`). An
  image is then known by the update and the release, as the identity check
  of a player without a history needs. The next boot release's image is the
  first so built; across tools it holds while the same `mksquashfs` (4.5.1,
  the emulator's image) packs it.
- [x] A light image builder of our own (Python and `squashfs-tools`), with
  the emulator's reader of FiiO's update fetched at its pinned revision
  rather than the emulator's image with its qemu build. Done 2026-10-08 as
  the same builder run on the computer without root: squashfs-tools 4.6 or
  later and openssl, the reader (`firmware/tools/firmware_inventory.py`)
  fetched at `f1d5e33` by its digest (`firmware/sources/emulator.json`),
  stock's owners, setuid bits and times packed from stock's own listing,
  every check kept. On macOS (squashfs-tools 4.7.5) the installer's dry run
  built 2.57.4's image in about a minute without Docker: the very image of
  the container (`4f8d68b6…`). `test_deployment`, `test_sources`.
- [x] The USB payloads from the release (owner, 2026-10-08: the installer
  compiled them with diskOS's toolchain image, which a new user lacks). The
  boot layer's own toolchain gives the very payloads diskOS's gave (the five
  of the sixth write's run, byte for byte); `build.json` names the compiler
  instead of the image's id, so a build is the same on any computer; the
  release carries `disc-usb-payloads-<v>.tar.gz` (the five and the boot
  evidence's two), built twice to the same bytes, and the installer takes it
  by its record's digest (`--boot-build`, or a release before 2.57.5, builds
  them with Docker). The review on the owner's history passes on the
  release's payloads with nothing compiled. Found on the way: the boot
  evidence's build had failed since the read by digest (stage 4b) asked its
  scopes for a mode; fixed. `test_release`, `test_usbboot`.
- [x] The image built so on the guest and on the player (the next boot
  release's image), then the installer's Docker build goes; the emulator
  stays for the guest. The guest (2026-10-08): release 2.57.4's image built
  on the computer without root or Docker (`4f8d68b6…`, the Docker build's
  bytes), `install.py --guest` the user's way with the catalog's published
  packages (menu 2.57.4, server 2.57.5, Disc Player 1.0.0): installed by
  Play, the menu answered, the service confirmed. Known by its first blocks
  (`firmware/images`), as the owner's player will hold it after the user
  path's test. The player (2026-10-08, the seventh write,
  [record](observations/first-write-observation.md#the-seventh-write-the-users-path-without-a-history)):
  written, and its first start found `4f8d68b6…` on its root device.
- [x] The installer's Docker build of the image goes (the condition above is
  met): squashfs-tools 4.6 or later and openssl only; the emulator stays for
  the guest. The USB payloads still need the toolchain image until a boot
  release carries them (2.57.5). Done 2026-10-09: the check names
  squashfs-tools and openssl with their install commands and asks about
  Docker only for `--guest` (`test_installer`).
- [x] The user answers only yes or no at the first start (owner, 2026-10-08):
  the words are the developers' evidence (`owner-boot-confirmation.json`,
  read by the next review with a history), never needed without one. Done
  2026-10-09: without a history, and on the way back to stock, the
  installer asks yes or no only (`test_usbboot`). To weigh still: no
  question at all when the first start's check comes back (the system
  started and holds the written image), the question only when it does not
  (then it decides the way back to stock).
- [x] diskOS's writer and SPL fetched at their pinned revision, as the diskOS
  package already is (`catalog.py fetch --name diskos --download`, checked
  from a clean clone on 2026-10-06). Done 2026-10-08: the six files the
  tools read (the writer, its source, the SPL and its source, `flasher.py`,
  `usbboot.c`), by the digests the reader, writer and probe profiles pin, from
  the revision `firmware/sources/diskos.json` names: a checkout given with
  `--diskos`, a copy kept in `work/downloads`, or fetched file by file (about
  15 MB, 5 s here) and checked; none of them is kept in this repository
  (owner: the SPL carries vendor-origin DDR bytes that diskOS itself has not
  cleared for redistribution). The review gives the same plans with the
  fetched copy as with the owner's checkout (`test_sources`).
- [x] The user's installation without a history (owner, 2026-10-08: the
  history is the developers' evidence; a user needs none). Every run decides
  by digests in one USB Boot entry: the boot evidence read there (the
  bootloader's selection and the kernel, so the player runs the reviewed
  FiiO version), then the identity probe of the first rootfs blocks
  compared with the images known: stock (from the user's FiiO update) and
  each boot release's image (reproducible since this stage: its digest and
  its first blocks' digest recorded with the release; the images before
  2.57.5 listed by their recorded digests, the owner's player holding
  `615d16c7…`). A known image is installed over or updated; an unknown one,
  or another FiiO version, stops the run before anything is written and
  offers the way back to stock: FiiO's own Local upgrade (no version gate,
  its zip at the card's root; the owner checks it later) or ours through USB
  Boot. After the write, the first start's check as now. No history, no
  captures, no review pin moved after each write for the user; `--history`
  stays the developers' way. The design (2026-10-08, accepted by the owner
  with its two risks: an image is known by its first 256 KiB, and the kernel
  partition is never read, since neither our writes nor any image of ours
  touch it and FiiO's update writes the kernel and the rootfs together):
  - [x] The known images (`firmware/images/v2.57.json`: stock and the owner's
    2.57.3 and 2.57.4 by their digests and their first blocks' digests;
    `scripts/deployment/known_images.py` adds every release's recorded image
    and refuses two that share their first blocks; `release.py record
    --image` records the image the guest ran). `test_known_images`.
  - [x] The boot evidence's audit as a tool of its own
    (`scripts/deployment/audit_usb_boot.py`, 2026-10-08): the session
    reconstructed from its saved files, the plan, the build and the SPL,
    with the entry's diagnostic (the SPL skipped when clean), the held
    completion ask and any bad blocks; its report is the review's
    `offline-review.json`. It replaces the first observation's one-off
    script. `test_audit_usb_boot` (full-size sessions of the fake ROM,
    tampered files refused, the review's `evidence()` taking the report).
  - [x] The identity probe's audit (`scripts/deployment/audit_usb_probe.py`,
    2026-10-08): the probe session reconstructed the same way, its report
    giving the first blocks' digest; the records and calls the two audits
    rebuild shared in `usb_trace_audit.py`. `test_audit_usb_probe`. On a
    player's session: the sixth write's identity check (2026-10-08, the
    sources it pinned at `8fab64b`) matched, 300 calls and 132 records, its
    first blocks `837ed890…`, recomputed the same from its records
    (`readback.first_blocks`) and named release 2.57.3 by the known images,
    the image the player held then.
  - [x] A review of its own (`source_state` `known-image`,
    `installation_review.py --known`, 2026-10-08): in one entry the metadata
    page (the entry's only SPL), the boot evidence with it, the identity
    probe, then the review offline in seconds: both sessions audited and
    carrying out the plans computed from the reviewed builds, of one entry,
    the first blocks recomputed from the probe's records and known, the way
    back stock, the write's ABI the one the sixth write ran
    (`firmware/images` `exercised`); an admission computed for the run in the
    package, never written to `firmware/installers`
    ([installation review](installation-review.md)). `test_known_review`.
  - [x] The write refused unless it runs in the same entry (its SPL skipped),
    with the admission the package computed for the run (2026-10-08): a
    `known-image` review's write plan carries `same_entry_required`, and a
    session that finds no clean DDR diagnostic stops before any SPL
    (`ram_transport.bring_up`); `writer_transport.py --installer-profile` takes
    the package's admission only for such a review, differing from the
    tracked profile only in its admission and pin (`run_layout`).
    `test_writer_transport`.
  - [x] The installer's user path (no `--history`), and `--restore` without
    a history (2026-10-08): in a terminal, after `CHECK` the partition
    table's page (the entry's SPL), the boot evidence and the identity probe,
    the last two audited; the review `--known`; `WRITE` with the run's
    admission in that entry; the first start's check, the readback only when
    it does not come back; no history. A no goes back to stock with a new
    entry's evidence (the review knowing this run's own image); `--restore`
    the same way; an unknown image stops before anything is written, naming
    FiiO's Local upgrade; without a terminal the card only
    ([development](development.md)). `test_usbboot` (`KnownPathTests`).
  - [x] On the owner's player after FiiO's Local upgrade back to stock (the
    owner's step, on its own go-ahead). Done 2026-10-08, the seventh write
    ([record](observations/first-write-observation.md#the-seventh-write-the-users-path-without-a-history)):
    the Local upgrade took our image back to stock (no version gate, about 2
    minutes); `install.py` with no history found stock, wrote the image in
    the same entry and its first start proved it, about 13 minutes in all.
    `/usr/data/disc-boot` stayed through the Local upgrade, so the first
    installation of a new user's packages is tested in stage 7, after a
    removal of everything ours.
- [x] Back to stock from any state without a history (owner, 2026-10-09,
  after the seventh write: a write cut short leaves first blocks the review
  does not know, and FiiO's Local upgrade needs a system that starts):
  `installation_review.py --known --allow-unknown` names such an image
  `unknown` and admits the restore only; `install.py --restore` asks for
  `STOCK` first, naming the kernel's risk (not read). A candidate is never
  written over an image the review does not know. `test_known_review`,
  `test_usbboot`.
- [x] The installer for users as one archive in the boot release
  (`disc-installer-<v>.tar.gz`, owner, 2026-10-08): `install.py` with the
  scripts, profiles, catalogs and source pins it needs, this release's files
  (the boot programs, the USB payloads, the menu) and the default server and
  Disc Player; nothing to clone. Not in it: FiiO's update (the user's),
  diskOS's files (fetched by their pins), Python 3.11+, libusb,
  squashfs-tools and openssl (checked, with the command that installs each).
  The user's runs kept outside it (`~/Library/Application Support/SNOWSKY
  DISC`, `~/.local/share/snowsky-disc`). macOS and Linux first; Windows
  later (WinUSB, with the ready-to-run installers under "Later").
  Done 2026-10-09 (`release.py installer`, [development](development.md#releases)):
  the archive holds `install.py`, `console.py`, the scripts, profiles,
  catalogs, earlier records, the payloads' sources and the image's files at
  the release's commit, this release's files and the catalog's default
  server and apps (`packages/`), and `installer.json` (this release, whose
  record names the archive's digest, so it is never inside); the same bytes
  on every build, rebuilt and checked by the release workflow. From the
  unpacked archive the installer takes its own packages first and keeps the
  runs in the user's folder. `test_release`, `test_installer` (the archive
  unpacked and run without the repository, offline).
- [x] The computer's check explains libusb and Docker where they are missing
  (2026-10-08): libusb is found where Homebrew or the system's packages put
  it (`--libusb` only to name another), and a missing one is said with the
  command that installs it; the image needs squashfs-tools and openssl,
  said the same way, and Docker only for the guest (`test_installer`).
- [x] The README's guide: what is installed, the risk, the way back, the
  packages. Done 2026-10-09: what you get, what you need (with the install
  commands), the steps with the typed words, the risk, both ways back (FiiO's
  Local upgrade as checked on the player, `install.py --restore`), the keys
  at power-on; the menu's own pictures. It is the archive's user guide.
- [x] Two timing tests fail now and then under the full suite's load and pass
  alone (`test_usb_console`'s unmounted card, `test_boot`'s menu hand-over
  order, 2026-10-08): wait for the event, not for a time. Done 2026-10-09,
  reproduced with the tests run 16 at once (15 of 16 failed): the console
  test unmounted the card once the gadget was bound, before the start's
  marker check (now it waits for the shell's answer); the boot fixture gave
  the menu 2 s and a package 5 s to be ready, real bounds of the boot program
  that a busy computer missed (now 20 s and 30 s, the short ones only where a
  test checks the bound itself), and the hand-over test's own waits are 30 s
  bounds for the events. 16 at once: none failed; `test_boot` takes as long
  as before.
- [x] The whole path from a clean clone, in CI where it can run (no player)
  and on a clean computer. In CI since 2026-10-09: the installer's archive
  built from the checkout, unpacked and run without the repository, offline
  (`test_installer`); the image and the guest need FiiO's update, which CI
  has not. The clean computer: the release's acceptance (2026-10-09,
  release 2.57.5): the guest from the archive unpacked outside the
  repository with an empty home; then the owner's player from another
  computer with the archive only (Python 3.14, libusb found, no Docker): the
  player's image known by its first blocks (2.57.4's `4f8d68b6`), written in
  the same entry, the first start's check matched, no history
  ([record](observations/first-write-observation.md#the-eighth-write-release-2575-from-a-clean-computer)).

## Stage 7 — the menu's three screens and services beside the server (owner, 2026-10-07/08)

After stage 6. The owner's concept (2026-10-07): the server manages only its
own web apps and its updates; the boot packages (menus, interfaces,
services) are installed and removed in the menu on the player, boot staying
the only installer. Play at power-on stays the recovery path only.

- [x] First, the repository's layout (owner, 2026-10-08): one folder for each
  component under `device/`, named as its package without `disc-`:
  `boot/` (today's `device/src`), `console/` (disc-usb-console), `menu/`,
  `health/`, `network/`, `common/` (SHA-256, manifests and the like),
  `usbboot/` (the programs run from RAM in USB Boot, today's
  `acquisition/`), `scripts/` (what goes into the image, today's
  `deployment/`), with `tests/`, `vendor/`, `licenses/`, the Makefile and
  the toolchain. `device/` stays the line between what runs on the player
  (C, built for MIPS) and what runs on the computer (the installer, Python).
  A move alone first, in its own PR: the same programs, only the paths in
  the build, CI, `release.py` and the documents change (the build id
  changes with them). Done 2026-10-09: `device/boot`, `device/console`,
  `device/common`, `device/menu`, `device/usbboot`, `device/scripts`
  (`health/` and `network/` come with their code); the payloads built from
  the new paths are 2.57.5's byte for byte (all seven `identity.bin`), so
  the ABI a player ran stays. Their build manifests name the new paths, so
  until 2.57.6 carries payloads built so, the repository's installer builds
  them itself (`--boot-build`); an archive keeps its own tree.
- [x] The documentation split, after the layout's move (owner, 2026-10-09):
  for users (the README, the installation guide, what is installed and its
  risks, the way back to stock, the CHANGELOG) and for developers
  (development, architecture, contract, reviews, observations, this plan),
  each in its own place, with the links between them kept. Done
  2026-10-09: for users the README (what you get, the quick start, the keys),
  `docs/install.md` (what you need, the steps, what is installed, the risk)
  and `docs/way-back.md`, carried by the installer's archive; for developers
  `docs/dev/` and the records in `docs/dev/observations/`; every relative
  link rewritten and checked, snowsky-disc-web's own paths left as they are.
- [x] The menu's three screens: the interface (today's), the services'
  autostart, installing and removing (the card's staged packages shown
  there; never automatic). A specimen page and numbered decisions first;
  no screen before it works. The manager's "Player software" becomes
  read-only with a hint.
  Done 2026-10-09 by the owner's eight decisions on the specimen
  ([contract](contract.md#the-menus-screens)): Services and Packages as rows
  at the end of the interfaces' list, Back first on each; the countdown
  standing while a package waits on the card; autostart from the next start;
  a removal by a second Play, with the package's data (a ui package at the
  hand-over, a service at the next start), only ui and service packages;
  each staged package installed by its own row, boot installing it as the
  recovery does; "Everything ours" last, two questions, applied at the next
  start before anything of ours runs (`/usr/data/disc-boot` and the card's
  `.disc`; music and `Apps` stay); the manager's hint (snowsky-disc-server,
  its plan, stage 5). Boot offers the menu what it shows in
  `ui/choices.json` and takes its changes through new commands of the boot
  program (`autostart`, `remove`, `install`, `remove-everything`). The font
  gained ‹ and › (Inter regenerated byte for byte with the same Pillow and
  FreeType); the pictures come from the menu itself. `test_menu` 14,
  `test_boot` 65 (macOS and Linux); on the guest `menu_guest.py`: the first
  three steps with the new list, then autostart off, a ui package removed
  at the hand-over, a package waiting on the card holding the countdown and
  installed from the menu, everything of ours gone at the next start (6 of
  6), and `boot_guest.py` 16 of 16, on the image of `d1ad405`. The guest
  found that the card, mounted already for reading and writing, takes no
  second read-only mount: the look uses the recovery's flags then.
- [x] The roles, laid out before other players run the boot layer (owner,
  2026-10-08): `controller`, at most one, the server (it owns the player's
  protocol, listens on the network and updates itself through its
  manager); `service`, any number with distinct names, background jobs
  (disc-health, disc-network) that listen on no network port and report
  through their status files, each with its own lifecycle, an autostart
  flag and limits; `ui` and `menu` as now. Boot API 2:
  `/usr/data/disc-boot/controller/` (`a`, `b`, `state.json`, `request`) and
  `service/<name>/` as `ui/<name>/`; the status folder's `controller.json`
  and `service/<name>.json`. The move without a reinstallation: the new
  boot program renames `service/` to `controller/` once, atomically, and
  logs it; a package of boot API 1 with the role `service` (disc-server up
  to 2.57.5) is installed and run as the controller; the server reads
  `controller.json`, else `service.json`, and its next package says
  `controller` with boot API 2. The server finds every other path in its
  environment (`$DISC_BOOT_INACTIVE`, `$DISC_BOOT_REQUEST`, …), so its
  binary does not change for the move.
  Done 2026-10-09 (boot API 2; [contract](contract.md#roles)), with the
  owner's decisions of that day: services take no part in the boot-loop
  guard (a failing one stops alone, with its reason); each starts after the
  controller is ready or settled, at nice +10 with its address space bounded
  by its manifest's `memory` (16 MiB unless it names 1-64; the controller
  keeps nice +5 and no bound); `autostart` lives in its state (on unless
  false; the menu's services screen will change it); a service asks only for
  `activate`, `rollback` and `remove`; listening on no port is a rule of the
  catalog's acceptance. The move: the first boot renames `service/` to
  `controller/` once and logs it; a service package of boot API 1 is the
  controller (told the role it names), its status also in `service.json`;
  `verify service` takes a controller's package for that server's own
  updates; the card's old `install/service/` is the controller. The tools
  stage by role (`install/controller/`, `install/service/<name>/`), the
  installer offers a server and services and removes the old folder, the
  boot report has each service. `test_boot` 62 (macOS and Linux, where
  `RLIMIT_AS` applies) and the tools' tests. The image of `944d9c5`
  (`3c3c141a…`, the same bytes built on macOS and in the container):
  `boot_report.py`, and `boot_layer.py` on the packed tree (API 1's
  `service/` moved, the controller and a service confirmed after 181 s). On
  the guest (emulator `f1d5e33`, FiiO 2.57): `roles_guest.py` 5 of 5 (the
  move, the old server's update to a controller, two services with Play, one
  failing while the count cleared, autostart off, stock mode; nice 5 and 10;
  qemu-user ignores `RLIMIT_AS`, so the bound takes effect on the player
  only), `boot_guest.py` 16 of 16, and `install_guest.py` with the published
  disc-server 2.57.5 (a package of API 1, staged in `install/controller/`):
  installed by Play, confirmed, serving Disc Player 1.0.0, its manager
  taking updates (`service.json` read). snowsky-disc-server reads `controller.json`, else `service.json`,
  and its manager lists the services (its plan, stage 5); its package keeps
  API 1's service role until this boot layer is released.
- [x] disc-health and disc-network are built here, as the menu is (owner,
  2026-10-08): native code beside `device/menu`, the same toolchain, released
  with the boot layer's numbers (`disc-health-<v>.zip`,
  `disc-network-<v>.zip`) and named in the catalog, so the installer offers
  them in the same entry as the image and boot installs them at the first
  start. Neither needs the server: each writes a small status file of its
  own in the boot layer's status folder, which the server shows (as it
  shows `ui.json` and `menu.json`). The installer's defaults (owner,
  2026-10-08): disc-health ticked (it only reads), disc-network offered but
  not ticked until it has run on the owner's player. Done 2026-10-09: both
  in `device/`, in every release (`release.py installer` requires the
  catalog to name them, disc-health ticked); the catalog's entries come with
  the next release, as the menu's do.
- [x] disc-health (owner, 2026-10-07): a read-only, offline journal of the
  battery, temperature, uptime, crashes, card errors and free space, shown
  by the server's diagnostics. Done 2026-10-09 ([disc-health](health.md)):
  `device/health`, built beside the menu; at its start and every 10 minutes
  the fuel gauge (the first power supply with a capacity: `cw221X-bat`), the
  thermal zones, uptime, load, memory, the free space of `/usr/data` and the
  card, the kernel's card errors and fatal signals and stock's restarts of
  its pair; a line of its journal (256 KiB in two files) and its report
  (`status.json`, at most 4 KiB). A release carries
  `disc-health-<version>.zip` (role `service`, `bootApi` 2), which the
  catalog offers ticked. `test_health` 6 (macOS and Linux; under the boot
  program's fixture as a service); on the guest (`health_guest.py`, the
  MIPS build under boot `944d9c5`'s image) installed by Play, confirmed,
  reading the emulator's gauge and the card, and a changed gauge at the next
  start. The guest found that `/proc` and `/sys` say 0 or a page as a file's
  size (uptime read as 0): they are read to their end; and the card comes
  after the service's start: a reading without it is taken again once it is
  there. The thermal zones of the player are not known yet: its first
  reading says. snowsky-disc-server shows the report (its plan, stage 5).
- [x] disc-network (owner, 2026-10-08): the player keeps several Wi-Fi
  networks (home, office) and joins whichever is in range without the
  password again. Why it does not today (static analysis of V2.57's
  `mq_player`, not yet observed on a player): the stock connection
  (`connect_wifi`) removes every saved network before it adds the new one
  and saves, so `/usr/data/wpa_supplicant.conf` holds one network; the
  Wi-Fi screen always asks the password and its "Forget" and "Reconnect"
  send nothing; `fix_wpa_conf_ssid` rewrites the file's first `ssid=` line
  through a 4 KiB buffer. The service: after a stock connection
  (`wpa_state=COMPLETED`, the file's time steady) it keeps the new
  network's block in its own store (mode 0600, on the player, never on the
  card); it adds the others after stock's own (`add_network`,
  `set_network`, `enable_network`, optionally `priority`, `save_config`),
  again at boot and when Wi-Fi turns on; a few networks only (the 4 KiB).
  Its API and the server show names, never keys (a PSK in hex is the
  password). "Reset all" clears its networks too (owner: before selling the
  player, these settings go with the rest): the store lives in
  `/usr/data/fiio/wifi/`, which stock's reset removes, or the service
  notices the reset (the stock header alone and that folder gone). The
  emulator has no Wi-Fi: checked with a stand-in `wpa_cli` on the guest,
  then on the player.
  Built 2026-10-09 ([disc-network](network.md)) with the owner's decisions of
  that day: the store in its own folder (`$DISC_BOOT_DATA/networks.json`,
  0600), forgotten when Wi-Fi comes on with a file holding no network (a
  reset); stock's networks changed only through stock's wpa_supplicant (the
  contract's one exception); every 5 s while Wi-Fi is on, acting only when
  wpa_supplicant's networks are those the file saves and both stayed so for
  two looks (never in the middle of stock's sequence); added back after
  stock's own with `priority=-1`, and enabled again after stock's selection;
  at most 8, the least recently used dropped; names only in its report;
  forgetting one network with the menu's services screen. `test_network` 6
  (macOS and Linux) against `wpa_cli_stand_in.sh`; on the guest
  (`network_guest.py`, the MIPS build, the stand-in in `wpa_cli`'s place
  after the emulator's own guard): Home kept, Office then Home added back
  below it, the store 0600 and no key in the report, confirmed, and the next
  start joining Home. snowsky-disc-server lists the networks (its plan,
  stage 5).
- [x] disc-network on the owner's player: two networks joined in turn
  through stock's screen, then the player joining either by itself; then
  the installer ticks it. 2026-10-09, release 2.57.7's design (the kept
  networks given back beside stock's): the player switched by itself, but
  stock's UI crashed twice while a password was typed (two networks in its
  configuration; [network](network.md#on-the-owners-player-2026-10-09)), so
  disc-network was turned off there and left out of 2.57.7. Its next design
  keeps one network in stock's configuration and puts a kept one in range in
  its place (owner, 2026-10-09; `test_network` 10 on macOS and Linux);
  `disc-network-2.57.8.zip` for the guest's `network_guest.py`, then the
  player: two networks joined in turn, each joined again by itself, and a
  password typed in stock's Wi-Fi screen without its UI failing. Done
  2026-10-10: `network_guest.py` 5 of 5 with the 2.57.8 package; on the
  owner's player (release 2.57.7's image, the package installed from the
  menu) stock joined iPhone XV with its password typed, its UI ran on (no
  fatal signal), the configuration held one network throughout; with the
  phone's hotspot off, disc-network put MikroTik_83 in its place 36 s later
  (the minute after stock's connection) and the player joined it; confirmed.
  Next: a release that carries it ticked, with the packages without the image.
- [x] Packages without the image (owner, 2026-10-09): `install.py --packages`
  skips FiiO's update, the image and the player, checks the catalog, takes
  the chosen packages by their digests and stages them on the card; the
  player installs them from the menu's Packages screen or with Play. Only
  the installer changes, not the image. 2026-10-10: the mode (Python alone
  is checked; the player's options refused), `test_installer` 21; from
  2.57.8's unpacked archive with an empty home, `--packages --dry-run`
  staged the menu, disc-server, disc-health, disc-network and Disc Player.
  On the owner's player (2026-10-10), twice: the card through stock's
  Working mode → USB Storage, no card reader; the menu offered the packages
  and installed each from its row; disc-health 2.57.8 started in its new
  slot at once (18 s after the start, no restart of the player), the menu
  kept while used past 60 s, then the server 2.57.6 the same way. The guide
  has "Updating the packages".
- [x] Release 2.57.8 (2026-10-10): no new image (build `8eb15a998d85`, the
  image `9e6448dd…` as 2.57.7's), disc-network 2.57.8 offered by default
  (one network in stock's configuration), `install.py --packages`, the
  texts on Play only the first time. The menu and disc-health are 2.57.7's
  binaries byte for byte; disc-network's package is the one accepted on the
  guest (`network_guest.py` 5 of 5) and on the owner's player. On the guest:
  `install.py --guest` from the unpacked archive with an empty home (the
  menu, disc-server 2.57.5, disc-health, disc-network installed by Play and
  confirmed; the image built on the computer `9e6448dd…`), again with the
  server 2.57.6 in its catalog (installed by Play, confirmed). On the owner's
  player with `install.py --packages` from this archive (above), the server
  2.57.6 included. Recorded (`releases/2.57.8.json`); tagged and published
  after the server 2.57.6, whose package its catalog names (the owner,
  2026-10-10: the tags and the publication by Claude, in that order; 2.57.7
  published the same day).
- [ ] Updates over Wi-Fi from the server's page (owner, 2026-10-09; after
  disc-network's check): the controller may ask boot to install a package it
  staged on the card (`disc-boot install <folder>`, the menu's command) and
  boot starts it at once (as the menu's installation does since 2.57.7). The
  contract's rule for that request here; the server's side (the catalog,
  each package by its digest, signed by our key) in snowsky-disc-server's
  plan.

- [ ] Remove everything ours (owner, 2026-10-08: before the player is sold,
  and for a clean test): in the menu, the boot layer's packages and their
  data in `/usr/data/disc-boot`, disc-network's networks and the card's
  `.disc` state (music and `Apps` asked about separately); then FiiO's Local
  upgrade leaves a stock player. Neither the Local upgrade nor stock's
  "Reset all" removes `/usr/data/disc-boot`. After it, the user path's test
  on the owner's player from a really new state: the first installation of
  the packages, a card without `.disc`. diskOS reinstalled then too (owner,
  2026-10-09): the player holds a diskOS 1.2.0 package assembled before
  packages named their project page and title, so the manager shows it as
  `diskos` without a link; the installer's assembly now gives both.

- [x] FiiO's own interface first in the menu's list (owner, 2026-10-09): the
  menu offers stock first, then the installed interfaces, so the project does
  not look built around diskOS, a project of its own (today the boot program
  lists the packages first and stock last: `ui/choices.json`). The default
  stays the last answer; the README's picture of the menu drawn again. Done
  2026-10-09: `write_choices` lists stock first (the contract says so),
  `test_boot` and `menu_guest.py` (rows by position) follow, the pictures
  show FiiO first and chosen. On the guest with release 2.57.6.
- [x] `release.py installer` downloads into a folder it creates (2026-10-09:
  on the tag v2.57.5 the release workflow found no folder to download the
  server and Disc Player into, since every local build had been given them
  with `--from`; the workflow downloads them first now, `release.bundled`
  into its own folder). The fix in `release.py` changes the archive, so it
  goes with the next release, with a test that downloads. Done 2026-10-09:
  `release.bundled` makes the folder it downloads into; the workflow calls
  `release.py installer` alone again; `test_installer` downloads the server
  from a stand-in for GitHub.
- [x] The player restarted from USB Boot by the installer (owner, 2026-10-09:
  FiiO's update restarts the player itself): after the writer's completion,
  in the same entry, a small RAM payload starts the watchdog as the pinned
  SPL source's `_machine_restart` does (TCU `TSCR`, `WDT` `TCNT`, `TDR`,
  `TCSR`, `TCER`), the ROM restarts the chip and, Volume Down no longer
  held, it starts the new system from NAND with the cable still connected;
  the installer then waits for its USB console and reads the first start's
  check, so the user neither unplugs nor plugs the cable, only answers.
  Its own plan, payload and journal audit as the others. On the player
  first, without a write (a session that only restarts): that the ROM
  boots NAND after the watchdog, and that stock starts normally and leaves
  the port to the USB console with the cable connected at power-on. After
  2.57.5, in its own release. 2026-10-09: the payload (`device/usbboot/restart.c`,
  `--mode restart`, 344 bytes; the other payloads unchanged), the session
  (`restart_player.py`) and its audit (`audit_usb_restart.py`), with the three
  outcomes on a fake ROM ([restart](restart.md)); next the session alone on the
  owner's player, then the installer. On the player the same day, alone and
  without a write (the owner, from another computer): `player-restarted`, the
  menu then stock with the cable connected, its audit matching. The installer
  restarts the player after every write in its entry, the cable the way only
  when that fails; the payload goes into the release's payloads.

- [ ] The card over the cable, no card reader (owner, 2026-10-09): USB Boot
  cannot reach the card (the ROM writes and reads RAM and runs code; an SD
  driver and an exFAT writer run from RAM would risk the music on it), but
  after the restart the system runs with the cable connected, and stock's
  `mq_player` offers the card as a USB drive (`storage_demo`, its USB mode
  loop, [diagnostics](usb-diagnostics.md)). The installer would then write
  the packages, apps and console marker there, and read the first start's
  check from the card, after the write instead of before it. To settle on
  the player first: how stock enters that mode (by itself on a cable, a
  setting, a prompt), that it and the USB console share the port in turn,
  and a first start's check without an expected digest on the card (the
  installer compares it). 2026-10-10, the owner: the card needs no reader;
  the player switched to stock's USB storage shows it to the computer as a
  drive, which the installer takes as any card (`--packages`, and the card's
  step of an installation before USB Boot). The guide and the installer say
  so; the installation's card after the write stays open.

- [x] Release 2.57.6 (2026-10-09): FiiO's own interface first, the player
  restarted by the installer after the write, the archive's downloads into a
  folder of their own. On the guest: `boot_guest.py` 16 of 16 (its offer's
  order fixed in the test), `menu_guest.py` 3 of 3, `two_packages.py` 6 of 6,
  `boot_report.py` and `boot_layer.py`, `install.py --guest` from the
  unpacked archive with an empty home; the image `c46ad1f1…` the same on
  macOS without root and in the Linux container. On the owner's player from
  the other computer with the archive only: written in the review's entry,
  the first start's check matched ([record](observations/first-write-observation.md#the-ninth-write-release-2576-the-player-restarted-by-the-installer)).
- [x] The run's report names the restart's outcome (`report.json`, the player
  step), not only its `usb/restart/result.json`; with the next release, as
  it changes the archive. Done for 2.57.7: `restart` in the player's step
  (`outcome`: player-restarted, restart-not-observed, restart-uncertain,
  failed or not available, and its run folder); `test_usbboot`.
- [x] Release 2.57.7 (2026-10-09/10): services beside the server (boot API 2),
  disc-health, disc-network (named, not ticked), the menu's three screens,
  the restart's outcome in the report. On the guest, with these binaries
  (build `d1ad405880ec`; the menu, disc-health and disc-network byte for byte
  the ones accepted; the USB payloads 2.57.6's): `boot_guest.py` 16 of 16,
  `menu_guest.py` 6 of 6, `roles_guest.py` 5 of 5, `health_guest.py` 3 of 3,
  `network_guest.py` 4 of 4, and `install.py --guest` from the unpacked
  archive with an empty home: the image built on the computer from the
  release file (`6d1a6780…`, the tested one), the menu, disc-server 2.57.5
  (the controller), disc-health and disc-network installed by Play and
  confirmed. The owner's player with the archive (2026-10-09): written and
  restarted by the installer (`player-restarted`), the server of boot API 1
  moved to `controller/`, the four packages installed by Play and confirmed.
  There disc-network gave no network back: stock's wpa_cli took `priority -1`
  for an option (glibc's getopt), so the service ends wpa_cli's options with
  `--` and the stand-in parses as stock's does. disc-health's first reading of
  each start had the time 6 h ahead (the hardware clock holds local time until
  stock's player sets the clock) and a current of 1 or 0 (the gauge's, not
  µA); the player has no thermal zones. A reading now has the time once the
  clock is set, and no current. Both fixes are only in their packages: the
  build id counts only the image's programs, so the image stays `6d1a6780…`
  and `d1ad405880ec`. Again on the guest: `network_guest.py` 4 of 4,
  `health_guest.py` 3 of 3 and `install.py --guest` from the unpacked archive.
  On the player with both packages (2026-10-09/10): disc-health's first
  reading without the time, the next with it, no current; disc-network
  switched by itself, but stock's UI crashed with two networks in its
  configuration, so disc-network left the release (its next design keeps one;
  see its item above). The menu's installation of the two services showed two
  faults of boot: the menu was stopped 60 s after its start while being read,
  and the services ran their previous version on, which then confirmed the
  new slot. Fixed in boot (the menu's time from the owner's last use, a slot
  installed over the running one started at once, a confirmation naming the
  slot that ran), so the image changes: build `8eb15a998d85`, image
  `9e6448dd…`; the installer knows the first build's image (`6d1a6780…`,
  first blocks `eff13569…`) that the owner's player holds. On the guest with
  these binaries: `boot_guest.py` 16 of 16, `menu_guest.py` 7 of 7 (its new
  step: the menu used beyond 60 s, a service installed over the running one),
  `roles_guest.py` 5 of 5, `health_guest.py` 3 of 3, and `install.py --guest`
  from the unpacked archive with an empty home (the menu, disc-server 2.57.5,
  disc-health; the image built on the computer `9e6448dd…`). On the owner's
  player with this archive's installer (2026-10-10): the first build's image
  known by its first blocks, this image written in the same entry, the player
  restarted by the installer, the first start's check matched `9e6448dd…`;
  the new menu confirmed by its first answer (the old one's answer left it
  tentative, as it should). Recorded (`releases/2.57.7.json`); the tag waits
  for the owner.

## Later

- diskOS Disco! as another interface before the large public release (owner,
  2026-10-09): zmd22's fork of diskOS (https://zmd22.github.io/diskos-disco/,
  https://github.com/zmd22/diskos-disco; 1.2.1 for V2.57; its UI under
  GPL-3.0-or-later, installer and documents under MIT), whose release carries
  the built `payload/mq_ui` as diskOS's does. As a `ui` package assembled on
  the user's computer by a recipe like diskOS's (its `mq_ui` from the release
  by its digest, the boot layer's entry beside it, nothing of it
  redistributed from here), its boot choice and card protection checked
  against what diskOS 1.2.0 expects, then the guest's two-package acceptance
  beside the server and diskOS, and the owner's player.

- An update of the image by the running system from the card (owner,
  2026-10-08: FiiO's own update takes about 2 minutes, ours over USB Boot
  about 10): the new rootfs written on the player as FiiO's recovery does,
  with the first start's check as its proof; USB Boot stays the first
  installation and the way back from any state.

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

- A faster exact check (owner, 2026-10-05): a SHA-256 of every block
  computed on the player, only the digests over USB, instead of all 100 MB
  in 794 small batches (about 40 min); sampling blocks was weighed and
  refused, since a NAND write fails in one block or page and a squashfs shows
  it only when that file is read. With diskOS 1.2.0's `my_write6`, which
  hashes on the device, as the writer to review; the hashing code is itself
  checked against a known image. The same digest replaces the backup's full
  read (owner, 2026-10-05): before a new image is written, the player's
  rootfs must hash to stock or to one of our released images (reproducible
  from the user's update and our release), else the installation is refused
  (a full read then keeps a copy for analysis); after the write it must
  hash to the image written; the way back to stock is allowed from any
  state. That digest, not a chain of earlier captures and confirmations, is
  then the proof of what the player held.
- Ready-to-run installers for macOS and Windows (Windows needs a WinUSB
  driver for the USB Boot device); a browser installer over WebUSB.
- An optional "official" label for packages signed by us.
- The console marker: the installer writes it and leaves it after the
  installation (owner, 2026-10-03).
