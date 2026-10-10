# Concrete installation package — 2026-09-24

This page records preparation before authorization. The owner subsequently
authorized the exact activation, one candidate write and conditional independent
readback. Current physical results and remaining acceptance are recorded in the
[candidate installation observation](observations/candidate-installation-observation.md).
The later [boot-report image](boot-report.md) changes code/profile/image inputs;
this historical package is not current for it, and admission is closed again.
The replacement [boot-report installation package](boot-report-installation.md)
accounts explicitly for the installed candidate and its exact readback.

The offline package binds the reviewed primary boot target, original stock
content, successful complete-image RAM staging, exact candidate/restore files
and independent post-write readback plans. All actual inputs passed again.
No physical operation occurred. The tracked installer profile remains closed:
**separate authorization is still needed before applying the prepared admission
profile and executing the exact candidate plan**.

## Evidence binding and execution guard

`scripts/deployment/installation_review.py` generates an ignored package with:

- An installation review containing fingerprints of the three saved physical
  sessions, their requests/results, journals, independent audits and captures.
- A fresh assessment of the complete boot/OTA records against the reviewed SPL.
- A fresh comparison of all stock records against the official rootfs and the
  reviewed FF tail. The original strict restore-padding rejection is retained.
- Verification that the previously exercised RAM layout, SPL, metadata reader,
  writer and complete candidate bytes match the current installation inputs.
- Current firmware/chip/reader/transport/bootloader/USB/pre-install/collector
  profiles, new build manifests, reviewed libusb, and all deployment Python
  sources. No historical source manifest is rewritten to look current.
- Separate candidate and restore write plans, a full collector plan, and separate
  **exact padded-image** comparison plans for both targets.

The installer profile pins the canonical review hash. Write planning now requires
that exact review, matching current profiles/source/build/metadata/image inputs
and the checked full-readback build. Setting the admission boolean alone fails.
Write acquisition also requires the exact approved plan hash, matching libusb,
and explicit confirmation of the reviewed device state. These checks precede
output creation, USB-library loading and device discovery. Stage mode cannot
execute the writer and does not require installation evidence.

The evidence hash is an engineering review anchor, not a signature, authorization,
unique device identity or proof that the player has not changed since capture.
ROM CPU_INFO does not distinguish otherwise identical players. The operator must
confirm the same physical unit and account for intervening actions:

- Candidate: no OTA, bootloader, kernel, partition, selector or rootfs changes
  since the accepted observations; prior attempts must not leave a writer active
  or its outcome unknown.
- Restore: the same bootloader/kernel/partition/selector context, with previous
  separately authorized rootfs writes accounted for. A known candidate write is
  allowed in this history; the original stock rootfs is not claimed to still be
  present. An unresolved writer invocation still stops all new bootstrap/write
  actions until its state is separately resolved.

The `--confirm-reviewed-device-state` flag records that operator confirmation;
it does not inspect hardware or turn an unknown completion into success.
The original capture reports and the bundle keep freshness/live-boot/readiness
claims false. Historical accepted data alone never authorizes an action.

## Exact prepared inputs and bounds

Package: `work/installation-package-002/`. Package 001 is superseded by final
state-confirmation wording and profile/source binding; do not use its plans.

| Item | Value |
| --- | --- |
| Canonical installation review SHA-256 | `ce9620f4c18b48eb3646da43be9242816e24f84dd6cff7b7b997b47167c5cb1e` |
| Proposed admission profile file SHA-256 | `c285bb733fb75420c4173cdbc4096bc28a48acc82c22c21d45e52871521464ae` |
| Review file SHA-256 | `5ca66efdb3025555b2df6c1c632422f9cb0341ab8d87be61b397218f1811e75e` |
| Candidate, 100,663,296 bytes | `463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2` |
| Stock restore, 100,663,296 bytes | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |
| libusb | `d4d61d9f4e5291e64c09783cb61bd1b4c67e202b514f40989504a5852f7560b9` |

The candidate is the existing USB engineering image with the companion and
opt-in diagnostic supervisor/hook. This proposal does not provision an SD marker,
change OTA selection, replace bootloader/kernel/recovery, or write userdata/MAC.
The write is 768 logical blocks (96 MiB), starting at physical block 80, scanning
at most through block 911 to skip bad blocks. Its physical interval is
`[0x00a00000, 0x07200000)`, entirely within primary rootfs. Bad blocks are read
afresh; the previously observed 383/716 are not hard-coded skips.

