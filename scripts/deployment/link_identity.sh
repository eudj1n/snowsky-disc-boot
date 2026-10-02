#!/bin/sh
# Called only inside the offline toolchain container, with /src read-only.
set -eu
for name in nand_reader sfc_x2000 identity batch identity_main identity_entry; do
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
  identity_entry.o identity_main.o identity.o batch.o sfc_x2000.o nand_reader.o -o identity.elf
"${CROSS}nm" -u identity.elf > undefined.txt
test ! -s undefined.txt
"${CROSS}objcopy" -O binary identity.elf identity.bin
"${CROSS}readelf" -h -l identity.elf > elf.txt
"${CROSS}objdump" -d identity.elf > disassembly.txt
