# Offline X2000 NAND identity payload

This stage adds the SFC command-table adapter and an executable RAM payload for
identity observations. It has passed synthetic host/MIPS tests and offline ELF
inspection, followed by **one separately authorized physical identity run**.
SPL/DDR/reserved-RAM checks, identity SFC observations and the payload's entry/ROM
return succeeded on that unit. Page reads, chip geometry/ECC, stock cold boot and
installation remain unqualified. [Physical evidence](../usb-boot/ram-transport.md#successful-authorized-identity-observation).
The later [host transport](../usb-boot/ram-transport.md) is implemented and tested synthetically;
both modes now have separate physical evidence and still require authorization for new runs.
The payload is separate from both companion image variants.

## Relationship to diskOS

The intended installation route is the same: mask-ROM USB, the external diskOS
DDR-initializing SPL, RAM execution, then the reviewed rootfs writer. We do not
need a replacement writer. The difference is the image content (retain stock
UI/player and add the companion) and the additional pre-install observations.
The inspected diskOS installer proceeds from SPL directly to its NAND writer and
reads the writer's status buffer; that readback is not a NAND backup.

At diskOS revision `646212d57425bd437468ab8896f63b16bae8f744`,
`diskos_installer/imagebuild.py` lists `209`, `228`, `240` in `TESTED_FW`.
It knows the V2.57 input hash but requires an explicit untested-firmware override
for it. That is the state of this checkout, not a claim about all diskOS releases.

A full NAND backup was added to this project's proposed procedure by the agent;
the owner did not separately request that deliverable. It adds reader work and
must not be represented as an inherent requirement of the diskOS installer or
as a user-imposed gate. Its scope can be reconsidered before installation. This
identity milestone does not implement a full backup or change the write route.

The SFC register behavior was reviewed against the pinned `flash/my_write5.c`.
The new C/assembly implementation is original. External SPL, source archive and
writer source fingerprints are in the selected reader profile; the builder
verifies them without copying or linking those files. The SPL remains an external
GPL input with its source/provenance obligations; private DDR parameters are not
redistributed here. See [read-core provenance](nand-reader-core.md).

## Controller and memory contract

`sfc_x2000.c` admits only `0x9f` (three-byte ID) and `0x0f` (one-byte A0/B0/C0
feature/status). It programs only unchained command-table slots 1, 2 and 4, uses
PIO receive direction, disables DMA and never writes the FIFO. Page/cache reads,
reset, write-enable, feature changes, program and erase are rejected before MMIO.
Controller/pin/clock register writes are volatile hardware configuration, so this
is not a claim that a physical run would make no hardware changes.

The selected profile supplies poll limits, crystal/target clocks and ID address
phase. Unknown clock-source selections (2/3), initial clock busy state and invalid
divisors fail. Pinmux and the SFC clock gate are saved before changes. Transfer
waits and clock waits are finite; FIFO under/overflow, missing receive or END
fail without retry. One FIFO word is copied as exactly one or three bytes, never
four bytes into a short buffer. Poll counts are not a measured wall-clock deadline.

Cleanup stops/flushes SFC, clears status and attempts to restore the clock,
selected GPIO bits and SFC gate bit. A cleanup timeout produces `NR_IO`, even if
ID reads succeeded. **Previous SFC descriptors/configuration are not restored**;
stock cold-boot acceptance is still required. No NAND features are changed.

`identity_run` permits only `NR_IDENTIFY`. Core request validation runs before the
first controller initialization. The result remains incomplete during all MMIO
and cleanup. Its fixed-size bytes are published to uncached RAM, followed by MIPS
`sync`, the completion marker, then another `sync`. The host must still validate
the nonce/sequence/result and retain failed evidence; a transport ACK is not success.

The current reviewed layout (addresses are profile fields, not version checks):

| Region | Address / size |
| --- | --- |
| Code and entry | `0xa0c00000`, at most 32 KiB |
| Private stack | `0xa0c08000` to `0xa0c10000`, 32 KiB, grows downward |
| Result | `0xa0a02000`, 4,428 bytes |
| Request | `0xa0a04000`, 48 bytes |

All are uncached KSEG1 addresses within the explicitly admitted DRAM window;
profile validation rejects overlap, cached aliases, misalignment and overflow.
This validates a proposed layout, not the presence/reliability of physical RAM.
The assembly entry saves ROM `sp`, `ra`, `gp` and `fp`, reserves the o32 argument
area and calls C on its own stack; C preserves ABI callee-saved registers. It
restores the saved context and returns with `jr ra`. It does not initialize DDR,
alter CP0/interrupt configuration or reset caches. ROM call/return assumptions
must be qualified after using the pinned external SPL.

The linker rejects BSS/GOT and code overflow; no runtime/heap/libc is linked.
The builder rejects unresolved symbols, dynamic/interpreter headers, wrong
entry/endianness/machine, uninitialized load memory and ELF/raw-image mismatch.

## Build and verification

Firmware-free tests run automatically in `bash scripts/test.sh` and GitHub
Actions: fake MMIO assertions, request/publish lifecycle, profile/layout checks
and malformed ELF fixtures. They require neither USB nor external inputs.

For an offline RAM build, use a fresh ignored output directory:

```sh
python3 scripts/deployment/build_identity.py \
  --diskos /path/to/diskos --output build/identity-review
# Optional --version; otherwise FW_VERSION / firmware/active-version selects it.
bash scripts/build.sh reader
```

The first command uses the local toolchain image with networking disabled and
the repository mounted read-only. It records the image ID, source/profile pins,
ELF/raw hashes, linker map, disassembly and compiler stack-usage files. The second
builds Linux MIPS synthetic tests, including `sfc-identity-test`; those tests use
fake MMIO, not physical peripherals. The bare RAM entry itself cannot be tested
by running it as a Linux userspace executable.

Recorded build: 4,728-byte ELF load image, entry `0xa0c00000`, MIPS32/o32,
soft-float, empty undefined-symbol list. Disassembly confirms entry/return
preservation, uncached request/result addresses and `sync`. Summing every
reported static C frame plus the 32-byte entry frame gives less than 6 KiB;
the call graph is nonrecursive and the reserved stack is 32 KiB. This is offline
stack analysis, not a hardware high-water measurement.

Synthetic coverage includes all disallowed opcodes, command-table/address/data
direction, short-buffer canaries, finite missing-RX/END waits, FIFO errors,
clock-init/cleanup failures, restored GPIO/gate state, invalid requests causing
zero register writes and completion withheld until cleanup. Host ASan/UBSan and
MIPS/QEMU fake-controller execution pass. There is no new browser behavior to test.

The [bounded host transport](../usb-boot/ram-transport.md) now supplies the concrete
SPL/DRAM/identity sequence. Physical execution is still a separately authorized
step. Do not infer NAND geometry/ECC or installation acceptance from this build.
