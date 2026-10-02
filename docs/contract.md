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
  order with `stop`. Busybox init's default `PATH` puts `/sbin` before
  `/usr/bin`.
- diskOS facts below come from its local checkout at `646212d`, before its
  release with V2.57 support (owner, 2026-10-02: not taken yet; reviewed in a
  separate session of the emulator project). Its V2.57 build may change the
  details of its image (the `fiio_init.sh` patch, the key check).
- Keys (diskOS, validated on a device 2026-08-13): a key held from power-on
  is invisible to the input layer (`mq_player` grabs `event0`; `EVIOCGKEY`
  sees no edge before the input core). The pin level is readable through
  `/dev/mem`: X2000 GPIO port B at `0x10010100`, register `PxPIN`, active
  low; bit 13 Volume Up, 14 Volume Down, 15 Play. Volume Down with USB at
  power-on is the chip's mask ROM (never a boot-layer gesture).
- The emulator does not model these pins yet (owner: a separate session for
  the emulator). It also does not run stock's `rcS` or `fiio_init.sh`: it
  starts `mq_ui`, `mq_player` and our service itself, so boot's hooks and the
  `mq_ui` launcher under stock's watch loop need that session too.
- Settled on the guest (2026-10-02):
  - `PATH`: init is BusyBox 1.31.1, whose root `PATH` is
    `/sbin:/usr/sbin:/bin:/usr/bin`; neither `rcS`, `S98FIIO` nor
    `fiio_init.sh` changes it, and stock has no `mq_ui` outside `/usr/bin`.
    A `/sbin/mq_ui` therefore takes stock's launches without changing a stock
    file (confirmed statically; the launch itself waits for the emulator
    session and the device).
  - The library's databases (`song.db`, `dic.db`, `sysconfig.db`,
    `theme.db`) are open in `mq_player`, none in `mq_ui`: replacing the UI
    leaves the library and the server's data level with the player.
  - "Reset all" (System settings) does not complete in the emulator: the UI
    shows "Please wait…" and returns, and nothing in `/usr/data` changes, not
    even the FiiO folders the player's reset strings name
    (`rm -rf /usr/data/fiio/wifi`, `/usr/data/storebluetooth`,
    `rm -rf %s/*`); the reset most likely waits for the MCU, which the
    emulator does not model. Contract-neutral: should a reset remove
    `/usr/data/disc-boot/`, boot runs with no packages and the user installs
    them again with Play.

## Modes and gestures

| At power-on | Effect |
| --- | --- |
| Nothing | The persisted default mode (after installation: `platform`) |
| Volume Up | The other mode, for this boot only |
| Play | Recovery: install the packages staged on the card, then `platform` |

- `stock`: boot starts no package and leaves the UI launch to stock. The USB
  console still follows its marker, so a broken platform stays repairable.
- `platform`: boot starts the installed `service` package and, for an
  installed `ui` package, launches it instead of stock's `mq_ui`.
- A failed or unavailable key read counts as "not held"; it never switches.
- **Boot-loop guard:** boot counts platform boots whose packages never
  reached confirmation; at 3 in a row it boots `stock` and says why in its
  status. Confirmation clears the count.
- The default is changed by the running server (a request, below) or by the
  console; there is no card file for it.

The keys are read once, as early as `/usr/data` is mounted (an `S22` hook),
and the result kept in `/run` for the rest of the boot.

## Packages

At most one package per role:

- `service`: runs beside the stock UI and player (our server is one).
- `ui`: runs instead of stock's `mq_ui`; stock's `mq_player` always stays.

Distributed as a zip; staged and installed as a folder with `package.json`:

```json
{
  "schema": 1,
  "name": "disc-server",
  "version": "2026.10.02-05a1422",
  "role": "service",
  "bootApi": 1,
  "arch": "mips32el-linux-static",
  "profiles": ["v2.57"],
  "entry": "bin/disc-server",
  "args": [],
  "ready": 30,
  "files": {
    "bin/disc-server": { "size": 4844948, "sha256": "…", "mode": "0755" }
  }
}
```

- `name` `[a-z0-9-]{1,32}`; `version` free text up to 64 bytes;
  `profiles` lists the firmware profiles the package supports (boot refuses
  the package on any other); `bootApi` the lowest API it needs.
- Every file is listed with its size and SHA-256; nothing unlisted is
  installed; paths are relative, without `..`, links or devices.
- Initial bounds: manifest 64 KiB, 256 files, 32 MiB per package; boot
  checks free space first and keeps 16 MiB of `/usr/data` for stock.
