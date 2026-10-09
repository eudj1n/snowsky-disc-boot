# Firmware selection and update acceptance

New firmware must be investigated, tested and supported explicitly. A higher
version number does not imply protocol, boot-chain or writer compatibility.
Selection follows the reference project's profile approach; deployment metadata
belongs here, while RE addresses and emulator patches remain in the reference.

## Selection and stored state

`firmware/active-version` is the tracked default. `firmware/v<version>.json`
contains product/main/recovery identity, rootfs size/hash/chunk count, stock boot
file hashes, protocol identity, accepted integration scenarios, reviewed reference
revisions and a writer-profile selector. Only explicit profiles enable use.

The builder, offline reviewer and new emulator stacks accept `--version`.
Precedence is explicit option, then `FW_VERSION`, then the active-version file.
There is no runtime comparison with a particular release number. Unknown
versions, malformed profiles and unreviewed scenarios fail before execution.
Existing historical evidence and synthetic protocol fixtures retain their labels.

Each emulator stack records its selected version and canonical profile SHA-256.
Changing the default or the caller's environment does not retarget that stack.
Changing its profile requires a fresh stack. Legacy state without these fields
is rejected; it must be explicitly validated/migrated or recreated. Our existing
local stack was migrated after checking its stock `version.in` against the
selected profile; this is not an automatic fallback to the default.

Before a new stack starts, shared identity/rootfs fields must agree with the
selected `firmware/v<version>.json` in the reviewed reference revision. Guest
integration uses the container's explicit `FW_VERSION`; host checks use the
recorded stack pin. Memory probes receive the selected version and still rely
on the reference's binary-fingerprint guards.

`firmware/writers/<id>.json` separately pins the writer source/binary, SPL and
host wrapper source, as well as logical capacity, physical range and the reviewed
instruction layout. Different firmware can share a qualified writer profile.
Changing a writer algorithm/record format requires code review as well as data;
arbitrary profiles do not turn unknown executable formats into supported ones.

New image reports include canonical firmware and writer profile hashes. The
offline reviewer requires those exact profiles and verifies both image files.
Old reports without pins need a new build; editing an old report is not a new
pack/extract verification. Generated images contain no profile-controlled shell
programs; USB hook arguments are restricted to validated paths, controller tokens
and bounded integers. This configuration does not grant physical write authorization.

`firmware/usb/v<version>.json` separately admits the opt-in engineering image.
Its rootfs fingerprint must match the main profile; it pins inspected stock USB
inputs, card mount/source, UDC and time budgets. Missing profiles are not inherited
from another release. Image reports and the offline reviewer pin this profile too.

`firmware/os/v<version>.json` records OS-level sources observed on the player
for that release, starting with the battery fuel gauge: its sysfs path, the
reported `type`, the attributes present and those absent (`status`, `online`
on V2.57). The rootfs fingerprint must match the main profile, paths are
restricted to `/sys/class/power_supply/<name>` and a missing profile is not
inherited. The disposable guest applies it through
`scripts/runtime/battery_overlay.py` so OS-level readers see the player's
layout there too; later images take their sysfs/procfs arguments from it.

`firmware/probes/v<version>.json` admits the separate host CPU-info observation.
It pins the requested firmware/rootfs, reviewed protocol source, USB VID/PID and
bounded timeout. This is a host-side selection, not firmware detection from the
ROM reply. No arbitrary USB operation is configurable. A missing probe profile
is rejected; physical results and NAND compatibility are separate evidence.
Exact admitted reply byte strings are listed in `accepted_reply_hex`; no prefix
or padding fallback is inferred. Offline `rom_probe.py review` can reassess a
fingerprinted old result under a revised profile without another physical request
or rewriting the original result's status/profile pin.

`firmware/readers/v<version>.json` admits the experimental identity-only RAM
payload. It binds the firmware/rootfs to external SPL/source fingerprints,
uncached code/stack/request/result regions, poll budgets, clocks and ID address
phase. Missing profiles fail; geometry/ECC and arbitrary NAND commands cannot be
enabled through this profile. The offline builder records both profile hashes
and checks region bounds/overlap. Synthetic tests select a different release and
layout without production code changes. Physical qualification remains separate.

`firmware/transports/v<version>.json` binds the selected reader profile to the
reviewed SPL layout and bounded USB execution/transfer budgets. The offline plan
also pins the exact build and transport source files; changing mode, profile or
input changes its review hash. These fields do not admit arbitrary ROM commands,
NAND writes or an unreviewed SPL layout. See [RAM observation](ram-transport.md).

`firmware/installers/v<version>.json` binds firmware, metadata, transport and
writer policies to the complete image staging layout and budgets. The current
profile keeps physical write admission closed. Every new release must repeat
staging, boot-target, exact-image readback and recovery acceptance before write
admission is considered; copying a previous admission flag is not qualification.
The pinned writer's linked ABI is checked independently of the version selector.
The installer now also pins `installation_review_sha256`, binding a particular
reviewed unit's boot/stock/staging evidence, exact images, tool/source/profile
fingerprints and readback plans. The admission flag alone cannot enable writes.
A new unit/release/input requires fresh review and a new pin; previous authority
or device-state confirmation is not inherited. See [installation package](installation-review.md)
and [writer transport](writer-transport.md).

