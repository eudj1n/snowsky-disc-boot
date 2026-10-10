# Boot-report candidate update package — 2026-09-24

This page records offline package preparation. The owner subsequently authorized
one new candidate write and full readback, which passed; see the
[physical observation](../observations/boot-report-installation-observation.md), including the
first reader's DDR stop and the successful read after a manual power cycle.
**Physical admission is closed again.** The subsequent separately authorized
[SD report](../observations/boot-report-observation.md) confirmed native startup and primary-root
boot data and identified a USB controller-name mismatch. A
[corrected-controller package](udc-update.md) is prepared; the artifact hashes
below describe the earlier image actually installed.

## Recorded source state

`installation_review.py` now accepts either the original stock/staging path or
an explicit installed-candidate history. The latter requires all four arguments:
`--previous-review`, `--previous-image`, `--write-capture`, `--readback-capture`.
It rejects mixing them with `--stage-capture` or supplying an incomplete history.
Version selection still uses the reviewed firmware profiles.

For an update, `installed_candidate.py` verifies:

- The historical request/result identities, approved plans, successful completion,
  cleanup, dependency and saved independent transport-audit fingerprints.
- The previous review's canonical hash against the actual write plan, its original
  admitted layout, source pins, payloads, build and image review. Historical source
  manifests are preserved; they are not made to claim current code was executed.
- The same firmware, boot/selector, NAND, reader/writer, RAM layout and transport
  contracts. USB diagnostic content and current host source/build manifests may
  change; a new firmware or hardware contract needs fresh compatibility evidence.
- The unchanged original stock/boot capture pins and stock restore image. Both
  historical captures are rechecked. Original stock bytes are historical evidence,
  **not a claim about the currently installed rootfs**.
- A fresh page-by-page comparison of the saved full readback against the previous
  approved image, including every padded byte, nonce, ECC and bad-block mapping.
  The result must reproduce the saved exact report and collector result. The raw
  writer completion record must independently agree with that mapping.
- A distinct read session after confirmed writer return, followed by the owner's
  recorded normal-boot confirmation. This confirmation is not an automated native
  process or live-root test. Missing confirmation, mixed sessions, unknown writer
  outcomes and inconsistent timestamps are rejected.

Saved transport audits are review evidence, not cryptographic authentication of
hardware provenance. Local timestamps constrain the recorded order, not device
identity. Freshness remains false. Before a physical run, the owner must confirm
this is the same player and account for intervening actions: no other rootfs,
OTA, bootloader, kernel, partition or selector changes after the recorded candidate
write/readback, and no unresolved writer invocation. Normal boots and the recorded
USB-storage/marker checks do not claim new NAND content evidence.

The new bundle records `source_state.kind = installed-candidate` and
`new_image_staged = false`. The old staging checks cover the exercised ABI and
old image only. A separately authorized new write repeats both complete RAM
patterns and exact comparisons of the **new** image, writer and guards before
one writer invocation. Historical evidence cannot skip those checks.

## Exact package

The seven files in `work/boot-report-installation-001/` were reproduced byte for
byte in `work/boot-report-installation-002/` after updating the closed profile pin.
The proposed profile differs from the tracked profile only by setting admission
true. Neither package grants permission to apply or execute it.

| Input | SHA-256 |
| --- | --- |
| Previous installed candidate, 100,663,296 bytes | `463f5796fcd45f56b482ffb9b671c08d855583595c24407d0d222477019bcdb2` |
| New boot-report candidate, 100,663,296 bytes | `18e628030998f4ec018f095d362e8f345a7a10136d32f3fbcce1339bb4d7a506` |
| Stock restore, 100,663,296 bytes | `e75d85bddb7b9fe304dfe334058693fb522a93cb1048ddfb5417f136f204eae0` |
| Canonical installation review | `45a2e5661bedf07242f06c070dca580c7dbaead1d6c94e97f7b340ab5be52ee5` |
| Review file | `cc8162a0955060ae04f8da4bf7cc59b7db01b8184c21bbb0376676d6c841d49c` |
| Proposed installer profile file | `9cf8d97df52869fe82b19e0951de9ca3438b4c120d61f64b5b1e134eb63ebb2f` |

