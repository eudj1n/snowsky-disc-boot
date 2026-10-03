#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-host}"
# The source commit the boot status reports ("+changes" when the tree differs).
build_id="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
git diff --quiet HEAD -- device 2>/dev/null || build_id="$build_id+changes"
case "$mode" in
  host)
    make -C device OUT=../build/host DISC_BUILD="$build_id" all test
    ;;
  mips)
    # The player kernel (Linux 4.4.94) emulates the FPU from a stack trampoline;
    # refuse any toolchain whose executables are not soft-float.
    docker run --rm --platform linux/amd64 --network none -e DISC_BUILD="$build_id" \
      -v "$PWD:/src" -w /src/device "${DISC_TOOLCHAIN_IMAGE:-disc-native-toolchain}" \
      sh -c 'make OUT=../build/mips CC="${CROSS}gcc" LDFLAGS=-static DISC_BUILD="$DISC_BUILD" all && for exe in disc-usb-console disc-boot disc-menu; do "${CROSS}readelf" -A ../build/mips/$exe | grep -q "FP ABI: Soft float" || { echo "$exe is not soft-float" >&2; exit 1; }; if "${CROSS}objdump" -d ../build/mips/$exe | grep -qE "\s(bc1[ft]|mtc1|mfc1|lwc1|swc1|ldc1|sdc1)\s"; then echo "$exe contains FPU instructions" >&2; exit 1; fi; done'
    ;;
  reader)
    docker run --rm --platform linux/amd64 --network none \
      -v "$PWD:/src" -w /src/device "${DISC_TOOLCHAIN_IMAGE:-disc-native-toolchain}" \
      sh -c 'make OUT=../build/mips CC="${CROSS}gcc" LDFLAGS=-static CFLAGS="-Os -g -std=c11 -Wall -Wextra -Werror -march=mips32" ../build/mips/nand-reader-test ../build/mips/sfc-identity-test && "${CROSS}gcc" -std=c11 -Os -Wall -Wextra -Werror -ffreestanding -fno-builtin -fno-pic -fno-pie -mno-abicalls -G0 -msoft-float -c acquisition/nand_reader.c -o ../build/mips/nand-reader-core.o && "${CROSS}nm" -u ../build/mips/nand-reader-core.o > ../build/mips/nand-reader-undefined.txt && test ! -s ../build/mips/nand-reader-undefined.txt && "${CROSS}readelf" -h ../build/mips/nand-reader-core.o'
    ;;
  *) echo 'Usage: scripts/build.sh host|mips|reader' >&2; exit 2;;
esac
