# Corrected-controller installation package — 2026-09-24

The [physical boot report](../observations/boot-report-observation.md) identified the sole USB
controller as `13500000.otg_new`. The installed engineering image still requests
`13500000.otg`, which fails the exact-name readiness guard. This offline package
applies the corrected, selected USB profile. Physical write admission stays closed;
preparation does not authorize installation or qualify USB enumeration.
The owner subsequently authorized the exact write and full readback, which
[passed](../observations/udc-installation-observation.md), followed by owner-confirmed normal
stock UI operation. Admission is closed again; physical diagnostic acceptance
remains outstanding.

## Image and scope

Ignored host artifacts are in `work/deployment-udc-001/`; unpacked verification
trees remain in disposable `/work/disc-deployment-udc-001/`. The builder preserved
all 3,492 stock objects and verified a complete content/metadata pack/extract
round trip. The squashfs is 81,043,456 bytes; each padded image is 100,663,296 bytes.

Compared with the installed candidate's pristine build tree, exactly two files
changed: `etc/init.d/S99disc-usb` and `opt/disc-web/boot-report.sh`. There are no
added or removed paths. They contain the selected controller name and updated
report profile fingerprint. The companion, native USB helper, stock files and
companion hook are unchanged (`work/udc-image-delta.json`). The offline image
review passed with `flashReady: false`.

| Artifact | SHA-256 |
| --- | --- |
| Installed candidate | `18e628030998f4ec018f095d362e8f345a7a10136d32f3fbcce1339bb4d7a506` |
| New candidate | `76cc1ee9a8a9089bfca59bc61e81cbfc2ef9f5225a687150dc314df2f6d84618` |
| Unchanged stock restore | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |
| Selected USB profile, canonical | `25fe6c616db3c15bdf0fcce9300bcde98922078a0362792ccbddf5f74d448b2d` |
| Packaged report script | `d053b2b316445fef5dcd37aba312fb197b1f757515ccb3f7bd3ed8a76eea983e` |

## Installation review

The review rechecked the latest completed write in
`work/boot-report-write-observation-001/`, successful independent full readback in
`work/boot-report-readback-observation-002/`, and owner-confirmed normal boot.
It recomputed the exact comparison of all saved 96 MiB against the installed
candidate, including padding. It also rechecked the original boot/stock evidence
and restore bytes. The interrupted first reader remains historical evidence;
it supplies no successful capture to this package.

`source_state.kind` is `installed-candidate`, not stock. The history review
does not prove current device identity or freshness. The owner must confirm the
same player and no intervening OTA, rootfs, kernel, bootloader, partition or
selector changes or unresolved write. Normal boots and the recorded SD report
sequence are accounted for separately.

All seven files in `work/udc-installation-001/` reproduce byte for byte in
`work/udc-installation-002/`. The tracked installer profile pins the new review
with `physical_write_admitted: false`; the proposed profile differs only by
setting that flag true. No profile activation or device access occurred here.

| Review / plan | Canonical SHA-256 |
| --- | --- |
| Installation review | `89f1a2cb21165ae587a6f59c2cbc94d2ed4981f1df9458091f7821be5077bbeb` |
| One candidate write | `abc6e62a491cc5a619105129258a3c1650c9698e987688f30d3a965711373cb4` |
| Full post-write collection | `eb1faaec76055a758f49fe319e6738e69c6edb70aa8e88f3992627449becc2dd` |
| Exact candidate comparison | `9c0503df7317916525dfaa067c58d1bb6252176f27acaefde3e009e5a64e0629` |
| Separate restore write | `81ba0b3d41d91d2f02aed26576eadcdf501981397ca46b79f9d325253bb8bc9d` |
| Exact restore comparison | `b62d2bdb7802916b9a007afbc0b58909d9f7140987102ddaf3e3a611784bf029` |

The review file SHA-256 is
`02b0a58e7e6d4110646e2567e8733f49cb9a53fd3cbb7e1ced9c86211d927b95`;
the proposed installer profile file SHA-256 is
`2e2941e26e53f1a5f3622602cd1c1f30535fce94e99aa34d189b417150f317a2`.

