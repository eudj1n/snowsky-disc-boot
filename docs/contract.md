# Boot layer contract

Status: accepted by the owner (2026-10-02); not implemented yet. No image,
NAND write or device step follows from it without a separately authorized
stage. The products' names are settled; this repository is
snowsky-disc-boot, and the gateway (today's snowsky-disc-web) becomes
snowsky-disc-server after this layer.

## Products

| Product | What it is | How it changes |
| --- | --- | --- |
| snowsky-disc-boot | The layer added to the user's own V2.57 rootfs: boot modes, the package loader with slots and rollback, recovery from the card, the USB console | Rarely; only through USB Boot (it lives in the read-only squashfs) |
| snowsky-disc-server | Today's gateway (`disc-service`): admission, the stock bridge, store, apps on the card, its own updates | As a file, through the boot layer's slots |
| snowsky-disc-player | The browser page | On the card (`Apps/`), as today |

The boot layer is a platform: it starts whatever package the user installed,
ours or a third party's, at the installer's own risk. diskOS-like projects can
ship their UI as a package instead of a firmware fork.

**Boot owns** which slot runs, readiness, rollback, the boot-loop guard, the
modes and the recovery path. **The server owns** where an update comes from,
its signature and authorization (serial number, request ID, control owner),
staging it into the inactive slot and asking boot to switch. A broken server
cannot roll itself back, so starting and rolling back never live in a package.

Boot has no network, no HTTP, no downloads and no required signature of ours.

## Owner's decisions (2026-10-02)

1. Modes: a persisted default plus **Volume Up held at power-on = the other
   mode for this boot**, as diskOS does (not "the platform only with the key").
2. The `ui` role (replacing the stock UI) is part of the contract from the
   start, so it never needs a second USB Boot.
3. No server inside the boot image: the boot layer installs nothing of ours by
   itself; the user installs packages through a clear installation process.
4. The USB console keeps its card marker; the installer writes it.
5. Names: `snowsky-disc-boot` / `-server` / `-player`. Once the guest had
   answered what it could, the work moved into this repository; the server
   follows when the boot layer is done.
6. Several UIs with a choice at power-on (multi-boot) are built into the
   image before its first write to a device (2026-10-03): the mechanism in
   the image, the screen that asks as a package ("Several UIs and the
   boot menu").
7. That package is ours, `disc-menu`, built and released with this
   repository, and not part of the image (decision 3 holds): the installer
   stages it by default and the user may leave it out (2026-10-03).

Closed with this draft: **updates of the whole image from the card are not
viable.** The second MTD pair is FiiO's recovery system, not a second main
slot: `rootfs2` is 25 MiB against the main `rootfs` of 128 MiB (our squashfs is
about 81 MB), and stock's own scripts are named for the round trip
(`network_main_os_update_recovery.sh`, `network_recovery_update_main_os.sh`,
`recovery_to_main_os.sh`). Writing it would destroy FiiO's recovery; rewriting
the running main rootfs risks a device that only USB Boot recovers. Stock's
update paths verify FiiO's signature (`openssl dgst` with
`/etc/ota_bin/ecc_public.pem`). The boot image itself is therefore written
only through USB Boot; everything above it becomes files.

## Facts this rests on

- MTD layout (combined-004 observation): uboot 2, kernel 8, rootfs 128,
  kernel2 8, rootfs2 25, ota 1, mac 1, userdata 83 MiB.
- Stock init (V2.57 rootfs, read in the guest): `rcS` runs `S??*` in order;
  `S21mount_ubifs` mounts `/usr/data`; `S98FIIO` prepares `/usr/data/fiio`
  and backgrounds `/usr/project/fiio_init.sh`. That script starts `mq_ui`,
  then `mq_player`, by name through `PATH`, and **every 5 s checks both with
  `pgrep -x`**: if either is missing it kills both (and the network and
  Bluetooth daemons) and starts both again. `rcK` stops `S??*` in reverse
  order with `stop`. Stock's own shutdowns (idle power-off, the empty
  battery, a long Power press) are `poweroff -f`, which syncs and powers off
  **without** `rcK`: nothing in boot depends on `stop` for durability (every
  change of state is written atomically when it happens). The card is not
  mounted while `rcS` runs. `/sbin` comes before `/usr/bin` in the `PATH`
  stock's scripts see (below).
- diskOS: its 1.2.0 release supports V2.57 and was reviewed in the emulator
  project (snowsky-disc-qemu `research/docs/reports/diskos-v257.md`): its UI
  runs and plays through stock's player there, but its image patches
  `fiio_init.sh`, decides the boot in its own `S96` hook and starts the player
  through its own link, which a `ui` package of this layer replaces (stage 3).
