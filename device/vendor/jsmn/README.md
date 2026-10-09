# jsmn dependency

Upstream: https://github.com/zserge/jsmn
Pinned revision: commit `25647e692c7906b96ffd2b05ca54c097948e879c` (`jsmn.h`
SHA-256 `c04533e9181e1e33baceb0f55ac449b05145bb936e8c68cc77dfe0d8277514fb`).
License: [LICENSE](LICENSE) (MIT). Vendored single header, unmodified, as in
snowsky-disc-web.

Used by `device/common/boot_util.c` to parse package manifests, the boot
layer's state files and packages' requests, compiled with `JSMN_STATIC` and
`JSMN_STRICT`. Every input is a bounded file (manifest 64 KiB, state and
requests 4 KiB) validated field by field.
