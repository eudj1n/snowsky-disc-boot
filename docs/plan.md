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
      payload (`device/acquisition/`) to read every block.
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
name ([observation](first-write-observation.md)).

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
- [ ] The fixed image written in steps: no package first (the console's marker only), checked
  on the player through the console (stock, the power key, the boot
  program's log and why it made no run folder), then the packages.
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
- [ ] Back to stock after the second write (the package's restore,
  `install.py --restore --run`).
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
  `rootfs-digest` (`device/acquisition/digest.c`, its own SHA-256 without
  libc, 8,024 bytes, stack about 13 of 32 KiB) answers the full read's
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
- [ ] The write session's waits (owner, 2026-10-05): the host sleeps a fixed
  15 min (`writer_wait_ms`) after starting the writer, since the ROM does not
  answer USB while it runs (diskOS waits the same for `my_write5`), while
  programming 768 blocks takes about a minute by the chip's timings; and
  the staging takes about 11 min of transfers (two RAM pattern passes over
  ~100 MB and the image's upload and comparison). diskOS 1.2.0's
  `my_write6` hashes the staged image in DRAM before writing and records
  timing samples of every NAND command in its debug block: its review gives
  both the check on the device and the measured writer time from which a
  wait may be computed. A too-early look at a writer is an unknown outcome
  for these tools, so the wait shortens only on measurements.
- [ ] With the next write of the boot layer (owner, 2026-10-05): the exact
  check by digest (stage "Later", the faster exact check), a SHA-256 of
  every logical block computed on the player by the reviewed reader
  (`device/acquisition/`, `device/src/sha256.c`), diskOS's `my_write6`
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
- [ ] `install.py` takes `disc-boot` and `disc-usb-console` from the boot
  release by their digests; `build/mips` only for development.

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