- Keys (diskOS, validated on a device 2026-08-13): a key held from power-on
  is invisible to the input layer (`mq_player` grabs `event0`; `EVIOCGKEY`
  sees no edge before the input core). The pin level is readable through
  `/dev/mem`: X2000 GPIO port B at `0x10010100`, register `PxPIN`, active
  low; bit 13 Volume Up, 14 Volume Down, 15 Play. Volume Down with USB at
  power-on is the chip's mask ROM (never a boot-layer gesture). The source
  is the stock V2.57 kernel (reviewed offline 2026-10-03, [kernel
  review](nand-kernel-review.md#keys)): its board tree's `x2000_key` node
  puts Volume Up, Volume Down and Play on GPB13, GPB14 and GPB15, and its
  key driver takes a raw level of 0 as pressed; `kernel_review.py` refuses a
  kernel whose tree moves Volume Up or Play. Confirmed on the owner's V2.57
  player (2026-10-03, read-only over the engineering USB console; plan,
  stage 4): resting word `0xF6EFF327`, and with each key held 20 of 20 reads
  gave Volume Up `0xF6EFD327` (bit 13), Volume Down `0xF6EFB327` (bit 14)
  and Play `0xF6EF7327` (bit 15), active low. Port B also carries the
  charger, card and power-detect pins (GPB0, GPB6, GPB20), so its whole word
  varies (`0xF6EFE127` was the V2.40 read); boot reads bits 13 and 15 only.
- Stock's player can delete the mounted card (read in the V2.57 `mq_player`
  and run on the guest, 2026-10-03; diskOS 1.1.3 found the same on V2.09,
  V2.28 and V2.40). Its mount routine (`util/src/mount_storage_dev.c`,
  `0x4c42b8`) runs `umount /tmp/sdcard`, ignores the answer and runs
  `rm -rf /tmp/sdcard` (`0x4c4390`–`0x4c43f4`); it is called from
  `on_sdcard_event` (card events) and from `loop_mode_handle_thread` (mode
  changes). While anything holds a file on the card open (a server
  streaming a track, say) the unmount fails and the removal empties the
  mounted card: on the guest one open file and a card event took a card
  from three files to none. Stock's UI is built with the same routine.
  Stock starts both programs by name through `PATH` (`fiio_init.sh`, its
  first start and its watch loop), and BusyBox 1.31.1's `sh` finds `rm`
  through `PATH` (checked on the guest), so a guard first in their `PATH`
  takes the removal (below, "The card guard").
- Read on the owner's V2.57 player (2026-10-03, over the console;
  `work/device-read/menu-facts.json`, ignored):
  - Stock's `mq_player` owns the hardware watchdog: `cmd_watchdog start
    10000` when it starts, fed by its own thread, stopped on a clean exit;
    `mq_ui` never touches it. Once it has started, 10 s without a player
    reset the device.
  - `fiio_init.sh` starts `mq_ui`, then `mq_player` 2 s later. Its loop
    checks both every 5 s; when either is gone it kills both, with the
    Wi-Fi and Bluetooth helpers (`udhcpc`, `wpa_supplicant`, `bluetoothd`,
    …), turns the backlight off and starts them again the same way; the new
    player lights the screen. The backlight is lit from power-on (the boot
    logo, also in USB Boot).
  - The player holds `event0` and gets the keys there: Volume + `0xfb`,
    Volume − `0xfc`, Play `0xfa`; the UI holds `event1`. A process that takes
    `event0` (`EVIOCGRAB`) gets every key and the player none; after the
    release the player handles them as before (a test program from `/tmp`).
  - Idle: 71 of 117 MiB available, no swap; `mq_ui` 16 MiB resident (24 at
    its peak), `mq_player` 9 (12).
- The emulator (snowsky-disc-qemu `d7f1b9b`, now `690a55c`) runs stock's `rcS`,
  `fiio_init.sh` and its watch loop, models the port B word in `/dev/mem`
  with keys held from power-on, and power events (reboot and off through
  `rcK`, a cut, `poweroff -f`), with `/usr/data` as an 83 MiB file system.
- Settled on the guest (2026-10-02):
  - `PATH`: `rcS` sources `/etc/profile`, so the hooks and `fiio_init.sh`
    see `/bin:/sbin:/usr/bin:/usr/sbin` (corrected in the emulator's
    stock-init guest; BusyBox init's own `PATH` is
    `/sbin:/usr/sbin:/bin:/usr/bin`). Either way `/sbin` comes before
    `/usr/bin`, and stock has no `mq_ui` outside `/usr/bin`: a `/sbin/mq_ui`
    takes stock's launches without changing a stock file (run in the
    emulator's stock-init guest; the device remains).
  - The library's databases (`song.db`, `dic.db`, `sysconfig.db`,
    `theme.db`) are open in `mq_player`, none in `mq_ui`: replacing the UI
    leaves the library and the server's data level with the player.
  - "Reset all" (System settings) involves no MCU (snowsky-disc-qemu
    `research/docs/reports/reset-all.md`, read in both programs and run in a
    stock-init guest): it rewrites `wpa_supplicant.conf`, removes the Wi-Fi
    and Bluetooth folders, `song.db` and `theme.db`, resets the settings row
    and reboots through `rcK`. `/usr/data/disc-boot/` and the packages' data
    stay; had they gone, boot would run no package and the user would install
    them again with Play.

## Modes and gestures

| At power-on | Effect |
| --- | --- |
| Nothing | The persisted default mode (after installation: `platform`) |
| Volume Up | The other mode, for this boot only |
| Play (with or without Volume Up) | Recovery: install the packages staged on the card, then `platform` |

- `stock`: boot starts no package and leaves the UI launch to stock. The USB
  console still follows its marker, so a broken platform stays repairable.
- `platform`: boot starts the installed `service` package and, for an
  installed `ui` package, launches it instead of stock's `mq_ui`.
- A failed or unavailable key read counts as "not held"; it never switches.
- **Boot-loop guard:** a platform boot with an installed package (or a
  recovery) counts as unconfirmed until every installed role has confirmed
  its package; with 3 counted and the default `platform`, boot chooses
  `stock` (reason `boot-loop`). A key still chooses (Volume Up, Play). Stock
  boots are not counted. Confirming means running 180 s in that boot: a
  version restored by a rollback is marked confirmed at once, yet the count
  clears only after it has run its 180 s again (seen on the guest).
- The default is changed by a package's request (below) or by the console;
  there is no card file for it.

`disc-boot early` (the `S22disc-boot` hook, after `S21mount_ubifs`) reads
the keys and decides once; `/run/disc-boot/boot.json` keeps the decision for
the rest of the boot.

## Packages

Three roles ("Several UIs and the boot menu" for the last two):

- `service`, at most one: runs beside the stock UI and player (our server
  is one).
- `ui`, any number with distinct names: one of them, or stock's UI, runs
  instead of stock's `mq_ui` in each platform boot; stock's `mq_player`
  always stays.
- `menu`, at most one: asks at power-on which UI runs.

Distributed as a zip; staged and installed as a folder with `package.json`:

```json
{
  "schema": 1,
  "name": "disc-server",
  "version": "2026.10.02-05a1422",
  "role": "service",
  "bootApi": 1,
  "arch": "mips32el-linux-static",
  "profiles": ["2.57"],
  "entry": "bin/disc-server",
  "args": [],
  "ready": 30,
  "files": {
    "bin/disc-server": { "size": 4844948, "sha256": "…", "mode": "0755" }
  }
}
```

- `name` `[a-z0-9-]{1,32}`; `version` 1–64 characters; every string is
  printable ASCII (`\uXXXX` escapes are refused); a key given twice is
  refused; keys boot does not know are ignored.
- `bootApi` is the lowest API the package needs; `arch` must be boot's own
  (`mips32el-linux-static` on the player); `profiles` lists the firmware
  profiles (`2.57`) the package supports, 1–8 of them; `ready` is 1–120
  seconds (30 when absent); `args` at most 32 strings of up to 256 bytes.
- Every file is listed with its size, lower-case SHA-256 and mode `0755` or
  `0644`; a path is relative, at most 8 folders deep and 200 bytes, of
  letters, digits and `._+@-`, without `.` or `..`. The entry is a listed
  `0755` file. A slot holding anything unlisted (besides `package.json` and
  folders), a link or a special file is refused.
- Bounds: manifest 64 KiB, 256 files, 32 MiB per package; an installation
  checks free space first and keeps 16 MiB of `/usr/data` for stock.
- A `ui` or `menu` package's entry is named `mq_ui`, because stock's watch
  loop finds the UI by that exact process name (the menu runs in its place).
- `title`, optional, 1–32 printable ASCII: the name a boot menu shows for
  the package (owner, 2026-10-03); without it the menu shows `name`.
- A `ui` package may name `player`, a listed `0755` file: its own launcher
  of stock's player (diskOS's sets up its card protection there and tells
  its UI so). Boot runs it as `mq_player` while that package's UI runs (not
  after a fallback to stock's UI), with the package's environment; it must
  end in `exec /usr/bin/mq_player` and never keep the player from starting:
  when stock restarts the pair, the watchdog the previous player started
  keeps running, and 10 s without a player reset the device.
  A `service` package with `player` is refused.
- A package may carry its own shared libraries in `lib/`; boot puts that
  folder ahead of stock's `LD_LIBRARY_PATH`. Helper programs (diskOS's SSH
  tooling, say) are ordinary listed files of the package, found through
  `$DISC_BOOT_SLOT`, never installed into the rootfs.
- `disc-boot verify ROLE DIR` checks a folder as boot would (manifest, fit,
  every file) and answers in JSON: a server checks a staged update with it
  before asking for its activation. On a computer, `scripts/package.py`
  writes a folder's `package.json`, checks it with the same rules and
  messages, zips it and stages it on a card.

## Slots and state (`/usr/data/disc-boot/`)

```text
/usr/data/disc-boot/
  state.json            {"default": "platform"|"stock", "unconfirmed": n,
                         "ui": "<name>"|"stock"|null, "next": "<name>"|"stock"|null}
  service/a/ service/b/ the two slots of the service role
  service/state.json    {"current": "a"|"b"|null, "confirmed": bool, "previous": "a"|"b"|null,
                         "previousManifest": "<sha256 of its package.json>"|null}
  service/request       a package's request, written atomically
  menu/…                the same for the menu role
  ui/<name>/…           the same for each ui package, under its name; ui/<name>/remove
                        a removal another package asked for, done at the launcher's next start
  data/<name>/          a package's own persistent data, kept across updates