- A `ui` package's entry is installed under the name `mq_ui`, because stock's
  watch loop finds the UI by that exact process name.
- A package may carry its own shared libraries in `lib/`; boot puts that
  folder ahead of stock's `LD_LIBRARY_PATH`. Helper programs (diskOS's SSH
  tooling, say) are ordinary listed files of the package, found through
  `$DISC_BOOT_SLOT`, never installed into the rootfs.

## Slots and state (`/usr/data/disc-boot/`)

```text
/usr/data/disc-boot/
  state.json            default mode, boot-loop count
  service/a/ service/b/ the two slots of the service role
  service/current       "a" or "b"; renamed into place atomically
  service/request       a package's request, written atomically
  ui/…                  the same for the ui role
  data/<name>/          a package's own persistent data, kept across updates
```

Every change is a new file, synced, then renamed into place (UBIFS keeps a
rename atomic through power loss). A slot is `tentative` until confirmed.

## Lifecycle of a `service` package

1. Start (`S99`, after stock's init): verify the current slot (manifest and
   every file's SHA-256), then run the entry with the arguments and the
   environment below, at a lower priority than stock (`nice` +5).
2. **Ready:** the package creates `$DISC_BOOT_RUN/ready` within `ready`
   seconds (at most 120), or boot stops it and counts a failure.
3. **Confirmed:** ready and still running 180 s later; a tentative slot
   becomes the confirmed one and the boot-loop count clears.
4. **Crashes:** a tentative slot rolls back to the previous confirmed one at
   its first failure. A confirmed one restarts at most 3 times in 10 minutes,
   then stays stopped and the status says why (stock keeps working).
5. **Stop** (`rcK`, or a request): SIGTERM, 5 s, then SIGKILL.

Timings are initial values, tuned on the guest and the device.

### Requests from a package

A package writes `$DISC_BOOT_REQUEST` (JSON, atomically) and exits:

- `{"action": "activate"}`: the inactive slot (`$DISC_BOOT_INACTIVE`, which
  the package filled and verified itself) becomes the tentative current one;
  boot verifies it again and starts it.
- `{"action": "rollback"}`: back to the previous confirmed slot.
- `{"action": "default", "mode": "stock" | "platform"}`.
- `{"action": "remove", "purge": false}`: the role's slots go; `purge` also
  removes `data/<name>/`.

Boot applies a request only after the package has exited and records the
outcome in its status; a request it cannot apply changes nothing.

## The `ui` role

Boot puts a launcher named `mq_ui` in `/sbin`, ahead of `/usr/bin` in
`PATH`, so stock's `fiio_init.sh` (its first start and every restart from its
watch loop) runs the launcher without any stock file changing. The launcher
execs the installed `ui` package in `platform` mode and stock's
`/usr/bin/mq_ui` otherwise. It counts its own starts: 3 starts within 2
minutes without confirmation fall back to stock's UI for the rest of the boot,
since each crash also restarts `mq_player`. To verify on the guest: the
`PATH` actually seen by `fiio_init.sh`; if it differs, the alternative is
diskOS's change to `fiio_init.sh` itself.

## Recovery from the card (Play at power-on)

- Staged folders: `.disc/boot/install/service/` and `.disc/boot/install/ui/`,
  each with `package.json` and its files. The installer or the page puts
  them there.
- Boot waits for the card within a bound, verifies each staged package
  completely, writes it into the role's inactive slot, makes it the tentative
  current one, removes the staged folder and writes
  `.disc/boot/result.json` (what was installed or why not). The packages then
  start as in `platform` mode.
- Without the gesture boot never installs or runs anything from the card.

## Environment of a package

| Variable | Meaning |
| --- | --- |
| `DISC_BOOT_API` | The boot layer's API version |
| `DISC_BOOT_ROLE` | `service` or `ui` |
| `DISC_BOOT_PROFILE` | The firmware profile (`v2.57`) |
| `DISC_BOOT_SLOT` | The running slot (read-only by contract) |
| `DISC_BOOT_INACTIVE` | Where an update is staged |
| `DISC_BOOT_REQUEST` | Where requests go |
| `DISC_BOOT_DATA` | `data/<name>/`, persistent |
| `DISC_BOOT_RUN` | `/run/disc-boot/<role>/`, volatile; `ready` goes here |
| `DISC_BOOT_STATUS` | `/run/disc-boot/status.json` |
| `DISC_BOOT_CARD` | The card's mount point (it may be absent) |

