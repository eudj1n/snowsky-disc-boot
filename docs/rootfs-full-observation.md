# Authorized full rootfs observation — 2026-09-24

The complete bounded collection succeeded. All 80,596,992 bytes of the official
rootfs match the player byte for byte. The remaining 20,066,304 bytes read as
`FF`, whereas the prepared 96 MiB stock-restore image uses zero padding. Therefore
the strict full-image comparison **failed**; it has not been waived or relabelled
as a pass. The owner confirmed normal stock operation after reboot.

## Session and physical scope

The owner separately authorized the [full read plan](rootfs-collector.md#proposed-full-read-after-oob-review)
and then requested the connected-device invocation. This was one execution,
with no retry, reconnect, NAND program, erase or feature write.

- Session: `a6091d9e-6d40-4a05-a69d-bba6ec183e60`.
- Nonce: `b9c8f3afbcf940838bc2f719d18772b3`.
- Plan SHA-256: `bf0d6e2877d36529ef49ecc2f1a0c3040814ce2343c6db64213d72ab25325894`.
- UTC interval: `2026-09-24T04:27:35.759954` to `05:05:10.229694` (37m34.47s).
- One SPL execution, six reserved RAM regions checked with two patterns,
  794 reader batches, 42,220 USB protocol calls and 796 exact `X2000` replies.
- Result: `rootfs-collected`, 50,816 complete records, 100,663,296 logical bytes;
  connection cleanup completed without errors.

Paired markers were read for every physical block in `[80, 912)`. Blocks 383 and
716 consistently carried non-FF markers; all others were FF. The data pass read
the first 768 good blocks: `[80, 850)` excluding those two. The third first-page
marker check agreed for each selected good block.

ECC status was zero in 50,795 records and one in 21 records. Those 21 are distinct
data pages, indicating one corrected bit per reported read under the reviewed
chip policy; their corrected main bytes also match the official rootfs. No
uncorrectable or undocumented ECC result was accepted. Readiness required two
polls in 49,149 records and four polls in 1,667 records.

## Independent saved-evidence checks

The offline journal reconstruction checked every call in order, every retained
incoming transfer and outgoing digest, both RAM pattern passes, the payload,
all request/result buffers and the resulting record/image files. Execution
targets were exactly the single SPL entry and the approved reader entry. The
review made no new device calls.

The existing strict verifier was run against the unchanged, independently
reviewed stock-restore image. It returned exit 1:

```text
Image mismatch at logical block 614, physical page 44538
```

The first differing byte is exactly offset 80,596,992, the selected firmware's
rootfs length. A separate streaming comparison and SHA-256 check confirmed the
entire official rootfs prefix. Every remaining byte through the 96 MiB endpoint
is FF on the player and zero in the prepared image; there are no other main-data
differences. Neither image nor the strict verifier was modified.

To validate records beyond the strict comparator's stopping point, the same
independent verifier also checked all 50,816 records against the saved collector
image. That second result is labelled **self-consistency only**: its reference
comes from this acquisition, so it cannot establish independent stock equality.
It validates the complete nonce/order/page/CRC/ECC/configuration checks, paired
markers, reconstructed block map and record-to-image agreement through EOF.
The separately pinned official-rootfs comparison establishes content identity.

The OOB classifier validated the complete stream and compared repeated pages
5120, 5184, 24512 and 45824 (the first two good blocks and the two bad blocks).
For those selected repeats, differences remain confined to internal ECC parity;
main and user metadata agree. The full stream contains three parity-region
hashes, so the probe's two-pattern alternation does not describe the whole
capture. Parity visibility/cause and raw NAND reproduction remain unqualified.

## Artifact fingerprints

All runtime files are ignored under `work/rootfs-full-observation-002/`.
No firmware, captures or proprietary content is committed.

| Artifact | SHA-256 |
| --- | --- |
| 227,452,416-byte `records.bin` | `ad8a12c385b9ef02f95533c35be02bffed81a244b2f90846599b034cd464f8d0` |
| 100,663,296-byte `logical-image.bin` | `7f63188c6dc60e8b45a2d79032c8c7fc68b7022d201710286f86fb38c889359f` |
| Official rootfs prefix | `111e4dd7ee3d7ff91ba7e61181690be7ffd22bd1bbd13ad513f5f015ffb302ae` |
| `result.json` | `3d891fe0fb9b822a09a1fbbf4d9a47a3c01ae609b67eb10097ad9ab58999dbff` |
| `transfers.jsonl` | `3f2f0dac154a74b3624ba73cb13ec0f199429d4f28d719b0671eb17b9a841d7a` |
| `content-classification.json` | `aedfb1bb720095ed6ae8da2696eee06db75108f043feab3602d809789baaf4ef` |
| `offline-journal-review.json` | `bb0b2c012a85201bbeefcd5e57713152c7074573c611821dc107d0bc35f36131` |

The exact stock mismatch is retained in `stock-readback-verification.json`;
complete record validation is in `saved-record-consistency.json`. The retained
offline scripts are `work/review-rootfs-full-002.py` and
`work/classify-rootfs-full-002.py`. The generic OOB CLI remains reproducible with
the session nonce, `--count 50816` and the selected pages above.

## Acceptance and remaining work

This establishes a complete main-data acquisition for the approved rootfs scope,
its observed bad-block mapping, exact official rootfs content, the actual FF tail
and a clean return to owner-observed stock operation. The observed region is not
a full-chip/userdata/calibration backup, and corrected main data is not raw NAND.

The original full stock-restore equality gate remains rejected because padding
differs. The installation review must explicitly distinguish pre-installation
stock content from the exact padded image a writer will program; it must not
silently change this run's expected hash. Any future post-write verification
still compares **every** programmed byte, including padding, with its approved
image. Active boot selection, writer execution, companion startup and restoration
remain unqualified; `flash_ready` remains false.

**Subsequent review:** the [separate pre-installation contract](preinstall-review.md)
now checks the pinned official rootfs and profile-reviewed FF tail. It passes
this saved capture; the original strict whole-image rejection above remains
unchanged, as does the exact post-write requirement.

On 2026-09-24 the owner answered that the player boots and works normally after
this run. This is direct owner observation, not an automated boot/playback test
or evidence that the companion is installed.

No runtime behavior, profile, acquisition binary or test changed for this
evidence stage. Validation used the actual authorized collection, the existing
independent verifier and OOB classifier, full journal reconstruction and separate
streaming content checks. Prior synthetic test results still apply; no new
GitHub Actions, emulator or browser run is claimed.
