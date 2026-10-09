#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-host}"
# The boot status reports the last commit that changed what the image's programs are built from
# ("+changes" when those files differ): commits elsewhere (documentation, scripts, tests, the
# toolchain's pinned recipe, the services, which are packages known by their digests) leave the
# image's bytes as they were, so a release built later from the same sources is the build the guest
# accepted, and a service's fix needs no new image.
sources=(device/boot device/console device/common device/menu device/vendor device/Makefile)
build_id="$( (git log -1 --format=%H -- "${sources[@]}" 2>/dev/null || true) | cut -c1-12)"
build_id="${build_id:-unknown}"
git diff --quiet HEAD -- "${sources[@]}" 2>/dev/null || build_id="$build_id+changes"
case "$mode" in
  host)
    make -C device OUT=../build/host DISC_BUILD="$build_id" all test
    ;;
  mips)
    # The player kernel (Linux 4.4.94) emulates the FPU from a stack trampoline;
    # refuse any toolchain whose executables are not soft-float.
    docker run --rm --platform linux/amd64 --network none -e DISC_BUILD="$build_id" \
      -v "$PWD:/src" -w /src/device "${DISC_TOOLCHAIN_IMAGE:-disc-native-toolchain}" \
      sh -c 'make OUT=../build/mips CC="${CROSS}gcc" LDFLAGS=-static DISC_BUILD="$DISC_BUILD" all && for exe in disc-usb-console disc-boot disc-menu disc-health disc-network; do "${CROSS}readelf" -A ../build/mips/$exe | grep -q "FP ABI: Soft float" || { echo "$exe is not soft-float" >&2; exit 1; }; if "${CROSS}objdump" -d ../build/mips/$exe | grep -qE "\s(bc1[ft]|mtc1|mfc1|lwc1|swc1|ldc1|sdc1)\s"; then echo "$exe contains FPU instructions" >&2; exit 1; fi; done'
    ;;
  reader)
    docker run --rm --platform linux/amd64 --network none \
      -v "$PWD:/src" -w /src/device "${DISC_TOOLCHAIN_IMAGE:-disc-native-toolchain}" \
      sh -c 'make OUT=../build/mips CC="${CROSS}gcc" LDFLAGS=-static CFLAGS="-Os -g -std=c11 -Wall -Wextra -Werror -march=mips32" ../build/mips/nand-reader-test ../build/mips/sfc-identity-test && "${CROSS}gcc" -std=c11 -Os -Wall -Wextra -Werror -ffreestanding -fno-builtin -fno-pic -fno-pie -mno-abicalls -G0 -msoft-float -c usbboot/nand_reader.c -o ../build/mips/nand-reader-core.o && "${CROSS}nm" -u ../build/mips/nand-reader-core.o > ../build/mips/nand-reader-undefined.txt && test ! -s ../build/mips/nand-reader-undefined.txt && "${CROSS}readelf" -h ../build/mips/nand-reader-core.o'
    ;;
  *) echo 'Usage: scripts/build.sh host|mips|reader' >&2; exit 2;;
esac