Standard output and error go to `$DISC_BOOT_RUN/log`, capped at 64 KiB.

A package must not write MTD devices, change FiiO's files in `/usr/data`
(`fiio/`, `sn.txt` and the rest), signal stock processes, take stock's ports
or create a USB gadget while the console owns the controller. Boot cannot
enforce this: packages run as root, at the installer's risk.

## Status (`/run/disc-boot/status.json`)

The boot layer's version and API, the profile, the mode and its reason
(`default`, `key`, `boot-loop`, `recovery`), the key read, and per role: the
package's name and version, the slot, tentative or confirmed, failures, the
last request's outcome. The server shows it in its diagnostics.

## USB console

Unchanged in behavior ([USB diagnostics](usb-diagnostics.md)): the card
marker `DISC_WEB_USB_DEBUG` with its exact content enables it at boot,
independently of the mode. The installer writes the marker (owner,
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
3. The installer copies the chosen packages (our server; the player into
   `Apps/`) into `.disc/boot/install/`, and the console marker.
4. The user powers on holding Play; boot installs and starts the packages and
   writes the result to the card; the page then shows the server's status.

## What changes against today

- `/opt/disc-web/disc-service` and `S99disc-web` leave the image; the server
  becomes the `service` package, and its `--supervise` moves into boot. This
  stage renames the on-device paths explicitly (`/usr/data/disc-web.disabled`
  is replaced by the default mode); the console's marker stays.
- The image builder's invariant stays: stock objects unchanged, only listed
  additions (now the boot binary, its `S22`/`S99` hooks, `/sbin/mq_ui`, the
  console and its hook).

## Tests

- Host: the manifest parser, verification, slot state machine, requests,
  boot-loop guard and launcher logic against a fake filesystem and clock.
- Guest (V2.57): install, activate, crash before ready, crash after ready,
  rollback, a request during power loss (kill and reboot between steps), a
  staged package with a wrong file, profile or API, `stock` and `platform`
  boots, a `ui` package under stock's watch loop.
- The key read sits behind one function; until the emulator models the GPIO
  pins it is tested on the host and confirmed on the device once.

## Acceptance: two independent packages (owner, 2026-10-02)

The contract holds when projects that know nothing of each other run side by
side through it: **diskOS's UI as the `ui` package and our server as the
`service` package, with the player on the card**, first on the guest, then on
the device. diskOS (MIT) is built from its own sources; nothing of it enters
this repository.

What diskOS's image adds today and how each part maps:

| diskOS image today | As a package |
| --- | --- |
| Its `mq_ui` in `/opt/diskos`, copied to `/usr/data` and checked against a baked manifest by `S97diskos_install` | The `ui` package's entry; boot verifies and installs it |
| A patch of `fiio_init.sh` that runs `/usr/data/mq_ui` and `/usr/data/mq_player` (a link to stock's player) | Boot's `/sbin/mq_ui` launcher; stock's player untouched |
| Its own Volume Up check and "Default UI" file | Boot's modes; diskOS's check becomes redundant (harmless: with the key held boot already runs stock's UI) |
| Dropbear and `diskos-debug.sh` under `/usr/project` | Listed files of the package, found through `$DISC_BOOT_SLOT` |
| Dev variant's always-on USB serial shell | Boot's console (one gadget owner) |

Already known: stock's control ports 12100 and 12103 belong to `mq_player`
(observed on the guest), so the server's bridge does not depend on the UI
process. The experiment checks the rest: which process scans the card and
keeps `song.db` (if it is stock's UI, a replaced UI must keep the database
or the server's library goes stale), both packages under stock's watch loop,
one package's rollback without touching the other, and the modes with both
installed. Whatever diskOS needs changed to fit becomes either a contract fix
here or a small, upstreamable change on its side.

## Open before implementation

- diskOS's release with V2.57 support: what its image changes on V2.57, and
  whether the table above still holds (a separate session of the emulator
  project).

- What "Reset all" removes in `/usr/data` on the device (the emulator does
  not complete it; contract-neutral, see the facts).
- Free space of `/usr/data` on the owner's player (a diagnostics field).
- Play on GPB15 on this unit (the device, or the emulator once it models the
  pins).
- Memory and priority limits that keep stock's audio smooth (the device).
- The emulator runs stock's `rcS` and `fiio_init.sh`, so boot's hooks and the
  `mq_ui` launcher can be accepted on the guest (the emulator session).
- Whether the installer writes the console marker always or as an option,
  and whether it removes it after the installation.
- An optional "official" label for packages signed by us (shown, never
  required).