Fresh builds in `build/udc-metadata-001/` and `build/udc-readback-001/` passed
ELF/raw/entry checks using the pinned toolchain
`sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
Both payloads and build manifests match the previously exercised builds:

| Build | Payload bytes / SHA-256 | Manifest SHA-256 |
| --- | --- | --- |
| Metadata | 5,656 / `086ceed72594fc2d21129808f2002b1e0dd8de959522e3470e334345732ea63e` | `be2e69d7b5085b796d9d4af2d4b63d02edf8919d8e8af308f2f66109076e7bcf` |
| Full reader | 6,312 / `b1743a1de8a2dbaf2cfa5622061437c19684572f217f051e561fb7ee7f3acd8b` | `f549910f3ca4ce1300a31f3084a05927fc7585af47557a429f92f17675b381ae` |

## Validation

The new packed-image integration passed loopback `/api/health`, disabled start,
native SIGTERM/relaunch and unrelated-PID preservation. USB duplicate-start,
stop and the 30-second no-card timeout passed. QEMU still changes argv matching,
so companion hook-managed stop/idempotency remain unqualified on hardware
(`work/udc-packed-boot.log`).

The report integration now creates the selected synthetic UDC and verifies both
the packaged hook argument and reported controller state. Its first run exposed
a test observation race: noclobber output exists before its final copy completes.
The test now waits for the complete footer under the original deadline and size
limit. Three firmware-free regressions cover partial publication, timeout and
oversized output. No production publication behavior or image bytes changed.
The rerun with stock BusyBox passed, exporting a complete 796-byte report with
the configured controller name/state in 47.96 seconds; duplicate invocation
preserved it (`work/udc-packed-report-accepted.log`).

Installation/history tests (23) and report-wait regressions (3) pass on macOS and
Linux (`work/udc-installation-tests.log`, `work/udc-report-wait-tests.log`,
`work/udc-linux-tests.log`). They require no firmware, physical player, sibling
checkout or credentials and remain discoverable by GitHub Actions. No hosted
Actions or new browser run is claimed; browser behavior is unchanged.
The full host suite also passed: 284 Python tests, eight JavaScript tests and
native C assertions (`work/udc-conformance.log`).

## Reproduction

Build with the selected firmware profile using the [USB image command](../usb-boot/usb-diagnostics.md#build-and-verification)
and a fresh output directory. After copying its two images and report to the host
and running `scripts/deployment/review.py`, prepare the package:

```sh
python3 scripts/deployment/installation_review.py \
  --diskos /path/to/diskos --artifacts work/deployment-udc-001 \
  --build build/udc-metadata-001 --readback-build build/udc-readback-001 \
  --boot-capture work/boot-evidence-observation-001 \
  --stock-capture work/rootfs-full-observation-002 \
  --previous-review work/boot-report-installation-002/installation-review.json \
  --previous-image work/deployment-boot-report-002/disc-web-v257-usb-engineering-review-only.bin \
  --write-capture work/boot-report-write-observation-001 \
  --readback-capture work/boot-report-readback-observation-002 \
  --libusb /path/to/reviewed/libusb.dylib --output work/new-udc-package
```

## Next physical step

After concrete authorization and current-state confirmation, perform one write
of the new candidate with fresh full RAM staging/comparison. The existing writer
covers 768 logical blocks within primary rootfs, scanning physical blocks 80–911.
It does not write bootloader, kernel, recovery, selector or userdata. The session
budget is 30 minutes. Only after confirmed writer return, independently read all
96 MiB (60-minute budget, 794 batches / 50,816 records) and compare every padded
byte with the new candidate. There is no automatic reconnect, retry or restore.
An uncertain write outcome stops the procedure.

After exact readback, the owner must confirm normal boot before diagnostic USB
and live health/read-only protocol acceptance. Existing SD markers and the first
report remain preserved; do not delete or overwrite that report implicitly.
This package is engineering-only and does not close the native hardware gate.
