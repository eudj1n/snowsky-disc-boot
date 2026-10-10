# Authorized candidate installation observation — 2026-09-24

One physical candidate write and independent exact 96 MiB readback passed.
The owner confirmed normal stock UI/operation after the first reboot. Native
companion process and diagnostic-access acceptance remain outstanding.

The owner authorized activation of the exact prepared installer profile, one
candidate write and, only after confirmed writer return, the independent full
readback. The owner confirmed the same player and unchanged firmware since the
accepted observations. This authorization does not include stock restoration,
SD diagnostic-marker provisioning or a diagnostic console connection.

## Admission and candidate write

The prepared profile was applied byte for byte; its only change is
`physical_write_admitted: false -> true`. The pinned installation review and
all other fields remain unchanged. Candidate write, full collection and exact
comparison plans reproduced the [approved package](../installer/installation-review.md)
exactly before USB access. The reviewed libusb matched and 50.5 GiB was free.
Admission is an engineering configuration; it does not authorize future writes.

The player was discovered in USB Boot. CPU_INFO, SPL/DDR, fresh NAND metadata,
both complete RAM patterns and the entire candidate/writer/guard comparison
passed. The metadata page hash remained
`2e4ef4fa5c53dcaa725a1af3530a6af32c80078c091c9cb77020c6e12ed486b2`.
The host issued exactly one writer invocation, observed the configured
15-minute wait, received the ROM CPU response and checked the completion record.

| Observation | Value |
| --- | --- |
| Ignored capture | `work/candidate-write-observation-001/` |
| Session | `cc617852-4ab8-4553-8eab-bc42ded2a3ee` |
| Nonce | `363931f2ea304f20901c7bfac6163d47` |
| UTC interval / elapsed | `07:39:39.742747`–`08:05:26.479005` / 25m46.736s |
| Status | `writer-completion-observed`; ROM return observed |
| Logical blocks / bytes | 768 / 100,663,296 |
| Physical range used | Blocks 80–849, skipping 383 and 716 |
| Retry counters | Zero retried blocks, zero worst-tries counter |
| Cleanup | No errors |
| Candidate SHA-256 | `463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2` |
| Result SHA-256 | `69f23a3d746f401c946ca04699959f3f945b145a5650ce7ddc42af2bf5917237` |
| Journal SHA-256 | `ad6cb37eaa1c62a039fde72a9ad03d192de8c49879d3048627cdc864b114f970` |

An independent offline reconstruction checked all 27,844 protocol calls, 4,645
saved reads (302,464,956 bytes), outgoing hashes, complete RAM comparisons,
metadata framing/CRC and writer completion fields. Its only execution targets
were the reviewed SPL, metadata reader and one writer. The saved audit is
`offline-review.json`, SHA-256
`fb8c6f48f8f69fb21706a0bd1804191820800ea2cb932c5149c81719e2c2883a`;
its script hash is
`0f1c95eb684d610cbbdeaf7570b92235ede831a6c3eb901a88abe858a6537cb0`.
No host retry, reset or restore occurred.

## Independent readback and acceptance boundary

After confirmed return and clean session closure, the already authorized
independent full collector was started under the exact approved collection plan.
All 100,663,296 bytes match the exact approved padded candidate. The strict
verifier used the prepared candidate comparison plan
`91361d910798643a2a33366f12d4663d45faa39711f4c27cc0a5d75836751715`;
no original-stock FF-tail exception was used.

| Observation | Value |
| --- | --- |
| Ignored capture | `work/candidate-readback-observation-001/` |
| Session | `1816ca16-a56c-4d29-8e7f-0682c7f7d43e` |
| Nonce | `82152be4356c44bba8afa356686c9bba` |
| UTC interval / elapsed | `08:06:00.045327`–`08:43:49.019305` / 37m48.974s |
| Collector status | `rootfs-collected`, no cleanup errors |
| Exact comparison | `saved-logical-readback-matches` |
| Batches / records | 794 / 50,816 |
| Bad blocks | 383 and 716; same observed map as before writing |
| ECC histogram | 50,805 records with code 0; 11 with admitted corrected code 1; no other codes |
| Result SHA-256 | `f35124d1c4a3d3471a7eccefe36005f1ac204ab38936753a7033d8b18cb7f518` |
| Journal SHA-256 | `5c39cadfa93d57523987a3f9800fe54fcf16ebe56b18b36d3750bf22fd3fad6a` |
| Records SHA-256 | `23ecca5dff20ad2279483735dd78f0a6845984cc77873eadef543e635a610b93` |
| Exact comparison report SHA-256 | `d4dfb11be56c25b638e854dc9c144880846f7004c75f0f9552511c04517e86cf` |
| Independent audit SHA-256 | `791e5527908fd03809b16d80daef41de3471c7add723acc96334b1f9f53fd6a2` |

The exact verifier checked request nonce/sequence, record framing/CRC, chip
configuration, ECC, paired markers, all 768 logical-to-physical mappings and
every main-data byte. Its independent expected image was the approved candidate,
not the collector's own output. An additional offline reconstruction checked
all 42,220 protocol calls, 9,553 raw reads (453,235,160 bytes), every outgoing
request/payload digest and the relationship between raw batch replies, records
and the saved image. It found one SPL execution and 794 reader executions, no
writer execution or NAND writes in the readback session. The audit script hash is
`8e1100e5f40bf0d7ffa6008de3121b31e53b7bc60007e9e5da648b3ff7f6105a`.

Both physical sessions ended and closed USB before requesting a normal reboot.
On 2026-09-24 the owner then confirmed that the player booted and operates
normally. This is direct owner observation of the stock interface, not automated
playback, cold-boot, process or mount evidence. The static SPL target assessment
plus exact NAND contents do not prove which root is running or that the companion
process has started.

The original transport result retains `postwrite_verified`, `boot_verified` and
`flash_ready` as false. Later evidence belongs in separate reports and must not
rewrite those original flags. Native process, live root mount, local playback,
USB diagnostics and physical restoration remain separate acceptance steps.

## Validation

The 13 writer transport tests and 11 installation-review tests passed again
with the activated profile. These synthetic tests use their own admission and
evidence fixtures and require no physical player or proprietary firmware.
Ignored logs: `work/candidate-write-transport-tests.log` and
`work/candidate-installation-review-tests.log`. The previous full suite and
focused Linux checks are recorded in the installation package review.

No production script, firmware payload source or external repository changed.
Captures, audit scripts, images and generated plans remain ignored.
