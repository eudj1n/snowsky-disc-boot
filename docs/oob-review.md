# Offline OOB review

The rootfs probe's differing bytes fall entirely within the documented internal
ECC parity region. This identifies their address range; it does **not** establish
why returned bytes alternate or qualify a raw NAND backup.

## Reviewed layout and implementation boundary

The manufacturer-authored [XT26G02C Rev 1.8 datasheet](https://pdf.elecfans.com/p/10947115.html),
section 11, Table 11 (page 40), describes these half-open cache byte ranges:

| Region | Cache offsets | OOB offsets |
| --- | --- | --- |
| Main data | `[0, 2048)` | — |
| Protected user metadata | `[2048, 2112)` | `[0, 64)` |
| Internal ECC parity | `[2112, 2164)` | `[64, 116)` |
| Unprotected user metadata | `[2164, 2176)` | `[116, 128)` |

The same section describes always-on ECC and corrected cache output. It does
not state that repeated reads should alternate between two parity values.
Therefore the observed alternation remains unexplained, rather than being
declared normal chip behavior.

Static review of `nand_reader.c` and `sfc_x2000.c` finds one 2,176-byte cache
transfer at column zero, followed by status/configuration checks and CRC over
the complete returned data. All bytes use the same FIFO copy loop; there is no
special handling at offset 2,112. The wrapper clears its result and publishes
completion after cleanup. This review finds no explicit parity transformation,
but cannot exclude a controller, timing or cache-visibility issue without new
evidence. It is not a hardware trace.

`firmware/oob/<chip>.json` selects the layout by reviewed chip identity and pins
the entire existing chip profile. It is separate from acquisition policy and
does not change payloads, accepted ECC statuses, CRC coverage or physical scope.
New firmware still needs its normal reviewed profiles and compatibility checks.

## Reproducible classification

`scripts/deployment/oob_review.py` streams saved request/result records, validates
all framing, nonce, consecutive sequence, page bounds, CRC, padding, ID,
ECC/configuration and counters, then compares selected repeated pages against
their first observation. Difference offsets are relative to the named region.
It also reports parity hashes across all records and whether two values alternate
by record position. No USB library or external checkout is used.

The caller supplies the expected count and nonce. File length must match exactly.
Limits are 65,536 records, 64 selected pages and 256 repeat comparisons. Each
selected page must occur at least twice; incomplete evidence is rejected.
Unselected pages are validated but are not checked for repeated-data equality.
The audit does not verify the collector's planned page order, USB journal, stock
image, provenance or freshness; those are separate checks. Exit zero means a
report was produced, even if differences affect main data or user metadata.

Reproduce the retained probe audit:

```sh
python3 scripts/deployment/oob_review.py --version 2.57 \
  --records work/rootfs-probe-observation-002/records.bin \
  --nonce 0b9c5bfa2f7f4a9b892a95b069068e11 --count 132 --pages 5120 5184 \
  > work/rootfs-probe-observation-002/oob-review.json
```

These values select existing evidence, not a new acquisition. The selected
firmware can also come from `FW_VERSION` or `firmware/active-version`.

## Saved observation — 2026-09-24

The audit validates all 132 records from the previously authorized probe,
capture SHA-256 `1aee3a94f353b3f35b0a80ebf8165a5d85efe445f53c384d92cc6edb4818bc12`.
Both selected first pages have three observations. Main bytes and both user
metadata regions agree in every comparison. Each second observation differs
from the first at 51 of 52 parity bytes; parity-relative offset 17 is unchanged.
Each third observation agrees with the first over the complete page and OOB.

Across all records, the parity region has exactly two hashes, 66 observations
each, alternating by record position:

- `c5041f6d2c080fa5183260d2122b03c103a4a71305ec1b45bedbc912f7ec7a53`
- `e528572c057ba9a4a13889e8bcda9ce9a78d41cdba7ea7f67b98bad17221a3b7`

The ignored report SHA-256 is
`3058f72924a0995717994aeaca4ebb98819307a45bae3a77afa2ad6215e5a9de`.
`cause_established`, `raw_nand_verified` and `flash_ready` remain false.
The earlier independent stock-prefix comparison and USB journal review remain
separate evidence. No new device access occurred during this review.

## Decision for the next step

The region review is complete enough to propose the existing **read-only full
rootfs collection**, retaining all returned OOB bytes and existing checks. Its
acceptance target is ECC-checked main-data equality and first-byte bad-block
mapping, not raw parity reproducibility. The full independent verifier must
compare every main byte, including writer-format padding, to the reviewed stock
image. Any mismatch fails equality; it must not be hidden by ignoring padding,
guessing bad blocks or changing the image expectation after acquisition.

Unexplained parity alternation remains a limitation. Neither this classification
nor a successful future full comparison alone authorizes programming or proves
active boot selection. Installation still requires the writer/recovery review.
See the [concrete full collection proposal](rootfs-collector.md#proposed-full-read-after-oob-review).

## Tests

Ten synthetic tests cover all region boundaries, alternating/equal/changing
parity, non-parity differences, report bounds, missing duplicates, malformed and
unselected records, ECC codes, stale nonce/order, page bounds, truncation, extra
bytes, symlinks, layout/profile drift, the CLI and a future firmware identity.
They use no captured device bytes, firmware, credentials or sibling repository
and are discovered by the existing GitHub Actions test command.

Local validation passed on macOS/Python 3.13 and Linux/Python 3.11 (all ten audit
tests). The full host regression passed with 193 Python tests, eight JavaScript
tests and native C assertions; the final audit run also covers two subsequently
added pattern/report-limit cases. Logs remain ignored under
`work/oob-review-conformance.log` and `work/oob-review-linux-tests.log`.
No hosted GitHub Actions run, browser change or new firmware execution is claimed.
