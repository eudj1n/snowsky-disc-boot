# Independent saved-image readback

`scripts/deployment/readback.py` verifies a saved NAND observation stream against
an exact candidate or stock-restore image. It is separate from diskOS's writer
and its in-writer compare loop. It never loads USB, executes a payload, writes
NAND or authorizes an installation. The separate [multi-page collector](rootfs-collector.md)
has synthetic acceptance and a separately authorized [full physical observation](observations/rootfs-full-observation.md).
Official rootfs content matches; strict whole-image equality is rejected because
the observed FF tail differs from the prepared image's zero padding. The existing
metadata payload still admits only its single compiled page.

## Mapping and comparison

The selected firmware reader/kernel/chip/writer profiles define the geometry,
ECC policy and scan bounds. A saved metadata main page must parse successfully
and contain the entire writer range within rootfs. The expected image SHA-256
comes from its separately reviewed build report; the image must have exactly
the writer's logical capacity and match that hash before comparison.

The observation stream contains fixed 4,476-byte records: the existing 48-byte
request followed by its 4,428-byte result. Record order is deterministic:

1. Two distinct, consecutively numbered page reads of the first page of every
   physical block in the writer's scan range, in ascending block order.
2. Every page of each selected good block, in logical-image order. Select the
   first N good blocks starting at the partition's first physical block, where N
   is the writer's logical block count. Do not use a writer-reported bad list to
   construct this independent mapping.

Both marker observations must return the same first OOB byte. FF means good;
any matching non-FF byte means bad. An unreadable or ambiguous marker stops
verification, rather than being guessed bad or replaced by a later read. The
scan includes unused reserve blocks because that matches the pinned writer's
scan scope. Its ceiling and reserve are profile-selected, not inferred from
historical per-device bad-block numbers.

Every request must match the expected page, supplied nonce and consecutive
sequence. Every result must have successful completion, matching request fields,
exact length, valid CRC, zero unused padding, reviewed ID/configuration, finite
counter values and an admitted ECC status. This also applies to marker records.
ECC 9–F, busy state, ECC disabled and OTP mode are rejected. This is stricter than
the stock BBT scan, which tolerates uncorrectable main-data ECC for its marker
comparison. A failed marker observation requires a separately reviewed
marker-specific policy/reader, not an implicit weakening of this verifier.

During the data pass, each selected block's first-page marker must still be FF.
Every main-data byte is compared with its corresponding image page, including
the full zero-padded image tail. OOB is retained as evidence and checked for
framing/CRC/marker purposes; it is not part of the logical image hash. Incomplete,
extra, duplicate or reordered records, too few good blocks, changed markers and
any image mismatch stop the operation. No resumption or replay is implemented.

The verifier reads one record and one image page at a time, plus a bounded
profile-sized block map. Initial image hashing uses a 1 MiB buffer. It never
loads a whole capture or image into memory. The output includes the capture and
image hashes, bad-block list, logical-to-physical map, map hash and ECC histogram.

## Meaning of success

`saved-logical-readback-matches` means the supplied stream reconstructs the exact
reviewed image under the selected policy. It does not authenticate the stream's
physical source or prove when it was captured. A matching nonce alone cannot
make an old or fabricated capture fresh. Physical provenance, freshness, active
boot selection and flash readiness remain explicitly false.

A future acquisition layer must retain the reviewed plan, artifact hashes,
fresh session/nonce, per-call journal and actual execution order. Installation
acceptance will additionally need a completed writer run, independent acquisition
after that run, stock/companion boot and restoration evidence. This checker is
one component of that procedure, not a replacement for those observations.

## Commands

Both commands are offline. Select another reviewed firmware with `--version`;
omission follows the standard environment/active-profile selection.

```sh
python3 scripts/deployment/readback.py plan \
  --metadata-page /path/to/saved-metadata-main.bin \
  --image /path/to/reviewed-image.bin --image-sha256 REVIEWED_IMAGE_SHA256

python3 scripts/deployment/readback.py verify \
  --metadata-page /path/to/saved-metadata-main.bin \
  --image /path/to/reviewed-image.bin --image-sha256 REVIEWED_IMAGE_SHA256 \
  --records /path/to/saved-records.bin --nonce-hex CAPTURE_NONCE_HEX \
  --first-sequence 1
```

The current writer profile requires 832 physical marker blocks, two reads each,
and 49,152 data pages for its 96 MiB image: 50,816 records, exactly 227,452,416
capture bytes. This is the proposed saved-record format and verification scope,
not a live acquisition timeout estimate or authorization. A future bounded
collector must avoid restarting SPL and the full RAM diagnostic per page; the
single-page metadata transport is not a practical full-image collector.

## Evidence — 2026-09-24

Ten synthetic tests pass on macOS/Python 3.13 and Linux/Python 3.11. They cover
skips at the beginning/middle/end of the scan, reserve exhaustion, all 16 ECC
values, marker disagreements/changes, wrong bytes despite valid CRC, every
record-boundary truncation, extra/duplicate/reordered records, wrong nonce/page,
invalid result fields/counters, policy/image changes, symlinks and a future
firmware selection. They run through existing GitHub Actions discovery using
only public profiles and generated fixtures. No hosted CI execution is claimed.
The full host regression passed: 176 Python tests, eight JavaScript tests and C
assertions (`work/readback-conformance.log`). The Linux focused result is in
`work/readback-linux-tests.log`. No service/browser or RAM payload behavior was
changed, so firmware/browser integration and MIPS rebuilds were not repeated.

The existing offline image/source reviewer was rerun successfully for both
companion and USB engineering artifacts. Readback plans were then generated
against the physically observed metadata page and the exact existing engineering,
companion and stock-restore images. This checked their hashes, capacity and
partition containment; no full physical readback was acquired or verified.

| Local plan | Plan SHA-256 |
| --- | --- |
| `work/readback-engineering-plan.json` | `91361d910798643a2a33366f12d4663d45faa39711f4c27cc0a5d75836751715` |
| `work/readback-companion-plan.json` | `b943af7017558ebdd040cfff3ab5b0b8244243a201a3a540b7cb359fc830f67d` |
| `work/readback-restore-plan.json` | `b62d2bdb7802916b9a007afbc0b58909d9f7140987102ddaf3e3a611784bf029` |

Logs and plans remain ignored under `work/readback-*`. No proprietary bytes
were added to tests or Git, and external repositories were not changed.

## Next implementation boundary

The separately bounded rootfs collector now implements this record contract and
has completed its authorized probe and full collection. The strict stock-image
comparison rejected the different padding; [the retained result](observations/rootfs-full-observation.md)
separates full official-rootfs identity from this failed equality check. The
[separate stock contract](preinstall-review.md) now defines and tests
pre-installation content/tail acceptance. Resolve active rootfs selection and
implement the separately bounded writer transport before installation. Prepare the exact engineering
image/stock-restore writer procedure and post-write evidence flow afterward.
Only those affected rootfs ranges are in the readback design; full-chip backup
remains deferred and is not introduced as a new installation prerequisite.

See [writer review](deployment-writer-review.md), [metadata evidence](nand-metadata.md),
[kernel mapping review](nand-kernel-review.md) and project plan (snowsky-disc-web `docs/plan.md`).
