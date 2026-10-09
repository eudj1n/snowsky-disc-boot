# Authorized complete-image RAM staging — 2026-09-24

The separately authorized stage-only experiment passed on the owner's player.
Both full RAM pattern passes matched, followed by an exact comparison of the
entire 96 MiB engineering image and staged writer/guard/debug regions. No writer
entry was invoked and no NAND program or erase was performed. This qualifies
this bounded staging sequence on this unit, not physical installation or
long-term RAM stability.

## Scope and result

The owner authorized checking device availability and starting the concrete
[stage-only plan](../writer-transport.md#stage-only-sequence). Before opening USB,
the regenerated plan matched the previously reviewed plan byte for byte,
all input/source pins passed and the reviewed libusb digest matched. The host
had more than 1 GiB of free disk. One matching ROM device was opened; there was
no retry, reconnect, reset or additional physical acquisition.

- Session: `8341f797-0af9-44e7-8cce-a6e2725d1fc7`.
- Nonce: `a0f71e801cde41a39a217daac3a06505`.
- Plan SHA-256: `6a1410c13549e697846fb2201a9fc7fb6336e952bf7fefef141602d9a60cd20d`.
- UTC interval: `2026-09-24T05:50:14.327079` to `06:00:25.934011`.
- Duration: 611.61 seconds (10m11.61s), within the 900-second limit.
- Result: `writer-staging-verified`; all staging flags true and cleanup errors empty.
- Calls: 27,839, below the 27,892-call bound; three exact `X2000` replies.
- Execution requests: one SPL at `0xb2401800`, one metadata reader at `0xa0c00000`.
  There was no execution at the writer entry `0xa0c00030`.

SPL SRAM comparison passed before execution. DDR diagnostics reported final
stage 9 and zero failures. The metadata reader returned page 11 with matching
nonce/CRC, NAND ID `0b 12 00`, A0=`0x38`, B0=`0x10`, C0=`0x00`, four readiness
polls and twelve SFC transfers. Its main bytes matched the previously reviewed
partition page exactly, and the writer range remained inside primary rootfs.
This does not identify which rootfs the boot chain selects.

Every byte of the seven declared RAM regions was exercised, including the
100,663,296-byte image area `[0xa1000000, 0xa7000000)`. For each of two patterns,
all chunks were written before all chunks were compared. The second pattern
was the first one's complement. Final staging then wrote the pinned writer,
complete engineering candidate, nonce-derived guards and invalid completion
markers; every byte was read and compared again. The host closed USB normally.

## Independent offline review

An offline reconstruction checked all 27,839 journaled calls in order against
an independently generated expected sequence, all outgoing digests, and all
4,643 saved incoming transfers (302,463,927 bytes). It regenerated both pattern
passes from the session nonce, checked the complete image and padded writer,
and checked the result poison and remaining guards. It also checked metadata
framing/CRC/identity/ECC/counters, exact CPU replies, DDR diagnostics, transfer
lengths, timeout bounds, execution targets and the absence of additional calls.
This review performed no device access.

All evidence remains ignored under `work/writer-stage-observation-001/`.
The offline reconstruction is retained as `work/audit-writer-stage-001.py`;
its SHA-256 is
`7bdd1f4c4e2345d13e9bf5f71d3c17fe39688c32d111c3922b2b69f75714ee8f`.
No proprietary image, RAM readback or runtime log is committed.

| Artifact | SHA-256 |
| --- | --- |
| `result.json` | `e631dac452b657da8339a2324f00a1573084c105d27bad500cea171cbef945bb` |
| `transfers.jsonl` | `1d5810e07de508546d3a30ed460966a2cce6cc83dd1cef14bbda3c162cfd6c1c` |
| `offline-review.json` | `9f5b95f579df1d14154499e9af41da94fff16848c197e0ce02b580ec88f1d1f6` |
| Compared engineering image | `463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2` |
| `metadata-main.bin` | `2e4ef4fa5c53dcaa725a1af3530a6af32c80078c091c9cb77020c6e12ed486b2` |
| `writer-debug-poison.bin` | `405c8ef273cb9f8aae729daa5d830cf087b26b37b44ee16235df4b88d4ee4183` |

The unchanged transport had already passed 13 synthetic macOS/Linux tests and
the full 217-test Python, eight-test JavaScript and native C suite. This stage
adds actual hardware and saved-evidence validation; no transport behavior or
firmware admission flags changed.

## Acceptance boundary

On 2026-09-24 the owner confirmed normal stock boot and operation after leaving
USB Boot and rebooting. This is direct owner observation, separate from the
automated RAM trace. `writer_execution_attempted`, `writer_return_observed`,
`postwrite_verified`, `boot_verified` and `flash_ready` remain false in the saved
transport result. The owner confirmation does not rewrite those original flags.

Physical write admission remains closed. Active boot selection, writer/restore
execution, exact independent post-write readback and installed companion/stock
coexistence still require their own acceptance and concrete authorization.
The writer bytes were only staged in volatile RAM; the companion is not installed.