```

Every change is a new file, synced, then renamed into place (UBIFS keeps a
rename atomic through power loss); changes of state happen under a lock. A
slot is tentative until confirmed; `previous` is the last confirmed slot,
the one a rollback returns to, and `previousManifest` the SHA-256 of its
`package.json` when it became so. The previous slot is the inactive one, so
a package that stages an update there replaces that version: boot then
rolls back to it neither on request (`rollback refused: the previous version
was replaced`) nor when a tentative version fails (that one stops and says
why). A server stages updates only while its own version is confirmed. An
unreadable state runs nothing for that role and says so.

## Lifecycle of a `service` package

1. Start (`disc-boot start`, the `S99disc-boot` hook, which returns at once):
   verify the current slot (manifest, fit, every file and its mode), then run
   the entry in a session of its own with the arguments and the environment
   below, in `$DISC_BOOT_DATA`, at a lower priority than stock (`nice` +5).
2. **Ready:** the package creates `$DISC_BOOT_RUN/ready` within `ready`
   seconds, or boot stops it and counts a failure.
3. **Confirmed:** ready and still running 180 s later; a tentative slot
   becomes the confirmed one and, once every installed role is confirmed,
   the boot-loop count clears.
4. **Failures:** a tentative slot gives way to the previous confirmed one at
   its first failure (or stops, with the reason, when there is none). A
   confirmed one restarts after 2 s, at most 3 times in 10 minutes, then
   stays stopped and the status says why (stock keeps working).
5. **Stop** (`disc-boot stop`, from `rcK`): SIGTERM to the package's session,
   5 s, then SIGKILL.

Timings are initial values, tuned on the guest and the device.

### Requests from a package

A package writes `$DISC_BOOT_REQUEST` (JSON, atomically, at most 4 KiB) and
exits:

- `{"action": "activate"}`: the inactive slot (`$DISC_BOOT_INACTIVE`, which
  the package filled and checked itself) becomes the tentative current one;
  boot verifies it again first and refuses it whole otherwise.
- `{"action": "rollback"}`: back to the previous confirmed slot.
- `{"action": "default", "mode": "stock" | "platform"}`.
- `{"action": "remove", "purge": false}`: the role's slots and state go;
  `purge` also removes `data/<name>/`.

Boot applies a request only after the package has exited, removes the file
and records the outcome (`lastRequest`) in the role's status; a refused or
unreadable request changes nothing, and the current package starts again
without a failure counted. The `ui` role's requests apply at the launcher's
next start (stock restarts the UI, and with it the player, when it exits).

## The `ui` role

Three pieces keep stock's UI independent of the boot program (and two more
do the same for its player):

- `/sbin/mq_ui`, a shell script ahead of `/usr/bin` in the `PATH`
  `fiio_init.sh` runs with (its first start and every restart from its watch
  loop): it starts the launcher only when `/run/disc-boot/ui-launch` exists,
  and stock's `/usr/bin/mq_ui` otherwise.
- `/run/disc-boot/ui-launch`, written by `disc-boot early` only in `platform`
  mode with a `ui` package installed (and by a recovery that installed one).
  In `stock` mode, after Volume Up, without a package or when the boot
  program fails before deciding, stock's UI starts without it.
- `/opt/disc-boot/mq_ui`, a link to `disc-boot`, which acts as the launcher
  by that name: it applies a pending request, checks the slot and execs the
  package as `mq_ui`. A forked watcher waits for `ready`, then confirms the
  slot after 180 s of running; a package not ready in time is killed, so
  stock's loop starts again. After 3 starts within 2 minutes without
  confirmation a tentative package gives way to the previous one; otherwise
  the launcher falls back to stock's UI for the rest of the boot
  (`/run/disc-boot/ui/fallback`), since every crash also restarts
  `mq_player`.
- `/sbin/mq_player`, the same kind of script for stock's player: with
  `ui-launch` and no fallback it starts `/opt/disc-boot/mq_player`, another
  link to `disc-boot`, and stock's `/usr/bin/mq_player` otherwise. By that
  name the boot program checks the `ui` slot and execs the package's
  `player` launcher as `mq_player`, through the link
  `/run/disc-boot/ui/mq_player` (stock's watch loop finds the player by its
  process name, which the kernel takes from the path executed: tested on the
  guest, BusyBox's `pgrep -x` matches that name); without one, or when anything fails
  (stock mode, an unreadable state, a slot that fails its check), it execs
  stock's player at once and says why in `/run/disc-boot/ui/player.json`.
  Every crash of the player restarts the UI too, so the UI's own count of
  starts bounds a launcher that fails.

## Several UIs and the boot menu (multi-boot)

Designed 2026-10-03 (owner's decision 6) and built the same day in the boot
program (`tests/conformance/test_boot.py`); its guest acceptance comes before
the image is first written to a device. Several `ui` packages can be installed and each boot runs
one of them or stock's UI; a `menu` package, when installed, lets the
user pick at power-on. Only the mechanism is in the image (the choice, the
launchers, the fallbacks); the screen that asks is a package and changes
without USB Boot. That package is ours, `disc-menu`, built and released with
this repository and staged by the installer by default (owner's decision 7);
any `menu` package fits the same contract. Boot works without one: the
default or a `ui-next` choice runs. Nothing has been released, so the API
stays 1 and the single-package layout gets no migration.

### Packages and storage

- `ui`: any number of packages with distinct names, within `/usr/data`'s
  bounds (an installation still keeps 16 MiB for stock). Each has its own
  slots, state and requests: `ui/<name>/a/`, `ui/<name>/b/`,
  `ui/<name>/state.json`, `ui/<name>/request`; `data/<name>/` as today.
- `menu`: at most one, in `menu/a/` and `menu/b/`, verified,
  installed and rolled back like a `ui` package; its entry is named `mq_ui`
  (stock's loop watches it by that name) and it has no `player`.
- `state.json` gains `ui`, the default (a `ui` package's name or `stock`),
  and `next`, a choice for the next platform boot only, or null.
- A `ui` package lives under its name: one installed or activated under
  another name is refused ("the package is named …").

### The choice

Each platform boot runs one UI, recorded in `/run/disc-boot/ui/choice.json`
(`ui`: a name or `stock`; `by`; a note), taken from the first of:

1. The menu's answer for this boot (`by: menu`).
2. `next`, which `disc-boot early` consumes (`by: next`).
3. The default (`by: default`); without one set, the first installed `ui`
   package by name, and stock's UI when none is.
4. Stock's UI when the chosen package is not installed (`by: fallback`, with
   the reason). A package that fails its check falls back as in "The `ui`
   role".

`disc-boot early` settles 2–4; the menu can still choose before any UI
runs. With stock's UI chosen and no menu, boot gives the launcher no
permission: stock's UI starts from the wrapper alone, as without packages. Volume Up at power-on still means stock *mode* for this boot (no
package runs, nor the menu) and Play still installs from the card; the
menu's `stock` entry is stock's UI with the `service` package running.

### The menu's turn

- In `platform` mode, with a menu installed and no choice by `next`, the
  `mq_ui` launcher starts the menu first. `/run/disc-boot/ui/choices.json`
  lists every installed `ui` package (`ui`, `title` or else its name,
  `version`, `confirmed`), then `{"ui": "stock", "version": "<firmware
  profile>"}`, and the `default` of this boot.
- The menu writes `$DISC_BOOT_RUN/choice` (`{"ui": "<name>"|"stock"}`)
  atomically and hands over (owner, 2026-10-03): it execs
  `$DISC_BOOT_LAUNCHER`, the UI launcher, in its own process, which checks
  and records the answer and starts the chosen UI at once. Everything the
  menu opened must close on that exec (`O_CLOEXEC`), its hold of `event0`
  with it.
- At the boot's first start of the pair no player has run yet, so no
  watchdog runs (stock's player starts it): `/sbin/mq_player` waits for
  the choice (at most the menu's 60 s and 10 more) and then starts the
  chosen UI's `player`, or stock's. The pair is never restarted: the
  screen stays lit, Wi-Fi and Bluetooth stay up. Once a player has run in
  the boot (`/run/disc-boot/player-ran`, marked by every start of a player,
  the wrapper's own start of stock's included), stock's player starts at
  once beside the menu, and when the chosen UI brings its own `player` the
  launcher exits instead of starting it, so that stock's watch loop
  restarts the pair (within its 5 s check and 2 s between the two, inside
  the watchdog's 10 s; dark until the new player lights the screen).
- A menu that exits after answering, rather than hand over, gets the pair
  restarted by stock's loop the same way; the answer stands.
- It may answer at once (a remembered choice, a timeout of its own). Boot
  stops it after 60 s and takes the default; an exit without a valid answer
  counts as a menu failure, and after 2 in one boot the default runs
  without it. Its exits are never counted against a `ui` package; a valid
  answer confirms a tentative menu version (it does not run 180 s), and
  a failing tentative version gives way to the previous one.
- The keys drive it. When stock's player runs beside it (a player ran
  earlier in the boot), the menu takes `event0` for itself (`EVIOCGRAB`),
  since that player reads the same keys there and would otherwise change
  the volume or start playback; taking it when no player runs is harmless.
  A key already down when it starts counts only after its release. The
  touch panel (`event1`) is the UI's own and may choose as well.
- Its countdown to the default is its own; `disc-menu`'s is 5 s (owner,
  2026-10-03).

### Requests

- From any package: `{"action": "ui-default", "ui": "<name>"|"stock"}` and
  `{"action": "ui-next", "ui": "<name>"|"stock"}`; a name that is not
  installed is refused. Our server's page can offer both.
- `{"action": "remove", "purge": bool}` keeps its meaning for the package
  that asks. `{"action": "ui-remove", "ui": "<name>", "purge": bool}` from
  the `service` or the `menu` package removes another `ui` package (the
  server's manager); removing the default makes `stock` the default.
- Requests about the choice apply to the next boot; a removal applies at
  the next start of the launcher, never under the running UI. Since a
  service keeps running, `disc-boot early` and each start of the launcher
  also take the requests about the choice (`ui-*`) that any package left,
  before anything of a package starts.

### Failures and the boot-loop guard

- Confirmation, rollback and fallback stay per package, as in "The `ui`
  role": a tentative version of the chosen UI gives way to its previous
  one, otherwise stock's UI runs for the rest of the boot (never another
  package).
- The boot-loop count clears once the `service` package and the chosen UI
  are confirmed; the menu's answer is part of the boot, not a condition.

### Recovery, environment and status

- Play installs every staged folder: `.disc/boot/install/ui/<name>/` (several,
  in the order of their names; the folder's name must be the package's) and
  `.disc/boot/install/menu/`. The first `ui` package installed becomes the
  default when there is none. A `package.json` directly in
  `.disc/boot/install/ui/` (the earlier layout) is refused with a note.
- `DISC_BOOT_ROLE` may be `menu`; its `$DISC_BOOT_RUN` is
  `/run/disc-boot/menu/`.
- `ui.json` keeps the chosen UI's status (`stock-ui` when stock's UI was
  chosen) and adds `choice` and `installed` (each package's name, version,
  slot, confirmed); `menu.json` is the menu's role status (`asking`,
  `answered`, `failed`, `absent`); `disc-boot status` adds both and the
  choice.

