# Experimental NAND read core

This stage implements a freestanding C core and an offline host record decoder.
It has passed synthetic host and MIPS/QEMU tests. **It is not an uploadable RAM
loader, a qualified NAND controller driver or a physical backup tool.** A later
[identity-only SFC/RAM stage](nand-identity.md) adds an offline executable but
does not qualify physical page reads. No new USB
request, RAM execution or NAND access was performed while developing it.

## Scope and ownership

`device/acquisition/nand_reader.{c,h}` is separate from the companion and USB
console. It is not linked into either image variant. It accepts a trusted chip
policy, a request and a receive-only controller adapter. There is no MMIO, USB,
OS call, heap allocation or storage output in this core. The separate SFC adapter
implements identity transactions only; page reads still have only a fake adapter.

Two operations exist:

- `NR_IDENTIFY`: bounded readiness polling, two matching three-byte ID reads,
  then protection/configuration observations. It does not require known geometry
  and admits no page/cache read. All-zero/all-ones or inconsistent IDs fail.
- `NR_READ_PAGE`: additionally requires an exact expected ID, reviewed geometry
  and feature/ECC policy, then reads one explicitly addressed physical page plus
  its OOB bytes. There is no logical remapping, partition selection or bad-block
  skipping. An error never causes automatic retry or synthetic replacement data.

The only emitted NAND opcodes are `0x9f` (ID), `0x0f` (feature/status), `0x13`
(page to cache) and `0x0b` (cache read). The API has no data-out buffer. There is no
write-enable, program, erase, reset, protection unlock or feature-setting path.
Reading a page changes the chip's volatile cache; it does not program its array.
These command semantics are the selected experimental SPI-NAND protocol, not
support for every NAND family.

The trusted policy supplies page/OOB sizes, total page count, polling budget,
ID address phase, expected ID and feature/ECC admission. It cannot come from an
untrusted request. Bounds are at most 4,096 data bytes, 256 OOB bytes, 24-bit page
addressing and 10,000 status polls per readiness wait. There is **no real chip
profile yet**. The test ID `0x563412` and geometry are synthetic and must never be
used to admit the owner's NAND. Future firmware support requires explicit reviewed
chip/controller profiles; CPU `X2000` does not supply them.

Status is sampled before and after the cache read, and configuration must remain
unchanged. Unadmitted ECC values, feature mismatch or status drift fail. ECC
interpretation belongs to the reviewed chip policy: returned OOB is not labeled
raw merely because it accompanies page data. No attempt is made to change ECC
settings or remove protection to make a read succeed.

## Bounded execution and result contract

Each controller callback must itself be bounded and return the exact requested
byte count, or an error. The core cannot interrupt a blocked callback. Its own
loops and memory are bounded, but this is not yet a wall-clock guarantee for a
hardware driver. Short transfers and errors stop the current request immediately.

The experimental ABI is little-endian: a 48-byte request and a 4,428-byte result
(76-byte header plus 4,352-byte maximum payload). Requests contain a nonzero
128-bit nonce, sequence, operation and physical page. The future acquisition layer
must generate fresh nonces and save its original requests; test nonces are fixed.
Core buffers must be distinct, correctly sized and owned by the caller.

Results echo that context, preserve observed ID/protection/feature/status values
and counters, and publish a completion marker last. Only successful page reads
expose a nonzero valid-data length and CRC-32. Failed result buffers can contain
partially received bytes, but those bytes must never become a successful backup
page. The future RAM adapter must supply uncached-buffer/cache handling and a
hardware memory barrier; the existing compiler barrier alone is insufficient.

`scripts/deployment/nand_records.py` encodes requests and validates saved binary
results offline. It checks the complete fixed-size record, magic/version/done,
nonce/sequence/operation/page, successful result code, expected payload length,
CRC and unused trailing bytes. It never strips OOB. It rejects failed records
rather than publishing their partial contents. All raw records should still be
retained by a future acquisition layer for diagnosis.

Matching CRC and nonce are integrity/correlation checks, not device authentication
or physical chip qualification. The decoder explicitly returns
`hardware_qualified: false`; it does not decode vendor ECC policy independently
or mark an entire NAND backup complete.

## Build and tests

Host C assertions and Python record tests are included in the firmware-free CI
suite with no USB library, firmware, sibling checkout or root privileges:

```sh
bash scripts/test.sh
```

Build the separate MIPS test and freestanding object with the reviewed toolchain:

```sh
bash scripts/build.sh reader
```

Outputs are `build/mips/nand-reader-test` (Linux synthetic test executable) and
`build/mips/nand-reader-core.o` (relocatable object, **not executable/uploadable**).
The build rejects undefined external symbols in the core. Recorded object size:
3,408 bytes, ELF32 little-endian MIPS1/o32, soft-float, no external dependencies.

For a disposable QEMU container, set `DISC_NAND_TEST_BINARY` to its MIPS test path
and run `test_nand_records.py`. Its fixture is emitted by the actual C executable,
so Python validates compiler-produced records rather than only Python-generated
data. The fixture preserves every data/OOB byte and a CRC independently checked
with Python's `zlib`. Test coverage includes faults/short returns at every normal
transaction, busy polling before and after PAGE_READ, changing IDs/features/status,
all synthetic ECC states, wrong IDs, invalid bounds/requests and stale records.
The fake adapter asserts its opcode allowlist on every call, including failures.
Host AddressSanitizer/UndefinedBehaviorSanitizer and MIPS/QEMU checks pass.

## Reviewed inputs and next hardware work

Inspected diskOS revision `646212d57425bd437468ab8896f63b16bae8f744` supplies useful
references, not device acceptance:

| External input | SHA-256 |
| --- | --- |
| `flash/my_write5.c` | `08f31458fd63f6cc20d3ee7a878013da07f50e607f99a7b0f7f523bcfb074ec4` |
| `flash/disc_spl_lpddr3.bin` | `907f4c924d5d5a733e532cd07efec58f24baa566f104ab058f59686808579fb9` |
| `spl-src/uboot-xburst-lpddr3-src.tar.gz` | `999d3e906dcdd6a078f6eeed1d4646de23ee0d08d9cbd2c37ae77bc158f504f3` |

The SPL source documents DDR initialization and returning to ROM, with a private
board-parameter input; it does not establish memory reliability on this unit.
Its generic `sfc_register.h` and the writer's command-table implementation also
use different controller programming paths. A generic-header implementation is
not assumed equivalent to the writer's experimentally established path. The new
read core is original code; no SPL, writer binary or captured DDR values are copied
into this repository or linked into it.

Progress beyond this original core stage is recorded in the
[identity payload document](nand-identity.md). Before a physical reader can be proposed:

1. Implement and audit the X2000 SFC adapter against the actual command-table path,
   including pin/clock setup, finite FIFO/status waits and cleanup. Test register
   traces and injected failures, not only this transaction-level fake.
2. Implement/audit RAM entry and ROM return, stack/register preservation, buffer
   layout and cache discipline. Pin the external SPL/DDR inputs and validate
   initialization/DRAM readback in a separately authorized RAM-only step.
3. Package an identity-only first probe with bounded host transfers and immutable
   evidence. No page geometry should be guessed before observing the chip ID and
   reviewing its data/ECC/OOB policy.
4. After separate physical qualification, add full addressed acquisition,
   independent repeated reads, partition/boot-state interpretation and recovery
   acceptance from the [acquisition procedure](native-acquisition.md).

This core closes the synthetic read/record milestone only. Native installation
and the full NAND backup remain open in the project plan (snowsky-disc-web `docs/plan.md`).
