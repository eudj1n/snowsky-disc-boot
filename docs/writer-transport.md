# Writer staging and single invocation transport

The host transport is implemented and tested against a synthetic ROM. The
current installer profile keeps `physical_write_admitted: false`, now with a
pinned [installation evidence review](installation-review.md). Only separately
authorized RAM staging is executable until the prepared admission profile is
authorized and activated. A closed write acquisition is rejected before output
creation, library loading or USB discovery. Initial
implementation validation was offline; the later separately authorized
[complete-image RAM experiment passed](writer-staging-observation.md).

The bounded staging sequence passed on the owner's unit. The subsequent
[captured SPL review](bootloader-review.md) establishes static primary-pair
selection. The installation package now binds that assessment; live-root observation,
writer execution, independent post-write readback and candidate/restore boot
acceptance remain open. The existing native companion passed its disposable guest gate; this
transport does not complete its physical deployment gate.

## Inputs and firmware selection

`scripts/deployment/writer_transport.py` selects the usual reviewed firmware,
CPU, reader, transport and metadata policies. `firmware/installers/v<version>.json`
binds their hashes and the writer profile to RAM regions and time budgets.
Selection uses `--version`, `FW_VERSION` or the active profile; no firmware
version predicate selects an address or bypasses acceptance.

The legacy diskOS executable is not relocatable. Its reviewed linked ABI is
explicitly checked: code at `0xa0c00000`, entry offset `0x30`, stack top
`0xa0bffff0`, debug at `0xa0a00000`, and image at `0xa1000000`. A different
executable/layout needs an ABI review and tests, not just changed addresses.
All source/binary pins and exact writer capacity are checked before planning.
The candidate and restore files both pass the existing offline image review;
only the explicitly selected file is staged. The image is held as immutable
host bytes, rehashed after review and included in the plan. No shell helper or
external diskOS flashing command is launched.

The plan binds the actual SPL, metadata build, writer, image, saved metadata,
source files, mode, target, regions and limits. Its hash is required for
acquisition. A changed input requires a fresh plan and scope review.

## Stage-only sequence

1. Select exactly one ROM device and retain its bus/port identity. No automatic
   detach, reconnect or fallback device selection is performed.
2. Reuse the bounded SPL, DDR diagnostic and small-RAM checks. Execute the
   metadata reader once; check fresh chip identity, configuration, ECC, framing
   and partition extents. Require the metadata main page to match the planned
   saved page byte for byte. This does not identify the active boot path.
3. Exercise every byte of the six small staging regions over USB. Write
   address/nonce-derived SHAKE256 patterns to **all** chunks before comparing
   **all** chunks, then repeat with their complement. This detects aliases
   that an immediate write/read of each chunk could miss.
4. Check the image region on the player (plan, stage 4c): upload the staging
   check (`device/acquisition/staging.c`, built as the `staging-check` payload
   with no NAND opcodes) into the code region and compare it, then run it once
   for the region. It writes xorshift32 words from a seed of the session's nonce
   to the whole region before reading any back, then their complement, and
   answers with the words it checked; the host compares the whole 80-byte
   result (nonce, address, length, count, no bad address).
5. Upload the complete image, then read all of it back and compare it, as
   before. Run the staging check again to hash the image's first
   `staging_sample_bytes` (1 MiB) on the player and compare that with the
   host's SHA-256 of the same bytes. See [the hash on the player](#the-hash-on-the-player).
6. Poison all 1 KiB of writer debug memory, explicitly invalidating its three
   completion words. Stage the pinned writer over the staging check and guard
   patterns for the other small regions; compare every byte again after all
   writes finish.
7. Save `writer-staging-verified` and close USB. There is no writer invocation,
   NAND erase/program, host reset, reboot or automatic next stage.

The metadata reader's NAND commands remain the reviewed read-only set. SPL
execution and RAM writes are physical actions and require their own concrete
owner authorization. This experiment overwrites volatile memory, so it is not
an ordinary running-stock operation. Normal stock boot after a manual exit from
ROM mode must be confirmed separately.

