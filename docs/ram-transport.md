# Bounded ROM RAM transport

`scripts/deployment/ram_transport.py` implements an offline plan and three explicitly
selected physical observation modes. Synthetic host/Linux tests pass. A separately
authorized `ram-check`, `identity` and single-page `metadata` observations now
passed on the owner's unit. **Full RAM stability, general NAND/ECC qualification and installation remain
unqualified.** This is not an installer or full NAND
reader; no erase/program path, stock boot hook or player image is changed here.

## Separate scopes

`ram-check` is the first proposed physical step:

1. Require exactly one profiled ROM USB device, record its port path, open it and
   exclusively claim interface 0. Check the exact profiled CPU signature.
2. Upload the fingerprinted external diskOS SPL to `0xb2401000`; read back and
   compare all 8,056 bytes before executing it once at `0xb2401800`.
3. Wait the profile's settle interval, check CPU response again, and read the
   five-word DDR diagnostic at `0xb24017c0`. Require magic `0xd1a6c0de`, completed
   stage 9, and zero first-failure/status/failure-count fields.
4. Write distinct nonce/address-derived patterns to **all** reserved code, stack,
   request and result regions, then read and compare **all** regions. Perform a
   second explicitly planned pass with complementary patterns. Writing all regions
   before reading any catches aliases between those regions.
5. Release the interface and close USB. Stop here; do not load/execute the identity
   code or automatically enter another mode.

The current reader profile reserves 32,768 + 32,768 + 48 + 4,428 = 70,012 bytes of
DRAM for this check. This is a short test of those regions, not a whole-memory
test, retention/temperature qualification or proof of future NAND write reliability.

`identity` repeats the SPL and RAM checks, then uploads/compares the reviewed
identity payload, a fresh nonce-bearing request and an incomplete zero result.
It executes the payload once, waits, checks the CPU response, reads the full
4,428-byte result and validates completion, request identity, error code, padding
and plausible identity/counter/status fields. Only the payload's `0x9f` and `0x0f`
NAND observations are admitted. There is no geometry/ECC admission or page read.
Approval of `ram-check` does **not** approve this second mode.

The separate `metadata` mode repeats the RAM checks and executes a payload that
can read only the profiled metadata page, with ID/ECC/OTP admission and retained
main/OOB data. One separately authorized physical observation now passed with
ECC status zero and decoded partition extents. See [scope, tests and evidence](nand-metadata.md). Earlier RAM-check
or identity authorization does not cover this new operation.

All modes change volatile state: SPL configures clocks, GPIO, DDR and watchdog;
RAM contents are overwritten. No automatic reset, reboot or restoration of that
volatile environment is attempted. Return to stock boot must be observed after a
manual power cycle. A USB control success alone does not establish SPL completion
or payload completion; diagnostics, readbacks and the result contract are separate
checks. An execution timeout is an uncertain outcome, never permission to retry.

## Reviewed protocol and profiles

The wire sequence was reviewed against diskOS revision
`646212d57425bd437468ab8896f63b16bae8f744`, `src/usbboot/usbboot.c`, and the pinned
SPL source archive's `xburst2/x2000/soc.c`, `start.S` and `xburst2/ddr_innophy.c`.
The `CONFIG_BURNER` path returns after DDR setup; its diagnostics distinguish
reaching the final stage from passing every poll. The original USB source is
fingerprinted by the existing CPU probe profile. No installer/helper is executed.

Only these ROM vendor operations exist in the transport:

| Request | Direction | Purpose |
| --- | --- | --- |
| 0 | `0xc0`, 8 requested bytes | Exact profiled CPU reply |
| 1 | `0x40`, no data | RAM transfer address, high/low halves in value/index |
| 2 | `0x40`, no data | RAM transfer length for both OUT and IN |
| 4 | `0x40`, no data | Execute the pinned SPL or selected reviewed payload entry |

Bulk OUT is endpoint `0x01`; bulk IN is `0x81`. Both writes and reads set address
and length before the corresponding bulk transfer. There is no cache-flush/start2, cloner,
NAND writer, detach, configuration change, alternate setting or arbitrary CLI
address/command argument. No automatic reconnect or target fallback occurs.

