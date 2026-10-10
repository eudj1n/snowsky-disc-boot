# Boot-report candidate installation observation — 2026-09-24

The owner authorized discovery, one new candidate write and full readback, and
confirmed the same player/firmware. The exact [prepared update package](../installer/boot-report-installation.md)
was activated after the player appeared as the profiled Ingenic USB Boot target.
The regenerated writer and collector plans matched their approved fingerprints.
The one write completed; its first readback session stopped at the DDR guard.
The owner then confirmed a full power-off and new USB Boot entry for another
read-only session. Full comparison and normal boot acceptance are recorded below.
No report marker was provisioned and no SD report was exported in these sessions.

## Completed write

Ignored evidence: `work/boot-report-write-observation-001/`.

| Field | Value |
| --- | --- |
| Session | `9fb63006-96ad-4256-a3e0-e58fb58c6d62` |
| Nonce | `d1feb538be814ec78915ffbc3cffc746` |
| UTC start → finish | `2026-09-24T09:42:49.298777+00:00` → `2026-09-24T10:08:33.910725+00:00` |
| Approved writer plan | `82dc4c80e2cc07bf8af723f83e72c97fc00bc2777859d6a7e2090ec9d777ee20` |
| Candidate, 100,663,296 bytes | `18e628030998f4ec018f095d362e8f345a7a10136d32f3fbcce1339bb4d7a506` |
| Result SHA-256 | `996878feacf22d2fdf8662ac03e48e395c0d6f77e42c32ab6dc1146496f13046` |
| Journal SHA-256 | `f6ced378aac55597d7f99f2204bd032d49f1e270789af2db0ed4f2a21457bb42` |
| Independent audit SHA-256 | `518cd779518ec99e1af633016c3f666a8f17d295242a3431d0bf31f1a5c2a003` |

Fresh CPU/SPL/DDR, NAND ID/ECC and partition metadata checks passed. The metadata
hash remained `2e4ef4fa5c53dcaa725a1af3530a6af32c80078c091c9cb77020c6e12ed486b2`.
Both complete RAM patterns and the exact image/writer/guard comparisons passed
before one writer invocation. After the profiled wait, ROM responded and the
writer completion record reported 768 logical blocks, physical end 850, skipped
blocks 383/716 and no block retries. Cleanup reported no errors.

The independent saved-trace audit reconstructed every one of 27,844 calls,
4,645 incoming raw files (302,464,956 bytes), 4,638 bulk writes and the three
executions: SPL, metadata reader and writer. It used the approved proposed
installer profile snapshot; later closure of admission does not rewrite that
historical profile. Audit script SHA-256:
`ae08c827838fdeb377d824ab273e9712e57866e713309635d3377c3eb9d81070`.
The original write result retains `postwrite_verified=false`, `boot_verified=false`
and `flash_ready=false`; independent readback/boot evidence is separate.

## First readback stopped before NAND access

Ignored evidence: `work/boot-report-readback-observation-001/`.
Session `35aba2ee-e84f-46bc-b78e-a45abb3de6ce`, nonce
`423f55bcff3c47a3bddb1dac1f3297f7`, ran from
`2026-09-24T10:09:06.372627+00:00` to `2026-09-24T10:09:08.407244+00:00`.
It used approved collector plan
`eb1faaec76055a758f49fe319e6738e69c6edb70aa8e88f3992627449becc2dd`.

SPL bytes matched and its execution returned to ROM (`X2000` response), but its
20-byte diagnostic was `(0xd1a6c0de, 9, 30, 0x12, 1)`. In diskOS's corresponding
SPL patch `0003-ddr-instrument-DDR-bring-up-with-TCSM-breadcrumbs-bo.patch`, failure
ID 30 identifies the bounded `CALIB_DONE` poll; status low bits were 2 instead of
required 3. Stage 9 alone is not success when a failure ID/count is nonzero.
The underlying cause of this calibration failure is not established.

The existing guard rejected the run after exactly 12 USB calls. No DDR test,
reader payload/batch, NAND read, writer invocation or NAND write followed.
Both capture files were absent, and interface/library cleanup completed without
errors. No automatic retry/reset/restore was attempted. This is distinct from an
unknown writer outcome: the earlier writer had returned and passed its audit.