For the current profile the image is 100,663,296 bytes at
`[0xa1000000, 0xa7000000)`. The reviewed address ceiling is `0xa8000000`; it is
an experiment bound, not proof that all that RAM works on the physical unit.
All regions are non-overlapping; code, reader stack/request/result, writer stack
and debug are checked alongside the image. Bulk chunks are at most 64 KiB.

The staging deadline is 900 seconds. The write plan's upper bound is a few
thousand calls fewer than before stage 4c (27,892): one held ask after each run
of the staging check (at most `staging_check_ms` 600 s and `staging_sample_ms`
60 s) and two for the writer.
The image crosses USB twice (its upload and its read back, 192 MiB, about
4 minutes at the observed 0.9 MB/s) instead of six times, and the session
retains about 96 MiB of image read evidence. Have at least 1 GiB of free host
disk before an authorized run. A timeout stops host activity; it does not
silently reduce the tested image or skip a comparison.

### The hash on the player

The staging check, like every payload of this transport, runs from the code
region's uncached address (kseg1): each instruction is fetched from DRAM. The
region's pattern costs about a dozen instructions a word in each of its four
passes; SHA-256 about 40 a byte, its schedule and constants uncached loads too,
so a whole image's
hash there may take longer than reading the image back (about 2 minutes). So
the image is compared over USB as before, and the player hashes a 1 MiB sample:
it checks the device's hash against the host's and its time
(`image_hash_sample_ms`, with `image_region_check_ms` for the pattern) decides
whether the whole image's hash replaces the read back. Running the payloads
from the cached alias (kseg0) would make both checks a matter of seconds, but
needs a review of the cache state the ROM and the SPL leave behind; the
transport deliberately has no cache flush request.

### Completion on the player

The ROM does not answer USB while a payload runs. After each run of the
staging check the host sends one CPU-info request held for at most
`staging_check_ms` or `staging_sample_ms`, answered once the check returns
(never a request given up: see [the RAM transport](ram-transport.md)); a
timeout or any other error stops the session before the writer. The audit
(`audit_usb_write.py`) reconstructs each ask. Measured on the player in a
stage-only session (2026-10-07, 5 min 41 s in all): the region's check 111 s
(about 7 minutes over USB before), the 1 MiB sample's hash 4.2 s (so the whole
image would take about 6.7 minutes there, longer than its read back).

## Write mode and uncertainty

The same implementation supports candidate and restore targets in a synthetic
write profile. **The checked-in profile keeps physical execution closed.**
Changing that flag is not hardware acceptance or owner authorization. Write
mode additionally requires the pinned review, exact current source/profile/build
and target image inputs, and checked readback build. Acquisition requires the
reviewed library, target-specific device-state confirmation and approved plan.
The [concrete package](installation-review.md) contains the prepared candidate,
restore and exact readback plans; its activation/physical actions still need
separate authorization.

Write mode repeats the entire stage in the same session; it never trusts a
previous process's RAM result. After successful staging and a remaining-budget
check, it journals one writer invocation before sending it. The current profile
specifies a 900-second wait and a 1,800-second overall session budget, following
the reviewed diskOS host wait. These bounds are not physically qualified writer
timing. Right after the invocation the host sends one CPU-info request held
for the whole `writer_wait_ms` (plan, stage 4c); the ROM answers it only once
the writer has returned, so the reply ends the wait and the result keeps the
writer's time (`writer_ms`). An ask changes nothing on the player: one that
fails at once is no outcome, and is followed by the rest of the wait and one
more request, as the fixed wait made it; no answer within `writer_wait_ms`
leaves the outcome unknown as before. After the reply, read exactly 1 KiB of completion; validate it with the
existing strict writer-record checker.

There is no host retry, reconnect or automatic restore, and an ask never
repeats the invocation. The pinned writer itself has up to six internal block
attempts, explicitly included in the plan. Killing the host cannot cancel that
RAM program. After an uncertain invocation, do not infer that writing stopped
or power-cycle/retry without resolving the execution state.