`firmware/boot/v<version>.json` pins the page policy, observed boot/OTA extents,
stock selector-script fingerprints and read budget. It never enables writes or
infers primary boot from malformed/erased selector bytes. Re-review the actual
bootloader consumer, selector and root arguments for each new release; a reused
OTA string is not boot-chain compatibility. See [boot evidence](boot-selection.md).

`firmware/bootloaders/v<version>.json` separately binds the reviewed captured SPL
image, acquisition policy, prefix mechanism, exact accepted selector tokens and
kernel/root argument mapping. The [offline checker](bootloader-review.md) validates
saved records against this review; it does not change the acquisition classifier,
authorize writing or assert a live mount. The complete current image pin is
capture-specific, not a guarantee that rootfs version identifies every installed
SPL. Re-establish image/mapping compatibility for a new unit or release before
reusing this assessment in installation review.

## Adding a release

Native deployment also uses `kernels/v<version>.json`: an independently reviewed
OTA kernel fingerprint, chip-table/DTB addresses and NAND metadata format, bound
to the selected rootfs and writer profile. Re-run the
[kernel and partition review](nand-kernel-review.md) for a new release. Passing
the offline checker never admits page reads or writes, and a previous physical
partition table must not be inferred from a new OTA kernel.

1. Inventory the official OTA in the reference repository. Record product,
   main/recovery version, chunk count, rootfs and binary fingerprints. Keep the
   previous profile and default intact while investigating the new release.
2. Inspect its boot sequence and required executables. Re-establish changed
   protocol behavior, addresses, listeners, USB ownership and power lifecycle.
   Add reviewed reference runtime support before attempting our emulator gate.
3. Add this project's matching profile with only reviewed acceptance scenarios
   and a justified writer choice. Copying old hashes, addresses or a capability
   list is not validation. Hardware qualification starts false.
4. Run firmware-free checks, then a fresh disposable stack explicitly selected
   with `--version`. Complete native read/stream/lifecycle/coexistence checks and
   relevant offline, power, idle and resource scenarios. Review catalog/handshake
   changes before enabling operations; keep mutations behind Controller guards.
5. Build and round-trip the companion-only image; verify its exact delta and
   stock restore artifact. Repeat writer/geometry and diagnostic-access review.
   Physical compatibility/install/restore acceptance remains distinct from QEMU.
6. Record evidence, actual supported scope and limitations. Promote by changing
   `firmware/active-version` after the applicable gates pass. Existing stacks
   remain pinned; future updates repeat this process.

## Reverse engineering

Reuse `research/ghidra/` in the reference repository, especially `FindText.java`
(strings/references), `RefsTo.java` (cross-references), `DecFuncs.java` and
`DecAt.java` (decompilation). Its README documents headless use and the separate
V2.57 analysis; older V2.40 function addresses are historical examples.

Import a fingerprint-verified binary into a separate ignored project for the
selected firmware. Preserve the binary hash, tool version, queried addresses
and observed call paths with each conclusion. Trace active callbacks rather
than assuming an unused function is reachable. Confirm important behavior in
the disposable runtime. Ghidra can resolve binary behavior; it cannot establish
the owner's NAND identity, bad-block map or successful physical recovery.

The [USB diagnostic investigation](usb-diagnostics.md) records stock card/USB
ownership and the vendor serial-number service in the fingerprinted V2.57 player.
No Ghidra-derived addresses are used by our runtime; new firmware needs renewed
analysis of changed paths before enabling the engineering profile.

## GitHub Actions and private integration

`.github/workflows/ci.yml` runs on each pull request, on `2.x` after each merge and by hand (not for a change of
documents alone), with
read-only repository permission and no firmware secrets. It builds the host C
service and runs JS plus all synthetic Python conformance tests, including
profile promotion, image/writer invariants, saved-result parsing and USB console
lifecycle. These tests use temporary synthetic files, PTYs and loopback servers, not a player or a sibling
checkout. Runner requirements: Python 3.11+, Node with `node:test`, C11 and make.

Firmware integration, image packing and Ghidra remain explicit local checks.
They are not enabled on arbitrary PRs and are not represented as hosted CI passes.
A future trusted/manual firmware workflow must provision the selected reviewed
reference and privately acquired OTA, use disposable volumes, and avoid uploading
firmware, private media or decrypted binaries as public artifacts.

Local evidence, including the subsequent USB stage, is recorded in
validation (snowsky-disc-web `docs/validation.md`). The full Linux host C/service suite has not been
run locally; the available emulator image lacks a host C compiler. MIPS USB
fixture tests and Python profile/deployment checks have run under Linux. The
workflow itself has not been executed by GitHub in this task.