Each write session repeats SPL/DDR checks, fresh NAND ID/ECC/partition comparison,
both full RAM patterns and exact complete-image/writer/guard comparisons before
one writer invocation. Limits: 27,892 protocol calls, 15 minutes for staging,
15 minutes of writer settling, 30 minutes total. A writer completion record is
only a completion observation; readback and boot acceptance remain separate.
No host retries, automatic reconnect, automatic reset or automatic restore.

After confirmed writer return, the independent read uses 794 batches and 50,816
records for the full logical 96 MiB, with a 60-minute session budget. Its exact
comparator covers every approved padded byte. It cannot use the original stock
FF-tail rule to accept a candidate or restoration result. Keep at least 2 GiB
free for the combined write/readback logs and captures. Previous timings suggest
roughly an hour for write plus readback; the combined configured maximum is
90 minutes. Keep power/USB stable and do not interrupt an executing writer.

| Prepared plan | Canonical SHA-256 |
| --- | --- |
| Candidate write | `1a7cdacc24cc50961469ae108052e8a508027c60a1bb43b11e0767470f881730` |
| Restore write — separate authorization | `6b9a42b31d24e7c24c3da6f09e8ce129681f35d19ec4d984b2bc900bc0d7982e` |
| Full post-write collection | `c53874fc2eb8c284f492e512fb3ba193d68e9c31e0e8ad255c67fd000e1cfe2b` |
| Exact candidate comparison | `91361d910798643a2a33366f12d4663d45faa39711f4c27cc0a5d75836751715` |
| Exact restore comparison | `b62d2bdb7802916b9a007afbc0b58909d9f7140987102ddaf3e3a611784bf029` |

These write plans use the prepared `proposed-installer-profile.json` with admission
true and the exact review pin. They are not executable under the current closed
profile. The proposed profile changes only `physical_write_admitted` from the
current checked-in, review-pinned profile. After separately authorized activation,
regeneration must reproduce the exact approved plan before any physical call.
A different pin, input or code change requires a new review, not substitution of
an old approval hash. Activating the profile is not physical qualification.

## Builds and reproduction

Both fresh offline MIPS builds use the pinned toolchain
`sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
ELF/raw, entry, segment and undefined-symbol checks passed. The metadata payload
is byte-identical to the reader used in the accepted full-image staging session.

| Build | Payload bytes / SHA-256 | Build manifest SHA-256 |
| --- | --- | --- |
| `build/installation-metadata-001` | 5,656 / `086ceed72594fc2d21129808f2002b1e0dd8de959522e3470e334345732ea63e` | `d15483ad6eedc5eb5f335cca25c0dd422444301072b41d4ab4a19e46f2d2fc77` |
| `build/installation-readback-001` | 6,312 / `b1743a1de8a2dbaf2cfa5622061437c19684572f217f051e561fb7ee7f3acd8b` | `b4d68b88b3a3fff3491728762921e6ea9353eeae4be05dad19eac441a21af46f` |

```sh
python3 scripts/deployment/installation_review.py \
  --diskos /path/to/diskos --artifacts work/deployment-usb \
  --build build/installation-metadata-001 \
  --readback-build build/installation-readback-001 \
  --boot-capture work/boot-evidence-observation-001 \
  --stock-capture work/rootfs-full-observation-002 \
  --stage-capture work/writer-stage-observation-001 \
  --libusb /path/to/reviewed/libusb.dylib --output work/new-installation-package