| Prepared plan | Canonical SHA-256 |
| --- | --- |
| One candidate write | `82dc4c80e2cc07bf8af723f83e72c97fc00bc2777859d6a7e2090ec9d777ee20` |
| Full post-write collection | `eb1faaec76055a758f49fe319e6738e69c6edb70aa8e88f3992627449becc2dd` |
| Exact new candidate comparison | `003376655ad0f8879c1d54f56c3d80152d7c65e43d8772dfd0605adb077f7b92` |
| Restore write, separate authorization | `c668f702772dc7e1c7a2673afdd2a9b5060015edcdbff56d13d5232ad3cc0358` |
| Exact restore comparison | `b62d2bdb7802916b9a007afbc0b58909d9f7140987102ddaf3e3a611784bf029` |

The writer still covers 768 logical blocks (96 MiB), scanning physical blocks
80 through 911 within primary rootfs `[0x00a00000, 0x07200000)`. It discovers bad
blocks afresh. No bootloader, kernel, selector, recovery, MAC or userdata writes
are proposed. Bounds remain 30 minutes for writing, followed only after confirmed
return by a separate 60-minute full collection: 794 batches, 50,816 records.
There is no automatic reconnect, retry, reset or restore. An unknown writer
outcome stops the sequence. See the [installation procedure](installation-procedure.md).

Both fresh MIPS builds used toolchain
`sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
ELF/raw/entry checks passed; payloads are identical to the previously exercised
metadata and full-reader payloads. Current source/build manifests are new.

| Build | Payload bytes / SHA-256 | Build manifest SHA-256 |
| --- | --- | --- |
| `build/update-metadata-001` | 5,656 / `086ceed72594fc2d21129808f2002b1e0dd8de959522e3470e334345732ea63e` | `be2e69d7b5085b796d9d4af2d4b63d02edf8919d8e8af308f2f66109076e7bcf` |
| `build/update-readback-001` | 6,312 / `b1743a1de8a2dbaf2cfa5622061437c19684572f217f051e561fb7ee7f3acd8b` | `f549910f3ca4ce1300a31f3084a05927fc7585af47557a429f92f17675b381ae` |

## Reproduction and checks

```sh
python3 scripts/deployment/installation_review.py \
  --diskos /path/to/diskos --artifacts work/deployment-boot-report-002 \
  --build build/update-metadata-001 --readback-build build/update-readback-001 \
  --boot-capture work/boot-evidence-observation-001 \
  --stock-capture work/rootfs-full-observation-002 \
  --previous-review work/installation-package-002/installation-review.json \
  --previous-image work/deployment-usb/disc-web-v257-usb-engineering-review-only.bin \
  --write-capture work/candidate-write-observation-001 \
  --readback-capture work/candidate-readback-observation-001 \
  --libusb /path/to/reviewed/libusb.dylib --output work/new-update-package
```

The 11 new history tests use generated pages and no firmware or player. They
cover actual byte comparison, altered captures/journals/plans, missing or mixed
boot reports, unresolved completion, wrong ordering, changed hardware/boot
contracts, wrong prior image and inconsistent completion/mapping. An additional
binding test rejects missing, unknown or falsely fresh source-state declarations.
Together with the 12 binding and 13 writer transport tests, they pass on macOS
and Linux (`work/update-tests.log`, `work/update-binding-tests.log`,
`work/update-linux-tests.log`). GitHub Actions discovers these same synthetic
tests without sibling checkouts, firmware or credentials; no hosted run is claimed.
The full host suite passed **277 Python tests, eight JavaScript tests and native
C assertions** (`work/update-conformance.log`). The host had 49 GiB free during
review, above the 2 GiB combined capture requirement.

No device payload, image content or browser behavior changed in this stage.
The existing [packed-image acceptance](../boot/boot-report.md) still applies. Generated
images, captures, plans and test logs remain ignored. External repositories are
unchanged.

## Next separately authorized steps

1. Confirm the same player and unchanged reviewed state; activate the exact
   proposed profile and perform one new candidate write. Only after confirmed
   writer return, collect all 96 MiB and compare against the exact new image.
2. Reboot normally and confirm the stock UI works. Provision the separate
   `DISC_WEB_BOOT_REPORT` marker only under report-export authorization. The
   existing `DISC_WEB_USB_DEBUG` marker does not authorize SD report writes.
3. Follow the [report procedure](../boot/boot-report.md): first report boot without a USB
   cable, wait at least two minutes after UI/card readiness, then use stock USB
   storage to read the report. Preserve existing reports. Inspect boot ID/profile,
   live root/cmdline and launch errors before claiming native acceptance.

The authorization for this run covered step 1 only. Restoration and SD
report provisioning/export are not automatic consequences of a successful write.
