# Development

## Layout

| Path | What |
| --- | --- |
| `docs/contract.md` | The boot layer's contract with packages |
| `docs/plan.md` | The canonical plan |
| `device/src/boot.c` | `disc-boot`: modes, the service's supervisor, requests, recovery, the `mq_ui` launcher |
| `device/src/manifest.c`, `boot_util.c`, `sha256.c` | Package manifests and verification, files, JSON and state, SHA-256 |
| `device/vendor/jsmn/` | The JSON tokenizer (MIT, pinned) |
| `device/src/usb_console.c` | The USB ACM engineering console |
| `device/acquisition/` | The NAND reader and SFC identity payloads for USB Boot sessions |
| `device/deployment/boot-report.sh` | The boot report written to the card |
| `scripts/package.py` | Packages: describe a folder, check it as `disc-boot` does, zip it, stage it on a card |
| `scripts/deployment/build_candidate.py` | The image builder (variant `boot`): stock plus the boot layer, verified offline |
| `scripts/deployment/` | Reviews, transports, readback and audits of a USB Boot installation |
| `tests/integration/` | Checks run in the disposable container ([build and flash](build-and-flash.md)) |
| `scripts/firmware_profile.py`, `firmware/` | Reviewed firmware profiles (V2.57) |
| `tests/conformance/` | Synthetic tests, runnable without firmware or a player |

## Build and test

```sh
bash scripts/test.sh          # host build of the console and NAND tests, then every conformance test
bash scripts/build.sh host    # host build only
```

The player's binaries need the pinned soft-float toolchain (Docker):

```sh
docker build --platform linux/amd64 -t disc-native-toolchain -f device/Dockerfile.toolchain .
bash scripts/build.sh mips     # the console, static and soft-float
bash scripts/build.sh reader   # the NAND reader's MIPS tests and freestanding core
```

`DISC_TOOLCHAIN_IMAGE=<image>` selects a reviewed copy of the toolchain image.

The host build also makes `disc-boot-fixture` (`-DDISC_BOOT_FIXTURE`): it
reads `DISC_BOOT_FIXTURE_ROOT` (every absolute path is taken under it, the
keys come from `fixture/keys`) and `DISC_BOOT_FIXTURE_TIMING`
(`confirm=1,grace=1,…`). The image builder refuses any binary carrying these
names. To run `test_boot` on Linux against the MIPS build, build
`../build/mips/disc-boot-fixture` with the toolchain and point
`DISC_BOOT_FIXTURE_BINARY` at a wrapper that runs it under
`qemu-mipsel-static -0 "$(basename "$0")"` (the emulator container has one),
with `DISC_BOOT_FIXTURE_FILE` naming the binary itself.

## Packages

`scripts/package.py` follows the contract's rules and messages (a test holds
it to `disc-boot verify` case by case):

```sh
# package.json for a folder: every file with its size, SHA-256 and mode (0755 when executable)
python3 scripts/package.py describe --source build/pkg --name disc-server --version 2026.10.02 \
  --role service --entry bin/disc-server --arg --listen --arg 0.0.0.0
python3 scripts/package.py check --source build/pkg          # or a zip; --role, --profile, --arch
python3 scripts/package.py zip --source build/pkg --output work/disc-server.zip
# On a mounted card, for the recovery with Play (an explicit operator step):
python3 scripts/package.py stage --package work/disc-server.zip --card /Volumes/PLAY --confirm-card-write
python3 scripts/package.py result --card /Volumes/PLAY        # the last recovery's result.json
```

A zip holds `package.json` and the files at its root with their modes, in a
fixed order with fixed times. `stage` checks the package for the player
(`mips32el-linux-static`, the active firmware profile), writes it beside
`.disc/boot/install/<role>/`, checks the copy (sizes and digests; a card keeps
no modes), then swaps it in; a refused package stages nothing and leaves what
was staged.

## Related repositories

- snowsky-disc-server: the gateway (the `service` package), its catalogs
  and the emulator wrapper (`scripts/emulator.py`) used for guest acceptance
  today.
- snowsky-disc-web: the frozen history of the gateway and the combined
  images; the running emulator stack still mounts it as `/platform`.
- snowsky-disc-qemu: the external emulator; never modified from here.
- snowsky-disc-player: the page, a card app of the server.