```

The builder never changes admission profiles or invokes the generated plans.
Version selection remains `--version`, `FW_VERSION`, or the active profile.
After activation, `writer_transport.py plan --mode write --target candidate`
requires its existing build/diskos/artifacts/metadata arguments plus
`--installation-review work/installation-package-002/installation-review.json`
and `--readback-build build/installation-readback-001`. Acquisition additionally
requires reviewed libusb, a fresh output, the exact approved plan SHA-256 and
`--confirm-reviewed-device-state`. Restore uses its separate target/plan/approval.
The [procedure](installation-procedure.md) defines uncertain-outcome stops,
independent readback, first boot and restoration acceptance.

## Validation and outstanding authorization

Eleven new firmware-free tests cover evidence/session mixing, changed records or
journals, image/profile/source/build drift, missing review, false boot/readback
contracts, wrong libusb, missing state confirmation, closed admission, symlinks
and admission hash-cycle separation. The thirteen existing writer transport
cases now supply synthetic pinned evidence and still exercise every transfer
failure, one invocation and unknown-outcome preservation. Both groups pass on
macOS and Linux without firmware or a player. GitHub Actions uses the existing
synthetic test discovery; no hosted run is claimed.

The full host suite passed: 249 Python tests, eight JavaScript tests and native
C assertions (`work/installation-conformance.log`). Focused Linux results are
`work/installation-linux-review.log` and `work/installation-linux-writer.log`.
A complete second actual-input preparation after pinning the closed installer
profile produced all seven package files byte for byte identically. Source and
profile pins, proposed admission-only diff, documentation links and free disk
space were checked again; the host had 51.6 GiB free. No new browser behavior or
device payload source changed, so browser/emulator integration was not rerun.

All generated packages, plans, evidence and builds remain ignored. Only code,
profiles, synthetic tests and documentation are committed. No physical write,
installation, restoration, post-write comparison or native hardware boot has
been performed. The next concrete request is authorization to activate this
prepared profile and run **one candidate write**, followed only after confirmed
return by the reviewed full read and exact comparison. Restoration is prepared
but requires its own authorization and is never an automatic fallback.

## Without a history: the known image (plan, stage 6, 2026-10-08)

A user's installation keeps no history: `installation_review.py --known` takes
the evidence of the USB Boot entry the write will run in, never a stock or
stage capture or a previous installation:

```sh
python3 scripts/deployment/installation_review.py --known \
  --diskos <diskOS files> --artifacts <run>/image --build <run>/build-metadata \
  --staging-build <run>/build-staging --readback-build <run>/build-readback \
  --boot-build <run>/build-boot-evidence --probe-build <run>/build-probe \
  --boot-capture <run>/boot --probe-capture <run>/identity \
  --libusb <libusb> --output <run>/package
```

- The boot evidence (`boot_evidence.py acquire`) and the identity probe
  (`collect_rootfs.py acquire --mode rootfs-probe`), each with its independent
  audit as `offline-review.json` (`audit_usb_boot.py`, `audit_usb_probe.py`),
  must have carried out exactly the plans the review computes from the given
  builds and the boot capture's metadata page, with the same libusb.
- One entry: neither session ran the SPL (both read the clean DDR diagnostic
  the entry's metadata read left), the boot evidence before the probe, over
  the same enumeration (bus, address, ports).
- The bootloader selects the primary rootfs (`boot_review.assess`, as before).
- The probe's first blocks are recomputed from its records
  (`readback.first_blocks`) and must name an image of `firmware/images`: stock
  or one of ours (`known_images.py`). An unknown one, another FiiO version or a
  changed image, stops the review: nothing is written, and FiiO's own update
  (Local upgrade) is the way back to stock. The kernel is not read: FiiO's
  update writes it with the rootfs, and none of our images touches it (the
  owner accepted this and the first blocks as the image's identity).
- The way back (`restore`) must be the stock image of the list, by its digest.
- The write's ABI must be the one a player ran (`exercised` in
  `firmware/images`: the sixth write's writer, SPL, metadata and staging
  payloads, profiles, installer contract, image size, RAM regions and writer
  entry), in place of a staging capture of the user's own player; every write
  still runs the full staging checks before the writer.

The way back to stock from any state (owner, 2026-10-05/09): with
`--allow-unknown` an image the review does not know is named so (`found`
`unknown`, no image digest) instead of refusing, and the bundle admits the
restore only (`source_state.targets`; `validate_binding` refuses a candidate
over it, and the package holds no candidate write plan). The installer passes
it for `--restore` and asks for the word `STOCK` first, naming the risk: the
kernel is not read, and a player of another FiiO version would not start
2.57's system with it.

The bundle's `source_state` is `known-image` with what the player holds
(`found`), its first blocks, and `same_entry_required`; `validate_binding`
accepts it only with those. The package keeps the proposed admission
(`proposed-installer-profile.json`) and `decision.json`, what the installer
tells the user; nothing is written to `firmware/installers`. The write takes
this admission with `--installer-profile` and runs only in the same entry
([writer transport](writer-transport.md#a-write-without-a-history-plan-stage-6)).
`test_known_review` runs both sessions on the fake ROMs, audits them and
reviews them, with an unknown image, an ABI no player ran, a way back that is
not stock, other plans and other entries refused.

