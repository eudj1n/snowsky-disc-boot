# Stock acceptance and installation/recovery sequence

Pre-installation stock identification and post-write verification have different
references. This stage makes that distinction explicit. No player access,
writer execution, image modification or hardware authorization is involved.

| Phase | Required comparison | Tool |
| --- | --- | --- |
| Stock before installation | Every official rootfs byte, then every profile-reviewed stock tail byte | `preinstall.py` |
| Candidate after writing | Every byte of the exact approved padded candidate | `readback.py` |
| Stock after restoration | Every byte of the exact approved padded restore image | `readback.py` |

The [earlier full-image rejection](rootfs-full-observation.md) remains valid.
This new stock check is not a retrospective pass of that test. The writer will
program a padded image, so subsequent readback must include its padding even
if the stock device previously had FF.

## Reviewed stock contract

`firmware/preinstall/v<version>.json` pins the firmware and complete page policy,
selects `stock-before-installation`, and specifies one exact tail byte. The
current profile selects FF based on the retained full observation. It does not
infer a tail from incoming data, accept mixed padding or fall back to zero.
Another firmware release needs its own reviewed profile and compatibility
acceptance; the algorithm has no version predicate.

This is a limit for the selected stock state, not a claim that every unit/reflash
must have that tail. Unrecognized tails are rejected for review. A previously
restored zero-padded image is checked with the exact restore-image verifier.

The supplied restore file must match its reviewed whole-image hash, writer
capacity, official rootfs hash/length and zero padding. It is read, not modified.
The capture is decoded directly from records; the collector's derived image is
not used as a reference. Shared record validation checks nonce/sequence/page,
CRC, padding, ID, ECC/configuration and counters, constructs the good-block map
from paired markers, and checks data-pass markers. The exact-image CLI has no
new flag to ignore or substitute its tail.

The comparison handles a rootfs boundary inside a page, checks the entire tail,
and rehashes its reference while streaming. Memory remains bounded by a
page/record and the profile-sized block map. It reports the observed full-image
hash separately from the reviewed restore hash.

`saved-stock-preinstall-content-matches` establishes saved content only.
`postwrite_verified`, provenance, freshness, active boot and `flash_ready` remain
false. A nonce cannot prove current device state or physical provenance.

```sh
python3 scripts/deployment/preinstall.py verify --version 2.57 \
  --metadata-page work/metadata-observation-001/metadata-main.bin \
  --restore work/deployment-profiled/stock-v257-restore-review-only.bin \
  --restore-sha256 e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0 \
  --records work/rootfs-full-observation-002/records.bin \
  --nonce-hex b9c8f3afbcf940838bc2f719d18772b3
```

Use `plan` with the reference arguments, omitting records/nonce, for an offline
plan. Firmware selection also supports `FW_VERSION`/active-profile selection.
There is no CLI tail override or physical execution action.

## Installation and restoration design

This is the required workflow for separately authorized installation. The
[writer transport](writer-transport.md) now implements staging and single-invocation
handling, with synthetic acceptance and physical write admission closed. This
is not an admitted physical flashing procedure yet.

1. Bind selected firmware, chip, metadata extents, writer/SPL source and binary
   pins, exact capacity and candidate/restore hashes. Bind the accepted
   [static boot-target assessment](bootloader-review.md) and capture provenance/currency. Primary-rootfs content and its
   observed map are established; no full-chip backup is added as a prerequisite.
2. Prepare one exact session plan with RAM ranges, writer entry, poisoned result,
   transfer/time bounds and one invocation. The pinned diskOS host stages the
   writer at `0xa0c00000`, starts it at `0xa0c00030`, stages 96 MiB at
   `[0xa1000000, 0xa7000000)`, and reads 1 KiB of completion at `0xa0a00000`.
   The pinned binary entry saves ROM return/stack at TCSM `0xb24017f0`/`0xb24017ec`
   and uses stack top `0xa0bffff0`. These facts do not expand the existing reader's
   RAM allowlist; its small checks did not qualify this image staging area.
3. Establish the required memory capacity/addressing and compare the complete
   staged writer and image before execution. Protect non-overlapping stack,
   code, data and result regions; poison/read back completion before the single
   start. The existing writer has no nonce or chip-ID admission. Bind fresh
   reviewed identity and exact inputs to a retained host session instead of
   trusting a standalone debug record.
4. Resolve completion without host replay/reconnect. The writer's internal
   block retries (at most six) must be explicit in the approved algorithm. The
   diskOS host defaults to 900 seconds of waiting; this is reference behavior,
   not a newly qualified timeout. Killing the host cannot cancel a RAM writer.
   An uncertain invocation requires resolving device execution state before
   retry or power-cycle, not automatic recovery actions.
5. Independently reacquire after writing and compare every byte to the approved
   candidate hash. Reconstruct the block map again; do not assume the old map or
   use the pre-installation tail rule. Verify companion startup, stock coexistence,
   cold boot and engineering USB activation/revocation on hardware.
6. Separately authorize the exact stock-restore write, perform the same reviewed
   writer flow, compare the complete restored image including its zeros, and
   check stock boot. A restore file on the host is not a tested recovery path.

The target remains primary rootfs; kernel, recovery rootfs, userdata and
calibration are excluded. Blocks 383/716 are observed evidence, never hard-coded
skips. The subsequent bounded [complete-image staging experiment](writer-staging-observation.md)
passed, with owner-confirmed stock reboot. The subsequent
[captured SPL review](bootloader-review.md) establishes static primary-pair
selection for the observed `ota:backup`. Live-root observation, physical
installation/recovery remain open. The [installation package](installation-review.md)
now binds accepted evidence to exact images and readback plans; physical write
admission remains closed pending concrete authorization. The [installation/restore sequence](installation-procedure.md)
details the next acceptance steps. No physical action is proposed by this document alone.

## Local evidence — 2026-09-24

The new checker accepted all 50,816 saved records: exact official rootfs,
20,066,304 FF tail bytes, bad blocks 383/716 and the original ECC histogram.
Its plan SHA-256 is
`52f295db7f487c1cc950c14dd28fd9e63f821d4d8966735a59f1f35cad594230`.
The ignored `work/rootfs-full-observation-002/preinstall-content-review.json`
hash is `79b285a019d68c7c1f340c3112f0a13abc99e297e377ba2cd605ea60715742fd`.
The strict comparator, rerun after sharing its record scanner, produced the
byte-identical rejection report at physical page 44538.

The offline engineering-image review passed again with all five diskOS pins
intact. Both unchanged review-only files have 96 MiB capacity:

| Image | SHA-256 |
| --- | --- |
| USB engineering candidate | `463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2` |
| Official stock restore | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |

Nine new synthetic tests cover the two contracts, non-page-aligned boundaries,
first/last/mixed tail corruption, reference corruption, record completeness,
invalid frames/ECC/nonce/order, symlinks, profile drift and explicit future
firmware/tail selection. They and ten strict readback tests passed on macOS and
Linux without firmware, a player or sibling checkout. GitHub Actions discovers
them through the existing test command; no hosted run is claimed. Logs and
actual-input reports stay ignored under `work/preinstall-*`.

The complete host suite passed: 204 Python tests, eight JavaScript tests and
native C assertions (`work/preinstall-conformance.log`). Linux focused results
are in `work/preinstall-linux-tests.log`. No service, browser, firmware payload
or acquisition profile changed, so those integrations were not rerun.
