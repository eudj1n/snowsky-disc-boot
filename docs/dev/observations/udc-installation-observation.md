# Corrected-controller installation observation — 2026-09-24

The owner authorized discovery, one write of the [reviewed corrected-controller
image](../installer/udc-update.md), and complete independent readback, confirming the same
player and unchanged firmware. macOS reported the profiled Ingenic USB Boot
target. Regenerated write and collector plans exactly matched the reviewed
package before acquisition. No automatic reconnect, retry, reset or restore was
performed. The original SD markers and report were not accessed or changed.

## Completed write

Ignored evidence: `work/udc-write-observation-001/`; authorization and admission
snapshots: `work/udc-physical-preflight-001/`.

| Field | Value |
| --- | --- |
| Session | `56f00c2b-6b2d-428d-bc62-8ffb0fde4d50` |
| Nonce | `f026c7dea23d4983b56225ff43fc27d4` |
| UTC start → finish | `2026-09-24T11:47:55.878097+00:00` → `2026-09-24T12:13:16.715618+00:00` |
| Approved writer plan | `abc6e62a491cc5a619105129258a3c1650c9698e987688f30d3a965711373cb4` |
| Candidate, 100,663,296 bytes | `76cc1ee9a8a9089bfca59bc61e81cbfc2ef9f5225a687150dc314df2f6d84618` |
| Result SHA-256 | `5674b4b32660ca1f26ea95d8f8255ed05b2e6ff7eed9c222de9bf5d0e90f49c3` |
| Journal SHA-256 | `a4ba28468845fb11f61120de6cfe3406143a9332834bf47e3149eee4276182de` |
| Independent audit SHA-256 | `be79398eeb47c8e592113cdf8c5a8446420eff85ce8f555209b840283ba898f4` |

Fresh CPU/SPL/DDR, NAND identity/ECC and partition metadata checks passed. Both
full RAM patterns and the complete image/writer/guard comparisons passed before
one writer invocation. After the profiled 15-minute wait, ROM responded and the
completion record reported all 768 logical blocks, physical end 850, skipped
blocks 383/716 and zero retries. USB cleanup completed without errors.

The saved-trace audit independently reconstructed every one of 27,844 calls,
4,645 raw incoming files (302,464,956 bytes), 4,638 bulk writes and exactly three
executions: SPL, metadata reader and writer. It uses the original proposed
installer profile snapshot and checks the nonce-derived RAM patterns, actual
image bytes, poisoned completion area, metadata and final writer record.
Audit script SHA-256:
`c5102419ff31da8a3cbfb77345d18a8d6a6c0467ec6a2af9cbb2a8943433442f`.

Physical write admission was closed again immediately after the successful
session. The original writer result retains `postwrite_verified=false`,
`boot_verified=false` and `flash_ready=false`; independent observations below
are separate evidence, not edits to that result.

## Full independent readback

Ignored evidence: `work/udc-readback-observation-001/`.

The read-only session began after confirmed writer return, USB cleanup and the
successful independent write audit. Its DDR guard passed without a reset or
retry. All 794 batches / 50,816 records completed, and cleanup reported no errors.
The exact comparator independently rechecked framing, nonce, ordering, CRC, ECC
and bad-block mapping against the separately approved candidate file. Every one
of 100,663,296 bytes matched, including zero padding. No stock-tail exception or
self-comparison with the reconstructed image was used.

| Field | Value |
| --- | --- |
| Session | `a3327b26-30c2-4011-92da-2beb8cb3c971` |
| Nonce | `cb319515f88642b7a1c3328c9ed767dc` |
| UTC start → finish | `2026-09-24T12:13:53.514260+00:00` → `2026-09-24T12:51:39.098576+00:00` |
| Approved collector plan | `eb1faaec76055a758f49fe319e6738e69c6edb70aa8e88f3992627449becc2dd` |
| Exact comparison plan | `9c0503df7317916525dfaa067c58d1bb6252176f27acaefde3e009e5a64e0629` |
| Result SHA-256 | `4fe1390208812185544bc9f5ec28f141647ed81100634e23703251c66e454c50` |
| Journal SHA-256 | `92ec3c377b01d272b0bc9149ea65eddaa6ce75efe6ec89db2e296052ac7802f5` |
| Records SHA-256 | `42a9aff3075b7d56460f5af15b7203252f50ef733ffd9df97b30e6a4e2f46406` |
| Exact comparison report SHA-256 | `36f1cd24ce1d2c811c1366da087aebe1e29f552a6355100e9debce9d929aaae7` |
| Independent audit SHA-256 | `52edc4a2f9de3e25fcdf4f94aed366c40e65417ea9a4a0da812e89d3fbaf5292` |

Mapping remains physical blocks 80 through 849, excluding 383/716. ECC status
counts were 50,809 code 0 and seven admitted corrected code 1, with no other
codes. Counts include repeated marker observations and do not constitute a
long-term NAND reliability assessment.

The independent read audit reconstructed all 42,220 calls, 9,553 raw incoming
files (453,235,160 bytes), 4,786 bulk RAM writes, one SPL and 794 reader executions.
No NAND writer ran in that session. Audit script SHA-256:
`8e49983f1a968279a9509183413448962135c3823dbb5453c54a65b18fff0f95`.
The original collector result and independent comparison/audit remain separate.

## Acceptance boundary

One authorized write and exact full readback passed. Both USB sessions are closed
and write admission remains false. After the requested normal reboot, the owner
confirmed: “штатный интерфейс работает” (the stock interface works). The exact
answer is retained in `owner-boot-confirmation.json` in the readback folder,
bound to the completed write and read sessions. This is owner-observed UI
acceptance, not an automated playback or native process test.

Physical USB enumeration, native health and stock protocol round trips remain
unverified. The earlier saved SD report describes the previous image and cannot
prove behavior of this update.

Acquisition code and payloads were unchanged. The prepared package's 284 Python,
eight JavaScript and native C tests, plus its packed boot/report integration,
remain the software validation baseline. This stage adds actual physical write,
full comparison and independent transport audits. Generated images, captures,
raw device data and audit scripts remain ignored; external repositories are
unchanged. No new browser or hosted GitHub Actions run is claimed.
