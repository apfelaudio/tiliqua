#!/bin/bash
#
# build an SoC bitstream by:
# - creating all the definitions required by the firmware (svd / memory map)
# - compiling the firmware itself
# - building the bitstream with firmware packaged inside it
#
# requirements:
# - rustup with riscv32imac target installed
# - cargo install svd2rust form

set -e -o pipefail

SOC=src/$1

# generate artifacts from design
mkdir -p build
pdm run $SOC/top.py --genrust

# create the PAC
make -C src/rs/pac

# move linker script into fw project (TODO: cleaner to leave in build/?)
cp build/memory.x $SOC/fw/memory.x

# build firmware itself and turn it into a binary stream
(cd $SOC/fw && cargo build --release)
(cd $SOC/fw && cargo objcopy --release -- -Obinary firmware.bin)

# (!) make sure path is correct in top.py of firmware.bin
# if the firmware is not present, luna-soc silently fills it with zeroes

# build final bitstream
pdm run $SOC/top.py --sc3