`firmware/transports/v<version>.json` binds firmware/rootfs and the exact reader
profile to SPL size/layout and finite time budgets. The admitted SPL protocol
has a linked TCSM layout; it cannot be freely relocated by editing a number.
These silicon/protocol constraints are not release-number checks. Firmware
selection follows explicit `--version`, `FW_VERSION`, then the active profile.
Every new release requires reviewed profiles and renewed physical acceptance.

Before loading libusb, preparation verifies the build report, current source
set/hashes, every payload artifact, ELF/raw agreement, external SPL/source pins
and the USB reference hash. It retains the exact SPL/payload bytes in memory for
the run. Modified payload/build sources require a new build; transport/profile
changes require a new plan review.
The external SPL and captured parameters stay external; see its GPL/source and
private-input boundaries in [identity provenance](nand-identity.md).

## Bounds, ownership and evidence

Each transfer is a single libusb call, at most 65,536 bulk bytes, with a positive
timeout capped by the remaining monotonic session budget. Defaults: control
1,000 ms, bulk 3,000 ms, execution control 5,000 ms, settle 2,000 ms and transfer
session budget 60,000 ms. At most 256 protocol calls may be journaled. OS/library
loading, enumeration/open/claim/release and filesystem operations do not have a
hard interruptible wall-clock guarantee from this synchronous API.

A batch of the rootfs collector (the backup, the readback, the read by digest)
no longer sleeps the settle after its execution (plan, stage 4c): the ROM does
not answer USB while a payload runs, so the host asks it for the CPU reply every
`completion_poll_ms` (50 ms, each request with that timeout) until it answers,
at most `settle_ms` in all. A timeout of such a request is the batch still
running, the only negative return that continues, and it carries no data; any
other error, or no answer within the settle, stops the session as before. The
plan admits up to `settle_ms / completion_poll_ms` requests a batch, the journal
marks each one (`poll: true`), the readback's audit accepts timeouts and then one
exact answer within that bound, and the result keeps how long the batches took
(`batch_ready_ms`). The SPL's execution and the writer keep their fixed waits.
`completion_poll_ms: 0` is the fixed settle as before.

Negative returns, zero progress, successful short transfers, excessive counts,
comparison failures or deadline expiry stop the sequence. Even an error with
partial bytes never causes continuation or replay. After an attempted execution,
failure does not prove that code did not run. Cleanup is attempted once; release
errors are retained and prevent a successful overall status.

Acquisition requires the exact SHA-256 of the reviewed offline plan. The hash
binds mode, inputs, profiles, code provenance, RAM regions and timeouts; it is an
accidental-change guard, **not a replacement for the owner's authorization**.
The CLI reserves a fresh private evidence directory before loading the explicitly
selected trusted libusb library. Existing directories cannot be replaced.

Evidence includes `request.json`, `dependency.json`, `transfers.jsonl`, binary
readbacks, `result.json`, and the identity request in identity mode. The session
UUID and fresh nonce are retained before device access. Journal attempts are
flushed/fsynced before each control/bulk call; returns retain codes, actual counts,
hashes and partial IN bytes. Interruption may leave an attempted call without a
return record, which means uncertain completion. No report claims unique device
authentication, full RAM/NAND qualification or readiness to flash.

## Offline review commands

Use the existing reviewed build or create a fresh one as described in
[identity payload builds](nand-identity.md). These commands never load libusb:

```sh
python3 scripts/deployment/ram_transport.py plan --mode ram-check \
  --build build/identity-final --diskos /path/to/diskos
python3 scripts/deployment/ram_transport.py plan --mode identity \
  --build build/identity-final --diskos /path/to/diskos
```

Only after separate authorization of the chosen mode, with the player explicitly
connected in mask-ROM, use the corresponding `plan_sha256`:

```sh
python3 scripts/deployment/ram_transport.py acquire --mode ram-check \
  --build build/identity-final --diskos /path/to/diskos \
  --libusb /absolute/path/to/trusted/libusb \
  --approved-plan-sha256 REVIEWED_PLAN_SHA256 \
  --output work/ram-observation-001
```

Do not reuse a prior CPU-info authorization for this step. Do not rerun an
uncertain acquisition automatically. Inspect its saved trace and confirm the
physical device's state before proposing another action.

## Synthetic verification