### Before it is built

- Settled on the device (2026-10-03, "Facts this rests on"): the watchdog
  belongs to stock's player, so the pair's restart after a choice is
  stock's own; the menu may take `event0` and give it back.
- Built and checked in the boot program's conformance tests (2026-10-03).
- Accepted on the guest with probe packages (shell scripts): two `ui` probes
  and a menu probe; a choice by the menu, by `ui-next` and by the
  default; a menu that hangs, fails or answers a name not installed; a
  chosen UI that fails; Volume Up and Play unchanged; the boot-loop guard.
  `boot_guest.py` and `two_packages.py` again on the new layout.
- `disc-menu` with its screen needs the emulator to serve static programs'
  screen and input (snowsky-disc-qemu #54); it proves the API before the
  image fixes it.

## The card guard

`/opt/disc-boot/guard/rm` is first in the `PATH` of stock's player and UI
(both wrappers put it there, in every mode, and a `ui` package and its
player launcher inherit it). It refuses an `rm` that names the card's mount
point (from the firmware profile) or a folder above it, in any spelling
(trailing or repeated slashes, `.` and `..`, a relative path, a link); the
mount point itself is removed only when it is an empty folder (`rmdir`,
which a mounted card refuses), which is what stock meant. Everything else,
files and folders on the card included (stock's own file manager deletes
them with `rm -rf`), goes to the real `rm` unchanged. A refusal answers 0, as
stock ignores the answer, and is logged to `/run/disc-boot/guard.log`
(capped at 64 KiB). It guards this removal, not every way to lose data; a
service package starts with its own `PATH` and does not need it.

## Recovery from the card (Play at power-on)

- Staged folders: `.disc/boot/install/service/`, `.disc/boot/install/menu/`
  and `.disc/boot/install/ui/<name>/` for each ui package, each with
  `package.json` and its files. The installer or the page puts them there
  (`scripts/package.py stage` places each by its role and name).
- Boot waits up to 90 s for the card mounted from its expected device (the
  card is mounted after the `S99` hooks run: stock mounts it once
  `mq_player` is up),
  verifies each staged package completely (modes do not count on the card's
  file system), copies it into the role's inactive slot, verifies the copy
  with modes, makes it the tentative current one, removes the staged folder
  and writes `.disc/boot/result.json` (per role, and per name under `ui`:
  installed, or why not). A refused package stays on the card. With a `ui`
  or `menu` package newly installed, boot settles this boot's choice again
  and, when the launcher has something to start, gives it the permission and
  stock's running UI a SIGTERM, so stock's loop starts it. The packages then run as in `platform` mode.
- Without the gesture boot never installs or runs anything from the card.

## Environment of a package

| Variable | Meaning |
| --- | --- |
| `DISC_BOOT_API` | The boot layer's API version (1) |
| `DISC_BOOT_ROLE` | `service`, `ui` or `menu` |
| `DISC_BOOT_PROFILE` | The firmware profile (`2.57`) |
| `DISC_BOOT_SLOT` | The running slot (read-only by contract) |
| `DISC_BOOT_INACTIVE` | Where an update is staged |
| `DISC_BOOT_REQUEST` | Where requests go |
| `DISC_BOOT_DATA` | `data/<name>/`, persistent; the service's working folder and `HOME` |
| `DISC_BOOT_RUN` | `/run/disc-boot/<role>/`, volatile; `ready` goes here |
| `DISC_BOOT_STATUS` | `/run/disc-boot/`, the status files below |
| `DISC_BOOT_CARD` | The card's mount point (it may be absent) |
| `DISC_BOOT_PROGRAM` | The boot program (`/opt/disc-boot/disc-boot`), for `verify` of a staged update |
| `DISC_BOOT_LAUNCHER` | The menu only: the UI launcher (`/opt/disc-boot/mq_ui`) it hands over to |

A `service` package starts from a clean environment (these, `PATH`, `HOME`
and `LD_LIBRARY_PATH`); its standard output and error go to
`$DISC_BOOT_RUN/log`, capped at 64 KiB; standard input is `/dev/null`, and
no other descriptor is open (none of the boot program's). A `ui` or `menu`
package keeps the environment stock's `fiio_init.sh` gives its UI, with these
added and its `lib/` first in `LD_LIBRARY_PATH`.

A package must not write MTD devices, change FiiO's files in `/usr/data`
(`fiio/`, `sn.txt` and the rest), signal stock processes, take stock's ports
or create a USB gadget while the console owns the controller. Boot cannot
enforce this: packages run as root, at the installer's risk.

## Status (`/run/disc-boot/`)

- `boot.json`: the boot layer's API and build, the profile, the mode and its
  reason (`default`, `key`, `boot-loop`, `recovery`), the key read, the count
  of unconfirmed boots before this one, whether the state was readable, the
  card.
- `service.json`, `ui.json`, `menu.json`: the role's state (`starting`,
  `ready`, `confirmed`, `restarting`, `rolled-back`, `failed`, `stopped`,
  `absent`, `stock-mode`, `fallback`, `not-ready`; `stock-ui` for the ui
  role, `asking` and `answered` for the menu), the package's name and version,
  the slot, confirmed or not, failures, a note, the last request's outcome
  and `previous`: `{slot, name, version, manifest}` of the version a
  rollback returns to while its slot still holds it, else null (written
  with the rest of the status; a package compares `manifest` with
  `$DISC_BOOT_INACTIVE/package.json` to see whether it staged over it since).
- `ui/choice.json`: this boot's choice of UI; `ui/choices.json`: what the
  menu was offered.
- `ui/player.json`: how stock's player last started while the launcher ran
  (`launch`: `package`, `stock`, or `waiting` while it waits for the
  menu's choice; the package; a note such as "the menu is choosing").
- `player-ran`: a player has run in this boot.
- `guard.log`: the card guard's refusals.
- `disc-boot status` prints the boot, the roles, the choice and `player`
  together; the server shows them in its diagnostics.

## USB console

Unchanged in behavior ([USB diagnostics](usb-diagnostics.md), whose marker
moved to `.disc/` with combined-008): the card file `.disc/dev/usb-console`
with the exact content `DISC_WEB_LOCAL_ROOT_CONSOLE` and a newline enables it
at boot, independently of the mode. The installer writes the marker (owner,
2026-10-02). USB needs the cable at the user's computer, so the marker alone
is the console's gate, as today.

## Trust

Boot checks integrity, not authorship: a package is what its manifest lists.
Consent is physical: the Play gesture for the card, or the running package's
own authorization for updates. Signatures belong to packages that update
without a person present (our server will require ours). Installing a
package hands it the device; the user guide says so, together with the way
back (Volume Up for stock, USB Boot for the stock image).

## Installation for users (outline)

1. The user's own FiiO V2.57 OTA file; the installer builds the image with
   the boot layer (no package inside) and verifies it.
2. USB Boot (Volume Down with USB): write and verify, as today's procedure.
3. The installer copies the chosen packages (our server; `disc-menu`, chosen
   by default; the player into `Apps/`) into `.disc/boot/install/`, and the
   console marker.
4. The user powers on holding Play; boot installs and starts the packages and
   writes the result to the card; the page then shows the server's status.

## What changes against today

- `/opt/disc-web/disc-service` and `S99disc-web` leave the image; the server
  becomes the `service` package, and its `--supervise` moves into boot. This
  stage renames the on-device paths explicitly (`/usr/data/disc-web.disabled`
  is replaced by the default mode); the console's marker stays.
- The image builder's invariant stays: stock objects unchanged, only listed
  additions. The boot image adds `/opt/disc-boot/` (`disc-boot`, the
  `mq_ui` and `mq_player` links to it, the card guard `guard/rm`, the
  console `disc-usb-console`, `boot-report.sh`), `/sbin/mq_ui`,
  `/sbin/mq_player`, and the hooks `S22disc-boot`, `S99disc-boot` and
  `S99disc-usb`. The console and the report moved from `/opt/disc-web/` with
  stage 1; the console's hook name, gadget and marker stay.

## Tests

- Host (`test_boot`): a fixture build of `disc-boot` (`-DDISC_BOOT_FIXTURE`,
  never packaged) runs under a temporary root with keys, the card's mount and
  stock's UI as files and shortened timings, against shell-script packages:
  modes, the boot-loop guard, every manifest refusal, the service's
  lifecycle, requests, recovery and the launcher behind the real wrapper. The
  same tests run on Linux against the MIPS build under `qemu-user`.
- Packed tree (`tests/integration/boot_layer.py`): the image's hooks and
  wrappers on its own stock BusyBox with the production build and its real
  timings, stock's `PATH` lookup of `mq_ui` and `mq_player` included, and
  stock's "umount, then rm -rf" on a busy mount through the guard.
- Guest (V2.57), once the emulator runs stock's init: power loss between
  steps (kill and reboot), `stock` and `platform` boots, a `ui` package under
  stock's watch loop.
- The key read sits behind one function; until the emulator models the GPIO
  pins it is tested on the host and confirmed on the device once.

## Acceptance: two independent packages (owner, 2026-10-02)

The contract holds when projects that know nothing of each other run side by
side through it: **diskOS's UI as the `ui` package and our server as the
`service` package, with the player on the card**, first on the guest, then on
the device. diskOS (MIT; its UI under `ui/` is GPL-3.0) is built from its
own sources, locally; nothing of it enters this repository or is passed on.

What diskOS's image adds today and how each part maps:

| diskOS image today | As a package |
| --- | --- |
| Its `mq_ui` in `/opt/diskos`, copied to `/usr/data` and checked against a baked manifest by `S97diskos_install` | The `ui` package's entry; boot verifies and installs it |
| A patch of `fiio_init.sh` that runs `/usr/data/mq_ui` and `/usr/data/mq_player` (a link to its UI binary, which as `mq_player` sets up its card guard, tells its UI and execs stock's player) | Boot's `/sbin/mq_ui` launcher, and `/sbin/mq_player` running the package's `player` (the same binary); stock's player itself untouched |
| `/tmp/.diskos_boot_select`, written by its `S96` hook, without which its UI runs stock's | Written by the package's own `mq_ui` entry, which then runs diskOS's binary as `mq_ui` (`exec -a`): boot already chose (open: a release that takes boot's mode) |
| Its own Volume Up check and "Default UI" file | Boot's modes; diskOS's check becomes redundant (harmless: with the key held boot already runs stock's UI) |
| Dropbear and `diskos-debug.sh` under `/usr/project` | Listed files of the package, found through `$DISC_BOOT_SLOT` |
| Dev variant's always-on USB serial shell | Boot's console (one gadget owner) |

Stock's control ports 12100 and 12103 belong to `mq_player` (observed on
the guest), so the server's bridge does not depend on the UI process, and
`song.db` stays open in stock's player under diskOS's UI.

Accepted on the guest (2026-10-03, plan, stage 3; `tests/integration/
two_packages.py` with diskOS 1.2.0 built locally by `diskos_package.py`; its
UI itself running since the emulator at `690a55c`, below): both installed
with Play and confirmed, stock's player started by diskOS's
own launcher (its verdict `guard=1 local=1`, its guard and then boot's first
in the player's `PATH`), the server answering and serving the player page
it installed, a busy card kept through stock's card event, the pair killed
and brought back by stock's loop with the server's process untouched, stock
mode with both installed, and a broken diskOS update giving way to the
confirmed one without touching the server. What diskOS would change to fit
(upstreamable; none blocks the package):

- Its UI insists on its own `S96` record; a release could take boot's
  choice (the record, or `DISC_BOOT_ROLE=ui`) instead.
- It has no readiness signal: the entry declares `ready` when it starts the
  UI, so boot's confirmation means "running for 180 s", not "drawn".
- Its launcher sets stock's `SYSCONFIG.WORK_MODE` to 0 (local playback) at
  every start of the player, a change of FiiO's settings that the rule
  above ("must not change FiiO's files") does not allow a package; it resets
  a work mode the user chose. A package's own risk today; the open question
  is whether boot's contract should name it or diskOS ask first.
- It keeps its files in `/usr/data` directly (`diskos.conf`, `diskos/`, its
  logs) rather than `$DISC_BOOT_DATA`, and finds its helpers at fixed
  `/opt/diskos/bin/` paths (the artwork decoder, its updates' keys), absent
  in a package: those features are off as a package.
- On the guest diskOS's UI first never got past its start: the emulator
  dropped `argv[0]` (snowsky-disc-qemu #52), then answered the screen's
  and input's `ioctl`s only to dynamically linked programs (#54), and its
  readiness missed a UI that pans once (#55). The first run did not see it:
  it checked the process and the launcher's verdict, and its screenshots
  were empty. With the emulator at `690a55c` the acceptance, which now
  requires the UI to hold the touch panel, passed all six steps without a
  workaround.

## Open before implementation

- diskOS's release with V2.57 support: what its image changes on V2.57, and
  whether the table above still holds (a separate session of the emulator
  project).

- What "Reset all" removes in `/usr/data` on the device (the emulator does
  not complete it; contract-neutral, see the facts).
- Free space of `/usr/data` on the owner's player (a diagnostics field).
- Play on GPB15 read on this unit: the stock kernel settles the pin and its
  level (facts above); the device read confirms it before the image is
  written.
- Memory and priority limits that keep stock's audio smooth (the device).
- The emulator runs stock's `rcS` and `fiio_init.sh`, so boot's hooks and the
  `mq_ui` launcher can be accepted on the guest (the emulator session).
- Whether the installer writes the console marker always or as an option,
  and whether it removes it after the installation.
- An optional "official" label for packages signed by us (shown, never
  required).