| Retained item | SHA-256 |
| --- | --- |
| Failed result | `62500a22164d936258f25683d3a7afd36b347c76be1d9cdd3441636d92857f6d` |
| Journal | `6c005f3cb9b860d44a05e66dbd4fd7918da4202492d809fb9013b5dad14fe96e` |
| Raw diagnostic | `86eb682f1e3e64e57d7bad050c83668ef7723735ee8c6297f55f3a38a0a55b9a` |
| Independent abort audit | `7ccc67fe5eb8010e36a5bf46af444f2735bab943981c0c7d79c30443af5fe704` |
| Abort audit script | `986fbab41102aa0f795ad4328c81e2896ef8523f515493eb0805f6c44a605ebb` |

A new firmware-free regression injects the same diagnostic and checks exactly
12 calls, one SPL execution, zero reader batches, retained raw diagnostic,
absent NAND capture files and successful cleanup. All ten collector conformance
tests pass on macOS and Linux (`work/boot-report-ddr-regression.log`,
`work/boot-report-ddr-regression-linux.log`). Acquisition code, payloads and the
approved read plan were not changed to bypass the guard.

## Readback after owner-confirmed power cycle

The owner confirmed a full power-off and a fresh USB Boot entry before the
separate read-only session in `work/boot-report-readback-observation-002/`.
Its DDR guard passed and collection started under the same approved plan.
All 794 batches and 50,816 records completed, and the USB session closed without
cleanup errors. The exact comparator rechecked request identity, ordering, CRC,
ECC and the good-block map against the independently approved padded image.
Every one of 100,663,296 bytes matched, including the zero padding. It did not
compare the capture against its own reconstructed output or use the stock FF-tail
exception. Physical mapping remains blocks 80 through 849, excluding 383/716.
ECC observations were 50,803 code 0 and 13 admitted corrected code 1, with no
other codes. These are per-observation counts, including repeated marker reads.

| Field | Value |
| --- | --- |
| Session | `14128b8a-33f6-4efa-a1ca-d364f876b430` |
| Nonce | `0744ffeadecd4b19a5bc7ead61d83f95` |
| UTC start → finish | `2026-09-24T10:12:39.242092+00:00` → `2026-09-24T10:50:21.541808+00:00` |
| Exact comparison plan | `003376655ad0f8879c1d54f56c3d80152d7c65e43d8772dfd0605adb077f7b92` |
| Result SHA-256 | `fa4dc2430ecab742be7aedb12d15e31c6947a340f74790b14e7f6e51c8b86fd3` |
| Journal SHA-256 | `1eecf833124ccb62f2e8b9aac301bbf53823aeb5b4a291a10725101fecca456f` |
| Records SHA-256 | `a6b273920ec45cc1051c38535fcf1f929cf6f0088c642c9cd886b40df2518197` |
| Exact comparison report SHA-256 | `8c1081865a646ace0fc77c33daed640155ae8f6a7aa2cb5b19e02f6f1bbf1f54` |
| Independent audit SHA-256 | `5e2bf7366a0b19254656e14e8070563f4fb0b149e17b3075b44fe79e238dbdac` |

The independent audit reconstructed all 42,220 calls, 9,553 incoming raw files
(453,235,160 bytes), 4,786 bulk RAM writes, one SPL and 794 reader executions.
There was no writer invocation in this session. Audit script SHA-256:
`100020237fb0cd3e97ccf45ba342b65bd2004fdd63eb24c04896b4ee9e9f688f`.
Reports are retained separately from original acquisition results; no historical
failure or qualification flag was overwritten. The clean cold-entry run does
not by itself prove why the preceding DDR calibration failed.

## Owner-confirmed normal boot

After full comparison and USB cleanup, the owner rebooted normally and reported:
“Да, загрузился и работает штатно” (loaded and operating normally). This observation
is retained in the successful readback folder as `owner-boot-confirmation.json`,
bound to the write and successful read sessions. It confirms the reported normal
UI boot, not automated playback, native process health or a live root mount.

## Remaining acceptance

Physical write admission is closed again after the one authorized write.
Exact readback and owner-observed normal boot passed. The subsequent separately
authorized [SD report](boot-report-observation.md) confirmed native process
startup, a loopback listener and primary-root boot arguments/mount. Native health,
stock protocol round trips and USB diagnostics remain unverified. The corrected
controller profile needs a new image and physical authorization; this installation
record continues to describe the image actually written.