`test_ram_transport.py` is included in the firmware-free GitHub Actions suite.
It models libusb's C ABI and ROM address/length/memory behavior, including an
aliasing memory fault. It exercises every protocol failure boundary, successful
short/zero bulk returns, partial bytes on error, mismatched SRAM/DDR/RAM evidence,
stale/incomplete/error identity results, deadlines, ownership/release failures,
journal failures and fresh-output/plan guards. Synthetic input tests reject
modified source/SPL/artifacts, changed profiles, symlinks and corrupt ELF files.
Tests select a future firmware without production code changes. Host/macOS and
Linux tests pass; neither fake ROM execution nor QEMU qualifies physical USB.

The service/browser and player images are unchanged, so this transport stage
does not need another browser or stock-firmware integration run. The following
sections retain the original physical RAM/identity observations. The subsequent
successful metadata experiment is recorded in the evidence linked above.

## First authorized attempt

On 2026-09-24 at 01:25:51 UTC, the owner authorized one `ram-check` invocation
under plan `7b16d160514fc21a8c00f75080acf32dc8b8fcb37b17324e056dfe2ec4aab8bb`.
Offline preparation passed, but USB discovery found zero matching `a108:eaef`
devices. The run stopped before opening/claiming a target or issuing CPU-info,
address, bulk or execution requests. No SPL upload, RAM test or NAND access
occurred; no retry was made. The observation does not establish whether the
player is disconnected, in another mode, or otherwise unavailable to libusb.

Private evidence is in `work/ram-observation-001/`, session
`0f979917-9d31-49be-8e5f-e3ea569009ff`. Result SHA-256:
`9940909c9378909f9fcfeedde5f7e425e9c36221ace9690e9554164f19ded1ad`.
The journal contains only `usb-discovery-attempt`; cleanup reported no errors.
`physical_device_accessed: null` reflects discovery without an opened target,
not a claim that libusb initialization performed no USB activity. The retained
plan's nested `physical_device_accessed: false` describes the offline plan.

Reconnect the intended player in mask-ROM and explicitly request another run
before repeating acquisition. The firmware/profiles/payload/transport remain
unchanged, and all physical qualification gates remain open.

## Second authorized attempt and download-length correction

The owner confirmed the first attempt's player was disconnected and separately
authorized another run. At 2026-09-24 01:27:15 UTC, the original plan opened and
claimed the single matching ROM device. CPU-info returned exactly `X2000`.
SPL bulk OUT and SRAM bulk IN each reported all 8,056 bytes, but 7,996 bytes of
the readback differed from the pinned file. The transport stopped before any
request 4: **neither SPL nor identity code was executed**. DDR diagnostics, RAM
round trips and NAND observations were never reached. Cleanup reported no errors.

Private evidence: `work/ram-observation-002/`, session
`272d982b-0c05-41c2-bc9d-dff5e28818e1`. Result SHA-256:
`e48cc943b45ea1d07b760c053b0be5b62060a63d23920c3561ec89470c31befc`.
`offline-review.json` records the saved-byte comparison without new USB activity.
The SRAM readback SHA-256 is
`89e95e6dc2e0ac3d53310e1844ddd254efce5f1f0fe133b359b18a7132dea365`.
The USB return counts alone do not establish that the SPL was stored correctly.

Offline inspection found a bug in our `Session.write`: it set the address but
omitted `VR_SET_DATA_LENGTH` before bulk OUT. The pinned diskOS source's
`jz_download` explicitly sets the file length before calling `bulk_transfer_out`.
The corrected transport now does the same for **every** download. The fake ROM
now requires the programmed length on OUT as well as IN; new tests check the
address/length/bulk ordering for all twelve downloads in identity mode and reject
the old sequence. Earlier tests checked the length only for IN and missed this.

This is a confirmed protocol omission, but the correction's effect on the owner's
device has not yet been observed. No automatic retry or verification bypass was
used. Original evidence/plans remain unchanged. Updated offline plans are
`work/ram-check-plan-002.json` (review hash
`68bfc3abedd04374fcdffe59cecb2df106104404c9115bf3fa8d3b1126871715`) and
`work/ram-identity-plan-002.json` (review hash
`45d0ad0cf93284e65c1c7b6a4964a5cf6756332c032a1f29612660031bf5d90c`).
A further device run requires separate authorization of the corrected sequence.
The subsequently authorized RAM-check is recorded below; this failed observation
and its original trace remain unchanged.

## Successful authorized RAM-check

