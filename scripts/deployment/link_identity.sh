#!/bin/sh
# Called only inside the offline toolchain container, with /src read-only.
set -eu
# The digest batch (plan, stage 4b) is compiled and linked only into the rootfs-digest payload:
# every other payload stays byte for byte as reviewed.
digest=
if grep -q '^#define ROOTFS_DIGEST' identity_layout.h; then digest=digest; fi
# The staging check (plan, stage 4c) is memory only: its payload links no SFC or NAND code.
names="nand_reader sfc_x2000 identity batch identity_main identity_entry $digest"
objects="identity_entry.o identity_main.o identity.o batch.o sfc_x2000.o nand_reader.o ${digest:+digest.o}"
if grep -q '^#define STAGING_CHECK' identity_layout.h; then
    names="identity_main identity_entry staging"
    objects="identity_entry.o identity_main.o staging.o"
fi
for name in $names; do
    suffix=c
    if [ "$name" = identity_entry ]; then suffix=S; fi
    "${CROSS}gcc" -std=c11 -Os -Wall -Wextra -Werror -march=mips32 -msoft-float \
      -ffreestanding -fno-builtin -fno-pic -fno-pie -mno-abicalls -G0 \
      -fno-stack-protector -fno-asynchronous-unwind-tables -fstack-usage \
      -ffunction-sections -fdata-sections -I/out -I/src/device/acquisition \
      -c "/src/device/acquisition/$name.$suffix" -o "$name.o"
done
"${CROSS}gcc" -nostdlib -static -no-pie -march=mips32 -msoft-float -mno-abicalls -G0 \
  -Wl,-T,identity.ld,--build-id=none,--gc-sections,-Map,identity.map \
  $objects -o identity.elf
"${CROSS}nm" -u identity.elf > undefined.txt
test ! -s undefined.txt
"${CROSS}objcopy" -O binary identity.elf identity.bin
"${CROSS}readelf" -h -l identity.elf > elf.txt
"${CROSS}objdump" -d identity.elf > disassembly.txt