| Result | Meaning |
| --- | --- |
| `failed-before-writer` | This session did not attempt the writer entry; inspect the recorded failure. |
| `writer-staging-verified` | Full volatile-memory checks passed; no writer entry was attempted. |
| `writer-outcome-unknown` | Writer invocation was attempted and acceptance did not finish cleanly, including interruption or cleanup failure. Never replay automatically. |
| `writer-completion-observed` | ROM return and a structurally consistent completion record were observed; independent readback and boot are still required. |

The legacy completion record does not echo a nonce. Host session IDs, request
hashes and poison comparisons prevent accidental local reuse but cannot turn
that record into proof of freshness or NAND contents. `postwrite_verified`,
`boot_verified` and `flash_ready` remain false in every transport result.
Independently collect and compare every byte of the exact approved padded image
after candidate or restore writing. The pre-install FF-tail rule never applies
to post-write acceptance.

## Offline planning and tests

Example using locally prepared, ignored inputs (no USB library is loaded):

```sh
python3 scripts/deployment/writer_transport.py plan \
  --mode stage --target candidate \
  --build build/batch-metadata-regression \
  --diskos /path/to/diskos \
  --artifacts work/deployment-usb \
  --metadata-page work/metadata-observation-001/metadata-main.bin
```

`--target restore` plans the reviewed stock restore image. `--mode write` produces
an offline plan showing closed admission; it does not enable acquisition.
After separate authorization, `acquire` requires the same arguments plus a
reviewed `--libusb` file, fresh `--output` directory and the exact
`--approved-plan-sha256`. Do not substitute a write plan for a stage authorization.

Retain `request.json`, `dependency.json`, `transfers.jsonl`, raw reads,
metadata/request bytes, `writer-debug-poison.bin` and `result.json`. Write mode
also retains `writer-result.bin` if returned. Attempts are journaled before
transport calls; partial/failed inputs and unknown outcomes are preserved.
No runtime files belong in Git. Routine absent-device attempts remain only in
ignored logs and do not warrant a documentation entry or commit.

Thirteen firmware-free tests cover complete staging, candidate/restore selection,
profile/input drift, future version selection, linked ABI and range bounds,
aliasing, corrupted image/ECC/metadata, debug poison, short transfers, exhausted
budgets, interrupted waits, journal failure and failure at every protocol call.
The fake ROM deliberately permits a timed-out execute to reach the device;
the host retains uncertainty and never invokes again. Tests run through the
existing GitHub Actions discovery, without libusb, firmware, a sibling checkout,
a player or credentials.

Since stage 4c, `test_writer_transport.py` has 16 tests: its fake ROM runs the
staging check (the pattern through the region's own mapping, so an alias shows,
and the hash) and stays busy for some asks after each run and after the
writer. They add the sample's hash and its mismatch, the writer's wait ended by
the ROM's answer, failed asks during the write as no outcome, and the audit
reconstructing a write's journal with every ask (one ask removed is refused).
`test_staging_payload.py` compiles `staging.c` for the host with the request's
DRAM mapped into a buffer: the hash against `hashlib`, the pattern and its
complement left in memory, and every request out of its rules refused without
touching memory.

## Local validation — 2026-09-24

All 13 tests passed on macOS and Linux. The complete host suite passed with
217 Python tests, eight JavaScript tests and the native C tests. These initial
checks did not access hardware; no hosted CI run is claimed. The subsequent
[physical observation](writer-staging-observation.md) is recorded separately.

Offline stage/write plans for both targets passed against the unchanged local
metadata build and engineering artifacts. The exact 96 MiB images remain:

| Target | SHA-256 |
| --- | --- |
| Engineering candidate | `463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2` |
| Stock restore | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |

Logs and plans are ignored under `work/writer-*`. The stage-only experiment
subsequently passed, and the owner confirmed normal stock boot and operation
after leaving USB Boot and rebooting.
The current candidate stage plan is `work/writer-stage-candidate-plan.json`,
SHA-256 `6a1410c13549e697846fb2201a9fc7fb6336e952bf7fefef141602d9a60cd20d`.
Its scope is one SPL, one read-only metadata execution, complete RAM checks and
image staging, with zero writer invocations. Regenerate and compare the plan
before requesting acquisition; this hash is evidence, not authorization.