After explicit authorization of the corrected plan, the single invocation on
2026-09-24 ran from 01:31:37.599885 to 01:31:39.962470 UTC (about 2.36 seconds).
Session `f84de23c-5d8c-4995-90f6-67f5872b88af`, evidence directory
`work/ram-observation-003/`, result SHA-256:
`24d7773d3afd8dc97be34d251df392faac1766e24df6c1380ae4d22d650193c8`.

- One profiled ROM endpoint was opened/claimed; CPU replied exactly `X2000`.
- All 8,056 uploaded SPL bytes matched the SRAM readback before execution.
- Exactly one execution request targeted the SPL entry. A subsequent CPU reply
  again matched `X2000`, demonstrating a responding ROM after SPL execution.
- DDR diagnostics reported magic `0xd1a6c0de`, final stage 9 and zero failure
  fields. This is the SPL's completion report, supplemented by the RAM checks.
- All four reserved RAM regions (70,012 bytes total) matched on both planned
  pattern passes, including the complementary second pass.
- Interface release and USB cleanup reported no errors. No identity execution,
  NAND command, automatic retry, reboot or firmware write occurred.

The trace contains 60 paired protocol attempts/returns. A separate offline
review recomputed all saved readback hashes, reconstructed the nonce/address
patterns and complements, compared every byte, checked both CPU replies and the
DDR diagnostic, and verified the sole execution address. Its report is
`offline-review.json`; it performed no additional USB access.

This confirms the download-length fix for the observed sequence and advances
the narrow SPL/DDR/reserved-region gate. It does not qualify the identity
payload's return shim/SFC adapter, every DRAM address, retention, cold stock boot,
NAND geometry/ECC or installation/recovery. Profile and result fields deliberately
retain `hardware_qualified: false` / `flash_ready: false`. The player was left
responding in ROM after volatile SPL/RAM changes; no return to stock boot was observed.

## Successful authorized identity observation

The owner separately authorized one `identity` run under corrected plan
`45d0ad0cf93284e65c1c7b6a4964a5cf6756332c032a1f29612660031bf5d90c`.
It completed on 2026-09-24 from 01:34:47.011246 to 01:34:51.381143 UTC (about
4.37 seconds), session `a84d5658-b286-4937-a9de-d1e4ba28f386`. Evidence directory:
`work/identity-observation-001/`. Result SHA-256:
`437ec64fde27c0f54651221882a6a91c446d93437899e5717f8405b8bf1d907a`.

SPL SRAM readback, clean stage-9 DDR diagnostics and both 70,012-byte RAM passes
succeeded again. Payload, request and incomplete-result uploads were compared
before the single identity entry invocation. A subsequent exact `X2000` reply
and a complete nonce/sequence-bound result were received after execution.

| Observed field | Value |
| --- | --- |
| ID, wire byte order | `0b 12 00` |
| Packed little-endian ID | `0x00120b` |
| Feature address A0 | `0x38` |
| Feature address B0 | `0x10` |
| Status address C0 | `0x00` |
| Core result | `NR_OK`, completion marker present |
| Readiness polls / NAND transactions | 1 / 5 |

The payload reports that its two ID reads matched. The host retains the combined
result record, not separate logic-analyzer captures of the NAND bus. These raw
register values are observations; vendor-specific protection/ECC interpretation
and chip geometry are not inferred or admitted by this stage.

All 83 USB protocol attempts have matching returns. The independent offline
review checks the three CPU replies, two exact execution targets (SPL and
identity), upload hashes, all fourteen bulk readbacks, DDR diagnostics, both
pattern passes, the zero pre-execution result and the final decoded request-bound
record. The raw final record's SHA-256 is
`7ee51a155fe28ce32d960fdc582d84213dbb4eaf449b2dca0d620a0d1284a3e4`.
Cleanup reported no errors. No page/cache read, NAND program/erase/reset/feature
write, firmware installation or automatic retry occurred.

This advances the narrow identity SFC/entry/return gate on this unit. The RAM
payload, chip observation and ROM return are now physically demonstrated; the
native Linux companion is **not** installed on the physical player yet.
`hardware_qualified` and `flash_ready` remain false. Next work is offline review
of the observed chip's documented geometry/ECC/OOB and recovery implications;
any subsequent physical operation needs its own concrete authorization. Stock
cold-boot return had not been observed at that point. After the subsequent metadata
read, the owner confirmed normal stock boot following the requested manual power
cycle; see [the recorded observation](nand-metadata.md#owner-confirmed-return-to-stock-boot).
General installation/recovery and post-installation cold boot remain outstanding.
